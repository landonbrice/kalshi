# Info Velocity Tagging — Spec

**Purpose:** Manual classification of the top series we'd realistically trade for LIP, with `info_velocity` and `correlation_group` tags. Feeds `estimate_share()` and the spread × velocity structural gate in EV Formula v2.

**Lifespan:** This is the *bootstrap* tagging. Replace with empirical classification once we have ≥30 days of fill data showing realized adverse selection per series.

---

## 1. What info_velocity actually measures

Not "how interesting is this market" or "how much volume does it have." It measures one thing:

**How violently and unpredictably does new information arrive during a typical LIP holding window (3–14 days)?**

The reason this matters: LIP is paid for *resting* on the book over time. If info arrives in unpredictable bursts that shift fair value 10¢+, your standing orders get adversely selected — informed traders lift your stale ask or hit your stale bid before you can re-quote. Wider spreads exist in fast-info markets precisely because experienced MMs are pricing in adverse selection. When our formula ignores velocity, it reads "wide spread = juicy rebate" when reality is "wide spread = warning sign."

Three buckets:

| Bucket | Description | Realized adverse selection (est.) | Quote width tolerance |
|---|---|---|---|
| **LOW** | Info arrives ONLY at scheduled, public moments. Between releases, fair value drifts slowly. | 0.2–1.0¢ per fill | Wide quotes OK |
| **MEDIUM** | Continuous price discovery, but rarely shocks >5¢ in minutes. | 1.0–4.0¢ per fill | Moderate quotes |
| **HIGH** | News, leaks, embargo lifts, or human decisions can shift fair value 5–15¢ unpredictably. | 5.0–15.0¢ per fill | Quote only if very tight + small size |

---

## 2. Decision tree for classifying any series

Apply in order. First match wins.

**LOW if all four are true:**
1. The outcome resolves on a single public data source (NWS, BLS, official exchange close, etc.)
2. New information arrives at scheduled times only (daily/weekly/monthly release windows)
3. Between scheduled releases, no realistic event can move fair value >3¢
4. The settlement criteria are mechanical, not judgmental

→ Examples: NWS daily high temperature, monthly precipitation totals, end-of-week climate index.

**HIGH if any one is true:**
1. Resolution depends on a single human/group decision (award, ruling, announcement, draft pick)
2. Embargo lifts, leaks, or surprise drops can occur at any time
3. Live games or events with continuous outcome reveal
4. A single news headline could plausibly move price 8¢+
5. Insider information is plausibly held by some market participants
6. Resolution depends on third-party rankings whose data is leaked by third parties (Netflix, Spotify, Billboard)

→ Examples: Rotten Tomatoes scores, Netflix top 10, music chart rankings, election outcomes, sports outcomes (excluded series), celebrity announcements.

**MEDIUM is everything else.** It's the catch-all. Continuous data flow but bounded volatility. Examples: end-of-day crypto closes (high volume, generally orderly), monthly subscriber counts.

**Untagged series default to HIGH.** Conservative. Forces a manual decision before quoting actively.

---

## 3. Correlation grouping

A separate but related axis. Two markets share a `correlation_group` if news that moves one moves the others. The EV scanner uses this to prevent ranking N correlated markets as N independent bets.

Rule of thumb: if a single news headline would plausibly move both markets ≥5¢, they're correlated.

The Drake case: KXPUREALBUMS-ICE26MAY21-25K, KXPUREALBUMS-ICE26MAY21-35K, etc. — four different threshold markets on the same album release. Group them all under `correlation_group: drake_ice_2026may`. Total exposure across the group is capped at the per-market limit ($50), not 4 × $50 = $200.

---

## 4. The tagged series (v0 — bootstrap)

These are the series most likely to appear in our LIP scans, classified using §2. Reasoning included so you can override anything you disagree with. **Verify exact ticker prefixes via `GET /series` before relying on them — Kalshi rotates naming conventions.**

### Weather (LOW)

The workhorse. Multiple cities × daily/monthly markets × multi-bracket structures = many independent LIP pools. Single public data source (NWS Daily Climate Report). Between morning forecast and afternoon settlement, info arrives only via incremental model updates (GFS, ECMWF, HRRR).

```yaml
KXHIGHNY:                    # NYC daily high temp (Central Park)
  info_velocity: low
  correlation_group: nyc_weather_{date}   # per-day grouping across temp brackets
  data_source: NWS / KNYC station
  edge_rationale: GFS ensemble computable, no info shocks, model-driven fair value
  notes: Pull all orders by 14:00 ET (typical daily high time)

KXHIGHCHI:
  info_velocity: low
  correlation_group: chi_weather_{date}
  data_source: NWS / KORD station
  notes: Pull by 15:00 CT

KXHIGHMIA:
  info_velocity: low
  correlation_group: mia_weather_{date}
  notes: Hurricane season caveat — Aug-Oct can produce 5¢ moves on track shifts. Bump to MEDIUM Aug–Oct.

KXHIGHLAX:
  info_velocity: low
  correlation_group: lax_weather_{date}

KXHIGHDEN:
  info_velocity: low
  correlation_group: den_weather_{date}
  notes: Front Range weather can be erratic in shoulder seasons; consider MEDIUM Mar–May and Oct–Nov.

KXHIGHAUS:
  info_velocity: low
  correlation_group: aus_weather_{date}

KXHIGHPHIL:
  info_velocity: low
  correlation_group: phi_weather_{date}

KXLOWNY:                     # NYC daily low temp
  info_velocity: low
  correlation_group: nyc_weather_{date}
  notes: Same group as KXHIGHNY — correlated by same weather system

KXSNOWNY:                    # NYC monthly snowfall (Dec-Mar only)
  info_velocity: low
  correlation_group: nyc_snow_{month}
  notes: Long-dated (monthly settlement); LIP pool tends to be larger; quote tighter

KXRAINNYC:                   # NYC monthly rainfall
  info_velocity: low
  correlation_group: nyc_rain_{month}
```

### Crypto (MEDIUM)

Continuous price discovery, high volume, generally orderly. Macro shocks (Fed surprises, exchange failures) can be HIGH but are rare. Treat day-of-FOMC as HIGH in config override.

```yaml
KXBTCD:                      # Daily Bitcoin close
  info_velocity: medium
  correlation_group: btc_{date}
  data_source: Coinbase or composite index
  edge_rationale: Public price feed; competitive but quotable
  notes: Bump to HIGH on FOMC days, large macro releases, ETF flow surprise days

KXETHD:                      # Daily Ethereum close
  info_velocity: medium
  correlation_group: eth_{date}
  notes: Correlated with BTC; if quoting both, treat as same correlation_group on news days

KXBTCW:                      # Weekly Bitcoin close
  info_velocity: medium
  correlation_group: btc_week_{date}
  notes: Long-dated version; smaller LIP pool but more reward-per-day potential
```

### Entertainment — RT Scores (HIGH)

Embargo lifts at unpredictable times. Single critic review can move 5¢. Surprise scores happen weekly. Pro density is real here.

```yaml
KXMOVIESCORE:                # Rotten Tomatoes critic scores (verify exact prefix)
  info_velocity: high
  correlation_group: movie_{movie_id}_rt
  data_source: rottentomatoes.com
  edge_rationale: NONE for us; pros (ZubbyBadger, Gaeten Dugas) live here
  strategy: Only quote in dormant phase (≥3 weeks pre-release). Auto-pull on embargo lift.
  notes: Default to SKIP unless wide spread + far settlement. Even then, small size only.
```

### Entertainment — Streaming Charts (HIGH)

Third-party data leaks (FlixPatrol etc.) precede official Netflix Tuesday release. Smart money trades Monday afternoon based on viewership data. We're the slow side.

```yaml
KXNETFLIXTOP:                # Netflix global top 10 weekly
  info_velocity: high
  correlation_group: netflix_{week}
  data_source: Netflix top10.com (Tuesday release)
  notes: AVOID quoting from Saturday onward. Pre-Friday quoting only, with hard pull rule.

KXTOPALBUM:                  # Billboard album chart
  info_velocity: high
  correlation_group: billboard_album_{week}
  data_source: Billboard Tuesday release
  notes: Same pattern as Netflix — pre-Friday only.

KXSPOTIFYWRAP:               # Spotify Wrapped (annual)
  info_velocity: high
  correlation_group: spotify_wrap_{year}
  data_source: Spotify late November release
  notes: LOW during Q1-Q3 (no info flow), HIGH starting Oct. Override per-quarter.
```

### Entertainment — Awards (HIGH near event, LOW far from)

Oscars/Emmys/Grammys: months out, info is leaked nominee speculation and trade press, slow drift. Days before ceremony: predictions firm up, sharp money piles in.

```yaml
KXOSCAR:                     # Academy Awards categories
  info_velocity: high          # default conservative
  correlation_group: oscars_{year}_{category}
  velocity_overrides:
    - condition: days_to_ceremony > 60
      info_velocity: medium    # slower drift, less news flow
  notes: Best Picture / Best Actor are pro-dense; technical categories (Sound Editing, etc.) are thinner
```

### Sports (HIGH — most excluded by maker fee)

Even non-excluded series (MLB game-level if it exists) are HIGH velocity: lineups, weather, injury news, live game scoring. Our edge here is domain-specific only.

```yaml
KXMLBGAME:                   # MLB game-level (verify maker fee status)
  info_velocity: high
  correlation_group: mlb_{game_id}
  edge_rationale: Lando's baseball-platform domain knowledge (pitcher health, fatigue patterns)
  notes: Only quote pre-game (≥4h before first pitch). Hard pull at lineup release.
```

### Politics (HIGH)

News-driven, often illiquid in normal times, spikes around events. Default avoid for LIP harvesting.

```yaml
KXPRES:                      # President-related markets
  info_velocity: high
  correlation_group: pres_{topic}
  notes: Default SKIP for LIP. Information asymmetry too high.

KXELECT:                     # Election outcomes
  info_velocity: high
  correlation_group: election_{race_id}
  notes: Default SKIP.
```

### Excluded series (record for clarity)

Listed in `config.yaml::excluded_series` already; included here so reviewers see why.

```yaml
KXNBA, KXNFLGAME, KXNHL, KXPGA, KXUSOPEN, KXFOMEN, KXFOWOMEN, KXUEFACL,
KXGDP, KXCPI, KXFEDDECISION, etc.:
  info_velocity: high
  excluded_reason: maker_fee
  notes: Maker fee compresses LIP rebate edge below adverse selection cost. Never quote.
```

---

## 5. Velocity overrides — when defaults aren't enough

Some series flip velocity based on calendar conditions. Encode these as `velocity_overrides` in the config:

```yaml
KXHIGHMIA:
  info_velocity: low
  velocity_overrides:
    - condition: month in [8, 9, 10]
      info_velocity: medium
      reason: hurricane season

KXBTCD:
  info_velocity: medium
  velocity_overrides:
    - condition: is_fomc_day(date)
      info_velocity: high
      reason: macro shock risk

KXSPOTIFYWRAP:
  info_velocity: high
  velocity_overrides:
    - condition: month <= 9
      info_velocity: low
      reason: no info flow until late October
```

The EV evaluator resolves overrides at scan time. If any override matches, use the override velocity instead of the default.

---

## 6. Tagging confidence

Each tag has implicit confidence. We're guessing more on some than others. To be honest about it, add a `confidence` field:

| Confidence | Meaning | When to use |
|---|---|---|
| HIGH | Multiple historical precedents observed | Weather markets — we've all seen weather LIP behavior before |
| MEDIUM | Reasonable inference from structure | Crypto daily — we know how price feeds work |
| LOW | Plausible guess, expect to revise | New series with no track record |

```yaml
KXHIGHNY:
  info_velocity: low
  confidence: high

KXTOPALBUM:
  info_velocity: high
  confidence: medium       # we know charts move on data leaks but realized AS unmeasured

KXSPOTIFYWRAP:
  info_velocity: high
  confidence: low          # untested by us, override schedule is speculative
```

Use in the EV formula: when `confidence: low`, widen the uncertainty bounds (ev_low and ev_high) by an extra 50%. This propagates classification uncertainty into the rankings.

---

## 7. Where this lives

Option A: standalone file `config/series_velocity.yaml`, imported by the EV evaluator at startup. Hot-reload like `config.yaml`.

Option B: merge into existing `config.yaml` under a `series:` map.

Recommend Option A. The series tag list will grow to 50+ entries. Keeping it separate keeps `config.yaml` readable.

Schema:

```yaml
# config/series_velocity.yaml
defaults:
  info_velocity: high          # untagged series default
  confidence: low

series:
  KXHIGHNY:
    info_velocity: low
    confidence: high
    correlation_group_template: "nyc_weather_{date}"
    velocity_overrides: []
    notes: "Central Park station; pull orders by 14:00 ET"
  
  # ... etc
```

---

## 8. Review cadence

- **Weekly:** review any series that produced an ANOMALY flag in the last 7 days. Likely a velocity misclassification.
- **Monthly:** review series with `confidence: low` against any realized fill data. Promote/demote as data arrives.
- **Quarterly:** review the whole list. Promote tags from MEDIUM-LOW → HIGH confidence as data accumulates. Add new series that have appeared in scans.

---

## 9. The empirical handoff

Once we have ≥30 days of fill data with realized adverse-selection measurements per series:

1. For each series with ≥20 fills, compute median adverse_move_cents at 30-min mark
2. Bucket: <1¢ = LOW, 1–4¢ = MEDIUM, >4¢ = HIGH
3. Compare bucket to manual tag
4. Any mismatch is a classification correction OR a market-structure shift

The manual tags become a *prior*; the data becomes the *posterior*. After 90 days, the empirical buckets should drive the formula and the manual tags become reference only.

---

## 10. What's NOT being tagged

A few things on purpose:

- **Reward pool size.** Tag is about info characteristics, not LIP economics. The scanner reads pool size separately.
- **Personal interest.** Some markets you find interesting but have no edge in (e.g., political markets you read for fun). Velocity tag is structural; it doesn't care about interest.
- **Volume.** Volume is in the live snapshot, not the static tag. A series tagged LOW that has zero recent volume should still get rejected by the volume gate.
- **Liquidity quality.** Top-of-book depth, spread tightness in normal conditions — these vary too much to tag. They come from live data.

---

## 11. First action

Drop this spec next to `EV_FORMULA_v2_SPEC.md` in the repo. Give Claude Code one task:

> Create `config/series_velocity.yaml` from the entries in §4 of `INFO_VELOCITY_TAGGING.md`. Wire it into the EV evaluator so that `series.info_velocity` and `series.confidence` are populated from this file at scan time. For any series not in the file, default per §7 schema. Implement the velocity_overrides resolver per §5.

Once that's running, the EV v2 scanner has the data it needs to start enforcing the spread × velocity gate. Drake fails on day one. Weather markets float to the top. That's the signal v2 is working.

---

## 12. Bottom line

Tagging is a one-time admin task that pays back every scan. The cost is ~30 minutes of judgment now. The payoff is the EV formula stops treating Drake-type markets as opportunities and the rankings actually represent what we can earn. If the file is wrong, it's editable in one place. If the file is missing, every market defaults to HIGH and the system fails closed — which is the right failure mode.
