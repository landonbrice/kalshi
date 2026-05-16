"""Typer CLI entrypoints for the Kalshi workstation."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
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


def _write_meta(
    path: Path,
    *,
    loop_started: datetime,
    scan_started: datetime,
    iteration: int,
    total: int,
    plays: int,
    category: str | None,
    duration_seconds: float,
) -> None:
    """Sidecar JSON for the dashboard's freshness widget. Atomic write."""
    meta = {
        "loop_started_at": loop_started.isoformat(),
        "scan_started_at": scan_started.isoformat(),
        "scan_duration_seconds": round(duration_seconds, 2),
        "iteration": iteration,
        "candidates_total": total,
        "candidates_play": plays,
        "candidates_pass": total - plays,
        "category_filter": category,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2))
    tmp.replace(path)


@app.command("scan-lip-loop")
@friendly_errors
def scan_lip_loop(
    interval: int = typer.Option(900, "--interval", help="Seconds between scans (default 15min)."),
    duration_hours: float = typer.Option(
        0.0, "--duration-hours", help="Hard stop after N hours (0 = no limit)."
    ),
    max_iterations: int = typer.Option(
        0, "--max-iterations", help="Hard stop after N iterations (0 = no limit)."
    ),
    category: str = typer.Option(None, "--category", "-c", help="Optional category filter."),
    min_ev: float = typer.Option(1.0, "--min-ev", help="EV/day floor in dollars."),
    min_spread: float = typer.Option(0.02, "--min-spread", help="Minimum quotable spread."),
    max_capital: float = typer.Option(150.0, "--max-capital", help="Per-market capital cap."),
    concurrent: int = typer.Option(10, "--concurrent", help="Parallel orderbook fetches."),
    out: Path = typer.Option(  # noqa: B008  (typer idiom)
        DEFAULT_LIP_CSV, "--out", help="CSV output path; sidecar .meta.json next to it."
    ),
) -> None:
    """Refresh the LIP candidates CSV every --interval seconds until stopped.

    Per-iteration errors are logged but don't crash the loop -- next interval
    will retry. Ctrl-C exits cleanly. Safety bounds: --duration-hours and
    --max-iterations are enforced after each iteration.
    """
    settings = get_settings()
    bootstrap(settings.db_path)
    gate = GateParams(
        min_ev_per_day=min_ev, min_spread=min_spread, max_capital=max_capital
    )
    meta_path = out.with_suffix(".meta.json")

    loop_started = datetime.now(UTC)
    deadline: datetime | None = (
        loop_started + timedelta(hours=duration_hours) if duration_hours > 0 else None
    )

    typer.echo(
        f"scan-lip-loop start @ {loop_started.isoformat()} "
        f"interval={interval}s out={out}"
        + (f" deadline={deadline.isoformat()}" if deadline else "")
        + (f" max_iter={max_iterations}" if max_iterations else "")
    )

    async def _one_iteration() -> tuple[int, int]:
        async with KalshiReadClient(settings) as client:
            cands = await scan_lip_candidates(
                client,
                category_filter=category,
                gate=gate,
                concurrent_orderbooks=concurrent,
                now=datetime.now(UTC),
            )
        write_candidates_csv(out, cands)
        plays = sum(1 for c in cands if c.ev.play)
        return len(cands), plays

    iteration = 0
    try:
        while True:
            iteration += 1
            scan_started = datetime.now(UTC)
            t0 = time.monotonic()
            try:
                total, plays = asyncio.run(_one_iteration())
                elapsed = time.monotonic() - t0
                _write_meta(
                    meta_path,
                    loop_started=loop_started,
                    scan_started=scan_started,
                    iteration=iteration,
                    total=total,
                    plays=plays,
                    category=category,
                    duration_seconds=elapsed,
                )
                typer.echo(
                    f"[{scan_started.isoformat()}] iter={iteration} "
                    f"total={total} play={plays} took={elapsed:.1f}s"
                )
            except Exception as e:  # log + keep going
                typer.echo(
                    f"[{datetime.now(UTC).isoformat()}] iter={iteration} ERROR: {e}",
                    err=True,
                )

            if max_iterations and iteration >= max_iterations:
                typer.echo(f"reached max-iterations={max_iterations}, stopping")
                break
            if deadline and datetime.now(UTC) >= deadline:
                typer.echo("reached deadline, stopping")
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("\ninterrupted, stopping")
