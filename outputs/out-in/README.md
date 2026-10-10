# OUT&IN Mission Control

A single-owner SME invoice-underwriting workbench with a React operations console, FastAPI service, bounded native CodeBuddy SDK extraction, exact financial reconciliation, reviewed model inference, and downloadable integrity evidence.

**Settlement, wallet entries, contracts and legal attestations are demonstration-only in every mode. No real funds are moved.** `live` enables SDK extraction and non-demo models; it does not enable real financing. This is not a certified, production-ready, multi-tenant or independently anchored audit system. Cross-border FX, production payments and identity-backed legal signing are not implemented.

This README describes **`outputs/out-in`**, the actual application in the Dormathon repository. `out-in-sdk-starter` is an older React/Express chat reference, not this service. Detailed interfaces and adapter contracts are in [INTEGRATION.md](INTEGRATION.md). The Dormathon pitch script and criterion-by-criterion rubric alignment map are in [PITCH.md](PITCH.md).

## Quick start: directly copyable Windows deployment

Use a normal-user **Windows PowerShell 5.1** terminal, a trusted checkout, **Python 3.13**, and **Node 22.20+ within 22.x** with `npm.cmd` on PATH. The reviewed local runtimes report Python 3.13.16 and Node 22.22.2; other Python minor versions are not claimed as verified. Internet access is needed for initial package installation.

From the **Dormathon repository root**, copy and run:

```powershell
& ./outputs/out-in/scripts/deploy_demo.ps1 -CopyKeyToClipboard
```

From the application directory instead:

```powershell
& ./scripts/deploy_demo.ps1 -CopyKeyToClipboard
```

The script creates an application-local `.venv` if absent, installs `requirements.txt` into that environment, runs `pip check`, uses `npm ci --include=dev`, builds the SPA, regenerates explicitly synthetic demo fixtures/models, and then starts one foreground Uvicorn worker at **http://127.0.0.1:8000**. It clears inherited MyInvois/OCR integration settings and forces demo mode. It does not change machine-wide execution policy, install globally, launch detached processes, or enable real settlement. If your organization's policy blocks scripts, use the manual commands below rather than weakening that policy.

Options:

```powershell
# Choose a Python executable and dedicated private workspace.
& ./outputs/out-in/scripts/deploy_demo.ps1 `
  -PythonExe "C:/Python313/python.exe" `
  -WorkspacePath "C:/PrivateData/OutIn"

# Subsequent preparation/launch using an existing reviewed environment.
& ./outputs/out-in/scripts/deploy_demo.ps1 -ReuseEnvironment -ReuseWorkspace -CopyKeyToClipboard

# Validate prerequisites only: no installs, generated files, keys or server.
& ./outputs/out-in/scripts/deploy_demo.ps1 -ValidateOnly

# Prepare/build without starting a backend.
& ./outputs/out-in/scripts/deploy_demo.ps1 -ReuseEnvironment -PrepareOnly

# Explicitly copy the operator key for pasting into the browser.
& ./outputs/out-in/scripts/deploy_demo.ps1 -ReuseEnvironment -CopyKeyToClipboard
```

`-EnvironmentPath` selects the venv directory; `-WorkspacePath` selects the evidence/database/private-key directory; `-Port` selects a loopback port (default 8000). `-ReuseEnvironment` is required when the environment already exists; pinned requirements are still checked/installed. The script rejects linked/junction source, dependency, environment and workspace paths to avoid installing through shared directories. Preparation rebuilds dependencies and **replaces synthetic demo assets**; never run it while jobs are active. It never deletes or resets a workspace. Existing workspaces require explicit `-ReuseWorkspace`; stop every owning backend before opting in. Reusing one triggers the application's fail-closed recovery rules, not resume. Only `.venv` and `workspace` are permitted inside the checkout; alternative environment/workspace locations must be external, mutually nonoverlapping directories. Named locks prevent concurrent copies of this launcher from preparing the same source/workspace, and a port precheck catches occupied selected ports. These checks cannot detect manually launched services or remove filesystem races: stop those separately before preparation. Caller environment configuration is restored when the launcher exits.

The script preserves a securely supplied `OUT_IN_API_KEY` (at least 32 printable ASCII characters, without spaces) or silently generates a session key when you explicitly request clipboard delivery. Without either input, it fails before installation so it cannot start an unusable protected UI. It never prints the key. Enter your securely supplied value in the browser, or explicitly use `-CopyKeyToClipboard` and clear the clipboard after entry. Do not enable transcripts, command tracing, or secret-bearing logs. Clipboard history/synchronization is a separate exposure to assess. The frontend keeps the operator key only in React memory; a reload requires re-entry. No persistent secret store is implemented.

Stop with **Ctrl+C** after active jobs finish. The script is a local demo launcher, not a Windows service installer, cloud publisher, TLS proxy or production deployment.

## Implementation and repository layout

```text
outputs/out-in/
  README.md / INTEGRATION.md       Scope, setup, interfaces and limitations
  PITCH.md                         Dormathon pitch script and rubric alignment map
  requirements.txt / .gitignore    Top-level Python pins; source hygiene
  agent_service/
    app.py                        Intake/auth/REST/WS/SPA serving
    orchestrator.py               Four-stage scheduler, retries and state
    sdk_runtime.py                Native SDK extraction/narrative and application hooks
    store.py                      SQLite jobs/events/simulated wallet/recovery
    myinvois_client.py             Fixed-host read-only OAuth/raw transport
  tools/
    ocr_tool.py                    PDF normalization and retry OCR/adapters
    reconciliation_tool.py        Authoritative exact Decimal audit/bank reader
    ml_underwrite_tool.py          Reviewed models/adapters and deterministic gates
    artifact_tool.py               Demo workbook/PDF/attestations/manifest
    decision_memo_tool.py          Plain-language memo from deterministic evidence only
    selfcheck_tool.py              Post-commit bundle self-verification gate
    myinvois_tool.py                Strict UBL mapping and commercial binding
  scripts/
    deploy_demo.ps1                Copy-and-run Windows demo deployment
    prepare_demo.py                Generate synthetic dossiers and demo models
    verify_manifest.py             Offline signature/hash/source verification
    browser_smoke.py               Optional synthetic browser test
    package_release.py             Allowlisted historical ZIP exporter
  tests/                          Engine, API, SDK and MyInvois regressions
  frontend/
    package.json / package-lock.json / vite.config.js / index.html
    src/                          Mission Control, review queue and WS hooks
  reference/
    Out_In_TNC_Agreement.pdf        Optional agreement layout reference
    reconciliation_handler.py      Legacy reference; never audit authority
```

Generated `demo/`, `frontend/dist/`, installed dependencies, venvs, caches and runtime `workspace/` are **not Git source**. Run the preparation script to generate the demo before testing or launching. The optional template is retained to preserve template-backed demonstration output; a reference PDF never confers legal enforceability.

Runtime workspace:

```text
workspace/
  jobs.sqlite3                    WAL database; sidecars may exist
  keys/                           Persistent PRIVATE demo signing keys
  incoming/<intake-id>/           Intake/rejected payloads can be retained
  jobs/<job-id>/
    inputs/                       Original accepted PDFs and statement
    normalized/                   Attempt-specific evidence JSON
    evidence/                     Proposals/audits/features/risk/terms/events
                                  Optional raw MyInvois/provenance snapshots
    crops/                        Retry evidence images when needed
    reference-template.pdf        Optional per-job template snapshot
    staging/                      Failed staging retained for diagnosis
    artifacts/                    Committed nine-file demo bundle
    quarantine/variance_report.json
```

Do not publish workspaces, keys, statements, dossiers, SQLite files, credentials or local validation output. Python requirements are **17 top-level pins**, not a complete transitive dependency lock. Frontend `package-lock.json` is committed (lockfile v3, matching manifest); use `npm ci`, not an install that silently updates the lockfile. Model pickles execute trusted-admin code: never treat uploaded dossiers as a source of executable models.

## Manual setup and single-port launch

### Windows PowerShell 5.1

From a fresh application checkout, with no existing `.venv`:

```powershell
Set-Location "C:/Dormathon/outputs/out-in"
py -3.13 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
$Python = (Resolve-Path ".venv/Scripts/python.exe").Path
& $Python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency check failed" }
npm.cmd --prefix frontend ci --include=dev --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw "Frontend installation failed" }
npm.cmd --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
& $Python scripts/prepare_demo.py
if ($LASTEXITCODE -ne 0) { throw "Demo generation failed" }
$env:OUT_IN_MODE = "demo"
$env:OUT_IN_REPO_ROOT = (Get-Location).Path
$env:OUT_IN_WORKSPACE = Join-Path (Get-Location).Path "workspace"
$env:OUT_IN_MYINVOIS_ENABLED = "0"
$env:OUT_IN_ALLOWED_ORIGINS = "http://127.0.0.1:8000,http://localhost:8000"
$env:OUT_IN_API_KEY = (& $Python -c "import secrets; print(secrets.token_urlsafe(32))").Trim()
# Explicit operator action: copy for browser entry; clear clipboard/history afterward.
Set-Clipboard -Value $env:OUT_IN_API_KEY
& $Python -m uvicorn agent_service.app:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Replace the example checkout path with yours. These manual commands assume no inherited OCR adapter configuration; use the deployment script for explicit isolation. Calling the venv interpreter directly avoids activation-script execution-policy issues. Do not recreate a venv over an environment you intend to preserve.

### Linux/macOS or Git Bash

From a fresh trusted checkout, use Python 3.13 and Node 22.20+ within 22.x:

```bash
set -euo pipefail
cd /path/to/Dormathon/outputs/out-in
python3.13 -m venv .venv
PYTHON="$PWD/.venv/bin/python"
# On Windows Git Bash use PYTHON="$PWD/.venv/Scripts/python.exe".
"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" -m pip check
npm --prefix frontend ci --include=dev --no-audit --no-fund
npm --prefix frontend run build
"$PYTHON" scripts/prepare_demo.py
export OUT_IN_MODE=demo OUT_IN_MYINVOIS_ENABLED=0
export OUT_IN_REPO_ROOT="$PWD" OUT_IN_WORKSPACE="$PWD/workspace"
export OUT_IN_ALLOWED_ORIGINS="http://127.0.0.1:8000,http://localhost:8000"
unset OUT_IN_OCR_ADAPTER OUT_IN_OCR_ADAPTER_SHA256
export OUT_IN_API_KEY="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(32))')"
"$PYTHON" -m uvicorn agent_service.app:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Keep `set -x` disabled. Do not use `sudo npm install` or global installs. Windows is the currently inspected baseline; Unix commands are provided for equivalent setup, not a claim that every OS has been tested.

### Frontend development

Run the backend on port 8000 and, in a second terminal from the application root:

```powershell
npm.cmd --prefix frontend ci --include=dev --no-audit --no-fund
npm.cmd --prefix frontend run dev -- --port 5173 --strictPort
```

Include `http://127.0.0.1:5173` and `http://localhost:5173` in `OUT_IN_ALLOWED_ORIGINS` **before** backend startup. Vite binds loopback and proxies `/api` HTTP and WebSocket traffic to port 8000. `--strictPort` prevents silent origin changes. `npm run preview` is not the documented API-proxied setup.

For single-port serving, **build before backend import**: FastAPI mounts `frontend/dist` only if present at import time. Missing non-API routes fall back to the SPA; unknown `/api` routes remain 404. Never restart an active dossier just to mount a new build.

## Run the synthetic demonstration

1. Open Mission Control at http://127.0.0.1:8000 and enter the configured operator key.
2. Fastest path: click **Run end-to-end demo** — one action; the backend stages its generated synthetic dossier with the canonical demo terms and executes the full pipeline (`POST /api/v2/demo-run`, operator-key protected, demo mode only). Manual path instead: click **Download demo dossier**. **Extract the ZIP first**; upload `invoice.pdf`, `packing_list.pdf`, and `statement.xlsx` together. ZIP intake is unsupported.
3. Enter these synthetic parameters:

   | Field | Value |
   |---|---|
   | Requested limit | `40000.00` MYR |
   | Duration | `12` months |
   | Annual rate | `0.08` (8%, not `8`) |
   | Existing monthly debt service | `1000.00` MYR |

4. Submit once and follow reconciliation, risk, state transitions and artifacts. HTTP acceptance means **queued**, not approved. POST is not automatically retried or request-idempotent; inspect the stored job before resubmitting after a disconnect.
5. Completed jobs expose nine demo artifacts. Review jobs expose no contract/wallet action. Exhausted reconciliation exposes a variance report.
6. Verify artifacts and sources before treating the demonstration bundle as intact.

The generator uses synthetic invoice `DEMO-INV-001`, 100 units at `1000.00`, total `100000.00` MYR, FOB and `DEMO-SEAL-001`. The statement records `100000.00` inflow and `30000.00` outflow in October 2026. These are test fixtures, not market data or real borrower records. The synthetic integration test expects `40000.00` demo principal; this is not calibrated credit performance or a financing promise.

UI routes: `/` is Mission Control; `/admin/dashboard` and `/assistant` are aliases; `/admin/review` is a **read-only** review queue. There is no chat/SSE interface or approve/release button behind these aliases. Theme is persisted in local storage; up to 20 job/token references in session storage. Tokens are sensitive bearer credentials, not tenant isolation.

## Workflow and exact underwriting policy

| Stage | Authority | Output |
|---|---|---|
| `trade-extraction-proposer` | Marked synthetic parser in demo; bounded SDK agent in live | Evidence-cited proposal; no financing authority |
| `reconciliation-auditor` | Scheduler-owned deterministic Python/Decimal | Exact monetary and shipping verdict |
| `ml-risk-underwriter` | Reviewed model inference plus fixed gates | Approved **demo** limit or review proposal |
| `settlement-legal-synthesizer` | Scheduler-owned simulation/artifact compiler | Demo wallet entry and integrity bundle |
| `compliance-narrative-explainer` | Deterministic text always; optional bounded live SDK restatement | Advisory plain-language decision memo |

Dossier PDFs are normalized **in parallel** inside each extraction cycle. After
risk evaluation the scheduler records a plain-language **decision memo**
(`evidence/decision_memo.json`, also on the job record) whose figures are
copied verbatim from deterministic evidence. In live mode, setting
`OUT_IN_NARRATIVE_AGENT=1` lets a second bounded SDK agent restate the memo;
its figures must exactly equal the deterministic values or the submission is
rejected and the deterministic text stands (`narrative_source` records the
outcome). Before a job may report `COMPLETED`, the scheduler **re-verifies the
committed nine-file bundle and every declared source** in-process
(`verify_bundle_integrity`, recorded in `metrics.self_check` and
`self_check.json`); a failed self-check fails the job closed and rolls back
the simulated wallet record. Only live extraction (and the optional narrative)
executes an SDK agent. Python owns later calls, retries and state transitions.
Terminal states are `COMPLETED`, `REVIEW_REQUIRED`, `QUARANTINED`, and `FAILED`.

### Bounded self-healing

Cycles `0..3` mean one initial extraction/audit and **at most three repairs**, with progressively higher-resolution retry evidence. Exhausted parity or commercial/MyInvois binding mismatch quarantines and prohibits disbursement. Malformed evidence, tampering, unsupported profiles, adapter/transport errors or unsuccessful SDK completion can fail immediately; retries are not universal. Never edit source values to manufacture parity.

### Reconciliation

Money uses bounded `Decimal` arithmetic, with no tolerance or silent sub-cent rounding:

```text
sum(quantity * unit_price) = subtotal
subtotal + tax + freight - discount = total
total = sum(bank inflows linked to that invoice)
```

Delivery quantity must match by invoice/SKU; seals and Incoterms must agree. Variance is the largest absolute recorded monetary discrepancy, not a cancelling net sum. **Zero monetary variance does not mean shipping or the overall audit passed.** When binding prevents establishing amounts, variance can be `null`, never an invented zero.

Exactly one bank statement and **one observed ledger month** are required, MYR only. There is no missing-month inference, statement-completeness assurance or implicit FX conversion. CSV is UTF-8; XLSX uses `BankLedger` and literal, nonformula values. Headers:

```text
transaction_id, invoice_id, currency, inflow, outflow, date
```

Transaction IDs must be unique; flows nonnegative; an entry cannot have both positive inflow and outflow. Invoice receipts must link to the matching invoice. Existing debt service is operator-declared with recorded provenance, not bank-verified total debt.

### Credit and repayment gates

Passing risk requires **default probability <= 0.12**, **all-in DSR <= 0.45**, and a positive principal bounded by all four ceilings:

```text
capacity = max(0, 0.45 * monthly_inflow - existing_monthly_debt_service)
per_unit_payment = 1 / duration_months + annual_rate / 12
capacity_limit = floor_to_cents(capacity / per_unit_payment)
limit = floor_to_cents(min(requested_limit, model_financing_ceiling,
                           0.80 * verified_invoice_total, capacity_limit))
all_in_DSR = (existing_monthly_debt_service + maximum_scheduled_installment)
             / monthly_inflow
```

Flat simple interest is computed on original principal:

```text
total_interest = HALF_UP_to_cents(principal * annual_rate * months / 12)
```

Principal and interest are separately allocated to conserve each cent. The final installment can receive a larger remainder; the DSR gate uses the maximum actual installment. `metrics.dsr` is all-in DSR; `metrics.existing_dsr` is existing debt only.

Failed risk sets `approved_limit` to `"0.00"`. A **50% requested-limit haircut is only a bounded human-review proposal**, also capped by model/invoice/capacity limits. It may be smaller or zero and never bypasses risk/reconciliation. Quarantined dossiers never reach this risk proposal. There is no approve, override, release or real-payment endpoint. The old project's 95% policy is not implemented.

## API, security and configuration

Configuration is read from the **process environment**, not an automatically loaded `.env` file. Mode, API key, origins and workspace are evaluated at backend import.

| Variable | Purpose/default |
|---|---|
| `OUT_IN_MODE` | `demo` or `live`; default `demo`; settlement remains simulated |
| `OUT_IN_WORKSPACE` | Private absolute workspace; default application `workspace/` |
| `OUT_IN_API_KEY` | Shared operator key; empty demo value disables protected-route checks; live needs >=32 characters |
| `OUT_IN_ALLOWED_ORIGINS` | Comma-separated exact WebSocket origins; default loopback 5173/8000 variants |
| `OUT_IN_REPO_ROOT` | Reviewed model/adapter repository; default application root |
| `OUT_IN_OCR_ADAPTER` / `OUT_IN_OCR_ADAPTER_SHA256` | Optional reviewed, pinned OCR callable |
| `OUT_IN_NARRATIVE_AGENT` | `1` opts in to the bounded live SDK narrative restatement of the decision memo; default off |
| `OUT_IN_MYINVOIS_*` | Server-side opt-in read-only integration; see below |

Protected HTTP endpoints require `X-API-Key` when configured. Public routes remain accessible:

| Endpoint | Access/purpose |
|---|---|
| `GET /api/v2/health` | Public health, mode, `real_funds: false` |
| `GET /api/v2/config` | Public demo/auth/integration configuration |
| `GET /api/v2/demo-dossier` | Public generated synthetic ZIP |
| `POST /api/v2/demo-run` | Protected one-action synthetic end-to-end run; demo mode only |
| `POST /api/v2/underwrite/batch` | Protected multipart intake |
| `GET /api/v2/jobs/{jid}` | Protected stored job snapshot |
| `GET /api/v2/review-queue` | Protected review-only queue |
| `GET /api/v2/jobs/{jid}/artifacts/{name}` | Protected published artifacts/report |
| `GET /api/v2/jobs/{jid}/myinvois-sources/{name}` | Protected retained hash-checked API/provenance sources |
| `WS /api/v2/agent-stream` | Job-scoped token and supplied-origin validation |
| `/docs`, `/redoc`, `/openapi.json` | Default **public** FastAPI API documentation/schema |

Batch fields: `files`, `requested_limit` (default `40000.00`), `duration_months` (12), `annual_rate` (`0.08`), `monthly_debt_service` (`1000.00`), optional `myinvois_document_ids`. Limits: 2–20 PDF/XLSX/CSV files, at least one PDF and exactly one statement; 20 MiB per file, 50 MiB per dossier; requested principal positive and <= MYR 1 billion; debt 0..1 billion; term 1..120 months; rate fraction 0..1. Filenames normalize uniquely; traversal, malformed files, XLSX macros/external links and archive-resource excess fail closed.

Accepted intake returns HTTP 202 with `job_id`, `stream_token`, and `state: "QUEUED"`. **Do not log the raw response.** WebSocket query is `job_id=...&token=...&after=...`; invalid requests close with code 1008. Stream tokens necessarily appear in URLs: keep proxy/access logs disabled or reliably redacted. REST snapshots reconcile after reconnect; the UI retains up to 300 events and attempts six reconnects.

The API key is a local shared-operator boundary, **not** user authentication, JWT/RBAC or tenant isolation. Origin validation is not a replacement for authentication. Public API documentation is not hidden by configuring a key. There is no implemented public-internet security perimeter, per-user authorization, comprehensive logging redaction or rate-limiting service.

## Live extraction and model prerequisites

Do not enable live mode merely by relabelling synthetic models. Before processing real evidence, independently review security, privacy, data, model, legal and operations controls.

- Authorize the installed **`codebuddy-agent-sdk==0.3.274`** runtime/provider for **`deepseek-v3.1`** through its supported mechanism. The service key is not a provider credential; no new login helper is implemented.
- Supply reviewed non-demo pickles, manifest and hash-pinned adapters in `OUT_IN_REPO_ROOT`: `ml/feature_engineering.py: build_features(metrics)` and `ml/predict.py: infer_credit(classifier, regressor, frame, default_class)`. Enforce matching sklearn version, ordered training schema, default class and model hashes.
- The scheduler collects six policy metrics: monthly inflow/outflow/debt, invoice total, cashflow margin and existing DSR, plus request/provenance. Supporting an original model schema does **not** supply missing tenure/tracking/etc. inputs. Missing inputs must fail closed; no invented defaults.
- Embedded-text normalization is supported. Scan-only input needs reviewed OCR integration. Installing `pytesseract` alone does not install Tesseract; current Tesseract invocation is retry-only, not an unconditional initial vision/OCR pass. A scan-only dossier can fail before repair.
- Review optional `normalize_invoice(path, regions)` OCR adapters and their hash, code, privileges and any network egress separately.

SDK extraction has 12 turns and a 180-second request bound. Application hooks allow controlled normalized-evidence reads, bounded 16,000-character chunks, OCR normalization and proposal submission. They block shell, writes, web access, delegation and later financial tools. MyInvois extraction additionally requires complete programmatic evidence-read coverage. The proposer subprocess receives blanked operator/MyInvois credentials, but other provider environment can remain inherited. **Hooks are not an OS sandbox**, and trusted in-process adapters/models execute with service privileges. Deterministic thread-based calls do not provide hard-kill timeouts.

Authenticated provider execution, original model performance and missing original feature integrations remain unverified. Completed live-mode bundles still carry **demo** settlement/legal status.

## Read-only LHDN MyInvois integration

Optional `myinvois_document_ids` accepts up to ten distinct opaque IDs separated by commas, semicolons or whitespace (1–128 ASCII letters/digits/underscore/hyphen each; maximum form length 2,000 characters). IDs are neither public authorization tokens nor necessarily RFC-4122 UUIDs. UI exposes intake only when the backend enables it.

Configure server-side process variables before starting the existing single-worker backend:

```text
OUT_IN_MODE=live
OUT_IN_API_KEY=<secure shared operator key, at least 32 characters>
OUT_IN_MYINVOIS_ENABLED=1
OUT_IN_MYINVOIS_ENVIRONMENT=sandbox
OUT_IN_MYINVOIS_AUTH_MODE=taxpayer
OUT_IN_MYINVOIS_CLIENT_ID=<authorized ERP client ID>
OUT_IN_MYINVOIS_CLIENT_SECRET=<authorized ERP client secret>
```

The angle-bracket descriptions are not executable credentials. Do not paste secrets into chat, source, logs or URLs. Production uses separately authorized credentials and `OUT_IN_MYINVOIS_ENVIRONMENT=production`. Intermediary mode requires `OUT_IN_MYINVOIS_AUTH_MODE=intermediary` and an authorized `OUT_IN_MYINVOIS_ON_BEHALF_OF` identity. Access depends on authorization; this integration does not expand LHDN permissions. Live SDK/model/OCR prerequisites still apply.

Implementation permits only:

```text
POST /connect/token
GET /api/v1.0/documents/{id}/raw
sandbox:    https://preprod-api.myinvois.hasil.gov.my
production: https://api.myinvois.hasil.gov.my
```

Tokens are cached in memory. Transport rejects redirects, inherited proxies, compressed responses and malformed strict JSON, bounds responses/execution, and permits one 401 renewal. Rate limiting retains cooldown and fails closed. There is no submission, cancellation, rejection, search polling or fund movement.

**Supported application profile—not all valid tax invoices:** ordinary invoice `01`, UBL `1.0/1.1`, MYR, exact cent-valued arithmetic, no nonzero unsupported allowances/charges/freight/prepayments/rounding. Unsupported profiles fail closed. Eligibility requires valid, non-cancelled/non-rejected retrieval-time status and validated timestamps.

Upload independent matching commercial invoice PDFs, delivery PDFs and exactly one statement with the IDs. Party TINs, invoice ID, values and goods identity must bind exactly. Classification codes are not invented into SKUs; shipping still needs independent agreement. Raw responses and provenance are retained/hash-pinned **once per job**, within a 50 MiB aggregate budget, then reused through repairs. Persistent binding mismatch quarantines; unknown amounts/variance remain `null`. Invalid profiles, transport errors or source tampering fail before risk.

Status is a **retrieval-time snapshot**, not continuous validity, source-certificate verification, payment/delivery proof or collateral uniqueness. Cancellation races and cross-job duplicate-financing prevention are unresolved production work. **No authenticated LHDN retrieval has been validated; automated integration tests use invented offline responses.**

Provider references (consult current contracts before live use): [taxpayer login](https://sdk.myinvois.hasil.gov.my/api/07-login-as-taxpayer-system/), [intermediary login](https://sdk.myinvois.hasil.gov.my/api/08-login-as-intermediary-system/), [Get Document](https://sdk.myinvois.hasil.gov.my/einvoicingapi/07-get-document/), [FAQ](https://sdk.myinvois.hasil.gov.my/faq/), [mappings](https://sdk.myinvois.hasil.gov.my/mathematical-mappings/). The profile/limits above describe repository implementation, not a freshly verified provider guarantee.

## Artifact and source verification

A completed bundle contains exactly nine files:

```text
MasterLedger.xlsx
Out_In_TNC_Agreement.pdf
demo_public_key.pem
demo_borrower_public_key.pem
demo_lender_public_key.pem
borrower_attestation.sig
lender_attestation.sig
verification_manifest.json
verification_manifest.sig
```

Workbook sheets: `Loan`, `InvoiceLines`, `Invoices`, `Bank`, `Repayments`, `Controls`, `DecimalAudit`. Excel formulas are display/review surfaces; canonical Decimal evidence is the software arithmetic record, not real-world truth.

PowerShell, with the chosen interpreter and an actual completed job ID:

```powershell
$JobId = "REPLACE_WITH_COMPLETED_JOB_ID"
$JobDir = Join-Path $env:OUT_IN_WORKSPACE ("jobs/" + $JobId)
& $Python scripts/verify_manifest.py "$JobDir/artifacts" --source-dir "$JobDir"
```

Bash equivalent:

```bash
JOB_ID="REPLACE_WITH_COMPLETED_JOB_ID"
JOB_DIR="$OUT_IN_WORKSPACE/jobs/$JOB_ID"
"$PYTHON" scripts/verify_manifest.py "$JOB_DIR/artifacts" --source-dir "$JOB_DIR"
```

**`--source-dir` is the entire job root, not just `inputs/`.** Sources can include evidence, normalization, retained API responses and the template snapshot. Downloaded bundles need the complete original source directory for source checks. No unrelated files may be placed in the exact-inventory bundle directory.

Verification checks seven payload hashes, canonicalized manifest-content signature, distinct manifest/borrower/lender public keys, role-bound final-PDF attestations and source/terms hashes. Formatting-equivalent manifest JSON can canonicalize to the same signed content; this is **not** a signature over arbitrary original JSON formatting bytes. Manifest/signature avoid circular self-hashing. Add `--trusted-key-dir` only for public keys acquired independently via a trusted channel; copying keys from the same untrusted bundle is not independent pinning. Private keys must never be distributed.

Local source hashes, demo Ed25519 signatures and SQLite event hash chains establish recorded consistency, **not** source truth, borrower/lender identity, legal enforceability or externally anchored immutability. A local writer can rebuild data/chains/bundles. No trusted timestamp, independent identity registry or immutable audit anchor exists. `execution_events.json` is a pre-artifact snapshot, not all future events; crop images are not automatically separate declared manifest sources.

## Tests and validation

Prepare synthetic assets first, then run from the application root:

```powershell
& $Python scripts/prepare_demo.py
& $Python -m pytest tests -q
npm.cmd --prefix frontend run build
```

Python-explicit Bash: `"$PYTHON" -m pytest tests -q`. A fresh standalone source copy is preferable when isolating generated test data. Do not regenerate assets while a backend is active.

Historical MyInvois release baseline: **975 passing tests**. Current executed suite: **992 passing tests**—295 engine, 69 API/integration, 230 offline SDK, 193 transport, 205 parser/workflow—plus a successful production frontend build. These are software tests, not proof of provider availability or model calibration. Generated JUnit/validation files are intentionally omitted from a source-only Git checkout. See the current delivery validation report for newly executed results.

- API tests use in-process FastAPI TestClient, synthetic PDFs/statements/models, real local reconciliation/inference/artifact generation and restricted socket access.
- SDK tests use installed SDK types with offline substitute clients/tool handlers.
- MyInvois tests use synthetic MockTransport/UBL/binding data; they do not authenticate against LHDN.
- Engine tests cover numerical, adapter, repayment, tamper and artifact boundaries.
- Starlette/httpx TestClient transport currently produces one deprecation warning.

Full browser upload-to-vault/theme end-to-end validation remains incomplete. `scripts/browser_smoke.py` needs separately installed browser tooling/Chromium and is not a runtime dependency. Tests do not establish real payments, legal identity, production tenant isolation, original-model lending performance or authenticated provider/LHDN execution.

## Operations and troubleshooting

Use **one Uvicorn worker and one owning service instance per workspace**. The engine permits two concurrent jobs; intake rejects at eight scheduled tasks. There is no durable distributed scheduler. Do not use `--reload` during active jobs.

Startup recovery fails listed interrupted jobs, rolls back simulation records and retains evidence; **automatic resume is not implemented**. Recovery and review enumeration are bounded to the newest 200 jobs. Shutdown cancellation does not kill already executing deterministic threads or guarantee reversal of every filesystem side effect. Inspect retained staging before assuming rollback.

Rejected intake can remain in `incoming/`; there is no automatic retention cleanup. Back up a quiescent complete workspace and reviewed assets under a reviewed retention policy. Account for WAL/sidecar consistency; copying only a live SQLite main file is insufficient. Protect evidence and private keys. No administrative resume/cancel/approval/key-rotation API is implemented.

| Symptom | Check |
|---|---|
| Deployment refuses existing environment | Review it and use `-ReuseEnvironment`; never overwrite unintentionally |
| Linked/junction checkout rejected | Use a fresh standalone trusted checkout |
| Module/import failure | Use the selected Python 3.13 environment and application root |
| 401 | Enter the current operator key; browser reload clears its in-memory copy |
| Demo ZIP 404 | Generate synthetic fixtures before backend startup |
| ZIP upload rejected | Extract and choose PDFs plus the statement |
| Statement rejected | Exactly one CSV/XLSX, literal required headers, MYR, one dated month |
| Real PDF rejected in demo | Demo requires marked generated packets; reviewed live prerequisites needed |
| Scan-only failure | No unconditional initial Tesseract/vision pass is implemented |
| Model hash/version/schema failure | Restore reviewed compatible assets; never edit hashes to bypass |
| WebSocket/config failure | Correct origin, same-origin proxy, job token and REST snapshot |
| Quarantine with zero variance | Shipping checks can fail independently; inspect `passed` and all checks |
| `REVIEW_REQUIRED` | Proposal is not approval; no wallet/contract action |
| `FAILED` after restart | Investigate retained evidence and submit a new dossier; do not resume manually |
| Missing manifest source | Use the complete job root for `--source-dir` |
| No SPA on 8000 | Build `frontend/dist` before importing/starting FastAPI |

## Contributing

This is a competition/source checkout, not a general-purpose framework. Before
proposing changes, read [INTEGRATION.md](INTEGRATION.md) end to end — the
documented boundaries are deliberate, not oversights.

Ground rules for any contribution:

- **Determinism owns money.** Reconciliation, credit gates, limits and wallet
  records stay in reviewed Python/`Decimal` code. LLM output is always a
  proposal, never an authority; never let model text alter a numeric value.
- **Fail closed.** Missing evidence, malformed input, hash/signature mismatch
  or policy failure must reject, quarantine or fail — never default to pass.
- **Demo stays demo.** Do not remove `is_demo` flags, simulated-settlement
  labels or attestation disclaimers, and do not add real payment/identity
  calls. `real_funds: false` is a hard invariant.
- **No secrets.** Never commit keys, tokens, workspaces, SQLite databases,
  dossiers or generated demo assets (see `.gitignore`).
- **Tests are part of the change.** Run the full suite plus a frontend build
  before submitting, and keep new behavior offline-testable with synthetic
  fixtures/mocks (no network, no real provider credentials):

```powershell
& $Python scripts/prepare_demo.py
& $Python -m pytest tests -q
npm.cmd --prefix frontend run build
```

Style: match the surrounding code — compact stdlib-first Python with bounded
inputs and explicit evidence errors; React hooks/components with
backend-reported state only (the frontend never infers outcomes).

## Source publishing versus historical ZIP releases

The Git update is source-only: current service/tools/frontend/tests/docs/scripts, frontend lockfile and reference assets. It excludes generated demos, validation/evidence bundles, build outputs, dependencies, databases and secrets. **Publishing code to GitHub does not deploy a running service.**

Older `OUT_IN_Mission_Control_Source*.zip` releases in the repository outputs are historical snapshots, not the latest Git source. They may include prebuilt SPA and explicitly synthetic model assets. Do not assume they include newer README/deployment changes; build from the current checkout.

For the optional ZIP exporter, generate fixtures/build and a full passing `demo/test-results.xml` first. `scripts/package_release.py` requires explicitly synthetic completed/review/quarantine jobs within this checkout's `workspace/`, verifies sources/signatures/models, and creates allowlisted archives. It now includes the frontend lockfile and deployment script. **Packaging does not independently run or certify a frontend build**; its report says build verification is unperformed rather than hardcoding success. See script help and INTEGRATION for legacy packaging contracts.

The legacy chat starter and its tracked dependency history are not runtime requirements. Theme-hook provenance remains: the starter pattern became `frontend/src/hooks/useTheme.js` with guarded storage, `out-in.theme` and `data-theme` styling; chat/SSE/Express were replaced by FastAPI and native browser WebSockets.
