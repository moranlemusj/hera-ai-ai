"""Hera dashboard session management.

The Hera dashboard at app.hera.video is gated behind BetterAuth cookies. Our
public REST integration uses x-api-key (separate, lives in env). The dashboard
endpoints we scrape (templates) require the user's logged-in cookies, which
expire every ~3 days.

This module handles:
- Parsing a pasted cURL command (from DevTools "Copy as cURL") into cookies.
- Validating the session by pinging the templates endpoint.
- Persisting the session in the `app_secrets` table on Neon.
- Decoding the BetterAuth `session_data` cookie to extract `expiresAt`.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

import httpx
from psycopg.types.json import Jsonb

from app.db import get_conn

log = logging.getLogger(__name__)

TEMPLATES_VALIDATE_URL = (
    "https://app.hera.video/api/templates?page=1&pageSize=1&public=true"
)
SESSION_TOKEN_COOKIE = "__Secure-better-auth.session_token"
SESSION_DATA_COOKIE = "__Secure-better-auth.session_data"
SECRET_KEY = "hera_session"
VALIDATE_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class HeraSessionError(Exception):
    """Base class for hera-session failures the UI should render specifically."""

    code: str = "hera_session_error"


class CurlParseError(HeraSessionError):
    code = "curl_parse_error"


class NoSessionTokenError(HeraSessionError):
    code = "no_session_token"


class SessionValidationError(HeraSessionError):
    code = "session_validation_failed"


class SessionNetworkError(HeraSessionError):
    code = "session_network_error"


class HeraSessionExpiredError(HeraSessionError):
    """Raised when a Hera dashboard call returns 401 — used to trigger interrupt."""

    code = "session_expired"


# ---------------------------------------------------------------------------
# cURL parsing
# ---------------------------------------------------------------------------

# Match -b 'cookies' or --cookie 'cookies' with single OR double quotes.
_COOKIE_FLAG_RE = re.compile(
    r"""(?:-b|--cookie)\s+(?P<q>['"])(?P<val>.+?)(?P=q)""",
    re.DOTALL,
)
# Match -H 'cookie: ...' (case-insensitive header name) with either quote style.
_COOKIE_HEADER_RE = re.compile(
    r"""-H\s+(?P<q>['"])\s*[Cc]ookie\s*:\s*(?P<val>.+?)(?P=q)""",
    re.DOTALL,
)


def _strip_curl_continuations(curl_text: str) -> str:
    """Collapse `\\\n` line continuations so multi-line cURL parses uniformly."""
    return re.sub(r"\\\s*\n\s*", " ", curl_text)


def _parse_cookie_pairs(blob: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for raw in blob.split(";"):
        pair = raw.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def parse_curl(curl_text: str) -> dict[str, str]:
    """Extract cookies from a pasted cURL command.

    Handles `-b`, `--cookie`, and `-H 'Cookie: ...'`. Multi-line continuations
    (trailing backslash) are collapsed first. Single and double quoted values
    are both supported. Cookie values are returned as-is (URL-encoded if that's
    how the browser emitted them).
    """
    if not curl_text or not curl_text.strip():
        raise CurlParseError("cURL text is empty")

    text = _strip_curl_continuations(curl_text)
    cookies: dict[str, str] = {}

    for match in _COOKIE_FLAG_RE.finditer(text):
        cookies.update(_parse_cookie_pairs(match.group("val")))
    for match in _COOKIE_HEADER_RE.finditer(text):
        cookies.update(_parse_cookie_pairs(match.group("val")))

    if not cookies:
        raise CurlParseError(
            "No cookies found in cURL. Make sure you copied the full request "
            "(DevTools → Network → right-click → Copy → Copy as cURL)."
        )

    return cookies


# ---------------------------------------------------------------------------
# Expiry decoding
# ---------------------------------------------------------------------------


def decode_expiry(cookies: dict[str, str]) -> datetime | None:
    """Read the BetterAuth session_data cookie and extract `expiresAt`.

    The cookie is URL-encoded base64 (sometimes with the trailing
    `--<signature>` chunk that we ignore). Returns None if absent or
    unparseable; the caller decides whether that's fatal.
    """
    raw = cookies.get(SESSION_DATA_COOKIE)
    if not raw:
        return None

    try:
        decoded = unquote(raw)
        # Some BetterAuth deployments append `--<signature>`; the JSON payload
        # is the leading base64 segment.
        payload = decoded.split("--", 1)[0]
        # Pad in case the encoder stripped trailing `=`
        padded = payload + "=" * (-len(payload) % 4)
        blob = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        log.warning("Failed to b64-decode session_data: %s", exc)
        return None

    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        log.warning("Failed to JSON-parse session_data: %s", exc)
        return None

    # Shape: { session: { session: { expiresAt: ISO }, ... }, expiresAt: epochMs, ... }
    expires_at: Any = (
        data.get("expiresAt")
        or data.get("session", {}).get("session", {}).get("expiresAt")
        or data.get("session", {}).get("expiresAt")
    )
    if expires_at is None:
        return None

    if isinstance(expires_at, int | float):
        # Epoch milliseconds
        return datetime.fromtimestamp(expires_at / 1000, tz=UTC)
    if isinstance(expires_at, str):
        try:
            return datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def validate_session(cookies: dict[str, str]) -> bool:
    """Ping the templates endpoint with the cookies. True iff 200 + JSON body."""
    if SESSION_TOKEN_COOKIE not in cookies:
        raise NoSessionTokenError(
            f"Cookie `{SESSION_TOKEN_COOKIE}` missing — paste a cURL from a "
            "logged-in Hera tab."
        )

    try:
        async with httpx.AsyncClient(timeout=VALIDATE_TIMEOUT) as client:
            resp = await client.get(TEMPLATES_VALIDATE_URL, cookies=cookies)
    except httpx.HTTPError as exc:
        raise SessionNetworkError(f"Network error talking to Hera: {exc}") from exc

    if resp.status_code == 401:
        return False
    if resp.status_code != 200:
        raise SessionValidationError(
            f"Hera returned {resp.status_code}: {resp.text[:200]}"
        )

    try:
        body = resp.json()
    except ValueError as exc:
        raise SessionValidationError(f"Non-JSON response from Hera: {exc}") from exc

    if not isinstance(body, dict) or "data" not in body:
        raise SessionValidationError("Unexpected response shape from Hera /api/templates")

    return True


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def save_session(cookies: dict[str, str], expires_at: datetime | None) -> None:
    async with get_conn() as conn:
        await conn.execute(
            """
            INSERT INTO app_secrets (key, value, expires_at, last_validated, updated_at)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                expires_at = EXCLUDED.expires_at,
                last_validated = EXCLUDED.last_validated,
                updated_at = NOW()
            """,
            (SECRET_KEY, Jsonb(cookies), expires_at),
        )


async def load_session() -> tuple[dict[str, str] | None, datetime | None, datetime | None]:
    """Return (cookies, expires_at, last_validated) or (None, None, None)."""
    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT value, expires_at, last_validated FROM app_secrets WHERE key = %s",
            (SECRET_KEY,),
        )
        row = await cur.fetchone()
    if row is None:
        return None, None, None
    value, expires_at, last_validated = row
    return value, expires_at, last_validated


async def require_session() -> dict[str, str]:
    """Return cookies for use in a Hera dashboard call. Raises HeraSessionExpiredError if absent."""
    cookies, _expires_at, _last_validated = await load_session()
    if cookies is None:
        raise HeraSessionExpiredError(
            "No Hera session stored. Set one via Settings → Hera Session."
        )
    return cookies


async def clear_session() -> None:
    async with get_conn() as conn:
        await conn.execute("DELETE FROM app_secrets WHERE key = %s", (SECRET_KEY,))


# ---------------------------------------------------------------------------
# UI status
# ---------------------------------------------------------------------------


def _classify_status(
    cookies: dict[str, str] | None,
    expires_at: datetime | None,
) -> str:
    if cookies is None or SESSION_TOKEN_COOKIE not in cookies:
        return "missing"
    if expires_at is None:
        return "active"
    now = datetime.now(tz=UTC)
    if expires_at <= now:
        return "expired"
    if (expires_at - now).total_seconds() < 6 * 3600:
        return "expiring"
    return "active"


async def get_status() -> dict[str, Any]:
    cookies, expires_at, last_validated = await load_session()
    status = _classify_status(cookies, expires_at)
    seconds_until_expiry: float | None = None
    if expires_at is not None:
        seconds_until_expiry = max(
            0.0, (expires_at - datetime.now(tz=UTC)).total_seconds()
        )
    return {
        "status": status,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "last_validated": last_validated.isoformat() if last_validated else None,
        "seconds_until_expiry": seconds_until_expiry,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def update_from_curl(curl_text: str) -> dict[str, Any]:
    """Full flow: parse → validate → persist → return status payload."""
    cookies = parse_curl(curl_text)
    if SESSION_TOKEN_COOKIE not in cookies:
        raise NoSessionTokenError(
            f"Cookie `{SESSION_TOKEN_COOKIE}` missing from the cURL — make sure "
            "you copied a request from a logged-in Hera tab."
        )

    ok = await validate_session(cookies)
    if not ok:
        raise SessionValidationError(
            "Hera rejected the session (401). The cookies are likely already expired."
        )

    expires_at = decode_expiry(cookies)
    await save_session(cookies, expires_at)
    log.info(
        "Hera session saved (expires %s, %d cookies)",
        expires_at.isoformat() if expires_at else "unknown",
        len(cookies),
    )
    return await get_status()
