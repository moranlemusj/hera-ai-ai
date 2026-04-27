"""Pure-function unit tests for templates content helpers.

`_content_source` builds the embedding source from a Hera template record.
`_content_hash` is sha256 of that source.

These are tiny but worth covering — they decide *what* we embed, and a silent
regression (e.g. dropping the title back out) would degrade search quality
without any visible failure mode.
"""

from __future__ import annotations

import hashlib

from app.services.templates import _content_hash, _content_source


class TestContentSource:
    def test_concatenates_title_and_summary(self) -> None:
        record = {"title": "WASHINGTON MAP", "summary": "A slow zoom into a dark map."}
        assert _content_source(record) == "WASHINGTON MAP\n\nA slow zoom into a dark map."

    def test_strips_whitespace(self) -> None:
        record = {"title": "  Bold Title  ", "summary": "\n  Body text  \n"}
        assert _content_source(record) == "Bold Title\n\nBody text"

    def test_omits_separator_when_title_missing(self) -> None:
        record = {"title": "", "summary": "Only the body."}
        assert _content_source(record) == "Only the body."

    def test_omits_separator_when_summary_missing(self) -> None:
        record = {"title": "Just A Title", "summary": ""}
        assert _content_source(record) == "Just A Title"

    def test_returns_empty_when_both_missing(self) -> None:
        assert _content_source({}) == ""
        assert _content_source({"title": None, "summary": None}) == ""
        assert _content_source({"title": "  ", "summary": "  "}) == ""


class TestContentHash:
    def test_is_deterministic(self) -> None:
        assert _content_hash("hello") == _content_hash("hello")

    def test_different_inputs_yield_different_hashes(self) -> None:
        assert _content_hash("abc") != _content_hash("abd")

    def test_is_sha256_hex(self) -> None:
        text = "WASHINGTON MAP\n\nA slow zoom into a dark map."
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert _content_hash(text) == expected
        assert len(_content_hash(text)) == 64

    def test_whitespace_significant(self) -> None:
        # Title vs summary on the same line vs separated should hash differently
        assert _content_hash("Title\n\nBody") != _content_hash("Title Body")
