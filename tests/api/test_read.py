import asyncio
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from kalshi_ws.api.read import KalshiReadClient
from kalshi_ws.config import Settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(pem)
    monkeypatch.setenv("KALSHI_API_KEY_ID", "abc-123")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key_path))
    monkeypatch.setenv("KALSHI_BASE_URL", "https://api.test.example/trade-api/v2")
    monkeypatch.setenv("KALSHI_DB_PATH", str(tmp_path / "db.sqlite"))
    return Settings()  # type: ignore[call-arg]


def test_get_markets_signs_and_returns_payload(settings: Settings) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "markets": [{"ticker": "TEST-1", "title": "Test market", "status": "active"}],
                "cursor": None,
            },
        )

    async def run() -> object:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as inner:
            client = KalshiReadClient(settings, client=inner)
            return await client.get_markets(limit=1)

    result = asyncio.run(run())

    from kalshi_ws.api.models import MarketsResponse

    assert isinstance(result, MarketsResponse)
    assert len(result.markets) == 1
    assert result.markets[0].ticker == "TEST-1"
    assert result.markets[0].title == "Test market"
    url = str(captured["url"])
    assert url.startswith("https://api.test.example/trade-api/v2/markets")
    assert "limit=1" in url
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["kalshi-access-key"] == "abc-123"
    assert headers["kalshi-access-timestamp"].isdigit()
    sig = headers["kalshi-access-signature"]
    assert len(sig) == 344
    import base64
    base64.b64decode(sig)


def test_list_incentive_programs_paginates(settings: Settings) -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        if params.get("cursor") == "page-2":
            return httpx.Response(
                200,
                json={
                    "incentive_programs": [
                        {
                            "id": "2",
                            "market_ticker": "KX-B",
                            "incentive_type": "liquidity",
                            "period_reward": 100_000,
                            "target_size_fp": "100",
                            "start_date": "2026-05-15T00:00:00Z",
                            "end_date": "2026-06-15T00:00:00Z",
                        }
                    ],
                    "next_cursor": None,
                },
            )
        return httpx.Response(
            200,
            json={
                "incentive_programs": [
                    {
                        "id": "1",
                        "market_ticker": "KX-A",
                        "incentive_type": "liquidity",
                        "period_reward": 200_000,
                        "target_size_fp": "250",
                        "start_date": "2026-05-15T00:00:00Z",
                        "end_date": "2026-06-15T00:00:00Z",
                    }
                ],
                "next_cursor": "page-2",
            },
        )

    from kalshi_ws.api.models import IncentiveProgram

    async def run() -> list[IncentiveProgram]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as inner:
            client = KalshiReadClient(settings, client=inner)
            return await client.list_incentive_programs()

    programs = asyncio.run(run())
    assert len(programs) == 2
    assert [p.market_ticker for p in programs] == ["KX-A", "KX-B"]
    assert len(calls) == 2
    assert calls[0]["type"] == "liquidity"
    assert calls[0]["status"] == "active"
    assert calls[1]["cursor"] == "page-2"


def test_get_markets_by_tickers_batches_100(settings: Settings) -> None:
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tickers_param = request.url.params.get("tickers", "")
        batch_sizes.append(len(tickers_param.split(",")) if tickers_param else 0)
        markets = [
            {"ticker": t, "title": f"title-{t}", "status": "active"}
            for t in tickers_param.split(",")
        ]
        return httpx.Response(200, json={"markets": markets, "cursor": None})

    from kalshi_ws.api.models import Market

    async def run() -> list[Market]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as inner:
            client = KalshiReadClient(settings, client=inner)
            return await client.get_markets_by_tickers([f"T-{i}" for i in range(250)])

    out = asyncio.run(run())
    assert len(out) == 250
    assert batch_sizes == [100, 100, 50]


def test_get_orderbook_parses_envelope(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/markets/KX-FOO/orderbook")
        return httpx.Response(
            200,
            json={
                "orderbook_fp": {
                    "yes_dollars": [["0.4000", "10"], ["0.4500", "5"]],
                    "no_dollars": [["0.5500", "20"]],
                }
            },
        )

    from kalshi_ws.api.models import OrderbookSnapshot

    async def run() -> OrderbookSnapshot:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as inner:
            client = KalshiReadClient(settings, client=inner)
            return await client.get_orderbook("KX-FOO")

    ob = asyncio.run(run())
    assert ob.top_yes_size() == 5.0
    assert ob.top_no_size() == 20.0
    assert ob.best_yes_price() == 0.45
