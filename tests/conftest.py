"""pytest fixtures.

When a real-API cassette test is needed, re-add `pytest-recording` to dev deps
and define a `vcr_config` fixture here that redacts the Kalshi auth headers.
"""

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def cassette_dir() -> Path:
    return Path(__file__).parent / "cassettes"
