# EV Formula v2 — Spec

**Status:** Replaces v1 in `kalshi_ws/intel/ev.py` and gate logic in `kalshi_ws/intel/lip_scanner.py`.

**Trigger:** v1 ranked Drake KXPUREALBUMS at EV/day = $602.81 on $127.50 capital_locked. That's 473% daily return on a regulated US exchange. The formula is producing physically impossible numbers and silently auto-marking them PLAY.

---

## 1. Vision — what EV should actually compute

The EV formula's job is to output the **expected dollars-per-day-of-held-capital that we would realistically net** from quoting in a LIP-eligible market, given:

- The structure of Kalshi's LIP scoring (size × tightness × uptime → share)
- Our specific account size and risk caps
- Realistic competition (we are not the only MM)
- Realistic adverse selection (informed traders pick us off on news)
- Fees (we pay full taker fees when we eventually take liquidity to flatten)
- Opportunity cost (capital tied up isn't free)
- Settlement risk (markets void, resolve weird, miss scoring snapshots)

Properties the formula must have:

1. **Calibrated in magnitude.** A "good" market should rank around 1–4% daily return on capital. A "great" market 4–8%. Anything above 10% is an anomaly, not an opportunity.
2. **Decomposable.** Every output is `ev_per_day = lip_rebate + spread_capture - adverse_selection - fees - opp_cost`. Every component is logged separately. We can audit any ranking.
3. **Uncertainty-aware.** The biggest unknowns (competitor_size, adverse_selection) get bounds, not point estimates. Output includes a low/mid/high band.
4. **Empirically calibrated over time.** Today the unknowns are pessimistic defaults. As we accumulate fill data and realized LIP payouts, those defaults get replaced by estimates from our own data.
5. **Anomaly-gating.** Any output that violates magnitude bounds is flagged ANOMALY, not PLAY. Bugs in the formula or stale data should never result in capital deployment.

---

## 2. v1 audit — what's wrong and what it costs

| # | v1 Behavior | Why it's wrong | Cost on Drake example |
|---|---|---|---|
| 1 | `share = target / (target + top_of_book_size_avg)` | Top-of-book size is not LIP competition. It's "who's posted at the touch *right now*." Says nothing about who'll qualify under LIP scoring. Reads thin books as "no competition" when they often mean "no one is qualifying at all." | share = 0.982 vs realistic ~0.10–0.20 |
| 2 | `discount_factor_bps` is read but never applied | Kalshi reduces payout for wide quotes. df_bps = 5000 = up to 50% reduction. Ignoring this overstates reward by up to 2×. | Reward should be ~50% of nominal → another ~50% cut |
| 3 | `capital_locked = target × max(mid, 1-mid)` | Treats posting as one-sided. We post both bid AND ask; both have margin requirements. | Real capital ~$250 not $127.50 (2× under-reported) |
| 4 | Spread treated as gate threshold (≥2¢ = good) | Wide spread on news-driven markets is a warning, not opportunity. The market is saying "info will arrive during your hold window." | 44¢ spread on a celebrity-album market = adverse selection lake |
| 5 | No magnitude sanity check | EV/day of $602 on $127 capital silently passes. | Drake gets flagged PLAY when it should be ANOMALY |
| 6 | `volume_24h` not in gate | Markets with zero recent activity ranked equally with active markets. No-flow markets often have ambiguous LIP qualification. | Drake had effectively no recent trades; gate didn't notice |
| 7 | No correlation grouping | 4 Drake markets ranked independently; would deploy $1,000 across them treating as 4 independent bets. | 4× capital deployed for 1× actual diversification |
| 8 | No adverse selection term | Implicitly assumes adverse selection = 0. Defensible for weather; catastrophic for news markets. | Drake market: probably $10–30/day adverse cost ignored |
| 9 | No fees term | Ignores both LIP-program taker fees on flattening fills AND the standard fee schedule. | ~5–15% of gross reward eaten |
| 10 | risk.py limits not wired | max_capital=150 ignores hard $50/market rail. Scanner ranks plays we can't actually take. | Drake play sized at 250 contracts, would need to be capped to 100 |

**Total magnitude error on Drake:** v1 says $602/day. Best-estimate corrected value is **$5–15/day** (could even be negative net of adverse selection). v1 is overstating by 30–100×.

---

## 3. The new formula — full

### 3.1 Inputs

Same as v1 plus:
```
MarketSnapshot:
    ticker
    yes_bid, yes_ask                 # dollars
    status
    top_yes_size, top_no_size        # used only for diagnostics, NOT for share
    volume_24h                       # NEW: 24h contract volume
    trade_count_24h                  # NEW: 24h trade count
    last_trade_at                    # NEW: timestamp of most recent trade

LipProgram:
    market_ticker
    period_reward_cents
    target_size
    end_date
    discount_factor_bps              # used (was ignored)
    reference_spread_cents           # NEW: Kalshi's tightness reference
    min_uptime_pct                   # NEW: minimum uptime to qualify

Series:
    series_ticker
    info_velocity                    # NEW: "low" | "medium" | "high"
    event_ticker                     # NEW: for correlation grouping
    category                         # used (was diagnostic only)

AccountState:
    available_balance_usd            # NEW: real-time
    total_open_exposure_usd          # NEW: for portfolio gate
    daily_realized_pnl               # NEW: for daily loss cap
    open_correlated_groups           # NEW: dict[event_ticker → exposure]
```

### 3.2 Risk constants (hardcoded; mirror risk.py)

```
PER_MARKET_MAX_POSITION_USD = 50
MAX_TOTAL_OPEN_EXPOSURE_USD = 400
DAILY_LOSS_LIMIT_USD = 35
ANNUAL_HURDLE = 0.10                 # 10% opportunity cost rate
MAGNITUDE_CEILING_PCT = 0.05         # max plausible daily return = 5% of capital
MIN_VOLUME_24H = 10                  # contracts in last 24h to qualify
```

### 3.3 Step-by-step derivation

```python
# 1. Effective position size we'd actually deploy
#    Bounded by both LIP target and our per-market risk cap
mid = (yes_bid + yes_ask) / 2
max_size_by_capital = floor(PER_MARKET_MAX_POSITION_USD / max(mid, 1 - mid))
effective_size = min(target_size, max_size_by_capital)

# 2. Capital locked — properly account for two-sided posting
#    We post a bid (cost: size × bid_price) AND an ask (margin: size × (1 - ask_price))
#    Both legs lock margin until filled/cancelled
capital_locked = effective_size * (
    max(yes_bid, 0.05) +              # floor at 5¢ to handle near-zero books
    max(1 - yes_ask, 0.05)
)

# 3. Effective reward — apply discount factor and uptime
#    df_bps assumes our quote is at the edge of the discount band.
#    For pessimism, apply full discount unless we're explicitly modeling tightness.
discount_multiplier = 1 - (discount_factor_bps / 10000)
assumed_uptime = 0.80                 # we won't be on 24/7; 80% is realistic
effective_period_reward = period_reward * discount_multiplier * assumed_uptime

# 4. Share — replace the broken proxy with a category-segmented estimate
#    Until empirical data exists, use pessimistic defaults by category.
share = estimate_share(
    category=series.category,
    info_velocity=series.info_velocity,
    volume_24h=volume_24h,
    target_size=target_size,
    effective_size=effective_size,
)
# See §3.4 for share estimation function

# 5. Time component
days_remaining = max((end_date - now) / 86400, 0)
if days_remaining < 1:
    days_remaining = 1                # avoid div-by-zero; treat sub-day as 1 day

# 6. Gross LIP reward per day
lip_rebate_per_day = (effective_period_reward / days_remaining) * share

# 7. Spread capture estimate
#    Only counts when BOTH sides of our quote fill in a given session
#    Fill rate scales with volume
expected_fills_per_day = min(volume_24h * share, 2 * effective_size)
quote_width_cents = 2                 # we plan to quote 2¢ inside reference
spread_capture_per_fill = quote_width_cents / 100
spread_capture_per_day = expected_fills_per_day * spread_capture_per_fill

# 8. Adverse selection estimate
#    Heavily depends on info velocity. Slow markets ~0; fast markets dominant.
adverse_per_fill_cents = ADVERSE_TABLE[series.info_velocity]
    # low:    0.5¢
    # medium: 3.0¢
    # high:   10.0¢
adverse_selection_per_day = expected_fills_per_day * (adverse_per_fill_cents / 100)

# 9. Fees — Kalshi standard fee schedule applies to taker side
#    LIP-eligible standard markets have 0% maker fee; we pay only when we take.
#    Assume 30% of our fills end up being taken to flatten inventory.
taker_fill_rate = 0.30
fee_per_contract = 0.07 * mid * (1 - mid)
fees_per_day = expected_fills_per_day * taker_fill_rate * fee_per_contract

# 10. Opportunity cost
opp_cost_per_day = capital_locked * ANNUAL_HURDLE / 365

# 11. Net EV
ev_per_day = (
    lip_rebate_per_day
    + spread_capture_per_day
    - adverse_selection_per_day
    - fees_per_day
    - opp_cost_per_day
)

# 12. Uncertainty bounds
ev_low  = ev_per_day - bound_adjustment(low_case)
ev_high = ev_per_day + bound_adjustment(high_case)
# See §3.5
```

### 3.4 Share estimation function

```python
def estimate_share(category, info_velocity, volume_24h, target_size, effective_size):
    """
    Pessimistic, segmented share estimate.
    Until we have empirical data, use defaults that are wrong-in-our-favor only
    in narrow, defensible cases.
    """
    # Base assumed competitor density (in multiples of target_size)
    competitor_multiplier = {
        ("weather", "low"):           2.0,   # 2× target_size of competition
        ("weather", "medium"):        3.0,
        ("entertainment", "low"):     4.0,
        ("entertainment", "medium"):  5.0,
        ("entertainment", "high"):    8.0,
        ("sports", "high"):          10.0,
        ("crypto", "medium"):         6.0,
        ("politics", "high"):         8.0,
    }.get((category, info_velocity), 5.0)  # default 5×

    # Volume adjustment: low-volume markets have less competition but also
    # less qualifying flow. Don't reward this; just don't penalize.
    if volume_24h < 20:
        competitor_multiplier *= 0.7        # softer competition
    elif volume_24h > 500:
        competitor_multiplier *= 1.5        # crowded book

    competitor_size = target_size * competitor_multiplier
    share = effective_size / (effective_size + competitor_size)
    return min(share, 0.25)                 # cap at 25% — we are never alone
```

### 3.5 Uncertainty bounds

The share estimate and adverse selection cost are the two biggest unknowns. Output bounds:

```python
def bound_adjustment(direction):
    """
    Low case: share halves, adverse selection doubles.
    High case: share doubles (capped at 0.25), adverse selection halves.
    """
    if direction == "low":
        share_factor = 0.5
        adverse_factor = 2.0
    else:  # high
        share_factor = 2.0
        adverse_factor = 0.5
    # Recompute lip_rebate and adverse_selection with these factors;
    # difference from mid-case is the bound adjustment.
```

---

## 4. New gate logic

The gates run in this order. First failing reason wins. Anomaly gates can fire after PLAY conditions are met — anomaly always overrides PLAY.

```python
def evaluate(snapshot, lip, series, account):
    # ---- Sanity gates (data quality) ----
    if snapshot.status != "active":
        return SKIP("status not active")
    if snapshot.yes_bid <= 0.02 or snapshot.yes_ask >= 0.98:
        return SKIP("near-binary book")
    if snapshot.volume_24h < MIN_VOLUME_24H:
        return SKIP("no flow")
    if not snapshot.last_trade_at or age(snapshot.last_trade_at) > 48 * 3600:
        return SKIP("stale market")

    # ---- Structural gates (market type) ----
    spread = snapshot.yes_ask - snapshot.yes_bid
    if spread < 0.02:
        return SKIP("spread<2¢")
    if spread > 0.10 and series.info_velocity == "high":
        return SKIP("wide spread + high info velocity = trap")
    if days_remaining < 3:
        return SKIP("days_remaining<3")

    # ---- Risk gates (account-level limits) ----
    if capital_locked > PER_MARKET_MAX_POSITION_USD:
        return SKIP(f"capital_locked > ${PER_MARKET_MAX_POSITION_USD}")
    
    correlated_exposure = account.open_correlated_groups.get(series.event_ticker, 0)
    if correlated_exposure + capital_locked > PER_MARKET_MAX_POSITION_USD:
        return SKIP(f"correlated group exposure cap on {series.event_ticker}")

    if account.total_open_exposure + capital_locked > MAX_TOTAL_OPEN_EXPOSURE_USD:
        return SKIP("portfolio exposure cap")

    if account.daily_realized_pnl < -DAILY_LOSS_LIMIT_USD:
        return SKIP("daily loss cap hit")

    if capital_locked > account.available_balance_usd:
        return SKIP("insufficient balance")

    # ---- Magnitude sanity (anomaly detection) ----
    if ev_per_day > MAGNITUDE_CEILING_PCT * capital_locked:
        log_anomaly(
            ticker=snapshot.ticker,
            ev_per_day=ev_per_day,
            capital_locked=capital_locked,
            reason="EV/day exceeds 5% of capital — formula or data error suspected"
        )
        return ANOMALY("magnitude exceeds 5%/day — investigate before playing")

    if lip_rebate_per_day > effective_period_reward * 0.8:
        # claiming >80% of pool per day is implausible
        return ANOMALY("share estimate too aggressive")

    # ---- EV threshold ----
    if ev_per_day < MIN_EV_PER_DAY:
        return SKIP(f"ev_per_day=${ev_per_day:.2f} < ${MIN_EV_PER_DAY}")

    if ev_low < 0:
        # mid-case is positive but low bound negative — risky
        return WATCH("EV positive in midcase but negative in low case")

    return PLAY(
        ev_per_day=ev_per_day,
        ev_low=ev_low,
        ev_high=ev_high,
        capital_locked=capital_locked,
        components=components,
    )
```

Four possible outcomes per market:
- **PLAY** — passed all gates, mid-case positive, low bound also positive
- **WATCH** — mid-case positive but low bound negative; needs human review
- **SKIP** — failed a gate; not worth attention
- **ANOMALY** — formula produced an implausible number; INVESTIGATE before any action

---

## 5. Empirical calibration loop

The pessimistic defaults in `estimate_share()` and `ADVERSE_TABLE` are placeholders. They get replaced by data from our own trading once we have fills.

### 5.1 Data we need to collect

In Supabase, add three tables:

```sql
create table realized_lip_payouts (
  id bigserial primary key,
  market_ticker text,
  series_ticker text,
  category text,
  info_velocity text,
  period_start timestamptz,
  period_end timestamptz,
  our_avg_qualifying_size numeric,
  pool_total_usd numeric,
  our_payout_usd numeric,
  implied_competitor_multiplier numeric,    -- back-calculated
  recorded_at timestamptz default now()
);

create table realized_adverse_selection (
  id bigserial primary key,
  fill_id text,
  market_ticker text,
  series_ticker text,
  info_velocity text,
  fill_price numeric,
  midpoint_5min_after numeric,
  midpoint_30min_after numeric,
  adverse_move_cents numeric,
  recorded_at timestamptz default now()
);

create table formula_calibration (
  category text,
  info_velocity text,
  metric text,                              -- 'competitor_multiplier' | 'adverse_per_fill'
  estimate numeric,
  sample_size integer,
  updated_at timestamptz default now(),
  primary key (category, info_velocity, metric)
);
```

### 5.2 Calibration job

Daily cron (`strategy/calibrate.py`):

1. Pull last 30 days of `realized_lip_payouts`. For each (category, info_velocity) bucket with N ≥ 5 samples, compute median implied_competitor_multiplier. Update `formula_calibration`.

2. Pull last 30 days of `realized_adverse_selection`. For each (category, info_velocity) bucket with N ≥ 20 samples, compute mean adverse_move_cents at 30-min mark. Update `formula_calibration`.

3. The EV formula reads from `formula_calibration` instead of the hardcoded tables. Hardcoded tables stay as fallback for buckets with insufficient sample size.

### 5.3 Bootstrap period

Until we have ≥ 5 LIP payouts in any category, all share estimates use the pessimistic defaults. This means the first 2–4 weeks of live trading are deliberately conservative. That's the price of not knowing.

---

## 6. Output schema

Every market evaluated produces this record (written to `data/lip_candidates.csv` and Supabase `lip_evaluations`):

```python
@dataclass
class LipEvaluation:
    # Identification
    ticker: str
    series_ticker: str
    event_ticker: str
    category: str
    info_velocity: str
    
    # Decision
    decision: Literal["PLAY", "WATCH", "SKIP", "ANOMALY"]
    reason: str
    
    # Inputs (snapshot)
    yes_bid: float
    yes_ask: float
    mid: float
    spread: float
    volume_24h: int
    days_remaining: float
    period_reward: float
    discount_factor_bps: int
    target_size: int
    
    # Derived
    effective_size: int
    capital_locked: float
    share_estimate: float
    
    # EV decomposition
    lip_rebate_per_day: float
    spread_capture_per_day: float
    adverse_selection_per_day: float
    fees_per_day: float
    opp_cost_per_day: float
    ev_per_day: float
    ev_low: float
    ev_high: float
    
    # Calibration metadata
    competitor_multiplier_used: float
    adverse_per_fill_used: float
    calibration_source: str  # "default" | "empirical"
    
    # Timing
    evaluated_at: datetime
```

This schema is the audit trail. Every PLAY must be reproducible from this record. Every ANOMALY must be debuggable from this record.

---

## 7. Drake re-run under v2

Using the new formula on the same Drake snapshot:

```
ticker:              KXPUREALBUMS-ICE26MAY21-25K
category:            entertainment
info_velocity:       high                          (NEW classification)
yes_bid:             0.27
yes_ask:             0.71
spread:              0.44                          → SKIP "wide spread + high info velocity"
```

The structural gate catches it before any math runs. Drake is rejected immediately.

Even if we forced the math through (hypothetical, with spread=0.04 for the sake of seeing the formula work):

```
mid:                       0.49
effective_size:            min(250, floor(50/0.51)) = 98 contracts
capital_locked:            98 × (0.49 + 0.51) = $98.00     [vs v1's $127.50]
discount_multiplier:       1 - 0.5 = 0.50
assumed_uptime:            0.80
effective_period_reward:   $2,500 × 0.50 × 0.80 = $1,000   [vs v1's $2,500]
competitor_multiplier:     8.0 (entertainment + high velocity)
competitor_size:           250 × 8 = 2,000
share:                     98 / (98 + 2,000) = 0.0467 → cap at 0.25, actual 0.047
lip_rebate_per_day:        $1,000 / 4.07 × 0.047 = $11.54  [vs v1's $602.85]
expected_fills_per_day:    min(volume × share, 2 × 98) = assume 5 fills
spread_capture_per_day:    5 × 0.02 = $0.10
adverse_selection_per_day: 5 × 0.10 = $0.50
fees_per_day:              5 × 0.30 × (0.07 × 0.25) = $0.026
opp_cost_per_day:          $98 × 0.10/365 = $0.027
ev_per_day:                $11.54 + $0.10 - $0.50 - $0.026 - $0.027 = $11.09
ev_per_day_pct_capital:    $11.09 / $98 = 11.3%            → ANOMALY (>5%)
```

Even with the bad-data-cleaned spread, the magnitude gate catches that 11.3%/day is implausible and flags ANOMALY. We'd investigate before deploying capital.

Realistically, with spread closer to 4¢ AND share dropping toward 0.02 once real competitors arrive AND adverse selection at full 10¢ on news, Drake settles around **$2–5/day on $50–100 capital**. Maybe break-even. Maybe negative. **Either way, not the top of the rankings.**

---

## 8. Implementation phases

### Phase A — Bug fixes only (2 days)

Touch `kalshi_ws/intel/ev.py` and `lip_scanner.py`:

1. Apply `discount_factor_bps` to effective_period_reward
2. Fix `capital_locked` to two-sided formula
3. Wire risk.py constants into the scanner gates
4. Add magnitude sanity gate (ev_per_day < 5% × capital_locked, else ANOMALY)
5. Add volume_24h gate
6. Cap `share` at 0.25 hardcoded (still using old proxy temporarily)

Expected outcome: Drake stops showing $602/day. Most current top-ranked markets drop or get rejected. The number of PLAY candidates per scan shrinks dramatically.

This is **necessary and safe.** Ship this before anything else.

### Phase B — New share model (3 days)

1. Add `info_velocity` and `event_ticker` to series metadata. Manual tagging of top 20 series first; expand as needed.
2. Implement `estimate_share()` with category × velocity defaults
3. Add structural gates for spread × velocity combinations
4. Add `event_ticker` correlation grouping in `evaluate()`

Expected outcome: PLAY rankings reorder. Weather/slow markets rise; news-driven markets fall.

### Phase C — Full EV decomposition (3 days)

1. Add spread_capture, adverse_selection, fees, opp_cost terms
2. Add uncertainty bounds (ev_low, ev_high)
3. Add WATCH decision type for low-bound-negative
4. Update output schema to log all components

Expected outcome: rankings explain themselves; every PLAY has a component breakdown.

### Phase D — Empirical calibration (1 week of data + 2 days code)

1. Add three Supabase tables in §5.1
2. Add fill-time tracking to capture realized adverse selection
3. Add daily LIP payout reconciliation (compare expected vs realized)
4. Add `strategy/calibrate.py` cron
5. Switch from hardcoded `ADVERSE_TABLE` / `competitor_multiplier` defaults to `formula_calibration` table lookup

Expected outcome: formula gets less wrong each week as live data comes in.

---

## 9. What NOT to do

- **Do not** raise MAGNITUDE_CEILING_PCT to chase plays that look "obviously good but exceed the gate." The gate exists precisely to catch the not-actually-good plays.
- **Do not** keep v1's top-of-book competitor proxy as a "feature." Remove it entirely.
- **Do not** auto-execute on ANOMALY plays even after manual review. Every anomaly is a formula bug or stale data; fix the underlying issue first.
- **Do not** add new EV components (e.g., "user-supplied alpha factor") without writing them into the formula explicitly. Implicit alpha factors are how scoring formulas drift into wishful thinking.

---

## 10. Open questions for the operator

1. **Info velocity tagging**: Manual for now, but where does it live? Suggest `config.yaml` under a `series:` map with `velocity:` field. Tag the top 20 series we'd actually trade; everything else defaults to "high" (conservative).

2. **Uptime assumption**: 80% is a guess. After 1 week of running the executor, measure actual uptime and update.

3. **Quote width**: assumed 2¢ for spread_capture math. Real quote width should come from the quoter config per market.

4. **Taker fill rate (30%)**: rough guess for what fraction of our positions need active flattening. Calibrate from realized fill data once we have it.

5. **Drake-specific**: even if Drake passes future gates, should the 4-market correlation be treated as 1 unit with $50 cap, or 1 unit with $50 cap each up to a group cap of $100? Suggest group cap of $50 — same as single market.

---

## 11. Bottom line

v1 produced $602/day for Drake. v2 either rejects Drake outright at the spread-velocity gate or, if forced through with cleaned data, produces $2–11/day with an ANOMALY flag if the number still looks too high.

The goal isn't to find Drake-sized opportunities. They don't exist on a regulated exchange with mature competition. The goal is to find $5–25/day opportunities in slow-info markets where we can reliably collect LIP rebates without getting picked off, and to **trust the magnitude.** A real opportunity should look boring on the ranking. The exciting numbers in v1 were the bug.
