import pytest
from pydantic import ValidationError

from kalshi_ws.api.models import Market, MarketsResponse


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
