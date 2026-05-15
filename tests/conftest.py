"""pytest fixtures and pytest-recording (vcrpy) configuration.

Recording mode: run with `--record-mode=once` to capture live API responses
into `tests/cassettes/`. Subsequent test runs replay from cassettes.
Spec §6: never hit live API in CI.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def cassette_dir() -> Path:
    return Path(__file__).parent / "cassettes"


@pytest.fixture(scope="session")
def vcr_config() -> dict[str, object]:
    return {
        "filter_headers": [
            ("KALSHI-ACCESS-KEY", "REDACTED"),
            ("KALSHI-ACCESS-TIMESTAMP", "REDACTED"),
            ("KALSHI-ACCESS-SIGNATURE", "REDACTED"),
            ("authorization", "REDACTED"),
        ],
        "match_on": ["method", "scheme", "host", "path", "query"],
        "record_mode": "none",  # default: replay only; override via --record-mode=once
    }


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Belt-and-suspenders: fail loudly if a test accidentally bypasses VCR."""
    import socket

    real_socket = socket.socket

    def guard(*args: object, **kwargs: object) -> socket.socket:
        raise RuntimeError("network access blocked in tests; use a VCR cassette")

    monkeypatch.setattr(socket, "socket", guard)
    yield
    monkeypatch.setattr(socket, "socket", real_socket)
