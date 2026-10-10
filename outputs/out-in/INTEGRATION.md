# OUT&IN integration and implementation guide

This is a source-grounded guide to the current local service. Read [README.md](README.md) for the complete layout, environment/dependency pins, Windows PowerShell 5 and bash setup, synthetic demonstration, verification commands, and operational checklist.

**Boundary:** live SDK extraction/model inference can be enabled with reviewed assets, but settlement and legal attestations are always demo simulations. This is not a production lending, identity, payments, or compliance implementation.

## 1. Architecture and authority

```text
React Mission Control
  |-- multipart POST /api/v2/underwrite/batch
  |-- one-action POST /api/v2/demo-run (synthetic dossier, demo mode)
  |-- protected REST snapshots/review/downloads
  |-- native WebSocket /api/v2/agent-stream (job-scoped token, replay cursor)
  v
FastAPI app -> SQLite Store -> Python UnderwritingEngine
  1. trade-extraction-proposer
       local normalization (dossier PDFs in parallel)
       -> demo marker parser OR bounded live SDK proposal
  2. reconciliation-auditor
       deterministic exact Decimal audit
       failed parity -> stages 1/2 again, at most three heals -> quarantine
  3. ml-risk-underwriter
       reviewed model inference + deterministic probability/all-in DSR/cap gates
       + plain-language decision memo (deterministic; optional bounded live
         SDK narrative restatement with byte-equal figure validation)
       failed risk -> read-only human review, no artifacts/wallet
  4. settlement-legal-synthesizer
       simulated wallet preparation -> staged demo bundle
       -> post-commit bundle self-verification -> commit simulation
```

There are **four stages**, not an autonomous agent per tool. Registered persona names describe those roles. Only stage 1 invokes `CodeBuddySDKClient` in live mode; stages 2–4 are scheduler-owned Python calls. `Task`/`Agent` delegation is disallowed. The extraction agent cannot choose a later stage, approve a limit, settle a wallet, or compile the final agreement.

Current implementation entrypoints:

| Responsibility | Source |
|---|---|
| Service environment, routes, auth, upload validation | `agent_service/app.py:18`, `agent_service/app.py:98` |
| Scheduler, bounded feedback, source checks | `agent_service/orchestrator.py:37`, `agent_service/orchestrator.py:85` |
| Native SDK/MCP/options/hooks | `agent_service/sdk_runtime.py:14`, `agent_service/sdk_runtime.py:238`, `agent_service/sdk_runtime.py:271` |
| Jobs/events/tokens/simulation wallet/recovery | `agent_service/store.py:22`, `agent_service/store.py:77`, `agent_service/store.py:122` |
| Statement and audit contracts | `tools/reconciliation_tool.py:48`, `tools/reconciliation_tool.py:105` |
| ML assets/adapters/policy | `tools/ml_underwrite_tool.py:43` |
| Normalization and OCR adapter | `tools/ocr_tool.py:22` |
| Artifact bundle and signatures | `tools/artifact_tool.py:756` |
| Standalone verification | `scripts/verify_manifest.py:252` |
| HTTP client and native stream hook | `frontend/src/api.js:1`, `frontend/src/hooks/useAgentStream.js:1` |

Line numbers identify the inspected source snapshot and can shift after future edits.

## 2. Configuration

### Service variables

| Environment variable | Default/requirement | Effect |
|---|---|---|
| `OUT_IN_MODE` | `demo`; only `demo` or `live` accepted | Demo parser/models versus SDK extraction/reviewed live models; never real funds |
| `OUT_IN_WORKSPACE` | Application root + `/workspace` | SQLite, job evidence, staging, demo private keys |
| `OUT_IN_API_KEY` | Empty allowed in demo; live needs >=32 characters | Shared operator credential for protected HTTP endpoints |
| `OUT_IN_ALLOWED_ORIGINS` | Four loopback origins below | WebSocket origin allowlist; comma-separated exact strings |
| `OUT_IN_REPO_ROOT` | Application root | Reviewed `ml/` assets and preferred reference template repository |
| `OUT_IN_OCR_ADAPTER` | Unset | Explicit reviewed Python OCR bridge file |
| `OUT_IN_OCR_ADAPTER_SHA256` | Required if adapter set | Exact SHA-256 of that reviewed adapter |
| `OUT_IN_NARRATIVE_AGENT` | Unset (off) | `1` in live mode enables the bounded SDK narrative restatement of the decision memo |

Default origins:

```text
http://127.0.0.1:5173
http://localhost:5173
http://127.0.0.1:8000
http://localhost:8000
```

The origin list is split on commas without whitespace normalization; use exact values without added spaces. This is **WebSocket origin checking**, not configured cross-origin REST CORS middleware. REST integration is intentionally same-origin through Vite or the Python-served SPA. A WebSocket client without an `Origin` header is not rejected by this origin check, but still needs the valid job token.

`app.py` constructs `Store`/engine and reads mode/key/origins at import. Change its process environment before launch; changing a variable in another terminal does not change the running process. `.env` is not automatically loaded. `OUT_IN_REPO_ROOT` and OCR variables are read when their tools run; do not rotate reviewed assets/configuration while active jobs depend on them.

SDK/provider authorization is separate and must be provisioned using the installed runtime's supported mechanism. The application does not define provider login, a provider credential proxy, or an automatic fallback model. **Do not use `OUT_IN_API_KEY` as a provider API key.** The SDK transport merges the service environment, but proposer options explicitly blank `OUT_IN_API_KEY` and all `OUT_IN_MYINVOIS_*` entries. Other SDK/provider configuration can still be inherited. These targeted exclusions do not isolate trusted in-process adapters or create an OS sandbox.

See README for silent key generation. Keep credentials out of URLs, debug output, command tracing, transcripts, version control, and analytics. WebSocket **stream tokens necessarily appear in its query string**; configure any access logs/proxies to omit or redact that query and never log authentication headers. This service does not guarantee redaction of arbitrary server/proxy/SDK logs.

### Launch paths

From `C:/Dormathon/outputs/out-in`, using the selected compatible venv:

```text
uvicorn agent_service.app:app --host 127.0.0.1 --port 8000 --workers 1
```

Prefer the interpreter-explicit equivalent (`python -m uvicorn ...`) to avoid selecting a different environment. Do not increase workers: task scheduling, concurrency gates, and single-owner SQLite assumptions are process-local. Avoid `--reload` during jobs.

Vite development in `frontend/`:

```text
npm install
npm run dev -- --port 5173 --strictPort
```

Install as a normal local user. `frontend/vite.config.js:6` proxies `/api` to `http://127.0.0.1:8000` with `changeOrigin: true` and `ws: true`. The frontend uses relative `/api/v2` URLs; no `VITE_API_URL`, Express server, SSE server, or npm SDK socket helper is required.

For one port, `frontend/dist` must exist before backend import; Python serves it on 8000 with SPA fallback for non-API paths. `npm run build` produces this directory. `npm run preview` is not a replacement API-proxy configuration. The current managed `node_modules` is a shared-workspace link; use a standalone local source copy before ordinary `npm install` if you must avoid touching shared dependencies.

## 3. HTTP API

### Actual routes

| Method/path | Authentication | Contract |
|---|---|---|
| `GET /api/v2/config` | Public | `mode`, `api_key_required`, `settlement: "simulated_only"`, `is_demo: true` |
| `GET /api/v2/health` | Public | `status: "ok"`, `mode`, `real_funds: false`; not a live-model readiness check |
| `POST /api/v2/underwrite/batch` | Operator key when configured | Multipart dossier and loan terms; returns 202 with job/token/state |
| `POST /api/v2/demo-run` | Operator key when configured | One-action synthetic end-to-end run; demo mode only, canonical terms; returns 202 with job/token/state |
| `GET /api/v2/jobs/{jid}` | Operator key when configured | Job snapshot excluding `sources` and `terms` |
| `GET /api/v2/review-queue` | Operator key when configured | `{ "jobs": [...] }` for `REVIEW_REQUIRED`; currently filters the newest 200 jobs |
| `GET /api/v2/jobs/{jid}/artifacts/{name}` | Operator key when configured | Published named artifact only; completed bundle or quarantined variance report |
| `GET /api/v2/demo-dossier` | Public | Generated ZIP; 404 until prepared |
| `WS /api/v2/agent-stream` | Job-scoped stream token | Event replay/stream described in section 5 |

For protected HTTP endpoints, send `X-API-Key: <configured key>`. `secrets.compare_digest` checks it when the configured key is nonempty; missing/incorrect keys return 401. With no configured key in demo, these routes do not authenticate the caller. There is no JWT/session exchange, user login, per-user role policy, or equivalent tenant isolation.

`/jobs/{jid}` strips `sources`/`terms`, but the review queue returns stored job objects and can contain those fields. Do not treat all endpoints as uniformly minimized. Artifacts contain evidence summaries and may be sensitive. Only the public synthetic download should be treated as a freely shareable fixture.

### Multipart intake

Use repeated fields named `files`, not `file`, and do not send a ZIP.

| Form field | Type/default | Bounds/meaning |
|---|---|---|
| `files` | Repeated upload | 2–20 total; at least one PDF, exactly one XLSX/CSV bank statement |
| `requested_limit` | Exact numeric string, `40000.00` | Positive, <=1,000,000,000; cent-valued money |
| `duration_months` | Integer, `12` | 1–120 months |
| `annual_rate` | Exact numeric string, `0.08` | Decimal fraction 0–1; `0.08` means 8% |
| `monthly_debt_service` | Exact numeric string, `1000.00` | Nonnegative, <=1,000,000,000; existing monthly debt, operator-declared |

Intake validates:

- Only `.pdf`, `.xlsx`, `.csv` extensions; filename separators/NUL and duplicate normalized names are rejected. Stems normalize to a bounded safe alphabet; extensions lowercase.
- At most 20 MiB per file and 50 MiB total; no empty file.
- PDF `%PDF-` signature.
- XLSX ZIP signature/structure, <=2,000 members, <=100 MiB declared decompression, compression-ratio budget, no VBA project or external workbook links.
- UTF-8 CSV (BOM allowed), no NUL.
- Terms through bounded exact numeric parsing.
- Queue limit: eight scheduled tasks. The engine semaphore executes up to two at a time.

Format checks do not prove document authenticity or fully eliminate malicious-file/parser risks. Runtime reconciliation additionally needs invoice **and** delivery records; passing intake with a PDF alone does not guarantee a usable dossier. Rejected files can remain in isolated `incoming/` directories. No automatic deletion/retention policy is implemented.

A successful intake returns:

```json
{
  "job_id": "<32 lowercase hexadecimal characters>",
  "stream_token": "<random token returned by this intake>",
  "state": "QUEUED"
}
```

This is a response-shape illustration, not a literal runnable job/token. The server stores only the stream token's SHA-256 digest. Treat the returned token as a scoped bearer secret. Intake schedules background execution; **202 does not mean approval or completion**.

There is no POST request-idempotency key. If a client loses the response after the server accepted a dossier, check the backend before retrying. Reposting creates another job and can duplicate processing.

Typical status boundaries: 401 key failure, 404 unknown/unpublished job/artifact, 409 uncommitted ordinary bundle, 413 file/decompression limits, 422 invalid evidence/terms, 429 queue full. Runtime failures after a 202 are reported through the job's state/error and events, not by rewriting the original POST response.

## 4. Synthetic curl examples

These examples are instructions, not requests executed during documentation. Prepare the demo first. The source file names are exactly `invoice.pdf`, `packing_list.pdf`, and `statement.xlsx`, with synthetic invoice `DEMO-INV-001`. Use the key already held in the local environment; do not paste a literal credential into commands or enable verbose/trace output.

Curl expands the header into a process argument, which may be visible to local process inspection; it is acceptable only within the intended trusted local-owner boundary. For a stronger perimeter use a reviewed client/secret-handling transport rather than claiming these snippets hide credentials from the OS. Do not print the raw intake JSON because it contains a stream token.

### PowerShell 5

Use `curl.exe`, not PowerShell's `curl` alias. Choose a local curl version supporting the flags below; `--fail` intentionally does not emit arbitrary server error bodies.

```powershell
Set-Location "C:/Dormathon/outputs/out-in"
$BaseUrl = "http://127.0.0.1:8000"
$Headers = @()
if ($env:OUT_IN_API_KEY) {
    $Headers = @("--header", ("X-API-Key: " + $env:OUT_IN_API_KEY))
}

# Public connectivity checks; no credential needed.
& curl.exe --silent --show-error --fail "$BaseUrl/api/v2/health"
& curl.exe --silent --show-error --fail "$BaseUrl/api/v2/config"

# Capture the token-bearing response instead of displaying it.
$UploadArgs = @(
    "--silent", "--show-error", "--fail",
    "--request", "POST", "$BaseUrl/api/v2/underwrite/batch",
    "--form", "files=@demo/dossier/invoice.pdf;type=application/pdf",
    "--form", "files=@demo/dossier/packing_list.pdf;type=application/pdf",
    "--form", "files=@demo/dossier/statement.xlsx;type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "--form", "requested_limit=40000.00",
    "--form", "duration_months=12",
    "--form", "annual_rate=0.08",
    "--form", "monthly_debt_service=1000.00"
)
$ResponseJson = & curl.exe @Headers @UploadArgs
if ($LASTEXITCODE -ne 0) { throw "Synthetic dossier upload failed" }
$Run = ($ResponseJson -join "`n") | ConvertFrom-Json
$JobId = $Run.job_id
$StreamToken = $Run.stream_token

# Fetch authoritative snapshot and read-only risk-review queue.
& curl.exe --silent --show-error --fail @Headers "$BaseUrl/api/v2/jobs/$JobId"
& curl.exe --silent --show-error --fail @Headers "$BaseUrl/api/v2/review-queue"
```

Only after the snapshot reports `COMPLETED`, download an individual published artifact if needed:

```powershell
& curl.exe --silent --show-error --fail @Headers `
    --output "MasterLedger.xlsx" `
    "$BaseUrl/api/v2/jobs/$JobId/artifacts/MasterLedger.xlsx"
```

Download **all nine** published files into a dedicated bundle directory to run the exact-inventory verifier. The server's existing job `artifacts/` directory is already the complete bundle; use it for local verification rather than assuming one workbook download suffices. A quarantined job publishes `variance_report.json` from `quarantine/`, not this workbook. Do not overwrite an existing local download without reviewing it.

### Bash

Use the chosen `PYTHON` from README to parse captured JSON locally. On Git Bash, `curl.exe` is also an option if that is your installed curl binary.

```bash
cd "C:/Dormathon/outputs/out-in"
BASE_URL="http://127.0.0.1:8000"
HEADERS=()
if [ -n "${OUT_IN_API_KEY:-}" ]; then
  HEADERS=(--header "X-API-Key: $OUT_IN_API_KEY")
fi

curl --silent --show-error --fail "$BASE_URL/api/v2/health"
curl --silent --show-error --fail "$BASE_URL/api/v2/config"

RUN_JSON="$(curl --silent --show-error --fail "${HEADERS[@]}" \
  --request POST "$BASE_URL/api/v2/underwrite/batch" \
  --form "files=@demo/dossier/invoice.pdf;type=application/pdf" \
  --form "files=@demo/dossier/packing_list.pdf;type=application/pdf" \
  --form "files=@demo/dossier/statement.xlsx;type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" \
  --form "requested_limit=40000.00" \
  --form "duration_months=12" \
  --form "annual_rate=0.08" \
  --form "monthly_debt_service=1000.00")" || exit 1
JOB_ID="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<< "$RUN_JSON")"
STREAM_TOKEN="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["stream_token"])' <<< "$RUN_JSON")"

curl --silent --show-error --fail "${HEADERS[@]}" "$BASE_URL/api/v2/jobs/$JOB_ID"
curl --silent --show-error --fail "${HEADERS[@]}" "$BASE_URL/api/v2/review-queue"
```

After completion, an individual workbook download:

```bash
curl --silent --show-error --fail "${HEADERS[@]}" \
  --output "MasterLedger.xlsx" \
  "$BASE_URL/api/v2/jobs/$JOB_ID/artifacts/MasterLedger.xlsx"
```

These examples use HTTP multipart, not WebSocket upload, SSE, or a fictitious SDK endpoint. Ordinary curl builds are not the documented WebSocket client here; use the frontend's native hook for streaming.

## 5. Native WebSocket and frontend state

`frontend/src/hooks/useAgentStream.js` creates a browser-native `WebSocket`:

```javascript
const url = new URL('/api/v2/agent-stream', window.location.origin);
url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
url.searchParams.set('job_id', jobId);
url.searchParams.set('token', token);
url.searchParams.set('after', String(lastSequence));
const socket = new WebSocket(url);
```

Browser WebSocket cannot attach arbitrary `X-API-Key` headers. The token returned by protected intake authenticates **only that job's stream**, not REST downloads or another job. WebSocket origin, job ID, token scope, and integer replay cursor are validated; policy failures close with code 1008. The server converts negative cursors to zero. Tokens have no implemented expiry/rotation endpoint and are bearer secrets for the life of the stored job.

The stream replays database events with `seq > after`, sends batches of up to 500, polls asynchronously every 0.3 seconds, and closes after the terminal job's pending events are drained. It does not transport assistant reasoning, arbitrary chat text, or binary dossier files.

Event envelope:

```json
{
  "seq": "<database-global integer>",
  "job_id": "<current job ID>",
  "type": "STATE_TRANSITION | TOOL_EXECUTION | METRICS | ROLLBACK_TRIGGERED | ARTIFACT_GENERATED",
  "timestamp": "<UTC ISO timestamp>",
  "payload": {},
  "previous_hash": "<previous event hash for this job>",
  "sha256": "<current event hash>"
}
```

`seq` is actually an integer; quoted placeholders above describe the shape. Sequences are database-global, not contiguous per job, so interleaved jobs can create gaps without proving lost events.

The frontend:

- Filters malformed, wrong-job, and already-seen events.
- Keeps the last 300 accepted events in memory and the replay cursor within the hook; it is not a durable replay cursor across page reloads.
- Retries connection up to six times with exponential backoff starting at one second, capped at 30 seconds; exposes manual reconnect/refresh.
- Fetches protected REST snapshots initially, on disconnect, on sequence gaps, and at terminal completion. A snapshot is the authoritative current state, not a frontend inference from an incomplete stream.
- Cleans up fetch controllers, timers, and sockets on effect teardown.
- Stores only up to 20 job IDs and stream tokens in `sessionStorage` under `out-in.run-history.v1`; no API key, dossier files, or financial metrics are persisted there.
- Holds the operator key only in React state and redacts credential fields/key text in displayed telemetry.
- Stores theme preference as `out-in.theme` in local storage. This is not a credential store.
- Uses same-origin protected fetches for artifact downloads, rejects external `/api` URLs and redirects, sanitizes filenames, and downloads via temporary blob URLs.

API-key memory/redaction is not an XSS-proof boundary. Browser history tokens and process/log exposure must be considered separately. The queue can open jobs without a saved token for REST-only inspection; it does not promise a stream token for every listed job.

UI routes (`frontend/src/App.jsx:108`): `/`, `/admin/dashboard`, and `/assistant` render CommandCenter; `/admin/review` renders ReviewQueue. Compatibility admin/AIAssistant exports exist but do not restore the original Express/chat server. The review endpoint selects **risk `REVIEW_REQUIRED` jobs**, not every parity-quarantined dossier. Review-page explanatory prose must not override the backend's capped-haircut mathematics or queue membership.

## 6. Native Python SDK integration

### Installed API surface

The code imports from `codebuddy_agent_sdk==0.3.274`:

```python
from codebuddy_agent_sdk import (
    AgentDefinition, CodeBuddyAgentOptions, CodeBuddySDKClient,
    CodeBuddySDKError, ErrorMessage, ResultMessage,
    HookMatcher, create_sdk_mcp_server, tool,
)
```

Native tool wrappers are created with `@tool(...)` and registered through:

```python
create_sdk_mcp_server(
    name="underwriting",
    version="1.0.0",
    tools=[normalize, reconcile, credit, agreement, read_chunk, submit],
)
```

| Native tool name | Qualified name | Extraction authority |
|---|---|---|
| `run_ocr_normalizer` | `mcp__underwriting__run_ocr_normalizer` | Allowed, current ingested PDF only |
| `read_evidence_chunk` | `mcp__underwriting__read_evidence_chunk` | Allowed, registered evidence ID only |
| `submit_trade_proposal` | `mcp__underwriting__submit_trade_proposal` | Allowed, one bounded validated proposal |
| `run_excel_reconciliation` | `mcp__underwriting__run_excel_reconciliation` | Registered but blocked/unavailable to proposer |
| `evaluate_credit_ml` | `mcp__underwriting__evaluate_credit_ml` | Registered but blocked/unavailable to proposer |
| `compile_tnc_agreement` | `mcp__underwriting__compile_tnc_agreement` | Registered but blocked/unavailable to proposer |

Registering wrappers does not grant stage authority. The MCP invocation wrapper and scheduler dispatch independently reject later-stage calls during extraction.

Exact allowlist:

```python
ALLOWED = {
    "Read",
    "mcp__underwriting__run_ocr_normalizer",
    "mcp__underwriting__read_evidence_chunk",
    "mcp__underwriting__submit_trade_proposal",
}
```

Exact blocklist:

```python
BLOCKED = {
    "Bash", "PowerShell", "Write", "Edit", "WebFetch", "WebSearch",
    "Glob", "Grep", "Task", "Agent", "Skill",
    "mcp__underwriting__run_excel_reconciliation",
    "mcp__underwriting__evaluate_credit_ml",
    "mcp__underwriting__compile_tnc_agreement",
}
```

### Exact options

`SDKRuntime` resolves its working directory to the individual job directory, **not the original repository, frontend, or workspace root**:

```python
self.job_dir = job_dir.resolve()
# e.g. <OUT_IN_WORKSPACE>/jobs/<job_id>
```

The following mirrors `SDKRuntime.options()`; `PERSONAS`, `ALLOWED`, and `BLOCKED` are the existing module constants:

```python
agents = {
    name: AgentDefinition(
        description=name, prompt=prompt, tools=[], model="deepseek-v3.1"
    )
    for name, prompt in PERSONAS.items()
}
options = CodeBuddyAgentOptions(
    permission_mode="bypassPermissions",
    cwd=str(self.job_dir),
    model="deepseek-v3.1",
    max_turns=12,
    tools=["Read"],
    allowed_tools=sorted(ALLOWED),
    disallowed_tools=sorted(BLOCKED),
    setting_sources=[],
    agents=agents,
    mcp_servers={"underwriting": self.server()},
    persist_session=False,
    request_timeout_ms=180_000,
    extra_args={"request-timeout-ms": "180000"},
    hooks={
        "PreToolUse": [HookMatcher(hooks=[self.pre_tool])],
        "PostToolUse": [HookMatcher(hooks=[self.post_tool])],
    },
    system_prompt=PERSONAS["trade-extraction-proposer"],
)
```

SDK 0.3.274's transport copies options but drops `request_timeout_ms`; `extra_args` survives and emits the verified CLI `--request-timeout-ms` flag. It is intentional to keep both the typed option and this exact transport workaround. Do not claim the typed field alone sets the transport timeout for this installed version.

There is no external MCP server configured, no repository settings inheritance (`setting_sources=[]`), and no persisted extraction chat session. The SDK's internal runtime/model connection is still a live external dependency when live mode is used; these options do not make inference offline.

Execution pattern:

```python
async with asyncio.timeout(180):
    async with CodeBuddySDKClient(options=self.options()) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, ErrorMessage):
                raise EvidenceError("SDK extraction failed; check runtime/model authorization")
            if isinstance(msg, ResultMessage):
                if msg.subtype != "success" or msg.is_error is not False or msg.errors:
                    raise EvidenceError("SDK extraction did not terminate successfully")
                success = True
```

Acceptance additionally requires a successfully completed structured proposal, no rollback flag, and no timeout/SDK error. A proposal submitted before a later failed terminal message is not accepted. Exceptions clear the stored proposal. No assistant prose/reasoning is emitted to Mission Control.

### Optional narrative explainer agent

With `OUT_IN_NARRATIVE_AGENT=1` in live mode, a second bounded SDK session
(`NarrativeRuntime`, persona `compliance-narrative-explainer`, separate
`narrative` MCP server) restates the deterministic decision memo in plain
language after risk evaluation. Its allowlist contains only
`mcp__narrative__read_evidence_chunk` and
`mcp__narrative__submit_decision_memo`; raw `Read`, shell, writes, web,
delegation and every `mcp__underwriting__*` tool are blocked. It receives
snapshotted finalized evidence JSON (`audit-N.json`, `features.json`,
`risk.json`) with a 120-second bound, 8 turns and a chunk-count tool budget.
The submitted memo's `figures` object must **exactly equal** the scheduler's
deterministic evidence figures — same keys, same strings — or it is rejected.
On any SDK/validation failure the deterministic memo stands and
`narrative_source` records `deterministic_fallback` (`sdk_agent` on success,
`deterministic` when the option is off or in demo mode). The memo is advisory:
the audit and gates remain the sole authorities, and the memo JSON itself is
hash-pinned into the completed bundle as `evidence/decision_memo.json`.

### Evidence, proposal, and hook constraints

- Registered evidence is current JSON under `<job>/normalized/`. `Read` must resolve to an authorized registered path; no arbitrary filesystem read is intended.
- Normalized JSON is snapshotted into runtime memory. Chunk reads use opaque `evidence-1`, `evidence-2`, etc., never caller-provided paths, so later file changes cannot redirect the registered chunk source.
- `read_evidence_chunk(evidence_id, offset, limit)` returns `text`, `total`, `next_offset`, and `complete`. Offsets/counts are **characters**, not bytes. Maximum limit is 16,000; offsets must be within evidence bounds.
- Prompt instructs full chunk reads because raw single-line `Read` can truncate. MyInvois-enabled extraction additionally sets `require_complete_reads=True`: delivered chunk intervals must cover every evidence ID before a proposal is accepted. Existing upload-only mode retains the earlier prompt-only requirement. Read coverage establishes delivery of evidence text, not LLM comprehension or truthful extraction.
- SDK transport merges inherited environment; options explicitly blank all `OUT_IN_MYINVOIS_*` and `OUT_IN_API_KEY` entries so the proposer subprocess cannot inherit those service credentials.
- Tool-call budget starts at 24 plus the total number of 16,000-character evidence chunks; `max_turns` remains 12. Large dossiers can still exhaust bounded execution.
- A session accepts at most one submitted proposal. Overall serialized proposal limit is 2,000,000 characters, with additional depth/node/list/string limits.
- Proposal requires nonempty invoice and delivery lists, bounded identifiers, known PDF source basenames, line items, numeric strings, valid signs, and invoice region citations bounded to known page dimensions. Citation coordinates are finite numeric page coordinates, not money.
- Invoice numeric fields include `subtotal`, `total`, `tax`, `freight`, `discount`; line fields include `quantity`/`unit_price`; delivery line quantity is also a numeric string. Current exact reconciliation remains the independent authority for math, delivery, and MYR.
- `PreToolUse` rejects blocked/unknown tools and unauthorized `Read` paths and counts bounded calls.
- `PostToolUse` contains defensive reconciliation-output parity handling, setting rollback and a block/stop response on invalid output. It is not the routine audit path: the proposer cannot invoke that tool, and the scheduler always calls `enforce_parity` itself.
- OCR dispatch accepts only current original ingested PDFs. Re-normalization through that MCP wrapper does not let the agent choose arbitrary paths or authorize later deterministic tools.

`bypassPermissions` removes interactive permission prompts; it does **not** remove these application checks, and these checks do **not** create an OS sandbox. Reviewed Python adapters and unpickled model code run with service-process privileges. For real evidence, independently provision least-privilege accounts, filesystem/process isolation, restricted egress, resource controls, and approved credential/data handling. Do not present those recommendations as already implemented guarantees.

## LHDN MyInvois batch integration

See README's **Official LHDN MyInvois retrieval** section for secure environment configuration and official references. The existing batch multipart form gains `myinvois_document_ids`; malformed input is 422, disabled integration with IDs is 409, and auth remains mandatory. Integration startup requires live mode and a strong operator API key. No credentials or arbitrary base URLs are accepted from the browser.

Interfaces:

```python
MyInvoisConfig.from_env()
MyInvoisClient(config, *, transport=None)
await client.fetch_document(document_id)
parse_myinvois_invoice(envelope, source)
bind_myinvois_trade(structured, proposal)
UnderwritingEngine(store, myinvois_client=None)
```

Fixed API/identity hosts: `https://preprod-api.myinvois.hasil.gov.my` (sandbox), `https://api.myinvois.hasil.gov.my` (production). OAuth form-urlencoded client credentials, optional intermediary `onbehalfof` on token request only, cached Bearer token; source GET `/api/v1.0/documents/{opaque-id}/raw`. No search, submission, cancellation or rejection endpoint is exposed. Client has serialized requests, one-second minimum raw spacing, five-second login cooldown, Retry-After state and one 401 renewal, bounded 60-second outer deadline. Locks/rate state are per shared client instance in one worker, not multi-process distributed limits.

API metadata is strict UTF-8 JSON; literal decimal tokens are preserved as strings, duplicate keys/nonfinite constants/deep JSON rejected. Original `document` contains XML or JSON, not assumed base64. UBL JSON namespaces and XML Invoice namespace required; XML DTD/entities forbidden, whole-document node/depth budgets include skipped extensions. Identity encoding is required to avoid uncontrolled decompression. Returned source bytes and provenance are SHA-256 pinned at retrieval and checked during execution. Successful output manifests include raw, provenance, structured mapping and original commercial proposals.

Narrow MYR ordinary-invoice no-adjustment profile matches the existing workbook/audit contract without altering core arithmetic. Missing/inconsistent seller/buyer TIN, timestamps, API/UBL amounts, type/version, line valuation and currency fail closed. Nonzero adjustments/foreign currency are explicitly unsupported rather than silently dropped. Commercial PDF binding requires exact parties, IDs, amounts and unique goods/quantity/price; SKU/shipping proof stays independent. Structured values cannot be rewritten by the LLM. Recoverable PDF/API binding mismatches use the existing three reslices, then quarantine; uncomputable variance is null, never fabricated zero.

Protected raw/provenance source route: `GET /api/v2/jobs/{jid}/myinvois-sources/{name}` with operator key, explicit allowlist, no-store/nosniff and hash verification. Source snapshots remain available for failed jobs that received responses. They can contain sensitive taxpayer/customer data and must remain local/protected. No authenticated LHDN execution or independent LHDN certificate verification has been performed. Continuous status assurance, duplicate collateral controls, received-invoice intermediary access and production identities are not supplied by these software tests.

## 7. Evidence normalization and OCR bridge

Current callable:

```python
def run_ocr_normalizer(file_path: str, regions: list | None = None, cycle: int = 0) -> dict:
    ...
```

Initial cycle 0 uses `pdfplumber` for embedded text, words, tables, and page bounds. It records source basename/SHA-256, per-page evidence, cycle, `scanned`, and `backend: "local_pdfplumber_pdfium"`. Bounds include 1–100 pages and at most 20,000 extracted words per page. Unicode text is normalized and control characters are removed except newline/tab.

Cycles 1–3 use PDFium to render the cited regions at **280, 320, and 360 DPI**, grayscale/contrast enhancement, a rendering pixel budget, crop hashes, and optional crop text. Region validity is checked in the originating pdfplumber coordinate system, then mapped to PDFium dimensions; this avoids silently tolerating out-of-page coordinates because the two engines expose different floating-point bounds. Scheduler strips `local_crop` paths before evidence reaches the SDK.

Tesseract is invoked only inside the retry branch when `shutil.which("tesseract")` finds the actual executable, with `--psm 6` and a 30-second call timeout. `pytesseract` is a wrapper, not the binary. **No Tesseract pass runs at cycle 0.** A scan-only PDF can fail before parity feedback triggers a retry. Do not promise scan-only support merely because the wrapper is installed or retry rendering works; an appropriate reviewed first-pass adapter/integration is required.

Optional explicit adapter:

```python
def normalize_invoice(path, regions):
    # Implement in separately reviewed adapter code.
    ...
```

Set `OUT_IN_OCR_ADAPTER` to the existing reviewed file and `OUT_IN_OCR_ADAPTER_SHA256` to its approved digest. The tool verifies the file/hash and callable before executing it. The result is added as `reviewed_fc_result` with `adapter_sha256`; it does not silently replace source hashes, schema checks, or the scheduler audit. This bridge is not proof that an original FC/cloud handler was supplied or executed. Adapter dependencies, serialization, credential use, egress, and privacy controls need their own review.

Demo extraction instead requires `OUT_IN_DEMO_JSON_BEGIN` / `OUT_IN_DEMO_JSON_END`, `is_demo: true`, and `kind` invoice/delivery in the generated embedded-text packet. That marker parser is intentionally not labelled SDK/LLM extraction.

## 8. Bank statement and exact audit contracts

Required headers:

```text
transaction_id,invoice_id,currency,inflow,outflow,date
```

XLSX requires `BankLedger`, at most 10,000 data rows and 32 columns, no duplicate headers, and literal values rather than formulas. XLSX numeric inflow/outflow cells are converted to their shortest decimal text at the reader boundary; binary floats are not directly accepted by the downstream exact numeric parser. CSV is UTF-8/BOM-compatible. Each transaction ID must be nonempty and unique; amounts are nonnegative, and a row cannot have both positive inflow and outflow. Currency is exactly MYR.

```python
def run_excel_reconciliation(
    invoice_data: list,
    statement_path: str,
    delivery_data: list | None = None,
) -> dict:
    ...

def enforce_parity(report: dict) -> dict:
    ...
```

The numerical contract rejects missing/nonfinite/out-of-bound values, booleans, binary-float amounts passed directly to numeric parsing, and sub-cent monetary values requiring silent rounding. Invoice quantities may be exact decimal fractions; their valuation and exported money must obey exact cent-valued constraints.

Audit covers:

- Unique invoice identifiers, bounded nonempty line items, positive quantities/nonnegative prices.
- Exact line sum/subtotal and subtotal + tax + freight - discount = total.
- Exact matched invoice receipts and aggregate bank/invoice parity.
- Missing/unmatched receipts and shipping/delivery quantities.
- Container seal and Incoterms agreement.

`variance` alone is not the whole verdict: a seal/quantity/Incoterms error can coexist with zero monetary variance. The scheduler records the report, emits its metrics, and calls `enforce_parity` independently.

`reference/reconciliation_handler.py` is a retained legacy DynamoDB report-export reference with fabricated rows, not the authoritative audit implementation and never executed by this workflow. Do not wire it into underwriting as a shortcut.

## 9. Credit models, actual original schemas, and policy

### Tool interface

```python
def evaluate_credit_ml(
    financial_metrics: dict,
    model_dir: str | None = None,
    mode: str = "live",
    requested_limit: str | None = None,
    term_months: int = 12,
    annual_rate: str = "0.08",
) -> dict:
    ...
```

The six mandatory **policy metrics** are:

```python
[
    "monthly_inflow", "monthly_outflow", "monthly_debt_service",
    "invoice_total", "cashflow_margin", "dsr",
]
```

These remain mandatory even when the model has a completely different original feature schema. Positive inflow/valid signs and computed existing DSR consistency are validated. Model features and policy metrics are separate contracts.

The current orchestrator builds only those six metrics plus `requested_limit` and provenance (ledger hash, observed month, operator-declared debt-service warning). It requires exactly one ISO-dated ledger month and does not infer absent periods. It **does not collect original tenure/tracking/etc. inputs** from uploads, HTTP fields, a database, or a hidden service.

### Reviewed assets

Demo uses application `demo/models`. Live uses `<OUT_IN_REPO_ROOT>/ml/models` plus these reviewed source adapters:

```text
<OUT_IN_REPO_ROOT>/
|-- ml/
    |-- feature_engineering.py
    |-- predict.py
    |-- models/
        |-- credit_classifier.pkl
        |-- credit_regressor.pkl
        |-- model_manifest.json
```

The original pickles/source interfaces were **not supplied**. Synthetic random forests from `prepare_demo.py` are not calibrated original production models. Never load pickles from dossiers: pickle is executable trusted-admin code, not a safe data format.

The model manifest requires:

- `is_demo: false` for live inference.
- `sklearn_version` exactly equal to the installed scikit-learn version used at runtime.
- A nonempty unique **ordered** `feature_names` list matching the actual training schema.
- `default_class` present in classifier `classes_`; the adapter must interpret that class as default, not assume a probability column index.
- `sha256` hashes for both pickle basenames, plus repository-relative `ml/feature_engineering.py` and `ml/predict.py` in live mode.
- Model `feature_names_in_`, when available, matching the same ordered schema. Its absence does not invent schema metadata; the manifest/adapter contract is still required.

Manifest template below is intentionally **not usable** until placeholders are replaced with reviewed facts and actual hashes. Do not create a manifest with invented feature names/default labels or merely hash unknown assets to make them pass:

```json
{
  "is_demo": false,
  "sklearn_version": "<exact reviewed training/runtime version>",
  "feature_names": ["<actual ordered training feature name>", "<next actual feature name>"],
  "default_class": "<actual classifier default label; retain its real JSON type>",
  "sha256": {
    "credit_classifier.pkl": "<approved 64-character SHA-256>",
    "credit_regressor.pkl": "<approved 64-character SHA-256>",
    "ml/feature_engineering.py": "<approved 64-character SHA-256>",
    "ml/predict.py": "<approved 64-character SHA-256>"
  }
}
```

Both model hashes are checked before unpickling; adapter path/hash/callable checks precede adapter execution. A manifest hash establishes consistency with its recorded claims, not that an untrusted manifest/model became safe or approved. Protect the review/pinning channel independently.

### Adapter contracts

```python
def build_features(metrics):
    # Return exactly one pandas DataFrame row in reviewed manifest column order.
    ...

def infer_credit(classifier, regressor, frame, default_class):
    # Return finite default probability [0,1] and nonnegative financing ceiling.
    return {
        "default_probability": ...,  # Reviewed classifier/default-label inference
        "financing_ceiling": ...,    # Reviewed regressor output
    }
```

The ML tool preserves explicitly supplied extra metrics/provenance while normalizing only the six policy metrics. Thus a reviewed integration can pass actual original input fields to `build_features`. Output must be a DataFrame of shape `(1, len(feature_names))`, exact ordered columns, finite numeric dtypes, no booleans/complex values, no NaN/infinity. Inference outputs must be finite, valid-range values.

**Schema support does not supply data.** If an original model needs tenure/tracking/etc., implement separately reviewed collection/provenance and fail closed when unavailable. Do not substitute zero, a guessed category, an average tenure, or synthetic tracking history. The current multipart/API/orchestrator does not supply those extra inputs. A direct tool caller supplying reviewed values does not prove the HTTP workflow has that integration.

### Deterministic risk policy

- Probability <= `0.12`.
- All-in DSR <= `0.45` using **maximum cent-conserving scheduled installment**, not existing-only debt.
- Limit = minimum of requested limit, model ceiling, `0.80 * verified_invoice_total`, and DSR capacity.
- Model ceiling/capacity/limit are bounded down to cents. The final schedule is checked again, including last-installment residuals.
- Flat-interest schedule's per-unit capacity approximation is `1 / term_months + annual_rate / 12`.
- Failed policy returns `approved_limit: "0.00"`, `review_required: true`, a bounded proposed limit based on 50% of the request, and the raw `haircut_requested_limit` for review. The capped proposal may be less than 50% or zero.
- No review/approval override or real payment endpoint exists. Parity failure quarantines before ML and does not automatically get a credit haircut.
- The original README's **95% policy is not adopted pending explicit policy review**. The fixed implemented limits above are authoritative.

Risk output includes default probability, all-in `dsr`, `existing_dsr`, financing ceiling, requested/approved/proposed limits, gate flags, both model hashes, model-manifest digest, model demo flag, and feature provenance. An approved software limit still does not represent a lawful real lending/disbursement instruction.

## 10. State machine, retry limits, and recovery

Actual scheduler states and transitions (`agent_service/store.py:10`):

```text
QUEUED -> EXTRACTING -> RECONCILING
RECONCILING -> SELF_HEALING -> RECONCILING  (at most 3 cycles)
RECONCILING -> QUARANTINED                 (fourth failed audit)
RECONCILING -> UNDERWRITING
UNDERWRITING -> REVIEW_REQUIRED            (failed credit policy)
UNDERWRITING -> SETTLING -> COMPLETED       (demo simulation only)
Any allowed nonterminal stage -> FAILED    (error/interruption)
```

Terminal server states: `COMPLETED`, `QUARANTINED`, `REVIEW_REQUIRED`, `FAILED`. Frontend normalization recognizes some additional compatibility names; that does not make them actual server transitions.

`for attempt in range(4)` means cycle 0 plus heals 1–3. Each failed audit emits `ROLLBACK_TRIGGERED`; its displayed `cycle: attempt + 1` can reach 4 on final quarantine. That is a rollback notice index, **not a fourth self-heal**. Inputs remain source-hash checked and are re-sliced rather than edited to force arithmetic agreement.

Completed live extraction, deterministic validation, and approved risk are all needed to enter settlement. Missing evidence/invalid proposal/SDK errors can fail earlier; retry is not an automatic repair path for arbitrary exceptions.

Startup `store.recover()` rolls back simulated wallet records for listed nonterminal jobs, marks them failed, and records: `Worker interrupted. Evidence retained; submit a new dossier.` It does not resume the extraction/session/stages. The list is bounded to 200 newest jobs; recovery/queue administration is not an unlimited archival scan.

There is an existing internal headless entrypoint:

```text
python -m agent_service.orchestrator JOB_ID --workspace WORKSPACE
```

It runs against a stored job, does not implement ingestion/recovery authorization, and must not be presented as a supported resume mechanism. Re-running terminal jobs can violate state/artifact constraints. Operational recovery is investigation and a new reviewed submission, not manual status edits or overwriting the old bundle.

SDK extraction has an async 180-second bound and an explicit transport timeout flag. Other deterministic calls use `asyncio.to_thread`; coroutine cancellation cannot hard-kill already running threads. Tesseract has its separate 30-second call bound. There is **no all-stage hard-kill timeout guarantee**, distributed worker supervisor, or real-funds rollback system.

## 11. Artifact production and verification

### Template and source inventory

Template preference:

1. `<OUT_IN_REPO_ROOT>/backend/lambda/send-email/Out_In_TNC_Agreement.pdf` when present.
2. Local `reference/Out_In_TNC_Agreement.pdf` fallback.
3. If neither exists, artifact compilation can create a demo-only contract without a template.

When available, the scheduler copies and SHA-256 checks the template into `<job>/reference-template.pdf`. That snapshot is declared as an actual job-relative source; the external repository template is not falsely claimed to live inside the job.

Current manifest sources contain portable POSIX-relative paths:

- `inputs/<accepted filename>` and original upload hashes.
- All attempted `normalized/*.json` packets.
- `evidence/*.json`: proposals, audits, features, risk, verified terms, execution-event snapshot.
- `reference-template.pdf` when supplied.

Raw crop images are not independently declared as manifest sources merely because their hashes appear in normalized packets. The event snapshot precedes artifact/completion events; the database contains later events not present in that snapshot. Model/code identities are recorded through risk/adapter metadata; the manifest verifier's source check does not automatically walk and verify the external live repository or every Python dependency.

### Staging and demo settlement

The scheduler rechecks parity and source hashes before settlement. Wallet operations create a local `PREPARED_SIMULATION` record, then finalization marks it `SIMULATED_ONLY`. Failure/interruption marks an existing record `ROLLED_BACK`; no bank/wallet provider or real transfer is called.

`build_artifacts` validates inputs, writes into a unique job `staging/` directory, finalizes PDF/workbook, hashes/signs complete payloads, and renames the finished directory to `artifacts/`. Existing artifacts are never overwritten; failed staging is retained. Filesystem rename, SQLite wallet changes, and job state transitions are not one distributed atomic transaction. HTTP ordinary artifact access requires a published name and `COMPLETED` state, so a retained staged/failed bundle is not automatically a committed downloadable result.

### Post-commit self-verification

After the rename, the scheduler runs `verify_bundle_integrity`
(`tools/selfcheck_tool.py`), which loads the reviewed standalone
`scripts/verify_manifest.py` logic in-process and re-verifies the exact
committed bundle — inventory, seven payload hashes, canonical manifest
signature, distinct keys, both role attestations and every declared source
against the job root. The result is written to `<job>/self_check.json`, merged
into `metrics.self_check`, emitted as a `METRICS` event and displayed as the
frontend SELF-CHECK VERIFIED chip. A failed self-check raises `EvidenceError`:
the job fails closed, the simulated wallet rolls back, and the unverifiable
bundle never reaches a downloadable state. This is built-in quality control,
not a replacement for independent offline verification with
`--trusted-key-dir`.

### Nine-file bundle

```text
MasterLedger.xlsx
Out_In_TNC_Agreement.pdf
verification_manifest.json
verification_manifest.sig
borrower_attestation.sig
lender_attestation.sig
demo_public_key.pem
demo_borrower_public_key.pem
demo_lender_public_key.pem
```

Workbook sheets: `Loan`, `InvoiceLines`, `Invoices`, `Bank`, `Repayments`, `Controls`, `DecimalAudit`. Formulas link invoice/loan/repayment review surfaces; canonical Decimal values and allocation/schedule evidence are retained. Excel calculation is not the underwriting authority, nor does recomputation establish real-world validity.

Contract compilation appends demo terms to a provided reference PDF or creates a demo-only document. Terms have `is_demo: true`; legal status is `demo_attestation_not_legal_countersignature`. Presence of a reference agreement does not create real identity, consent, legal signature, countersignature, or enforceability.

Manifest, borrower-role, and lender-role signatures use **three distinct demo Ed25519 keys**. Private keys live under `<workspace>/keys`, outside job outputs. Both role signatures bind a domain/role prefix to the final PDF bytes:

```text
OUT_IN_DEMO_PDF_ATTESTATION_V1 NUL role NUL final PDF bytes
```

The manifest signature binds canonical UTF-8 JSON. Seven output payloads are hashed: workbook, PDF, three public keys, two role signatures. Manifest/signature are excluded from self-hashing to avoid circularity. The upstream `evidence_hash` hashes declared source identities/hashes plus verified loan terms; it is **not** claimed to be a hash of the PDF that embeds it.

### Correct verifier invocation

Using an existing completed job directory:

```text
python scripts/verify_manifest.py JOB/artifacts --source-dir JOB
```

**`JOB` is the full job root**, not `JOB/inputs`. Current sources include `inputs/`, `evidence/`, `normalized/`, and template snapshot. README contains PowerShell/bash variable-based commands. A downloaded exact nine-file bundle can be the first argument while `--source-dir` still points to retained full job evidence.

Optional:

```text
python scripts/verify_manifest.py JOB/artifacts --source-dir JOB --trusted-key-dir TRUSTED_PUBLIC_KEYS
```

Trusted directory must contain `demo_public_key.pem`, `demo_borrower_public_key.pem`, and `demo_lender_public_key.pem`, obtained independently. Do not derive trust by copying keys from the same candidate bundle.

The verifier fails on unexpected/missing bundle inventory, noncanonical/unsafe source paths, symlinks, malformed keys/signatures, role/key mismatch, output/source hash mismatch, or upstream evidence inconsistency. With no `--source-dir`, it explicitly reports source verification not requested. With no trusted keys, it reports self-contained demo key trust only. It verifies supplied relationships, not original-source authenticity, audit-policy correctness, or approval authorization.

Source hashes and event chains are **local integrity evidence**, not externally anchored audit. The event chain digest is:

```python
sha256(previous_hash + job_id + event_type + utc_timestamp + canonical_payload_json)
```

A writer with local database/keys/assets control can replace evidence and rebuild chains/bundles. There is no external trusted timestamp, immutable journal, independent identity registry, or legally verified countersigning party. Even externally pinned demo keys do not turn demo attestations into identity/legal signatures.

## 12. Provenance of the copied web starter

The reference at `C:/Dormathon/out-in-sdk-starter` comes from `init-cbc-sdk-web` and its `copy-template.sh` script. The inspected script resides under:

```text
C:/Users/tzx20/.workbuddy-ai/plugins/cache/cb_teams_marketplace/
codebuddy-chat-web/1.0.0/skills/init-cbc-sdk-web/copy-template.sh
```

Its contract is `bash <skill-directory>/copy-template.sh <project-name>`: copy the sibling `templates/` contents into a new project, update `package.json` name, and refuse an already existing target. This is a **provenance description**, not a launch prerequisite or an instruction to overwrite/recreate the existing starter. The documentation task inspected the script and copied reference; it did not execute initialization again.

Starter architecture: React/TypeScript, Express, SSE chat streams, session/message SQLite, and starter SDK dependencies. Current architecture: React JavaScript/Vite plus Python FastAPI and native WebSocket events. They are not interchangeable runtime stacks.

Actual adaptation:

| Starter | Current application |
|---|---|
| `src/hooks/useTheme.ts` | `frontend/src/hooks/useTheme.js` |
| Storage key `theme` | `out-in.theme` with guarded storage access |
| DOM `dark` class | Root `data-theme` styling |
| Starter theme initializer precedence ambiguity | Explicitly parenthesized system-theme fallback |
| Chat/SSE/Express application | Deliberately replaced by Python scheduler + native WebSocket hook |

No `codebuddy-chat-web` npm helper is claimed, no Express/SSE backend is launched, and no starter chat SDK import is fabricated into `frontend/package.json`. Existing admin compatibility exports preserve source-layout/route compatibility only. The original project's README's 95% policy is likewise not imported as active underwriting policy.

## 13. Validation and remaining production boundaries

From application root:

```text
pytest tests -q
```

Use the interpreter-explicit form in README to select the confirmed environment. Inspected managed runtime is **Python 3.13.16**, with all 17 top-level requirements matching installed metadata; recommended frontend runtime is Node 22 (inspected `v22.22.2`). The requirements file records the inspected version.

The baseline had 558 passing cases. MyInvois coverage expanded the suite to 975 cases; the decision-memo, narrative-agent, self-check and one-action demo-run additions bring the current executed total to **992 passing cases** across five files (295 engine, 69 API, 230 offline SDK, 193 client transport, 205 structured-invoice/workflow), including a terminal-state/event race regression. Final executed counts and timing are recorded in `demo/test-results.xml` and `demo/validation_results.json`; transport is synthetic and authenticated LHDN execution remains unverified. Vite 6.0.5 production build passed with 1,588 transformed modules. `demo/test-results.xml` and `demo/validation_results.json` record release evidence. API integration runs real local demo stages, while SDK tests use mocked transports; no authenticated provider inference or real funds were verified. Full Chromium upload-to-vault/theme validation remains incomplete after automation navigation timeout; final browser and owned worker cleanup succeeded. The source ZIP includes a fresh SPA build and excludes dependencies/private workspace files. A separate synthetic evidence ZIP includes the nine-file bundle plus every declared source for independent byte/signature/source verification; see README for portable launch and verification paths.

The five test files cover synthetic REST/WS completion/review/quarantine, numerical and artifact/tamper contracts, original-schema adapters with controlled fixtures, native SDK options/messages/MCP/hooks, and timeout/failure behavior. Offline substitute SDK clients do not establish provider readiness. Synthetic models and dossiers do not validate real lending performance.

Before processing real data, separately review and implement where required:

- Verified original model assets, ordered schemas, default labels, independent provenance, missing-feature collection, and calibration/performance validation.
- Appropriate initial-pass scan OCR; reviewed adapter security/privacy and data completeness.
- Least-privilege process/filesystem isolation, egress policy, parser/resource limits, secret handling, stream-token log redaction, expiry/rotation, and protected deployment perimeter.
- Real authentication/authorization/tenant isolation instead of asserting API-key/JWT equivalence.
- Bounded retention, consistent backups, scalable recovery/queue administration, supervision, and hard process timeouts where required.
- Independent legal/privacy/compliance review. **No PDPA/BNM certification is claimed.**
- A separately authorized real payments and identity/legal-signature design, if ever required; none exists here and demo flags must not be removed to imply it does.
- External anchoring/trusted keys/timestamps if audit authenticity beyond local consistency is required.

These are explicit unimplemented/review boundaries, not claims that the supplied local workflow already satisfies them. Keep this integration scoped to the implemented demo-safe software behavior.
