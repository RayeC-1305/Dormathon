"""Optional synthetic browser smoke test; no SDK inference or real funds.

Requires an already installed agent-browser CLI and Chromium. Starts a temporary
loopback demo worker with no operator key; never use real data in this test.
Usage: python scripts/browser_smoke.py --node NODE --browser-cli CLI
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--browser-cli", required=True)
    parser.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()
    origin = f"http://127.0.0.1:{args.port}"
    environment = os.environ.copy()
    for name in list(environment):
        if name.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"} or name.startswith(("AGENT_BROWSER_", "OUT_IN_MYINVOIS_")):
            environment.pop(name)
    environment.update({
        "OUT_IN_MODE": "demo",
        "OUT_IN_API_KEY": "",
        "OUT_IN_WORKSPACE": str(ROOT / "workspace/browser-release"),
        "OUT_IN_ALLOWED_ORIGINS": origin,
        "OUT_IN_REPO_ROOT": str(ROOT),
        "AGENT_BROWSER_DEFAULT_TIMEOUT": "15000",
        "AGENT_BROWSER_IDLE_TIMEOUT_MS": "60000",
        "NO_PROXY": "localhost,127.0.0.1",
        "PATH": str(Path(args.node).parent) + os.pathsep + environment.get("PATH", ""),
    })
    environment.pop("OUT_IN_OCR_ADAPTER", None)
    environment.pop("OUT_IN_OCR_ADAPTER_SHA256", None)
    client = httpx.Client(trust_env=False, timeout=1)
    try:
        client.get(origin + "/api/v2/health")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        pass
    else:
        raise RuntimeError("Refusing to reuse an existing service on the selected port")
    worker = subprocess.Popen([
        sys.executable, "-m", "uvicorn", "agent_service.app:app", "--app-dir", str(ROOT),
        "--host", "127.0.0.1", "--port", str(args.port), "--workers", "1", "--no-access-log",
    ], cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = {"synthetic_only": True, "real_funds": False, "live_sdk_verified": False,
               "browser_api_key_required": False, "steps": [], "completed": False,
               "validated_at": datetime.now(timezone.utc).isoformat()}

    def browser(*command, timeout=30):
        run = subprocess.run([args.node, args.browser_cli, "--session", "out-in-release", *command],
                             env=environment, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        if run.returncode:
            raise RuntimeError(f"Browser step {command[0]} failed: {run.stderr.strip()[:500]}")
        results["steps"].append(command[0])
        return run.stdout

    try:
        deadline = time.monotonic() + 15
        while True:
            if worker.poll() is not None:
                raise RuntimeError("Demo worker stopped during startup")
            try:
                response = client.get(origin + "/api/v2/health")
                if response.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("Demo worker startup deadline exceeded")
        browser("open", origin)
        browser("set", "viewport", "1440", "1100")
        snapshot = browser("snapshot", "-i")
        if "Execute Autonomous Underwriting" not in snapshot:
            raise RuntimeError("Mission Control did not render")
        browser("upload", "input[type=file]", *[str(ROOT / "demo/dossier" / name)
                for name in ("invoice.pdf", "packing_list.pdf", "statement.xlsx")])
        for name, value in {"requested_limit": "40000", "duration_months": "12",
                            "annual_rate": "0.08", "monthly_debt_service": "1000"}.items():
            browser("fill", f"input[name={name}]", value)
        browser("click", "button[type=submit]")
        browser("wait", ".vault-panel", timeout=25)
        snapshot = browser("snapshot")
        if "MasterLedger.xlsx" not in snapshot or "COMPLETED" not in snapshot:
            raise RuntimeError("Completed vault contents were not observed")
        results["completed"] = True
        results["artifact_vault_verified"] = True
        # Use the control's observed accessibility label, not storage injection.
        theme = json.loads(browser("eval", "JSON.stringify(document.documentElement.dataset.theme)"))
        if theme != "dark":
            browser("click", "button[aria-label='Switch to dark theme']")
        browser("screenshot", str(ROOT / "demo/mission-control-dark.png"), "--full")
        browser("click", "button[aria-label='Switch to light theme']")
        browser("screenshot", str(ROOT / "demo/mission-control-light.png"), "--full")
        results["themes_verified"] = True
        browser("open", origin + "/admin/review")
        if "Review queue" not in browser("snapshot"):
            raise RuntimeError("Direct review deep link did not render")
        results["review_deep_link_verified"] = True
    except subprocess.TimeoutExpired:
        results["error"] = "Browser automation command exceeded its bounded timeout; browser end-to-end verification is incomplete"
    except Exception as exc:
        results["error"] = str(exc)[:700]
    finally:
        try:
            browser("close", timeout=15)
            results["browser_closed"] = True
        except Exception:
            results["browser_closed"] = False
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=5)
        results["worker_stopped"] = worker.poll() is not None
        client.close()
        (ROOT / "demo/browser_validation.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0 if results.get("completed") and results.get("themes_verified") and results.get("review_deep_link_verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
