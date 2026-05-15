from pathlib import Path

import pytest

from kalshi_ws.config import Settings


def test_settings_loads_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    key_file = tmp_path / "key.pem"
    key_file.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----")
    monkeypatch.setenv("KALSHI_API_KEY_ID", "abc-123")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("KALSHI_BASE_URL", "https://api.example.com/v2")
    monkeypatch.setenv("KALSHI_DB_PATH", str(tmp_path / "test.db"))

    s = Settings()

    assert s.api_key_id == "abc-123"
    assert s.private_key_path == key_file
    assert s.base_url == "https://api.example.com/v2"
    assert s.db_path == tmp_path / "test.db"


def test_settings_missing_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    with pytest.raises(Exception):
        Settings(_env_file=None)  # type: ignore[call-arg]
