"""LIP candidate scanner.

Orchestrates: pull active LIP programs -> enrich with markets + event category
-> cheap-gate -> fetch orderbooks concurrently -> compute EV -> rank.

Pure I/O glue. Math lives in `kalshi_ws.intel.ev`.
"""

from __future__ import annotations

import asyncio
import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from kalshi_ws.api.auth import signed_headers
from kalshi_ws.api.models import IncentiveProgram, Market
from kalshi_ws.api.read import KalshiReadClient
from kalshi_ws.intel.ev import (
    DEFAULT_GATE,
    EvResult,
    GateParams,
    LipProgram,
    MarketSnapshot,
    evaluate,
)


@dataclass(frozen=True)
class LipCandidate:
    """One scored LIP market candidate."""

    market: Market
    program: IncentiveProgram
    category: str
    snapshot: MarketSnapshot
    ev: EvResult


async def scan_lip_candidates(
    client: KalshiReadClient,
    *,
    category_filter: str | None = None,
    gate: GateParams = DEFAULT_GATE,
    concurrent_orderbooks: int = 10,
    now: datetime | None = None,
) -> list[LipCandidate]:
    """Return LIP candidates ranked by EV/day desc.

    Includes both PLAY and PASS rows so the operator can see why each was gated.
    Filter to `c.ev.play` for the actionable shortlist.
    """
    now = now or datetime.now(UTC)

    programs = await client.list_incentive_programs(
        incentive_type="liquidity", status="active"
    )
    program_by_ticker = {p.market_ticker: p for p in programs}
    tickers = sorted(program_by_ticker.keys())
    markets = await client.get_markets_by_tickers(tickers)
    market_by_ticker = {m.ticker: m for m in markets}

    event_tickers = {m.event_ticker for m in markets if m.event_ticker}
    categories = await _fetch_event_categories(client, event_tickers)

    pre_filtered: list[tuple[Market, IncentiveProgram, str]] = []
    for t in tickers:
        m = market_by_ticker.get(t)
        p = program_by_ticker.get(t)
        if m is None or p is None:
            continue
        cat = categories.get(m.event_ticker or "", "")
        if category_filter and cat != category_filter:
            continue
        if not _passes_cheap_gates(m, p, gate, now):
            continue
        pre_filtered.append((m, p, cat))

    candidates = await _score_with_orderbooks(
        client, pre_filtered, gate=gate, now=now, concurrency=concurrent_orderbooks
    )
    candidates.sort(key=lambda c: c.ev.ev_per_day, reverse=True)
    return candidates


def _passes_cheap_gates(
    m: Market, p: IncentiveProgram, gate: GateParams, now: datetime
) -> bool:
    """Filters that don't require an orderbook fetch. Cuts the 3k+ universe sharply."""
    if m.status != "active":
        return False
    if m.yes_bid == 0 or m.yes_ask == 100:
        return False
    spread_cents = m.yes_ask - m.yes_bid
    if spread_cents < gate.min_spread * 100:
        return False
    days = (p.end_date - now).total_seconds() / 86400
    if days < gate.min_days_remaining:
        return False
    mid_dollars = (m.yes_bid + m.yes_ask) / 200.0
    capital = p.target_size() * max(mid_dollars, 1.0 - mid_dollars)
    return capital <= gate.max_capital


async def _fetch_event_categories(
    client: KalshiReadClient, event_tickers: Iterable[str]
) -> dict[str, str]:
    """Look up `event.category` for each event ticker. Concurrent, error-tolerant."""
    tickers = list(event_tickers)
    sem = asyncio.Semaphore(20)

    async def one(et: str) -> tuple[str, str]:
        url = f"{client._settings.base_url}/events/{et}"  # noqa: SLF001
        async with sem:
            headers = signed_headers(
                api_key_id=client._settings.api_key_id,  # noqa: SLF001
                private_key_path=client._settings.private_key_path,  # noqa: SLF001
                method="GET",
                path=urlparse(url).path,
            )
            try:
                r = await client._client.get(url, headers=headers)  # noqa: SLF001
                r.raise_for_status()
                body = r.json()
                return et, (body.get("event") or {}).get("category") or ""
            except Exception:
                return et, ""

    results = await asyncio.gather(*(one(et) for et in tickers))
    return dict(results)


async def _score_with_orderbooks(
    client: KalshiReadClient,
    candidates: list[tuple[Market, IncentiveProgram, str]],
    *,
    gate: GateParams,
    now: datetime,
    concurrency: int,
) -> list[LipCandidate]:
    sem = asyncio.Semaphore(concurrency)

    async def score(
        m: Market, p: IncentiveProgram, cat: str
    ) -> LipCandidate | None:
        async with sem:
            try:
                ob = await client.get_orderbook(m.ticker)
            except Exception:
                return None
        snap = MarketSnapshot(
            ticker=m.ticker,
            yes_bid=m.yes_bid / 100.0,
            yes_ask=m.yes_ask / 100.0,
            status=m.status,
            top_yes_size=ob.top_yes_size(),
            top_no_size=ob.top_no_size(),
        )
        prog = LipProgram(
            market_ticker=p.market_ticker,
            period_reward_cents=p.period_reward,
            target_size=p.target_size(),
            end_date=p.end_date,
        )
        ev = evaluate(snap, prog, now=now, gate=gate)
        return LipCandidate(market=m, program=p, category=cat, snapshot=snap, ev=ev)

    raw = await asyncio.gather(*(score(m, p, c) for m, p, c in candidates))
    return [c for c in raw if c is not None]


def write_candidates_csv(path: Path, candidates: list[LipCandidate]) -> None:
    """Persist a ranked candidate list. Columns chosen for dashboard ingestion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ticker",
        "title",
        "category",
        "play",
        "reason",
        "ev_per_day",
        "reward_per_day",
        "share",
        "opp_cost_per_day",
        "capital_locked",
        "days_remaining",
        "spread",
        "mid",
        "yes_bid_cents",
        "yes_ask_cents",
        "top_yes_size",
        "top_no_size",
        "lip_target_size",
        "lip_period_reward_cents",
        "lip_end_date",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in candidates:
            w.writerow(
                {
                    "ticker": c.market.ticker,
                    "title": c.market.title,
                    "category": c.category,
                    "play": c.ev.play,
                    "reason": c.ev.reason,
                    "ev_per_day": f"{c.ev.ev_per_day:.4f}",
                    "reward_per_day": f"{c.ev.reward_per_day:.4f}",
                    "share": f"{c.ev.share:.4f}",
                    "opp_cost_per_day": f"{c.ev.opp_cost_per_day:.4f}",
                    "capital_locked": f"{c.ev.capital_locked:.4f}",
                    "days_remaining": f"{c.ev.days_remaining:.2f}",
                    "spread": f"{c.ev.spread:.4f}",
                    "mid": f"{c.ev.mid:.4f}",
                    "yes_bid_cents": c.market.yes_bid,
                    "yes_ask_cents": c.market.yes_ask,
                    "top_yes_size": c.snapshot.top_yes_size,
                    "top_no_size": c.snapshot.top_no_size,
                    "lip_target_size": c.program.target_size(),
                    "lip_period_reward_cents": c.program.period_reward,
                    "lip_end_date": c.program.end_date.isoformat(),
                }
            )
