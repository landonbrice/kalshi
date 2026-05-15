"""SQLite ledger schema. Source of truth for all persisted workstation state."""

from __future__ import annotations

from pathlib import Path

from kalshi_ws.state.db import open_connection

EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "orders",
        "fills",
        "positions",
        "quotes",
        "session_log",
        "params",
        "rebates",
    }
)

_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        side TEXT NOT NULL CHECK (side IN ('yes', 'no')),
        action TEXT NOT NULL CHECK (action IN ('buy', 'sell')),
        price_cents INTEGER NOT NULL,
        qty INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fills (
        fill_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        side TEXT NOT NULL,
        action TEXT NOT NULL,
        price_cents INTEGER NOT NULL,
        qty INTEGER NOT NULL,
        fee_cents INTEGER NOT NULL DEFAULT 0,
        filled_at TEXT NOT NULL,
        FOREIGN KEY (order_id) REFERENCES orders(order_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        ticker TEXT PRIMARY KEY,
        yes_qty INTEGER NOT NULL DEFAULT 0,
        no_qty INTEGER NOT NULL DEFAULT 0,
        avg_yes_cost_cents INTEGER NOT NULL DEFAULT 0,
        avg_no_cost_cents INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quotes (
        ts TEXT NOT NULL,
        ticker TEXT NOT NULL,
        yes_bid INTEGER NOT NULL,
        yes_ask INTEGER NOT NULL,
        volume INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (ts, ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        ticker TEXT,
        summary TEXT NOT NULL,
        reasoning TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS params (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rebates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        source TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )
    """,
)


def bootstrap(db_path: Path) -> None:
    """Create all tables if they don't exist. Idempotent."""
    with open_connection(db_path) as conn:
        for stmt in _DDL:
            conn.execute(stmt)
