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
    """Filters that don't require an orderbook fetch. Cuts the 3k+ universe sharply.

    Stays loose on capital: the post-orderbook `evaluate()` enforces the real
    per-market capital cap using effective_size after risk bounding. This
    function only rejects markets that can never be evaluated meaningfully —
    closed, empty book, narrow spread, expiring LIP, or no recent flow.
    """
    if m.status != "active":
        return False
    if m.yes_bid == 0 or m.yes_ask == 100:
        return False
    if m.volume_24h < gate.min_volume_24h:
        return False
    spread_cents = m.yes_ask - m.yes_bid
    if spread_cents < gate.min_spread * 100:
        return False
    days = (p.end_date - now).total_seconds() / 86400
    return days >= gate.min_days_remaining


async def _fetch_event_categories(
    client: KalshiReadClient,
    event_tickers: Iterable[str],
    *,
    concurrency: int = 4,
) -> dict[str, str]:
    """Look up `event.category` for each event ticker.

    Concurrency is intentionally low: empirically Kalshi rate-limits the
    /events endpoint when fanning out 20+ concurrent requests, returning 429
    (which raise_for_status surfaces). Failed lookups fall back to empty
    string so the scanner can still rank by EV; the dashboard treats missing
    category as "uncategorized".
    """
    tickers = list(event_tickers)
    sem = asyncio.Semaphore(concurrency)

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
            volume_24h=m.volume_24h,
        )
        prog = LipProgram(
            market_ticker=p.market_ticker,
            period_reward_cents=p.period_reward,
            target_size=p.target_size(),
            end_date=p.end_date,
            discount_factor_bps=p.discount_factor_bps,
        )
        ev = evaluate(snap, prog, now=now, gate=gate)
        return LipCandidate(market=m, program=p, category=cat, snapshot=snap, ev=ev)

    raw = await asyncio.gather(*(score(m, p, c) for m, p, c in candidates))
    return [c for c in raw if c is not None]


def write_candidates_csv(path: Path, candidates: list[LipCandidate]) -> None:
    """Persist a ranked candidate list atomically (tmp file + rename).

    Atomic write matters because the dashboard polls this file; a half-written
    CSV would either parse-fail or yield phantom rows. Columns chosen for
    dashboard ingestion -- see also the dashboard agent brief.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fields = [
        "ticker",
        "title",
        "category",
        "decision",  # Phase A: PLAY / SKIP / ANOMALY
        "play",  # back-compat bool for dashboard agent (decision == PLAY)
        "reason",
        "ev_per_day",
        "ev_pct_of_capital",
        "reward_per_day",
        "effective_period_reward",
        "discount_multiplier",
        "share",
        "effective_size",
        "opp_cost_per_day",
        "capital_locked",
        "days_remaining",
        "spread",
        "mid",
        "volume_24h",
        "yes_bid_cents",
        "yes_ask_cents",
        "top_yes_size",
        "top_no_size",
        "lip_target_size",
        "lip_period_reward_cents",
        "lip_discount_factor_bps",
        "lip_end_date",
    ]
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in candidates:
            w.writerow(
                {
                    "ticker": c.market.ticker,
                    "title": c.market.title,
                    "category": c.category,
                    "decision": c.ev.decision.value,
                    "play": c.ev.decision.value == "PLAY",
                    "reason": c.ev.reason,
                    "ev_per_day": f"{c.ev.ev_per_day:.4f}",
                    "ev_pct_of_capital": f"{c.ev.ev_pct_of_capital:.4f}",
                    "reward_per_day": f"{c.ev.reward_per_day:.4f}",
                    "effective_period_reward": f"{c.ev.effective_period_reward:.2f}",
                    "discount_multiplier": f"{c.ev.discount_multiplier:.2f}",
                    "share": f"{c.ev.share:.4f}",
                    "effective_size": c.ev.effective_size,
                    "opp_cost_per_day": f"{c.ev.opp_cost_per_day:.4f}",
                    "capital_locked": f"{c.ev.capital_locked:.4f}",
                    "days_remaining": f"{c.ev.days_remaining:.2f}",
                    "spread": f"{c.ev.spread:.4f}",
                    "mid": f"{c.ev.mid:.4f}",
                    "volume_24h": c.snapshot.volume_24h,
                    "yes_bid_cents": c.market.yes_bid,
                    "yes_ask_cents": c.market.yes_ask,
                    "top_yes_size": c.snapshot.top_yes_size,
                    "top_no_size": c.snapshot.top_no_size,
                    "lip_target_size": c.program.target_size(),
                    "lip_period_reward_cents": c.program.period_reward,
                    "lip_discount_factor_bps": c.program.discount_factor_bps,
                    "lip_end_date": c.program.end_date.isoformat(),
                }
            )
    tmp.replace(path)
