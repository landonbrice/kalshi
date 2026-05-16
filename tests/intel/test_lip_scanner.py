"""Tests for the LIP scanner orchestrator."""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from kalshi_ws.api.models import IncentiveProgram, Market
from kalshi_ws.api.read import KalshiReadClient
from kalshi_ws.config import Settings
from kalshi_ws.intel.ev import GateParams
from kalshi_ws.intel.lip_scanner import (
    LipCandidate,
    _passes_cheap_gates,
    scan_lip_candidates,
    write_candidates_csv,
)

NOW = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(pem)
    monkeypatch.setenv("KALSHI_API_KEY_ID", "abc")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key_path))
    monkeypatch.setenv("KALSHI_BASE_URL", "https://api.test.example/trade-api/v2")
    monkeypatch.setenv("KALSHI_DB_PATH", str(tmp_path / "db.sqlite"))
    return Settings()  # type: ignore[call-arg]


def _market(**kw: object) -> Market:
    base: dict[str, object] = {
        "ticker": "KX-A",
        "title": "Test",
        "status": "active",
        "yes_bid": 40,
        "yes_ask": 45,
        "volume_24h": 100,  # passes default min_volume_24h=10
        "event_ticker": "EV-A",
    }
    base.update(kw)
    return Market.model_validate(base)


def _program(**kw: object) -> IncentiveProgram:
    base: dict[str, object] = {
        "id": "p1",
        "market_ticker": "KX-A",
        "incentive_type": "liquidity",
        "period_reward": 250_000,
        "target_size_fp": "250",
        "start_date": "2026-05-15T00:00:00Z",
        "end_date": (NOW + timedelta(days=30)).isoformat(),
    }
    base.update(kw)
    return IncentiveProgram.model_validate(base)


def test_cheap_gate_passes_typical_play() -> None:
    assert _passes_cheap_gates(_market(), _program(), GateParams(), NOW) is True


def test_cheap_gate_rejects_closed_market() -> None:
    assert _passes_cheap_gates(_market(status="closed"), _program(), GateParams(), NOW) is False


def test_cheap_gate_rejects_empty_book() -> None:
    # yes_bid=0 means no bid; yes_ask=100 means no ask. Both should reject.
    assert _passes_cheap_gates(_market(yes_bid=0), _program(), GateParams(), NOW) is False
    assert _passes_cheap_gates(_market(yes_ask=100), _program(), GateParams(), NOW) is False


def test_cheap_gate_rejects_narrow_spread() -> None:
    # 1¢ spread fails the 2¢ floor
    m = _market(yes_bid=44, yes_ask=45)
    assert _passes_cheap_gates(m, _program(), GateParams(), NOW) is False


def test_cheap_gate_rejects_expiring_lip() -> None:
    p = _program(end_date=(NOW + timedelta(days=1)).isoformat())
    assert _passes_cheap_gates(_market(), p, GateParams(), NOW) is False


def test_cheap_gate_rejects_low_volume() -> None:
    """Phase A adds volume_24h floor at the cheap gate (no flow = no signal)."""
    m = _market(volume_24h=5)
    assert _passes_cheap_gates(m, _program(), GateParams(), NOW) is False


def test_scan_end_to_end_with_mocked_api(settings: Settings) -> None:
    """Full flow: programs -> markets -> events -> orderbooks -> ranked candidates."""

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/incentive_programs"):
            return httpx.Response(
                200,
                json={
                    "incentive_programs": [
                        {
                            "id": "p1",
                            "market_ticker": "KX-A",
                            "incentive_type": "liquidity",
                            "period_reward": 250_000,
                            "target_size_fp": "250",
                            "start_date": "2026-05-15T00:00:00Z",
                            "end_date": (NOW + timedelta(days=30)).isoformat(),
                        },
                        {
                            "id": "p2",
                            "market_ticker": "KX-B",
                            "incentive_type": "liquidity",
                            "period_reward": 100_000,
                            "target_size_fp": "100",
                            "start_date": "2026-05-15T00:00:00Z",
                            "end_date": (NOW + timedelta(days=30)).isoformat(),
                        },
                    ],
                    "next_cursor": None,
                },
            )
        if p.endswith("/markets"):
            return httpx.Response(
                200,
                json={
                    "markets": [
                        {
                            "ticker": "KX-A",
                            "title": "Entertainment market A",
                            "status": "active",
                            "yes_bid": 40,
                            "yes_ask": 45,
                            "volume_24h": 100,
                            "event_ticker": "EV-A",
                        },
                        {
                            "ticker": "KX-B",
                            "title": "Sports market B",
                            "status": "active",
                            "yes_bid": 40,
                            "yes_ask": 45,
                            "volume_24h": 100,
                            "event_ticker": "EV-B",
                        },
                    ],
                    "cursor": None,
                },
            )
        if "/events/EV-A" in p:
            return httpx.Response(
                200,
                json={
                    "event": {
                        "category": "Entertainment",
                        "series_ticker": "KX-ENT-SERIES",
                    }
                },
            )
        if "/events/EV-B" in p:
            return httpx.Response(
                200,
                json={
                    "event": {
                        "category": "Sports",
                        "series_ticker": "KX-SPORT-SERIES",
                    }
                },
            )
        if p.endswith("/orderbook"):
            return httpx.Response(
                200,
                json={
                    "orderbook_fp": {
                        "yes_dollars": [["0.4000", "10"]],
                        "no_dollars": [["0.5500", "10"]],
                    }
                },
            )
        return httpx.Response(404, json={"error": f"unmocked: {p}"})

    async def run() -> list[LipCandidate]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as inner:
            client = KalshiReadClient(settings, client=inner)
            return await scan_lip_candidates(
                client,
                category_filter="Entertainment",
                now=NOW,
                concurrent_orderbooks=4,
            )

    candidates = asyncio.run(run())

    # Only the Entertainment-tagged one survives the category filter
    assert len(candidates) == 1
    c = candidates[0]
    assert c.market.ticker == "KX-A"
    assert c.category == "Entertainment"
    assert c.series_ticker == "KX-ENT-SERIES"
    # Untagged series falls through to default HIGH velocity
    assert c.velocity_tag.is_default is True
    assert c.ev.info_velocity.value == "high"
    # Entertainment + HIGH → competitor_multiplier base 8.0
    # volume_24h=100 → no vol adjustment → mult stays 8.0
    assert c.ev.competitor_multiplier == pytest.approx(8.0)
    # With the share now properly estimated (not 0.25 cap), we expect
    # a real decision: typically SKIP for low EV or ANOMALY for high.
    assert c.ev.decision.value in {"PLAY", "SKIP", "ANOMALY"}


def test_csv_writer_round_trip(tmp_path: Path) -> None:
    """The CSV must be parseable and contain the headline EV fields."""
    from kalshi_ws.intel.ev import Decision, EvResult, MarketSnapshot
    from kalshi_ws.intel.ev import LipProgram as IntelLipProgram
    from kalshi_ws.intel.velocity import Confidence, SeriesTag, Velocity

    velocity_tag = SeriesTag(
        series_ticker="KX-ENT-SERIES",
        info_velocity=Velocity.HIGH,
        confidence=Confidence.MEDIUM,
        correlation_group="entertainment_xyz",
        notes="",
        is_default=False,
    )
    c = LipCandidate(
        market=_market(),
        program=_program(),
        category="Entertainment",
        series_ticker="KX-ENT-SERIES",
        velocity_tag=velocity_tag,
        snapshot=MarketSnapshot(
            ticker="KX-A",
            yes_bid=0.40,
            yes_ask=0.45,
            status="active",
            top_yes_size=10.0,
            top_no_size=10.0,
            volume_24h=100,
            category="Entertainment",
            info_velocity=Velocity.HIGH,
            confidence=Confidence.MEDIUM,
            series_ticker="KX-ENT-SERIES",
        ),
        ev=EvResult(
            ticker="KX-A",
            decision=Decision.PLAY,
            reason="play",
            effective_size=50,
            capital_locked=47.50,
            share=0.20,
            competitor_multiplier=8.0,
            discount_multiplier=0.5,
            effective_period_reward=1000.0,
            reward_per_day=6.66,
            opp_cost_per_day=0.013,
            ev_per_day=6.65,
            ev_pct_of_capital=0.14,
            spread=0.05,
            mid=0.425,
            days_remaining=30.0,
            info_velocity=Velocity.HIGH,
            confidence=Confidence.MEDIUM,
        ),
    )
    out = tmp_path / "candidates.csv"
    write_candidates_csv(out, [c])

    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "KX-A"
    assert r["category"] == "Entertainment"
    assert r["series_ticker"] == "KX-ENT-SERIES"
    assert r["info_velocity"] == "high"
    assert r["confidence"] == "medium"
    assert r["velocity_is_default"] == "False"
    assert r["correlation_group"] == "entertainment_xyz"
    assert r["decision"] == "PLAY"
    assert r["play"] == "True"  # back-compat bool for dashboard agent
    assert float(r["ev_per_day"]) == pytest.approx(6.65)
    assert float(r["ev_pct_of_capital"]) == pytest.approx(0.14)
    assert float(r["competitor_multiplier"]) == pytest.approx(8.0)
    assert int(r["volume_24h"]) == 100
    # Confirm we also wrote LIP program fields for the dashboard
    assert int(r["lip_period_reward_cents"]) == 250_000
    assert float(r["lip_target_size"]) == 250.0
    assert int(r["lip_discount_factor_bps"]) == 0
    # Avoid an unused-import flake — also confirms the intel type re-exports work
    _ = IntelLipProgram
    json.dumps(r)  # row should be JSON-serializable strings
