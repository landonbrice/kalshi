# Kalshi LIP Market-Making System — Build Spec

## 0. Purpose

Build a small, reliable market-making system that quotes resting orders on Kalshi markets eligible for the **Liquidity Incentive Program (LIP)**, with the goal of earning LIP rebates plus spread capture while staying flat to slightly-positive on adverse selection. Starting capital: **$700**. Operator: solo. Hosting: headless Mac Mini accessed via Tailscale.

This is not a high-frequency system. Decision loop target: 1–5 seconds. Reliability and safety dominate over speed.

---

## 1. Non-Negotiable Constraints

These are not suggestions. If a design choice violates one of these, redo the design.

1. **No LLM in the hot trading loop.** The executor is fully deterministic Python. Strategy/analysis happens in separate Claude Code sessions that write config, never that execute orders directly.
2. **Paper-trade mode is the default.** Live mode is opt-in via explicit config flag. New markets ship in paper mode and graduate to live only after manual sign-off.
3. **Hard loss cap in code.** If realized + unrealized daily P&L drops below `-$50`, the executor sets `kill_switch: true` and cancels all orders. This is enforced in code, not in the dashboard. The dashboard's kill button is a *second* layer.
4. **Cancel-on-disconnect.** If WebSocket heartbeat fails for more than 30 seconds, cancel all open orders before attempting reconnect.
5. **Dead-man switch.** Executor writes a heartbeat to Supabase every 30 seconds. An external watchdog (Railway cron or Supabase scheduled function) checks the heartbeat and issues a cancel-all via Kalshi REST if no heartbeat in 3 minutes.
6. **Per-market position caps.** No market can hold more than `$50` gross notional without explicit per-market override in config.
7. **Per-market price-band guards.** No order can be placed at a price more than `X cents` away from current midpoint (X configurable per market, default 5).
8. **Pre-trade balance check.** Before any order placement, verify available balance via Kalshi `/portfolio/balance`. Never assume cached balance is fresh.

---

## 2. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language (everything) | Python 3.11+ | One language, async-native |
| HTTP client | `httpx` (async) | Native async, modern |
| WebSocket | `websockets` | Standard, well-maintained |
| Schemas | `pydantic` v2 | Type safety, validation |
| Auth crypto | `cryptography` | RSA-PSS signing |
| Database | Supabase (Postgres) | Already in your stack |
| DB client | `supabase-py` async client | Match your existing pattern |
| Config | YAML + `pyyaml` + `watchdog` | Hot-reload without restart |
| Secrets | macOS Keychain via `keyring` | Don't store API keys on disk |
| Process supervisor | `launchd` (macOS native) | Mac Mini's native option |
| Dashboard | `streamlit` | Single Python file, zero JS, auto-rerun |
| Alerts | Telegram bot (`python-telegram-bot`) | Reuse existing bot scaffolding |
| Tests | `pytest` + `pytest-asyncio` | Standard |
| Lint/format | `ruff` + `mypy --strict` | Catch issues early |

---

## 3. Repo Structure

```
kalshi-lip/
├── pyproject.toml
├── README.md
├── SPEC.md                         # this file
├── config.yaml                     # mutable runtime config (see §6)
├── config.example.yaml             # checked in; config.yaml is gitignored
├── .env.example                    # checked in; .env gitignored
│
├── executor/                       # the hot loop
│   ├── __init__.py
│   ├── main.py                     # entrypoint, asyncio event loop
│   ├── auth.py                     # RSA-PSS signing, token refresh
│   ├── client.py                   # REST + WS client wrapper
│   ├── ingest/
│   │   ├── orderbook.py            # WS subscriber → in-memory L2 state
│   │   ├── incentives.py           # poll /incentive_programs every 5min
│   │   └── markets.py              # /markets metadata cache
│   ├── fair_value/
│   │   ├── base.py                 # abstract FairValueEngine
│   │   ├── weather.py              # GFS ensemble → bracket probs
│   │   ├── manual.py               # config-driven static priors
│   │   └── registry.py             # name → engine class lookup
│   ├── quoter.py                   # fair value + LIP target → order intents
│   ├── risk.py                     # pre-trade + live risk checks
│   ├── state.py                    # position/order reconciliation
│   ├── config_loader.py            # watch config.yaml, hot-reload
│   └── watchdog.py                 # heartbeat writer + WS health
│
├── strategy/                       # cold loop, Claude Code lives here
│   ├── __init__.py
│   ├── scout/
│   │   ├── scan_lip.py             # rank current LIP markets
│   │   └── score_book.py           # attackability score per market
│   ├── backtest/
│   │   ├── replay.py               # replay orderbook snapshots
│   │   └── attribution.py          # decompose P&L into components
│   └── write_config.py             # safely edit config.yaml
│
├── dashboard/
│   ├── app.py                      # streamlit entry
│   ├── pages/
│   │   ├── 1_positions.py
│   │   ├── 2_lip_attribution.py
│   │   ├── 3_markets.py
│   │   └── 4_kill_switch.py
│   └── components/
│       └── shared.py               # data loaders, formatters
│
├── shared/
│   ├── __init__.py
│   ├── schemas.py                  # all pydantic models
│   ├── db.py                       # supabase client factory
│   ├── kalshi_types.py             # API request/response types
│   ├── alerts.py                   # telegram send helpers
│   └── constants.py                # EXCLUDED_SERIES, fee formulas, etc.
│
├── ops/
│   ├── launchd/
│   │   └── com.lando.kalshi-executor.plist
│   ├── migrations/                 # supabase SQL migrations
│   │   └── 0001_initial.sql
│   └── watchdog/
│       └── external_watchdog.py    # runs on Railway, cancels on heartbeat miss
│
└── tests/
    ├── unit/
    │   ├── test_auth.py
    │   ├── test_fair_value_weather.py
    │   ├── test_risk.py
    │   └── test_quoter.py
    └── integration/
        └── test_paper_trade_loop.py
```

---

## 4. Build Phases

Build strictly in order. Do not start phase N+1 until phase N is tested and runs cleanly for at least 24 hours.

### Phase 1 — Read-only foundation (target: 2–3 days)

**Goal: data flows from Kalshi into Supabase. No trading.**

Tasks:
1. Create Supabase project, run migration `0001_initial.sql` (schema in §5).
2. Set up Kalshi sandbox/demo account, generate read-only API key, store in Keychain.
3. Implement `executor/auth.py` — RSA-PSS request signing, 30-min session token refresh, retry on 401.
4. Implement `executor/client.py` — REST wrapper for the endpoints in §7.
5. Implement `executor/ingest/incentives.py` — poll `/incentive_programs?status=active` every 5 min, upsert to Supabase.
6. Implement `executor/ingest/markets.py` — cache market metadata, refresh on demand.
7. Implement `executor/ingest/orderbook.py` — WebSocket subscribe to a configured list of tickers, write snapshots to Supabase every N seconds (default 5).
8. Wire all three into a minimal `main.py` that runs the ingest loop only.
9. Run for 24 hours against demo. Verify Supabase fills with reasonable data.

**Done when:** demo connection runs 24h without intervention, Supabase has populated `incentive_programs`, `markets`, `orderbook_snapshots` tables, and you can SQL-query them.

### Phase 2 — Fair value engine for weather (target: 2 days)

**Goal: For any active weather market, compute a model probability for the YES side.**

Tasks:
1. Implement `executor/fair_value/base.py` — abstract class with `compute(market) -> FairValue` returning `(probability, confidence, model_metadata)`.
2. Implement `executor/fair_value/weather.py`:
   - Parse market ticker to extract city + threshold + date
   - Fetch GFS 31-member ensemble from Open-Meteo for that city
   - Count members above/below threshold → probability
   - Confidence = how lopsided the ensemble is (1.0 = unanimous, 0.0 = 50/50)
3. Implement `executor/fair_value/manual.py` — reads `fair_value_override` from config for markets where you set probability by hand.
4. Implement `executor/fair_value/registry.py` — maps `fair_value_source` config string to engine.
5. Write `tests/unit/test_fair_value_weather.py` with mock Open-Meteo responses for at least 3 cities.

**Done when:** for every active KXHIGH market in `incentive_programs`, the engine returns a probability in [0, 1] with a confidence score, logged to Supabase `fair_value_log` table.

### Phase 3 — Paper-trading quoter and risk module (target: 3–4 days)

**Goal: Compute order intents and risk-check them. Do not actually place orders yet.**

Tasks:
1. Implement `executor/risk.py`:
   - `check_pre_trade(order_intent, current_state) -> RiskResult`
   - Checks: position cap, daily loss cap, price band, balance, kill switch, market enabled
   - Returns either `Approved` or `Rejected(reason)`
2. Implement `executor/quoter.py`:
   - Input: fair value + LIP target + current orderbook + config
   - Output: list of `OrderIntent(side, price, size, ticker)`
   - Logic:
     - For each rewarded market in config with `enabled: true`:
       - Compute YES bid = `fair_value - quote_width_cents/200` (clamp to valid range)
       - Compute YES ask = `fair_value + quote_width_cents/200`
       - Size based on `target_size_fp` from LIP program and `max_position_contracts`
     - Skip if confidence below threshold
     - Skip if outside pull-time window
3. Implement `executor/state.py` — reconcile internal state vs Kalshi-reported orders and positions on a 30s interval.
4. Implement `executor/config_loader.py` — watch `config.yaml` for changes, validate via pydantic, hot-reload.
5. Implement `executor/watchdog.py` — write heartbeat to `system_heartbeat` table every 30s, monitor WS health.
6. In `main.py`, run the full loop in **paper-trade mode**: log all intended orders to Supabase `paper_orders` table. Do not actually call `POST /portfolio/orders`.
7. Run paper-trade for 48 hours against live market data. Manually review the `paper_orders` table — would these have been good orders?

**Done when:** the paper-trade log shows orders that pass eyeball test. No risk rejections that surprise you. Heartbeats consistent.

### Phase 4 — Dashboard (target: 2 days)

**Goal: One-screen visibility into the system + working kill switch.**

Tasks:
1. `dashboard/app.py` — Streamlit entry with sidebar navigation across 4 pages.
2. `pages/1_positions.py` — current positions per market, P&L excluding rebates, P&L including rebates, total exposure vs cap.
3. `pages/2_lip_attribution.py` — for each active LIP program: target size, your size, your share, expected payout. Sort descending by expected payout.
4. `pages/3_markets.py` — full list of attackable markets right now (from `strategy/scout/scan_lip.py` logic), with status badges (live / paper / disabled).
5. `pages/4_kill_switch.py` — single big red button. On click: writes `kill_switch: true` to config.yaml, cancels all orders via REST, posts Telegram alert.
6. All pages auto-rerun every 10 seconds.
7. Bind dashboard to localhost only. Access via Tailscale.

**Done when:** you can see system state from your phone via Tailscale and kill it in one tap.

### Phase 5 — Live trading, gated (target: 1 week observation)

**Goal: Live quoting on 1–2 markets with hard caps.**

Tasks:
1. Add `live_mode: true` flag *per market* in config (global default stays false).
2. Pick ONE weather market with active LIP, set `live_mode: true`, `max_position_contracts: 20`, `quote_width_cents: 2`.
3. Implement `POST /portfolio/orders` and `DELETE /portfolio/orders/{id}` in client.
4. Wire quoter to actually submit when `live_mode: true`.
5. Wire fill notifications to Telegram.
6. Run for 48 hours. Review every fill manually.
7. If clean: add one more market. Repeat.

**Done when:** 1 week of live trading shows positive net P&L (after fees, after adverse selection, including LIP rebates) on at least one market.

---

## 5. Database Schema (Supabase)

```sql
-- ops/migrations/0001_initial.sql

create table markets (
  ticker text primary key,
  series_ticker text not null,
  title text,
  category text,
  status text,
  open_time timestamptz,
  close_time timestamptz,
  settled_at timestamptz,
  metadata jsonb,
  updated_at timestamptz default now()
);

create table incentive_programs (
  id text primary key,
  market_ticker text references markets(ticker),
  incentive_type text not null,           -- 'liquidity' | 'volume'
  start_date timestamptz not null,
  end_date timestamptz not null,
  period_reward numeric,
  paid_out boolean default false,
  discount_factor_bps integer,
  target_size_fp text,
  raw jsonb,
  fetched_at timestamptz default now()
);

create table orderbook_snapshots (
  id bigserial primary key,
  ticker text references markets(ticker),
  snapshot_at timestamptz default now(),
  yes_bids jsonb,                          -- [[price, size], ...]
  yes_asks jsonb,
  midpoint numeric,
  spread_cents numeric
);
create index on orderbook_snapshots (ticker, snapshot_at desc);

create table fair_value_log (
  id bigserial primary key,
  ticker text references markets(ticker),
  computed_at timestamptz default now(),
  probability numeric,
  confidence numeric,
  source text,                             -- 'weather' | 'manual' | etc
  model_metadata jsonb
);
create index on fair_value_log (ticker, computed_at desc);

create table paper_orders (
  id bigserial primary key,
  intended_at timestamptz default now(),
  ticker text,
  side text,                               -- 'yes' | 'no'
  action text,                             -- 'buy' | 'sell'
  price_cents integer,
  size integer,
  fair_value numeric,
  risk_result text,
  reasoning jsonb
);

create table orders (
  order_id text primary key,
  ticker text,
  side text,
  action text,
  price_cents integer,
  size integer,
  status text,
  placed_at timestamptz,
  updated_at timestamptz default now()
);

create table fills (
  fill_id text primary key,
  order_id text references orders(order_id),
  ticker text,
  side text,
  action text,
  price_cents integer,
  size integer,
  fee_cents integer,
  filled_at timestamptz
);

create table positions (
  ticker text primary key,
  yes_contracts integer default 0,
  avg_price_cents numeric,
  realized_pnl_cents integer default 0,
  unrealized_pnl_cents integer default 0,
  updated_at timestamptz default now()
);

create table pnl_attribution_daily (
  date date,
  ticker text,
  spread_capture_cents integer,
  lip_rebate_cents integer,
  vip_rebate_cents integer,
  fees_paid_cents integer,
  adverse_selection_cents integer,
  net_cents integer,
  primary key (date, ticker)
);

create table system_heartbeat (
  component text primary key,              -- 'executor' | 'watchdog'
  last_beat timestamptz default now(),
  metadata jsonb
);

create table alerts_log (
  id bigserial primary key,
  sent_at timestamptz default now(),
  severity text,
  channel text,
  payload jsonb
);
```

---

## 6. Config File Format

```yaml
# config.yaml

global:
  mode: paper                      # paper | live
  kill_switch: false
  max_total_exposure_usd: 700
  daily_loss_cap_usd: 50
  ws_heartbeat_timeout_sec: 30
  config_hot_reload: true

api:
  base_url: https://api.elections.kalshi.com/trade-api/v2
  ws_url: wss://api.elections.kalshi.com/trade-api/ws/v2
  rate_limit_buffer_pct: 0.8       # use only 80% of published rate limit

risk:
  per_market_position_cap_usd: 50  # default; overridable per market
  price_band_cents: 5              # max distance from midpoint
  min_fair_value_confidence: 0.6   # skip markets with confidence below this
  min_seconds_to_settlement: 3600  # don't quote within 1h of resolution

excluded_series:                   # markets that carry maker fees
  - KXNBA
  - KXNBAGAME
  - KXNFLGAME
  - KXNHL
  - KXNHLGAME
  - KXPGA
  - KXUSOPEN
  - KXTHEOPEN
  - KXFOMENSINGLES
  - KXFOWOMENSINGLES
  - KXWMENSINGLES
  - KXWWOMENSINGLES
  - KXUSOMENSINGLES
  - KXUSOWOMENSINGLES
  - KXAOMENSINGLES
  - KXAOWOMENSINGLES
  - KXINDY500
  - KXNASCARRACE
  - KXTOURDEFRANCE
  - KXUEFACL
  - KXCLUBWC
  - KXNATHANSHD
  - KXNATHANDOGS
  - KXPGARYDER
  - KXPGASOLHEIM
  - KXNBAFINALSMVP
  - KXCONNSMYTHE
  - KXFOMEN
  - KXFOWOMEN
  - KXGDP
  - KXPAYROLLS
  - KXU3
  - KXEGGS
  - KXCPI
  - KXCPIYOY
  - KXFEDDECISION
  - KXFED
  - KXAAAGASM

markets:
  # example weather market
  KXHIGHNY-26MAY15-T81:
    enabled: false
    live_mode: false
    fair_value_source: weather
    quote_width_cents: 2
    max_position_contracts: 20
    pull_time_local: "14:00"
    timezone: "America/New_York"

  # example culture market with manual fair value
  KXTOPALBUM-26JUN01-OPALITE:
    enabled: false
    live_mode: false
    fair_value_source: manual
    fair_value_override: 0.42
    quote_width_cents: 3
    max_position_contracts: 15
    news_keywords_pull:
      - "taylor swift"
      - "opalite"

alerts:
  telegram_chat_id: "<set in .env>"
  notify_on:
    fills: true
    risk_rejects: true
    ws_disconnects: true
    fair_value_deviations_bps: 200
    daily_summary_time: "23:00"
```

---

## 7. Kalshi API Endpoints (v2)

Endpoints the executor must implement, in priority order:

| Method | Path | Use |
|---|---|---|
| GET | `/incentive_programs` | Phase 1 — LIP/VIP active programs (`?status=active`) |
| GET | `/markets` | Phase 1 — market metadata, paginated |
| GET | `/markets/{ticker}` | Phase 1 — single market detail |
| GET | `/markets/{ticker}/orderbook` | Phase 1 — REST fallback for orderbook |
| GET | `/series/{ticker}` | Phase 1 — series metadata |
| GET | `/portfolio/balance` | Phase 3 — pre-trade balance check |
| GET | `/portfolio/positions` | Phase 3 — reconciliation |
| GET | `/portfolio/orders` | Phase 3 — reconciliation |
| GET | `/portfolio/fills` | Phase 3 — fill ingestion |
| POST | `/portfolio/orders` | Phase 5 — order placement (live only) |
| DELETE | `/portfolio/orders/{order_id}` | Phase 3 — cancel single |
| DELETE | `/portfolio/orders` | Phase 3 — cancel all (kill switch) |
| WS | `/trade-api/ws/v2` | Phase 1 — subscribe to `orderbook_delta`, `market_lifecycle`, `fill` |

Auth: every request signed with RSA-PSS using the private key associated with the API key ID. Reference Kalshi docs for exact signing payload format.

---

## 8. Telegram Alert Spec

Reuse the existing bot scaffolding from the baseball platform. Configure a dedicated `KALSHI_LIP` chat ID.

Alerts to send:
- `[FILL]` Market, side, size, price, running P&L for that market
- `[RISK_REJECT]` Market, intent, reason
- `[WS_DOWN]` Disconnect timestamp, action taken
- `[KILL_TRIGGERED]` Source (auto loss cap | manual | watchdog), state at trigger
- `[DAILY_SUMMARY]` Per-market P&L, total, LIP earned, fees, top winner, top loser

---

## 9. Testing Requirements

**Phase 1 must pass:**
- `test_auth.py` — RSA-PSS signing produces a valid signature against a known fixture
- `test_client.py` — request retry on 401, token refresh works

**Phase 2 must pass:**
- `test_fair_value_weather.py` — for 3 mock cities/dates, ensemble counting produces expected probability ±0.05

**Phase 3 must pass:**
- `test_risk.py` — every constraint in §1 has a unit test for both pass and fail cases
- `test_quoter.py` — given fixture market + fair value + config, produces expected order intents
- `test_paper_trade_loop.py` — integration test running ingest + fair value + quoter end-to-end against mocked Kalshi, verifying paper orders match expected output

**Phase 5 gate (manual checklist):**
- [ ] 48 hours of paper trading with no surprising risk rejects
- [ ] Heartbeats consistent for 7 days
- [ ] Kill switch tested end-to-end (button → orders cancelled → confirmed in Kalshi)
- [ ] External watchdog tested (kill executor → orders cancelled within 4 min)
- [ ] Daily P&L summary lands in Telegram on schedule
- [ ] Telegram alerts received for paper fills (rendered as if live)

---

## 10. Out of Scope for v1

Do not build these. Save for later:
- Volume Incentive Program (VIP) optimization — focus on LIP only
- Multi-venue (Polymarket) integration
- Order book level-2 microstructure modeling
- Reinforcement learning anything
- Mobile app (the Streamlit dashboard via Tailscale works fine on mobile)
- Auth UI / user management (single operator)
- Historical data backfill beyond what arrives naturally during Phase 1

---

## 11. Anti-Patterns to Reject

If Claude Code suggests any of these, push back:
- Putting an LLM call in `quoter.py` or `risk.py`
- Storing API keys in `.env` instead of Keychain
- Using `requests` (sync) instead of `httpx` (async)
- Building a Postgres schema with no indexes on time-series tables
- Implementing the dashboard as a SPA with WebSockets (Streamlit auto-rerun is enough)
- Adding a "smart" cache layer that obscures real-time state from Kalshi
- Optimizing for latency before reliability

---

## 12. First Session Instruction for Claude Code

Open this repo. Read `SPEC.md` (this file) end to end. Then:

1. Create `pyproject.toml` with the dependencies in §2.
2. Create the directory structure in §3.
3. Generate `ops/migrations/0001_initial.sql` from §5 exactly.
4. Generate `config.example.yaml` from §6.
5. Implement Phase 1 only. Do not touch Phase 2+ until I review Phase 1.
6. For Phase 1, write the tests in §9 first, then the code to pass them.
7. When Phase 1 is complete, output a short summary of (a) what was built, (b) what to run to verify, (c) any spec ambiguities you resolved and how.

Do not place any order. Do not enable live mode. Read-only API key only.
