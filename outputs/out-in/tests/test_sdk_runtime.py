"""Offline SDK contracts using installed message types and MCP handlers; no inference."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from codebuddy_agent_sdk import ErrorMessage, ExecutionError, ResultMessage
from codebuddy_agent_sdk.transport import SubprocessTransport

from agent_service import sdk_runtime
from agent_service.sdk_runtime import (
    ALLOWED, BLOCKED, CHUNK_LIMIT, LATER_STAGE_TOOLS, MAX_PROPOSAL_CHARS,
    PERSONAS, REQUEST_TIMEOUT_MS, SDKRuntime,
)
from tools.reconciliation_tool import EvidenceError


def terminal(subtype="success", is_error=False, errors=None):
    return ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1,
                         is_error=is_error, num_turns=1, session_id="offline", errors=errors)


def proposal():
    return {
        "invoices": [{
            "invoice_id": "SYNTHETIC-1", "currency": "MYR", "subtotal": "100.00",
            "total": "100.00", "tax": "0.00", "freight": "0.00", "discount": "0.00",
            "container_seal": "SYNTHETIC-SEAL", "incoterms": "FOB", "source": "invoice.pdf",
            "regions": [{"page": 1, "bbox": [0, 0, 100, 100]}],
            "line_items": [{"sku": "SYNTHETIC-SKU", "quantity": "10", "unit_price": "10.00"}],
        }],
        "deliveries": [{
            "invoice_id": "SYNTHETIC-1", "container_seal": "SYNTHETIC-SEAL", "incoterms": "FOB",
            "source": "delivery.pdf", "line_items": [{"sku": "SYNTHETIC-SKU", "quantity": "10"}],
        }],
    }


@pytest.fixture
def case(tmp_path, monkeypatch):
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    paths = []
    for source in ("invoice.pdf", "delivery.pdf"):
        packet = {"source": source, "pages": [{"page": 1, "width": 600, "height": 800,
                                             "text": "Synthetic offline evidence", "words": []}], "crops": []}
        path = normalized / (source + ".json")
        path.write_text(json.dumps(packet), encoding="utf-8")
        paths.append(path)
    runtime = SDKRuntime(tmp_path, Mock(), AsyncMock(return_value={"normalized": True}))
    state = SimpleNamespace(runtime=runtime, paths=paths, messages=[terminal()],
                            submit=proposal(), prompt=None, options=None, before_query=None,
                            stream_error=None, exited=False)

    class OfflineClient:
        def __init__(self, *, options):
            state.options = options
            self.tools = options.mcp_servers["underwriting"]["server"].tools

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            state.exited = True

        async def query(self, prompt):
            state.prompt = prompt
            if state.before_query is not None:
                await state.before_query(self)
            if state.submit is not None:
                await self.tools["submit_trade_proposal"].handler(state.submit)

        async def receive_response(self):
            for message in state.messages:
                yield message
            if state.stream_error is not None:
                raise state.stream_error

    monkeypatch.setattr(sdk_runtime, "CodeBuddySDKClient", OfflineClient)
    return state


def extract(case, **kwargs):
    return asyncio.run(case.runtime.extract(case.paths, **kwargs))


@pytest.fixture
def registered(case):
    extract(case)
    case.runtime.proposal = None
    return case


def test_myinvois_proposer_subprocess_blanks_server_credentials(case, monkeypatch):
    for key in ("OUT_IN_API_KEY", "OUT_IN_MYINVOIS_CLIENT_SECRET", "OUT_IN_MYINVOIS_CLIENT_ID", "OUT_IN_MYINVOIS_ON_BEHALF_OF", "OUT_IN_MYINVOIS_FUTURE_SECRET"):
        monkeypatch.setenv(key, "SYNTHETIC-SERVER-ONLY-CREDENTIAL")
    options = case.runtime.options()
    for key in ("OUT_IN_API_KEY", "OUT_IN_MYINVOIS_CLIENT_SECRET", "OUT_IN_MYINVOIS_CLIENT_ID", "OUT_IN_MYINVOIS_ON_BEHALF_OF", "OUT_IN_MYINVOIS_FUTURE_SECRET"):
        assert options.env[key] == ""


def test_myinvois_requires_complete_evidence_reads_before_proposal(case):
    case.runtime.require_complete_reads = True
    with pytest.raises(EvidenceError, match="Every normalized evidence chunk"):
        extract(case)
    assert case.runtime.proposal is None

    async def read_everything(client):
        for evidence_id, text in case.runtime._evidence_text.items():
            for offset in range(0, len(text), 40):
                case.runtime.read_evidence_chunk(evidence_id, offset, 40)
    case.before_query = read_everything
    assert extract(case) == proposal()


def test_myinvois_read_coverage_cannot_bridge_missing_interval(case):
    case.runtime.require_complete_reads = True
    async def read_with_gap(client):
        for evidence_id, text in case.runtime._evidence_text.items():
            case.runtime.read_evidence_chunk(evidence_id, 0, 10)
            case.runtime.read_evidence_chunk(evidence_id, 11, len(text))
    case.before_query = read_with_gap
    with pytest.raises(EvidenceError, match="Every normalized evidence chunk"):
        extract(case)


def test_accepts_only_proposal_with_successful_result_and_keeps_prose_private(case):
    case.messages.insert(0, SimpleNamespace(content="Untrusted assistant prose"))
    result = extract(case, cycle=2, prior_errors=[{"gate": "synthetic-discrepancy"}])
    assert result == proposal()
    assert case.exited
    case.runtime.emit.assert_not_called()
    case.runtime.dispatch.assert_not_awaited()
    assert "evidence-1" in case.prompt and "evidence-2" in case.prompt
    assert "ALL chunks of EVERY evidence ID" in case.prompt
    assert "next_offset" in case.prompt and "complete=true" in case.prompt
    assert "single-line JSON is truncated" in case.prompt
    assert "cycle=2" in case.prompt and "synthetic-discrepancy" in case.prompt
    assert str(case.paths[0]) not in case.prompt


@pytest.mark.parametrize("messages", [
    [], [SimpleNamespace(content="Proposal submitted")],
    [SimpleNamespace(subtype="success", is_error=False)],
    [ErrorMessage(error="Synthetic SDK authorization failure")],
    [terminal("error_max_turns")], [terminal("error_during_execution")],
    [terminal(is_error=True)], [terminal(is_error=0)], [terminal(errors=["Synthetic failure"])],
    [terminal(), ErrorMessage(error="Synthetic late failure")],
])
def test_error_or_eof_after_proposal_never_returns_it(case, messages):
    case.messages = messages
    with pytest.raises(EvidenceError):
        extract(case)
    assert case.runtime.proposal is None
    assert case.exited


@pytest.mark.parametrize("stream_error", [ExecutionError(["Synthetic failure"], "error"),
                                           TimeoutError("Synthetic timeout"), RuntimeError("Synthetic disconnect")])
def test_stream_exceptions_clear_proposal(case, stream_error):
    case.stream_error = stream_error
    with pytest.raises((EvidenceError, RuntimeError)):
        extract(case)
    assert case.runtime.proposal is None
    assert case.exited


def test_success_without_proposal_is_rejected(case):
    case.submit = None
    with pytest.raises(EvidenceError):
        extract(case)


def test_rollback_in_current_session_blocks_success(case):
    async def mark_rollback(client):
        case.runtime.rollback = True
    case.before_query = mark_rollback
    with pytest.raises(EvidenceError):
        extract(case)
    assert case.runtime.proposal is None


def test_each_invocation_resets_proposal_counters_rollback_and_registry(case):
    assert extract(case) == proposal()
    previous_budget = case.runtime.tool_budget
    case.runtime.tool_calls = 999
    case.runtime.rollback = True
    case.runtime._evidence_text["stale"] = "private"
    case.runtime.evidence_paths["stale"] = case.paths[0]
    case.runtime._source_pages["stale.pdf"] = {}

    async def verify_reset(client):
        assert case.runtime.proposal is None
        assert case.runtime.tool_calls == 0
        assert case.runtime.rollback is False
        assert case.runtime.tool_budget == previous_budget
        assert "stale" not in case.runtime._evidence_text
        assert "stale" not in case.runtime.evidence_paths
        assert "stale.pdf" not in case.runtime._source_pages
        assert await case.runtime.pre_tool({"tool_name": "mcp__underwriting__read_evidence_chunk"}) == {}

    case.before_query = verify_reset
    assert extract(case, cycle=1) == proposal()
    assert case.runtime.tool_calls == 1


def test_chunk_reconstruction_preserves_long_single_line_unicode_and_newlines(case):
    packet = json.loads(case.paths[0].read_text(encoding="utf-8"))
    packet["pages"][0]["text"] = ("Synthetic quotation \" and Unicode \u03a9; " * 15000) + "LAST-TEXT"
    packet["crops"] = [{"text": "Synthetic high-DPI crop evidence"}]
    original = json.dumps(packet, ensure_ascii=False) + "\r\n"
    with case.paths[0].open("w", encoding="utf-8", newline="") as handle:
        handle.write(original)
    extract(case)
    tools = case.options.mcp_servers["underwriting"]["server"].tools
    chunks, offset = [], 0
    while True:
        args = {"evidence_id": "evidence-1", "offset": offset, "limit": CHUNK_LIMIT}
        assert asyncio.run(case.runtime.pre_tool({"tool_name": "mcp__underwriting__read_evidence_chunk",
                                                 "tool_input": args})) == {}
        wrapped = asyncio.run(tools["read_evidence_chunk"].handler(args))
        chunk = json.loads(wrapped["content"][0]["text"])
        assert chunk == case.runtime.read_evidence_chunk(**args)
        assert 0 < len(chunk["text"]) <= CHUNK_LIMIT
        assert chunk["total"] == len(original)
        assert chunk["offset"] == offset
        assert chunk["next_offset"] == offset + len(chunk["text"])
        chunks.append(chunk["text"])
        offset = chunk["next_offset"]
        if chunk["complete"]:
            break
    assert len(chunks) > 24
    assert "".join(chunks) == original
    assert json.loads("".join(chunks)) == packet
    assert case.runtime.read_evidence_chunk("evidence-1", offset, 1)["text"] == ""
    assert case.runtime.read_evidence_chunk("evidence-1", offset, 1)["complete"] is True


@pytest.mark.parametrize("offset,limit", [(-1, 1), (True, 1), (0.0, 1), ("0", 1), (None, 1),
                                         (0, 0), (0, -1), (0, CHUNK_LIMIT + 1),
                                         (0, True), (0, 1.0), (0, "1"), (10**12, 1)])
def test_chunk_bounds_fail_closed(registered, offset, limit):
    with pytest.raises(EvidenceError):
        registered.runtime.read_evidence_chunk("evidence-1", offset, limit)


@pytest.mark.parametrize("evidence_id", ["../private.json", "normalized/../private.json", "evidence-1/../../secret",
                                         "C:/private.json", "/private.json", "evidence-999", None, [], {}])
def test_chunk_ids_never_accept_paths_or_unknown_ids(registered, evidence_id):
    with pytest.raises(EvidenceError):
        registered.runtime.read_evidence_chunk(evidence_id, 0, 1)


def test_chunks_use_registered_snapshot_not_subsequent_file_contents(registered):
    original = registered.runtime.read_evidence_chunk("evidence-1", 0, CHUNK_LIMIT)
    registered.paths[0].write_text("private replacement bytes", encoding="utf-8")
    assert registered.runtime.read_evidence_chunk("evidence-1", 0, CHUNK_LIMIT) == original


def test_unregistered_normalized_json_and_old_cycle_are_denied(case):
    unrelated = case.paths[0].parent / "unrelated.json"
    unrelated.write_text("{}", encoding="utf-8")
    extract(case)
    assert asyncio.run(case.runtime.pre_tool({"tool_name": "Read", "tool_input": {
        "file_path": str(unrelated)}}))["decision"] == "block"
    for value in (str(case.paths[0]), "normalized/invoice.pdf.json"):
        assert asyncio.run(case.runtime.pre_tool({"tool_name": "Read", "tool_input": {
            "file_path": value}})) == {}
    packet = json.loads(case.paths[0].read_text(encoding="utf-8"))
    new_path = case.paths[0].parent / "invoice-cycle-1.json"
    new_path.write_text(json.dumps(packet), encoding="utf-8")
    old_path = case.paths[0]
    case.paths[0] = new_path
    extract(case)
    assert asyncio.run(case.runtime.pre_tool({"tool_name": "Read", "tool_input": {
        "file_path": str(old_path)}}))["decision"] == "block"


@pytest.mark.parametrize("relative", ["../private.json", "normalized/../private.json", "normalized/file.txt",
                                     "normalized/file.JSON", "normalized-other/file.json"])
def test_extract_cannot_register_paths_outside_approved_normalized_json(case, relative):
    case.paths = [case.runtime.job_dir / relative]
    with pytest.raises(EvidenceError):
        extract(case)
    assert case.options is None


def test_extract_rejects_symlink_to_unapproved_json(case):
    outside = case.runtime.job_dir / "private.json"
    outside.write_text("{}", encoding="utf-8")
    link = case.paths[0].parent / "link.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires OS permission")
    case.paths = [link]
    with pytest.raises(EvidenceError):
        extract(case)


@pytest.mark.parametrize("packet", [[], {}, {"source": "../invoice.pdf", "pages": []},
                                    {"source": "invoice.pdf", "pages": []},
                                    {"source": "invoice.pdf", "pages": [{"page": True, "width": 600, "height": 800}]},
                                    {"source": "invoice.pdf", "pages": [{"page": 1, "width": 0, "height": 800}]}])
def test_malformed_normalized_packets_never_start_sdk(case, packet):
    case.paths[0].write_text(json.dumps(packet), encoding="utf-8")
    with pytest.raises(EvidenceError):
        extract(case)
    assert case.options is None


@pytest.mark.parametrize("malformed", [None, [], "proposal", {},
                                      {"invoices": {}, "deliveries": [{}]},
                                      {"invoices": "invoice", "deliveries": [{}]},
                                      {"invoices": [], "deliveries": [{}]},
                                      {"invoices": [None], "deliveries": [{}]},
                                      {"invoices": [{}], "deliveries": "delivery"}])
def test_submit_rejects_malformed_records_before_setting_proposal(registered, malformed):
    handler = registered.options.mcp_servers["underwriting"]["server"].tools["submit_trade_proposal"].handler
    with pytest.raises(EvidenceError):
        asyncio.run(handler(malformed))
    assert registered.runtime.proposal is None


@pytest.mark.parametrize("field", ["subtotal", "total", "tax", "freight", "discount", "quantity", "unit_price"])
@pytest.mark.parametrize("value", [None, 1, 1.0, True, "NaN", "Infinity", "-1", "1e16", "0.0000000000001", "9" * 41])
def test_invoice_numeric_fields_are_required_bounded_strings(registered, field, value):
    data = proposal()
    record = data["invoices"][0]
    target = record["line_items"][0] if field in ("quantity", "unit_price") else record
    if value is None:
        target.pop(field)
    else:
        target[field] = value
    with pytest.raises(EvidenceError):
        asyncio.run(registered.runtime.submit_trade_proposal(data))
    assert registered.runtime.proposal is None


@pytest.mark.parametrize("field", ["subtotal", "total", "tax", "freight", "discount"])
def test_invoice_money_rejects_subcent_precision(registered, field):
    data = proposal()
    data["invoices"][0][field] = "100.001"
    with pytest.raises(EvidenceError, match="sub-cent"):
        asyncio.run(registered.runtime.submit_trade_proposal(data))
    assert registered.runtime.proposal is None


@pytest.mark.parametrize("kind", ["invoices", "deliveries"])
@pytest.mark.parametrize("field", ["invoice_id", "container_seal", "incoterms", "source", "line_items"])
def test_required_record_fields_cannot_be_missing(registered, kind, field):
    data = proposal()
    data[kind][0].pop(field)
    with pytest.raises(EvidenceError):
        asyncio.run(registered.runtime.submit_trade_proposal(data))
    assert registered.runtime.proposal is None


@pytest.mark.parametrize("defect", ["quantity_missing", "quantity_float", "quantity_zero", "quantity_negative",
                                   "sku_missing", "items_object", "item_not_object", "items_empty"])
def test_delivery_item_shape_and_numbers_are_validated(registered, defect):
    data = proposal()
    record = data["deliveries"][0]
    item = record["line_items"][0]
    if defect == "quantity_missing":
        item.pop("quantity")
    elif defect.startswith("quantity_"):
        item["quantity"] = {"quantity_float": 1.0, "quantity_zero": "0", "quantity_negative": "-1"}[defect]
    elif defect == "sku_missing":
        item.pop("sku")
    else:
        record["line_items"] = {"items_object": {}, "item_not_object": [None], "items_empty": []}[defect]
    with pytest.raises(EvidenceError):
        asyncio.run(registered.runtime.submit_trade_proposal(data))
    assert registered.runtime.proposal is None


@pytest.mark.parametrize("regions", [None, {}, [], [{"page": True, "bbox": [0, 0, 1, 1]}],
                                    [{"page": 0, "bbox": [0, 0, 1, 1]}],
                                    [{"page": 2, "bbox": [0, 0, 1, 1]}],
                                    [{"page": 1}], [None]])
def test_required_invoice_regions_and_page_citations_are_validated(registered, regions):
    data = proposal()
    data["invoices"][0]["regions"] = regions
    with pytest.raises(EvidenceError):
        asyncio.run(registered.runtime.submit_trade_proposal(data))
    assert registered.runtime.proposal is None


@pytest.mark.parametrize("kind", ["invoices", "deliveries"])
@pytest.mark.parametrize("bbox", [[0, 0, 1], "0,0,1,1", [False, 0, 1, 1], ["0", 0, 1, 1],
                                  [0, 0, float("nan"), 1], [0, 0, float("inf"), 1],
                                  [-1, 0, 1, 1], [1, 0, 1, 1], [2, 0, 1, 1],
                                  [0, 1, 1, 1], [0, 2, 1, 1], [0, 0, 601, 800], [0, 0, 600, 801]])
def test_invalid_bboxes_are_rejected_before_acceptance(registered, kind, bbox):
    data = proposal()
    data[kind][0]["regions"] = [{"page": 1, "bbox": bbox}]
    with pytest.raises(EvidenceError):
        asyncio.run(registered.runtime.submit_trade_proposal(data))
    assert registered.runtime.proposal is None


@pytest.mark.parametrize("source", ["../invoice.pdf", "C:/invoice.pdf", "missing.pdf", "", None])
@pytest.mark.parametrize("kind", ["invoices", "deliveries"])
def test_unknown_source_citations_rejected(registered, kind, source):
    data = proposal()
    data[kind][0]["source"] = source
    with pytest.raises(EvidenceError):
        asyncio.run(registered.runtime.submit_trade_proposal(data))
    assert registered.runtime.proposal is None


@pytest.mark.parametrize("defect", ["invoices", "deliveries", "line_items", "regions", "long_text",
                                   "total_text", "deep", "non_json", "many_keys", "node_budget"])
def test_oversized_or_non_json_proposals_fail_before_acceptance(registered, defect):
    data = proposal()
    if defect in ("invoices", "deliveries"):
        data[defect] *= 501
    elif defect == "line_items":
        data["invoices"][0]["line_items"] *= 5001
    elif defect == "regions":
        data["invoices"][0]["regions"] *= 101
    elif defect == "long_text":
        data["extra"] = "x" * (CHUNK_LIMIT + 1)
    elif defect == "total_text":
        data["extra"] = ["x" * CHUNK_LIMIT] * (MAX_PROPOSAL_CHARS // CHUNK_LIMIT + 1)
    elif defect == "deep":
        nested = "text"
        for _ in range(14):
            nested = [nested]
        data["extra"] = nested
    elif defect == "non_json":
        data["extra"] = object()
    elif defect == "many_keys":
        data.update({f"extra-{n}": "text" for n in range(65)})
    else:
        data["extra"] = [[0] * 5000 for _ in range(21)]
    with pytest.raises(EvidenceError):
        asyncio.run(registered.runtime.submit_trade_proposal(data))
    assert registered.runtime.proposal is None


def test_validation_preserves_discrepancies_for_deterministic_auditor(registered):
    data = proposal()
    invoice, delivery = data["invoices"][0], data["deliveries"][0]
    invoice.update(currency="USD", subtotal="99.00", total="98.00", discount="101.00")
    invoice["line_items"][0].update(quantity="10.000000000001", unit_price="9.999999999999")
    delivery.update(invoice_id="OTHER-SYNTHETIC-ID", container_seal="DIFFERENT", incoterms="CIF")
    delivery["line_items"][0].update(sku="OTHER-SYNTHETIC-SKU", quantity="9")
    original = deepcopy(data)
    result = asyncio.run(registered.runtime.submit_trade_proposal(data))
    assert json.loads(result["content"][0]["text"]) == {"accepted_for_validation": True}
    assert registered.runtime.proposal == original == data
    data["invoices"][0]["total"] = "0"
    assert registered.runtime.proposal == original
    with pytest.raises(EvidenceError, match="Only one proposal"):
        asyncio.run(registered.runtime.submit_trade_proposal(original))
    assert registered.runtime.proposal == original


def test_rejected_submission_does_not_poison_valid_retry(registered):
    malformed = proposal()
    malformed["invoices"][0]["total"] = 100
    with pytest.raises(EvidenceError):
        asyncio.run(registered.runtime.submit_trade_proposal(malformed))
    assert registered.runtime.proposal is None
    valid = proposal()
    asyncio.run(registered.runtime.submit_trade_proposal(valid))
    assert registered.runtime.proposal == valid


@pytest.mark.parametrize("kind,field", [("invoices", "quantity"), ("invoices", "total"),
                                        ("deliveries", "quantity")])
def test_strictly_positive_fields_reject_zero(registered, kind, field):
    data = proposal()
    target = data[kind][0]
    if field == "quantity":
        target = target["line_items"][0]
    target[field] = "0"
    with pytest.raises(EvidenceError):
        asyncio.run(registered.runtime.submit_trade_proposal(data))
    assert registered.runtime.proposal is None


def test_chunk_access_before_extract_has_no_registered_ids(case):
    with pytest.raises(EvidenceError, match="Unknown evidence ID"):
        case.runtime.read_evidence_chunk("evidence-1", 0, 1)


def test_empty_evidence_never_starts_sdk(case):
    case.paths = []
    with pytest.raises(EvidenceError):
        extract(case)
    assert case.options is None


def test_malformed_sdk_submission_never_returns_success(case):
    case.submit = {"invoices": [{}], "deliveries": [{}]}
    with pytest.raises(EvidenceError):
        extract(case)
    assert case.runtime.proposal is None
    assert case.exited


@pytest.mark.parametrize("name", sorted(LATER_STAGE_TOOLS))
def test_stage_tools_remain_registered_but_are_denied_in_options_hooks_and_handlers(case, name):
    options = case.runtime.options()
    bare_name = name.removeprefix("mcp__underwriting__")
    tools = options.mcp_servers["underwriting"]["server"].tools
    assert bare_name in tools
    assert name in options.disallowed_tools
    assert name not in options.allowed_tools
    assert asyncio.run(case.runtime.pre_tool({"tool_name": name, "tool_input": {}}))["decision"] == "block"
    with pytest.raises(EvidenceError, match="later deterministic stage"):
        asyncio.run(tools[bare_name].handler({}))
    case.runtime.dispatch.assert_not_awaited()


def test_preserved_options_personas_and_supported_tool_schema(case):
    options = case.runtime.options()
    assert options.model == "deepseek-v3.1"
    assert options.max_turns == 12
    assert options.permission_mode == "bypassPermissions"
    assert options.tools == ["Read"]
    assert options.setting_sources == [] and options.persist_session is False
    assert set(options.allowed_tools) == ALLOWED
    assert set(options.disallowed_tools) == BLOCKED
    assert set(options.agents) == set(PERSONAS) and len(options.agents) == 4
    for name, agent in options.agents.items():
        assert agent.prompt == PERSONAS[name]
        assert agent.tools == [] and agent.model == "deepseek-v3.1"
    tools = options.mcp_servers["underwriting"]["server"].tools
    assert set(tools) == {"run_ocr_normalizer", "run_excel_reconciliation", "evaluate_credit_ml",
                          "compile_tnc_agreement", "read_evidence_chunk", "submit_trade_proposal"}
    schema = tools["read_evidence_chunk"].input_schema
    assert schema.properties == {"evidence_id": {"type": "string"}, "offset": {"type": "integer"},
                                 "limit": {"type": "integer"}}
    assert schema.required == ["evidence_id", "offset", "limit"]
    assert tools["submit_trade_proposal"].input_schema.properties == {
        "invoices": {"type": "array"}, "deliveries": {"type": "array"}}


def test_verified_timeout_workaround_survives_sdk_transport_option_copy(case):
    options = case.runtime.options()
    assert options.request_timeout_ms == REQUEST_TIMEOUT_MS
    assert options.extra_args == {"request-timeout-ms": str(REQUEST_TIMEOUT_MS)}
    # Constructing a transport and building argv do not launch the CLI or authenticate.
    transport = SubprocessTransport(options)
    argv = transport._build_args()
    assert argv.count("--request-timeout-ms") == 1
    index = argv.index("--request-timeout-ms")
    assert argv[index + 1] == str(REQUEST_TIMEOUT_MS)


def narrative_figures():
    return {"variance": "0", "invoice_total": "100000.00", "verified_inflow": "100000.00",
            "default_probability": "0.05", "dsr": "0.35", "existing_dsr": "0.01",
            "requested_limit": "40000.00", "approved_limit": "40000.00", "proposed_limit": "40000.00"}


def narrative_memo(figures):
    return {"headline": "Synthetic narrative headline", "summary": "Synthetic plain-language summary.",
            "reasons": ["Synthetic reason one.", "Synthetic reason two."],
            "next_steps": ["Synthetic next step."], "figures": figures}


@pytest.fixture
def narrative_case(tmp_path, monkeypatch):
    from agent_service.sdk_runtime import NarrativeRuntime

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    paths = []
    for name, packet in [("audit-0.json", {"passed": True, "variance": "0"}),
                         ("risk.json", {"passed": True, "approved_limit": "40000.00"})]:
        path = evidence / name
        path.write_text(json.dumps(packet), encoding="utf-8")
        paths.append(path)
    runtime = NarrativeRuntime(tmp_path, Mock())
    state = SimpleNamespace(runtime=runtime, paths=paths, figures=narrative_figures(),
                            messages=[terminal()], submit=narrative_memo(narrative_figures()),
                            prompt=None, options=None)

    class OfflineClient:
        def __init__(self, *, options):
            state.options = options
            self.tools = options.mcp_servers["narrative"]["server"].tools

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def query(self, prompt):
            state.prompt = prompt
            if state.submit is not None:
                await self.tools["submit_decision_memo"].handler(state.submit)

        async def receive_response(self):
            for message in state.messages:
                yield message

    monkeypatch.setattr(sdk_runtime, "CodeBuddySDKClient", OfflineClient)
    return state


def narrate(case, **kwargs):
    return asyncio.run(case.runtime.narrate(case.paths, case.figures, **kwargs))


def test_narrative_agent_accepts_memo_with_exact_deterministic_figures(narrative_case):
    memo = narrate(narrative_case)
    assert memo["figures"] == narrative_figures()
    assert memo["headline"] == "Synthetic narrative headline"
    assert memo["reasons"] == ["Synthetic reason one.", "Synthetic reason two."]
    assert "read ALL chunks" in narrative_case.prompt.replace("Read ALL", "read ALL")
    options = narrative_case.options
    assert options.tools == [] and options.persist_session is False
    assert set(options.allowed_tools) == {"mcp__narrative__read_evidence_chunk", "mcp__narrative__submit_decision_memo"}
    assert "Read" in set(options.disallowed_tools)
    assert set(options.mcp_servers) == {"narrative"}


def test_narrative_agent_rejects_altered_figures(narrative_case):
    narrative_case.submit = narrative_memo({**narrative_figures(), "approved_limit": "999999.00"})
    with pytest.raises(EvidenceError):
        narrate(narrative_case)
    assert narrative_case.runtime.memo is None


def test_narrative_agent_rejects_missing_figure_keys(narrative_case):
    figures = narrative_figures()
    del figures["dsr"]
    narrative_case.submit = narrative_memo(figures)
    with pytest.raises(EvidenceError):
        narrate(narrative_case)
    assert narrative_case.runtime.memo is None


def test_narrative_agent_rejects_non_evidence_registration(narrative_case, tmp_path):
    outside = tmp_path / "normalized" / "packet.json"
    outside.parent.mkdir()
    outside.write_text("{}", encoding="utf-8")
    narrative_case.paths = [outside]
    with pytest.raises(EvidenceError, match="evidence JSON"):
        narrate(narrative_case)
    root_file = tmp_path / "self_check.json"
    root_file.write_text("{}", encoding="utf-8")
    narrative_case.paths = [root_file]
    with pytest.raises(EvidenceError, match="evidence JSON"):
        narrate(narrative_case)


def test_narrative_pre_tool_blocks_reads_and_later_stage_tools(narrative_case):
    runtime = narrative_case.runtime
    runtime._register_evidence(narrative_case.paths)
    for name in ("Read", "Bash", "mcp__underwriting__evaluate_credit_ml", "mcp__underwriting__read_evidence_chunk"):
        verdict = asyncio.run(runtime.pre_tool({"tool_name": name, "tool_input": {}}))
        assert verdict["decision"] == "block", name
    verdict = asyncio.run(runtime.pre_tool({"tool_name": "mcp__narrative__read_evidence_chunk",
                                            "tool_input": {"evidence_id": "evidence-1", "offset": 0, "limit": 10}}))
    assert verdict == {}
