# Kalshi Trading Workstation — Vision & Design

- **Date:** 2026-05-14
- **Status:** Approved (pre-implementation)
- **Author:** Landon Brice (with Claude)
- **Seed capital:** $700 (funded Kalshi production account, API key in hand)

---

## 1. Goal

Build an **agent-assisted trading workstation** for Kalshi prediction markets. Not a fully autonomous bot — a Python toolkit invoked from Claude Code that lets a human strategist (Landon) operate efficiently with $700 of capital.

**Primary edge sources, in priority order:**

1. **Wide-spread market making in low-volume weather markets** — thin competition means we can be the NBBO and capture meaningful spread on small size.
2. **Stale-quote / mispricing taking in niche entertainment markets** — markets with low participation often have resting orders that don't reflect new information. Pick them off.
3. **Kalshi Market Maker Rebate Program (KMMRP) rebates** — a **tie-breaker preference**, not a primary driver. When MM candidates are roughly equivalent on spread × turnover, prefer the LIP/KMMRP-eligible market. Rebate-farming as a standalone strategy doesn't pencil out at this capital and uptime; treating eligibility as a ranking input captures the upside without distorting market selection.

**Future, not initial:** mutual-exclusivity arbs across complete outcome sets, cross-contract stat arbs. Surfaced by scanners; strategies added later.

## 2. Framing: workstation, not bot

A traditional "bot" implies a codified strategy running autonomously around the clock. That's the wrong shape for this situation:

- $700 of capital does not justify cloud infra or sophisticated guardrails.
- Niche entertainment markets require judgment that a hardcoded strategy will not have.
- Quote uptime is bounded by the operator's waking hours anyway.

Instead, the system is a **collection of capabilities** invoked from Claude Code: scanners, decision briefs, executors, and a ledger. The strategy lives in the operator's head + the ongoing Claude conversation. Narrow, bounded autonomous loops (e.g., "refresh quotes in market X for 1h, hard-capped exposure") are sanctioned where the operator has verified the pattern.

The line between this and a "bot" is the loop: nothing runs continuously without explicit sanction. This keeps the system safe to iterate on with small capital.

## 3. Architecture

Five layers, each independently testable, plus a thin dashboard.

```
+------------------------------------------------------------+
|                Claude Code (operator interface)            |
|  /kalshi-scan-*  /kalshi-brief  /kalshi-quote  /positions  |
+------------------------------------------------------------+
                            |
+------------------------------------------------------------+
|  Decision support  (briefs, suggested prices/sizes)        |
+------------------------------------------------------------+
       |                                       |
+------------------+               +-------------------------+
|  Market intel    |               |  Execution              |
|  (read-only      |               |  (confirm / sanctioned, |
|   scanners)      |               |   risk-gated)           |
+------------------+               +-------------------------+
       |                                       |
+------------------------------------------------------------+
|  Kalshi API client (REST + WebSocket, read/write split)    |
+------------------------------------------------------------+
                            |
+------------------------------------------------------------+
|  State / Ledger  (SQLite, single source of truth)          |
+------------------------------------------------------------+
                            |
+------------------------------------------------------------+
|  Dashboard  (localhost HTML, reads SQLite, auto-refresh)   |
+------------------------------------------------------------+
```

### 3.1 API client (`kalshi_ws/api/`)
Typed wrapper around Kalshi's REST + WebSocket APIs. Auth, rate limiting, retries with jitter. **Read methods and write methods are in separate modules** so a "scanner only" run cannot place an order even by mistake.

### 3.2 Market intelligence (`kalshi_ws/intel/`)
Pure-function scanners over current market state. No side effects, no order placement.

- `weather_scanner`: lists low-volume weather markets ranked by spread × turnover; flags KMMRP eligibility.
- `entertainment_arb_scanner`: identifies stale-quote candidates and mutual-exclusivity gaps in entertainment markets.
- `rebate_eligibility`: which markets currently qualify for KMMRP and what the size/uptime requirements are.

### 3.3 Decision support (`kalshi_ws/decision/`)
Given a candidate market, assembles a **trade brief**: order book depth, recent prints, current exposure, suggested quote prices and size given $700 budget + risk limits, rebate eligibility. Returns structured data + a human-readable summary. Never places an order.

This is the layer where Claude contributes most directly: the brief surfaces structured market state, and Claude (in the operator's session) supplies fair-value intuition and size recommendations against the brief. The code produces deterministic facts; Claude produces judgment on top of them.

### 3.4 Execution (`kalshi_ws/execution/`)
Places/cancels/modifies orders. Two modes:

- **Confirm mode** (default): every order prompts the operator in Claude Code before sending.
- **Sanctioned mode**: pre-approved scope — e.g., "quote market X for 1h, ≤$50 net exposure, refresh every 30s, hard stop on $20 loss." Started explicitly; killable from terminal; auto-expires.

Global hard limits (Section 4) are enforced in both modes and cannot be bypassed without editing `risk.py`.

### 3.5 State / ledger (`kalshi_ws/state/`)
Local SQLite database. Single source of truth for: open orders, positions, fills, rebates accrued, P&L per market, session log. Survives across sessions. **Reconciles against Kalshi on startup** so the local view matches the exchange.

The session log captures more than executed orders — it stores the Claude-assisted reasoning behind each brief and sanctioned-mode setup (fair-value call, size rationale, risk-off triggers if any). The dashboard surfaces this so a past trade can be re-read as "why did I do this" without leaving the workstation.

### 3.6 Dashboard (`kalshi_ws/dashboard/`)
Simple localhost HTML page for at-a-glance tracking. Reads directly from SQLite via a tiny FastAPI server. Shows: open positions, today's P&L, recent fills, rebates accrued, open orders, sanctioned-mode loops in progress. Auto-refreshes every few seconds. No interactivity beyond viewing — all actions go through Claude Code. Deliberately minimal; "just need tracking, nothing crazy."

### 3.7 Key isolation principle
Read-only scanners cannot talk to the execution layer directly. Every trade flows: intel → decision-support → execution. This keeps "what should I do" code separate from "do it" code, which is what makes the system safe to iterate on.

## 4. Capital & risk framework

All limits live in `kalshi_ws/risk.py` and every order path checks them. No bypass without editing code.

| Limit | Default | Rationale |
|---|---|---|
| Per-market max position | $50 | ~7% of capital; survives a full adverse move |
| Single order max | $20 | Limits one-shot mistakes |
| Daily loss limit (kill switch) | $35 | 5% of capital; sanctioned loops halt and execution layer refuses new orders until manual re-enable |
| Max total open exposure | $400 | Leaves cash for opportunities + slippage |
| Min ticket size | $1 | Below this, fees + rebate logic break |
| Sanctioned-mode max duration | 4h | Forces human re-check |
| Consecutive losses kill | 5 trades | Halts sanctioned loops; manual restart |

All values are config; conservative for $700 and scalable later by changing one file.

## 5. Phasing

Each phase produces a usable system. Stop and reassess after each.

- **Phase 0 — Setup.** Repo skeleton, `.env` with Kalshi API creds, API client stub that authenticates and reads one market, SQLite schema bootstrapped. Smoke test: `python -m kalshi_ws hello` prints a real market.
- **Phase 1 — Read-only intel.** Weather scanner, entertainment scanner, trade-brief command, `/kalshi-positions` reconciler. No execution path exists yet. Operator manually places trades surfaced by scanners; logs results.
- **Phase 2 — Confirm-mode execution.** `/kalshi-quote` and `/kalshi-take` with full pre-trade brief + Y/N confirm. Risk limits enforced. SQLite ledger captures every fill. Dashboard shows positions.
- **Phase 3 — Sanctioned auto-quoting.** Bounded background quote-refresh loop. Start/stop from terminal. Inventory-aware quote skewing. Sanctioned-mode safety guardrails.
- **Phase 4 — Metrics & rebate tracking.** Weekly rebate report, P&L per market, win-rate by strategy. Dashboard surfaces these. Informs Phase 5+ strategy expansion.

## 6. Tech stack

- **Python 3.12+**, async (`httpx` + `websockets`)
- **SQLite** for ledger (single-file, no infra)
- **`pydantic` v2** for typed API models — Kalshi's API is loosely typed; this prevents whole classes of bugs
- **`typer`** for CLI entrypoints; Claude Code slash commands wrap these
- **`FastAPI`** for the dashboard server (minimal — one or two endpoints)
- **`pytest`** + recorded-response fixtures for tests; never hit live API in tests
- **`ruff`** + **`mypy --strict`** for lint/type-check
- **Repo location:** `/Users/landonbrice/Desktop/kalshi/`

## 7. Repository layout

```
kalshi/
├── docs/
│   └── superpowers/
│       ├── specs/           # design docs (this file)
│       └── plans/           # implementation plans
├── kalshi_ws/
│   ├── __init__.py
│   ├── api/                 # Kalshi API client (read/write split)
│   ├── intel/               # scanners (read-only)
│   ├── decision/            # trade briefs, sizing
│   ├── execution/           # order placement, sanctioned-mode runner
│   ├── state/               # SQLite ledger, reconciler
│   ├── dashboard/           # FastAPI server + static HTML
│   ├── cli/                 # typer entrypoints
│   └── risk.py              # hard limits (single file, no bypass)
├── skills/                  # Claude Code slash command definitions
├── tests/
├── data/                    # SQLite file lives here (gitignored)
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

## 8. Out of scope (YAGNI guardrails)

To keep Phase 0–4 tight, these are explicitly **not** in the initial build:

- Cloud / 24/7 deployment of any kind
- Multi-account support
- ML / RL / sophisticated pricing models — heuristics + operator judgment only
- Slack/Discord/Telegram/email alerting (Telegram MCP disconnected; not pursuing)
- Complex multi-leg or conditional orders
- Strategies beyond (a) MM in weather and (b) arb/take in entertainment
- Production observability (Prometheus, etc.) — dashboard + logs are enough at this scale
- Backtesting harness — small capital + thin niche markets make historical backtests unreliable; we paper-trade Phase 1 instead

## 9. Open items

- **KMMRP enrollment status** — not confirmed. If not enrolled, Phase 0 includes applying. Scanners design for "rebate-eligible or not" both ways.
- **Kalshi sandbox/demo availability** — confirm whether a paper environment exists; if so, Phase 0 uses it before touching real funds.
- **Slash command names** — the `/kalshi-*` prefix is tentative; finalize during Phase 0.

## 10. Success criteria

- **Phase 1 done when:** scanners reliably surface 3+ actionable opportunities per session; operator can place trades on them and see them in the ledger.
- **Phase 2 done when:** an entire session can be executed through the workstation (no manual Kalshi UI clicks); ledger matches exchange on startup.
- **Phase 3 done when:** a sanctioned quoting loop runs unattended for 1h with zero risk-limit breaches and positive expected rebate accrual.
- **Phase 4 done when:** weekly review surfaces which markets/strategies are profitable so capital can be reallocated intentionally.
