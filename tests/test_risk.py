from kalshi_ws import risk


def test_limits_match_spec() -> None:
    # Values from docs/superpowers/specs/2026-05-14-kalshi-workstation-design.md §4
    assert risk.PER_MARKET_MAX_POSITION_USD == 50
    assert risk.SINGLE_ORDER_MAX_USD == 20
    assert risk.DAILY_LOSS_LIMIT_USD == 35
    assert risk.MAX_TOTAL_OPEN_EXPOSURE_USD == 400
    assert risk.MIN_TICKET_SIZE_USD == 1
    assert risk.SANCTIONED_MODE_MAX_DURATION_HOURS == 4
    assert risk.CONSECUTIVE_LOSSES_KILL == 5
