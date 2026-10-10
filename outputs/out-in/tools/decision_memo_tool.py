"""Plain-language decision memo derived only from deterministic evidence.

The memo is an advisory explanation surface for non-expert reviewers. The exact
Decimal audit, reviewed model gates and scheduler policy remain the sole
authorities; figures are copied verbatim from evidence strings and are never
recomputed, rounded or invented here. An optional SDK narrative agent may
restate the explanation in live mode, but its figures are validated to equal
these deterministic values before any text is accepted. Demo settlement stays
simulated in every mode.
"""
from __future__ import annotations
from tools.reconciliation_tool import EvidenceError

FIGURE_KEYS = ("variance", "invoice_total", "verified_inflow", "default_probability",
               "dsr", "existing_dsr", "requested_limit", "approved_limit", "proposed_limit")
TEXT_LIMITS = {"headline": 300, "summary": 4000, "reason": 600, "step": 400}
MAX_REASONS, MAX_STEPS = 12, 8
AUTHORITY = ("Deterministic Decimal reconciliation and reviewed model gates decide outcomes; "
             "this memo explains them and has no financing authority.")
DISCLAIMER = "Demo simulation only; no disbursement, legal countersignature or real funds."


def bounded_text(value, limit: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EvidenceError(f"Memo {label} must be 1..{limit} characters")
    return value.strip()


def expected_figures(audit: dict, risk: dict, terms: dict) -> dict:
    """Copy verbatim evidence strings; missing keys stay missing, never invented.

    ML ratios can legitimately be long decimal/scientific strings, so figures
    are bounded as opaque evidence text, not re-parsed through the strict
    sub-cent monetary parser.
    """
    figures = {}
    for key in FIGURE_KEYS:
        value = audit.get(key) if key in ("variance", "invoice_total", "verified_inflow") else risk.get(key)
        if key == "requested_limit":
            value = terms.get("requested_limit")
        if value is None:
            continue
        if isinstance(value, (bool, float)) or not isinstance(value, str):
            raise EvidenceError(f"Evidence figure {key} must be a bounded decimal string")
        text = value.strip()
        if not text or len(text) > 128 or any(ord(char) < 32 for char in text):
            raise EvidenceError(f"Evidence figure {key} exceeds memo bounds")
        figures[key] = text
    return figures


def _default_narrative(audit: dict, risk: dict, figures: dict, passed: bool) -> dict:
    reasons = []
    if audit.get("passed") is True:
        reasons.append(f"Exact Decimal reconciliation passed with zero variance: verified bank inflow "
                       f"{figures.get('verified_inflow', '?')} MYR equals the audited invoice total "
                       f"{figures.get('invoice_total', '?')} MYR, and delivery records agree.")
    gates = risk.get("gates") if isinstance(risk.get("gates"), dict) else {}
    if "probability_le_0_12" in gates:
        verdict = "within" if gates["probability_le_0_12"] else "above"
        reasons.append(f"Reviewed model default probability {figures.get('default_probability', '?')} is "
                       f"{verdict} the 0.12 policy ceiling.")
    if "all_in_dsr_le_0_45" in gates:
        verdict = "within" if gates["all_in_dsr_le_0_45"] else "above"
        reasons.append(f"All-in debt service ratio {figures.get('dsr', '?')} (maximum scheduled installment "
                       f"plus existing debt) is {verdict} the 0.45 policy ceiling; existing-only DSR is "
                       f"{figures.get('existing_dsr', '?')}.")
    if passed:
        reasons.append(f"Approved demo limit {figures.get('approved_limit', '?')} MYR is the minimum of the "
                       f"request, the model financing ceiling, 80% of the verified invoice total and the "
                       f"DSR capacity bound.")
        headline = f"Demo limit {figures.get('approved_limit', '?')} MYR approved after exact reconciliation and model gates."
        summary = ("Every number was re-derived by deterministic Decimal arithmetic and the reviewed credit "
                   "model; the request passed all fixed policy gates, so a simulated settlement bundle was "
                   "sealed and self-verified. No real funds moved.")
        steps = ["Download the nine-file bundle and re-verify it offline with scripts/verify_manifest.py.",
                 "Review the memo figures against evidence/decision_memo.json and the hash-chained event log.",
                 "Treat the wallet entry as a simulation: there is no disbursement or approval override."]
    else:
        reasons.append(f"Credit gates failed, so the approved limit is 0.00 and a bounded human-review "
                       f"proposal of {figures.get('proposed_limit', '?')} MYR was recorded; the proposal is "
                       f"never an approval and creates no wallet or contract action.")
        headline = "Credit policy gates failed; dossier routed to read-only human review."
        summary = ("Reconciliation may be exact, but the fixed risk policy did not pass. The scheduler "
                   "recorded a capped 50% haircut proposal for a human operator; nothing is disbursed, "
                   "settled or compiled automatically.")
        steps = ["Open the read-only review queue to inspect the recorded proposal and gate failures.",
                 "Submit a new dossier with revised terms if the borrower situation changes.",
                 "No artifact vault or wallet entry exists for this run; quarantine rules were not triggered."]
    return {"headline": headline, "summary": summary, "reasons": reasons, "next_steps": steps}


def build_decision_memo(audit: dict, risk: dict, terms: dict, narrative: dict | None = None,
                        narrative_source: str = "deterministic") -> dict:
    if not isinstance(audit, dict) or not isinstance(risk, dict) or not isinstance(terms, dict):
        raise EvidenceError("Memo requires audit, risk and terms evidence objects")
    if narrative_source not in ("deterministic", "sdk_agent", "deterministic_fallback"):
        raise EvidenceError("Unknown memo narrative source")
    passed = risk.get("passed") is True
    figures = expected_figures(audit, risk, terms)
    text = _default_narrative(audit, risk, figures, passed)
    if narrative is not None:
        # SDK narrative restates the explanation; deterministic figures win ties.
        if narrative.get("figures") != figures:
            raise EvidenceError("SDK narrative figures differ from deterministic evidence")
        text = {"headline": narrative["headline"], "summary": narrative["summary"],
                "reasons": narrative["reasons"], "next_steps": narrative["next_steps"]}
    memo = {"is_demo": True, "outcome": "APPROVED" if passed else "REVIEW_REQUIRED",
            "headline": bounded_text(text["headline"], TEXT_LIMITS["headline"], "headline"),
            "summary": bounded_text(text["summary"], TEXT_LIMITS["summary"], "summary"),
            "reasons": [bounded_text(r, TEXT_LIMITS["reason"], "reason")
                        for r in text["reasons"]][:MAX_REASONS],
            "next_steps": [bounded_text(s, TEXT_LIMITS["step"], "step")
                           for s in text["next_steps"]][:MAX_STEPS],
            "figures": figures, "narrative_source": narrative_source,
            "authority": AUTHORITY, "disclaimer": DISCLAIMER}
    if not memo["reasons"]:
        raise EvidenceError("Memo must explain at least one deterministic gate")
    return memo
