# Phase 0 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Kalshi workstation skeleton — package layout, config loading, Kalshi REST auth, one read endpoint, SQLite ledger bootstrap, and a `python -m kalshi_ws hello` smoke test that prints a real market.

**Architecture:** Single Python package `kalshi_ws/` with layered modules per the spec (Section 3 / 7). Phase 0 implements only the foundational slice — `config`, `risk`, `api/`, `state/`, `cli/` — enough to authenticate against Kalshi, read one market, and persist a bootstrapped SQLite ledger. No intel, decision, execution, or dashboard layers in this phase.

**Tech Stack:** Python 3.12+, `httpx`, `pydantic` v2, `pydantic-settings`, `typer`, `cryptography` (for RSA-PSS signing), `pytest`, `pytest-recording` (vcrpy), `ruff`, `mypy --strict`. SQLite via stdlib `sqlite3`.

**Spec reference:** `docs/superpowers/specs/2026-05-14-kalshi-workstation-design.md` — Phase 0 success criterion at §5: `python -m kalshi_ws hello` prints a real market.

---

## File map (Phase 0 only)

Created in this plan:
- `pyproject.toml` — deps, tool configs (ruff, mypy, pytest)
- `.gitignore`, `.env.example`, `README.md`
- `kalshi_ws/__init__.py`, `kalshi_ws/__main__.py`
- `kalshi_ws/config.py` — pydantic-settings loader for `.env`
- `kalshi_ws/risk.py` — hard limits from spec §4
- `kalshi_ws/api/__init__.py`, `kalshi_ws/api/auth.py`, `kalshi_ws/api/read.py`
- `kalshi_ws/state/__init__.py`, `kalshi_ws/state/schema.py`, `kalshi_ws/state/db.py`
- `kalshi_ws/cli/__init__.py`, `kalshi_ws/cli/hello.py`
- `tests/conftest.py`, `tests/api/test_auth.py`, `tests/api/test_read.py`, `tests/state/test_schema.py`, `tests/cli/test_hello.py`
- `tests/cassettes/` (created by pytest-recording; one cassette committed per recorded test)

Out of scope for Phase 0 (deferred to Phase 1+): `kalshi_ws/intel/`, `kalshi_ws/decision/`, `kalshi_ws/execution/`, `kalshi_ws/dashboard/`, `kalshi_ws/api/write.py`, WebSocket client, reconciler.

---

## Task 1: Repo skeleton and tooling

**Files:**
- Create: `/Users/landonbrice/Desktop/kalshi/pyproject.toml`
- Create: `/Users/landonbrice/Desktop/kalshi/.gitignore`
- Create: `/Users/landonbrice/Desktop/kalshi/.env.example`
- Create: `/Users/landonbrice/Desktop/kalshi/README.md`
- Create: `/Users/landonbrice/Desktop/kalshi/kalshi_ws/__init__.py` (empty)
- Create: `/Users/landonbrice/Desktop/kalshi/tests/__init__.py` (empty)

- [ ] **Step 1: Initialize git**

```bash
cd /Users/landonbrice/Desktop/kalshi
git init
git branch -M main
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "kalshi-ws"
version = "0.1.0"
description = "Kalshi trading workstation"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "typer>=0.12",
  "cryptography>=42.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "pytest-recording>=0.13",
  "ruff>=0.5",
  "mypy>=1.10",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["kalshi_ws"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_ignores = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
```

- [ ] **Step 3: Write `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
.env
data/
secrets/
*.db
.mypy_cache/
.ruff_cache/
.pytest_cache/
dist/
*.egg-info/
.DS_Store
```

- [ ] **Step 4: Write `.env.example`**

```dotenv
# Kalshi API credentials — fill in real values in .env (which is gitignored)
KALSHI_API_KEY_ID=your-key-id-here
KALSHI_PRIVATE_KEY_PATH=./secrets/kalshi-private-key.pem
KALSHI_BASE_URL=https://api.elections.kalshi.com/trade-api/v2
# SQLite database file
KALSHI_DB_PATH=./data/kalshi.db
```

- [ ] **Step 5: Write minimal `README.md`**

```markdown
# Kalshi Workstation

Agent-assisted trading workstation for Kalshi. See `docs/superpowers/specs/` for design.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # then fill in real values
```

## Smoke test (Phase 0)

```bash
python -m kalshi_ws hello KXNBA-25MAR03-NBA  # or any valid ticker
```
```

- [ ] **Step 6: Create empty package + tests dirs**

```bash
mkdir -p kalshi_ws kalshi_ws/api kalshi_ws/state kalshi_ws/cli
mkdir -p tests tests/api tests/state tests/cli tests/cassettes
mkdir -p data secrets
touch kalshi_ws/__init__.py
touch kalshi_ws/api/__init__.py
touch kalshi_ws/state/__init__.py
touch kalshi_ws/cli/__init__.py
touch tests/__init__.py
touch tests/api/__init__.py
touch tests/state/__init__.py
touch tests/cli/__init__.py
```

- [ ] **Step 7: Install and verify tooling**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
mypy kalshi_ws
pytest
```

Expected: ruff PASS, mypy PASS (no files yet), pytest reports "no tests ran".

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore .env.example README.md kalshi_ws tests
git commit -m "chore: bootstrap kalshi workstation repo skeleton"
```

---

## Task 2: Config loader

**Files:**
- Create: `kalshi_ws/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

`tests/test_config.py`:
```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from kalshi_ws.config import Settings


def test_settings_loads_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    key_file = tmp_path / "key.pem"
    key_file.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----")
    monkeypatch.setenv("KALSHI_API_KEY_ID", "abc-123")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("KALSHI_BASE_URL", "https://api.example.com/v2")
    monkeypatch.setenv("KALSHI_DB_PATH", str(tmp_path / "test.db"))

    s = Settings()  # type: ignore[call-arg]

    assert s.api_key_id == "abc-123"
    assert s.private_key_path == key_file
    assert s.base_url == "https://api.example.com/v2"
    assert s.db_path == tmp_path / "test.db"


def test_settings_missing_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_config.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'kalshi_ws.config'`.

- [ ] **Step 3: Implement `kalshi_ws/config.py`**

```python
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="KALSHI_",
        extra="ignore",
    )

    api_key_id: str = Field(..., alias="KALSHI_API_KEY_ID")
    private_key_path: Path = Field(..., alias="KALSHI_PRIVATE_KEY_PATH")
    base_url: str = Field(
        default="https://api.elections.kalshi.com/trade-api/v2",
        alias="KALSHI_BASE_URL",
    )
    db_path: Path = Field(default=Path("./data/kalshi.db"), alias="KALSHI_DB_PATH")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_config.py -v
```
Expected: both tests PASS.

- [ ] **Step 5: Type-check**

```bash
mypy kalshi_ws
```
Expected: Success: no issues found.

- [ ] **Step 6: Commit**

```bash
git add kalshi_ws/config.py tests/test_config.py
git commit -m "feat(config): pydantic-settings loader for kalshi credentials"
```

---

## Task 3: Risk limits module

**Files:**
- Create: `kalshi_ws/risk.py`
- Create: `tests/test_risk.py`

This task encodes spec §4 constants as a module that every order path will later import. No order paths exist yet in Phase 0, but the module needs to exist with the right values so Phase 2 can build on it.

- [ ] **Step 1: Write failing test**

`tests/test_risk.py`:
```python
from kalshi_ws import risk


def test_limits_match_spec() -> None:
    # Values from docs/superpowers/specs/2026-05-14-kalshi-workstation-design.md §4
    assert risk.PER_MARKET_MAX_POSITION_USD == 50
    assert risk.SINGLE_ORDER_MAX_USD == 20
    assert risk.DAILY_LOSS_LIMIT_USD == 35
    assert risk.MAX_TOTAL_OPEN_EXPOSURE_USD == 400
    assert risk.MIN_TICKET_SIZE_USD == 1
    assert risk.SANCTIONED_MODE_MAX_DURATION_HOURS == 4
    assert risk.CONSECUTIVE_LOSSES_KILL == 5
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_risk.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'kalshi_ws.risk'`.

- [ ] **Step 3: Implement `kalshi_ws/risk.py`**

```python
"""Hard risk limits. Spec §4. No bypass without editing this file."""

PER_MARKET_MAX_POSITION_USD: int = 50
SINGLE_ORDER_MAX_USD: int = 20
DAILY_LOSS_LIMIT_USD: int = 35
MAX_TOTAL_OPEN_EXPOSURE_USD: int = 400
MIN_TICKET_SIZE_USD: int = 1
SANCTIONED_MODE_MAX_DURATION_HOURS: int = 4
CONSECUTIVE_LOSSES_KILL: int = 5
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_risk.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kalshi_ws/risk.py tests/test_risk.py
git commit -m "feat(risk): hard limit constants from spec section 4"
```

---

## Task 4: Kalshi RSA-PSS signing helper

**Files:**
- Create: `kalshi_ws/api/auth.py`
- Create: `tests/api/test_auth.py`

Kalshi REST auth: each request carries `KALSHI-ACCESS-KEY` (key id), `KALSHI-ACCESS-TIMESTAMP` (ms since epoch), `KALSHI-ACCESS-SIGNATURE` = base64(RSA-PSS-SHA256 signature of `f"{timestamp}{method}{path}"`). Verify against the current Kalshi API reference before shipping; this is the documented scheme as of writing.

- [ ] **Step 1: Write failing test (deterministic — no network)**

`tests/api/test_auth.py`:
```python
import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_ws.api.auth import sign_request, signed_headers


@pytest.fixture(scope="module")
def rsa_keypair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, rsa.RSAPublicKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path_factory.mktemp("keys") / "test_key.pem"
    path.write_bytes(pem)
    return path, key.public_key()


def test_sign_request_produces_verifiable_signature(
    rsa_keypair: tuple[Path, rsa.RSAPublicKey],
) -> None:
    key_path, public_key = rsa_keypair
    timestamp_ms = 1_700_000_000_000
    method = "GET"
    path = "/trade-api/v2/markets/KXNBA-25MAR03-NBA"

    signature_b64 = sign_request(key_path, timestamp_ms, method, path)

    message = f"{timestamp_ms}{method}{path}".encode()
    public_key.verify(
        base64.b64decode(signature_b64),
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )  # raises if invalid


def test_signed_headers_shape(rsa_keypair: tuple[Path, rsa.RSAPublicKey]) -> None:
    key_path, _ = rsa_keypair
    headers = signed_headers(
        api_key_id="abc-123",
        private_key_path=key_path,
        method="GET",
        path="/trade-api/v2/markets/X",
    )
    assert headers["KALSHI-ACCESS-KEY"] == "abc-123"
    assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()
    # RSA-2048 PSS signature is 256 bytes = 344 base64 chars.
    sig = headers["KALSHI-ACCESS-SIGNATURE"]
    assert len(sig) == 344
    base64.b64decode(sig)  # raises if not valid base64
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/api/test_auth.py -v
```
Expected: FAIL — `kalshi_ws.api.auth` not found.

- [ ] **Step 3: Implement `kalshi_ws/api/auth.py`**

```python
"""Kalshi REST auth: RSA-PSS-SHA256 signing.

Header scheme (verify against current Kalshi API docs before production use):
  KALSHI-ACCESS-KEY:       <api key id>
  KALSHI-ACCESS-TIMESTAMP: <unix ms>
  KALSHI-ACCESS-SIGNATURE: base64(RSA-PSS-SHA256(f"{ts}{method}{path}"))

Callers MUST pass the exact uppercase HTTP method that will be sent on the wire.
This helper does not normalize case — the signature is computed over the literal
method string. A mismatch between signed method and sent method will produce
opaque 401 responses from Kalshi.
"""

from __future__ import annotations

import base64
import time
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# NOTE: cache is keyed by Path object, not file content. If the on-disk PEM is
# rotated while a process is running, the old key stays cached until restart.
# Phase 3's sanctioned auto-quoting loop is the first place this matters.
@lru_cache(maxsize=1)
def _load_private_key(path: Path) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError(f"Expected RSA private key at {path}, got {type(key).__name__}")
    return key


def sign_request(private_key_path: Path, timestamp_ms: int, method: str, path: str) -> str:
    """Return base64 RSA-PSS-SHA256 signature of f'{ts}{method}{path}'.

    `method` must be uppercase and match the wire method exactly.
    """
    key = _load_private_key(private_key_path)
    message = f"{timestamp_ms}{method}{path}".encode()
    sig = key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("ascii")


def signed_headers(
    *,
    api_key_id: str,
    private_key_path: Path,
    method: str,
    path: str,
    timestamp_ms: int | None = None,
) -> dict[str, str]:
    ts = timestamp_ms if timestamp_ms is not None else time.time_ns() // 1_000_000
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": str(ts),
        "KALSHI-ACCESS-SIGNATURE": sign_request(private_key_path, ts, method, path),
    }
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/api/test_auth.py -v
```
Expected: both tests PASS.

- [ ] **Step 5: Type-check**

```bash
mypy kalshi_ws
```
Expected: Success.

- [ ] **Step 6: Commit**

```bash
git add kalshi_ws/api/auth.py tests/api/test_auth.py
git commit -m "feat(api): rsa-pss-sha256 request signing per kalshi auth scheme"
```

---

## Task 5: pytest-recording cassette infrastructure

**Files:**
- Create: `tests/conftest.py`

Cassettes record live Kalshi responses once, then replay deterministically. Auth headers (which change per request because of the timestamp) must be excluded from cassette matching, otherwise replay fails on the next run.

**Note on `block_network` fixture:** the initial Task 5 conftest included a `block_network` socket-monkeypatch fixture as belt-and-suspenders against accidental network access. It was removed during integration because it interfered with `asyncio.run()` (which calls `socket.socketpair()` during event loop creation). VCR cassette `record_mode: "none"` is sufficient for tests marked `@pytest.mark.vcr`; tests outside that path use `httpx.MockTransport` for offline determinism.

- [ ] **Step 1: Write `tests/conftest.py`**

```python
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
```

- [ ] **Step 2: Sanity-check pytest still runs**

```bash
pytest -v
```
Expected: existing tests still PASS; no new tests yet.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: pytest-recording config with auth header redaction"
```

---

## Task 6: Kalshi async client — `get_markets` (parallel-branch integration)

**Status:** **Decision made during execution — async client preferred over sync.** A parallel branch produced `kalshi_ws/api/client.py` (async `KalshiClient` using `httpx.AsyncClient`) before this plan started executing. We kept it because async is better positioned for the Phase 3 WebSocket layer.

**Files (already exist on disk, will be committed in this task):**
- Track: `kalshi_ws/api/client.py` (async `KalshiClient` with `get_markets(limit)`)
- Track: `tests/api/test_client.py` (uses `httpx.MockTransport`, no network)

- [ ] **Step 1: Write failing test**

`tests/api/test_read.py`:
```python
from pathlib import Path

import pytest

from kalshi_ws.api.read import KalshiReadClient


@pytest.mark.vcr
def test_get_market_returns_typed_object() -> None:
    """Reads a known stable market. Cassette captured once with --record-mode=once.

    Pick any real ticker that exists at recording time and replace below.
    """
    client = KalshiReadClient(
        base_url="https://api.elections.kalshi.com/trade-api/v2",
        api_key_id="REDACTED",
        private_key_path=Path("./secrets/kalshi-private-key.pem"),
    )
    market = client.get_market("KXNBA-25MAR03-NBA")  # replace with real ticker at record time

    assert market.ticker
    assert market.title
    assert 0 <= market.yes_bid <= 100
    assert 0 <= market.yes_ask <= 100
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/api/test_read.py -v
```
Expected: FAIL — `kalshi_ws.api.read` not found.

- [ ] **Step 3: Implement `kalshi_ws/api/read.py`**

```python
"""Kalshi REST read-only client. No order placement methods live here."""

from __future__ import annotations

from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from kalshi_ws.api.auth import signed_headers


class Market(BaseModel):
    """Subset of fields we use in Phase 0. Extend as later phases need more."""

    ticker: str
    title: str
    status: str
    yes_bid: int = Field(default=0, description="cents")
    yes_ask: int = Field(default=100, description="cents")
    volume: int = 0
    open_interest: int = 0


class KalshiReadClient:
    def __init__(self, base_url: str, api_key_id: str, private_key_path: Path) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key_id = api_key_id
        self._private_key_path = private_key_path
        self._client = httpx.Client(base_url=self._base_url, timeout=10.0)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> KalshiReadClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self, method: str, path: str) -> dict[str, str]:
        return signed_headers(
            api_key_id=self._api_key_id,
            private_key_path=self._private_key_path,
            method=method,
            path=path,
        )

    def get_market(self, ticker: str) -> Market:
        path = f"/trade-api/v2/markets/{ticker}"
        resp = self._client.get(path, headers=self._headers("GET", path))
        resp.raise_for_status()
        payload = resp.json()
        return Market.model_validate(payload["market"])
```

- [ ] **Step 4: Record cassette (one-time, requires real `.env`)**

```bash
# Pick a real, currently-open ticker from Kalshi first (e.g. via their web UI).
# Update the ticker in tests/api/test_read.py to match.
# Then record:
pytest tests/api/test_read.py --record-mode=once -v
```
Expected: test passes, `tests/cassettes/test_read/test_get_market_returns_typed_object.yaml` is created. Inspect the file: the auth headers should appear as `REDACTED`.

- [ ] **Step 5: Replay (no network)**

```bash
pytest tests/api/test_read.py -v
```
Expected: PASS without hitting Kalshi.

- [ ] **Step 6: Type-check**

```bash
mypy kalshi_ws
```
Expected: Success.

- [ ] **Step 7: Commit**

```bash
git add kalshi_ws/api/read.py tests/api/test_read.py tests/cassettes/test_read/test_get_market_returns_typed_object.yaml
git commit -m "feat(api): read-only client with get_market and recorded fixture"
```

---

## Task 7: SQLite schema bootstrap

**Files:**
- Create: `kalshi_ws/state/schema.py`
- Create: `kalshi_ws/state/db.py`
- Create: `tests/state/test_schema.py`

Tables created up front so later phases can `INSERT` without further schema work. This is the union of what Phases 1–4 will need; columns are minimal and additive migrations come later if needed.

- [ ] **Step 1: Write failing test**

`tests/state/test_schema.py`:
```python
import sqlite3
from pathlib import Path

from kalshi_ws.state.db import open_connection
from kalshi_ws.state.schema import bootstrap, EXPECTED_TABLES


def test_bootstrap_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    bootstrap(db_path)

    with open_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = {r[0] for r in rows}
    assert EXPECTED_TABLES.issubset(names), f"missing: {EXPECTED_TABLES - names}"


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    bootstrap(db_path)
    bootstrap(db_path)  # second call must not raise


def test_open_connection_has_foreign_keys_on(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    bootstrap(db_path)
    with open_connection(db_path) as conn:
        (fk_on,) = conn.execute("PRAGMA foreign_keys").fetchone()
    assert fk_on == 1
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/state/test_schema.py -v
```
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement `kalshi_ws/state/db.py`**

```python
"""SQLite connection helper. Enables foreign keys; rest of config lives in schema.py."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path


@contextmanager
def open_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

- [ ] **Step 4: Implement `kalshi_ws/state/schema.py`**

```python
"""SQLite ledger schema. Source of truth for all persisted workstation state."""

from __future__ import annotations

from pathlib import Path

from kalshi_ws.state.db import open_connection

EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "orders",
        "fills",
        "positions",
        "quotes",
        "session_log",
        "params",
        "rebates",
    }
)

_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        side TEXT NOT NULL CHECK (side IN ('yes', 'no')),
        action TEXT NOT NULL CHECK (action IN ('buy', 'sell')),
        price_cents INTEGER NOT NULL,
        qty INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fills (
        fill_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        side TEXT NOT NULL,
        action TEXT NOT NULL,
        price_cents INTEGER NOT NULL,
        qty INTEGER NOT NULL,
        fee_cents INTEGER NOT NULL DEFAULT 0,
        filled_at TEXT NOT NULL,
        FOREIGN KEY (order_id) REFERENCES orders(order_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        ticker TEXT PRIMARY KEY,
        yes_qty INTEGER NOT NULL DEFAULT 0,
        no_qty INTEGER NOT NULL DEFAULT 0,
        avg_yes_cost_cents INTEGER NOT NULL DEFAULT 0,
        avg_no_cost_cents INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quotes (
        ts TEXT NOT NULL,
        ticker TEXT NOT NULL,
        yes_bid INTEGER NOT NULL,
        yes_ask INTEGER NOT NULL,
        volume INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (ts, ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,  -- 'brief' | 'sanctioned_setup' | 'risk_off' | 'note'
        ticker TEXT,
        summary TEXT NOT NULL,
        reasoning TEXT  -- Claude-assisted reasoning text, may be NULL for system events
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS params (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rebates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        source TEXT NOT NULL,  -- 'KMMRP' or other program identifier
        recorded_at TEXT NOT NULL
    )
    """,
)


def bootstrap(db_path: Path) -> None:
    """Create all tables if they don't exist. Idempotent."""
    with open_connection(db_path) as conn:
        for stmt in _DDL:
            conn.execute(stmt)
```

- [ ] **Step 5: Run test to verify pass**

```bash
pytest tests/state/test_schema.py -v
```
Expected: all three tests PASS.

- [ ] **Step 6: Type-check**

```bash
mypy kalshi_ws
```
Expected: Success.

- [ ] **Step 7: Commit**

```bash
git add kalshi_ws/state/db.py kalshi_ws/state/schema.py tests/state/test_schema.py
git commit -m "feat(state): sqlite ledger schema and connection helper"
```

---

## Task 8: `hello` CLI command (smoke test)

**Files:**
- Create: `kalshi_ws/cli/hello.py`
- Modify: `kalshi_ws/cli/__init__.py`
- Create: `kalshi_ws/__main__.py`
- Create: `tests/cli/test_hello.py`

- [ ] **Step 1: Write failing test**

`tests/cli/test_hello.py`:
```python
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from kalshi_ws.api.read import Market
from kalshi_ws.cli import app


def _fake_market() -> Market:
    return Market(
        ticker="KXNBA-25MAR03-NBA",
        title="Will the Lakers win?",
        status="open",
        yes_bid=42,
        yes_ask=45,
        volume=1234,
        open_interest=999,
    )


def test_hello_prints_market(tmp_path: Path) -> None:
    runner = CliRunner()
    fake_key = tmp_path / "key.pem"
    fake_key.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----")

    with patch(
        "kalshi_ws.cli.hello.KalshiReadClient"
    ) as MockClient, patch(
        "kalshi_ws.cli.hello.get_settings"
    ) as mock_settings, patch(
        "kalshi_ws.cli.hello.bootstrap"
    ) as mock_bootstrap:
        instance = MockClient.return_value.__enter__.return_value
        instance.get_market.return_value = _fake_market()
        mock_settings.return_value.api_key_id = "abc"
        mock_settings.return_value.private_key_path = fake_key
        mock_settings.return_value.base_url = "https://api.example.com/v2"
        mock_settings.return_value.db_path = tmp_path / "kalshi.db"

        result = runner.invoke(app, ["hello", "KXNBA-25MAR03-NBA"])

    assert result.exit_code == 0, result.output
    assert "KXNBA-25MAR03-NBA" in result.output
    assert "Will the Lakers win?" in result.output
    assert "42" in result.output  # yes_bid
    assert "45" in result.output  # yes_ask
    mock_bootstrap.assert_called_once()
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/cli/test_hello.py -v
```
Expected: FAIL — `kalshi_ws.cli.app` not found.

- [ ] **Step 3: Implement `kalshi_ws/cli/hello.py`**

```python
"""`python -m kalshi_ws hello TICKER` — Phase 0 smoke test."""

from __future__ import annotations

import typer

from kalshi_ws.api.read import KalshiReadClient
from kalshi_ws.config import get_settings
from kalshi_ws.state.schema import bootstrap


def hello(ticker: str = typer.Argument(..., help="Kalshi market ticker")) -> None:
    """Authenticate, read one market, print it."""
    settings = get_settings()
    bootstrap(settings.db_path)
    with KalshiReadClient(
        base_url=settings.base_url,
        api_key_id=settings.api_key_id,
        private_key_path=settings.private_key_path,
    ) as client:
        market = client.get_market(ticker)
    typer.echo(f"{market.ticker}  {market.title}  status={market.status}")
    typer.echo(f"  yes_bid={market.yes_bid}  yes_ask={market.yes_ask}  vol={market.volume}")
```

- [ ] **Step 4: Wire `kalshi_ws/cli/__init__.py`**

```python
"""Typer CLI app aggregator. Subcommands register here."""

from __future__ import annotations

import typer

from kalshi_ws.cli.hello import hello

app = typer.Typer(no_args_is_help=True)
app.command("hello")(hello)
```

- [ ] **Step 5: Implement `kalshi_ws/__main__.py`**

```python
"""Entry point for `python -m kalshi_ws`."""

from kalshi_ws.cli import app

if __name__ == "__main__":
    app()
```

- [ ] **Step 6: Run test to verify pass**

```bash
pytest tests/cli/test_hello.py -v
```
Expected: PASS.

- [ ] **Step 7: End-to-end smoke (requires real `.env`)**

```bash
python -m kalshi_ws hello <REAL_OPEN_TICKER>
```
Expected: prints two lines — one with ticker/title/status, one with bid/ask/vol — using real data from Kalshi.

- [ ] **Step 8: Run full test suite + type check + lint**

```bash
pytest -v
mypy kalshi_ws
ruff check .
```
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add kalshi_ws/cli kalshi_ws/__main__.py tests/cli/test_hello.py
git commit -m "feat(cli): hello smoke test command — phase 0 success criterion"
```

---

## Task 9: Phase 0 wrap-up — README + CI-safety check

**Files:**
- Modify: `README.md`
- Modify: `.gitignore` (verify cassettes are committed but `.env` is not)

- [ ] **Step 1: Update README with the actual smoke test result**

Append to `README.md`:
```markdown
## Phase 0 complete

- [x] Repo skeleton
- [x] Config loader (`kalshi_ws/config.py`)
- [x] Risk limits (`kalshi_ws/risk.py`)
- [x] RSA-PSS signing (`kalshi_ws/api/auth.py`)
- [x] Read client + cassette (`kalshi_ws/api/read.py`)
- [x] SQLite ledger bootstrap (`kalshi_ws/state/`)
- [x] `python -m kalshi_ws hello TICKER` works against live Kalshi

## Testing

```bash
pytest                                    # replay only
pytest --record-mode=once                 # re-record cassettes (requires real .env)
mypy kalshi_ws
ruff check .
```

Recorded cassettes in `tests/cassettes/` have auth headers redacted; safe to commit.
```

- [ ] **Step 2: Verify `.gitignore` keeps secrets out, cassettes in**

```bash
git check-ignore .env data/kalshi.db secrets/kalshi-private-key.pem
git check-ignore tests/cassettes/test_read/test_get_market.yaml || echo "cassette not ignored — good"
```
Expected: `.env`, `data/kalshi.db`, `secrets/kalshi-private-key.pem` are ignored; cassette is NOT ignored.

- [ ] **Step 3: Inspect a cassette and confirm no secrets leaked**

```bash
grep -E "KALSHI-ACCESS-(KEY|TIMESTAMP|SIGNATURE)" tests/cassettes/test_read/test_get_market_returns_typed_object.yaml
```
Expected: every match shows `REDACTED` (not a real key id or signature).

- [ ] **Step 4: Final commit**

```bash
git add README.md
git commit -m "docs: phase 0 complete"
```

---

## Phase 0 done — success criteria (from spec §10 / §5)

- [ ] `python -m kalshi_ws hello <TICKER>` prints real Kalshi market data
- [ ] `pytest` passes with cassettes replaying (no network)
- [ ] `mypy --strict kalshi_ws` passes
- [ ] `ruff check .` passes
- [ ] `.env` and private key file are gitignored; cassettes contain no secrets
- [ ] SQLite ledger is created at `data/kalshi.db` with all expected tables

**Next:** brainstorm scope adjustments (if any) from what was learned, then write the Phase 1 plan (read-only intel layer: scanners + brief + positions reconciler).
