from pathlib import Path

from kalshi_ws.state.db import open_connection
from kalshi_ws.state.schema import EXPECTED_TABLES, bootstrap


def test_bootstrap_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    bootstrap(db_path)

    with open_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = {r[0] for r in rows}
    assert EXPECTED_TABLES.issubset(names), f"missing: {EXPECTED_TABLES - names}"


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    bootstrap(db_path)
    bootstrap(db_path)  # second call must not raise


def test_open_connection_has_foreign_keys_on(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    bootstrap(db_path)
    with open_connection(db_path) as conn:
        (fk_on,) = conn.execute("PRAGMA foreign_keys").fetchone()
    assert fk_on == 1
