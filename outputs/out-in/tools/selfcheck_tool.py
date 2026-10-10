"""In-pipeline bundle self-verification; demo integrity only.

Reuses the standalone verifier so the exact committed nine-file bundle and
every declared source are re-checked before a job may report COMPLETED. A
failed self-check fails the job closed and rolls back the simulated wallet
record; the scheduler never trusts its own staging step without verification.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path
from tools.reconciliation_tool import EvidenceError

ROOT = Path(__file__).resolve().parents[1]


def _verifier():
    # Load the reviewed standalone script directly so no sys.path working
    # directory assumption sneaks into the runtime contract.
    spec = importlib.util.spec_from_file_location("out_in_verify_manifest", ROOT / "scripts" / "verify_manifest.py")
    if spec is None or spec.loader is None:
        raise EvidenceError("Cannot load the standalone bundle verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_bundle_integrity(job_dir) -> dict:
    job_dir = Path(job_dir).resolve()
    artifacts = job_dir / "artifacts"
    if not artifacts.is_dir():
        raise EvidenceError("Committed artifact bundle is missing")
    module = _verifier()
    try:
        result = module.verify_manifest(artifacts, source_dir=job_dir)
    except module.VerificationError as exc:
        raise EvidenceError(f"Committed bundle failed self-verification: {exc}") from exc
    if not isinstance(result, dict) or result.get("verified") is not True:
        raise EvidenceError("Bundle self-verification did not confirm integrity")
    return {**result, "self_check": "post_commit", "is_demo": True}
