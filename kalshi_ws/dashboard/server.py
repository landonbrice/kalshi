"""FastAPI dashboard server for the Kalshi LIP workstation.

Routes
------
GET /            - full page render (Jinja2 template)
GET /api/candidates  - JSON: top-25 PLAY candidates
GET /api/meta        - JSON: scanner meta sidecar (or null)
GET /api/ledger      - JSON: positions, recent_fills, rebates
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

import kalshi_ws.risk as risk
from kalshi_ws.dashboard.data_sources import (
    Candidate,
    Ledger,
    concentration,
    freshness,
    load_candidates,
    load_meta,
    read_ledger,
    risk_usage,
    totals,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path("data")
_CSV_PATH = _DATA_DIR / "lip_candidates.csv"
_META_PATH = _DATA_DIR / "lip_candidates.meta.json"
_DB_PATH = _DATA_DIR / "kalshi.db"

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Kalshi Workstation Dashboard", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RISK_RAILS = {
    "PER_MARKET_MAX_POSITION_USD": risk.PER_MARKET_MAX_POSITION_USD,
    "SINGLE_ORDER_MAX_USD": risk.SINGLE_ORDER_MAX_USD,
    "DAILY_LOSS_LIMIT_USD": risk.DAILY_LOSS_LIMIT_USD,
    "MAX_TOTAL_OPEN_EXPOSURE_USD": risk.MAX_TOTAL_OPEN_EXPOSURE_USD,
}


def _format_age(age_seconds: float) -> str:
    """Human-readable age string: '2 min ago', '47 s ago', etc."""
    if math.isinf(age_seconds):
        return "unknown"
    secs = int(age_seconds)
    if secs < 60:
        return f"{secs} s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min ago"
    hrs = mins // 60
    return f"{hrs} h ago"


def _gather_page_data() -> dict[str, object]:
    """Collect all template variables in one call."""
    candidates = load_candidates(_CSV_PATH)
    meta = load_meta(_META_PATH)
    label, age_secs = freshness(meta, now=datetime.now(UTC))
    age_str = _format_age(age_secs)
    ledger = read_ledger(_DB_PATH)

    play_rows = [c for c in candidates if c["play"]]
    pass_rows = [c for c in candidates if not c["play"]]
    top_candidates: list[Candidate] = play_rows[:25]

    conc = concentration(top_candidates)
    row_totals = totals(top_candidates)
    usage = risk_usage(ledger)

    return {
        "risk": _RISK_RAILS,
        "risk_usage": usage,
        "candidates": top_candidates,
        "play_count": len(play_rows),
        "pass_count": len(pass_rows),
        "total_scanned": len(candidates),
        "meta": meta,
        "freshness_label": label,
        "freshness_age": age_str,
        "concentration": conc,
        "row_totals": row_totals,
        "positions": ledger["positions"],
        "recent_fills": ledger["recent_fills"],
        "rebates": ledger["rebates"],
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    data = _gather_page_data()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=dict(data),
    )


@app.get("/api/candidates")
def api_candidates() -> JSONResponse:
    candidates = load_candidates(_CSV_PATH)
    play_rows = [c for c in candidates if c["play"]]
    return JSONResponse(content={"candidates": play_rows[:25]})


@app.get("/api/meta")
def api_meta() -> JSONResponse:
    meta = load_meta(_META_PATH)
    label, age_secs = freshness(meta, now=datetime.now(UTC))
    return JSONResponse(
        content={
            "meta": meta,
            "freshness_label": label,
            "age_seconds": None if math.isinf(age_secs) else age_secs,
        }
    )


@app.get("/api/ledger")
def api_ledger() -> JSONResponse:
    ledger: Ledger = read_ledger(_DB_PATH)
    return JSONResponse(content=ledger)
