"""Pure-function tests for `cache_key`. The DB roundtrip lives in
tests/integration/test_render_cache_db.py.
"""

from __future__ import annotations

import hashlib

from app.services.render_cache import cache_key


class TestCacheKey:
    def test_is_deterministic(self) -> None:
        a = cache_key("p", "tpl", "16:9", 5.0, 30, "1080p")
        b = cache_key("p", "tpl", "16:9", 5.0, 30, "1080p")
        assert a == b

    def test_returns_sha256_hex(self) -> None:
        k = cache_key("p", None, "16:9", 5.0, 30, "1080p")
        assert len(k) == 64
        assert all(c in "0123456789abcdef" for c in k)

    def test_different_prompt_changes_key(self) -> None:
        a = cache_key("hello", None, "16:9", 5.0, 30, "1080p")
        b = cache_key("world", None, "16:9", 5.0, 30, "1080p")
        assert a != b

    def test_different_parent_video_id_changes_key(self) -> None:
        a = cache_key("p", "tpl-a", "16:9", 5.0, 30, "1080p")
        b = cache_key("p", "tpl-b", "16:9", 5.0, 30, "1080p")
        assert a != b

    def test_no_parent_vs_empty_string_match(self) -> None:
        # We canonicalize None → "" so an explicit "" caller can't drift apart.
        assert cache_key("p", None, "16:9", 5.0, 30, "1080p") == cache_key(
            "p", "", "16:9", 5.0, 30, "1080p"
        )

    def test_duration_changes_key(self) -> None:
        a = cache_key("p", None, "16:9", 5.0, 30, "1080p")
        b = cache_key("p", None, "16:9", 6.0, 30, "1080p")
        assert a != b

    def test_aspect_changes_key(self) -> None:
        a = cache_key("p", None, "16:9", 5.0, 30, "1080p")
        b = cache_key("p", None, "9:16", 5.0, 30, "1080p")
        assert a != b

    def test_resolution_changes_key(self) -> None:
        a = cache_key("p", None, "16:9", 5.0, 30, "1080p")
        b = cache_key("p", None, "16:9", 5.0, 30, "720p")
        assert a != b

    def test_known_value(self) -> None:
        # Pinned hash so a refactor of the canonicalization is a visible diff.
        expected = hashlib.sha256(
            b"hello\x1f\x1f16:9\x1f5.000\x1f30\x1f1080p"
        ).hexdigest()
        assert (
            cache_key("hello", None, "16:9", 5.0, 30, "1080p") == expected
        )
