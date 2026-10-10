# Dormathon — OUT&IN Mission Control

An agent web application for bounded, audit-oriented SME invoice underwriting:
React operations console, FastAPI scheduler, native CodeBuddy SDK extraction,
exact `Decimal` reconciliation, reviewed scikit-learn credit gates, read-only
LHDN MyInvois retrieval, and downloadable signed integrity evidence.

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

Then open http://127.0.0.1:8000 and paste the operator key from the clipboard.

Full documentation, manual setup for other platforms, credit policy, API
contracts, MyInvois integration, verification commands, tests and limitations:
**[outputs/out-in/README.md](outputs/out-in/README.md)** and
**[outputs/out-in/INTEGRATION.md](outputs/out-in/INTEGRATION.md)**.
