"""FastAPI TestClient tests for kalshi_ws.dashboard.server."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers shared with test_data_sources
# ---------------------------------------------------------------------------

_CSV_HEADER = (
    "ticker,title,category,play,reason,"
    "ev_per_day,reward_per_day,share,opp_cost_per_day,capital_locked,"
    "days_remaining,spread,mid,yes_bid_cents,yes_ask_cents,"
    "top_yes_size,top_no_size,lip_target_size,lip_period_reward_cents,lip_end_date"
)


def _csv_row(
    ticker: str = "KXFOO-25",
    title: str = "Foo market title",
    play: str = "True",
    ev_per_day: float = 42.5,
) -> str:
    return (
        f"{ticker},{title},Sports,{play},play,"
        f"{ev_per_day},5.0,0.8,1.0,100.0,"
        f"6.0,0.03,0.50,48,53,"
        f"20.0,30.0,10.0,3000,2026-05-21T00:00:00+00:00"
    )


def _bootstrap_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS positions (
            ticker TEXT PRIMARY KEY,
            yes_qty INTEGER NOT NULL DEFAULT 0,
            no_qty INTEGER NOT NULL DEFAULT 0,
            avg_yes_cost_cents INTEGER NOT NULL DEFAULT 0,
            avg_no_cost_cents INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fills (
            fill_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            action TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            qty INTEGER NOT NULL,
            fee_cents INTEGER NOT NULL DEFAULT 0,
            filled_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rebates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            source TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixture: patch data paths inside the server module
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Return a TestClient with all data paths redirected to tmp_path."""
    import kalshi_ws.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_CSV_PATH", tmp_path / "lip_candidates.csv")
    monkeypatch.setattr(
        server_mod, "_META_PATH", tmp_path / "lip_candidates.meta.json"
    )
    monkeypatch.setattr(server_mod, "_DB_PATH", tmp_path / "kalshi.db")

    # Bootstrap an empty db so ledger queries don't fail
    _bootstrap_db(tmp_path / "kalshi.db")

    return TestClient(server_mod.app)


@pytest.fixture()
def client_with_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with CSV + meta + bootstrapped DB containing one position."""
    import kalshi_ws.dashboard.server as server_mod

    csv_path = tmp_path / "lip_candidates.csv"
    meta_path = tmp_path / "lip_candidates.meta.json"
    db_path = tmp_path / "kalshi.db"

    # Write a two-row CSV (one PLAY, one PASS)
    csv_path.write_text(
        _CSV_HEADER + "\n" + _csv_row(ev_per_day=387.78) + "\n"
        + _csv_row(ticker="KXBAR-25", play="False", ev_per_day=5.0) + "\n",
        encoding="utf-8",
    )

    # Write a fresh meta sidecar
    meta = {
        "loop_started_at": "2026-05-15T01:00:00+00:00",
        "scan_started_at": "2026-05-15T01:05:00+00:00",
        "scan_duration_seconds": 15.0,
        "iteration": 1,
        "candidates_total": 2,
        "candidates_play": 1,
        "candidates_pass": 1,
        "category_filter": None,
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    _bootstrap_db(db_path)

    monkeypatch.setattr(server_mod, "_CSV_PATH", csv_path)
    monkeypatch.setattr(server_mod, "_META_PATH", meta_path)
    monkeypatch.setattr(server_mod, "_DB_PATH", db_path)

    return TestClient(server_mod.app)


# ---------------------------------------------------------------------------
# Tests: GET /
# ---------------------------------------------------------------------------


def test_index_returns_200(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200


def test_index_contains_page_title(client: TestClient) -> None:
    resp = client.get("/")
    assert "Kalshi Workstation" in resp.text


def test_index_contains_risk_constant(client: TestClient) -> None:
    """PER_MARKET_MAX_POSITION_USD value (50) must appear in the page."""
    resp = client.get("/")
    assert "50" in resp.text


def test_index_no_scanner_data_shows_badge(client: TestClient) -> None:
    """When CSV and meta are absent the page shows the no-data badge text."""
    resp = client.get("/")
    assert "no scanner data yet" in resp.text


def test_index_with_data_shows_fresh_badge(client_with_data: TestClient) -> None:
    """With a recent meta sidecar the page shows FRESH or STALE."""
    resp = client_with_data.get("/")
    assert resp.status_code == 200
    # The meta exists; freshness badge must appear (either FRESH or STALE)
    assert ("FRESH" in resp.text or "STALE" in resp.text)


def test_index_with_data_shows_candidate(client_with_data: TestClient) -> None:
    resp = client_with_data.get("/")
    assert "KXFOO-25" in resp.text
    # EV formatted as $387.78
    assert "387.78" in resp.text


def test_index_empty_ledger_no_exception(client: TestClient) -> None:
    """Empty DB must produce a 200, not a 500."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "none yet" in resp.text


def test_index_html_content_type(client: TestClient) -> None:
    resp = client.get("/")
    assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Tests: JSON endpoints
# ---------------------------------------------------------------------------


def test_api_candidates_empty(client: TestClient) -> None:
    resp = client.get("/api/candidates")
    assert resp.status_code == 200
    data = resp.json()
    assert "candidates" in data
    assert data["candidates"] == []


def test_api_candidates_with_data(client_with_data: TestClient) -> None:
    resp = client_with_data.get("/api/candidates")
    assert resp.status_code == 200
    cands = resp.json()["candidates"]
    assert len(cands) == 1  # only the PLAY row
    assert cands[0]["ticker"] == "KXFOO-25"


def test_api_meta_no_file(client: TestClient) -> None:
    resp = client.get("/api/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"] is None
    assert data["freshness_label"] == "STALE"


def test_api_meta_with_data(client_with_data: TestClient) -> None:
    resp = client_with_data.get("/api/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"] is not None
    assert data["freshness_label"] in {"FRESH", "STALE"}


def test_api_ledger_empty(client: TestClient) -> None:
    resp = client.get("/api/ledger")
    assert resp.status_code == 200
    data = resp.json()
    assert data["positions"] == []
    assert data["recent_fills"] == []
    assert data["rebates"] == []


# ---------------------------------------------------------------------------
# Fixtures for new feature tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def client_concentrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with 4-of-5 rows sharing the same ticker family."""
    import kalshi_ws.dashboard.server as server_mod

    csv_path = tmp_path / "lip_candidates.csv"
    meta_path = tmp_path / "lip_candidates.meta.json"
    db_path = tmp_path / "kalshi.db"

    rows = [
        _csv_row(ticker=f"KXMLBDEBUT-PLAYER{i}-26NOV01", play="True")
        for i in range(4)
    ] + [_csv_row(ticker="KXOTHER-26JUN01", play="True")]

    csv_path.write_text(
        _CSV_HEADER + "\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    meta = {
        "loop_started_at": "2026-05-15T01:00:00+00:00",
        "scan_started_at": "2026-05-15T01:05:00+00:00",
        "scan_duration_seconds": 10.0,
        "iteration": 3,
        "candidates_total": 5,
        "candidates_play": 5,
        "candidates_pass": 0,
        "category_filter": None,
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    _bootstrap_db(db_path)

    monkeypatch.setattr(server_mod, "_CSV_PATH", csv_path)
    monkeypatch.setattr(server_mod, "_META_PATH", meta_path)
    monkeypatch.setattr(server_mod, "_DB_PATH", db_path)

    return TestClient(server_mod.app)


@pytest.fixture()
def client_diverse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with 5 rows each in a distinct ticker family."""
    import kalshi_ws.dashboard.server as server_mod

    csv_path = tmp_path / "lip_candidates.csv"
    meta_path = tmp_path / "lip_candidates.meta.json"
    db_path = tmp_path / "kalshi.db"

    rows = [
        _csv_row(ticker=f"KXFAMILY{i}-26NOV01", play="True")
        for i in range(5)
    ]
    csv_path.write_text(
        _CSV_HEADER + "\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    meta = {
        "loop_started_at": "2026-05-15T01:00:00+00:00",
        "scan_started_at": "2026-05-15T01:05:00+00:00",
        "scan_duration_seconds": 8.0,
        "iteration": 2,
        "candidates_total": 5,
        "candidates_play": 5,
        "candidates_pass": 0,
        "category_filter": None,
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    _bootstrap_db(db_path)

    monkeypatch.setattr(server_mod, "_CSV_PATH", csv_path)
    monkeypatch.setattr(server_mod, "_META_PATH", meta_path)
    monkeypatch.setattr(server_mod, "_DB_PATH", db_path)

    return TestClient(server_mod.app)


@pytest.fixture()
def client_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with a STALE meta sidecar (scan >30 min ago)."""
    import kalshi_ws.dashboard.server as server_mod

    csv_path = tmp_path / "lip_candidates.csv"
    meta_path = tmp_path / "lip_candidates.meta.json"
    db_path = tmp_path / "kalshi.db"

    csv_path.write_text(
        _CSV_HEADER + "\n" + _csv_row() + "\n",
        encoding="utf-8",
    )
    meta = {
        "loop_started_at": "2026-05-15T00:00:00+00:00",
        "scan_started_at": "2026-05-14T00:00:00+00:00",  # >24 h ago → definitely STALE
        "scan_duration_seconds": 20.0,
        "iteration": 7,
        "candidates_total": 1,
        "candidates_play": 1,
        "candidates_pass": 0,
        "category_filter": None,
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    _bootstrap_db(db_path)

    monkeypatch.setattr(server_mod, "_CSV_PATH", csv_path)
    monkeypatch.setattr(server_mod, "_META_PATH", meta_path)
    monkeypatch.setattr(server_mod, "_DB_PATH", db_path)

    return TestClient(server_mod.app)


@pytest.fixture()
def client_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with a FRESH meta sidecar (scan just happened)."""
    from datetime import UTC, datetime, timedelta

    import kalshi_ws.dashboard.server as server_mod

    csv_path = tmp_path / "lip_candidates.csv"
    meta_path = tmp_path / "lip_candidates.meta.json"
    db_path = tmp_path / "kalshi.db"

    csv_path.write_text(
        _CSV_HEADER + "\n" + _csv_row() + "\n",
        encoding="utf-8",
    )
    # Scan started 5 minutes ago — well within the 30-min threshold
    fresh_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    meta = {
        "loop_started_at": "2026-05-15T00:00:00+00:00",
        "scan_started_at": fresh_time,
        "scan_duration_seconds": 11.0,
        "iteration": 4,
        "candidates_total": 1,
        "candidates_play": 1,
        "candidates_pass": 0,
        "category_filter": None,
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    _bootstrap_db(db_path)

    monkeypatch.setattr(server_mod, "_CSV_PATH", csv_path)
    monkeypatch.setattr(server_mod, "_META_PATH", meta_path)
    monkeypatch.setattr(server_mod, "_DB_PATH", db_path)

    return TestClient(server_mod.app)


# ---------------------------------------------------------------------------
# Tests: new UI features
# ---------------------------------------------------------------------------


def test_index_renders_category_badge(client_with_data: TestClient) -> None:
    """Category badge class must appear in the response HTML."""
    resp = client_with_data.get("/")
    assert resp.status_code == 200
    assert "badge-cat" in resp.text


def test_index_clickable_ticker_anchor(client_with_data: TestClient) -> None:
    """Each ticker must link to kalshi.com/markets/<series>/x/<event>."""
    resp = client_with_data.get("/")
    assert resp.status_code == 200
    assert "kalshi.com/markets/" in resp.text
    assert "/x/" in resp.text  # placeholder slug pattern — new series/x/event format


def test_index_concentration_callout_when_concentrated(
    client_concentrated: TestClient,
) -> None:
    """When >50% of PLAYs share a family, 'share event family' appears."""
    resp = client_concentrated.get("/")
    assert resp.status_code == 200
    assert "share event family" in resp.text


def test_index_no_concentration_callout_when_diverse(
    client_diverse: TestClient,
) -> None:
    """When no family exceeds 50%, the callout must NOT appear."""
    resp = client_diverse.get("/")
    assert resp.status_code == 200
    assert "share event family" not in resp.text


def test_index_totals_row_present(client_with_data: TestClient) -> None:
    """'TOTAL' must appear in the candidates table footer."""
    resp = client_with_data.get("/")
    assert resp.status_code == 200
    assert "TOTAL" in resp.text


def test_index_stale_shows_launch_command(client_stale: TestClient) -> None:
    """STALE page must contain the scan-lip-loop launch command."""
    resp = client_stale.get("/")
    assert resp.status_code == 200
    assert "scan-lip-loop" in resp.text


def test_index_fresh_hides_launch_command(client_fresh: TestClient) -> None:
    """FRESH page must NOT contain the scan-lip-loop launch command."""
    resp = client_fresh.get("/")
    assert resp.status_code == 200
    assert "scan-lip-loop" not in resp.text


def test_index_shows_iteration_when_meta_present(client_with_data: TestClient) -> None:
    """'iter ' text must appear when meta is present."""
    resp = client_with_data.get("/")
    assert resp.status_code == 200
    assert "iter " in resp.text
