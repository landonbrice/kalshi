"""Typer CLI entrypoints for the Kalshi workstation."""

from __future__ import annotations

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
