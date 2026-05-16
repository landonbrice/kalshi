from datetime import UTC, datetime, timedelta

import pytest

from kalshi_ws.intel.ev import (
    Decision,
    GateParams,
    LipProgram,
    MarketSnapshot,
    evaluate,
)

NOW = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)


def _market(**overrides: object) -> MarketSnapshot:
    base = {
        "ticker": "TEST-1",
        "yes_bid": 0.40,
        "yes_ask": 0.45,
        "status": "active",
        "top_yes_size": 0.0,
        "top_no_size": 0.0,
        "volume_24h": 100,
    }
    base.update(overrides)
    return MarketSnapshot(**base)  # type: ignore[arg-type]


def _program(**overrides: object) -> LipProgram:
    base = {
        "market_ticker": "TEST-1",
        "period_reward_cents": 250_000,  # $2,500
        "target_size": 250.0,
        "end_date": NOW + timedelta(days=30),
        "discount_factor_bps": 0,  # default to no discount in test fixtures
    }
    base.update(overrides)
    return LipProgram(**base)  # type: ignore[arg-type]


# ---- Component math ----


def test_two_sided_capital_uses_bid_and_one_minus_ask() -> None:
    """capital_locked = size × (max(bid,5c) + max(1-ask,5c)). Spec §3.3 step 2."""
    # Modest size so the gate doesn't trip on $50 cap
    r = evaluate(
        _market(yes_bid=0.27, yes_ask=0.71),
        _program(target_size=50, period_reward_cents=10_000),
        now=NOW,
    )
    # Per-unit capital = max(0.27, 0.05) + max(0.29, 0.05) = 0.56
    # effective_size limited by either target or capital cap
    # max_size_by_capital = int(50 / 0.56) = 89; target=50; so effective_size=50
    assert r.effective_size == 50
    assert r.capital_locked == pytest.approx(50 * 0.56)


def test_max_size_by_capital_uses_actual_two_sided_cost_not_mid() -> None:
    """Spec bug fix: spec's max(mid, 1-mid) under-estimates capital for wide spreads."""
    # bid=0.27, ask=0.71 → per-unit = 0.56, NOT mid=0.49 / 0.51
    # At $50 cap: max_size = int(50 / 0.56) = 89, not int(50/0.51) = 98
    r = evaluate(
        _market(yes_bid=0.27, yes_ask=0.71),
        _program(target_size=500, period_reward_cents=10_000),
        now=NOW,
    )
    assert r.effective_size == 89
    assert r.capital_locked == pytest.approx(89 * 0.56)
    assert r.capital_locked <= 50.0  # respects risk cap


def test_discount_factor_applied_to_period_reward() -> None:
    """discount_factor_bps=5000 → 50% reduction. Spec §3.3 step 3."""
    p_no = _program(discount_factor_bps=0)
    p_50 = _program(discount_factor_bps=5000)
    r_no = evaluate(_market(), p_no, now=NOW)
    r_50 = evaluate(_market(), p_50, now=NOW)
    # 50% discount × same 0.80 uptime → effective reward halved
    assert r_50.effective_period_reward == pytest.approx(r_no.effective_period_reward / 2)
    assert r_50.discount_multiplier == 0.5


def test_uptime_multiplier_applied() -> None:
    """assumed_uptime=0.80 → reward × 0.80. Spec §3.3 step 3."""
    r = evaluate(_market(), _program(discount_factor_bps=0), now=NOW)
    # period_reward $2,500 × 1.0 discount × 0.80 uptime = $2,000
    assert r.effective_period_reward == pytest.approx(2000.0)


def test_share_hard_capped_at_25_pct() -> None:
    """The v1 thin-book hallucination must be ceiling'd. Spec §A.6."""
    # Empty book → old proxy says share=1.0
    r = evaluate(_market(top_yes_size=0, top_no_size=0), _program(), now=NOW)
    assert r.share == pytest.approx(0.25)


def test_share_below_cap_uses_proxy() -> None:
    """Phase A keeps v1's proxy active below the cap (it's only wrong at thin-book extreme)."""
    # top_of_book_avg = 1000, target=250 → proxy = 250/1250 = 0.20 < 0.25
    r = evaluate(
        _market(top_yes_size=1000, top_no_size=1000),
        _program(),
        now=NOW,
    )
    assert r.share == pytest.approx(0.20)


# ---- Decision gates ----


def test_skip_when_not_active() -> None:
    r = evaluate(_market(status="closed"), _program(), now=NOW)
    assert r.decision == Decision.SKIP
    assert "status" in r.reason


def test_skip_when_one_sided_book() -> None:
    r = evaluate(_market(yes_bid=0.0, yes_ask=0.30), _program(), now=NOW)
    assert r.decision == Decision.SKIP
    assert "one-sided" in r.reason


def test_skip_when_volume_24h_below_floor() -> None:
    """No flow = no signal. Spec §4 data quality gate."""
    r = evaluate(_market(volume_24h=5), _program(), now=NOW)
    assert r.decision == Decision.SKIP
    assert "volume_24h" in r.reason


def test_skip_when_spread_too_narrow() -> None:
    r = evaluate(_market(yes_bid=0.49, yes_ask=0.50), _program(), now=NOW)
    assert r.decision == Decision.SKIP
    assert "spread" in r.reason


def test_skip_when_days_remaining_too_short() -> None:
    r = evaluate(
        _market(),
        _program(end_date=NOW + timedelta(days=1)),
        now=NOW,
    )
    assert r.decision == Decision.SKIP
    assert "days_remaining" in r.reason


def test_anomaly_when_magnitude_exceeds_5pct_of_capital() -> None:
    """The headline safety gate. v1's Drake = 473% daily must be caught here."""
    # Set up a market where reward looks huge relative to capital:
    # tiny capital, big rebate, slow burn (long days_remaining, small denom)
    # Use a big pool + small target so reward_per_day blows through 5% × cap
    r = evaluate(
        _market(yes_bid=0.40, yes_ask=0.45, top_yes_size=0, top_no_size=0),
        _program(period_reward_cents=10_000_000, target_size=50, discount_factor_bps=0),
        now=NOW,
    )
    assert r.decision == Decision.ANOMALY
    assert "% of capital" in r.reason
    # Sanity: gate is comparing ev_pct_of_capital > 0.05
    assert r.ev_pct_of_capital > 0.05


def test_anomaly_overrides_play_when_math_blows_up() -> None:
    """Anomaly must fire even when all other gates would have passed."""
    # Same setup as above; verify it's not skipping for any other reason
    r = evaluate(
        _market(volume_24h=500, yes_bid=0.40, yes_ask=0.45),
        _program(period_reward_cents=10_000_000, target_size=50, discount_factor_bps=0),
        now=NOW,
    )
    assert r.decision == Decision.ANOMALY
    # Volume, spread, days, capital all OK
    assert r.spread >= 0.02
    assert r.days_remaining >= 3
    assert r.capital_locked <= 50.0


def test_play_when_within_realistic_envelope() -> None:
    """A market that should pass: small reward, sane share, low return %."""
    # period_reward $200, 30 days, share 0.20 → reward_per_day = $200/30 × 0.20 = $1.33
    # Wait that's < $1 min_ev. Let me bump to make it pass cleanly.
    # $1,500 pool, share capped 0.25, 30 days, uptime 0.80, no discount
    # reward_per_day = $1500 × 0.80 / 30 × 0.25 = $10
    # capital at size=50, bid=0.40, ask=0.45 → 50 × (0.40 + 0.55) = $47.50
    # ev/cap = 10/47.5 = 21% → ANOMALY. Still too high.
    # Need to lower share or raise capital.
    # Smaller pool, smaller share, lower return %:
    # $300 pool, share 0.20, uptime 0.80, 30 days → $300 × 0.80 / 30 × 0.20 = $1.60/day
    # capital $47.50 → ev/cap = 3.4% < 5% ✓
    r = evaluate(
        _market(top_yes_size=1000, top_no_size=1000, volume_24h=500),
        _program(period_reward_cents=30_000, discount_factor_bps=0),
        now=NOW,
    )
    assert r.decision == Decision.PLAY, f"expected PLAY, got {r.decision} ({r.reason})"
    assert r.share == pytest.approx(0.20)  # proxy active
    assert r.ev_pct_of_capital <= 0.05


def test_skip_when_ev_below_floor_but_not_anomaly() -> None:
    """EV below min_ev_per_day but plausibly small → SKIP (not ANOMALY)."""
    r = evaluate(
        _market(top_yes_size=10_000, top_no_size=10_000, volume_24h=500),
        _program(period_reward_cents=1_000, discount_factor_bps=0),
        now=NOW,
    )
    assert r.decision == Decision.SKIP
    assert "ev_per_day" in r.reason


def test_play_capital_respects_risk_cap() -> None:
    """Even with target_size=1000, capital_locked never exceeds risk.py cap."""
    from kalshi_ws import risk

    r = evaluate(
        _market(top_yes_size=1000, top_no_size=1000, volume_24h=500),
        _program(target_size=1000, period_reward_cents=30_000, discount_factor_bps=0),
        now=NOW,
    )
    assert r.capital_locked <= float(risk.PER_MARKET_MAX_POSITION_USD)


def test_custom_magnitude_ceiling_overrides_default() -> None:
    """Operators can tighten the anomaly gate with GateParams."""
    strict = GateParams(magnitude_ceiling_pct=0.01)
    # A market that PLAYs at 5% ceiling should ANOMALY at 1%
    r_loose = evaluate(
        _market(top_yes_size=1000, top_no_size=1000, volume_24h=500),
        _program(period_reward_cents=30_000, discount_factor_bps=0),
        now=NOW,
    )
    r_tight = evaluate(
        _market(top_yes_size=1000, top_no_size=1000, volume_24h=500),
        _program(period_reward_cents=30_000, discount_factor_bps=0),
        now=NOW,
        gate=strict,
    )
    assert r_loose.decision == Decision.PLAY
    assert r_tight.decision == Decision.ANOMALY
