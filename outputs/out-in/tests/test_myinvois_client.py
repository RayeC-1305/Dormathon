"""Offline MyInvois contracts: every HTTP request uses httpx.MockTransport."""
import asyncio
import gzip
import hashlib
import json
import traceback
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode

import httpx
import pytest

from agent_service import myinvois_client as myinvois
from agent_service.myinvois_client import (
    MyInvoisClient,
    MyInvoisConfig,
    MyInvoisError,
    enabled,
    parse_document_ids,
    validate_document_id,
)
from tools.reconciliation_tool import EvidenceError


CLIENT_ID = "offline-client+id&=42"
CLIENT_SECRET = "offline-secret+&=%/42"
IDENTITY = "C0000000000:ROB_TEST"
TOKEN = "offline-access-token-1"
RENEWED_TOKEN = "offline-access-token-2"
DOCUMENT_ID = "Opaque_Ab-019"
BASE_URLS = {
    "sandbox": "https://preprod-api.myinvois.hasil.gov.my",
    "production": "https://api.myinvois.hasil.gov.my",
}
REMOTE_URL = "https://attacker.invalid/capture"
REMOTE_ECHO = " | ".join((CLIENT_ID, CLIENT_SECRET, IDENTITY, TOKEN, RENEWED_TOKEN, REMOTE_URL))
TOKEN_PAYLOAD = {"access_token": TOKEN, "token_type": "Bearer", "expires_in": 3600}
DOCUMENT_PAYLOAD = {"uuid": DOCUMENT_ID, "document": "<Invoice>offline</Invoice>"}
ENV_KEYS = (
    "OUT_IN_MYINVOIS_ENABLED",
    "OUT_IN_MYINVOIS_CLIENT_ID",
    "OUT_IN_MYINVOIS_CLIENT_SECRET",
    "OUT_IN_MYINVOIS_ENVIRONMENT",
    "OUT_IN_MYINVOIS_AUTH_MODE",
    "OUT_IN_MYINVOIS_ON_BEHALF_OF",
)


@pytest.fixture(autouse=True)
def isolated_environment_and_no_pacing(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(myinvois, "_RAW_REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(myinvois, "_AUTH_COOLDOWN_SECONDS", 0.0)
    monkeypatch.setattr(myinvois, "_DEFAULT_RETRY_AFTER_SECONDS", 0.25)


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=1000.0)
    # Replace the module reference, not the process-wide event-loop clock.
    monkeypatch.setattr(myinvois, "time", SimpleNamespace(monotonic=lambda: state.now))
    return state


def token_response(**changes):
    return httpx.Response(200, json={**TOKEN_PAYLOAD, **changes})


def success_response(request):
    if request.method == "POST":
        return token_response()
    assert request.method == "GET"
    document_id = request.url.path.split("/")[-2]
    return httpx.Response(200, json={**DOCUMENT_PAYLOAD, "uuid": document_id})


def make_client(handler=success_response, **config_changes):
    requests = []

    def record(request):
        requests.append(request)
        return handler(request)

    config = MyInvoisConfig(
        **{"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, **config_changes}
    )
    return MyInvoisClient(config, transport=httpx.MockTransport(record)), requests


def fetch(client, document_id=DOCUMENT_ID):
    return asyncio.run(client.fetch_document(document_id))


def assert_sanitized(error):
    assert isinstance(error, EvidenceError)
    exposed = str(error) + repr(error) + "".join(traceback.format_exception(error))
    for private in (CLIENT_ID, CLIENT_SECRET, IDENTITY, TOKEN, RENEWED_TOKEN, REMOTE_URL):
        assert private not in exposed


def fetch_error(client, message, document_id=DOCUMENT_ID):
    with pytest.raises(MyInvoisError, match=message) as caught:
        fetch(client, document_id)
    assert_sanitized(caught.value)
    return caught.value


def methods(requests):
    return [request.method for request in requests]


@pytest.mark.parametrize("value,expected", [
    (None, False), ("0", False), ("true", False), (" 1", False), ("1 ", False), ("1", True),
])
def test_enabled_requires_exact_opt_in(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("OUT_IN_MYINVOIS_ENABLED", value)
    assert enabled() is expected
    if not expected:
        with pytest.raises(MyInvoisError, match="not enabled"):
            MyInvoisConfig.from_env()


@pytest.mark.parametrize("changes,message", [
    ({"environment": "prod"}, "environment"),
    ({"environment": REMOTE_URL}, "environment"),
    ({"environment": None}, "environment"),
    ({"auth_mode": "Taxpayer"}, "authentication mode"),
    ({"auth_mode": None}, "authentication mode"),
    ({"client_id": ""}, "client ID"),
    ({"client_id": None}, "client ID"),
    ({"client_id": "x" * 257}, "client ID"),
    ({"client_id": "with space"}, "client ID"),
    ({"client_id": "id\r\nInjected: value"}, "client ID"),
    ({"client_secret": ""}, "client secret"),
    ({"client_secret": "x" * 1025}, "client secret"),
    ({"client_secret": "secret\x00"}, "client secret"),
    ({"client_secret": "secret\x7f"}, "client secret"),
    ({"client_secret": "secret\u00e9"}, "client secret"),
    ({"on_behalf_of": ""}, "bounded TIN"),
    ({"on_behalf_of": ":ROB"}, "bounded TIN"),
    ({"on_behalf_of": "TIN:"}, "bounded TIN"),
    ({"on_behalf_of": "TIN:ROB:OTHER"}, "bounded TIN"),
    ({"on_behalf_of": "-TIN"}, "bounded TIN"),
    ({"on_behalf_of": "TIN\nInjected"}, "bounded TIN"),
    ({"on_behalf_of": "A" * 65}, "bounded TIN"),
    ({"on_behalf_of": 123}, "bounded TIN"),
    ({"auth_mode": "intermediary"}, "requires an on-behalf-of identity"),
])
def test_config_rejects_invalid_values_without_echoing_them(changes, message):
    with pytest.raises(MyInvoisError, match=message) as caught:
        MyInvoisConfig(**{"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, **changes})
    assert_sanitized(caught.value)


def test_config_accepts_bounds_and_repr_hides_credentials_and_identity():
    config = MyInvoisConfig(
        client_id="i" * 256,
        client_secret="s" * 1024,
        environment="production",
        auth_mode="intermediary",
        on_behalf_of="A" * 64 + ":" + "B" * 64,
    )
    assert config.base_url == BASE_URLS["production"]
    assert "production" in repr(config) and "intermediary" in repr(config)
    for private in (config.client_id, config.client_secret, config.on_behalf_of):
        assert private not in repr(config)
    with pytest.raises(AttributeError):
        config.environment = "sandbox"


def set_enabled_credentials(monkeypatch):
    monkeypatch.setenv("OUT_IN_MYINVOIS_ENABLED", "1")
    monkeypatch.setenv("OUT_IN_MYINVOIS_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("OUT_IN_MYINVOIS_CLIENT_SECRET", CLIENT_SECRET)


def test_from_env_defaults_and_explicit_intermediary_configuration(monkeypatch):
    set_enabled_credentials(monkeypatch)
    defaults = MyInvoisConfig.from_env()
    assert defaults == MyInvoisConfig(CLIENT_ID, CLIENT_SECRET)
    monkeypatch.setenv("OUT_IN_MYINVOIS_ENVIRONMENT", "production")
    monkeypatch.setenv("OUT_IN_MYINVOIS_AUTH_MODE", "intermediary")
    monkeypatch.setenv("OUT_IN_MYINVOIS_ON_BEHALF_OF", IDENTITY)
    config = MyInvoisConfig.from_env()
    assert (config.environment, config.auth_mode, config.on_behalf_of) == (
        "production", "intermediary", IDENTITY
    )
    for private in (CLIENT_ID, CLIENT_SECRET, IDENTITY):
        assert private not in repr(config)


@pytest.mark.parametrize("key,value,message", [
    ("CLIENT_ID", "", "client ID"),
    ("CLIENT_SECRET", "", "client secret"),
    ("ENVIRONMENT", "Sandbox", "environment"),
    ("AUTH_MODE", "INTERMEDIARY", "authentication mode"),
    ("AUTH_MODE", "intermediary", "requires an on-behalf-of identity"),
    ("ON_BEHALF_OF", "TIN:ROB:OTHER", "bounded TIN"),
])
def test_from_env_applies_validation(monkeypatch, key, value, message):
    set_enabled_credentials(monkeypatch)
    monkeypatch.setenv("OUT_IN_MYINVOIS_" + key, value)
    with pytest.raises(MyInvoisError, match=message) as caught:
        MyInvoisConfig.from_env()
    assert_sanitized(caught.value)


def test_empty_environment_identity_is_none_for_taxpayer(monkeypatch):
    set_enabled_credentials(monkeypatch)
    monkeypatch.setenv("OUT_IN_MYINVOIS_ON_BEHALF_OF", "")
    assert MyInvoisConfig.from_env().on_behalf_of is None


@pytest.mark.parametrize("config", [None, {"client_id": CLIENT_ID}, CLIENT_ID])
def test_client_requires_validated_config_before_any_http(config):
    transport = httpx.MockTransport(success_response)
    with pytest.raises(MyInvoisError, match="validated configuration"):
        MyInvoisClient(config, transport=transport)


def test_client_revalidates_even_a_mutated_config():
    config = MyInvoisConfig(CLIENT_ID, CLIENT_SECRET)
    object.__setattr__(config, "environment", REMOTE_URL)
    with pytest.raises(MyInvoisError, match="environment") as caught:
        MyInvoisClient(config, transport=httpx.MockTransport(success_response))
    assert_sanitized(caught.value)


def test_client_rejects_non_async_transport_before_any_http():
    with pytest.raises(MyInvoisError, match="asynchronous HTTP transport"):
        MyInvoisClient(MyInvoisConfig(CLIENT_ID, CLIENT_SECRET), transport=object())


@pytest.mark.parametrize("document_id", ["A", "_", "-", DOCUMENT_ID, "a" * 128])
def test_document_ids_are_opaque_and_preserved(document_id):
    assert validate_document_id(document_id) == document_id


@pytest.mark.parametrize("document_id", [
    None, 123, b"opaque", "", "a" * 129, "../private", "a/b", "a\\b", "a%2Fb",
    "a?x=1", "a#fragment", "a:b", " a", "a\n", "\u00e9",
])
def test_document_ids_reject_paths_encoding_and_non_ascii(document_id):
    with pytest.raises(MyInvoisError, match="document ID"):
        validate_document_id(document_id)


@pytest.mark.parametrize("text,expected", [
    ("", []),
    (" \t\n", []),
    (" A;B, A\nC\tB ", ["A", "B", "C"]),
    ("case,Case,case", ["case", "Case"]),
    (";".join(str(i) for i in range(10)), [str(i) for i in range(10)]),
    (";".join(str(i) for i in range(10)) + ";0;9", [str(i) for i in range(10)]),
    ("A" + " " * 1999, ["A"]),
])
def test_parser_preserves_order_deduplicates_and_enforces_distinct_limit(text, expected):
    assert parse_document_ids(text) == expected


@pytest.mark.parametrize("text,message", [
    (None, "must be text"),
    ([], "must be text"),
    (" " * 2001, "at most 2000"),
    (", ; ,", "at least one"),
    ("A;../private", "document ID"),
    ("A;B%2FC", "document ID"),
    (";".join(str(i) for i in range(11)), "at most 10 distinct"),
])
def test_parser_fails_closed_on_invalid_input(text, message):
    with pytest.raises(MyInvoisError, match=message):
        parse_document_ids(text)


@pytest.mark.parametrize("document_id", ["../private", None])
def test_invalid_fetch_id_never_requests_a_token(document_id):
    client, requests = make_client()
    fetch_error(client, "document ID", document_id)
    assert requests == []


@pytest.mark.parametrize("environment", ["sandbox", "production"])
@pytest.mark.parametrize("auth_mode,identity", [
    ("taxpayer", None), ("taxpayer", IDENTITY), ("intermediary", IDENTITY),
])
def test_exact_official_urls_form_and_auth_headers(environment, auth_mode, identity, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", REMOTE_URL)
    monkeypatch.setenv("HTTPS_PROXY", REMOTE_URL)
    client, requests = make_client(
        environment=environment, auth_mode=auth_mode, on_behalf_of=identity
    )
    result = fetch(client)
    assert methods(requests) == ["POST", "GET"]
    login, raw = requests
    assert str(login.url) == BASE_URLS[environment] + "/connect/token"
    expected_form = [
        ("grant_type", "client_credentials"),
        ("scope", "InvoicingAPI"),
        ("client_id", CLIENT_ID),
        ("client_secret", CLIENT_SECRET),
    ]
    assert login.content == urlencode(expected_form).encode("ascii")
    assert parse_qsl(login.content.decode("ascii")) == expected_form
    assert login.headers["accept"] == "application/json"
    assert login.headers["content-type"] == "application/x-www-form-urlencoded"
    assert "authorization" not in login.headers
    if auth_mode == "intermediary":
        assert login.headers["onbehalfof"] == IDENTITY
    else:
        assert "onbehalfof" not in login.headers
    assert str(raw.url) == BASE_URLS[environment] + f"/api/v1.0/documents/{DOCUMENT_ID}/raw"
    assert raw.content == b""
    assert raw.headers["authorization"] == "Bearer " + TOKEN
    assert raw.headers["accept"] == raw.headers["content-type"] == "application/json"
    assert "onbehalfof" not in raw.headers
    assert result["envelope"] == DOCUMENT_PAYLOAD
    for request in requests:
        assert CLIENT_ID not in str(request.url) and CLIENT_SECRET not in str(request.url)


def test_snapshot_preserves_exact_bytes_hash_and_decimal_lexeme():
    body = (
        b'{"uuid":"Opaque_Ab-019","document":"<Invoice>offline</Invoice>",'
        b'"metric":1.2300,"large":1e309}'
    )

    def handler(request):
        if request.method == "POST":
            return token_response()
        return httpx.Response(
            200, content=body, headers={"Content-Type": "Application/JSON; charset=utf-8"}
        )

    client, requests = make_client(handler)
    result = fetch(client)
    assert methods(requests) == ["POST", "GET"]
    assert result["response_bytes"] == body
    assert result["envelope"]["metric"] == "1.2300"
    assert result["envelope"]["large"] == "1e309"
    provenance = result["provenance"]
    assert provenance["response_sha256"] == hashlib.sha256(body).hexdigest()
    assert provenance["endpoint"] == f"/api/v1.0/documents/{DOCUMENT_ID}/raw"
    assert provenance["environment"] == "sandbox"
    assert provenance["auth_mode"] == "taxpayer"
    assert provenance["content_type"] == "Application/JSON; charset=utf-8"
    assert provenance["freshness"] == "one-shot snapshot; not continuous status assurance"
    assert datetime.fromisoformat(provenance["fetched_at"]).utcoffset().total_seconds() == 0
    for private in (CLIENT_ID, CLIENT_SECRET, IDENTITY, TOKEN):
        assert private not in repr(result)


def test_token_is_cached_across_two_document_calls(clock):
    client, requests = make_client()

    async def scenario():
        first = await client.fetch_document(DOCUMENT_ID)
        second = await client.fetch_document("Another_opaque-ID")
        return first, second

    first, second = asyncio.run(scenario())
    assert methods(requests) == ["POST", "GET", "GET"]
    assert first["envelope"]["uuid"] == DOCUMENT_ID
    assert second["envelope"]["uuid"] == "Another_opaque-ID"
    assert [request.headers["authorization"] for request in requests[1:]] == [
        "Bearer " + TOKEN, "Bearer " + TOKEN
    ]


def test_token_renews_at_expiry_skew_boundary_without_sleep(clock):
    logins = 0

    def handler(request):
        nonlocal logins
        if request.method == "POST":
            logins += 1
            return token_response(access_token=TOKEN if logins == 1 else RENEWED_TOKEN, expires_in=30)
        return success_response(request)

    client, requests = make_client(handler)

    async def scenario():
        await client.fetch_document(DOCUMENT_ID)
        clock.now += 24.999
        await client.fetch_document(DOCUMENT_ID)
        clock.now = 1025.0
        await client.fetch_document(DOCUMENT_ID)

    asyncio.run(scenario())
    assert methods(requests) == ["POST", "GET", "GET", "POST", "GET"]
    assert [r.headers["authorization"] for r in requests if r.method == "GET"] == [
        "Bearer " + TOKEN, "Bearer " + TOKEN, "Bearer " + RENEWED_TOKEN
    ]


@pytest.mark.parametrize("second_status", [200, 401])
def test_401_allows_exactly_one_renewal_and_invalidates_failed_token(second_status):
    logins = 0
    gets = 0

    def handler(request):
        nonlocal logins, gets
        if request.method == "POST":
            logins += 1
            return token_response(access_token=TOKEN if logins == 1 else RENEWED_TOKEN)
        gets += 1
        if gets == 1 or second_status == 401:
            return httpx.Response(401, text=REMOTE_ECHO, headers={"Content-Type": "text/html"})
        return success_response(request)

    client, requests = make_client(handler)
    if second_status == 200:
        assert fetch(client)["envelope"] == DOCUMENT_PAYLOAD
    else:
        fetch_error(client, "authentication failed after one renewal")
        assert client._token is None
    assert methods(requests) == ["POST", "GET", "POST", "GET"]
    assert requests[1].headers["authorization"] == "Bearer " + TOKEN
    assert requests[3].headers["authorization"] == "Bearer " + RENEWED_TOKEN


@pytest.mark.parametrize("stage", ["POST", "GET"])
@pytest.mark.parametrize("retry_after,seconds", [
    ("3", 3.0),
    (None, 0.25),
    ("invalid " + REMOTE_ECHO, 0.25),
    ("9" * 129, 0.25),
    ("Fri, 02 Jan 2026 00:00:03 GMT", 3.0),
    ("Fri, 02 Jan 2026 00:00:03", 0.25),
])
def test_429_blocks_all_calls_until_retry_after_then_recovers(
    stage, retry_after, seconds, clock, monkeypatch
):
    frozen = datetime(2026, 1, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(myinvois, "datetime", SimpleNamespace(now=lambda tz: frozen))
    limited = False

    def handler(request):
        nonlocal limited
        if request.method == stage and not limited:
            limited = True
            headers = {} if retry_after is None else {"Retry-After": retry_after}
            return httpx.Response(429, text=REMOTE_ECHO, headers=headers)
        return success_response(request)

    client, requests = make_client(handler)
    fetch_error(client, "rate limit reached")
    initial = ["POST"] if stage == "POST" else ["POST", "GET"]
    assert methods(requests) == initial
    fetch_error(client, "cooldown is active", "Other_document")
    assert methods(requests) == initial
    clock.now += seconds - 0.001
    fetch_error(client, "cooldown is active")
    assert methods(requests) == initial
    clock.now += 0.002
    assert fetch(client)["envelope"] == DOCUMENT_PAYLOAD
    suffix = ["POST", "GET"] if stage == "POST" else ["GET"]
    assert methods(requests) == initial + suffix


STRICT_MEMBERS = json.dumps({**TOKEN_PAYLOAD, **DOCUMENT_PAYLOAD, "echo": REMOTE_ECHO})[:-1]


@pytest.mark.parametrize("stage", ["POST", "GET"])
@pytest.mark.parametrize("body,message", [
    (b'{"broken":', "strict UTF-8 JSON"),
    ((STRICT_MEMBERS + ',"uuid":"duplicate"}').encode(), "strict UTF-8 JSON"),
    ((STRICT_MEMBERS + ',"nested":{"x":1,"x":2}}').encode(), "strict UTF-8 JSON"),
    ((STRICT_MEMBERS + ',"value":NaN}').encode(), "strict UTF-8 JSON"),
    ((STRICT_MEMBERS + ',"value":Infinity}').encode(), "strict UTF-8 JSON"),
    ((STRICT_MEMBERS + ',"value":-Infinity}').encode(), "strict UTF-8 JSON"),
    (b'\xff', "strict UTF-8 JSON"),
    (b'[]', "must be a JSON object"),
    (b'null', "must be a JSON object"),
], ids=[
    "malformed", "duplicate-top-level", "duplicate-nested", "nan", "positive-infinity",
    "negative-infinity", "invalid-utf8", "array-root", "null-root",
])
def test_strict_json_rejection_never_retries_or_echoes_payload(stage, body, message):
    def handler(request):
        if request.method == stage:
            return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})
        return success_response(request)

    client, requests = make_client(handler)
    fetch_error(client, message)
    assert methods(requests) == (["POST"] if stage == "POST" else ["POST", "GET"])


@pytest.mark.parametrize("stage", ["POST", "GET"])
@pytest.mark.parametrize("headers,message", [
    ([], "requires one JSON Content-Type"),
    ([("Content-Type", "application/json"), ("Content-Type", "application/json")],
     "requires one JSON Content-Type"),
    ([("Content-Type", "text/html")], "must be application/json"),
    ([("Content-Type", "application/problem+json")], "must be application/json"),
    ([("Content-Type", "application/json, application/json")], "must be application/json"),
    ([("Content-Type", "application/json;" + "x" * 256)], "must be application/json"),
    ([("Content-Type", "application/json;\tcharset=utf-8")], "must be application/json"),
])
def test_content_type_must_be_single_bounded_ascii_json(stage, headers, message):
    def handler(request):
        if request.method == stage:
            payload = TOKEN_PAYLOAD if stage == "POST" else DOCUMENT_PAYLOAD
            return httpx.Response(200, content=json.dumps(payload).encode(), headers=headers)
        return success_response(request)

    client, requests = make_client(handler)
    fetch_error(client, message)
    assert methods(requests) == (["POST"] if stage == "POST" else ["POST", "GET"])


@pytest.mark.parametrize("stage,status", [("POST", 302), ("POST", 307), ("GET", 301), ("GET", 308)])
def test_redirects_are_rejected_without_following_location(stage, status):
    def handler(request):
        if request.method == stage:
            return httpx.Response(status, text=REMOTE_ECHO, headers={"Location": REMOTE_URL})
        return success_response(request)

    client, requests = make_client(handler)
    fetch_error(client, "redirects are forbidden")
    assert methods(requests) == (["POST"] if stage == "POST" else ["POST", "GET"])
    assert all(request.url.host == "preprod-api.myinvois.hasil.gov.my" for request in requests)


@pytest.mark.parametrize("stage,status,message", [
    ("POST", 400, "authentication request failed"),
    ("POST", 401, "authentication request failed"),
    ("POST", 500, "authentication request failed"),
    ("GET", 403, "document request failed"),
    ("GET", 404, "document request failed"),
    ("GET", 503, "document request failed"),
])
def test_remote_error_bodies_with_credentials_are_not_exposed_or_retried(stage, status, message):
    def handler(request):
        if request.method == stage:
            return httpx.Response(status, json={"error_description": REMOTE_ECHO})
        return success_response(request)

    client, requests = make_client(handler)
    fetch_error(client, message)
    assert methods(requests) == (["POST"] if stage == "POST" else ["POST", "GET"])


@pytest.mark.parametrize("uuid", [None, "another-id", DOCUMENT_ID.lower(), 123])
def test_document_identity_must_match_exactly(uuid):
    def handler(request):
        if request.method == "POST":
            return token_response()
        return httpx.Response(200, json={**DOCUMENT_PAYLOAD, "uuid": uuid, "echo": REMOTE_ECHO})

    client, requests = make_client(handler)
    fetch_error(client, "identity does not match")
    assert methods(requests) == ["POST", "GET"]


@pytest.mark.parametrize("document", [None, "", {}, 123])
def test_document_must_be_a_nonempty_string(document):
    def handler(request):
        if request.method == "POST":
            return token_response()
        return httpx.Response(200, json={**DOCUMENT_PAYLOAD, "document": document, "echo": REMOTE_ECHO})

    client, requests = make_client(handler)
    fetch_error(client, "requires a nonempty document string")
    assert methods(requests) == ["POST", "GET"]


@pytest.mark.parametrize("changes", [
    {"access_token": None},
    {"access_token": ""},
    {"access_token": 123},
    {"access_token": "token with space"},
    {"access_token": "token\r\nInjected: value"},
    {"access_token": "token\u00e9"},
    {"token_type": None},
    {"token_type": "Basic"},
    {"token_type": " bearer"},
    {"expires_in": None},
    {"expires_in": True},
    {"expires_in": 30.0},
    {"expires_in": "3600"},
    {"expires_in": 29},
    {"expires_in": 86401},
])
def test_invalid_tokens_are_not_cached_or_used_as_authorization(changes):
    client, requests = make_client(lambda request: token_response(**changes, echo=REMOTE_ECHO))
    fetch_error(client, "token response is invalid")
    assert methods(requests) == ["POST"]
    assert client._token is None


@pytest.mark.parametrize("token_type,expires_in", [("bearer", 30), ("Bearer", 3600), ("BEARER", 86400)])
def test_valid_token_type_is_case_insensitive_and_expiry_bounds_are_inclusive(token_type, expires_in):
    def handler(request):
        if request.method == "POST":
            return token_response(token_type=token_type, expires_in=expires_in)
        return success_response(request)

    client, requests = make_client(handler)
    assert fetch(client)["envelope"] == DOCUMENT_PAYLOAD
    assert methods(requests) == ["POST", "GET"]
    assert requests[-1].headers["authorization"] == "Bearer " + TOKEN


@pytest.mark.parametrize("stage", ["POST", "GET"])
@pytest.mark.parametrize("over_limit", [False, True])
def test_response_size_limit_is_inclusive_and_counts_utf8_bytes(stage, over_limit, monkeypatch):
    payload = TOKEN_PAYLOAD if stage == "POST" else {**DOCUMENT_PAYLOAD, "document": "\u03a9" * 80}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    constant = "_TOKEN_RESPONSE_LIMIT" if stage == "POST" else "_RAW_RESPONSE_LIMIT"
    monkeypatch.setattr(myinvois, constant, len(body) - int(over_limit))

    def handler(request):
        if request.method == stage:
            return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})
        return success_response(request)

    client, requests = make_client(handler)
    if over_limit:
        fetch_error(client, "response exceeds the permitted size")
    else:
        assert fetch(client)["envelope"]["uuid"] == DOCUMENT_ID
    assert methods(requests) == (["POST"] if stage == "POST" and over_limit else ["POST", "GET"])


def test_streamed_response_limit_accumulates_chunks_and_closes_stream(monkeypatch):
    tail = json.dumps(DOCUMENT_PAYLOAD).encode().ljust(64 * 1024, b" ")
    state = SimpleNamespace(yielded=0, closed=False)

    class ResponseStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in (b" " * (64 * 1024), tail, b"must-not-be-consumed"):
                state.yielded += 1
                yield chunk

        async def aclose(self):
            state.closed = True

    monkeypatch.setattr(myinvois, "_RAW_RESPONSE_LIMIT", 64 * 1024 + len(tail) - 1)

    def handler(request):
        if request.method == "POST":
            return token_response()
        return httpx.Response(200, stream=ResponseStream(), headers={"Content-Type": "application/json"})

    client, requests = make_client(handler)
    fetch_error(client, "response exceeds the permitted size")
    assert methods(requests) == ["POST", "GET"]
    assert state.yielded == 2
    assert state.closed


def test_compressed_body_rejected_before_stream_decompression(monkeypatch):
    body = json.dumps({**DOCUMENT_PAYLOAD, "document": "x" * 2048}).encode()
    compressed = gzip.compress(body)
    assert len(compressed) < len(body) - 1
    monkeypatch.setattr(myinvois, "_RAW_RESPONSE_LIMIT", len(body) - 1)

    def handler(request):
        if request.method == "POST":
            return token_response()
        return httpx.Response(200, content=compressed, headers={
            "Content-Type": "application/json", "Content-Encoding": "gzip"
        })

    client, requests = make_client(handler)
    fetch_error(client, "Compressed MyInvois responses are unsupported")
    assert methods(requests) == ["POST", "GET"]


@pytest.mark.parametrize("stage", ["POST", "GET"])
@pytest.mark.parametrize("error_type,message", [
    (httpx.ConnectError, "failed safely"),
    (httpx.ReadError, "failed safely"),
    (httpx.RemoteProtocolError, "failed safely"),
    (httpx.ConnectTimeout, "timed out"),
    (httpx.ReadTimeout, "timed out"),
    (OSError, "failed safely"),
    (ValueError, "failed safely"),
])
def test_transport_failures_and_timeouts_are_sanitized_without_retries(stage, error_type, message):
    def handler(request):
        if request.method == stage:
            if issubclass(error_type, httpx.HTTPError):
                raise error_type(REMOTE_ECHO, request=request)
            raise error_type(REMOTE_ECHO)
        return success_response(request)

    client, requests = make_client(handler)
    error = fetch_error(client, message)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert methods(requests) == (["POST"] if stage == "POST" else ["POST", "GET"])


def test_whole_fetch_deadline_cancels_pending_mock_request_and_is_sanitized(monkeypatch):
    state = SimpleNamespace(cancelled=False, requests=[])
    monkeypatch.setattr(myinvois, "_FETCH_TIMEOUT_SECONDS", 0.05)

    async def pending(request):
        state.requests.append(request)
        try:
            await asyncio.Future()
        finally:
            state.cancelled = True

    client = MyInvoisClient(
        MyInvoisConfig(CLIENT_ID, CLIENT_SECRET), transport=httpx.MockTransport(pending)
    )
    fetch_error(client, "timed out")
    assert methods(state.requests) == ["POST"]
    assert state.cancelled


def test_midstream_network_failure_is_sanitized_and_closes_response():
    state = SimpleNamespace(closed=False)

    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"uuid":'
            raise httpx.ReadError(REMOTE_ECHO)

        async def aclose(self):
            state.closed = True

    def handler(request):
        if request.method == "POST":
            return token_response()
        return httpx.Response(200, stream=InterruptedStream(), headers={"Content-Type": "application/json"})

    client, requests = make_client(handler)
    fetch_error(client, "failed safely")
    assert methods(requests) == ["POST", "GET"]
    assert state.closed
