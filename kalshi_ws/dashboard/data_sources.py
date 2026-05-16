"""Pure reader functions for the LIP dashboard.

All functions are synchronous and side-effect-free.  They read from files /
SQLite and return plain data structures.  The server calls these in ordinary
(non-async) route handlers because the I/O is cheap.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

STALE_THRESHOLD_SECONDS: int = 30 * 60  # 30 minutes


class Candidate(TypedDict):
    """One row from lip_candidates.csv.  Unknown columns are silently ignored."""

    ticker: str
    url: str
    title: str
    category: str
    play: bool  # True == PLAY, False == PASS
    reason: str
    ev_per_day: float
    reward_per_day: float
    share: float
    opp_cost_per_day: float
    capital_locked: float
    days_remaining: float
    spread: float
    mid: float
    yes_bid_cents: int
    yes_ask_cents: int
    top_yes_size: float
    top_no_size: float
    lip_target_size: float
    lip_period_reward_cents: int
    lip_end_date: str
    # Pre-formatted display strings for new dashboard columns
    display_book: str
    display_depth: str
    display_req: str


class Meta(TypedDict):
    """Parsed lip_candidates.meta.json."""

    loop_started_at: str
    scan_started_at: str
    scan_duration_seconds: float
    iteration: int
    candidates_total: int
    candidates_play: int
    candidates_pass: int
    category_filter: str | None


class Position(TypedDict):
    ticker: str
    yes_qty: int
    no_qty: int
    avg_yes_cost_cents: int
    avg_no_cost_cents: int
    updated_at: str


class Fill(TypedDict):
    fill_id: str
    order_id: str
    ticker: str
    side: str
    action: str
    price_cents: int
    qty: int
    fee_cents: int
    filled_at: str


class Rebate(TypedDict):
    id: int
    ticker: str
    period_start: str
    period_end: str
    amount_cents: int
    source: str
    recorded_at: str


class Ledger(TypedDict):
    positions: list[Position]
    recent_fills: list[Fill]
    rebates: list[Rebate]


# ---------------------------------------------------------------------------
# CSV reader
# ---------------------------------------------------------------------------


def _parse_float(val: str) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _parse_int(val: str) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Display formatters
# ---------------------------------------------------------------------------


def fmt_size(n: float) -> str:
    """Format a size number: strips trailing zeros, returns '—' for zero."""
    if n == 0:
        return "—"
    return f"{n:g}"


def fmt_book(bid_cents: int, ask_cents: int) -> str:
    """Format bid/ask as '0.27 / 0.71', or '—' when both are unquoted (0 or 100)."""
    if bid_cents in (0, 100) and ask_cents in (0, 100):
        return "—"
    return f"{bid_cents / 100:.2f} / {ask_cents / 100:.2f}"


def fmt_depth(yes_size: float, no_size: float) -> str:
    """Format top-of-book sizes as 'Y × N', or '—' when both are zero."""
    if yes_size == 0 and no_size == 0:
        return "—"
    return f"{yes_size:g} × {no_size:g}"


def load_candidates(csv_path: Path) -> list[Candidate]:
    """Read lip_candidates.csv and return rows sorted PLAY-first by ev_per_day desc.

    Returns an empty list if the file does not exist.  Unknown CSV columns are
    ignored so forward-compatible with scanner additions.
    """
    if not csv_path.exists():
        return []

    rows: list[Candidate] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            play_val = raw.get("play", "False")
            is_play = play_val.strip().lower() in {"true", "1", "yes"}
            ticker_val = raw.get("ticker", "")
            yes_bid = _parse_int(raw.get("yes_bid_cents", "0"))
            yes_ask = _parse_int(raw.get("yes_ask_cents", "0"))
            top_yes = _parse_float(raw.get("top_yes_size", "0"))
            top_no = _parse_float(raw.get("top_no_size", "0"))
            lip_target = _parse_float(raw.get("lip_target_size", "0"))
            candidate: Candidate = {
                "ticker": ticker_val,
                "url": kalshi_market_url(ticker_val),
                "title": raw.get("title", ""),
                "category": raw.get("category", ""),
                "play": is_play,
                "reason": raw.get("reason", ""),
                "ev_per_day": _parse_float(raw.get("ev_per_day", "0")),
                "reward_per_day": _parse_float(raw.get("reward_per_day", "0")),
                "share": _parse_float(raw.get("share", "0")),
                "opp_cost_per_day": _parse_float(raw.get("opp_cost_per_day", "0")),
                "capital_locked": _parse_float(raw.get("capital_locked", "0")),
                "days_remaining": _parse_float(raw.get("days_remaining", "0")),
                "spread": _parse_float(raw.get("spread", "0")),
                "mid": _parse_float(raw.get("mid", "0")),
                "yes_bid_cents": yes_bid,
                "yes_ask_cents": yes_ask,
                "top_yes_size": top_yes,
                "top_no_size": top_no,
                "lip_target_size": lip_target,
                "lip_period_reward_cents": _parse_int(
                    raw.get("lip_period_reward_cents", "0")
                ),
                "lip_end_date": raw.get("lip_end_date", ""),
                "display_book": fmt_book(yes_bid, yes_ask),
                "display_depth": fmt_depth(top_yes, top_no),
                "display_req": fmt_size(lip_target),
            }
            rows.append(candidate)

    # Default sort: PLAY first (descending ev_per_day), then PASS (descending ev_per_day)
    rows.sort(key=lambda c: (not c["play"], -c["ev_per_day"]))
    return rows


# ---------------------------------------------------------------------------
# Meta sidecar reader
# ---------------------------------------------------------------------------


def load_meta(json_path: Path) -> Meta | None:
    """Return parsed meta sidecar, or None if the file is missing / unreadable."""
    if not json_path.exists():
        return None
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    return Meta(
        loop_started_at=raw.get("loop_started_at", ""),
        scan_started_at=raw.get("scan_started_at", ""),
        scan_duration_seconds=float(raw.get("scan_duration_seconds", 0.0)),
        iteration=int(raw.get("iteration", 0)),
        candidates_total=int(raw.get("candidates_total", 0)),
        candidates_play=int(raw.get("candidates_play", 0)),
        candidates_pass=int(raw.get("candidates_pass", 0)),
        category_filter=raw.get("category_filter"),
    )


def freshness(
    meta: Meta | None,
    now: datetime | None = None,
) -> tuple[str, float]:
    """Return (label, age_seconds) where label is 'FRESH' or 'STALE'.

    - If meta is None or scan_started_at is missing/unparseable -> ('STALE', inf)
    - If age > STALE_THRESHOLD_SECONDS (30 min) -> ('STALE', age)
    - Otherwise -> ('FRESH', age)
    """
    if now is None:
        now = datetime.now(UTC)

    if meta is None:
        return ("STALE", float("inf"))

    scan_str = meta.get("scan_started_at", "")
    if not scan_str:
        return ("STALE", float("inf"))

    try:
        scan_dt = datetime.fromisoformat(scan_str)
        # Ensure timezone-aware
        if scan_dt.tzinfo is None:
            scan_dt = scan_dt.replace(tzinfo=UTC)
        age = (now - scan_dt).total_seconds()
    except ValueError:
        return ("STALE", float("inf"))

    label = "STALE" if age > STALE_THRESHOLD_SECONDS else "FRESH"
    return (label, age)


# ---------------------------------------------------------------------------
# SQLite ledger reader
# ---------------------------------------------------------------------------


def read_ledger(db_path: Path) -> Ledger:
    """Read positions, recent 20 fills, and rebates from the SQLite ledger.

    Returns empty lists for any table that doesn't exist or has no rows.
    Never raises; all exceptions are swallowed with empty results.
    """
    empty: Ledger = {"positions": [], "recent_fills": [], "rebates": []}

    if not db_path.exists():
        return empty

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            positions = _query_positions(conn)
            recent_fills = _query_fills(conn, limit=20)
            rebates = _query_rebates(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return empty

    return Ledger(positions=positions, recent_fills=recent_fills, rebates=rebates)


def _query_positions(conn: sqlite3.Connection) -> list[Position]:
    try:
        rows = conn.execute(
            "SELECT ticker, yes_qty, no_qty, avg_yes_cost_cents, avg_no_cost_cents, updated_at"
            " FROM positions"
        ).fetchall()
        return [
            Position(
                ticker=r["ticker"],
                yes_qty=r["yes_qty"],
                no_qty=r["no_qty"],
                avg_yes_cost_cents=r["avg_yes_cost_cents"],
                avg_no_cost_cents=r["avg_no_cost_cents"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]
    except sqlite3.Error:
        return []


def _query_fills(conn: sqlite3.Connection, limit: int = 20) -> list[Fill]:
    try:
        rows = conn.execute(
            "SELECT fill_id, order_id, ticker, side, action, price_cents, qty, fee_cents, filled_at"
            " FROM fills"
            " ORDER BY filled_at DESC"
            " LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Fill(
                fill_id=r["fill_id"],
                order_id=r["order_id"],
                ticker=r["ticker"],
                side=r["side"],
                action=r["action"],
                price_cents=r["price_cents"],
                qty=r["qty"],
                fee_cents=r["fee_cents"],
                filled_at=r["filled_at"],
            )
            for r in rows
        ]
    except sqlite3.Error:
        return []


# ---------------------------------------------------------------------------
# Aggregate helpers
# ---------------------------------------------------------------------------


def kalshi_market_url(ticker: str) -> str:
    """Build a Kalshi frontend URL for a given market ticker.

    Pattern: https://kalshi.com/markets/<series>/x/<event> (all lowercase).
    The middle 'x' is a placeholder; Kalshi's router routes by series + event IDs
    and ignores the slug. Example:
        ticker=KXNBARETURN-26OKCJWILLIAMS8-519
          -> series=kxnbareturn, event=kxnbareturn-26okcjwilliams8
          -> https://kalshi.com/markets/kxnbareturn/x/kxnbareturn-26okcjwilliams8

    For tickers without hyphens (rare), returns the series-only page.
    """
    parts = ticker.split("-")
    series = parts[0].lower()
    if len(parts) <= 1:
        return f"https://kalshi.com/markets/{series}"
    event = "-".join(parts[:-1]).lower()
    return f"https://kalshi.com/markets/{series}/x/{event}"


def concentration(rows: list[Candidate]) -> tuple[str, int, int] | None:
    """Return (family, count, total_play) when the largest family is > 50% of plays.

    Family is derived from the first hyphen-separated component of ticker.
    Returns None when no family exceeds the threshold or there are no PLAY rows.
    """
    play_rows = [r for r in rows if r["play"]]
    total_play = len(play_rows)
    if total_play == 0:
        return None

    family_counts: dict[str, int] = {}
    for r in play_rows:
        family = r["ticker"].split("-")[0] if r["ticker"] else ""
        family_counts[family] = family_counts.get(family, 0) + 1

    top_family = max(family_counts, key=lambda k: family_counts[k])
    top_count = family_counts[top_family]

    if top_count / total_play > 0.5:
        return (top_family, top_count, total_play)
    return None


def totals(rows: list[Candidate]) -> dict[str, float | int]:
    """Sum capital_locked and ev_per_day over PLAY rows.

    Returns {"capital": float, "ev_per_day": float, "play_count": int}.
    """
    play_rows = [r for r in rows if r["play"]]
    return {
        "capital": sum(r["capital_locked"] for r in play_rows),
        "ev_per_day": sum(r["ev_per_day"] for r in play_rows),
        "play_count": len(play_rows),
    }


def risk_usage(ledger: Ledger) -> dict[str, float]:
    """Compute current usage of risk rails from ledger positions.

    Phase 1: positions may be empty; placeholders return 0.
    - per_market: largest single-position notional exposure
    - total_open: sum of all position notional (worst-case 100¢ per contract)
    - daily_loss: 0 (requires fills + intraday math — TODO Phase 2)
    - single_order: 0 (not tracked yet — TODO Phase 2)
    """
    positions = ledger["positions"]
    if not positions:
        return {
            "per_market": 0.0,
            "total_open": 0.0,
            "daily_loss": 0.0,  # TODO Phase 2
            "single_order": 0.0,  # TODO Phase 2
        }

    per_market_values: list[float] = []
    total_open = 0.0
    for p in positions:
        # Worst-case: each contract worth $1.00 (100¢)
        notional = (p["yes_qty"] + p["no_qty"]) * 1.0
        per_market_values.append(notional)
        total_open += notional

    return {
        "per_market": max(per_market_values),
        "total_open": total_open,
        "daily_loss": 0.0,  # TODO Phase 2
        "single_order": 0.0,  # TODO Phase 2
    }


def _query_rebates(conn: sqlite3.Connection) -> list[Rebate]:
    try:
        rows = conn.execute(
            "SELECT id, ticker, period_start, period_end, amount_cents, source, recorded_at"
            " FROM rebates"
            " ORDER BY recorded_at DESC"
        ).fetchall()
        return [
            Rebate(
                id=r["id"],
                ticker=r["ticker"],
                period_start=r["period_start"],
                period_end=r["period_end"],
                amount_cents=r["amount_cents"],
                source=r["source"],
                recorded_at=r["recorded_at"],
            )
            for r in rows
        ]
    except sqlite3.Error:
        return []
