"""Shared retry wrapper for transient Gemini server errors.

Gemini occasionally returns 503 UNAVAILABLE / 429 RESOURCE_EXHAUSTED during
demand spikes. The SDK's built-in retry only covers a narrow set of cases,
so we wrap our own call sites with a 5s-sleep retry on the server-side
errors we know are worth retrying.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx
from google.genai import errors as genai_errors
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_fixed,
)

log = logging.getLogger(__name__)

RETRY_SLEEP_SECONDS = 5.0
RETRY_MAX_ATTEMPTS = 3


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.APIError) and getattr(exc, "code", None) == 429:
        return True
    return isinstance(exc, (httpx.HTTPError, ConnectionError, TimeoutError, OSError))


async def gemini_call[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run ``fn(*args, **kwargs)`` off-thread with retry on transient errors.

    Sleeps RETRY_SLEEP_SECONDS between attempts, up to RETRY_MAX_ATTEMPTS.
    Non-retryable errors (4xx other than 429, malformed payloads) raise
    immediately.
    """
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
        wait=wait_fixed(RETRY_SLEEP_SECONDS),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
        before_sleep=lambda rs: log.warning(
            "Gemini transient error (%s); retrying in %.0fs (attempt %d/%d)",
            rs.outcome.exception() if rs.outcome else "?",
            RETRY_SLEEP_SECONDS,
            rs.attempt_number,
            RETRY_MAX_ATTEMPTS,
        ),
    ):
        with attempt:
            return await asyncio.to_thread(fn, *args, **kwargs)
    raise RuntimeError("unreachable")  # pragma: no cover
