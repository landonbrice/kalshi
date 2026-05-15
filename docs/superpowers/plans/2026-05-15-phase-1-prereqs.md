# Phase 1 Prerequisites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four foundation gaps the Phase 0 final reviewer surfaced before Phase 1's scanners land: enforce the read/write split, introduce typed pydantic API models, add a friendly CLI error wrapper, and decide cassette policy.

**Architecture:** No new layers — these are refactors and small additions inside the existing `api/`, `cli/`, and test layout. The scope is *exactly* what's needed to keep `dict[str, Any]` out of `intel/` (Phase 1) and to give every CLI command consistent error UX. Anything beyond that defers to the per-scanner plan.

**Tech Stack:** Same as Phase 0 — Python 3.12+, `httpx`, `pydantic` v2, `typer`, `cryptography`, `pytest`. **Removing** `pytest-recording` from deps (re-add when first cassette test arrives).

**Spec reference:** `docs/superpowers/specs/2026-05-14-kalshi-workstation-design.md` §3.1 (read/write split), §6 (typed pydantic models).
**Prior plan:** `docs/superpowers/plans/2026-05-14-phase-0-foundation.md` (Phase 0).
**Final-review notes that drove this plan:** four items in the Phase 0 final code review — read/write split, pydantic models, cassette policy, error wrapper.

---

## File map

Renames (move + rename, not new files):
- `kalshi_ws/api/client.py` → `kalshi_ws/api/read.py`
- `tests/api/test_client.py` → `tests/api/test_read.py`

Created:
- `kalshi_ws/api/models.py` — pydantic `Market` and `MarketsResponse` models
- `kalshi_ws/cli/_errors.py` — `friendly_errors` decorator/context manager
- `tests/api/test_models.py` — model parsing tests
- `tests/cli/test_errors.py` — error wrapper behavior tests

Modified:
- `kalshi_ws/cli/__init__.py` — import from new path; use typed model; wrap with friendly_errors
- `tests/cli/test_hello.py` — update patch targets after rename
- `pyproject.toml` — drop `pytest-recording` from `[project.optional-dependencies].dev`
- `tests/conftest.py` — remove `vcr_config` fixture (keep `cassette_dir` for future)

Out of scope (deferred to per-scanner plans): scanner code, brief, reconciler, cassette recording.

---

## Task 1: Rename `client.py` → `read.py` and class → `KalshiReadClient`

**Files:**
- Rename: `kalshi_ws/api/client.py` → `kalshi_ws/api/read.py`
- Rename: `tests/api/test_client.py` → `tests/api/test_read.py`
- Modify: `kalshi_ws/cli/__init__.py` (import path + class name)
- Modify: `tests/cli/test_hello.py` (patch target updates)

This is a mechanical rename to enforce the spec §3.1 read/write split *structurally* before Phase 2 adds write methods. The async behavior is unchanged.

- [ ] **Step 1: Rename files via git**

```bash
cd /Users/landonbrice/Desktop/kalshi
git mv kalshi_ws/api/client.py kalshi_ws/api/read.py
git mv tests/api/test_client.py tests/api/test_read.py
```

- [ ] **Step 2: Rename the class inside `kalshi_ws/api/read.py`**

Replace the docstring line and every occurrence of `KalshiClient` with `KalshiReadClient`. The full file becomes:

```python
"""Minimal async Kalshi REST client — read methods only.

Read/write split per spec §3.1: this module must NOT grow order-placement
methods. Phase 2 will introduce `kalshi_ws/api/write.py` for those.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any
from urllib.parse import urlparse

import httpx

from kalshi_ws.api.auth import signed_headers
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
```

(Return type stays `dict[str, Any]` for this task — Task 3 narrows it to a typed model.)

- [ ] **Step 3: Update `tests/api/test_read.py`**

Replace every occurrence of `KalshiClient` with `KalshiReadClient` and update the import line:

```python
from kalshi_ws.api.read import KalshiReadClient
```

(Test function names stay the same: `test_get_markets_signs_and_returns_payload` is still valid.)

- [ ] **Step 4: Update `kalshi_ws/cli/__init__.py`**

Replace these two lines:
```python
from kalshi_ws.api.client import KalshiClient
```
and
```python
        async with KalshiClient(settings) as client:
```
with:
```python
from kalshi_ws.api.read import KalshiReadClient
```
and
```python
        async with KalshiReadClient(settings) as client:
```

- [ ] **Step 5: Update `tests/cli/test_hello.py` patch targets**

Replace:
```python
        "kalshi_ws.cli.KalshiClient", return_value=fake_client_cm
```
with:
```python
        "kalshi_ws.cli.KalshiReadClient", return_value=fake_client_cm
```

- [ ] **Step 6: Run full test suite + type check + lint**

```bash
source .venv/bin/activate
pytest -v
mypy kalshi_ws && mypy tests
ruff check .
```

Expected: 10 tests pass (counts unchanged from Phase 0); mypy + ruff clean.

- [ ] **Step 7: Commit**

```bash
git add kalshi_ws/api/read.py kalshi_ws/cli/__init__.py tests/api/test_read.py tests/cli/test_hello.py
git commit -m "refactor(api): rename KalshiClient -> KalshiReadClient, move to read.py"
```

---

## Task 2: Pydantic `Market` and `MarketsResponse` models

**Files:**
- Create: `kalshi_ws/api/models.py`
- Create: `tests/api/test_models.py`

Kalshi's `/markets` endpoint returns a JSON object with a `markets` list and a paginated `cursor`. Each market has many fields; we model only what scanners and the CLI need today, with `extra="allow"` so additional fields don't break parsing.

- [ ] **Step 1: Write failing test**

`tests/api/test_models.py`:

```python
import pytest
from pydantic import ValidationError

from kalshi_ws.api.models import Market, MarketsResponse


def test_market_parses_minimal_payload() -> None:
    payload = {
        "ticker": "KX-FOO",
        "title": "Foo market",
        "status": "active",
        "yes_bid": 42,
        "yes_ask": 45,
        "volume": 1000,
        "open_interest": 250,
    }
    m = Market.model_validate(payload)
    assert m.ticker == "KX-FOO"
    assert m.title == "Foo market"
    assert m.status == "active"
    assert m.yes_bid == 42
    assert m.yes_ask == 45
    assert m.volume == 1000
    assert m.open_interest == 250


def test_market_tolerates_extra_fields() -> None:
    payload = {
        "ticker": "KX-FOO",
        "title": "Foo",
        "status": "active",
        "yes_bid": 0,
        "yes_ask": 100,
        "volume": 0,
        "open_interest": 0,
        "made_up_field": "ignored",
        "another_extra": {"nested": 1},
    }
    m = Market.model_validate(payload)
    assert m.ticker == "KX-FOO"


def test_market_defaults_missing_optional_numeric_fields() -> None:
    """Some markets have no bids/asks. yes_bid=0 / yes_ask=100 are sensible defaults."""
    payload = {
        "ticker": "KX-FOO",
        "title": "Foo",
        "status": "active",
    }
    m = Market.model_validate(payload)
    assert m.yes_bid == 0
    assert m.yes_ask == 100
    assert m.volume == 0
    assert m.open_interest == 0


def test_market_missing_required_raises() -> None:
    with pytest.raises(ValidationError):
        Market.model_validate({"title": "no ticker"})


def test_markets_response_parses_list_and_cursor() -> None:
    payload = {
        "markets": [
            {"ticker": "A", "title": "A title", "status": "active"},
            {"ticker": "B", "title": "B title", "status": "closed"},
        ],
        "cursor": "next-page-token",
    }
    resp = MarketsResponse.model_validate(payload)
    assert len(resp.markets) == 2
    assert resp.markets[0].ticker == "A"
    assert resp.cursor == "next-page-token"


def test_markets_response_cursor_optional() -> None:
    """The last page may omit cursor."""
    payload = {"markets": []}
    resp = MarketsResponse.model_validate(payload)
    assert resp.markets == []
    assert resp.cursor is None
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/api/test_models.py -v
```
Expected: FAIL — `kalshi_ws.api.models` not found.

- [ ] **Step 3: Implement `kalshi_ws/api/models.py`**

```python
"""Typed pydantic models for Kalshi REST responses.

Models use `extra="allow"` so Kalshi adding fields never breaks parsing.
Scanners and decision-support code consume these models, never raw JSON dicts.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Market(BaseModel):
    """Subset of fields used by Phase 1 scanners. Extend as needs grow."""

    model_config = ConfigDict(extra="allow")

    ticker: str
    title: str
    status: str
    yes_bid: int = Field(default=0, description="Best YES bid in cents (0 if none).")
    yes_ask: int = Field(default=100, description="Best YES ask in cents (100 if none).")
    volume: int = 0
    open_interest: int = 0


class MarketsResponse(BaseModel):
    """Envelope returned by GET /markets."""

    model_config = ConfigDict(extra="allow")

    markets: list[Market]
    cursor: str | None = None
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/api/test_models.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Type-check + lint**

```bash
mypy kalshi_ws
ruff check kalshi_ws/api/models.py tests/api/test_models.py
```
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add kalshi_ws/api/models.py tests/api/test_models.py
git commit -m "feat(api): pydantic Market and MarketsResponse models"
```

---

## Task 3: Type `KalshiReadClient.get_markets` to return `MarketsResponse`

**Files:**
- Modify: `kalshi_ws/api/read.py`
- Modify: `tests/api/test_read.py`
- Modify: `kalshi_ws/cli/__init__.py`
- Modify: `tests/cli/test_hello.py`

- [ ] **Step 1: Update `tests/api/test_read.py` to assert typed return**

Replace the body of `test_get_markets_signs_and_returns_payload` with:

```python
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
```

(Imports at the top of the file: keep `KalshiReadClient`, add `MarketsResponse` if you prefer hoisted — the inline import inside the test is fine for clarity.)

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/api/test_read.py -v
```
Expected: FAIL — `isinstance(result, MarketsResponse)` is False (still a dict).

- [ ] **Step 3: Update `kalshi_ws/api/read.py`**

Change the `get_markets` method signature and body:

```python
    async def get_markets(self, *, limit: int = 1) -> MarketsResponse:
        url = f"{self._settings.base_url}/markets"
        r = await self._client.get(
            url,
            params={"limit": limit},
            headers=self._auth_headers("GET", url),
        )
        r.raise_for_status()
        return MarketsResponse.model_validate(r.json())
```

And add the import at the top:

```python
from kalshi_ws.api.models import MarketsResponse
```

Remove the now-unused `from typing import Any` import.

- [ ] **Step 4: Update `kalshi_ws/cli/__init__.py` to use the typed model**

Replace the body of `_go()` with:

```python
    async def _go() -> None:
        async with KalshiReadClient(settings) as client:
            resp = await client.get_markets(limit=1)
            if not resp.markets:
                typer.echo("OK: authenticated, but no markets returned.")
                return
            m = resp.markets[0]
            typer.echo(f"{m.ticker} — {m.title}")
```

- [ ] **Step 5: Update `tests/cli/test_hello.py` mock**

The mock currently returns a dict. Change `get_markets`'s return value to a `MarketsResponse`:

```python
    from kalshi_ws.api.models import Market, MarketsResponse

    fake_client_instance = MagicMock()
    fake_client_instance.get_markets = AsyncMock(
        return_value=MarketsResponse(
            markets=[Market(ticker="TEST-XYZ", title="Test market", status="active")],
        )
    )
```

- [ ] **Step 6: Run full test suite + type-check + lint**

```bash
pytest -v
mypy kalshi_ws && mypy tests
ruff check .
```
Expected: 16 tests pass (10 prior + 6 new); mypy + ruff clean.

- [ ] **Step 7: Commit**

```bash
git add kalshi_ws/api/read.py kalshi_ws/cli/__init__.py tests/api/test_read.py tests/cli/test_hello.py
git commit -m "refactor(api): get_markets returns typed MarketsResponse"
```

---

## Task 4: CLI friendly-error wrapper

**Files:**
- Create: `kalshi_ws/cli/_errors.py`
- Create: `tests/cli/test_errors.py`

Phase 0's `hello` surfaces raw `httpx.HTTPStatusError` / `FileNotFoundError` / `ValidationError` tracebacks for auth failures, missing keys, or bad JSON. Every CLI command in Phase 1 (`scan`, `brief`, `positions`) will need the same wrapping. Build it once.

- [ ] **Step 1: Write failing test**

`tests/cli/test_errors.py`:

```python
from pathlib import Path

import httpx
import pytest
import typer
from pydantic import ValidationError
from typer.testing import CliRunner

from kalshi_ws.cli._errors import friendly_errors


def _make_app(raises: Exception) -> typer.Typer:
    app = typer.Typer()

    @app.command()
    @friendly_errors
    def cmd() -> None:
        raise raises

    return app


def test_friendly_errors_passthrough_on_success() -> None:
    app = typer.Typer()

    @app.command()
    @friendly_errors
    def ok() -> None:
        typer.echo("hello")

    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0
    assert "hello" in result.output


def test_friendly_errors_http_status() -> None:
    request = httpx.Request("GET", "https://x/y")
    response = httpx.Response(401, request=request, json={"error": "bad auth"})
    err = httpx.HTTPStatusError("401", request=request, response=response)
    app = _make_app(err)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 1
    assert "Kalshi API error" in result.stderr or "Kalshi API error" in result.output
    assert "401" in result.stderr or "401" in result.output


def test_friendly_errors_missing_private_key(tmp_path: Path) -> None:
    err = FileNotFoundError(2, "No such file or directory", str(tmp_path / "missing.pem"))
    app = _make_app(err)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 1
    output = result.stderr + result.output
    assert "private key" in output.lower() or "file not found" in output.lower()
    assert "missing.pem" in output


def test_friendly_errors_validation() -> None:
    try:
        from pydantic import BaseModel

        class M(BaseModel):
            x: int

        M.model_validate({"x": "not-an-int"})
    except ValidationError as e:
        err = e
    app = _make_app(err)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 1
    output = result.stderr + result.output
    assert "invalid" in output.lower() or "validation" in output.lower()


def test_friendly_errors_unknown_exception_reraises() -> None:
    """Unfamiliar exceptions bubble up so unknown bugs aren't silently swallowed."""

    class WeirdError(Exception):
        pass

    app = _make_app(WeirdError("boom"))
    result = CliRunner().invoke(app, [])
    # Either the runner captures it (exit_code != 0) or it raises; both are acceptable
    # as long as we DON'T quietly print a friendly message and exit 0.
    assert result.exit_code != 0
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/cli/test_errors.py -v
```
Expected: FAIL — `kalshi_ws.cli._errors` not found.

- [ ] **Step 3: Implement `kalshi_ws/cli/_errors.py`**

```python
"""CLI error-handling decorator.

Wraps a typer command so common failures (auth, network, missing key, bad
response shape) surface as one-line messages on stderr with exit code 1
instead of raw tracebacks.

Unknown exceptions are re-raised so genuine bugs are not silently hidden.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TypeVar

import httpx
import typer
from pydantic import ValidationError

F = TypeVar("F", bound=Callable[..., None])


def friendly_errors(func: F) -> F:
    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> None:
        try:
            func(*args, **kwargs)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            typer.echo(f"Kalshi API error: HTTP {status} from {e.request.url}", err=True)
            raise typer.Exit(1) from e
        except httpx.HTTPError as e:
            typer.echo(f"Network error talking to Kalshi: {e}", err=True)
            raise typer.Exit(1) from e
        except FileNotFoundError as e:
            path = e.filename or "(unknown path)"
            typer.echo(
                f"File not found: {path}. Check KALSHI_PRIVATE_KEY_PATH in your .env.",
                err=True,
            )
            raise typer.Exit(1) from e
        except ValidationError as e:
            typer.echo(f"Invalid data from Kalshi (validation error): {e}", err=True)
            raise typer.Exit(1) from e

    return wrapper  # type: ignore[return-value]
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/cli/test_errors.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Type-check + lint**

```bash
mypy kalshi_ws
ruff check kalshi_ws/cli/_errors.py tests/cli/test_errors.py
```
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add kalshi_ws/cli/_errors.py tests/cli/test_errors.py
git commit -m "feat(cli): friendly_errors decorator for command error UX"
```

---

## Task 5: Apply `friendly_errors` to `hello`

**Files:**
- Modify: `kalshi_ws/cli/__init__.py`

- [ ] **Step 1: Decorate the `hello` command**

Update `kalshi_ws/cli/__init__.py` so the `hello` command is wrapped. The relevant region after the change:

```python
import asyncio

import typer

from kalshi_ws.api.read import KalshiReadClient
from kalshi_ws.cli._errors import friendly_errors
from kalshi_ws.config import get_settings
from kalshi_ws.state.schema import bootstrap

app = typer.Typer(help="Kalshi workstation commands.", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Force typer to keep subcommands rather than collapse to a single root."""


@app.command()
@friendly_errors
def hello() -> None:
    """Smoke test: authenticate against Kalshi and print one market."""
    settings = get_settings()
    bootstrap(settings.db_path)

    async def _go() -> None:
        async with KalshiReadClient(settings) as client:
            resp = await client.get_markets(limit=1)
            if not resp.markets:
                typer.echo("OK: authenticated, but no markets returned.")
                return
            m = resp.markets[0]
            typer.echo(f"{m.ticker} — {m.title}")

    asyncio.run(_go())
```

- [ ] **Step 2: Run full test suite**

```bash
pytest -v
mypy kalshi_ws && mypy tests
ruff check .
```
Expected: 21 tests pass total (16 prior + 5 new in test_errors); clean tooling.

- [ ] **Step 3: Live smoke test still works**

```bash
python -m kalshi_ws hello
```
Expected: prints a real Kalshi market (unchanged behavior on the happy path).

- [ ] **Step 4: Optional negative-path smoke (do not commit)**

To eyeball the friendly-error path manually, temporarily point at a bogus key path:

```bash
KALSHI_PRIVATE_KEY_PATH=/tmp/does-not-exist.pem python -m kalshi_ws hello
```
Expected: one-line "File not found: ..." on stderr, exit code 1, NO traceback.

Restore as before (no .env change needed since this was via env override).

- [ ] **Step 5: Commit**

```bash
git add kalshi_ws/cli/__init__.py
git commit -m "feat(cli): apply friendly_errors to hello"
```

---

## Task 6: Drop unused `pytest-recording` and clean conftest

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/conftest.py`
- Modify: `README.md`

Reviewer's note: `pytest-recording` is wired but no test uses `@pytest.mark.vcr` and `tests/cassettes/` is empty. Cassettes will return when the first real-API test needs replay. Until then, drop the dep and the dead `vcr_config` fixture.

- [ ] **Step 1: Remove `pytest-recording` from `pyproject.toml`**

In `[project.optional-dependencies].dev`, delete the line:

```toml
  "pytest-recording>=0.13",
```

The dev section becomes:

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "ruff>=0.5",
  "mypy>=1.10",
]
```

- [ ] **Step 2: Trim `tests/conftest.py`**

Replace the entire file with:

```python
"""pytest fixtures.

When a real-API cassette test is needed, re-add `pytest-recording` to dev deps
and define a `vcr_config` fixture here that redacts the Kalshi auth headers.
"""

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def cassette_dir() -> Path:
    return Path(__file__).parent / "cassettes"
```

- [ ] **Step 3: Reinstall the project to drop the package**

```bash
source .venv/bin/activate
pip uninstall -y pytest-recording vcrpy
pip install -e ".[dev]"
```

Expected: `pytest-recording` and `vcrpy` are no longer installed.

- [ ] **Step 4: Update README**

In `README.md`, replace this block:

```markdown
## Testing

```bash
pytest                                    # offline (MockTransport + future cassettes)
pytest --record-mode=once                 # re-record VCR cassettes (requires real .env)
mypy kalshi_ws && mypy tests
ruff check .
```

Recorded cassettes in `tests/cassettes/` (none yet in Phase 0) redact auth headers — safe to commit.
```

with:

```markdown
## Testing

```bash
pytest                                    # offline (httpx.MockTransport)
mypy kalshi_ws && mypy tests
ruff check .
```

Cassette infra was removed in Phase 1 prereqs. It will return when the first test needs to replay a real Kalshi response.
```

- [ ] **Step 5: Verify everything still passes**

```bash
pytest -v
mypy kalshi_ws && mypy tests
ruff check .
```
Expected: 21 tests pass; mypy + ruff clean. (No test depended on `pytest-recording` after Task 4.)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/conftest.py README.md
git commit -m "chore(test): drop unused pytest-recording until first cassette test"
```

---

## Phase 1 prereqs done — success criteria

- [ ] `kalshi_ws/api/read.py` holds the read-only client; `kalshi_ws/api/write.py` does not exist yet (reserved for Phase 2)
- [ ] `kalshi_ws/api/models.py` exports `Market` and `MarketsResponse`; both used by the read client and the CLI
- [ ] `kalshi_ws/cli/_errors.py` exists; `hello` is decorated; manual negative-path test shows a friendly one-line message
- [ ] `pytest-recording` removed from dev deps and conftest; tests still all pass
- [ ] 21 tests pass total
- [ ] `python -m kalshi_ws hello` still prints a real Kalshi market
- [ ] mypy strict + ruff clean across `kalshi_ws` and `tests`

**Next:** brainstorm and plan the **weather scanner** (Phase 1 first deliverable). Phase 1 spec criteria (§10): "scanners reliably surface 3+ actionable opportunities per session." The scanner plan will build on the typed `Market` model and the friendly_errors wrapper introduced here.
