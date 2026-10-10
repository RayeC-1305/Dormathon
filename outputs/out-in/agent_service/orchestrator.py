"""Headless linear-with-feedback underwriting DAG. Run through FastAPI or CLI.

One dossier event => extract => exact audit => ML => demo settlement/artifacts.
Three reslicing retries, fail closed, no chat turns or LLM-controlled money.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
import shutil
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path
from agent_service.store import Store, TERMINAL
from tools.reconciliation_tool import EvidenceError, ParityViolation, dec, money, run_excel_reconciliation, enforce_parity
from tools.ocr_tool import run_ocr_normalizer, parse_demo_packet
from tools.ml_underwrite_tool import evaluate_credit_ml
from tools.artifact_tool import build_artifacts
from tools.decision_memo_tool import build_decision_memo, expected_figures
from tools.selfcheck_tool import verify_bundle_integrity
from tools.myinvois_tool import parse_myinvois_invoice, bind_myinvois_trade

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ["trade-extraction-proposer", "reconciliation-auditor", "ml-risk-underwriter", "settlement-legal-synthesizer"]


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class UnderwritingEngine:
    def __init__(self, store: Store, myinvois_client=None):
        self.store = store
        self.myinvois_client = myinvois_client
        self.semaphore = asyncio.Semaphore(2)

    async def run(self, jid: str):
        async with self.semaphore:
            await self._run(jid)

    async def _run(self, jid):
        job = self.store.get(jid)
        directory = self.store.workspace / "jobs" / jid
        inputs = directory / "inputs"
        sources = job["sources"]
        mode = job["mode"]
        terms = job["terms"]
        attempt = 0
        structured_sources = []

        def emit(kind, payload):
            return self.store.event(jid, kind, payload)

        async def call(agent, name, function, *args, **kwargs):
            emit("TOOL_EXECUTION", {"agent": agent, "tool": name, "status": "STARTED", "cycle": attempt})
            try:
                result = await asyncio.to_thread(function, *args, **kwargs)
            except Exception:
                emit("TOOL_EXECUTION", {"agent": agent, "tool": name, "status": "FAILED"})
                raise
            emit("TOOL_EXECUTION", {"agent": agent, "tool": name, "status": "COMPLETED", "cycle": attempt})
            return result

        def validate_sources():
            for source in sources:
                name = source.get("name", "")
                path = inputs / name
                if not name or name != Path(name).name or any(c in name for c in "/\\\\") or not path.resolve().is_relative_to(inputs.resolve()):
                    raise EvidenceError("Source path is outside ingested dossier")
                if not path.is_file() or path.is_symlink() or hash_file(path) != source["sha256"]:
                    raise EvidenceError("Immutable source hash verification failed")
            for source in structured_sources:
                path = directory / source["name"]
                if path.is_symlink() or not path.is_file() or hash_file(path) != source["sha256"]:
                    raise EvidenceError("MyInvois source snapshot changed during execution")

        try:
            validate_sources()
            pdfs = [inputs / s["name"] for s in sources if s["name"].endswith(".pdf")]
            statements = [inputs / s["name"] for s in sources if s["name"].endswith((".xlsx", ".csv"))]
            if not pdfs or len(statements) != 1:
                raise EvidenceError("Dossier requires invoice/delivery PDFs and exactly one bank XLSX or CSV")
            self.store.transition(jid, "EXTRACTING", AGENTS[0])
            structured, retrieved_bytes = [], 0
            document_ids = terms.get("myinvois_document_ids", [])
            if document_ids:
                if mode != "live" or self.myinvois_client is None:
                    raise EvidenceError("MyInvois requires an authorized live-mode server client")
                for index, document_id in enumerate(document_ids):
                    emit("TOOL_EXECUTION", {"agent": AGENTS[0], "tool": "fetch_myinvois_invoice", "status": "STARTED"})
                    response = await self.myinvois_client.fetch_document(document_id)
                    retrieved_bytes += len(response["response_bytes"])
                    if retrieved_bytes > 50 * 1024 * 1024:
                        raise EvidenceError("MyInvois dossier response budget exceeded")
                    filename = f"myinvois-response-{index}.json"
                    raw = directory / "evidence" / filename
                    raw.parent.mkdir(parents=True, exist_ok=True)
                    raw.write_bytes(response["response_bytes"])
                    structured_sources.append({"name": "evidence/" + filename, "sha256": hash_file(raw)})
                    provenance_path = directory / "evidence" / f"myinvois-provenance-{index}.json"
                    write_json(provenance_path, response["provenance"])
                    structured_sources.append({"name": provenance_path.relative_to(directory).as_posix(), "sha256": hash_file(provenance_path)})
                    # Persist source before validation, including rejected/unsupported response.
                    self.store.update(jid, myinvois_sources=list(structured_sources))
                    structured.append(parse_myinvois_invoice(response["envelope"], "evidence/" + filename))
                    emit("TOOL_EXECUTION", {"agent": AGENTS[0], "tool": "fetch_myinvois_invoice", "status": "COMPLETED", "source_sha256": hash_file(raw)})
                write_json(directory / "evidence" / "myinvois-invoices.json", structured)
            proposal, audit, regions = None, None, {}
            for attempt in range(4):
                async def normalize_pdf(path):
                    packet = await call(AGENTS[0], "run_ocr_normalizer", run_ocr_normalizer, str(path), regions.get(path.name), attempt)
                    # Crop paths are server internals; only normalized text reaches SDK.
                    for crop in packet.get("crops", []):
                        crop.pop("local_crop", None)
                    target = directory / "normalized" / f"{path.stem}-cycle-{attempt}.json"
                    write_json(target, packet)
                    return target, packet

                # Dossier PDFs normalize concurrently; gather preserves dossier order.
                results = await asyncio.gather(*(normalize_pdf(path) for path in pdfs))
                paths = [target for target, _ in results]
                normalized = [packet for _, packet in results]
                if mode == "demo":
                    packets = [parse_demo_packet(p) for p in normalized]
                    proposal = {"invoices": [p["data"] for p in packets if p["kind"] == "invoice"],
                                "deliveries": [p["data"] for p in packets if p["kind"] == "delivery"]}
                else:
                    from agent_service.sdk_runtime import SDKRuntime

                    async def dispatch(name, args):
                        # Native tools are bound, but authority is stage-scoped.
                        if name != "run_ocr_normalizer":
                            raise EvidenceError("Tool belongs to a later deterministic stage")
                        path = Path(args["file_path"])
                        if not path.is_absolute():
                            path = directory / path
                        path = path.resolve()
                        if path not in pdfs:
                            raise EvidenceError("Only current ingested PDFs may be normalized")
                        return await call(AGENTS[0], name, run_ocr_normalizer, str(path))

                    runtime = SDKRuntime(directory, emit, dispatch, require_complete_reads=bool(structured))
                    proposal = await runtime.extract(paths, attempt, audit.get("errors") if audit else [])
                if not proposal.get("invoices") or not proposal.get("deliveries"):
                    raise EvidenceError("Missing invoice or delivery evidence")
                for record in proposal["invoices"] + proposal["deliveries"]:
                    if record.get("source") not in {p.name for p in pdfs}:
                        raise EvidenceError("Extracted record cites an unknown source")
                    if not record.get("regions") and record in proposal["invoices"]:
                        raise EvidenceError("Invoice must cite page regions for verification and self-heal")
                binding_error = None
                if structured:
                    write_json(directory / "evidence" / f"commercial-proposal-{attempt}.json", proposal)
                    try:
                        proposal = bind_myinvois_trade(structured, proposal)
                    except EvidenceError as exc:
                        binding_error = str(exc)
                write_json(directory / "evidence" / f"proposal-{attempt}.json", proposal)
                self.store.transition(jid, "RECONCILING", AGENTS[1])
                if binding_error:
                    # No audit amounts are invented when trade identity/source binding fails.
                    audit = {"passed": False, "variance": None, "ledger_delta": None,
                             "invoice_total": None, "verified_inflow": None, "bank_rows": [],
                             "errors": [{"gate": "myinvois_commercial_binding", "message": binding_error}],
                             "gates": {"exact_money_parity": False, "delivery_verified": False}}
                else:
                    audit = await call(AGENTS[1], "run_excel_reconciliation", run_excel_reconciliation,
                                       proposal["invoices"], str(statements[0]), proposal["deliveries"])
                write_json(directory / "evidence" / f"audit-{attempt}.json", audit)
                self.store.update(jid, metrics={"variance": audit["variance"], "passed": audit["passed"]})
                emit("METRICS", {"variance": audit["variance"], "passed": audit["passed"]})
                try:
                    enforce_parity(audit)
                    break
                except ParityViolation:
                    emit("ROLLBACK_TRIGGERED", {"cycle": attempt+1, "variance": audit["variance"], "maximum_self_heals": 3,
                                                "action": "invalidate_proposal_and_reslice_original_evidence"})
                    if attempt == 3:
                        report_path = directory / "quarantine" / "variance_report.json"
                        write_json(report_path, {**audit, "self_heal_cycles": 3, "disbursement": "prohibited", "is_demo": True})
                        self.store.update(jid, artifacts=[{"name": report_path.name, "url": f"/api/v2/jobs/{jid}/artifacts/{report_path.name}"}])
                        self.store.transition(jid, "QUARANTINED", AGENTS[1])
                        return
                    regions = {p["source"]: p["regions"] for p in proposal["invoices"]}
                    self.store.transition(jid, "SELF_HEALING", AGENTS[0])
            validate_sources()
            # Require cent-precision line values for exported Excel contract.
            for inv in proposal["invoices"]:
                for item in inv["line_items"]:
                    money(dec(item["quantity"]) * dec(item["unit_price"]), "line valuation")
            self.store.transition(jid, "UNDERWRITING", AGENTS[2])
            rows = audit["bank_rows"]
            months = set()
            for row in rows:
                try:
                    month = date.fromisoformat(row["date"][:10]).strftime("%Y-%m")
                except ValueError as exc:
                    raise EvidenceError("Statement date must be ISO YYYY-MM-DD") from exc
                months.add(month)
            # This demo supports one fully specified ledger month only; infer no
            # missing months. Production bank adapter must prove period completeness.
            if len(months) != 1:
                raise EvidenceError("Demo feature window requires exactly one ledger month")
            with localcontext() as ctx:
                ctx.prec = 80
                income = sum((money(r["inflow"]) for r in rows), Decimal(0))
                expense = sum((money(r["outflow"]) for r in rows), Decimal(0))
                debt = money(terms["monthly_debt_service"])
                if income <= 0 or debt < 0:
                    raise EvidenceError("Nonpositive income or invalid debt service")
                features = {"monthly_inflow": str(income), "monthly_outflow": str(expense), "monthly_debt_service": str(debt),
                            "invoice_total": audit["invoice_total"], "cashflow_margin": format((income-expense)/income, ".12f"),
                            "dsr": format(debt/income, ".12f"), "requested_limit": terms["requested_limit"],
                            "provenance": {"ledger_sha256": audit["statement_sha256"], "months": sorted(months),
                                           "debt_service": "operator-declared; requires independent production verification"}}
            write_json(directory / "evidence" / "features.json", features)
            repo = Path(os.environ.get("OUT_IN_REPO_ROOT", ROOT)).resolve()
            model_dir = repo / "ml/models" if mode != "demo" else ROOT / "demo/models"
            risk = await call(AGENTS[2], "evaluate_credit_ml", evaluate_credit_ml, features,
                              str(model_dir), mode, terms["requested_limit"], terms["duration_months"], terms["annual_rate"])
            write_json(directory / "evidence" / "risk.json", risk)
            # Advisory plain-language memo. Deterministic text is always computed;
            # the opt-in live SDK narrative agent may only restate it, and its
            # figures must equal the deterministic evidence before acceptance.
            narrative, narrative_source = None, "deterministic"
            figures = expected_figures(audit, risk, terms)
            if mode == "live" and os.environ.get("OUT_IN_NARRATIVE_AGENT") == "1":
                from agent_service.sdk_runtime import NARRATIVE_PERSONA_NAME, NarrativeRuntime

                emit("TOOL_EXECUTION", {"agent": NARRATIVE_PERSONA_NAME, "tool": "draft_decision_memo",
                                        "status": "STARTED", "cycle": attempt})
                try:
                    narrative = await NarrativeRuntime(directory, emit).narrate(
                        [directory / "evidence" / f"audit-{attempt}.json",
                         directory / "evidence" / "features.json",
                         directory / "evidence" / "risk.json"], figures)
                    narrative_source = "sdk_agent"
                    emit("TOOL_EXECUTION", {"agent": NARRATIVE_PERSONA_NAME, "tool": "draft_decision_memo",
                                            "status": "COMPLETED", "cycle": attempt})
                except Exception:
                    # The memo is advisory; fail-safe to the deterministic text.
                    narrative, narrative_source = None, "deterministic_fallback"
                    emit("TOOL_EXECUTION", {"agent": NARRATIVE_PERSONA_NAME, "tool": "draft_decision_memo",
                                            "status": "FAILED", "cycle": attempt})
            memo = build_decision_memo(audit, risk, terms, narrative=narrative, narrative_source=narrative_source)
            write_json(directory / "evidence" / "decision_memo.json", memo)
            self.store.update(jid, decision_memo=memo)
            metrics = {**self.store.get(jid)["metrics"], **risk}
            self.store.update(jid, metrics=metrics)
            emit("METRICS", metrics)
            if not risk["passed"]:
                self.store.transition(jid, "REVIEW_REQUIRED", AGENTS[2])
                return  # No contract, artifact vault, or wallet action on failed risk.
            enforce_parity(audit)
            validate_sources()
            self.store.transition(jid, "SETTLING", AGENTS[3])
            verified_terms = {"principal": risk["approved_limit"], "loan_cap": risk["approved_limit"],
                              "annual_rate": terms["annual_rate"], "term_months": terms["duration_months"],
                              "job_id": jid, "currency": "MYR", "is_demo": True}
            write_json(directory / "evidence" / "loan_terms.json", verified_terms)
            history, cursor = [], 0
            while True:
                events = self.store.events(jid, cursor)
                if not events:
                    break
                history.extend(events)
                cursor = events[-1]["seq"]
            write_json(directory / "evidence" / "execution_events.json", history)
            # Every declared source resolves beneath this immutable job directory.
            # POSIX manifest paths stay portable on Windows and Unix.
            evidence_sources = [{"name": "inputs/" + s["name"], "sha256": s["sha256"]} for s in sources]
            evidence_sources += [{"name": p.relative_to(directory).as_posix(), "sha256": hash_file(p)}
                                 for folder in ("evidence", "normalized")
                                 for p in sorted((directory / folder).glob("*.json"))]
            candidate_template = repo / "backend/lambda/send-email/Out_In_TNC_Agreement.pdf"
            original_template = candidate_template if candidate_template.is_file() else ROOT / "reference/Out_In_TNC_Agreement.pdf"
            template = None
            if original_template.is_file():
                expected_template_hash = hash_file(original_template)
                template = directory / "reference-template.pdf"
                shutil.copyfile(original_template, template)
                if hash_file(template) != expected_template_hash:
                    raise EvidenceError("Template changed while snapshotting job evidence")
            self.store.settle_demo(jid, risk["approved_limit"])
            # SDK mode can use real extraction/models but all settlement stays demo.
            names = await call(AGENTS[3], "compile_tnc_agreement", build_artifacts, directory, evidence_sources,
                               proposal["invoices"], audit, risk, verified_terms, "demo", template)
            validate_sources()
            # Built-in quality gate: re-verify the exact committed bundle and every
            # declared source before the job may report COMPLETED. Failure closes
            # the job as FAILED and rolls back the simulated wallet record.
            self_check = await call(AGENTS[3], "verify_bundle_integrity", verify_bundle_integrity, directory)
            write_json(directory / "self_check.json", self_check)
            self.store.update(jid, metrics={**self.store.get(jid)["metrics"], "self_check": self_check})
            emit("METRICS", {"self_check": self_check})
            self.store.finalize_demo(jid)
            vault = [{"name": n, "url": f"/api/v2/jobs/{jid}/artifacts/{n}"} for n in names]
            self.store.update(jid, artifacts=vault, settlement="SIMULATED_ONLY", legal_status="demo_attestation_not_legal_countersignature")
            for artifact in vault:
                emit("ARTIFACT_GENERATED", artifact)
            self.store.transition(jid, "COMPLETED", AGENTS[3])
        except asyncio.CancelledError:
            self.store.rollback_wallet(jid)
            self.store.update(jid, error="Execution interrupted; no real funds moved.")
            if self.store.get(jid)["state"] not in TERMINAL:
                self.store.transition(jid, "FAILED")
            raise
        except Exception as exc:
            self.store.rollback_wallet(jid)
            # Keep internal stack/log details off telemetry. Evidence errors are
            # controlled reader-facing validation messages, not arbitrary SDK output.
            message = str(exc) if isinstance(exc, EvidenceError) else "Execution failed closed; inspect server locally."
            self.store.update(jid, error=message, artifacts=[])
            if self.store.get(jid)["state"] not in TERMINAL:
                self.store.transition(jid, "FAILED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--workspace", default=str(ROOT / "workspace"))
    args = parser.parse_args()
    store = Store(Path(args.workspace))
    asyncio.run(UnderwritingEngine(store).run(args.job_id))
    print(json.dumps(store.get(args.job_id), indent=2))
