"""Minimal async Kalshi REST client — read methods only.

Read/write split per spec §3.1: this module must NOT grow order-placement
methods. Phase 2 will introduce `kalshi_ws/api/write.py` for those.
"""

from __future__ import annotations

from types import TracebackType
from urllib.parse import urlparse

import httpx

from kalshi_ws.api.auth import signed_headers
from kalshi_ws.api.models import (
    IncentiveProgram,
    IncentiveProgramsResponse,
    Market,
    MarketsResponse,
    OrderbookSnapshot,
)
from kalshi_ws.config import Settings


class KalshiReadClient:
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

    async def __aenter__(self) -> KalshiReadClient:
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

    async def get_markets(
        self,
        *,
        limit: int = 1,
        tickers: list[str] | None = None,
        status: str | None = None,
        cursor: str | None = None,
    ) -> MarketsResponse:
        url = f"{self._settings.base_url}/markets"
        params: dict[str, str | int] = {"limit": limit}
        if tickers:
            params["tickers"] = ",".join(tickers)
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        r = await self._client.get(
            url, params=params, headers=self._auth_headers("GET", url)
        )
        r.raise_for_status()
        return MarketsResponse.model_validate(r.json())

    async def get_markets_by_tickers(self, tickers: list[str]) -> list[Market]:
        """Batch-fetch markets in chunks of 100. Returns merged Market list."""
        out: list[Market] = []
        for i in range(0, len(tickers), 100):
            resp = await self.get_markets(tickers=tickers[i : i + 100], limit=1000)
            out.extend(resp.markets)
        return out

    async def list_incentive_programs(
        self,
        *,
        incentive_type: str = "liquidity",
        status: str = "active",
    ) -> list[IncentiveProgram]:
        """Page through every LIP/VIP program matching the filter."""
        out: list[IncentiveProgram] = []
        cursor: str | None = None
        while True:
            url = f"{self._settings.base_url}/incentive_programs"
            params: dict[str, str | int] = {
                "type": incentive_type,
                "status": status,
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            r = await self._client.get(
                url, params=params, headers=self._auth_headers("GET", url)
            )
            r.raise_for_status()
            page = IncentiveProgramsResponse.model_validate(r.json())
            out.extend(page.incentive_programs)
            cursor = page.next_cursor or None
            if not cursor:
                break
        return out

    async def get_orderbook(self, ticker: str, *, depth: int = 10) -> OrderbookSnapshot:
        url = f"{self._settings.base_url}/markets/{ticker}/orderbook"
        r = await self._client.get(
            url, params={"depth": depth}, headers=self._auth_headers("GET", url)
        )
        r.raise_for_status()
        return OrderbookSnapshot.model_validate(r.json())
