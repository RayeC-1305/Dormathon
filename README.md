# Dormathon — OUT&IN Mission Control

An agent web application for bounded, audit-grade SME invoice underwriting and
cross-border financing: React operations console, FastAPI scheduler, native
CodeBuddy SDK extraction and narrative agents, exact `Decimal` reconciliation,
reviewed scikit-learn credit gates, read-only LHDN MyInvois retrieval,
plain-language decision memos, and downloadable self-verified signed integrity
evidence.

**Settlement, wallet entries, contracts and attestations are demonstration-only
in every mode. No real funds are moved.**

## Layout

| Path | Contents |
|---|---|
| `outputs/out-in/` | The actual application (backend, tools, frontend, tests, docs) |
| `out-in-sdk-starter/` | Original chat SDK starter retained as provenance reference only |

## Quick start (Windows PowerShell 5.1, Python 3.13, Node 22.20+)

```powershell
& ./outputs/out-in/scripts/deploy_demo.ps1 -CopyKeyToClipboard
```

Then open http://127.0.0.1:8000, paste the operator key from the clipboard, and
click **Run end-to-end demo** — one action executes the full autonomous
underwriting pipeline on the synthetic dossier.

## Documentation

- **[outputs/out-in/README.md](outputs/out-in/README.md)** — product overview,
  setup (all platforms), workflow, underwriting policy, API, security,
  verification, tests, operations and contributing guidelines.
- **[outputs/out-in/INTEGRATION.md](outputs/out-in/INTEGRATION.md)** —
  architecture, adapter contracts, SDK/MCP details, MyInvois integration and
  production boundaries.
- **[outputs/out-in/PITCH.md](outputs/out-in/PITCH.md)** — Dormathon pitch
  script and criterion-by-criterion rubric alignment map.

## Verification

```powershell
cd outputs/out-in
& ./.venv/Scripts/python.exe scripts/prepare_demo.py
& ./.venv/Scripts/python.exe -m pytest tests -q   # 992 offline tests
npm.cmd --prefix frontend run build
```

Current executed suite: 992 passing offline tests and a clean Vite production
build, on Python 3.13.16 and Node 22.22.2.
