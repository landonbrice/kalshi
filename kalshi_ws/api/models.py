"""Typed pydantic models for Kalshi REST responses.

Models use `extra="allow"` so Kalshi adding fields never breaks parsing.
Scanners and decision-support code consume these models, never raw JSON dicts.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Market(BaseModel):
    """Subset of fields used by Phase 1 scanners. Extend as needs grow."""

    model_config = ConfigDict(extra="allow")

    ticker: str
    title: str
    status: str
    yes_bid: int = Field(default=0, description="Best YES bid in cents (0 if none).")
    yes_ask: int = Field(default=100, description="Best YES ask in cents (100 if none).")
    volume: int = 0
    open_interest: int = 0


class MarketsResponse(BaseModel):
    """Envelope returned by GET /markets."""

    model_config = ConfigDict(extra="allow")

    markets: list[Market]
    cursor: str | None = None
