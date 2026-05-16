# Cross-Bracket Arbitrage Scanner — Vision & Design

- **Date:** 2026-05-16
- **Status:** Draft for operator review
- **Author:** Landon Brice (with Claude)
- **Relation to main spec:** Parallel sub-project to `2026-05-14-kalshi-workstation-design-v2.md`. Independent of LIP MM. Reuses existing module layout, SQLite ledger, FastAPI dashboard scaffolding, and risk-gate pattern.
- **Capital commitment in Phase A:** $0. Scanner is read-only, no order placement anywhere in the codepath.

---

## 1. Goal

Build a **read-only cross-bracket arbitrage scanner** for Kalshi prediction markets that identifies, logs, and visualizes opportunities where the sum of YES prices across mutually-exclusive-and-exhaustive (MEE) bracket markets deviates from $1.00 by more than fees + execution friction.

Phase A produces no orders. It produces *data and judgment*: enough observation to answer whether persistent capturable edge exists at a non-colocated Python script's latency in the operator's chosen series. If the data says yes, a separate Phase B spec proposes a passive executor. If the data says no, the sub-project archives cleanly.

This is **separate from LIP market-making**. LIP work proceeds on its own track per the main v2 spec; arb scanning runs alongside it as an independent intel module.

---

## 2. Theory & edge thesis

A Kalshi *event* whose *markets* form a mutually-exclusive-and-exhaustive (MEE) set has, by construction:

```
Σ P(market_i settles YES) = 1
```

so the sum of fair YES prices equals $1.00. Any deviation is a structural inefficiency.

**Why deviations exist in low-volume markets:**

- **Attention asymmetry**: MMs and informed traders concentrate quotes on modal brackets. Tail brackets get neglected — wide spreads, stale prices, sparse depth.
- **Per-market order books**: Kalshi maintains a separate book per market, not per event. A unified "sum to 1" constraint isn't enforced anywhere; only arbitrageurs enforce it.
- **Slow MMs**: many Kalshi MMs are humans or simple algos. They don't recompute the full bracket simultaneously when underlying expectations shift.
- **Asymmetric fee burdens**: in series where one side carries elevated fees, MMs withdraw and the sum drifts off-fair.

**Why it is not free money:**

- Fees consume ~10–20% of gross edge on a typical 5–10 leg arb.
- Multi-leg execution risk: no native combo orders on Kalshi → partial fills create directional exposure (this is a Phase B problem; Phase A only observes).
- Capital is tied up to settlement (days to weeks).
- Opportunity windows shrink as more arbers join the market.
- **Adverse selection**: a drifted sum may signal "one bracket repriced first on new information; others are lagging" — catching a falling knife, not an arb.

**Phase A bet**: bracket observability is poor enough on Kalshi that a patient observer can produce useful data on whether persistent capturable edge exists at retail latency. The data is the deliverable. No money is at risk to collect it.

---

## 3. Kalshi market geography — what to scan

### Tier 1 (initial whitelist)

- **`KXHIGHCHI`** — Chicago daily high temperature. 5°F brackets, open-tail extremes. Settled via NWS METAR (ORD daily summary). Deterministic single-source resolution. Low retail volume.
- **`KXHIGHNY`** — New York daily high temperature. Same structure, different weather regime → useful generality test across two series with the same fee schedule.

### Tier 2 (investigate later, not in initial whitelist)

- Other city `KXHIGH*` / `KXLOW*` series.
- `KXSNOW*` / `KXRAIN*` — daily precipitation accumulation, *only* where brackets are mutually-exclusive ranges and not overlapping "above X inches" structures. Per-series rule review required.
- Crypto end-of-period bracketed close prices, where structure is verified MEE and series is not on the excluded list.
- Niche economic data brackets not on the excluded list.

### Tier 3 (explicitly skip)

- Anything in `risk.py`'s excluded series list (sports, `KXFED*`, `KXCPI*`, `KXNBA*`, etc.) — fee structure destroys the math.
- Election margin brackets — frequent void conditions, sometimes non-exhaustive.
- Entertainment / awards markets — typically single-binary or non-exhaustive (ties, withdrawals).
- Any series with documented void conditions where all brackets could settle NO.

### Eligibility criteria (a series joins Tier 1 only when all hold)

1. **Mutually exclusive**: at most one bracket can settle YES.
2. **Exhaustive**: exactly one bracket *must* settle YES. No "void" outcome.
3. **Simultaneous settlement**: all brackets resolve at the same time on the same data source.
4. **Standard fee schedule**: not on the excluded list in `risk.py` §6.
5. **Stable rules text**: rules hash stored in `arb_series_meta`; alert on change.
6. **Manual rule-text verification**: operator has read the rules and signed off. Whitelist additions require a code commit, not a config flip.

---

## 4. Architecture

Plugs into the existing module layout. No structural surgery. Read-only paths only.

```
kalshi_ws/
├── intel/
│   └── arb/                          # NEW sub-package
│       ├── __init__.py
│       ├── scanner.py                # pure function: market state → opportunities
│       ├── eligibility.py            # loads + validates whitelist TOML, hash checks
│       ├── fees.py                   # Kalshi fee math, per-series overrides
│       ├── models.py                 # pydantic: Leg, Opportunity, ScanResult
│       └── persistence.py            # SQLite read/write for arb tables
├── config/                           # NEW directory
│   └── arb_series.toml               # whitelist with per-series metadata
├── state/
│   └── schema.py                     # +arb_opportunities, +arb_scans, +arb_series_meta
├── cli/
│   └── arb.py                        # /kalshi-arb-scan, /kalshi-arb-log, /kalshi-arb-status
└── dashboard/                        # NEW (Phase A.2) — same app as future LIP dashboard
    ├── app.py                        # FastAPI bootstrap
    └── pages/
        └── arb.py                    # arb-only page; LIP pages slot in alongside later
```

### Isolation rules (enforced by tests)

- `intel/arb/*` imports `api/read.py` only. `api/write.py` is unimportable. A pytest rule scans the AST of every module under `intel/arb/` and fails if `kalshi_ws.api.write` (or any submodule of it) appears in the import graph.
- `scanner.py` is a pure function `(list[EventWithMarkets]) → list[Opportunity]`. No I/O, no DB, no logging side effects. The caller (CLI or scheduled job) is responsible for fetch and persist.
- All scanner state lives in SQLite. No in-memory caches that could hide stale data.

### Data flow per scan tick

```
CLI / scheduled job
   │
   ▼
api/read.py  ───►  fetch markets for whitelisted series (async, parallel per event)
   │
   ▼
intel/arb/scanner.py  ───►  compute sum_asks, sum_bids, fees, edge per event
   │
   ▼
intel/arb/persistence.py  ───►  write arb_scans row + arb_opportunities rows
   │
   ▼
SQLite
   │
   ▼
dashboard/pages/arb.py  ◄───  reads SQLite, renders page
```

---

## 5. Data model

```sql
CREATE TABLE arb_scans (
    id INTEGER PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP NOT NULL,
    series_scanned INTEGER NOT NULL,
    events_scanned INTEGER NOT NULL,
    opportunities_logged INTEGER NOT NULL,
    errors_count INTEGER NOT NULL,
    errors_json TEXT
);

CREATE TABLE arb_opportunities (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL REFERENCES arb_scans(id),
    captured_at TIMESTAMP NOT NULL,
    series_ticker TEXT NOT NULL,
    event_ticker TEXT NOT NULL,
    leg_count INTEGER NOT NULL,
    sum_asks_cents INTEGER NOT NULL,
    sum_bids_cents INTEGER NOT NULL,
    total_fee_buy_cents INTEGER NOT NULL,
    total_fee_sell_cents INTEGER NOT NULL,
    net_edge_buy_cents INTEGER NOT NULL,      -- 100 − sum_asks − fees; >0 = profitable buy
    net_edge_sell_cents INTEGER NOT NULL,     -- sum_bids − 100 − fees; >0 = profitable sell
    min_ask_depth_contracts INTEGER NOT NULL, -- min(qty at top ask across legs)
    min_bid_depth_contracts INTEGER NOT NULL,
    seconds_to_settlement INTEGER NOT NULL,
    apr_buy REAL,                              -- (edge / cost) × (365 × 86400 / seconds_to_settle)
    would_have_fired_buy BOOLEAN NOT NULL,
    would_have_fired_sell BOOLEAN NOT NULL,
    legs_json TEXT NOT NULL                    -- per-leg snapshot: ticker, top 3 levels both sides, sizes
);

CREATE TABLE arb_series_meta (
    series_ticker TEXT PRIMARY KEY,
    status TEXT NOT NULL,                      -- 'active' | 'paused' | 'archived'
    fee_schedule TEXT NOT NULL,                -- 'standard' or named override
    settlement_source TEXT NOT NULL,
    void_conditions TEXT,
    last_verified_at TIMESTAMP NOT NULL,
    last_rules_hash TEXT,
    notes TEXT
);

CREATE INDEX idx_arb_opp_captured ON arb_opportunities(captured_at);
CREATE INDEX idx_arb_opp_fired ON arb_opportunities(would_have_fired_buy, captured_at);
CREATE INDEX idx_arb_opp_event ON arb_opportunities(event_ticker, captured_at);
```

**Volume sanity**: 30s scan × 2 series × ~3 events/series = ~17k opportunity rows/day. SQLite handles years of this. No rollup table needed in Phase A.

---

## 6. Fee model

Standard Kalshi formula per leg, computed in `intel/arb/fees.py` as `Decimal`-typed math:

```
fee_per_contract = ceil_to_cent(0.07 × price × (1 − price))
```

Per-series overrides via `config/arb_series.toml` if Phase A reveals discrepancies between formula and realized fees. None expected for Tier 1 weather series.

### Example fee impact

10-bracket buy-side arb, average leg price $0.10:
- Per leg: `ceil(0.07 × 0.10 × 0.90)` = $0.0063 → $0.01 / contract
- 10 legs × $0.01 = **$0.10 of fees per $1 of payoff**
- Required: `Σ asks < $0.88` to clear a $0.02 net edge minimum

This means the "actionable" threshold is much tighter than `Σ ≠ 1`. The scanner computes the exact figure per opportunity.

### Example `arb_series.toml`

```toml
[KXHIGHCHI]
status = "active"
fee_schedule = "standard"
settlement_source = "NWS METAR ORD daily summary"
void_conditions = "None expected; flag if METAR missing at 23:59 CT"
last_verified_at = "2026-05-16"
notes = "5°F brackets between configured floor and ceiling, plus open-tail brackets. Verified rules text 2026-05-16."

[KXHIGHNY]
status = "active"
fee_schedule = "standard"
settlement_source = "NWS METAR LGA daily summary"
void_conditions = "None expected"
last_verified_at = "2026-05-16"
notes = "Same structure as Chicago; LGA observation."
```

`risk.py` gains `verify_arb_series_allowed(ticker) -> None` which raises if the ticker is not present in this file with `status = "active"`. Loaded once at module import. Whitelist edits require a commit, not a runtime config flip.

---

## 7. UI / decision loop

### Phase A.1 — CLI only (week 1)

`rich`-formatted tables via `typer` commands:

- `/kalshi-arb-scan` — run one scan cycle; print summary table; persist.
- `/kalshi-arb-log [--since=1h] [--series=KXHIGHCHI] [--actionable]` — query recent opportunities.
- `/kalshi-arb-status` — health: last scan time, opportunities today, per-series counts, rule-hash drift alerts.

### Phase A.2 — FastAPI page (week 2)

Single page, Tailscale-bound, auto-refresh 10s. Same FastAPI app that the main-spec Phase 4 dashboard will use; LIP pages slot in alongside this one when that phase lands.

**Sections:**

1. **Live opportunities** — current scan's actionable opportunities table. Per row: event, leg count, Σ asks, net edge, min depth, APR-equivalent, seconds to settlement. Click → detail.
2. **Detail view** — leg-by-leg breakdown of one opportunity: per-leg ticker, top 3 bid/ask levels, size, fee, contribution to sum. L2 ladder snapshot. Time-series of `sum_asks` for this event over the last hour.
3. **History (rolling 7d)** — per-series stats: opportunities per day, hit rate, edge percentile distribution, time-of-day heatmap.
4. **Decision loop** —
   - "Pause series" / "Resume series" buttons. Writes to `arb_series.toml` via a small API endpoint, requires confirmation. Hash and timestamp updated.
   - "Phase B readiness scorecard" — banner showing each gate criterion (§10) with current value vs. threshold. Turns green when all gates pass over a 14-day window.
   - "Rule-hash drift alerts" — series whose rules hash has changed since `last_verified_at`. Operator must re-verify and confirm.

### Decisions the operator makes via UI

- Whitelist additions (must be committed to TOML by operator; UI only triggers Pause/Resume on already-whitelisted entries).
- Polling cadence adjustments — environment variable, no code change.
- Edge threshold for "actionable" — environment variable.
- Phase B go/no-go — by reading the scorecard.

---

## 8. Key tradeoffs

| Decision | Phase A choice | Why | When to revisit |
|---|---|---|---|
| REST poll vs. WebSocket | REST @ 30s | Simple, debuggable, sufficient for "do windows exist" question | If A.3 data shows median window <30s, move to WS in Phase B |
| Top-of-book vs. full L2 | Top for trigger signal; full L2 captured in snapshot | Cheaper scan; snapshot enables post-hoc depth analysis | Full L2 needed for Phase B execution sizing |
| Async vs. sync | Async (`httpx`) | Matches the rest of the codebase; per-event scans parallelize | n/a |
| Tight vs. loose whitelist | Tight (2 series) | False-positive arbs are expensive to debug; per-series data is more interpretable | After 4 weeks if Tier 2 looks promising |
| APR-equiv vs. raw cents | Both; threshold uses APR | $0.02 edge over 1 day ≠ $0.02 over 30 days | n/a |
| Per-event scan vs. prioritization | All events on each tick | Phase A is observation; prioritization biases data | Phase B may prioritize near-settlement events |
| Dashboard now vs. CLI-only | Both: CLI week 1, UI week 2 | Decision loop needs visual surface; operator requested it | n/a |
| Same FastAPI app as LIP dashboard vs. separate | Same | Pay setup cost once; LIP pages join alongside | n/a |

---

## 9. Risks specific to this sub-project

- **Misclassified series**: a "verified exhaustive" series turns out to have a void condition. *Mitigation*: `rules_hash` recompute on every scan tick; alert if hash changes from `arb_series_meta.last_rules_hash`. Manual rule re-read on alert.
- **Fee schedule drift**: Kalshi changes the formula or introduces variants. *Mitigation*: hardcode formula with `last_verified` constant; recompute once in Phase B against realized fees from order receipts when execution exists.
- **API rate limits**: 30s scan × 2 series × ~3 events × ~12 legs ≈ 30 requests per 30s = 1 req/s on average. Well under Kalshi's typical limits. *Mitigation*: log rate-limit headers; alert if usage exceeds 50% of the limit.
- **Scope creep into Tier 2**: tempting and dangerous. *Mitigation*: whitelist edits require a commit; PRs to self for additions.
- **Time-of-day observation bias**: scan runs only when the operator's machine is on. Misses overnight/weekend windows. *Mitigation*: document the bias; if A.3 scorecard is marginal, set up `launchd` to run scanner 24/7 *for observation only* before deciding Phase B.
- **Self-referential bias (Phase B concern, flagged here)**: in Phase B, the operator's own fills close opportunities the scanner would have logged → ledger-aware scanner needed in Phase B.
- **Adverse selection in logged opportunities**: a dislocation often signals "one leg has new info, others are stale" rather than "free money." Phase A logs both buy and sell edges so post-hoc analysis can distinguish persistent dislocations from information shocks.

---

## 10. Phasing

| Phase | Time | Deliverable | Gate to next |
|---|---|---|---|
| **A.0** Foundation | 2 days | Schema migration, stub scanner, TOML loader, import-isolation test | Tests pass; empty whitelist scan returns no opps without error |
| **A.1** Backend | 3 days | Live scanner, fees, persistence, CLI commands, 2-series whitelist | One scan tick produces sane numbers against live read-only account |
| **A.2** UI | 3 days | FastAPI app shell + arb page, Tailscale-bound, pause/resume + scorecard | Page reachable on phone; pause/resume series via UI works end-to-end |
| **A.3** Observation | 2–4 weeks | No new code; data accumulates; daily glance at UI | Pre-defined Phase B scorecard thresholds met or not |
| **A.4** Go/no-go | 1 day | Postmortem doc: what the data said; next-step decision | Decision: Phase B (executor) spec written **OR** sub-project archived |

**Total time to first data: ~8 working days. Total time to decision: ~6 weeks calendar.**

### Phase B readiness scorecard

Phase B (passive executor) is green-lit only if **all** are true over the most recent 14-day rolling window of Phase A.3:

- ≥10 actionable buy-side opportunities per week (`would_have_fired_buy = true`).
- Median opportunity window ≥30 seconds (window = time from first appearance until sum returns inside threshold).
- ≥2 distinct series each contributing ≥3 opportunities per week.
- Median APR-equivalent on actionable opportunities ≥20%.
- Zero detected exhaustivity violations (rules_hash changes confirmed safe, no void scenarios observed).

If any gate fails: archive sub-project and write postmortem. The scanner infrastructure stays in the repo as a passive intel module; near-zero cost to leave running.

---

## 11. Tech stack

Unchanged from main v2 spec:

- **Python 3.12+**, async (`httpx`)
- **SQLite** ledger (stdlib `sqlite3`)
- **`pydantic` v2** for typed models
- **`pydantic-settings`** for `.env` loading
- **`typer`** for CLI entrypoints; Claude Code slash commands wrap these
- **`FastAPI`** for the dashboard (Phase A.2)
- **`pytest`** + `pytest-recording` for tests; never hits live API in tests
- **`ruff`** + **`mypy --strict`**
- **`tomllib`** (stdlib) for whitelist parsing
- **`rich`** for CLI tables
- **`hashlib`** for rules-text hashing

No new dependencies required beyond what the main v2 spec already pulls in.

---

## 12. Repository layout deltas

Net additions relative to main v2 spec:

```
kalshi_ws/intel/arb/                  # NEW sub-package
    __init__.py
    scanner.py
    eligibility.py
    fees.py
    models.py
    persistence.py

kalshi_ws/config/                     # NEW directory
    arb_series.toml

kalshi_ws/cli/arb.py                  # NEW

kalshi_ws/dashboard/app.py            # NEW (shared with future LIP dashboard)
kalshi_ws/dashboard/pages/arb.py      # NEW

tests/intel/arb/                      # NEW
    test_scanner.py
    test_fees.py
    test_eligibility.py
    test_import_isolation.py

docs/superpowers/specs/2026-05-16-bracket-arb-scanner-design.md  # this file
docs/superpowers/plans/                                          # plan to be written next
```

---

## 13. Anti-patterns to reject

- Importing `api/write.py` from any module under `intel/arb/`. Enforced by test.
- Auto-discovery of new bracket series from API metadata. Whitelist only.
- Title-parsing for exhaustivity. Manual rule-text review only.
- "Smart" cache between API reads and scanner. Recompute every tick.
- Multi-leg execution logic anywhere in Phase A. Out of scope.
- LLM ranking of opportunities. Heuristics only.
- Whitelist edits via UI without a commit. UI may pause/resume; only commits add series.
- Loosening the Phase B scorecard to "just try it." Either the data clears the bar or the sub-project archives.
- Treating opportunities as guaranteed profit. Phase A is observation; "would_have_fired" is an *if-we-could-have* counterfactual, not a realized P&L.

---

## 14. Out of scope (Phase A)

- Any order placement, anywhere in the codepath.
- WebSocket-based scanning (reuse main-spec's `api/ws.py` only when it lands, and only in Phase B).
- Auto-series-discovery.
- Cross-event correlation arbs.
- Sell-side execution (Phase B+ if buy-side proves out first).
- Backtesting against historical L2 (no such data; Phase A *is* the data collection).
- Notifications/alerts (UI is sufficient for Phase A).
- ML or LLM ranking.
- 24/7 cloud deployment.

---

## 15. Open items for operator review

1. **Initial whitelist** — confirm `KXHIGHCHI` + `KXHIGHNY`, or swap in different cities / a precip series.
2. **Polling cadence** — 30s default. Acceptable, or want 10s / 60s?
3. **24/7 observation** — operator's machine on continuously, or use `launchd` from Phase A.1 to avoid time-of-day bias in observation?
4. **Edge thresholds** — proposed: log everything with `net_edge_buy ≥ −$0.05` (capture near-misses for distribution analysis); fire-flag at `≥$0.02 AND APR ≥20%`. Confirm or adjust.
5. **Phase B scorecard ambition** — §10 thresholds: are these where you want the bar, or tighter / looser?
6. **UI hosting** — same FastAPI process as the future LIP dashboard (recommended), or separate?

---

## 16. Success criteria

- **A.0 done when:** schema migration applied; empty-whitelist scan completes without error; import-isolation test passes.
- **A.1 done when:** scanner produces sane opportunity rows from one live read-only scan against both whitelisted series; CLI commands return correct data.
- **A.2 done when:** dashboard page reachable on phone via Tailscale; pause/resume series via UI works; Phase B scorecard banner renders with current values.
- **A.3 done when:** 14+ days of observation data accumulated; scorecard evaluated.
- **A.4 done when:** go/no-go decision committed in writing as postmortem doc.

Either outcome of A.4 is success. A null result is information.
