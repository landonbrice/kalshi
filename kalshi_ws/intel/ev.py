"""EV math for LIP market candidates.

Pure functions; no I/O. The orchestrator in lip_scanner.py feeds these
snapshots assembled from the Kalshi API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MarketSnapshot:
    """Read-only view of a Kalshi market at scan time. Prices in dollars."""

    ticker: str
    yes_bid: float
    yes_ask: float
    status: str
    top_yes_size: float  # aggregate size at best yes bid (competing maker proxy)
    top_no_size: float  # aggregate size at best no bid (competing maker proxy)


@dataclass(frozen=True)
class LipProgram:
    """Read-only view of an active LIP program for one market."""

    market_ticker: str
    period_reward_cents: int
    target_size: float
    end_date: datetime


@dataclass(frozen=True)
class GateParams:
    """Pass/play gate thresholds. All defaults tuned to the $700 account."""

    annual_hurdle: float = 0.10
    min_ev_per_day: float = 1.0
    min_spread: float = 0.02
    min_days_remaining: float = 3.0
    max_capital: float = 150.0


DEFAULT_GATE = GateParams()


@dataclass(frozen=True)
class EvResult:
    ticker: str
    ev_per_day: float
    reward_per_day: float
    share: float
    spread: float
    mid: float
    capital_locked: float
    opp_cost_per_day: float
    days_remaining: float
    play: bool
    reason: str


def evaluate(
    market: MarketSnapshot,
    program: LipProgram,
    *,
    now: datetime,
    gate: GateParams = DEFAULT_GATE,
) -> EvResult:
    spread = market.yes_ask - market.yes_bid
    mid = (market.yes_bid + market.yes_ask) / 2
    days_remaining = max((program.end_date - now).total_seconds() / 86400, 0.0)

    # Competitor proxy: average of resting sizes on both sides
    competitor_size = (market.top_yes_size + market.top_no_size) / 2
    denom = program.target_size + competitor_size
    share = program.target_size / denom if denom > 0 else 1.0

    period_reward = program.period_reward_cents / 100.0
    reward_per_day = (period_reward / days_remaining) * share if days_remaining > 0 else 0.0

    capital_locked = program.target_size * max(mid, 1.0 - mid)
    opp_cost_per_day = capital_locked * gate.annual_hurdle / 365.0

    ev_per_day = reward_per_day - opp_cost_per_day

    play, reason = _gate(market, spread, days_remaining, capital_locked, ev_per_day, gate)

    return EvResult(
        ticker=market.ticker,
        ev_per_day=ev_per_day,
        reward_per_day=reward_per_day,
        share=share,
        spread=spread,
        mid=mid,
        capital_locked=capital_locked,
        opp_cost_per_day=opp_cost_per_day,
        days_remaining=days_remaining,
        play=play,
        reason=reason,
    )


def _gate(
    market: MarketSnapshot,
    spread: float,
    days_remaining: float,
    capital_locked: float,
    ev_per_day: float,
    g: GateParams,
) -> tuple[bool, str]:
    if market.status != "active":
        return False, f"status={market.status}"
    if market.yes_bid <= 0 or market.yes_ask >= 1:
        return False, "one-sided book"
    if spread < g.min_spread:
        return False, f"spread<{g.min_spread:.2f}"
    if days_remaining < g.min_days_remaining:
        return False, f"days_remaining<{g.min_days_remaining:.0f}"
    if capital_locked > g.max_capital:
        return False, f"capital_locked>{g.max_capital:.0f}"
    if ev_per_day < g.min_ev_per_day:
        return False, f"ev_per_day<{g.min_ev_per_day:.2f}"
    return True, "play"
