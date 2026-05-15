from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from kalshi_ws.cli import app


def test_hello_bootstraps_db_and_prints_market(tmp_path: Path) -> None:
    fake_key = tmp_path / "key.pem"
    fake_key.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----")
    fake_db = tmp_path / "kalshi.db"

    fake_settings = MagicMock()
    fake_settings.api_key_id = "abc"
    fake_settings.private_key_path = fake_key
    fake_settings.base_url = "https://api.example.com/v2"
    fake_settings.db_path = fake_db

    from kalshi_ws.api.models import Market, MarketsResponse

    fake_client_instance = MagicMock()
    fake_client_instance.get_markets = AsyncMock(
        return_value=MarketsResponse(
            markets=[Market(ticker="TEST-XYZ", title="Test market", status="active")],
        )
    )
    fake_client_cm = AsyncMock()
    fake_client_cm.__aenter__.return_value = fake_client_instance
    fake_client_cm.__aexit__.return_value = None

    with patch("kalshi_ws.cli.get_settings", return_value=fake_settings), patch(
        "kalshi_ws.cli.KalshiReadClient", return_value=fake_client_cm
    ), patch("kalshi_ws.cli.bootstrap") as mock_bootstrap:
        runner = CliRunner()
        result = runner.invoke(app, ["hello"])

    assert result.exit_code == 0, result.output
    mock_bootstrap.assert_called_once_with(fake_db)
    assert "TEST-XYZ" in result.output
    assert "Test market" in result.output
