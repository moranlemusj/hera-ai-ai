"""Gemini text embeddings.

Used by the templates module to embed each scraped template's `summary` so the
planner can do hybrid (semantic + popularity + trigram) search later.

We use Gemini's `text-embedding-004` (768-dim) since it shares the bill with
the planner/critic/strategist Gemini calls — one SDK, one key.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING

import httpx
from google import genai
from google.genai import types as genai_types
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)

EMBEDDING_DIM = 768
_CACHE_MAX = 4096
_TRANSIENT_ERRORS = (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
_cache: OrderedDict[str, list[float]] = OrderedDict()
_cache_lock = asyncio.Lock()
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.GOOGLE_API_KEY:
            raise RuntimeError("GOOGLE_API_KEY not set; required for Gemini embeddings.")
        _client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    return _client


async def _cache_get(key: str) -> list[float] | None:
    async with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    return None


async def _cache_put(key: str, value: list[float]) -> None:
    async with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def _validate_vec(vec: list[float]) -> list[float]:
    if len(vec) != EMBEDDING_DIM:
        raise RuntimeError(
            f"Gemini returned a {len(vec)}-dim vector (expected {EMBEDDING_DIM})"
        )
    return vec


async def _embed_request(contents: list[str]) -> list[list[float]]:
    """Single Gemini embed call with transient-error retries.

    Validation errors (wrong dim, missing values) raise immediately — retrying
    a malformed-shape response won't help. Only transport/timeout errors retry.
    """
    client = _get_client()

    last_vectors: list[list[float]] | None = None
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(_TRANSIENT_ERRORS),
        reraise=True,
    ):
        with attempt:
            # google-genai's embed_content is sync; off-thread to keep the loop free.
            resp = await asyncio.to_thread(
                client.models.embed_content,
                model=settings.EMBEDDING_MODEL,
                contents=contents,
                config=genai_types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=EMBEDDING_DIM,
                ),
            )
            embeddings = resp.embeddings or []
            if len(embeddings) != len(contents):
                raise RuntimeError(
                    f"Gemini returned {len(embeddings)} embeddings for "
                    f"{len(contents)} inputs"
                )
            vectors: list[list[float]] = []
            for i, emb in enumerate(embeddings):
                if emb.values is None:
                    raise RuntimeError(f"Embedding #{i} has no values")
                vectors.append(_validate_vec(list(emb.values)))
            last_vectors = vectors

    assert last_vectors is not None  # tenacity reraise=True guarantees we either succeeded or raised
    return last_vectors


async def embed_text(text: str) -> list[float]:
    """Return a 768-dim embedding for one text. Cached by exact string."""
    if not text:
        raise ValueError("Cannot embed empty text")

    cached = await _cache_get(text)
    if cached is not None:
        return cached

    [vec] = await _embed_request([text])
    await _cache_put(text, vec)
    return vec


async def embed_batch(texts: Iterable[str], batch_size: int = 100) -> list[list[float]]:
    """Embed a list of texts in batched API calls. Cache hits short-circuit."""
    text_list = [t for t in texts if t]
    if not text_list:
        return []

    out: list[list[float] | None] = [None] * len(text_list)
    miss_indices: list[int] = []
    miss_texts: list[str] = []

    for i, text in enumerate(text_list):
        cached = await _cache_get(text)
        if cached is not None:
            out[i] = cached
        else:
            miss_indices.append(i)
            miss_texts.append(text)

    for chunk_start in range(0, len(miss_texts), batch_size):
        chunk = miss_texts[chunk_start : chunk_start + batch_size]
        log.info("Embedding batch: %d items", len(chunk))
        vectors = await _embed_request(chunk)
        for offset, vec in enumerate(vectors):
            global_idx = miss_indices[chunk_start + offset]
            out[global_idx] = vec
            await _cache_put(miss_texts[chunk_start + offset], vec)

    if any(v is None for v in out):
        raise RuntimeError("embed_batch produced gaps — should be unreachable")
    return [v for v in out if v is not None]


def cache_size() -> int:
    return len(_cache)
