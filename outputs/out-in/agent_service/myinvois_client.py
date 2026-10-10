"""Read-only, one-shot MyInvois document retrieval; caller owns live-mode gating."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from tools.reconciliation_tool import EvidenceError
from tools.myinvois_tool import check_json_structure

__all__ = [
    "MyInvoisConfig",
    "MyInvoisClient",
    "MyInvoisError",
    "enabled",
    "validate_document_id",
    "parse_document_ids",
]

_BASE_URLS = {
    "sandbox": "https://preprod-api.myinvois.hasil.gov.my",
    "production": "https://api.myinvois.hasil.gov.my",
}
_DOCUMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ON_BEHALF_OF = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?::[A-Za-z0-9][A-Za-z0-9_-]{0,63})?\Z"
)
_RAW_RESPONSE_LIMIT = 16 * 1024 * 1024
_TOKEN_RESPONSE_LIMIT = 64 * 1024
_RAW_REQUEST_INTERVAL_SECONDS = 1.0
_AUTH_COOLDOWN_SECONDS = 5.0
_FETCH_TIMEOUT_SECONDS = 60.0
_TOKEN_EXPIRY_SKEW_SECONDS = 5.0
_DEFAULT_RETRY_AFTER_SECONDS = 60.0
_FRESHNESS = "one-shot snapshot; not continuous status assurance"


class MyInvoisError(EvidenceError):
    """Sanitized configuration, transport, or evidence failure."""


def enabled() -> bool:
    return os.environ.get("OUT_IN_MYINVOIS_ENABLED") == "1"


def _safe_credential(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and all(0x21 <= ord(char) <= 0x7E for char in value)
    )


@dataclass(frozen=True)
class MyInvoisConfig:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    environment: str = "sandbox"
    auth_mode: str = "taxpayer"
    on_behalf_of: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.environment, str) or self.environment not in _BASE_URLS:
            raise MyInvoisError("MyInvois environment must be sandbox or production.")
        if not isinstance(self.auth_mode, str) or self.auth_mode not in ("taxpayer", "intermediary"):
            raise MyInvoisError("MyInvois authentication mode must be taxpayer or intermediary.")
        if not _safe_credential(self.client_id, 256):
            raise MyInvoisError("MyInvois client ID is missing or invalid.")
        if not _safe_credential(self.client_secret, 1024):
            raise MyInvoisError("MyInvois client secret is missing or invalid.")
        if self.on_behalf_of is not None and (
            not isinstance(self.on_behalf_of, str)
            or len(self.on_behalf_of) > 129
            or _ON_BEHALF_OF.fullmatch(self.on_behalf_of) is None
        ):
            raise MyInvoisError("MyInvois on-behalf-of identity must be a bounded TIN[:ROB].")
        if self.auth_mode == "intermediary" and self.on_behalf_of is None:
            raise MyInvoisError("MyInvois intermediary authentication requires an on-behalf-of identity.")

    @property
    def base_url(self) -> str:
        return _BASE_URLS[self.environment]

    @classmethod
    def from_env(cls) -> MyInvoisConfig:
        if not enabled():
            raise MyInvoisError("MyInvois retrieval is not enabled.")
        return cls(
            client_id=os.environ.get("OUT_IN_MYINVOIS_CLIENT_ID", ""),
            client_secret=os.environ.get("OUT_IN_MYINVOIS_CLIENT_SECRET", ""),
            environment=os.environ.get("OUT_IN_MYINVOIS_ENVIRONMENT", "sandbox"),
            auth_mode=os.environ.get("OUT_IN_MYINVOIS_AUTH_MODE", "taxpayer"),
            on_behalf_of=os.environ.get("OUT_IN_MYINVOIS_ON_BEHALF_OF") or None,
        )


def validate_document_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or _DOCUMENT_ID.fullmatch(value) is None
    ):
        raise MyInvoisError("MyInvois document ID must contain 1..128 letters, digits, underscores, or hyphens.")
    return value


def parse_document_ids(value: str) -> list[str]:
    if not isinstance(value, str) or len(value) > 2000:
        raise MyInvoisError("MyInvois document ID input must be text of at most 2000 characters.")
    if not value.strip():
        return []
    result = []
    seen = set()
    for item in re.split(r"[;,\s]+", value):
        if not item:
            continue
        document_id = validate_document_id(item)
        if document_id not in seen:
            seen.add(document_id)
            result.append(document_id)
        if len(result) > 10:
            raise MyInvoisError("MyInvois retrieval requires at most 10 distinct document IDs.")
    if not result:
        raise MyInvoisError("MyInvois retrieval requires at least one document ID.")
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _forbid_constant(value: str) -> None:
    raise ValueError("Nonstandard JSON constant")


def _strict_json(body: bytes) -> dict:
    try:
        text = body.decode("utf-8")
        check_json_structure(text)
        envelope = json.loads(
            text,
            parse_float=str,
            parse_constant=_forbid_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise MyInvoisError("MyInvois response is not valid strict UTF-8 JSON.") from None
    if not isinstance(envelope, dict):
        raise MyInvoisError("MyInvois response must be a JSON object.")
    return envelope


def _retry_after_seconds(value: str | None) -> float:
    if not isinstance(value, str) or len(value) > 128:
        return _DEFAULT_RETRY_AFTER_SECONDS
    value = value.strip()
    if re.fullmatch(r"[0-9]{1,128}", value):
        return float(int(value))
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            return _DEFAULT_RETRY_AFTER_SECONDS
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return _DEFAULT_RETRY_AFTER_SECONDS


class MyInvoisClient:
    def __init__(
        self,
        config: MyInvoisConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not isinstance(config, MyInvoisConfig):
            raise MyInvoisError("MyInvois requires validated configuration.")
        config.__post_init__()
        if transport is not None and not isinstance(transport, httpx.AsyncBaseTransport):
            raise MyInvoisError("MyInvois transport must be an asynchronous HTTP transport.")
        self._config = config
        self._transport = transport
        self._fetch_lock = asyncio.Lock()
        self._auth_lock = asyncio.Lock()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._next_login_at = 0.0
        self._next_raw_at = 0.0
        self._retry_after_until = 0.0

    def _check_rate_limit(self) -> None:
        if time.monotonic() < self._retry_after_until:
            raise MyInvoisError("MyInvois rate-limit cooldown is active; retry later.")

    async def _wait_until(self, permitted_at: float) -> None:
        remaining = permitted_at - time.monotonic()
        while remaining > 0:
            await asyncio.sleep(remaining)
            remaining = permitted_at - time.monotonic()

    def _record_rate_limit(self, response: httpx.Response, *, token_request: bool) -> None:
        retry_at = time.monotonic() + _retry_after_seconds(response.headers.get("Retry-After"))
        self._retry_after_until = max(self._retry_after_until, retry_at)
        if token_request:
            self._next_login_at = max(self._next_login_at, retry_at)
        else:
            self._next_raw_at = max(self._next_raw_at, retry_at)

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        headers: dict[str, str],
        limit: int,
        data: dict[str, str] | None = None,
    ) -> tuple[int, bytes, str]:
        self._check_rate_limit()
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                method,
                self._config.base_url + endpoint,
                headers=headers,
                data=data,
            ) as response:
                status = response.status_code
                if status == 429:
                    self._record_rate_limit(response, token_request=method == "POST")
                    raise MyInvoisError("MyInvois rate limit reached; retry later.")
                if 300 <= status < 400:
                    raise MyInvoisError("MyInvois redirects are forbidden.")
                if status == 401 and method == "GET":
                    return status, b"", ""
                if status != 200:
                    raise MyInvoisError("MyInvois authentication request failed." if method == "POST"
                                         else "MyInvois document request failed.")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise MyInvoisError("Compressed MyInvois responses are unsupported; bounded identity encoding required.")
                content_types = response.headers.get_list("Content-Type")
                if len(content_types) != 1:
                    raise MyInvoisError("MyInvois response requires one JSON Content-Type.")
                content_type = content_types[0]
                if (
                    not 1 <= len(content_type) <= 256
                    or not all(0x20 <= ord(char) <= 0x7E for char in content_type)
                    or content_type.split(";", 1)[0].strip().lower() != "application/json"
                ):
                    raise MyInvoisError("MyInvois response Content-Type must be application/json.")
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise MyInvoisError("MyInvois response exceeds the permitted size.")
                    chunks.append(chunk)
                return status, b"".join(chunks), content_type

    async def _get_token(self) -> str:
        async with self._auth_lock:
            self._check_rate_limit()
            if self._token is not None and time.monotonic() < self._token_expires_at:
                return self._token
            self._token = None
            self._token_expires_at = 0.0
            await self._wait_until(self._next_login_at)
            self._check_rate_limit()
            login_started = time.monotonic()
            self._next_login_at = login_started + _AUTH_COOLDOWN_SECONDS
            headers = {"Accept": "application/json", "Accept-Encoding": "identity", "Content-Type": "application/x-www-form-urlencoded"}
            if self._config.auth_mode == "intermediary":
                headers["onbehalfof"] = self._config.on_behalf_of
            try:
                _, body, _ = await self._request(
                    "POST",
                    "/connect/token",
                    headers=headers,
                    limit=_TOKEN_RESPONSE_LIMIT,
                    data={
                        "grant_type": "client_credentials",
                        "scope": "InvoicingAPI",
                        "client_id": self._config.client_id,
                        "client_secret": self._config.client_secret,
                    },
                )
            finally:
                self._next_login_at = max(
                    self._next_login_at, time.monotonic() + _AUTH_COOLDOWN_SECONDS
                )
            payload = _strict_json(body)
            token = payload.get("access_token")
            token_type = payload.get("token_type")
            expires_in = payload.get("expires_in")
            if (
                not _safe_credential(token, _TOKEN_RESPONSE_LIMIT)
                or not isinstance(token_type, str)
                or token_type.lower() != "bearer"
                or type(expires_in) is not int
                or not 30 <= expires_in <= 86400
            ):
                raise MyInvoisError("MyInvois token response is invalid.")
            self._token = token
            self._token_expires_at = login_started + expires_in - _TOKEN_EXPIRY_SKEW_SECONDS
            return token

    async def _invalidate_token(self, used_token: str) -> None:
        async with self._auth_lock:
            if self._token == used_token:
                self._token = None
                self._token_expires_at = 0.0

    async def _fetch_document(self, document_id: str) -> dict:
        # The lock spans renewal and both possible GETs, preventing concurrent 401 storms.
        async with self._fetch_lock:
            self._check_rate_limit()
            endpoint = f"/api/v1.0/documents/{document_id}/raw"
            for attempt in range(2):
                await self._wait_until(self._next_raw_at)
                token = await self._get_token()
                self._check_rate_limit()
                self._next_raw_at = time.monotonic() + _RAW_REQUEST_INTERVAL_SECONDS
                try:
                    status, body, content_type = await self._request(
                        "GET",
                        endpoint,
                        headers={
                            "Authorization": "Bearer " + token,
                            "Accept-Encoding": "identity",
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                        },
                        limit=_RAW_RESPONSE_LIMIT,
                    )
                finally:
                    self._next_raw_at = max(
                        self._next_raw_at, time.monotonic() + _RAW_REQUEST_INTERVAL_SECONDS
                    )
                if status == 401:
                    await self._invalidate_token(token)
                    if attempt == 0:
                        continue
                    raise MyInvoisError("MyInvois document authentication failed after one renewal.")
                envelope = _strict_json(body)
                if envelope.get("uuid") != document_id:
                    raise MyInvoisError("MyInvois response document identity does not match the request.")
                document = envelope.get("document")
                if not isinstance(document, str) or not document:
                    raise MyInvoisError("MyInvois response requires a nonempty document string.")
                if len(document.encode("utf-8")) > _RAW_RESPONSE_LIMIT:
                    raise MyInvoisError("MyInvois document exceeds the permitted size.")
                return {
                    "response_bytes": body,
                    "envelope": envelope,
                    "provenance": {
                        "environment": self._config.environment,
                        "auth_mode": self._config.auth_mode,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "endpoint": endpoint,
                        "response_sha256": hashlib.sha256(body).hexdigest(),
                        "content_type": content_type,
                        "freshness": _FRESHNESS,
                    },
                }
        raise MyInvoisError("MyInvois document retrieval failed.")

    async def fetch_document(self, document_id: str) -> dict:
        document_id = validate_document_id(document_id)
        try:
            return await asyncio.wait_for(
                self._fetch_document(document_id), timeout=_FETCH_TIMEOUT_SECONDS
            )
        except MyInvoisError:
            raise
        except (asyncio.TimeoutError, httpx.TimeoutException):
            raise MyInvoisError("MyInvois document retrieval timed out.") from None
        except (httpx.HTTPError, OSError, UnicodeError, ValueError, RecursionError):
            raise MyInvoisError("MyInvois document retrieval failed safely.") from None
