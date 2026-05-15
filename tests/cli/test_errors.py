from pathlib import Path

import httpx
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
    combined = result.stderr + result.output
    assert "Kalshi API error" in combined
    assert "401" in combined


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


def test_friendly_errors_sqlite_operational() -> None:
    import sqlite3

    err = sqlite3.OperationalError("unable to open database file")
    app = _make_app(err)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 1
    output = result.stderr + result.output
    assert "database" in output.lower() or "sqlite" in output.lower()
    # No raw traceback — friendly one-liner only.
    assert "Traceback" not in output
