from datetime import UTC, datetime, timedelta

import pytest

from kalshi_ws.intel.ev import (
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
    }
    base.update(overrides)
    return MarketSnapshot(**base)  # type: ignore[arg-type]


def _program(**overrides: object) -> LipProgram:
    base = {
        "market_ticker": "TEST-1",
        "period_reward_cents": 250_000,  # $2,500
        "target_size": 250.0,
        "end_date": NOW + timedelta(days=30),
    }
    base.update(overrides)
    return LipProgram(**base)  # type: ignore[arg-type]


def test_play_case_no_competitors() -> None:
    r = evaluate(_market(), _program(), now=NOW)
    assert r.play is True
    assert r.reason == "play"
    # Reward: $2500 / 30 days * share(1.0) ≈ $83.33/day
    assert r.reward_per_day == pytest.approx(2500 / 30, rel=1e-3)
    assert r.share == pytest.approx(1.0)
    # Capital: 250 * max(0.425, 0.575) = 250 * 0.575 = $143.75 → within $150 cap
    assert r.capital_locked == pytest.approx(250 * 0.575)
    # EV is rebate minus opp cost
    expected_opp = 250 * 0.575 * 0.10 / 365
    assert r.opp_cost_per_day == pytest.approx(expected_opp)
    assert r.ev_per_day == pytest.approx(r.reward_per_day - expected_opp)


def test_share_halves_when_competitor_matches_target() -> None:
    r = evaluate(
        _market(top_yes_size=250.0, top_no_size=250.0),
        _program(),
        now=NOW,
    )
    assert r.share == pytest.approx(0.5)


def test_share_one_third_when_competitor_double_target() -> None:
    r = evaluate(
        _market(top_yes_size=500.0, top_no_size=500.0),
        _program(),
        now=NOW,
    )
    assert r.share == pytest.approx(1 / 3, rel=1e-3)


def test_pass_when_not_active() -> None:
    r = evaluate(_market(status="closed"), _program(), now=NOW)
    assert r.play is False
    assert "status" in r.reason


def test_pass_when_spread_too_narrow() -> None:
    r = evaluate(_market(yes_bid=0.49, yes_ask=0.50), _program(), now=NOW)
    assert r.play is False
    assert "spread" in r.reason


def test_pass_when_one_sided_book() -> None:
    r = evaluate(_market(yes_bid=0.0, yes_ask=0.30), _program(), now=NOW)
    assert r.play is False
    assert "one-sided" in r.reason


def test_pass_when_days_remaining_too_short() -> None:
    r = evaluate(
        _market(),
        _program(end_date=NOW + timedelta(days=1)),
        now=NOW,
    )
    assert r.play is False
    assert "days_remaining" in r.reason


def test_pass_when_capital_too_high() -> None:
    # 500 contracts at mid 0.5 → $250 capital, above $150 cap
    r = evaluate(
        _market(),
        _program(target_size=500.0),
        now=NOW,
    )
    assert r.play is False
    assert "capital" in r.reason


def test_pass_when_ev_below_floor() -> None:
    # Tiny reward → EV negligible
    r = evaluate(
        _market(),
        _program(period_reward_cents=1_000),  # $10 over 30 days
        now=NOW,
    )
    assert r.play is False
    assert "ev_per_day" in r.reason


def test_custom_gate_overrides() -> None:
    # With a stricter EV floor, a marginal market should fail.
    strict = GateParams(min_ev_per_day=200.0)
    r = evaluate(_market(), _program(), now=NOW, gate=strict)
    assert r.play is False
    assert "ev_per_day" in r.reason
