# Phase 2 — `kalshi-take` (Confirm-Mode IOC Execution)

**Status:** Draft. Ships as the first signed-write code path in the workstation.

**Goal:** A single CLI command that places one IOC (immediate-or-cancel) order against a Kalshi market, gated by a pre-trade brief + human Y/N confirm + hard risk caps. Defaults to paper-trade mode; live requires explicit `--live` flag.

**Supersedes nothing.** This is the first Phase 2 module per the v2 workstation spec §5 ("Phase 2 — Confirm-mode execution"). Quoting (resting orders) and the position reconciler are deliberately deferred to Phase 2.1 and 2.2.

**Why it's the right minimum:** The scanner produces WATCH and PLAY candidates today. The operator needs a way to act on them (or take advantage of mispricings) that (a) cannot accidentally over-spend, (b) generates real fill data to unblock EV Phase D calibration, and (c) doesn't introduce a quoting loop the operator can't yet kill cleanly.

---

## 1. Vision

`kalshi-take` is the entire trading surface for Phase 2. The operator:

1. Sees a candidate (from the scanner, dashboard, or manual research).
2. Decides parameters (price, size).
3. Types a `kalshi-take` command with explicit flags.
4. Reviews a generated pre-trade brief that surfaces every risk relevant to this order.
5. Hits `y` to send (or `n` to abort).
6. Receives confirmation of the fill (or non-fill in paper mode), with the ledger updated to match.

The brief is the safety. The flag-driven invocation is the audit trail. The risk client is the floor.

---

## 2. Non-goals (Phase 2 v0)

Explicitly NOT in this spec — defer to follow-ups:

- **Resting quotes / post-only orders.** No `kalshi-quote`. IOC only.
- **Cancel command.** IOC auto-cancels unfilled; we don't rest anything to cancel.
- **Position reconciler.** Phase 2.1 will add `kalshi-positions` (compare ledger ↔ `/portfolio/positions` on startup).
- **Multi-leg / conditional orders.** Out of scope per v2 spec §8.
- **Background loops or auto-execution.** Phase 3.
- **Cancel-on-disconnect and dead-man switch.** master_spec §1.4, §1.5 — relevant once we have resting orders, not yet.
- **Order-state reconciliation after restart.** Not needed for IOC: every order has a terminal state within seconds.

---

## 3. Architecture

### File layout (new)

```
kalshi_ws/
├── api/
│   └── write.py                 # WriteClient: signed POST + paper variant
├── execution/
│   ├── __init__.py
│   ├── brief.py                 # render pre-trade brief
│   ├── orders.py                # OrderRequest / OrderResult dataclasses + idempotency
│   ├── risk_check.py            # risk.check_order(req, account, ledger)
│   └── take.py                  # the `kalshi-take` orchestrator (called from CLI)
├── state/
│   └── orders_repo.py           # read/write to orders + fills + positions tables
```

### File layout (modified)

```
kalshi_ws/
├── api/
│   └── models.py                # add Balance, OrderResponse, Fill
├── cli/
│   └── __init__.py              # add `kalshi-take` subcommand
└── config.py                    # add KALSHI_MODE (default "paper")
```

### Module ownership

- `api/write.py` is the **only** module allowed to send POST/DELETE. Per master_spec §1.1 and v2 spec §3.1: the read/write split is structural. Anything else trying to mutate exchange state is a bug.
- `execution/` owns the human-facing workflow: brief, risk check, take orchestration. CLI is a thin wrapper.
- `state/orders_repo.py` owns ledger writes for orders, fills, positions. Schema already defined in `state/schema.py`.

---

## 4. The CLI surface

```
python -m kalshi_ws kalshi-take \
    --ticker KXHIGHNY-26MAY16-T78 \
    --side yes \
    --action buy \
    --price 0.42 \
    --size 30 \
    [--live]                  # defaults to paper
    [--max-slippage-cents 3]  # optional safety override on price-band guard
    [--client-order-id <uuid>]  # rare; usually auto-generated
```

| Flag | Type | Required | Default | Notes |
|---|---|---|---|---|
| `--ticker` | str | yes | — | Kalshi market ticker |
| `--side` | enum | yes | — | `yes` or `no` |
| `--action` | enum | yes | — | `buy` or `sell` |
| `--price` | float | yes | — | Dollars (0.00–1.00). Rounded to nearest cent. |
| `--size` | int | yes | — | Number of contracts |
| `--live` | flag | no | False | Required to actually hit Kalshi. Otherwise paper. |
| `--max-slippage-cents` | int | no | 5 | Refuse if order price is more than N cents off mid |
| `--client-order-id` | str | no | auto UUID4 | Override only for testing/replay |

### CLI behavior

1. Parse flags. Reject malformed input (e.g., size < 1, price outside [0.01, 0.99]).
2. Load settings + connect read client.
3. Fetch live state: market snapshot, orderbook, `/portfolio/balance`, ledger snapshot.
4. Construct `OrderRequest`.
5. Run `risk.check_order(req, account, ledger)`. If REJECT → print reason, exit 1.
6. Render pre-trade brief to stdout.
7. Prompt `Confirm? [y/N]`. Default N.
8. If y: route to `LiveWriteClient` or `PaperWriteClient` based on `--live` flag.
9. Persist order + fill to ledger.
10. Print result line.

---

## 5. The pre-trade brief

The brief is the single most important UX surface in Phase 2. It must surface every fact the operator needs to refuse a bad trade.

### Format (fixed-width, terminal-friendly, no colors)

```
╭─ PRE-TRADE BRIEF ────────────────────────────────────────────────────╮
│ Ticker:       KXHIGHNY-26MAY16-T78                                   │
│ Market:       Will NYC daily high temp exceed 78°F on 2026-05-16?    │
│ Series:       KXHIGHNY  (Climate and Weather)                        │
│ Velocity:     LOW       (confidence: HIGH; tag is explicit)          │
│                                                                      │
│ Book (live):  yes_bid 0.40   yes_ask 0.43   spread 0.03   mid 0.415  │
│ Volume 24h:   147 contracts                                          │
│                                                                      │
│ Your order:   BUY 30 YES @ $0.42  IOC LIMIT                          │
│ Will cross:   yes (ask is 0.43, you're paying 0.42)                  │
│ Slippage:     +0.5¢ vs mid  ($0.15 worse than midpoint)              │
│ Notional:     $12.60                                                 │
│                                                                      │
│ Risk headroom:                                                       │
│   Single order:  $12.60 / $20.00  (63%)  OK                          │
│   Per-market:    $12.60 / $50.00  (25%)  OK   (current pos: $0)     │
│   Total open:    $147.60 / $400.00 (37%)  OK   (other pos: $135)    │
│   Daily P&L:     -$8.40 / -$35.00 (24%)  OK                          │
│   Balance req:   $12.60 / $691.83 avail  OK                          │
│                                                                      │
│ MODE:         PAPER                                                  │
│ Client order id: 7c8f3a92-...                                        │
╰──────────────────────────────────────────────────────────────────────╯
Confirm? [y/N]:
```

### Fields the brief MUST surface

- Ticker + market title + series + velocity + confidence
- Current book (bid/ask/spread/mid) + 24h volume
- Order spec (action, size, price, IOC) + whether it will cross the spread
- Slippage in cents and dollars
- Notional dollar value
- Each hard risk cap with current usage, post-trade usage, headroom — all four caps + balance
- Mode (PAPER / LIVE), shown distinctly
- The client order ID (for traceability)

### Refusal cases (no brief, no confirm)

If `risk.check_order()` returns REJECT, the CLI prints **only** the rejection reason and exits 1 — no brief, no prompt. We never let an operator confirm an order that would breach a hard cap.

---

## 6. `OrderRequest` and `OrderResult`

```python
# kalshi_ws/execution/orders.py

@dataclass(frozen=True)
class OrderRequest:
    ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    price_cents: int          # 1..99
    size: int                 # >= 1
    client_order_id: str      # UUID4; deterministic per CLI invocation
    type: Literal["limit_ioc"] = "limit_ioc"  # only one supported in Phase 2

    @property
    def notional_dollars(self) -> float:
        return self.size * self.price_cents / 100.0


@dataclass(frozen=True)
class OrderResult:
    request: OrderRequest
    accepted: bool             # exchange accepted (live) or paper recorded
    exchange_order_id: str | None
    filled_size: int           # 0 if no fill
    filled_avg_price_cents: int  # 0 if no fill
    fees_cents: int
    timestamp: datetime
    mode: Literal["paper", "live"]
    error: str | None          # populated when accepted=False
```

`client_order_id` is a UUID4 generated per invocation, written to ledger BEFORE the write client is called. If the write fails partway, the order_id is the recovery handle.

---

## 7. Risk check

```python
# kalshi_ws/execution/risk_check.py

@dataclass(frozen=True)
class AccountState:
    available_balance_dollars: float      # /portfolio/balance
    open_positions: dict[str, Position]   # from ledger
    daily_realized_pnl_dollars: float     # from ledger
    total_open_exposure_dollars: float    # sum across positions


@dataclass(frozen=True)
class RiskCheckResult:
    accepted: bool
    reasons: list[str]                    # one per violated cap; empty if accepted
    # Headroom (for the brief)
    single_order_used: float
    per_market_used: float
    total_open_used: float
    daily_loss_used: float
    balance_required: float


def check_order(
    req: OrderRequest,
    market_mid_cents: int,
    account: AccountState,
    *,
    max_slippage_cents: int,
) -> RiskCheckResult:
    """All hard caps from kalshi_ws.risk. ANY violation → accepted=False."""
```

### Gates evaluated (all must pass)

1. **Single order cap** (`risk.SINGLE_ORDER_MAX_USD = $20`): `notional ≤ $20`
2. **Minimum ticket size** (`risk.MIN_TICKET_SIZE_USD = $1`): `notional ≥ $1`
3. **Per-market position cap** (`risk.PER_MARKET_MAX_POSITION_USD = $50`): `existing_pos_notional + new_notional ≤ $50`
4. **Total open exposure** (`risk.MAX_TOTAL_OPEN_EXPOSURE_USD = $400`): `total_open + new_notional ≤ $400`
5. **Daily loss cap** (`risk.DAILY_LOSS_LIMIT_USD = $35`): `daily_realized_pnl > -$35`
6. **Balance check** (master_spec §1.8): `available_balance ≥ notional`
7. **Price-band guard** (master_spec §1.7): `|price_cents − mid_cents| ≤ max_slippage_cents`
8. **Price sanity**: `1 ≤ price_cents ≤ 99`
9. **Size sanity**: `size ≥ 1`

If ANY gate fails, return all failed reasons (not just first). This gives the operator a complete picture of why the order was refused.

---

## 8. Paper-trade mode

### What paper does

- Goes through every check, brief, and confirm prompt **exactly as live mode**.
- After the `y` confirm, instead of POSTing to Kalshi:
  - **If the order would cross** (limit price meets or beats the touch): simulate a full fill at the order limit price. Apply standard Kalshi maker/taker fee (the spec assumes taker for marketable IOC; fee = `0.07 × mid × (1-mid)` per contract).
  - **If the order would not cross**: simulate as "expired unfilled" (IOC behavior). `filled_size=0`.
- Writes the order + (optional) fill to paper-mode SQLite tables.

### Paper SQLite tables

Mirror the real tables with `paper_` prefix:

```
paper_orders, paper_fills, paper_positions
```

The dashboard agent may want to surface paper P&L separately. For now: same schema, separate tables. Phase 2.1 reconciler will ignore paper tables.

### Why a real `/portfolio/balance` call in paper mode

The pre-trade brief shows real account balance even in paper mode, because:
1. Risk gates assume the same balance whether paper or live.
2. Easier to graduate paper → live (no env switch changes constraint surface).
3. The "do I have enough?" sanity check is the same question regardless.

This requires API auth in paper mode. We already have it (Phase 0).

### Mode signaling

Default mode is **paper**. `--live` flag is required for any real POST. In addition:

- The brief renders `MODE: PAPER` or `MODE: LIVE` prominently.
- Config setting `KALSHI_MODE` (env var) can default to `live` for production setups but `--live` is still required per invocation. Belt-and-suspenders intentional.

---

## 9. Ledger writes

Per the existing `state/schema.py`:

### `orders` (existing) — written BEFORE the write client is called

```
order_id, ticker, side, action, price_cents, qty, status, created_at, updated_at
```

`status` starts as `submitted` (paper or live). Updated to `filled` / `partial` / `cancelled` / `rejected` after the write completes.

### `fills` (existing) — written after a successful fill

```
fill_id, order_id, ticker, side, action, price_cents, qty, fee_cents, filled_at
```

For IOC partial fills, write one row per filled lot (Kalshi may return multiple).

### `positions` (existing) — updated after fill

Recompute `yes_qty`, `no_qty`, `avg_yes_cost_cents`, `avg_no_cost_cents` based on the fill.

### `session_log` (existing) — append every kalshi-take invocation

```
ts, kind="kalshi-take", ticker, summary, reasoning
```

Where `summary` is "BUY 30 YES @ 0.42 PAPER → filled" or similar, and `reasoning` is the full pre-trade brief text. This is the audit trail.

---

## 10. Live mode — the Kalshi POST

### Endpoint

`POST /trade-api/v2/portfolio/orders` (Kalshi v2)

### Request shape (to be verified by probe before implementation)

```json
{
  "action": "buy",
  "buy_max_cost": 1260,           // cents, optional cap
  "client_order_id": "uuid",
  "count": 30,
  "side": "yes",
  "ticker": "KXHIGHNY-26MAY16-T78",
  "type": "limit",
  "yes_price": 42,                // cents
  "expiration_ts": null           // null = IOC behavior per Kalshi docs
}
```

Need to confirm during impl whether `expiration_ts: null` or a tiny duration gives IOC. The spec assumes IOC is achievable; if it's not, fall back to "place limit then cancel after 1s."

### Response handling

- 200 + order body: capture `order_id`, write to ledger, then poll `/portfolio/orders/{id}` to confirm fill or non-fill.
- 4xx: surface error verbatim, mark order as `rejected` in ledger.
- 5xx / network timeout: this is the dangerous case. Use `client_order_id` to GET the order back. If it exists on Kalshi, recover its state. If not, assume the POST never landed.

### Fill polling

After a successful POST, poll `/portfolio/orders/{order_id}` until status is terminal (filled, partial-then-expired, cancelled, expired). Timeout: 10 seconds. If still pending after timeout, log warning and trust Phase 2.1 reconciler to pick it up later. (In practice IOC orders terminate in <1s; if not, something is wrong.)

---

## 11. Failure modes

| Failure | Detection | Recovery |
|---|---|---|
| Risk gate refuses | Pre-flight `check_order` | Print reasons, exit 1. No write. |
| Operator types `n` | Prompt | Print "aborted", exit 0. No write. |
| Paper-mode "wouldn't fill" | Limit price doesn't cross | Log + write `expired_unfilled` to paper_orders. Exit 0. |
| Kalshi 4xx (e.g. insufficient balance) | Response status | Mark order `rejected`, log error, exit 1. |
| Kalshi 5xx | Response status | Mark order `pending_unknown`, attempt one GET via client_order_id. If found → recover state. If not → log and exit. |
| Network timeout on POST | httpx exception | Same as 5xx — attempt GET, otherwise pending_unknown. |
| Partial fill | Fill polling | Write `filled` row with partial qty + `expired_unfilled` for remainder. Update position. |
| Polling times out (>10s pending) | Polling | Log warning, leave order in `submitted` status. Phase 2.1 reconciler resolves. |

The key principle: **the ledger always tracks the operator's intent, even when the exchange response is uncertain.** Recovery is a separate problem.

---

## 12. Open items

Things to verify or decide during implementation:

1. **IOC encoding on Kalshi v2.** Probe `POST /portfolio/orders` and confirm the actual field for IOC semantics. Could be `expiration_ts: 0`, `type: "ioc"`, or `expiration_ts: now()+1s`.
2. **Buy/sell ↔ yes/no semantics.** Verify: "BUY YES at price P" means "I'll pay P to win $1 if YES resolves." "SELL YES at price P" means "I'll receive P now, owe $1 if YES resolves." Mirror for NO. Document precisely in `OrderRequest` docstring.
3. **Fee model.** master_spec assumes 0% maker fee + standard taker fee. Confirm against current Kalshi schedule. Update `risk_check` if there's a per-market fee variation that affects the balance check.
4. **Idempotency window.** Should the CLI refuse if the same client_order_id was used in the last hour? Last day? Today, the auto-UUID guarantees uniqueness, but if the operator passes `--client-order-id` to retry a known order, we want it to work. Default: no idempotency check from our side; rely on Kalshi's.
5. **Logging.** session_log captures every invocation. Should we also write a structured JSON log line to stderr for grep-ability? Suggest yes, in `data/kalshi-take.log`.

---

## 13. Implementation plan

### Pass 1 — read-only + paper happy path (~2 days)

1. Add `KALSHI_MODE` to `config.py` (default `paper`).
2. New module `kalshi_ws/api/write.py` with `WriteClient` ABC, `PaperWriteClient` (simulates IOC fill if marketable), `LiveWriteClient` (raises NotImplementedError for now).
3. New module `kalshi_ws/execution/orders.py` — `OrderRequest`, `OrderResult`.
4. New module `kalshi_ws/execution/risk_check.py` — `check_order`, all 9 gates.
5. New module `kalshi_ws/execution/brief.py` — render brief as text.
6. New module `kalshi_ws/execution/take.py` — orchestrator.
7. New module `kalshi_ws/state/orders_repo.py` — write to existing tables.
8. CLI: `kalshi-take` subcommand wired up.
9. Tests: each new module gets unit tests; one integration test using MockTransport for the read-side API.

### Pass 2 — live mode (~1 day)

1. Probe `POST /portfolio/orders` to confirm IOC semantics and field shape.
2. Implement `LiveWriteClient.place_order`.
3. Add response handling, fill polling, error mapping.
4. Add a test against MockTransport for live happy path + 4xx rejection + 5xx recovery.
5. Live smoke test on a tiny (`size=1`) Kalshi order to confirm round-trip.

### Pass 3 — operator polish (~0.5 day)

1. Logging to `data/kalshi-take.log`.
2. Pre-trade brief rendering refinement (alignment, spacing).
3. Update CLAUDE.md status section to "Phase 2 v0 shipped."
4. Add `kalshi-positions` placeholder spec for Phase 2.1.

Total: ~3.5 days for one operator working with Claude.

---

## 14. Acceptance criteria

The Phase 2 v0 ships when ALL of:

- `python -m kalshi_ws kalshi-take --help` shows the documented flags.
- A paper-mode invocation with valid parameters renders the brief, accepts `y`, writes to `paper_orders` and `paper_fills`, prints a result line.
- A paper-mode invocation that would breach any of the 9 risk gates is refused with the specific reason printed and no brief shown.
- A paper-mode invocation that would not cross the spread is recorded as `expired_unfilled` in `paper_orders` with no fill row.
- A live-mode invocation against a real Kalshi market with `size=1` places an order, captures the fill (or non-fill), writes to `orders` and `fills`, and updates `positions`.
- A live-mode 4xx response is surfaced with the exchange's error string.
- All existing tests still pass; new modules have unit + integration test coverage.
- `git diff --stat` shows changes contained to the file list in §3.
- `ruff` clean, `mypy --strict` clean on the new surface area.

---

## 15. What this unblocks

Once Phase 2 v0 lands:

- **EV Phase D calibration** can begin: each live fill produces a row in `fills` with realized adverse-selection measurable from a 30-min post-fill mid snapshot.
- **WATCH candidates become actionable.** Operator can take a Drake ALBUMEQUIV at $1.30/day mid EV, then observe whether realized P&L matches the model. Validates or invalidates `estimate_share` per-market.
- **Operator stops needing Kalshi UI.** A full session — scan, decide, execute, log — happens in the terminal.
- **Phase 2.1 reconciler and Phase 3 quoting** have a real `WriteClient` to extend.

---

## 16. Bottom line

`kalshi-take` is the smallest possible piece of execution machinery that's actually useful. It's pre-trade-gated, brief-confirmed, paper-defaulted, ledger-logged, and structurally walled off from the read path. It can't accidentally cost more than $20 on any single trade or $50 of total exposure per market. It can't bypass the daily $35 loss cap without editing `risk.py`.

It is the bridge from "we know what we'd do" to "we did it."
