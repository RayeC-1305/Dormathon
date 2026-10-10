# OUT&IN Mission Control — Dormathon 2026 pitch (Track 2: WorkBuddy / Agentic Solutions)

**Guiding question:** *Can your AI Agent use WorkBuddy to solve a real problem and actually get things done?*

**Answer in one sentence:** an operator drops an SME invoice dossier (or clicks one button), and a bounded agent workforce extracts the evidence, re-derives every number with exact Decimal arithmetic, gates credit with reviewed models, explains the decision in plain language, and seals a self-verifying, hash-chained evidence bundle — with disbursement explicitly prohibited and every failure routed to humans.

## The problem (criterion 1 — relevance & utility)

SME cross-border invoice financing today means analysts manually re-keying
invoices, eyeballing bank statements against delivery dockets, and stitching
audit trails by hand — hours per dossier, high error rates, and decisions that
are hard to defend to credit committees or regulators. OUT&IN compresses that
to minutes per dossier while making the evidence *more* rigorous than the
manual process: exact (not approximate) reconciliation, fixed policy gates, and
a downloadable integrity bundle a third party can re-verify offline.

## 3-minute pitch script (Phase 1)

1. **(0:00–0:30) Problem.** SME lenders drown in invoice dossiers: invoice PDFs,
   packing lists, bank statements. Manual reconciliation is slow and
   approximate. We built an autonomous, audit-grade underwriting engine for it.
2. **(0:30–1:10) What the agent does.** One dossier in — four bounded agents
   run: extraction, exact reconciliation, model-gated risk, and a settlement
   simulator that seals a nine-file evidence bundle with Ed25519 attestations
   and a signed manifest. Watch the live pipeline: PDFs normalize in parallel,
   any one-cent mismatch triggers a bounded self-heal that re-slices the
   original evidence — never edits numbers to force parity.
3. **(1:10–1:50) Why it's trustworthy.** The LLM never touches money. Exact
   Decimal Python owns reconciliation, deterministic gates own credit, and the
   bundle *re-verifies itself* before a job may complete — 7 payload hashes,
   3 signatures, every declared source. Quarantine and review states route to
   humans; there is no approve/override endpoint.
4. **(1:50–2:30) WorkBuddy depth.** Extraction is a native codebuddy-agent-sdk
   agent with an in-process MCP server, PreToolUse/PostToolUse hooks, tool
   budgets, and stage-scoped authority: it can read evidence and submit a
   proposal — nothing else. An opt-in second WorkBuddy agent, the
   compliance-narrative-explainer, restates the deterministic decision in plain
   language; its figures must byte-equal the deterministic evidence or the
   submission is rejected.
5. **(2:30–3:00) Close.** Real bottleneck, autonomous multi-agent workflow,
   deep WorkBuddy integration, verifiable outputs, and a one-click live demo.
   Settlement is demo-simulated by design — the integrity engineering is real.

## 4-minute demo script (Phase 2)

1. Open Mission Control (`http://127.0.0.1:8000`), enter the operator key.
2. Click **Run end-to-end demo** — one action stages the synthetic dossier and
   canonical terms; follow the DAG, telemetry and event hash chain live.
3. Point at the **plain-language decision memo** — non-expert readable, figures
   verbatim from evidence.
4. Open the **artifact vault** — note the *SELF-CHECK VERIFIED* chip: the
   backend re-verified its own bundle before reporting COMPLETED.
5. Q&A insurance: re-run `scripts/verify_manifest.py` offline against the
   bundle; show `tests/` (975+ offline cases); show the quarantine path
   (one-cent mismatch → three self-heals → quarantine with variance report,
   no wallet).

## Rubric alignment map

| Rubric criterion (20 pts each) | Where the implementation answers it |
|---|---|
| 1. Problem Relevance & Real-World Utility | SME invoice underwriting / cross-border financing bottleneck; minutes-not-hours dossier processing; read-only LHDN MyInvois retrieval for real tax-invoice binding |
| 2. Autonomous Workflow & Agentic Capability | Four-persona bounded pipeline; dynamic error handling via bounded self-heal (3 reslices → quarantine); parallel dossier normalization; multi-agent collaboration (extraction proposer + narrative explainer) |
| 3. WorkBuddy Integration Depth | Native `codebuddy-agent-sdk` execution with in-process MCP tools, PreToolUse/PostToolUse hooks, tool budgets, read coverage enforcement, stage-scoped authority, credential blanking; optional second SDK narrative agent |
| 4. Output Reliability, Validation & Verification | Exact Decimal audit; event hash chains; Ed25519 manifest + role attestations; in-pipeline post-commit bundle self-verification; offline `verify_manifest.py`; 975+ offline tests; fail-closed states |
| 5. Live Execution, Prototype & Pitch | Single-command deploy (`scripts/deploy_demo.ps1`); **Run end-to-end demo** button = one prompt, one goal, end-to-end; runs fully offline in demo mode; WebSocket live telemetry |

## Honest boundaries (stated on stage if asked)

Settlement, wallet entries, contracts and attestations are demonstration-only;
no real funds move in any mode. Demo models are synthetic and not calibrated.
Authenticated LHDN execution and original production model assets are
unverified prerequisites documented in README/INTEGRATION, not claimed features.
