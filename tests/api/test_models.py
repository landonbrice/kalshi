import pytest
from pydantic import ValidationError

from kalshi_ws.api.models import (
    IncentiveProgram,
    IncentiveProgramsResponse,
    Market,
    MarketsResponse,
    OrderbookSnapshot,
)


def test_market_parses_minimal_payload() -> None:
    payload = {
        "ticker": "KX-FOO",
        "title": "Foo market",
        "status": "active",
        "yes_bid": 42,
        "yes_ask": 45,
        "volume": 1000,
        "open_interest": 250,
    }
    m = Market.model_validate(payload)
    assert m.ticker == "KX-FOO"
    assert m.title == "Foo market"
    assert m.status == "active"
    assert m.yes_bid == 42
    assert m.yes_ask == 45
    assert m.volume == 1000
    assert m.open_interest == 250


def test_market_tolerates_extra_fields() -> None:
    payload = {
        "ticker": "KX-FOO",
        "title": "Foo",
        "status": "active",
        "yes_bid": 0,
        "yes_ask": 100,
        "volume": 0,
        "open_interest": 0,
        "made_up_field": "ignored",
        "another_extra": {"nested": 1},
    }
    m = Market.model_validate(payload)
    assert m.ticker == "KX-FOO"


def test_market_defaults_missing_optional_numeric_fields() -> None:
    """Some markets have no bids/asks. yes_bid=0 / yes_ask=100 are sensible defaults."""
    payload = {
        "ticker": "KX-FOO",
        "title": "Foo",
        "status": "active",
    }
    m = Market.model_validate(payload)
    assert m.yes_bid == 0
    assert m.yes_ask == 100
    assert m.volume == 0
    assert m.open_interest == 0


def test_market_missing_required_raises() -> None:
    with pytest.raises(ValidationError):
        Market.model_validate({"title": "no ticker"})


def test_market_coerces_volume_24h_from_volume_24h_fp() -> None:
    """Live Kalshi sends volume_24h_fp as a stringified float."""
    payload = {
        "ticker": "KX-FOO",
        "title": "Foo",
        "status": "active",
        "yes_bid_dollars": "0.4500",
        "yes_ask_dollars": "0.5500",
        "volume_24h_fp": "123.0",
    }
    m = Market.model_validate(payload)
    assert m.volume_24h == 123
    assert m.yes_bid == 45
    assert m.yes_ask == 55


def test_markets_response_parses_list_and_cursor() -> None:
    payload = {
        "markets": [
            {"ticker": "A", "title": "A title", "status": "active"},
            {"ticker": "B", "title": "B title", "status": "closed"},
        ],
        "cursor": "next-page-token",
    }
    resp = MarketsResponse.model_validate(payload)
    assert len(resp.markets) == 2
    assert resp.markets[0].ticker == "A"
    assert resp.cursor == "next-page-token"


def test_markets_response_cursor_optional() -> None:
    """The last page may omit cursor."""
    payload: dict[str, list[object]] = {"markets": []}
    resp = MarketsResponse.model_validate(payload)
    assert resp.markets == []
    assert resp.cursor is None


def test_incentive_program_parses_and_converts_target_size() -> None:
    payload = {
        "id": "abc-123",
        "market_ticker": "KX-FOO",
        "incentive_type": "liquidity",
        "period_reward": 250_000,
        "target_size_fp": "250.00",
        "start_date": "2026-05-15T21:05:00Z",
        "end_date": "2026-06-15T03:59:00Z",
        "paid_out": False,
        "discount_factor_bps": 5000,
    }
    p = IncentiveProgram.model_validate(payload)
    assert p.market_ticker == "KX-FOO"
    assert p.period_reward == 250_000
    assert p.target_size() == 250.0
    assert p.discount_factor_bps == 5000


def test_incentive_programs_response_pagination_shape() -> None:
    payload = {
        "incentive_programs": [
            {
                "id": "1",
                "market_ticker": "KX-A",
                "incentive_type": "liquidity",
                "period_reward": 100_000,
                "target_size_fp": "50",
                "start_date": "2026-05-15T00:00:00Z",
                "end_date": "2026-06-15T00:00:00Z",
            }
        ],
        "next_cursor": "page-2",
    }
    resp = IncentiveProgramsResponse.model_validate(payload)
    assert len(resp.incentive_programs) == 1
    assert resp.next_cursor == "page-2"


def test_orderbook_unwraps_orderbook_fp_envelope() -> None:
    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.4000", "10.0"], ["0.4500", "5.0"]],
            "no_dollars": [["0.5000", "200.00"]],
        }
    }
    ob = OrderbookSnapshot.model_validate(payload)
    # Top of book = highest price (last entry in ascending list)
    assert ob.best_yes_price() == 0.45
    assert ob.top_yes_size() == 5.0
    assert ob.best_no_price() == 0.5
    assert ob.top_no_size() == 200.0


def test_orderbook_handles_empty_sides() -> None:
    ob = OrderbookSnapshot.model_validate({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
    assert ob.top_yes_size() == 0.0
    assert ob.top_no_size() == 0.0
    assert ob.best_yes_price() == 0.0
