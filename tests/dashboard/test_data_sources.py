"""Unit tests for kalshi_ws.dashboard.data_sources."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kalshi_ws.dashboard.data_sources import (
    STALE_THRESHOLD_SECONDS,
    Candidate,
    concentration,
    fmt_book,
    fmt_depth,
    fmt_size,
    freshness,
    kalshi_market_url,
    load_candidates,
    load_meta,
    read_ledger,
    totals,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CSV_HEADER = (
    "ticker,title,category,play,reason,"
    "ev_per_day,reward_per_day,share,opp_cost_per_day,capital_locked,"
    "days_remaining,spread,mid,yes_bid_cents,yes_ask_cents,"
    "top_yes_size,top_no_size,lip_target_size,lip_period_reward_cents,lip_end_date"
)


def _csv_row(
    ticker: str = "KXFOO-25",
    title: str = "Foo market",
    category: str = "Sports",
    play: str = "True",
    reason: str = "play",
    ev_per_day: float = 10.0,
    **_kwargs: object,
) -> str:
    return (
        f"{ticker},{title},{category},{play},{reason},"
        f"{ev_per_day},5.0,0.8,1.0,100.0,"
        f"6.0,0.03,0.50,48,53,"
        f"20.0,30.0,10.0,3000,2026-05-21T00:00:00+00:00"
    )


def _make_csv(tmp_path: Path, rows: list[str]) -> Path:
    p = tmp_path / "lip_candidates.csv"
    p.write_text(_CSV_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def _make_meta(tmp_path: Path, scan_started_at: str, **extra: object) -> Path:
    meta = {
        "loop_started_at": "2026-05-15T00:00:00+00:00",
        "scan_started_at": scan_started_at,
        "scan_duration_seconds": 12.5,
        "iteration": 1,
        "candidates_total": 2,
        "candidates_play": 1,
        "candidates_pass": 1,
        "category_filter": None,
        **extra,
    }
    p = tmp_path / "lip_candidates.meta.json"
    p.write_text(json.dumps(meta), encoding="utf-8")
    return p


def _bootstrap_db(db_path: Path) -> None:
    """Create the schema tables so read_ledger can run queries."""
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
# load_candidates
# ---------------------------------------------------------------------------


def test_load_candidates_missing_file(tmp_path: Path) -> None:
    """Returns empty list when CSV doesn't exist."""
    result = load_candidates(tmp_path / "nonexistent.csv")
    assert result == []


def test_load_candidates_empty_csv(tmp_path: Path) -> None:
    """Returns empty list for a CSV with only a header row."""
    p = _make_csv(tmp_path, [])
    result = load_candidates(p)
    assert result == []


def test_load_candidates_single_play_row(tmp_path: Path) -> None:
    p = _make_csv(tmp_path, [_csv_row(ticker="KXBAR-25", ev_per_day=42.0)])
    rows = load_candidates(p)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "KXBAR-25"
    assert rows[0]["play"] is True
    assert rows[0]["ev_per_day"] == pytest.approx(42.0)


def test_load_candidates_sort_play_first_then_by_ev(tmp_path: Path) -> None:
    """PLAY rows first (descending ev_per_day), then PASS rows."""
    csv_rows = [
        _csv_row(ticker="PASS1", play="False", reason="low_ev", ev_per_day=5.0),
        _csv_row(ticker="PLAY_LOW", play="True", ev_per_day=3.0),
        _csv_row(ticker="PLAY_HIGH", play="True", ev_per_day=99.0),
        _csv_row(ticker="PASS2", play="False", reason="spread", ev_per_day=2.0),
    ]
    rows = load_candidates(_make_csv(tmp_path, csv_rows))
    tickers = [r["ticker"] for r in rows]
    assert tickers[0] == "PLAY_HIGH"
    assert tickers[1] == "PLAY_LOW"
    # PASS rows follow (any order among themselves by ev_per_day desc)
    assert tickers[2] == "PASS1"
    assert tickers[3] == "PASS2"


def test_load_candidates_play_false_string(tmp_path: Path) -> None:
    """'False' string is correctly parsed as not-play."""
    row = _csv_row(play="False", reason="spread_too_small")
    p = _make_csv(tmp_path, [row])
    rows = load_candidates(p)
    assert rows[0]["play"] is False


def test_load_candidates_forward_compatible_extra_column(tmp_path: Path) -> None:
    """Extra columns in the CSV don't raise an error."""
    header = _CSV_HEADER + ",future_column"
    row = _csv_row() + ",some_value"
    p = tmp_path / "lip_candidates.csv"
    p.write_text(header + "\n" + row + "\n", encoding="utf-8")
    rows = load_candidates(p)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# load_meta + freshness
# ---------------------------------------------------------------------------


def test_load_meta_missing_file(tmp_path: Path) -> None:
    result = load_meta(tmp_path / "no_such.meta.json")
    assert result is None


def test_load_meta_valid(tmp_path: Path) -> None:
    p = _make_meta(tmp_path, scan_started_at="2026-05-15T01:00:00+00:00")
    meta = load_meta(p)
    assert meta is not None
    assert meta["iteration"] == 1
    assert meta["candidates_play"] == 1
    assert meta["category_filter"] is None


def test_freshness_fresh(tmp_path: Path) -> None:
    now = datetime(2026, 5, 15, 1, 5, 0, tzinfo=UTC)
    scan_time = "2026-05-15T01:00:00+00:00"  # 5 min ago
    p = _make_meta(tmp_path, scan_started_at=scan_time)
    meta = load_meta(p)
    label, age = freshness(meta, now=now)
    assert label == "FRESH"
    assert age == pytest.approx(300.0)


def test_freshness_stale_old(tmp_path: Path) -> None:
    now = datetime(2026, 5, 15, 2, 0, 0, tzinfo=UTC)
    scan_time = "2026-05-15T01:00:00+00:00"  # 60 min ago -> STALE
    p = _make_meta(tmp_path, scan_started_at=scan_time)
    meta = load_meta(p)
    label, age = freshness(meta, now=now)
    assert label == "STALE"
    assert age > STALE_THRESHOLD_SECONDS


def test_freshness_stale_no_meta() -> None:
    label, age = freshness(None)
    assert label == "STALE"
    assert age == float("inf")


def test_freshness_boundary_exactly_stale() -> None:
    """Exactly at threshold+1 second should be STALE."""
    anchor = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
    scan_time = anchor - timedelta(seconds=STALE_THRESHOLD_SECONDS + 1)
    from kalshi_ws.dashboard.data_sources import Meta

    meta: Meta = {
        "loop_started_at": "",
        "scan_started_at": scan_time.isoformat(),
        "scan_duration_seconds": 0.0,
        "iteration": 1,
        "candidates_total": 0,
        "candidates_play": 0,
        "candidates_pass": 0,
        "category_filter": None,
    }
    label, _ = freshness(meta, now=anchor)
    assert label == "STALE"


def test_freshness_boundary_just_fresh() -> None:
    """One second under threshold should be FRESH."""
    anchor = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
    scan_time = anchor - timedelta(seconds=STALE_THRESHOLD_SECONDS - 1)
    from kalshi_ws.dashboard.data_sources import Meta

    meta: Meta = {
        "loop_started_at": "",
        "scan_started_at": scan_time.isoformat(),
        "scan_duration_seconds": 0.0,
        "iteration": 1,
        "candidates_total": 0,
        "candidates_play": 0,
        "candidates_pass": 0,
        "category_filter": None,
    }
    label, _ = freshness(meta, now=anchor)
    assert label == "FRESH"


# ---------------------------------------------------------------------------
# read_ledger
# ---------------------------------------------------------------------------


def test_read_ledger_missing_db(tmp_path: Path) -> None:
    """Missing DB file returns empty ledger, no exception."""
    ledger = read_ledger(tmp_path / "no.db")
    assert ledger["positions"] == []
    assert ledger["recent_fills"] == []
    assert ledger["rebates"] == []


def test_read_ledger_empty_tables(tmp_path: Path) -> None:
    """Bootstrapped-but-empty DB returns empty lists."""
    db_path = tmp_path / "kalshi.db"
    _bootstrap_db(db_path)
    ledger = read_ledger(db_path)
    assert ledger["positions"] == []
    assert ledger["recent_fills"] == []
    assert ledger["rebates"] == []


def test_read_ledger_with_data(tmp_path: Path) -> None:
    """Rows inserted into the DB are returned correctly."""
    db_path = tmp_path / "kalshi.db"
    _bootstrap_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?)",
        ("KXFOO-25", 10, 0, 48, 0, "2026-05-15T01:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "fill-1",
            "order-1",
            "KXFOO-25",
            "yes",
            "buy",
            48,
            10,
            0,
            "2026-05-15T01:00:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO rebates (ticker, period_start, period_end, amount_cents, source, recorded_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            "KXFOO-25",
            "2026-05-01",
            "2026-05-31",
            500,
            "LIP",
            "2026-05-15T01:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    ledger = read_ledger(db_path)
    assert len(ledger["positions"]) == 1
    assert ledger["positions"][0]["ticker"] == "KXFOO-25"
    assert ledger["positions"][0]["yes_qty"] == 10

    assert len(ledger["recent_fills"]) == 1
    assert ledger["recent_fills"][0]["fill_id"] == "fill-1"

    assert len(ledger["rebates"]) == 1
    assert ledger["rebates"][0]["amount_cents"] == 500


def test_read_ledger_fills_capped_at_20(tmp_path: Path) -> None:
    """Only the 20 most recent fills are returned."""
    db_path = tmp_path / "kalshi.db"
    _bootstrap_db(db_path)

    conn = sqlite3.connect(db_path)
    for i in range(25):
        conn.execute(
            "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"fill-{i:03d}",
                f"order-{i}",
                "KXFOO-25",
                "yes",
                "buy",
                48,
                1,
                0,
                f"2026-05-15T{i:02d}:00:00+00:00",
            ),
        )
    conn.commit()
    conn.close()

    ledger = read_ledger(db_path)
    assert len(ledger["recent_fills"]) == 20


# ---------------------------------------------------------------------------
# concentration
# ---------------------------------------------------------------------------


def _make_candidate(ticker: str, play: bool = True) -> Candidate:
    """Return a minimal Candidate-like dict for concentration/totals tests."""
    return Candidate(
        ticker=ticker,
        url=kalshi_market_url(ticker),
        title="Test",
        category="Sports",
        play=play,
        reason="play" if play else "pass",
        ev_per_day=5.0,
        reward_per_day=4.0,
        share=0.8,
        opp_cost_per_day=0.5,
        capital_locked=100.0,
        days_remaining=7.0,
        spread=0.03,
        mid=0.50,
        yes_bid_cents=48,
        yes_ask_cents=53,
        top_yes_size=20.0,
        top_no_size=30.0,
        lip_target_size=10.0,
        lip_period_reward_cents=3000,
        lip_end_date="2026-05-21T00:00:00+00:00",
        display_book=fmt_book(48, 53),
        display_depth=fmt_depth(20.0, 30.0),
        display_req=fmt_size(10.0),
    )


def test_concentration_above_threshold() -> None:
    """7-of-10 in same family → returns (family, 7, 10)."""
    rows = (
        [_make_candidate(f"KXMLBDEBUT-TWHITE-{i:02d}", play=True) for i in range(7)]
        + [_make_candidate(f"KXOTHER-{i:02d}", play=True) for i in range(3)]
    )
    result = concentration(rows)
    assert result is not None
    family, count, total = result
    assert family == "KXMLBDEBUT"
    assert count == 7
    assert total == 10


def test_concentration_below_threshold() -> None:
    """4-of-10 in same family (40%) → returns None.

    The remaining 6 rows each belong to a distinct family so no single family
    exceeds 50%.
    """
    rows = (
        [_make_candidate(f"KXMLBDEBUT-TWHITE-{i:02d}", play=True) for i in range(4)]
        + [_make_candidate(f"KXFAMILY{i:02d}-26NOV01", play=True) for i in range(6)]
    )
    result = concentration(rows)
    assert result is None


def test_concentration_zero_plays() -> None:
    """No PLAY rows → returns None."""
    rows = [_make_candidate(f"KXFOO-{i:02d}", play=False) for i in range(5)]
    result = concentration(rows)
    assert result is None


# ---------------------------------------------------------------------------
# totals
# ---------------------------------------------------------------------------


def test_totals_sums_play_rows_only() -> None:
    """totals() sums capital and ev from PLAY rows, skipping PASS rows."""
    play1 = _make_candidate("KXFOO-01", play=True)
    play2 = _make_candidate("KXFOO-02", play=True)
    pass1 = _make_candidate("KXBAR-01", play=False)

    result = totals([play1, play2, pass1])
    assert result["play_count"] == 2
    assert result["capital"] == pytest.approx(200.0)
    assert result["ev_per_day"] == pytest.approx(10.0)


def test_totals_zero_plays() -> None:
    """Empty list → all zeros with play_count=0."""
    result = totals([])
    assert result["play_count"] == 0
    assert result["capital"] == pytest.approx(0.0)
    assert result["ev_per_day"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# kalshi_market_url
# ---------------------------------------------------------------------------


def test_kalshi_market_url_three_segment_ticker() -> None:
    url = kalshi_market_url("KXNBARETURN-26OKCJWILLIAMS8-519")
    assert url == "https://kalshi.com/markets/kxnbareturn/x/kxnbareturn-26okcjwilliams8"


def test_kalshi_market_url_two_segment_ticker() -> None:
    # IIHF-style: KXIIHF-26-CAN -> series=kxiihf, event=kxiihf-26
    url = kalshi_market_url("KXIIHF-26-CAN")
    assert url == "https://kalshi.com/markets/kxiihf/x/kxiihf-26"


def test_kalshi_market_url_single_segment_ticker() -> None:
    # Defensive: no hyphens -> series page only.
    url = kalshi_market_url("KXFOO")
    assert url == "https://kalshi.com/markets/kxfoo"


# ---------------------------------------------------------------------------
# fmt_size / fmt_book / fmt_depth
# ---------------------------------------------------------------------------


def test_fmt_size_integer() -> None:
    assert fmt_size(250.0) == "250"


def test_fmt_size_float() -> None:
    assert fmt_size(4.91) == "4.91"


def test_fmt_size_zero() -> None:
    assert fmt_size(0) == "—"


def test_fmt_book_normal() -> None:
    assert fmt_book(27, 71) == "0.27 / 0.71"


def test_fmt_book_zero_bid_ask() -> None:
    assert fmt_book(0, 0) == "—"


def test_fmt_book_hundred_bid_ask() -> None:
    assert fmt_book(100, 100) == "—"


def test_fmt_book_mixed_unquoted() -> None:
    # Both must be in (0, 100) to return dash; if only one is, render normally
    assert fmt_book(0, 53) == "0.00 / 0.53"


def test_fmt_depth_normal() -> None:
    assert fmt_depth(20.0, 30.0) == "20 × 30"


def test_fmt_depth_float() -> None:
    assert fmt_depth(4.91, 3.5) == "4.91 × 3.5"


def test_fmt_depth_zero() -> None:
    assert fmt_depth(0, 0) == "—"
