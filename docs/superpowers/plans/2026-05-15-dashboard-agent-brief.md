# Dashboard Agent Brief — Local LIP & Ledger Dashboard

> **For an agentic worker:** read this whole file before touching code. The backend (scanner + ledger) is owned by another concurrent session; don't reach into the directories listed under "Do not modify."

## Goal

Build a local FastAPI + HTML dashboard at `http://localhost:8765` (no auth, localhost only) that surfaces, in one page:

1. **Latest LIP scanner output** — top-N PLAY candidates with EV, share, capital, days remaining, spread; sortable.
2. **Scanner freshness** — when did the last scan complete? How long ago?
3. **Risk-limit display** — read-only printout of the four hard limits in `kalshi_ws/risk.py` so the operator sees the rails without opening code.
4. **Ledger snapshot** — current positions, recent fills, accrued rebates. Tables will be empty in Phase 1; design for that gracefully.

This is read-only. The dashboard never places, cancels, or mutates anything.

## Tech & versions

- Python 3.12+, FastAPI, uvicorn (dev server)
- One Jinja2 HTML template for the page; HTMX or vanilla JS for refresh — your call
- `pandas` only if it earns its weight; `csv.DictReader` is enough for the scanner CSV

Add new deps to `pyproject.toml` under `[project.optional-dependencies].dashboard` (don't touch `dev`).

## File map (what you create)

```
kalshi_ws/dashboard/
├── __init__.py          # empty
├── server.py            # FastAPI app + routes
├── data_sources.py      # readers: load CSV, load meta, query SQLite
└── templates/
    └── index.html
tests/dashboard/
├── __init__.py
├── test_data_sources.py # unit-test the readers against fixture files
└── test_server.py       # FastAPI TestClient: route returns 200, contains expected text
```

CLI entry to launch:

```python
# add to kalshi_ws/cli/__init__.py
@app.command("dashboard")
@friendly_errors
def dashboard(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
) -> None:
    import uvicorn
    from kalshi_ws.dashboard.server import app as fastapi_app
    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")
```

Add a one-line note to README about how to launch.

## Inputs you read

### `data/lip_candidates.csv` (refreshed every ~15 min)

Atomically written by `scan-lip-loop`; safe to read at any time. Column schema (locked enough for the dashboard, but **expect drift** — the backend session may add columns; treat unknown columns as forward-compatible):

| column | type | notes |
|---|---|---|
| `ticker` | str | Kalshi market ticker |
| `title` | str | Market title |
| `category` | str | "Entertainment", "Sports", etc. May be empty when an event lookup failed. |
| `play` | str | "True" / "False" |
| `reason` | str | "play" or the gate that rejected it |
| `ev_per_day` | float-as-str | Headline rank metric. Show as `$X.XX/day`. |
| `reward_per_day` | float-as-str | LIP rebate component |
| `share` | float-as-str | 0..1, our estimated share of the reward pool |
| `opp_cost_per_day` | float-as-str |  |
| `capital_locked` | float-as-str | Dollars |
| `days_remaining` | float-as-str | Until LIP period ends |
| `spread` | float-as-str | yes_ask - yes_bid in dollars |
| `mid` | float-as-str | (yes_bid + yes_ask) / 2 |
| `yes_bid_cents`, `yes_ask_cents` | int |  |
| `top_yes_size`, `top_no_size` | float | Resting size at best bid (proxy for competitor MM size) |
| `lip_target_size` | float | Contracts per side we'd need to post |
| `lip_period_reward_cents` | int |  |
| `lip_end_date` | ISO datetime |  |

Default sort: PLAY rows first (descending by `ev_per_day`), then PASS rows.

### `data/lip_candidates.meta.json` (sidecar, atomic)

```json
{
  "loop_started_at": "2026-05-16T02:10:33.968043+00:00",
  "scan_started_at": "2026-05-16T02:11:52.581943+00:00",
  "scan_duration_seconds": 15.67,
  "iteration": 2,
  "candidates_total": 23,
  "candidates_play": 23,
  "candidates_pass": 0,
  "category_filter": null
}
```

Use `scan_started_at` for the "last refreshed" widget. If the file is missing OR `scan_started_at` is older than 30 min, surface a "STALE" badge — the loop may have died.

### `data/kalshi.db` (SQLite ledger)

Schema source of truth: `kalshi_ws/state/schema.py`. Open with `kalshi_ws.state.db.open_connection`. **Read-only** queries only — no INSERT / UPDATE / DELETE from the dashboard.

Tables you'll show:

- `positions` — `ticker, yes_qty, no_qty, avg_yes_cost_cents, avg_no_cost_cents, updated_at`
- `fills` (recent N=20) — `ticker, side, action, price_cents, qty, fee_cents, filled_at`
- `rebates` — `ticker, period_start, period_end, amount_cents, source`

All three will be empty in Phase 1. Render an empty state explicitly ("No fills yet — Phase 2 wiring lands the executor"); don't show a stack trace.

### `kalshi_ws/risk.py` constants

Import and display:

```
PER_MARKET_MAX_POSITION_USD = 50
SINGLE_ORDER_MAX_USD = 20
DAILY_LOSS_LIMIT_USD = 35
MAX_TOTAL_OPEN_EXPOSURE_USD = 400
```

Just a small fixed panel — these are the rails the operator wants to see at a glance.

## Do not modify

These are owned by the backend session running concurrently:

```
kalshi_ws/api/         # all of it
kalshi_ws/intel/       # all of it (LIP scanner)
kalshi_ws/state/       # schema is read-only for you
kalshi_ws/risk.py      # display only, never edit
kalshi_ws/config.py
kalshi_ws/cli/__init__.py  # only ADD the `dashboard` command per the snippet above
kalshi_ws/cli/_errors.py
kalshi_ws/api/auth.py
data/                  # never write
secrets/               # never read or write (the dashboard has no business with auth)
```

Tests outside `tests/dashboard/` are also off-limits.

## Suggested page layout

```
┌────────────────────────────────────────────────────────────────────┐
│ Kalshi Workstation                  Last scan: 2 min ago [FRESH]   │
├──────────────────┬─────────────────────────────────────────────────┤
│ Risk rails       │ LIP candidates (top 25 PLAY, sorted by EV/day)  │
│  $50 / market    │ ┌──┬─────────┬───────┬───────┬─────┬──────────┐ │
│  $20 / order     │ │# │ EV/day  │ share │ cap$  │days │ ticker   │ │
│  $35 / day loss  │ ├──┼─────────┼───────┼───────┼─────┼──────────┤ │
│  $400 total      │ │ 1│ $387.78 │ 0.97  │ $144  │ 6.2 │ KX...    │ │
│                  │ │ 2│ $240.97 │ 0.60  │ $149  │ 6.2 │ KX...    │ │
│                  │ └──┴─────────┴───────┴───────┴─────┴──────────┘ │
│                  │ [25 PLAY · 5 PASS · 80 scanned]                 │
├──────────────────┴─────────────────────────────────────────────────┤
│ Positions (0)                  Recent fills (0)     Rebates (0)    │
│ — none yet —                   — none yet —         — none yet —   │
└────────────────────────────────────────────────────────────────────┘
```

Auto-refresh the page every 30s (HTMX `hx-trigger="every 30s"` on the body, or a meta refresh). Don't poll the LIP CSV faster than that — the loop only writes every 15 min anyway.

## Open items / decisions you should make

1. **Routing**: One `/` route serving the full page is fine. If you want `/api/candidates`, `/api/meta`, `/api/ledger` JSON endpoints behind it (so it's debuggable via curl), that's a +1.
2. **Sorting / filtering**: column sort in the candidates table is a nice-to-have; default sort is enough for v1.
3. **Category dropdown**: optional. The scanner also accepts `--category Entertainment` for narrower CSVs; for now, render whatever's in the CSV.
4. **HTMX vs vanilla**: pick whichever you ship faster.

## Acceptance

- `python -m kalshi_ws dashboard` launches uvicorn, page loads at `http://127.0.0.1:8765`.
- With `data/lip_candidates.csv` and `data/lip_candidates.meta.json` present, the candidates table renders top-25 PLAY by EV/day; freshness widget shows scan age.
- With `data/kalshi.db` empty, all three ledger panels render empty states (no exceptions).
- `pytest tests/dashboard` passes.
- `ruff check kalshi_ws/dashboard tests/dashboard` passes.
- `mypy kalshi_ws/dashboard tests/dashboard` passes.

## How to start the scanner loop (for reference, not your job)

The operator (or an autonomous loop) starts the scanner separately:

```
nohup .venv/bin/python -m kalshi_ws scan-lip-loop \
  --interval 900 \
  --out data/lip_candidates.csv \
  >> data/scan-lip-loop.log 2>&1 &
```

If the dashboard's freshness widget says "STALE", that loop died. Just surface it; recovery is the operator's call.
