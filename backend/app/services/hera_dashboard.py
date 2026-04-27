"""HTTP client for the Hera dashboard API (cookie-auth).

Distinct from `hera_api.py` (the public REST API at api.hera.video, which uses
`x-api-key`). The dashboard endpoints are the unofficial ones that power
app.hera.video — currently we only use `/api/templates` for the scrape.

The httpx client is module-level and lifecycle-managed by `main.lifespan` so
TLS/keep-alive is reused across the ~50–200 requests of a full scrape.
"""

from __future__ import annotations

import logging

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.services.hera_session import HeraSessionExpiredError, require_session

log = logging.getLogger(__name__)

DASHBOARD_BASE = "https://app.hera.video"
TEMPLATES_PATH = "/api/templates"
DEFAULT_TIMEOUT = 20.0

_client: httpx.AsyncClient | None = None


class HeraDashboardError(Exception):
    """Non-401 dashboard failures (5xx, malformed response, network)."""


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=DASHBOARD_BASE, timeout=DEFAULT_TIMEOUT)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def fetch_templates_page(
    category: str,
    page: int,
    page_size: int = 24,
    public: bool = True,
) -> list[dict]:
    """Fetch one page of templates for a single category.

    Returns the `data` array. Raises:
    - `HeraSessionExpiredError` on 401 (so the caller can trigger an interrupt).
    - `HeraDashboardError` on any other unexpected response.
    """
    cookies = await require_session()
    client = await get_client()
    params = {
        "page": page,
        "pageSize": page_size,
        "public": "true" if public else "false",
        "category": category,
    }

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(httpx.TransportError),
        reraise=True,
    ):
        with attempt:
            resp = await client.get(TEMPLATES_PATH, params=params, cookies=cookies)

    if resp.status_code == 401:
        raise HeraSessionExpiredError("Hera dashboard returned 401 — session expired.")
    if resp.status_code != 200:
        raise HeraDashboardError(
            f"Hera dashboard {resp.status_code} on {TEMPLATES_PATH}: {resp.text[:200]}"
        )

    try:
        body = resp.json()
    except ValueError as exc:
        raise HeraDashboardError(f"Non-JSON response from Hera: {exc}") from exc

    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise HeraDashboardError("Unexpected response shape: missing `data` array")

    return data
