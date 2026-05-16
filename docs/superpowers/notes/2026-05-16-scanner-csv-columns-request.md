# Request to scanner agent: extend lip_candidates.csv with market-quality columns

**From:** dashboard session
**Date:** 2026-05-16
**Status:** open request — hand off to the scanner-agent session at your convenience.

## Ask

Add three columns to every row written by `scan-lip-loop` into `data/lip_candidates.csv`. These are already returned by Kalshi's `/markets/{ticker}` endpoint as part of the same response the scanner already reads — no new API calls required, just additional field pulls and CSV columns.

| New column | Kalshi field | Type | Notes |
|---|---|---|---|
| `volume_24h` | `volume_24h_fp` | float (contracts) | trailing-24h volume; primary "is this market alive?" signal |
| `volume_total` | `volume_fp` | float (contracts) | lifetime volume; useful for new vs. mature markets |
| `open_interest` | `open_interest_fp` | float (contracts) | resting positions; competitor MM commitment proxy |
| `liquidity_dollars` | `liquidity_dollars` | float (dollars) | Kalshi's bonded liquidity estimate; another health signal |

All four are already present in the `/markets/{ticker}` JSON response (verified 2026-05-16). Sample values from the live API for `KXNBARETURN-26OKCJWILLIAMS8-519`:

```
volume_fp: 1512.65
volume_24h_fp: 1404.89
open_interest_fp: 868.54
liquidity_dollars: 0.0000
```

## Why

The dashboard currently shows EV/share/spread/book/depth — enough to rank candidates by *theoretical* attractiveness, but no signal for whether a market is **trading**. A market with EV/day=$30 and zero 24h volume is a paper opportunity. The operator wants these columns to:

1. Sanity-check the top of the list — high EV with no volume = likely scanner noise
2. Filter out brand-new markets that haven't established a real book
3. Identify markets where the LIP rebate is the *only* reason anyone is making a market (potential trap)

## Where to add them

In `kalshi_ws/intel/lip_scanner.py`'s `write_candidates_csv` (or wherever the CSV columns list lives), extend `fields` to include the four new column names, then populate each row dict from the corresponding `Market` model field.

The `Market` model in `kalshi_ws/api/models.py` may need the new fields added (with `extra="allow"` they parse, but you'll want them typed for the scanner code). Suggested additions:

```python
class Market(BaseModel):
    ...
    volume_fp: float = 0.0  # lifetime contracts
    volume_24h_fp: float = 0.0  # trailing 24h
    open_interest_fp: float = 0.0
    liquidity_dollars: float = 0.0  # may be 0 for new markets
```

(Names matched Kalshi's API; the `_fp` suffix is their fixed-point indicator. Dashboard will rename to friendly column headers like `volume_24h` at display time.)

## Backward compatibility

The dashboard's `data_sources.py` reads CSV columns with `raw.get(col, default)` — adding new columns is forward-compatible and won't break anything. Once the columns are written, I'll wire the dashboard display side in a separate commit. No coordination needed beyond "let me know when the new columns are landing."

## Out of scope

- Computing trailing volume rates or velocity tiers — handle in EV math if useful (you already added `series velocity tags` in commit `bc81f6e`, so this overlaps; do what makes sense).
- Anything beyond just-pull-from-API-and-write-to-CSV. Dashboard display is owned by the dashboard session.
