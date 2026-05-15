"""Minimal async Kalshi API client. Phase 0 scope: one signed read."""

from __future__ import annotations

from types import TracebackType
from typing import Any
from urllib.parse import urlparse

import httpx

from kalshi_ws.api.auth import signed_headers
from kalshi_ws.config import Settings


class KalshiClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> KalshiClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _auth_headers(self, method: str, url: str) -> dict[str, str]:
        return signed_headers(
            api_key_id=self._settings.api_key_id,
            private_key_path=self._settings.private_key_path,
            method=method,
            path=urlparse(url).path,
        )

    async def get_markets(self, *, limit: int = 1) -> dict[str, Any]:
        url = f"{self._settings.base_url}/markets"
        r = await self._client.get(
            url,
            params={"limit": limit},
            headers=self._auth_headers("GET", url),
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data
