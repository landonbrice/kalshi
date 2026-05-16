# Kalshi LIP Market-Making Workstation — Vision & Design (v2)

- **Date:** 2026-05-14
- **Status:** Draft — to be activated once Phase 0 (v1) lands
- **Supersedes:** `2026-05-14-kalshi-workstation-design.md` (v1)
- **Author:** Landon Brice (with Claude)
- **Seed capital target:** $3,000 to launch live MM; $5,000 once Phase 3 runs clean for a week
- **Account:** Funded Kalshi production, API key in hand, KMMRP enrollment status to be confirmed in Phase 0

---

## 1. What changed from v1

v1 framed the system as a generalist operator workstation with LIP rebates as a **tie-breaker** between otherwise-equivalent MM candidates. After review, the operator has decided:

- **LIP rebate capture is the primary edge thesis**, not a tie-breaker.
- Capital will scale from $700 → $3,000 to launch, → $5,000 once Phase 3 is clean.
- Continuous quote uptime becomes a first-class system property; sanctioned-mode loops become the *normal* operating mode, not the exception.

That decision pulls operational safety, WebSocket ingestion, an excluded-series list, a weather fair-value model, and LIP attribution accounting from "later" into the core architecture. The clean module split, sanctioned-mode primitive, SQLite ledger, FastAPI dashboard, and read/write API isolation from v1 all survive.

The Phase 0 work currently in progress is **not invalidated** — pyproject, `risk.py`, `api/auth.py`, `api/read.py`, `state/`, and the `hello` smoke test are all still the right starting point.

---

## 2. Goal

Build a **LIP market-making workstation** for Kalshi prediction markets that earns rebates + spread capture on a curated set of low-volume LIP-eligible markets (primarily weather), with judgment-driven take plays in entertainment markets as a secondary edge.

Operated by a single human (Landon) from Claude Code. Most trading-hour activity happens inside *sanctioned-mode quoting loops* — bounded, killable, auto-expiring scopes the operator starts at the beginning of a shift and tears down at the end. Outside those loops, the system runs read-only scanners and confirm-mode take execution.

### Primary edge sources, in priority order

1. **LIP rebate capture via continuous quoting in low-volume weather markets.** Standing $200–$400 of resting size per side, near but inside the rewarded spread band, across 5–8 simultaneously-eligible markets. Rebates + spread − fees − adverse selection = expected positive net at this scale.
2. **Stale-quote / mispricing take in niche entertainment markets.** Operator-judgment plays surfaced by a scanner, executed in confirm-mode. Secondary contributor to P&L; primary contributor to the operator's market intuition.
3. **Future, not initial:** mutual-exclusivity arbs, cross-contract stat arbs.

---

## 3. Framing: workstation that mostly runs sanctioned loops

The system is still a Claude Code-driven workstation, not a black-box bot. The difference from v1 is that the **default state during trading hours is "one or more sanctioned loops running."** A loop is:

- Started explicitly by the operator with a defined scope: market list, max position per market, daily loss cap, quote width, duration.
- Killable from terminal in one command and from a kill button on the dashboard.
- Auto-expiring on duration *and* on consecutive-loss count *and* on daily-loss-cap breach.
- Bounded by hard limits in `risk.py` that the loop scope cannot override.

The operator's job during a shift is to (a) start the right loops, (b) monitor attribution and inventory drift, (c) intervene on outliers, (d) opportunistically run take plays surfaced by the entertainment scanner. Strategy lives in the operator's head + the ongoing Claude conversation; the loops live in code.

---

## 4. Architecture

Six layers. Read-only and write paths are physically separated.

```
+------------------------------------------------------------+
|             Claude Code (operator interface)               |
|  /kalshi-scan-lip  /kalshi-brief  /kalshi-quote-loop       |
|  /kalshi-take  /kalshi-positions  /kalshi-kill             |
+------------------------------------------------------------+
                            |
+------------------------------------------------------------+
|  Decision support  (briefs, suggested prices/sizes,        |
|                    LIP attribution forecasts)              |
+------------------------------------------------------------+
       |                                       |
+------------------+               +-------------------------+
|  Market intel    |               |  Execution              |
|  (LIP scanner,   |               |  (confirm-mode +        |
|   entertainment  |               |   sanctioned-mode loop  |
|   scanner,       |               |   runner; risk-gated)   |
|   rebate calc)   |               +-------------------------+
+------------------+                          |
       |                          +-----------+----------+
       |                          | Operational safety   |
       |                          | (cancel-on-disconnect,|
       |                          |  dead-man switch,    |
       |                          |  heartbeat writer)   |
       |                          +----------------------+
       |                                       |
+------------------------------------------------------------+
|  Kalshi API client (REST + WebSocket, read/write split)    |
|  Real-time order-book ingest, fills feed, balance polling  |
+------------------------------------------------------------+
                            |
+------------------------------------------------------------+
|  State / Ledger  (SQLite, single source of truth)          |
|  Orders, fills, positions, rebates accrued, attribution    |
+------------------------------------------------------------+
                            |
+------------------------------------------------------------+
|  Dashboard  (FastAPI + static HTML, localhost over Tailscale)|
|  LIP attribution, positions, loop status, kill button      |
+------------------------------------------------------------+
```

### 4.1 API client (`kalshi_ws/api/`)

Typed wrapper around Kalshi REST + WebSocket. Read methods (`api/read.py`) and write methods (`api/write.py`) live in separate modules so a scanner-only invocation cannot place an order even by mistake. WebSocket client (`api/ws.py`) subscribes to `orderbook_delta`, `market_lifecycle`, and `fill` channels for the active loop's market set.

### 4.2 Market intelligence (`kalshi_ws/intel/`)

Pure-function scanners over current market state. No side effects, no order placement.

- `lip_scanner`: ranks current LIP-active markets by expected rebate-per-dollar of standing size, accounting for `target_size_fp`, `period_reward`, current maker density, and our excluded-series filter.
- `weather_scanner`: lists low-volume weather markets by spread × turnover; flags LIP eligibility.
- `entertainment_scanner`: identifies stale-quote and mispricing candidates in entertainment markets.

### 4.3 Decision support (`kalshi_ws/decision/`)

Given a candidate market, assembles a **trade brief**: order-book depth, recent prints, current exposure, fair-value estimate (from `decision/fair_value/`), suggested quote prices and size given capital + risk limits, LIP eligibility and expected rebate, recommended sanctioned-loop parameters. Returns structured data + human-readable summary. Never places an order.

Sub-layer `decision/fair_value/`:

- `base.py` — abstract `FairValueEngine.compute(market) → (probability, confidence, metadata)`.
- `weather.py` — parses ticker → city + threshold + date, fetches GFS 31-member ensemble from Open-Meteo, returns ensemble-share probability with lopsidedness confidence.
- `manual.py` — reads operator-supplied prior from config.
- `registry.py` — `fair_value_source` config string → engine class.

### 4.4 Execution (`kalshi_ws/execution/`)

Places, modifies, and cancels orders. Two modes:

- **Confirm mode** (default for take plays): every order prompts the operator in Claude Code before sending. Used for entertainment-market take plays.
- **Sanctioned-mode loop** (default for LIP MM): pre-approved scope — market list, max position per market, quote width, refresh interval, daily loss cap, duration. The loop:
  - Reads order book state from the in-process WS subscriber.
  - Computes target bid/ask = fair value ± quote_width / 2.
  - Skews quotes by inventory.
  - Submits, modifies, cancels via `api/write.py`.
  - Logs every action + reasoning to SQLite session log.
  - Tears down (cancel-all) on any kill trigger.

Both modes route every order through the same `risk.py` gate.

### 4.5 Operational safety (`kalshi_ws/execution/safety/`)

This sub-layer exists in v2 because uptime now matters. Three components:

- **Cancel-on-disconnect.** WebSocket heartbeat fails >30s → cancel-all via REST before reconnect attempts.
- **Heartbeat writer.** Every 30s, write `(component, last_beat)` to SQLite + a small JSON file at a known path. The dashboard reads this; an optional external watchdog reads the JSON file via Tailscale and cancels-all via Kalshi REST if the heartbeat is >3 min stale.
- **Dead-man switch.** Phase 4. External cron (cloud or another always-on machine on the local network) that pings the heartbeat file and triggers cancel-all if stale. Optional but recommended for $5k+ capital.

### 4.6 State / ledger (`kalshi_ws/state/`)

Local SQLite. Single source of truth: orders, fills, positions, rebates accrued, P&L per market with attribution (spread capture / LIP rebate / fees / adverse selection), session log, heartbeat log. Reconciles against Kalshi on startup. Survives across sessions.

### 4.7 Dashboard (`kalshi_ws/dashboard/`)

FastAPI + static HTML, localhost over Tailscale so the operator can monitor from a phone. Pages:

- **LIP attribution** — for each active LIP program: target size, our size, our share, expected payout, realized rebate today, rebate run-rate.
- **Positions** — per market: contracts, avg price, P&L excl. rebates, P&L incl. rebates, % of cap used.
- **Loops** — active sanctioned-mode loops: scope, age, kill triggers armed, time-to-expiry.
- **Recent fills** — last 50, with adverse-selection flag (filled then moved through).
- **Kill switch** — one big button. Cancel-all + halt all loops + write `kill_switch: true` to config.

Auto-refresh every 5–10s. No order placement from the dashboard.

### 4.8 Isolation principle

Read-only scanners and read-only WS subscriptions cannot import the write client. Every trade flows: intel → decision → execution → write. This keeps "what should I do" and "do it" physically separate.

---

## 5. Capital & risk framework

All limits in `kalshi_ws/risk.py`. No bypass without editing the file.

| Limit | Phase 1 default ($3k) | Phase 5 default ($5k) | Rationale |
|---|---|---|---|
| Per-market max position | $100 | $200 | ~3–4% of capital; survives full adverse move |
| Single order max | $40 | $60 | Limits one-shot mistakes |
| Daily loss limit (kill switch) | $90 (3%) | $150 (3%) | Bigger than v1's $35; rebate-capture strategy can't tolerate $35 halts on normal variance |
| Max total open exposure | $1,800 | $3,000 | 60% deployed max; leaves cash for fills + opportunities |
| Min ticket size | $1 | $1 | Below this, fee + rebate logic breaks |
| Sanctioned-mode max duration | 8h | 8h | One trading shift; forces end-of-day re-check |
| Sanctioned-mode mid-shift re-check | 4h | 4h | Operator must acknowledge state at midpoint |
| Consecutive losses kill | 6 trades | 6 trades | Halts loop; manual restart |
| Per-market price band | 5¢ from midpoint | 5¢ | Hard guard on quote placement |
| Min fair-value confidence | 0.6 | 0.6 | Skip markets below threshold |
| Min seconds-to-settlement | 3600 | 3600 | Don't quote within 1h of resolution |

Pre-trade balance check (`/portfolio/balance`) runs before every order; cached balance is never trusted.

---

## 6. Excluded series

The following series carry maker fees that destroy LIP MM economics. Hardcoded in `risk.py`, loaded as a set, rejected at the risk gate regardless of any other configuration. Sourced from `master_spec.md` §6:

```
KXNBA, KXNBAGAME, KXNFLGAME, KXNHL, KXNHLGAME, KXPGA, KXUSOPEN,
KXTHEOPEN, KXFOMENSINGLES, KXFOWOMENSINGLES, KXWMENSINGLES,
KXWWOMENSINGLES, KXUSOMENSINGLES, KXUSOWOMENSINGLES,
KXAOMENSINGLES, KXAOWOMENSINGLES, KXINDY500, KXNASCARRACE,
KXTOURDEFRANCE, KXUEFACL, KXCLUBWC, KXNATHANSHD, KXNATHANDOGS,
KXPGARYDER, KXPGASOLHEIM, KXNBAFINALSMVP, KXCONNSMYTHE,
KXFOMEN, KXFOWOMEN, KXGDP, KXPAYROLLS, KXU3, KXEGGS, KXCPI,
KXCPIYOY, KXFEDDECISION, KXFED, KXAAAGASM
```

This list is verified at Phase 1 against current Kalshi fee schedules; updates require a code edit + commit, not a config change.

---

## 7. Phasing

Each phase produces a usable system. Stop and reassess after each. Phase 0 (foundation) is already in flight under the v1 plan and is **not redone** for v2.

### Phase 0 — Foundation (in progress, v1 plan)

Already specified in `docs/superpowers/plans/2026-05-14-phase-0-foundation.md`. Repo skeleton, config, `risk.py`, REST auth, one read endpoint, SQLite schema bootstrap, `hello` smoke test. **No changes for v2.**

### Phase 1 — Read-only intel + WS ingestion + KMMRP enrollment (target: 3–4 days)

Difference from v1 Phase 1: WS ingestion moves in, scanners are LIP-first.

Tasks:
1. Confirm KMMRP enrollment status. If not enrolled, apply. **Gate:** no further phases until enrolled.
2. Implement `api/ws.py` — WebSocket client subscribing to `orderbook_delta`, `market_lifecycle`. In-memory L2 order-book state per ticker; snapshot to SQLite every 5s.
3. Implement `intel/lip_scanner.py` — poll `/incentive_programs?status=active` every 5min, persist to SQLite `incentive_programs` table, rank by expected rebate-per-dollar.
4. Implement `intel/weather_scanner.py` and `intel/entertainment_scanner.py`.
5. Implement `decision/brief.py` — assembles a trade brief from intel + decision/fair_value.
6. Wire `/kalshi-scan-lip`, `/kalshi-brief`, `/kalshi-positions` Claude Code slash commands.
7. Run for 24h against live (read-only) account. Verify SQLite fills with order-book snapshots, LIP programs, scanner output.

**Done when:** scanners surface 3+ actionable LIP-eligible markets per session; order-book snapshots populated; KMMRP enrollment confirmed.

### Phase 2 — Confirm-mode execution + weather fair-value (target: 3–4 days)

Tasks:
1. Implement `api/write.py` — order placement, modification, cancellation, cancel-all.
2. Implement `decision/fair_value/weather.py` — GFS ensemble model, tested against 3 mock cities.
3. Implement `decision/fair_value/manual.py` — config-driven priors for entertainment markets.
4. Implement `execution/confirm.py` — `/kalshi-quote` and `/kalshi-take` slash commands with pre-trade brief + Y/N confirm.
5. Wire risk gate: every order through `risk.check_pre_trade()`.
6. Implement reconciler in `state/reconcile.py` — local view matches exchange on startup and every 30s.
7. Run 5–10 take plays in entertainment markets manually. Verify ledger matches Kalshi.

**Done when:** entire session executable through the workstation; ledger matches exchange; weather fair-value within ±0.05 of hand-computed truth on test fixtures.

### Phase 3 — Sanctioned-mode quoting loop (target: 4–5 days)

Tasks:
1. Implement `execution/loop_runner.py` — sanctioned-mode loop with full scope schema (market list, caps, duration, kill triggers).
2. Implement `execution/safety/disconnect.py` — cancel-on-WS-disconnect.
3. Implement `execution/safety/heartbeat.py` — heartbeat writer.
4. Implement inventory-aware quote skew in `decision/quote_skew.py`.
5. Wire `/kalshi-quote-loop` and `/kalshi-kill` slash commands.
6. **Paper run first:** 48h paper mode (loop computes intents and logs to `paper_orders` table without submitting). Eyeball every paper order.
7. **Live, single market:** pick ONE LIP weather market, $50 max position, $30 quote width, 4h duration. Sit with it.
8. **Live, expanded:** if clean, expand to 3 markets, then 5–8.

**Done when:** one full 4h sanctioned loop runs unattended without any risk-limit breach and positive expected-rebate accrual.

### Phase 4 — Dashboard, attribution, external watchdog (target: 3–4 days)

Tasks:
1. FastAPI dashboard with four pages (LIP attribution, positions, loops, recent fills) + kill button.
2. Daily P&L attribution job — splits net P&L into spread capture, LIP rebate, fees, adverse selection.
3. External watchdog (Phase 4 — optional but recommended). Runs on a cheap always-on host or local Raspberry Pi; pings heartbeat file via Tailscale; cancel-all via REST if stale >3 min.
4. Tailscale-bind dashboard to localhost; verify mobile access works.

**Done when:** can monitor system state from phone; daily attribution emails ready (mailer is Phase 5+).

### Phase 5 — Scale + alerting (target: 1 week observation)

Tasks:
1. Fund up to $5,000.
2. Raise per-market and total exposure caps per the table in §5.
3. Implement alerting (transport TBD — Telegram bot, SMS via Twilio, email, or webhook to a separate Telegram MCP if reconnected). Channels: fills, risk rejects, WS disconnects, kill-triggered, daily summary.
4. Run for 1 week. Review every fill manually for the first 3 days, then sample.

**Done when:** 1 week of live trading shows net positive P&L (after fees, after adverse selection, including LIP rebates) on at least 3 markets simultaneously.

---

## 8. Tech stack

Unchanged from v1 except WebSocket client and external-watchdog dependency:

- **Python 3.12+**, async (`httpx` + `websockets`)
- **SQLite** for ledger (stdlib `sqlite3`); single-file, no infra
- **`pydantic` v2** for typed API models
- **`pydantic-settings`** for `.env` loading
- **`typer`** for CLI entrypoints; Claude Code slash commands wrap these
- **`FastAPI`** for the dashboard server
- **`websockets`** for WS subscriber
- **`cryptography`** for RSA-PSS request signing
- **`pytest`** + `pytest-recording` for tests; never hit live API in tests
- **`ruff`** + **`mypy --strict`**
- **macOS `launchd`** to keep the executor process running during trading hours
- **Open-Meteo** (free, no auth) for GFS ensemble weather data
- **Repo location:** `/Users/landonbrice/Desktop/kalshi/`

---

## 9. Repository layout

```
kalshi/
├── docs/
│   └── superpowers/
│       ├── specs/                # this file, v1, future revisions
│       └── plans/                # phase-by-phase implementation plans
├── kalshi_ws/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── risk.py                   # hard limits + excluded series
│   ├── api/
│   │   ├── auth.py
│   │   ├── read.py
│   │   ├── write.py              # Phase 2
│   │   └── ws.py                 # Phase 1
│   ├── intel/                    # Phase 1
│   │   ├── lip_scanner.py
│   │   ├── weather_scanner.py
│   │   └── entertainment_scanner.py
│   ├── decision/                 # Phase 1–2
│   │   ├── brief.py
│   │   ├── quote_skew.py
│   │   └── fair_value/
│   │       ├── base.py
│   │       ├── weather.py
│   │       ├── manual.py
│   │       └── registry.py
│   ├── execution/                # Phase 2–3
│   │   ├── confirm.py
│   │   ├── loop_runner.py
│   │   └── safety/
│   │       ├── disconnect.py
│   │       └── heartbeat.py
│   ├── state/
│   │   ├── schema.py
│   │   ├── db.py
│   │   └── reconcile.py          # Phase 2
│   ├── dashboard/                # Phase 4
│   │   ├── app.py
│   │   ├── pages/
│   │   └── static/
│   └── cli/
│       ├── hello.py              # Phase 0
│       ├── scan.py               # Phase 1
│       ├── brief.py              # Phase 1
│       ├── quote.py              # Phase 2
│       ├── quote_loop.py         # Phase 3
│       └── kill.py               # Phase 3
├── ops/
│   ├── launchd/
│   │   └── com.lando.kalshi.plist  # Phase 3
│   └── watchdog/
│       └── external_watchdog.py    # Phase 4 (optional)
├── skills/                       # Claude Code slash command definitions
├── tests/
├── data/                         # SQLite file (gitignored)
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## 10. Out of scope

- Volume Incentive Program (VIP) optimization — LIP only for v2
- Multi-venue (Polymarket, PredictIt) integration
- ML / RL pricing — heuristics + GFS ensemble + operator judgment only
- Reinforcement-learning quote skew — inventory-linear skew is enough
- Mutual-exclusivity arb scanners — surfaced in v3 once LIP MM is stable
- Mobile-native app — Tailscale + browser is enough
- Production observability (Prometheus, OTel) — SQLite + dashboard are enough at this scale
- 24/7 cloud deployment — Mac Mini under `launchd` during operator's trading hours
- Backtesting harness — small capital + thin markets make historical backtests unreliable; paper-trade Phase 3 instead

---

## 11. Open items

- **KMMRP enrollment status** — confirm in Phase 1. Phase 2+ blocked until confirmed.
- **Alert transport** — Telegram MCP disconnected; the Phase 5 alerting layer will use a stand-alone `python-telegram-bot` bot, SMS via Twilio, or email. Decision deferred to Phase 5.
- **Excluded-series list freshness** — verify against current Kalshi fee schedule at the start of Phase 1.
- **Sanctioned-mode duration** — 8h is a guess; revisit after Phase 3 paper run.
- **External watchdog host** — Raspberry Pi vs. Railway vs. another always-on machine. Decide in Phase 4 based on what's cheapest and most reliable.

---

## 12. Success criteria

- **Phase 1 done when:** scanners reliably surface 3+ actionable LIP-eligible markets per session; WS order-book snapshots populated in SQLite; KMMRP enrollment confirmed.
- **Phase 2 done when:** an entire session of take plays can run through the workstation; ledger matches exchange on startup and after every fill.
- **Phase 3 done when:** one 4h sanctioned-mode loop runs unattended on a single LIP weather market with zero risk-limit breaches and positive expected rebate accrual.
- **Phase 4 done when:** operator can monitor system state from phone via Tailscale and kill the system in one tap; daily attribution breaks net P&L into rebate / spread / fees / adverse selection.
- **Phase 5 done when:** 1 week of live trading on 3+ markets simultaneously shows net positive P&L after all costs.

---

## 13. Anti-patterns to reject

- LLM call inside the sanctioned-mode loop or `risk.py`
- Storing API keys in `.env` for production (Phase 5 — move to macOS Keychain via `keyring`)
- Using `requests` (sync) instead of `httpx` (async)
- A "smart" cache between the WS feed and the loop that hides stale data
- Optimizing latency before reliability
- Adding new fair-value engines before Phase 3 sanctioned loop is stable
- Lifting risk limits to "test something" — limit edits are a deliberate code change, not a flag flip
- Treating the dashboard as the kill switch (it's a *second* layer; the in-code limits are first)
