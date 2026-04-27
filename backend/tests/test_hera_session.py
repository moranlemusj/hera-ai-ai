"""Unit tests for hera_session.parse_curl and decode_expiry.

These functions have real parsing logic — string manipulation, multi-flag
handling, base64 + JSON decoding — and are worth covering directly. Network /
DB-touching functions are covered by smoke checks, not here.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from urllib.parse import quote

import pytest

from app.services.hera_session import (
    SESSION_DATA_COOKIE,
    SESSION_TOKEN_COOKIE,
    CurlParseError,
    decode_expiry,
    parse_curl,
)


def _make_session_data_cookie(payload: dict) -> str:
    """Build a value that looks like Hera's URL-encoded base64 session_data."""
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return quote(f"{raw}--fakeSignatureValue", safe="")


# ---------------------------------------------------------------------------
# parse_curl
# ---------------------------------------------------------------------------


class TestParseCurl:
    def test_extracts_cookies_from_b_flag_single_quotes(self) -> None:
        cmd = "curl 'https://example.com/x' -b 'a=1; b=two; c=three=with=eq'"
        cookies = parse_curl(cmd)
        assert cookies["a"] == "1"
        assert cookies["b"] == "two"
        assert cookies["c"] == "three=with=eq"

    def test_extracts_cookies_from_double_quotes(self) -> None:
        cmd = 'curl "https://example.com/x" -b "a=1; b=2"'
        assert parse_curl(cmd) == {"a": "1", "b": "2"}

    def test_extracts_cookies_from_long_flag(self) -> None:
        cmd = "curl 'https://example.com/x' --cookie 'foo=bar; baz=qux'"
        assert parse_curl(cmd) == {"foo": "bar", "baz": "qux"}

    def test_extracts_cookies_from_cookie_header(self) -> None:
        cmd = "curl 'x' -H 'Cookie: a=1; b=2'"
        assert parse_curl(cmd) == {"a": "1", "b": "2"}

    def test_handles_lowercase_cookie_header(self) -> None:
        cmd = "curl 'x' -H 'cookie: a=1'"
        assert parse_curl(cmd) == {"a": "1"}

    def test_handles_multiline_continuations(self) -> None:
        cmd = (
            "curl 'https://example.com/x' \\\n"
            "  -H 'accept: */*' \\\n"
            "  -b 'session=abc; other=def' \\\n"
            "  -H 'user-agent: test'"
        )
        cookies = parse_curl(cmd)
        assert cookies == {"session": "abc", "other": "def"}

    def test_merges_b_flag_and_cookie_header(self) -> None:
        cmd = "curl 'x' -b 'a=1; b=2' -H 'Cookie: c=3'"
        cookies = parse_curl(cmd)
        assert cookies == {"a": "1", "b": "2", "c": "3"}

    def test_preserves_url_encoded_values(self) -> None:
        cmd = "curl 'x' -b 'token=abc%2Ddef%3Dxyz; sig=hello%20world'"
        cookies = parse_curl(cmd)
        assert cookies["token"] == "abc%2Ddef%3Dxyz"
        assert cookies["sig"] == "hello%20world"

    def test_handles_real_hera_curl_shape(self) -> None:
        """Mirrors the shape user pasted — BetterAuth cookies + analytics noise."""
        cmd = (
            "curl 'https://app.hera.video/api/templates?page=1&pageSize=24&public=true' "
            "-H 'accept: */*' "
            "-H 'accept-language: en-US,en' "
            "-b 'intercom-device-id-cldhhlw2=device123; "
            "__stripe_mid=stripe456; "
            f"{SESSION_TOKEN_COOKIE}=tokenABC; "
            f"{SESSION_DATA_COOKIE}=dataXYZ; "
            "ph_phc_test=ph789'"
        )
        cookies = parse_curl(cmd)
        assert cookies[SESSION_TOKEN_COOKIE] == "tokenABC"
        assert cookies[SESSION_DATA_COOKIE] == "dataXYZ"
        assert cookies["__stripe_mid"] == "stripe456"

    def test_empty_input_raises(self) -> None:
        with pytest.raises(CurlParseError):
            parse_curl("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(CurlParseError):
            parse_curl("   \n  \t  ")

    def test_no_cookies_present_raises(self) -> None:
        cmd = "curl 'https://example.com' -H 'accept: */*'"
        with pytest.raises(CurlParseError):
            parse_curl(cmd)

    def test_ignores_malformed_cookie_pairs(self) -> None:
        # 'novalue' has no '=' — should be skipped, not raise
        cmd = "curl 'x' -b 'a=1; novalue; b=2; =empty_name'"
        cookies = parse_curl(cmd)
        assert cookies == {"a": "1", "b": "2", "": "empty_name"}


# ---------------------------------------------------------------------------
# decode_expiry
# ---------------------------------------------------------------------------


class TestDecodeExpiry:
    def test_returns_none_when_session_data_missing(self) -> None:
        assert decode_expiry({"some-other-cookie": "x"}) is None

    def test_extracts_top_level_iso_expires_at(self) -> None:
        target = datetime(2026, 4, 30, 16, 56, 50, tzinfo=UTC)
        cookie = _make_session_data_cookie({"expiresAt": target.isoformat()})
        result = decode_expiry({SESSION_DATA_COOKIE: cookie})
        assert result == target

    def test_extracts_nested_expires_at(self) -> None:
        target = datetime(2026, 4, 30, 16, 56, 50, tzinfo=UTC)
        cookie = _make_session_data_cookie(
            {"session": {"session": {"expiresAt": target.isoformat()}}}
        )
        result = decode_expiry({SESSION_DATA_COOKIE: cookie})
        assert result == target

    def test_handles_epoch_milliseconds(self) -> None:
        target = datetime(2026, 4, 30, 16, 56, 50, tzinfo=UTC)
        cookie = _make_session_data_cookie({"expiresAt": int(target.timestamp() * 1000)})
        result = decode_expiry({SESSION_DATA_COOKIE: cookie})
        assert result == target

    def test_handles_z_suffix_iso(self) -> None:
        cookie = _make_session_data_cookie({"expiresAt": "2026-04-30T16:56:50.850Z"})
        result = decode_expiry({SESSION_DATA_COOKIE: cookie})
        assert result is not None
        assert result.tzinfo is not None
        assert result.year == 2026

    def test_returns_none_on_garbage_base64(self) -> None:
        assert decode_expiry({SESSION_DATA_COOKIE: "not%20valid%20b64!!!"}) is None

    def test_returns_none_on_missing_expires_field(self) -> None:
        cookie = _make_session_data_cookie({"unrelated": "field"})
        assert decode_expiry({SESSION_DATA_COOKIE: cookie}) is None
