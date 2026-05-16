"""EV math for LIP market candidates — Phase A of the v2 spec.

Pure functions; no I/O. The orchestrator in lip_scanner.py feeds these
snapshots assembled from the Kalshi API.

Phase A scope (per EV_FORMULA_v2_SPEC.md §8.A):
- Apply discount_factor_bps to effective reward (pessimistic: always on,
  since Kalshi doesn't expose reference_spread_cents to evaluate conditionally)
- Apply assumed uptime multiplier (pessimistic linear; spec §3.3 step 3)
- Two-sided capital_locked = size × (max(bid,5c) + max(1-ask,5c))
- max_size_by_capital uses the actual two-sided cost (spec bug fixed —
  spec's `max(mid, 1-mid)` denominator under-estimates capital for wide spreads)
- Share hard-capped at 0.25 — old proxy is still here temporarily but the
  cap prevents the "thin book = 98% share" hallucination
- Magnitude sanity gate: ev_per_day > 5% of capital_locked → ANOMALY
- Risk caps wired from risk.py
- Volume gate: volume_24h must be > MIN_VOLUME_24H

Explicitly NOT in Phase A: info_velocity, category×velocity share model,
spread_capture / adverse_selection / fees terms, uncertainty bounds, WATCH
decision, account-state gates, correlation grouping, empirical calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from kalshi_ws import risk
from kalshi_ws.intel.velocity import Confidence, Velocity


class Decision(StrEnum):
    """Per-market output of `evaluate`. See module docstring + spec §4."""

    PLAY = "PLAY"  # passed all gates including magnitude
    SKIP = "SKIP"  # failed a gate; not worth attention
    ANOMALY = "ANOMALY"  # math output is implausible; investigate before any action


@dataclass(frozen=True)
class MarketSnapshot:
    """Read-only view of a Kalshi market at scan time. Prices in dollars."""

    ticker: str
    yes_bid: float
    yes_ask: float
    status: str
    top_yes_size: float  # diagnostic only post-Phase-B
    top_no_size: float  # diagnostic only post-Phase-B
    volume_24h: int  # contracts in last 24h; 0 = stale market
    category: str = ""  # e.g. "Climate and Weather", "Entertainment"; "" if unknown
    info_velocity: Velocity = Velocity.HIGH  # from velocity registry; HIGH if untagged
    confidence: Confidence = Confidence.LOW  # diagnostic only; not in math (yet)
    series_ticker: str = ""  # for correlation grouping; "" if unknown


@dataclass(frozen=True)
class LipProgram:
    """Read-only view of an active LIP program for one market."""

    market_ticker: str
    period_reward_cents: int
    target_size: float
    end_date: datetime
    discount_factor_bps: int  # Kalshi's payout reduction for wide quotes


@dataclass(frozen=True)
class GateParams:
    """Pass/play gate thresholds. Defaults align with risk.py + spec §3.2."""

    annual_hurdle: float = 0.10
    min_ev_per_day: float = 1.0
    min_spread: float = 0.02
    max_spread_high_velocity: float = 0.10  # spread > 10c + HIGH velocity = trap
    min_days_remaining: float = 3.0
    min_volume_24h: int = 10
    max_capital: float = float(risk.PER_MARKET_MAX_POSITION_USD)
    magnitude_ceiling_pct: float = 0.05  # ev_per_day above 5% of cap → ANOMALY
    assumed_uptime: float = 0.80  # pessimistic; we won't be on 24/7


DEFAULT_GATE = GateParams()


@dataclass(frozen=True)
class EvResult:
    ticker: str
    decision: Decision
    reason: str
    # Components (all populated for debugging; some may be 0 if gate fired early)
    effective_size: int
    capital_locked: float
    share: float
    competitor_multiplier: float  # what estimate_share used; for audit/calibration
    discount_multiplier: float
    effective_period_reward: float  # dollars after discount + uptime
    reward_per_day: float
    opp_cost_per_day: float
    ev_per_day: float
    ev_pct_of_capital: float
    # Diagnostics
    spread: float
    mid: float
    days_remaining: float
    # Velocity context (for audit + dashboard)
    info_velocity: Velocity
    confidence: Confidence


def _max_size_by_capital(
    yes_bid: float, yes_ask: float, max_capital: float
) -> int:
    """Largest size we can deploy without exceeding per-market capital.

    Spec §3.3 step 1 uses max(mid, 1-mid) as denominator. That under-estimates
    real capital for wide spreads (Drake bid=0.27 ask=0.71: spec says we can
    afford 98 contracts at $50 cap, but actual capital at size 98 is $54.88
    because we lock margin on bid AND (1-ask), not on mid).
    """
    per_unit_capital = max(yes_bid, 0.05) + max(1.0 - yes_ask, 0.05)
    if per_unit_capital <= 0:
        return 0
    return int(max_capital // per_unit_capital)


# Category × velocity → assumed competitor density in multiples of target_size.
# Pessimistic defaults from EV_FORMULA_v2_SPEC.md §3.4 + INFO_VELOCITY_TAGGING.md.
# Categories use Kalshi's actual category strings (case-sensitive).
_COMPETITOR_MULTIPLIER: dict[tuple[str, Velocity], float] = {
    ("Climate and Weather", Velocity.LOW): 2.0,
    ("Climate and Weather", Velocity.MEDIUM): 3.0,
    ("Climate and Weather", Velocity.HIGH): 6.0,
    ("Economics", Velocity.LOW): 3.0,
    ("Economics", Velocity.MEDIUM): 5.0,
    ("Economics", Velocity.HIGH): 8.0,
    ("Entertainment", Velocity.LOW): 4.0,
    ("Entertainment", Velocity.MEDIUM): 5.0,
    ("Entertainment", Velocity.HIGH): 8.0,
    ("Sports", Velocity.HIGH): 10.0,
    ("Sports", Velocity.MEDIUM): 6.0,
    ("Crypto", Velocity.MEDIUM): 6.0,
    ("Crypto", Velocity.HIGH): 10.0,
    ("Politics", Velocity.HIGH): 8.0,
    ("Elections", Velocity.HIGH): 8.0,
    ("Companies", Velocity.MEDIUM): 5.0,
    ("Companies", Velocity.HIGH): 8.0,
}
_DEFAULT_MULTIPLIER = 5.0
_SHARE_CAP = 0.25  # we are never alone — even at very thin books


def estimate_share(
    category: str,
    info_velocity: Velocity,
    volume_24h: int,
    target_size: float,
    effective_size: int,
) -> tuple[float, float]:
    """Pessimistic share estimate, segmented by category × velocity.

    Returns `(share, competitor_multiplier_used)` for audit. The multiplier
    is exposed because it's the dominant assumption — when calibration data
    arrives, this is the lever we adjust.
    """
    base_mult = _COMPETITOR_MULTIPLIER.get((category, info_velocity), _DEFAULT_MULTIPLIER)
    # Volume adjustment: thin flow = less competition, but don't reward heavily.
    if volume_24h < 20:
        mult = base_mult * 0.7
    elif volume_24h > 500:
        mult = base_mult * 1.5
    else:
        mult = base_mult
    competitor_size = target_size * mult
    raw_share = (
        effective_size / (effective_size + competitor_size)
        if (effective_size + competitor_size) > 0
        else 0.0
    )
    return min(raw_share, _SHARE_CAP), mult


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

    effective_size = min(
        int(program.target_size),
        _max_size_by_capital(market.yes_bid, market.yes_ask, gate.max_capital),
    )
    capital_locked = effective_size * (
        max(market.yes_bid, 0.05) + max(1.0 - market.yes_ask, 0.05)
    )

    discount_multiplier = 1.0 - (program.discount_factor_bps / 10_000.0)
    effective_period_reward = (
        (program.period_reward_cents / 100.0)
        * discount_multiplier
        * gate.assumed_uptime
    )

    share, competitor_multiplier = estimate_share(
        category=market.category,
        info_velocity=market.info_velocity,
        volume_24h=market.volume_24h,
        target_size=program.target_size,
        effective_size=effective_size,
    )

    reward_per_day = (
        (effective_period_reward / days_remaining) * share
        if days_remaining > 0
        else 0.0
    )
    opp_cost_per_day = capital_locked * gate.annual_hurdle / 365.0
    ev_per_day = reward_per_day - opp_cost_per_day
    ev_pct_of_capital = ev_per_day / capital_locked if capital_locked > 0 else 0.0

    decision, reason = _gate(
        market=market,
        spread=spread,
        days_remaining=days_remaining,
        effective_size=effective_size,
        capital_locked=capital_locked,
        ev_per_day=ev_per_day,
        ev_pct_of_capital=ev_pct_of_capital,
        gate=gate,
    )

    return EvResult(
        ticker=market.ticker,
        decision=decision,
        reason=reason,
        effective_size=effective_size,
        capital_locked=capital_locked,
        share=share,
        competitor_multiplier=competitor_multiplier,
        discount_multiplier=discount_multiplier,
        effective_period_reward=effective_period_reward,
        reward_per_day=reward_per_day,
        opp_cost_per_day=opp_cost_per_day,
        ev_per_day=ev_per_day,
        ev_pct_of_capital=ev_pct_of_capital,
        spread=spread,
        mid=mid,
        days_remaining=days_remaining,
        info_velocity=market.info_velocity,
        confidence=market.confidence,
    )


def _gate(
    *,
    market: MarketSnapshot,
    spread: float,
    days_remaining: float,
    effective_size: int,
    capital_locked: float,
    ev_per_day: float,
    ev_pct_of_capital: float,
    gate: GateParams,
) -> tuple[Decision, str]:
    """Gate order: data quality → structural → risk → magnitude → EV floor.

    First failing rejects. Magnitude sanity gate runs AFTER all positive
    gates pass and overrides PLAY — bugs in the formula should never deploy
    capital.
    """
    # Data quality
    if market.status != "active":
        return Decision.SKIP, f"status={market.status}"
    if market.yes_bid <= 0 or market.yes_ask >= 1:
        return Decision.SKIP, "one-sided book"
    if market.volume_24h < gate.min_volume_24h:
        return Decision.SKIP, f"volume_24h<{gate.min_volume_24h}"

    # Structural
    if spread < gate.min_spread:
        return Decision.SKIP, f"spread<{gate.min_spread:.2f}"
    if (
        spread > gate.max_spread_high_velocity
        and market.info_velocity == Velocity.HIGH
    ):
        return Decision.SKIP, (
            f"spread>{gate.max_spread_high_velocity:.2f} + HIGH velocity "
            "(adverse selection trap)"
        )
    if days_remaining < gate.min_days_remaining:
        return Decision.SKIP, f"days_remaining<{gate.min_days_remaining:.0f}"

    # Risk
    if effective_size <= 0:
        return Decision.SKIP, "effective_size=0 (capital cap binds at 0)"
    if capital_locked > gate.max_capital:
        # Should be rare since we already bounded size by capital, but check.
        return Decision.SKIP, f"capital_locked>${gate.max_capital:.0f}"

    # Magnitude sanity — anomaly overrides everything below
    if ev_pct_of_capital > gate.magnitude_ceiling_pct:
        return Decision.ANOMALY, (
            f"ev_per_day={ev_pct_of_capital * 100:.1f}% of capital "
            f"(ceiling {gate.magnitude_ceiling_pct * 100:.0f}%) — formula or "
            "data error suspected"
        )

    # EV floor
    if ev_per_day < gate.min_ev_per_day:
        return Decision.SKIP, f"ev_per_day<${gate.min_ev_per_day:.2f}"

    return Decision.PLAY, "play"
