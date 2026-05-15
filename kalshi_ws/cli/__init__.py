"""Typer CLI entrypoints for the Kalshi workstation."""

from __future__ import annotations

import asyncio

import typer

from kalshi_ws.api.client import KalshiClient
from kalshi_ws.config import get_settings

app = typer.Typer(help="Kalshi workstation commands.", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Force typer to keep subcommands rather than collapse to a single root."""


@app.command()
def hello() -> None:
    """Smoke test: authenticate against Kalshi and print one market."""
    settings = get_settings()

    async def _go() -> None:
        async with KalshiClient(settings) as client:
            payload = await client.get_markets(limit=1)
            markets = payload.get("markets") or []
            if not markets:
                typer.echo("OK: authenticated, but no markets returned.")
                return
            m = markets[0]
            typer.echo(f"{m.get('ticker')} — {m.get('title')}")

    asyncio.run(_go())
