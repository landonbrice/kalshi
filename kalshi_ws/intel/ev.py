"""EV math for LIP market candidates — v2 spec, Phases A+B+C.

Pure functions; no I/O. The orchestrator in lip_scanner.py feeds these
snapshots assembled from the Kalshi API.

Phase A (commit 79299a7): two-sided capital, discount, uptime, share hardcap,
volume gate, magnitude/anomaly gate, Decision enum.

Phase B (commit bc81f6e): replace share hardcap with category × velocity
estimate (`estimate_share`); structural gate "wide spread + HIGH velocity =
adverse selection trap"; thread velocity tags from `intel.velocity`.

Phase C (this commit, per EV_FORMULA_v2_SPEC.md §3.3 steps 7-9 + §3.5 + §4):
- spread_capture_per_day = expected_fills × quote_width
- adverse_selection_per_day = expected_fills × adverse_per_fill[velocity]
- fees_per_day = expected_fills × taker_fill_rate × (fee_rate × mid × (1-mid))
- expected_fills_per_day = min(volume_24h × share, 2 × effective_size)
- Uncertainty bounds: ev_low (share halved, adverse doubled), ev_high (share
  doubled to share cap, adverse halved). Per spec note: bounds adjust only
  the two biggest unknowns (lip_rebate and adverse_selection); spread_capture
  and fees stay at mid values for sensitivity-analysis cleanliness.
- Decision.WATCH: mid-case positive but ev_low negative — needs human review.

Known modeling weakness (called out in Phase C scoping with user):
spread_capture and adverse_selection are modeled as independent of each
other. In reality spread capture requires a round trip (both sides fill);
adverse selection happens on a one-sided fill where the next move is against
us. The spec's approximation treats both as `fill_rate × magnitude` —
roughly OK at low fill rates, gets sloppy at high fill rates. Refine once
we have realized fill data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from kalshi_ws import risk
from kalshi_ws.intel.velocity import Confidence, Velocity


class Decision(StrEnum):
    """Per-market output of `evaluate`. See module docstring + spec §4."""

    PLAY = "PLAY"  # all gates passed; ev_low also positive
    WATCH = "WATCH"  # mid-case positive but low bound negative — needs review
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
    # Phase C: spread capture + fees
    quote_width_dollars: float = 0.02  # we quote 2c inside reference
    taker_fill_rate: float = 0.30  # fraction of fills needing active flattening
    fee_rate: float = 0.07  # Kalshi standard rate; applied to mid × (1-mid)


DEFAULT_GATE = GateParams()


# Velocity → expected adverse-selection cost per fill (dollars).
# Pessimistic defaults from EV_FORMULA_v2_SPEC.md §3.3 step 8; replace with
# empirical estimates once we have ≥20 fills per velocity bucket.
_ADVERSE_PER_FILL: dict[Velocity, float] = {
    Velocity.LOW: 0.005,  # 0.5c
    Velocity.MEDIUM: 0.030,  # 3.0c
    Velocity.HIGH: 0.100,  # 10.0c
}


@dataclass(frozen=True)
class EvComponents:
    """Per-day decomposition of ev_per_day (all dollars; positive = revenue).

    `total` = lip_rebate + spread_capture - adverse_selection - fees - opp_cost.
    Stored as fields rather than computed-on-read so EvResult is straight-
    forwardly serializable.
    """

    lip_rebate: float
    spread_capture: float
    adverse_selection: float
    fees: float
    opp_cost: float
    total: float


@dataclass(frozen=True)
class EvResult:
    ticker: str
    decision: Decision
    reason: str
    # Sizing
    effective_size: int
    capital_locked: float
    share: float
    competitor_multiplier: float  # what estimate_share used; for audit
    discount_multiplier: float
    effective_period_reward: float  # dollars after discount + uptime
    expected_fills_per_day: float
    # Mid-case decomposition (each term in dollars/day)
    components: EvComponents
    ev_per_day: float  # alias for components.total; kept for back-compat
    reward_per_day: float  # alias for components.lip_rebate; back-compat
    opp_cost_per_day: float  # alias for components.opp_cost; back-compat
    ev_pct_of_capital: float
    # Uncertainty bounds
    ev_low: float
    ev_high: float
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


def _compute_components(
    *,
    share: float,
    effective_period_reward: float,
    days_remaining: float,
    volume_24h: int,
    effective_size: int,
    mid: float,
    capital_locked: float,
    info_velocity: Velocity,
    gate: GateParams,
) -> tuple[EvComponents, float]:
    """Mid-case full decomposition. Returns (components, expected_fills_per_day).

    expected_fills is returned separately because it's a diagnostic the
    dashboard shows, not part of the EV breakdown itself.
    """
    expected_fills_per_day = min(volume_24h * share, 2.0 * effective_size)

    lip_rebate = (
        (effective_period_reward / days_remaining) * share
        if days_remaining > 0
        else 0.0
    )
    spread_capture = expected_fills_per_day * gate.quote_width_dollars
    adverse_per_fill = _ADVERSE_PER_FILL.get(info_velocity, _ADVERSE_PER_FILL[Velocity.HIGH])
    adverse_selection = expected_fills_per_day * adverse_per_fill
    fee_per_contract = gate.fee_rate * mid * (1.0 - mid)
    fees = expected_fills_per_day * gate.taker_fill_rate * fee_per_contract
    opp_cost = capital_locked * gate.annual_hurdle / 365.0

    total = lip_rebate + spread_capture - adverse_selection - fees - opp_cost
    return (
        EvComponents(
            lip_rebate=lip_rebate,
            spread_capture=spread_capture,
            adverse_selection=adverse_selection,
            fees=fees,
            opp_cost=opp_cost,
            total=total,
        ),
        expected_fills_per_day,
    )


def _bound_adjustment(
    mid_components: EvComponents,
    *,
    share_factor: float,
    adverse_factor: float,
) -> float:
    """Compute the perturbed total per spec §3.5.

    Bounds adjust ONLY lip_rebate and adverse_selection (the two biggest
    unknowns); spread_capture, fees, and opp_cost stay at mid values. This
    is a sensitivity analysis on the dominant uncertainties, not a full
    counterfactual recomputation.
    """
    return (
        mid_components.lip_rebate * share_factor
        + mid_components.spread_capture
        - mid_components.adverse_selection * adverse_factor
        - mid_components.fees
        - mid_components.opp_cost
    )


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

    components, expected_fills = _compute_components(
        share=share,
        effective_period_reward=effective_period_reward,
        days_remaining=days_remaining,
        volume_24h=market.volume_24h,
        effective_size=effective_size,
        mid=mid,
        capital_locked=capital_locked,
        info_velocity=market.info_velocity,
        gate=gate,
    )
    ev_per_day = components.total
    ev_pct_of_capital = ev_per_day / capital_locked if capital_locked > 0 else 0.0

    # Uncertainty bounds (spec §3.5)
    ev_low = _bound_adjustment(components, share_factor=0.5, adverse_factor=2.0)
    # High case: share doubles, but already-capped at 0.25 means the actual
    # multiplier is min(2.0, 0.25 / share) when share > 0.
    high_share_factor = (
        min(2.0, _SHARE_CAP / share) if share > 0 else 1.0
    )
    ev_high = _bound_adjustment(
        components, share_factor=high_share_factor, adverse_factor=0.5
    )

    decision, reason = _gate(
        market=market,
        spread=spread,
        days_remaining=days_remaining,
        effective_size=effective_size,
        capital_locked=capital_locked,
        ev_per_day=ev_per_day,
        ev_pct_of_capital=ev_pct_of_capital,
        ev_low=ev_low,
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
        expected_fills_per_day=expected_fills,
        components=components,
        ev_per_day=ev_per_day,
        reward_per_day=components.lip_rebate,
        opp_cost_per_day=components.opp_cost,
        ev_pct_of_capital=ev_pct_of_capital,
        ev_low=ev_low,
        ev_high=ev_high,
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
    ev_low: float,
    gate: GateParams,
) -> tuple[Decision, str]:
    """Gate order: data quality → structural → risk → magnitude → EV floor → bounds.

    First failing rejects with SKIP. Magnitude anomaly overrides PLAY. WATCH
    fires when mid-case clears but low bound is negative — operator decides.
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

    # Uncertainty bound — WATCH if mid is positive but low case goes negative
    if ev_low < 0:
        return Decision.WATCH, (
            f"ev_per_day=${ev_per_day:.2f} positive but ev_low=${ev_low:.2f} "
            "negative — share/adverse uncertainty too wide to play"
        )

    return Decision.PLAY, "play"
