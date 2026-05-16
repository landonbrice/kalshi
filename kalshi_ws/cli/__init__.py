"""Typer CLI entrypoints for the Kalshi workstation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import typer

from kalshi_ws.api.read import KalshiReadClient
from kalshi_ws.cli._errors import friendly_errors
from kalshi_ws.config import get_settings
from kalshi_ws.intel.ev import GateParams
from kalshi_ws.intel.lip_scanner import (
    LipCandidate,
    scan_lip_candidates,
    write_candidates_csv,
)
from kalshi_ws.state.schema import bootstrap

DEFAULT_LIP_CSV = Path("data/lip_candidates.csv")

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


@app.command("scan-lip")
@friendly_errors
def scan_lip(
    category: str = typer.Option(
        None, "--category", "-c", help='Filter to one Kalshi category (e.g. "Entertainment").'
    ),
    top: int = typer.Option(20, "--top", "-n", help="Print top N PLAY candidates."),
    min_ev: float = typer.Option(
        1.0, "--min-ev", help="EV/day floor in dollars. Pass-through to GateParams."
    ),
    min_spread: float = typer.Option(
        0.02, "--min-spread", help="Minimum quotable spread (dollars)."
    ),
    max_capital: float = typer.Option(
        150.0, "--max-capital", help="Per-market capital cap (dollars)."
    ),
    concurrent: int = typer.Option(
        10, "--concurrent", help="Parallel orderbook fetches."
    ),
    out: Path = typer.Option(  # noqa: B008  (typer idiom)
        DEFAULT_LIP_CSV,
        "--out",
        help="CSV output path. Includes both PLAY and PASS rows.",
    ),
) -> None:
    """Rank LIP markets by expected $/day. Writes CSV + prints top N PLAYs."""
    settings = get_settings()
    bootstrap(settings.db_path)
    gate = GateParams(
        min_ev_per_day=min_ev,
        min_spread=min_spread,
        max_capital=max_capital,
    )

    async def _go() -> list[LipCandidate]:
        async with KalshiReadClient(settings) as client:
            return await scan_lip_candidates(
                client,
                category_filter=category,
                gate=gate,
                concurrent_orderbooks=concurrent,
                now=datetime.now(UTC),
            )

    candidates = asyncio.run(_go())
    write_candidates_csv(out, candidates)

    plays = [c for c in candidates if c.ev.play]
    passes = len(candidates) - len(plays)
    typer.echo(
        f"Scanned {len(candidates)} markets that passed cheap gates "
        f"({len(plays)} PLAY, {passes} PASS). CSV -> {out}\n"
    )
    if not plays:
        typer.echo("No PLAY candidates at current thresholds.")
        return

    typer.echo(f"{'#':>3}  {'EV/day':>7}  {'reward':>7}  {'share':>5}  "
               f"{'cap$':>5}  {'days':>4}  {'sprd':>4}  ticker | title")
    for i, c in enumerate(plays[:top], 1):
        typer.echo(
            f"{i:>3}  ${c.ev.ev_per_day:>6.2f}  ${c.ev.reward_per_day:>6.2f}  "
            f"{c.ev.share:>5.2f}  ${c.ev.capital_locked:>4.0f}  "
            f"{c.ev.days_remaining:>4.1f}  {c.ev.spread:>4.2f}  "
            f"{c.market.ticker} | {c.market.title[:60]}"
        )
