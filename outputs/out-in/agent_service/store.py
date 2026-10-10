"""Durable local state/events. One worker process; interrupted jobs fail closed."""
from __future__ import annotations
import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

TERMINAL = {"COMPLETED", "QUARANTINED", "REVIEW_REQUIRED", "FAILED"}
TRANSITIONS = {"QUEUED": {"EXTRACTING", "FAILED"}, "EXTRACTING": {"RECONCILING", "FAILED"},
               "RECONCILING": {"SELF_HEALING", "UNDERWRITING", "QUARANTINED", "FAILED"},
               "SELF_HEALING": {"RECONCILING", "FAILED"},
               "UNDERWRITING": {"REVIEW_REQUIRED", "SETTLING", "FAILED"},
               "SETTLING": {"COMPLETED", "FAILED"}}


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.database = self.workspace / "jobs.sqlite3"
        with self.connect() as db:
            db.executescript("""PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, state TEXT, token_hash TEXT,
              data TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT, type TEXT, timestamp TEXT, payload TEXT, previous_hash TEXT, sha256 TEXT);
            CREATE INDEX IF NOT EXISTS events_job ON events(job_id,seq);
            CREATE TABLE IF NOT EXISTS wallet(job_id TEXT PRIMARY KEY, amount TEXT, status TEXT);
            """)

    def connect(self):
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def create(self, mode, terms, sources):
        jid, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        data = {"id": jid, "state": "QUEUED", "mode": mode, "metrics": {}, "artifacts": [],
                "error": None, "terms": terms, "sources": sources, "created_at": now()}
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?)", (jid, "QUEUED", hashlib.sha256(token.encode()).hexdigest(),
                       json.dumps(data), data["created_at"]))
        self.event(jid, "STATE_TRANSITION", {"state": "QUEUED"})
        return jid, token

    def get(self, jid):
        with self.connect() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (jid,)).fetchone()
        if row is None:
            raise KeyError("Unknown job")
        return json.loads(row["data"])

    def update(self, jid, **changes):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM jobs WHERE id=?", (jid,)).fetchone()
            if row is None:
                raise KeyError("Unknown job")
            data = json.loads(row["data"])
            data.update(changes)
            db.execute("UPDATE jobs SET data=?,state=? WHERE id=?", (json.dumps(data), data["state"], jid))
        return data

    def transition(self, jid, state, agent=None):
        old = self.get(jid)["state"]
        if state not in TRANSITIONS.get(old, set()):
            raise ValueError(f"Invalid transition {old} -> {state}")
        self.update(jid, state=state)
        self.event(jid, "STATE_TRANSITION", {"state": state, "agent": agent})

    def event(self, jid, kind, payload):
        stamp = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT sha256 FROM events WHERE job_id=? ORDER BY seq DESC LIMIT 1", (jid,)).fetchone()
            previous = row[0] if row else "0" * 64
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
            digest = hashlib.sha256((previous + jid + kind + stamp + encoded).encode()).hexdigest()
            cursor = db.execute("INSERT INTO events(job_id,type,timestamp,payload,previous_hash,sha256) VALUES(?,?,?,?,?,?)",
                                (jid,kind,stamp,encoded,previous,digest))
            seq = cursor.lastrowid
        return {"seq": seq, "job_id": jid, "type": kind, "timestamp": stamp, "payload": payload,
                "previous_hash": previous, "sha256": digest}

    def events(self, jid, after=0, limit=500):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?", (jid,after,limit)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def token_ok(self, jid, token):
        with self.connect() as db:
            row = db.execute("SELECT token_hash FROM jobs WHERE id=?", (jid,)).fetchone()
        return row is not None and secrets.compare_digest(row[0], hashlib.sha256(token.encode()).hexdigest())

    def list(self, states=None):
        with self.connect() as db:
            rows = db.execute("SELECT data FROM jobs ORDER BY created_at DESC LIMIT 200").fetchall()
        data = [json.loads(r[0]) for r in rows]
        return [j for j in data if not states or j["state"] in states]

    def settle_demo(self, jid, amount):
        with self.connect() as db:
            db.execute("INSERT INTO wallet VALUES(?,?,?) ON CONFLICT(job_id) DO NOTHING", (jid,amount,"PREPARED_SIMULATION"))
            row = db.execute("SELECT amount FROM wallet WHERE job_id=?", (jid,)).fetchone()
            if row[0] != amount:
                raise ValueError("Idempotency conflict")

    def finalize_demo(self, jid):
        with self.connect() as db:
            db.execute("UPDATE wallet SET status='SIMULATED_ONLY' WHERE job_id=?", (jid,))

    def rollback_wallet(self, jid):
        with self.connect() as db:
            db.execute("UPDATE wallet SET status='ROLLED_BACK' WHERE job_id=?", (jid,))

    def recover(self):
        for job in self.list():
            if job["state"] not in TERMINAL:
                self.rollback_wallet(job["id"])
                self.update(job["id"], error="Worker interrupted. Evidence retained; submit a new dossier.")
                self.transition(job["id"], "FAILED")
