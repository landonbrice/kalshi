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
