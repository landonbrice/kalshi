"""CLI error-handling decorator.

Wraps a typer command so common failures (auth, network, missing key, bad
response shape) surface as one-line messages on stderr with exit code 1
instead of raw tracebacks.

Unknown exceptions are re-raised so genuine bugs are not silently hidden.
"""

from __future__ import annotations

import functools
from collections.abc import Callable

import httpx
import typer
from pydantic import ValidationError


def friendly_errors[F: Callable[..., None]](func: F) -> F:
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
