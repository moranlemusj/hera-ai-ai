"""Article extraction via Jina Reader with httpx + readability fallback.

Jina Reader (`https://r.jina.ai/{url}`) returns LLM-clean markdown of any URL
with JS rendered server-side. Free, zero infra. We fall back to a local
parser only if Jina fails.
"""

from __future__ import annotations

import logging
from typing import TypedDict

import httpx
from readability import Document

log = logging.getLogger(__name__)

JINA_BASE = "https://r.jina.ai/"
DEFAULT_TIMEOUT = 30.0
HTTP_HEADERS = {
    "Accept": "text/markdown",
    "User-Agent": "Mozilla/5.0 (compatible; HeraAgent/0.1)",
}


class ArticleFetchError(Exception):
    """Raised when both Jina and the local fallback fail to extract content."""


class Article(TypedDict):
    title: str
    byline: str | None
    text: str
    url: str


_MIN_TEXT_CHARS = 200


async def _fetch_via_jina(url: str) -> Article:
    full = JINA_BASE + url
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(full, headers={"Accept": "text/markdown"})
    if resp.status_code != 200:
        raise ArticleFetchError(f"Jina returned {resp.status_code}: {resp.text[:200]}")
    body = resp.text or ""
    if len(body) < _MIN_TEXT_CHARS:
        raise ArticleFetchError(f"Jina body too short ({len(body)} chars)")

    title = ""
    byline: str | None = None
    text = body
    # Jina prefixes with "Title: ...", "URL Source: ...", etc.
    lines = body.splitlines()
    for line in lines[:8]:
        if line.startswith("Title:"):
            title = line[len("Title:") :].strip()
        elif line.startswith("Markdown Content:"):
            idx = body.find("Markdown Content:") + len("Markdown Content:")
            text = body[idx:].strip()
            break
    return Article(title=title, byline=byline, text=text, url=url)


async def _fetch_via_readability(url: str) -> Article:
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers=HTTP_HEADERS)
    if resp.status_code != 200:
        raise ArticleFetchError(f"Source returned {resp.status_code}")
    if not resp.text:
        raise ArticleFetchError("Empty response body")

    doc = Document(resp.text)
    title = doc.title() or ""
    summary_html = doc.summary() or ""

    # readability returns HTML; strip to plain text crudely
    from bs4 import BeautifulSoup

    text = BeautifulSoup(summary_html, "lxml").get_text(separator="\n").strip()
    if len(text) < _MIN_TEXT_CHARS:
        raise ArticleFetchError(f"Readability extract too short ({len(text)} chars)")

    return Article(title=title, byline=None, text=text, url=url)


async def fetch_article(url: str) -> Article:
    """Try Jina Reader first, fall back to httpx + readability-lxml on failure."""
    try:
        article = await _fetch_via_jina(url)
        log.info("Fetched via Jina: %s (%d chars)", url, len(article["text"]))
        return article
    except Exception as exc:  # noqa: BLE001
        log.warning("Jina fetch failed for %s: %s — falling back to readability", url, exc)

    try:
        article = await _fetch_via_readability(url)
        log.info("Fetched via readability: %s (%d chars)", url, len(article["text"]))
        return article
    except Exception as exc:  # noqa: BLE001
        raise ArticleFetchError(f"All extractors failed for {url}: {exc}") from exc
