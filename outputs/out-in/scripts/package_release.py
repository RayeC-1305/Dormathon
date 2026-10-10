"""Create verified, allowlisted OUT&IN demo source/evidence ZIPs.

Use after pytest --junitxml=demo/test-results.xml. The --test-workspace must be
that run's synthetic API workspace, never a workspace containing real dossiers.
No runtime database or private signing key is copied. Does not remove files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_manifest import verify_manifest

SOURCE_PATHS = (
    "README.md", "INTEGRATION.md", "requirements.txt", ".gitignore",
    "agent_service/app.py", "agent_service/orchestrator.py", "agent_service/sdk_runtime.py", "agent_service/store.py", "agent_service/myinvois_client.py",
    "tools/artifact_tool.py", "tools/ml_underwrite_tool.py", "tools/ocr_tool.py", "tools/reconciliation_tool.py", "tools/myinvois_tool.py",
    "scripts/prepare_demo.py", "scripts/verify_manifest.py", "scripts/browser_smoke.py", "scripts/package_release.py", "scripts/deploy_demo.ps1",
    "tests/test_api.py", "tests/test_engine.py", "tests/test_sdk_runtime.py", "tests/test_myinvois_client.py", "tests/test_myinvois_tool.py",
    "reference/Out_In_TNC_Agreement.pdf", "reference/reconciliation_handler.py",
    "frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.js", "frontend/index.html",
    "demo/dossier.zip", "demo/invoice.json", "demo/dossier/invoice.pdf", "demo/dossier/packing_list.pdf", "demo/dossier/statement.xlsx",
    "demo/models/credit_classifier.pkl", "demo/models/credit_regressor.pkl", "demo/models/model_manifest.json",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_payload(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Release source must be a regular file: {path.name}")
    content = path.read_bytes()
    # Source tests may contain quoted PEM sentinel literals; actual PEM material
    # has an unquoted header on its own line followed by encoded key bytes.
    headers = {b"-----BEGIN PRIVATE KEY-----", b"-----BEGIN RSA PRIVATE KEY-----", b"-----BEGIN EC PRIVATE KEY-----"}
    if any(line.strip() in headers for line in content.splitlines()):
        raise RuntimeError("Refusing to distribute private key material")
    return content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-workspace", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--release-tag", default="")
    parser.add_argument("--myinvois-test-job", default="")
    args = parser.parse_args()
    if args.release_tag and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.release_tag):
        raise RuntimeError("Unsafe release tag")
    suffix = "_" + args.release_tag if args.release_tag else ""
    work = Path(args.test_workspace).resolve()
    output = Path(args.output_dir).resolve()
    if not output.is_dir():
        raise RuntimeError("Output parent directory must already exist")
    if not work.is_relative_to(ROOT / "workspace"):
        raise RuntimeError("Use this checkout's explicitly synthetic test workspace")
    tree = ET.parse(ROOT / "demo/test-results.xml")
    suites = list(tree.getroot().iter("testsuite"))
    counts = {key: sum(int(s.get(key, "0")) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    if counts["tests"] < 975 or counts["failures"] or counts["errors"] or counts["skipped"]:
        raise RuntimeError("A complete passing release test run is required")
    groups = Counter(Path(t.get("classname", "").replace(".", "/")).name
                     for t in tree.getroot().iter("testcase"))
    manifest = json.loads((ROOT / "demo/models/model_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("is_demo") is not True or "synthetic" not in manifest.get("training_data", "").lower():
        raise RuntimeError("Models are not explicitly synthetic")
    for name, expected in manifest["sha256"].items():
        if digest(read_payload(ROOT / "demo/models" / name)) != expected:
            raise RuntimeError("Demo model hash mismatch")
    database = sqlite3.connect((work / "jobs.sqlite3").as_uri() + "?mode=ro", uri=True)
    jobs = [json.loads(row[0]) for row in database.execute("SELECT data FROM jobs ORDER BY created_at")]
    terminal = {state: next((j for j in jobs if j["state"] == state), None)
                for state in ("COMPLETED", "REVIEW_REQUIRED", "QUARANTINED")}
    if not all(terminal.values()) or any(j.get("mode") != "demo" for j in terminal.values()):
        raise RuntimeError("Expected three synthetic terminal outcomes are missing")
    summaries = {}
    for state, job in terminal.items():
        directory = work / "jobs" / job["id"]
        packet = json.loads((directory / "normalized/invoice-cycle-0.json").read_text(encoding="utf-8"))
        if "OUT&IN SYNTHETIC DEMO" not in json.dumps(packet):
            raise RuntimeError("Evidence export requires explicitly synthetic source text")
        rows = database.execute("SELECT type,timestamp,payload,previous_hash,sha256 FROM events WHERE job_id=? ORDER BY seq", (job["id"],)).fetchall()
        previous = "0" * 64
        transitions = []
        for kind, timestamp, encoded, prior, sha in rows:
            actual = digest((previous + job["id"] + kind + timestamp + encoded).encode())
            if prior != previous or sha != actual:
                raise RuntimeError("Event chain does not verify")
            previous = sha
            if kind == "STATE_TRANSITION":
                transitions.append(json.loads(encoded)["state"])
        entries = [{"amount": row[0], "status": row[1]} for row in database.execute("SELECT amount,status FROM wallet WHERE job_id=?", (job["id"],))]
        summaries[state] = {"state": state, "metrics": job["metrics"], "artifact_count": len(job["artifacts"]),
                            "event_count": len(rows), "event_hash_chain_verified": True,
                            "state_transitions": transitions, "wallet_entries": entries}
    database.close()
    completed = terminal["COMPLETED"]
    directory = work / "jobs" / completed["id"]
    verification = verify_manifest(directory / "artifacts", source_dir=directory)
    if not verification["verified"] or verification["source_verification"] != "checked":
        raise RuntimeError("Completed evidence bundle failed verification")
    if summaries["REVIEW_REQUIRED"]["wallet_entries"] or summaries["QUARANTINED"]["wallet_entries"]:
        raise RuntimeError("Review/quarantine unexpectedly created a wallet record")
    if summaries["QUARANTINED"]["state_transitions"].count("SELF_HEALING") != 3:
        raise RuntimeError("Expected three self-heals were not observed")
    browser_path = ROOT / "demo/browser_validation.json"
    browser = json.loads(browser_path.read_text(encoding="utf-8")) if browser_path.exists() else {"completed": False, "notice": "Browser smoke execution not completed"}
    report = {
        "schema_version": 2, "validated_at": datetime.now(timezone.utc).isoformat(),
        "synthetic_only": True, "real_funds": False,
        "test_results": {**counts, "passed": counts["tests"], "groups": dict(groups),
                         "suite_seconds": sum(float(s.get("time", "0")) for s in suites),
                         "test_timestamp": suites[0].get("timestamp"),
                         "warning": "Starlette httpx TestClient transport deprecation"},
        "transport": "In-process FastAPI TestClient; real local PDF normalization/reslices, exact audit, sklearn, artifact signing and verification; SDK clients substituted offline",
        "terminal_outcomes": summaries, "artifact_source_verification": verification,
        "browser": browser,
        "frontend": {"build_passed": None, "verification": "Packaging does not execute or independently verify a frontend build; run npm ci and npm run build before packaging.", "dist_present": (ROOT / "frontend/dist/index.html").is_file()},
        "corrected_release_defects": ["Windows POSIX manifest paths", "PDF source-to-renderer coordinates", "Resolvable job-relative evidence sources", "SPA deep-link exception class and Windows API separator handling", "Review-only capped haircut wording"],
        "myinvois": {"implemented": True, "authenticated_live_verified": False,
                     "transport_tested": "synthetic httpx.MockTransport; no LHDN credentials or live requests",
                     "scope": "read-only single-event raw invoice retrieval; taxpayer/intermediary token caching; exact party/trade binding; no search polling",
                     "profile": "MYR ordinary invoice 01 v1.0/1.1; nonzero allowances/charges/prepayments/rounding rejected; supplemental PDFs required"},
        "unverified": ["Authenticated LHDN MyInvois retrieval", "Authenticated deepseek-v3.1 extraction", "Original cloud/model adapters and external feature collection", "Production tenant isolation", "Real funds", "Identity-backed legal signatures", "Cross-border FX processing"],
        "notice": "Synthetic software evidence only, not calibrated lending conclusions or legal/payment authority. No private signing keys or runtime databases are distributed.",
    }
    (ROOT / "demo/validation_results.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    # Preserve raw local JUnit; strip machine identity only in the distributed copy.
    for suite in suites:
        suite.attrib.pop("hostname", None)
    junit = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True)
    paths = [ROOT / relative for relative in SOURCE_PATHS]
    paths += sorted((ROOT / "frontend/src").rglob("*"))
    paths += sorted((ROOT / "frontend/dist").rglob("*"))
    paths += [ROOT / "demo/validation_results.json"]
    if browser_path.exists():
        paths.append(browser_path)
    paths += [p for p in (ROOT / "demo/mission-control-dark.png", ROOT / "demo/mission-control-light.png") if p.is_file()]
    payloads = {"out-in/" + p.relative_to(ROOT).as_posix(): read_payload(p) for p in paths if p.is_file()}
    payloads["out-in/demo/test-results.xml"] = junit
    source_zip = output / ("OUT_IN_Mission_Control_Source" + suffix + ".zip")
    evidence_zip = output / ("OUT_IN_Synthetic_Completed_Evidence" + suffix + ".zip")
    with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(payloads.items()):
            archive.writestr(name, content)
    evidence_paths = [directory / "artifacts" / a["name"] for a in completed["artifacts"]]
    evidence_paths += [directory / source["name"] for source in json.loads((directory / "artifacts/verification_manifest.json").read_text(encoding="utf-8"))["sources"]]
    evidence_payloads = {"synthetic-completed-job/" + p.relative_to(directory).as_posix(): read_payload(p) for p in evidence_paths}
    with zipfile.ZipFile(evidence_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(evidence_payloads.items()):
            archive.writestr(name, content)
    targets = [source_zip, evidence_zip]
    if args.myinvois_test_job:
        myjob = Path(args.myinvois_test_job).resolve()
        if not myjob.is_relative_to(ROOT / "workspace"):
            raise RuntimeError("MyInvois export requires a synthetic test workspace")
        raw = json.loads((myjob / "evidence/myinvois-response-0.json").read_bytes())
        if raw.get("issuerTin") != "SYNTHETICSELLER" or raw.get("uuid") != "Synthetic_opaque-001":
            raise RuntimeError("MyInvois evidence must be the explicit synthetic integration fixture")
        result = verify_manifest(myjob / "artifacts", source_dir=myjob)
        if not result["verified"]:
            raise RuntimeError("Synthetic MyInvois bundle did not verify")
        mymanifest = json.loads((myjob / "artifacts/verification_manifest.json").read_bytes())
        if mymanifest.get("risk", {}).get("is_demo_model") is not True or mymanifest.get("real_funds") is not False:
            raise RuntimeError("MyInvois output must use explicit demo models/no funds")
        entries = [p for p in (myjob / "artifacts").iterdir() if p.is_file()]
        entries += [myjob / s["name"] for s in mymanifest["sources"]]
        myzip = output / ("OUT_IN_Synthetic_MyInvois_Evidence" + suffix + ".zip")
        with zipfile.ZipFile(myzip, "w", zipfile.ZIP_DEFLATED) as archive:
            for p in sorted(entries):
                archive.writestr("synthetic-myinvois-job/" + p.relative_to(myjob).as_posix(), read_payload(p))
        targets.append(myzip)
    output_report = {}
    for target in targets:
        with zipfile.ZipFile(target) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("ZIP CRC verification failed")
            names = archive.namelist()
            if any("node_modules" in n or "/workspace/" in n or "/keys/" in n or ".." in Path(n).parts for n in names):
                raise RuntimeError("Forbidden runtime content in archive")
        output_report[target.name] = {"sha256": digest(target.read_bytes()), "bytes": target.stat().st_size, "files": len(names)}
    (output / ("OUT_IN_Release_Checksums" + suffix + ".json")).write_text(json.dumps(output_report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"validation": counts, "archives": output_report}, indent=2))


if __name__ == "__main__":
    main()
