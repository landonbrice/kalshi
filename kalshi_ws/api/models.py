"""Typed pydantic models for Kalshi REST responses.

Models use `extra="allow"` so Kalshi adding fields never breaks parsing.
Scanners and decision-support code consume these models, never raw JSON dicts.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Market(BaseModel):
    """Subset of fields used by Phase 1 scanners. Extend as needs grow.

    Note on `yes_bid` / `yes_ask` defaults: Kalshi returns 0 and 100 (cents)
    for sides with no live quote. Scanners that rank by `yes_ask - yes_bid`
    MUST filter unquoted markets first (`yes_bid == 0 or yes_ask == 100`),
    otherwise empty books rank highest by spread. Don't tighten these defaults
    to non-zero values — real Kalshi markets legitimately publish 0/100.

    Live Kalshi v2 sends bids/asks as `yes_bid_dollars` / `yes_ask_dollars`
    string fields (e.g. "0.4500"). The before-validator converts those to
    integer cents so the rest of the codebase can treat prices uniformly.
    """

    model_config = ConfigDict(extra="allow")

    ticker: str
    title: str
    status: str
    yes_bid: int = Field(default=0, description="Best YES bid in cents (0 if no bid).")
    yes_ask: int = Field(default=100, description="Best YES ask in cents (100 if no ask).")
    volume: int = 0  # lifetime contracts
    volume_24h: int = 0  # contracts traded in the last 24h; 0 = stale / no flow
    open_interest: int = 0
    # Float mirrors of Kalshi's `_fp` fields; preserve decimal precision the
    # int fields above truncate. Dashboard consumes these via the CSV writer.
    volume_fp: float = 0.0
    volume_24h_fp: float = 0.0
    open_interest_fp: float = 0.0
    liquidity_dollars: float = 0.0
    event_ticker: str | None = None
    close_time: datetime | None = None
    category: str | None = None  # Filled from event lookup by the scanner.

    @model_validator(mode="before")
    @classmethod
    def _coerce_dollar_strings(cls, data: Any) -> Any:
        """Promote `yes_bid_dollars` / `yes_ask_dollars` strings to int cents."""
        if not isinstance(data, dict):
            return data
        if "yes_bid" not in data and "yes_bid_dollars" in data:
            with contextlib.suppress(TypeError, ValueError):
                data["yes_bid"] = round(float(data["yes_bid_dollars"]) * 100)
        if "yes_ask" not in data and "yes_ask_dollars" in data:
            with contextlib.suppress(TypeError, ValueError):
                # An ask of 0.0 means "no ask"; preserve that as 100 (default).
                ask_d = float(data["yes_ask_dollars"])
                if ask_d > 0:
                    data["yes_ask"] = round(ask_d * 100)
        if "volume" not in data and "volume_fp" in data:
            with contextlib.suppress(TypeError, ValueError):
                data["volume"] = int(float(data["volume_fp"]))
        if "volume_24h" not in data and "volume_24h_fp" in data:
            with contextlib.suppress(TypeError, ValueError):
                data["volume_24h"] = int(float(data["volume_24h_fp"]))
        return data


class MarketsResponse(BaseModel):
    """Envelope returned by GET /markets."""

    model_config = ConfigDict(extra="allow")

    markets: list[Market]
    cursor: str | None = None


class IncentiveProgram(BaseModel):
    """One LIP/VIP program tied to a market. Mirrors GET /incentive_programs entry."""

    model_config = ConfigDict(extra="allow")

    id: str
    market_ticker: str
    incentive_type: str  # "liquidity" or "volume"
    period_reward: int = 0  # cents
    target_size_fp: str = "0"  # string-encoded float; convert at use site
    start_date: datetime
    end_date: datetime
    paid_out: bool = False
    discount_factor_bps: int = 0

    def target_size(self) -> float:
        return float(self.target_size_fp)


class IncentiveProgramsResponse(BaseModel):
    """Envelope returned by GET /incentive_programs."""

    model_config = ConfigDict(extra="allow")

    incentive_programs: list[IncentiveProgram] = Field(default_factory=list)
    next_cursor: str | None = None


class OrderbookSnapshot(BaseModel):
    """Aggregated orderbook. Sides are [price_dollars, size] pairs, ascending by price."""

    model_config = ConfigDict(extra="allow")

    yes: list[list[str]] = Field(default_factory=list)
    no: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _unwrap_orderbook_fp(cls, data: Any) -> Any:
        # Kalshi nests the book under "orderbook_fp" with side keys "yes_dollars" / "no_dollars".
        if isinstance(data, dict) and "orderbook_fp" in data:
            ob = data.get("orderbook_fp") or {}
            return {
                "yes": ob.get("yes_dollars") or [],
                "no": ob.get("no_dollars") or [],
            }
        return data

    def top_yes_size(self) -> float:
        """Aggregate size at the best YES bid (highest yes price). 0 if empty."""
        return float(self.yes[-1][1]) if self.yes else 0.0

    def top_no_size(self) -> float:
        """Aggregate size at the best NO bid (highest no price). 0 if empty."""
        return float(self.no[-1][1]) if self.no else 0.0

    def best_yes_price(self) -> float:
        return float(self.yes[-1][0]) if self.yes else 0.0

    def best_no_price(self) -> float:
        return float(self.no[-1][0]) if self.no else 0.0
