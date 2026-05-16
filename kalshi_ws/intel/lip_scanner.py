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
from kalshi_ws.intel.velocity import (
    SeriesTag,
    VelocityRegistry,
    get_default_registry,
)


@dataclass(frozen=True)
class EventMeta:
    """What we extract from a /events/{ticker} lookup."""

    category: str
    series_ticker: str


@dataclass(frozen=True)
class LipCandidate:
    """One scored LIP market candidate."""

    market: Market
    program: IncentiveProgram
    category: str
    series_ticker: str
    velocity_tag: SeriesTag
    snapshot: MarketSnapshot
    ev: EvResult


async def scan_lip_candidates(
    client: KalshiReadClient,
    *,
    category_filter: str | None = None,
    gate: GateParams = DEFAULT_GATE,
    concurrent_orderbooks: int = 10,
    now: datetime | None = None,
    velocity_registry: VelocityRegistry | None = None,
) -> list[LipCandidate]:
    """Return LIP candidates ranked by EV/day desc.

    Includes PLAY / SKIP / ANOMALY rows so the operator can audit the gating.
    `velocity_registry` defaults to the cached `config/series_velocity.yaml`
    load; tests inject a fixture.
    """
    now = now or datetime.now(UTC)
    registry = velocity_registry or get_default_registry()

    programs = await client.list_incentive_programs(
        incentive_type="liquidity", status="active"
    )
    program_by_ticker = {p.market_ticker: p for p in programs}
    tickers = sorted(program_by_ticker.keys())
    markets = await client.get_markets_by_tickers(tickers)
    market_by_ticker = {m.ticker: m for m in markets}

    event_tickers = {m.event_ticker for m in markets if m.event_ticker}
    event_meta = await _fetch_event_meta(client, event_tickers)

    pre_filtered: list[tuple[Market, IncentiveProgram, EventMeta]] = []
    for t in tickers:
        m = market_by_ticker.get(t)
        p = program_by_ticker.get(t)
        if m is None or p is None:
            continue
        meta = event_meta.get(m.event_ticker or "", EventMeta(category="", series_ticker=""))
        if category_filter and meta.category != category_filter:
            continue
        if not _passes_cheap_gates(m, p, gate, now):
            continue
        pre_filtered.append((m, p, meta))

    candidates = await _score_with_orderbooks(
        client,
        pre_filtered,
        gate=gate,
        now=now,
        concurrency=concurrent_orderbooks,
        registry=registry,
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


async def _fetch_event_meta(
    client: KalshiReadClient,
    event_tickers: Iterable[str],
    *,
    concurrency: int = 4,
) -> dict[str, EventMeta]:
    """Look up `event.category` AND `event.series_ticker` for each event ticker.

    Concurrency is intentionally low: empirically Kalshi rate-limits the
    /events endpoint when fanning out 20+ concurrent requests, returning 429
    (which raise_for_status surfaces). Failed lookups fall back to empty
    strings so the scanner can still rank — series_ticker="" means the
    velocity registry will return its default (HIGH) tag.
    """
    tickers = list(event_tickers)
    sem = asyncio.Semaphore(concurrency)

    async def one(et: str) -> tuple[str, EventMeta]:
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
                ev = (r.json().get("event") or {})
                return et, EventMeta(
                    category=ev.get("category") or "",
                    series_ticker=ev.get("series_ticker") or "",
                )
            except Exception:
                return et, EventMeta(category="", series_ticker="")

    results = await asyncio.gather(*(one(et) for et in tickers))
    return dict(results)


async def _score_with_orderbooks(
    client: KalshiReadClient,
    candidates: list[tuple[Market, IncentiveProgram, EventMeta]],
    *,
    gate: GateParams,
    now: datetime,
    concurrency: int,
    registry: VelocityRegistry,
) -> list[LipCandidate]:
    sem = asyncio.Semaphore(concurrency)

    async def score(
        m: Market, p: IncentiveProgram, meta: EventMeta
    ) -> LipCandidate | None:
        async with sem:
            try:
                ob = await client.get_orderbook(m.ticker)
            except Exception:
                return None
        tag = registry.lookup(meta.series_ticker or None)
        snap = MarketSnapshot(
            ticker=m.ticker,
            yes_bid=m.yes_bid / 100.0,
            yes_ask=m.yes_ask / 100.0,
            status=m.status,
            top_yes_size=ob.top_yes_size(),
            top_no_size=ob.top_no_size(),
            volume_24h=m.volume_24h,
            category=meta.category,
            info_velocity=tag.info_velocity,
            confidence=tag.confidence,
            series_ticker=meta.series_ticker,
        )
        prog = LipProgram(
            market_ticker=p.market_ticker,
            period_reward_cents=p.period_reward,
            target_size=p.target_size(),
            end_date=p.end_date,
            discount_factor_bps=p.discount_factor_bps,
        )
        ev = evaluate(snap, prog, now=now, gate=gate)
        return LipCandidate(
            market=m,
            program=p,
            category=meta.category,
            series_ticker=meta.series_ticker,
            velocity_tag=tag,
            snapshot=snap,
            ev=ev,
        )

    raw = await asyncio.gather(*(score(m, p, meta) for m, p, meta in candidates))
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
        "series_ticker",
        "info_velocity",
        "confidence",
        "velocity_is_default",
        "correlation_group",
        "decision",  # PLAY / WATCH / SKIP / ANOMALY
        "play",  # back-compat bool for dashboard agent (decision == PLAY)
        "reason",
        "ev_per_day",
        "ev_low",
        "ev_high",
        "ev_pct_of_capital",
        # Phase C component breakdown ($/day)
        "lip_rebate_per_day",
        "spread_capture_per_day",
        "adverse_selection_per_day",
        "fees_per_day",
        "opp_cost_per_day",
        "expected_fills_per_day",
        # Back-compat alias (= lip_rebate_per_day)
        "reward_per_day",
        # Sizing + program detail
        "effective_period_reward",
        "discount_multiplier",
        "share",
        "competitor_multiplier",
        "effective_size",
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
                    "series_ticker": c.series_ticker,
                    "info_velocity": c.ev.info_velocity.value,
                    "confidence": c.ev.confidence.value,
                    "velocity_is_default": c.velocity_tag.is_default,
                    "correlation_group": c.velocity_tag.correlation_group or "",
                    "decision": c.ev.decision.value,
                    "play": c.ev.decision.value == "PLAY",
                    "reason": c.ev.reason,
                    "ev_per_day": f"{c.ev.ev_per_day:.4f}",
                    "ev_low": f"{c.ev.ev_low:.4f}",
                    "ev_high": f"{c.ev.ev_high:.4f}",
                    "ev_pct_of_capital": f"{c.ev.ev_pct_of_capital:.4f}",
                    "lip_rebate_per_day": f"{c.ev.components.lip_rebate:.4f}",
                    "spread_capture_per_day": f"{c.ev.components.spread_capture:.4f}",
                    "adverse_selection_per_day": f"{c.ev.components.adverse_selection:.4f}",
                    "fees_per_day": f"{c.ev.components.fees:.4f}",
                    "opp_cost_per_day": f"{c.ev.components.opp_cost:.4f}",
                    "expected_fills_per_day": f"{c.ev.expected_fills_per_day:.2f}",
                    "reward_per_day": f"{c.ev.reward_per_day:.4f}",
                    "effective_period_reward": f"{c.ev.effective_period_reward:.2f}",
                    "discount_multiplier": f"{c.ev.discount_multiplier:.2f}",
                    "share": f"{c.ev.share:.4f}",
                    "competitor_multiplier": f"{c.ev.competitor_multiplier:.2f}",
                    "effective_size": c.ev.effective_size,
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
