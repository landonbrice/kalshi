# Kalshi Trading Workstation

## What this project is

An **agent-assisted LIP market-making workstation** for Kalshi prediction markets — a Python toolkit invoked from Claude Code, not an autonomous bot. The operator (Landon) is the strategist; Claude + scanners, decision briefs, executors, and a ledger are the hands.

The line between this and a "bot": nothing runs continuously without explicit operator sanction. Strategy lives in the operator's head and the ongoing Claude conversation, not in config files.

## Operating context

- **Operator:** Landon Brice — solo, learning Kalshi market microstructure while building.
- **Capital:** $700 seed today; **target $3,000 to launch live MM, $5,000 once Phase 3 runs clean for a week** (per v2 spec).
- **Where it runs:** Local machine, scheduled hours (not 24/7). Sanctioned-mode quoting loops are the *normal* operating mode during a shift; outside those loops the system runs read-only scanners and confirm-mode take execution.
- **Primary edge thesis (v2):** **LIP rebate capture on a curated set of low-volume LIP-eligible markets**, primarily weather. Judgment-driven take plays in entertainment markets are a secondary edge. Continuous quote uptime is a first-class system property.

## Where decisions live

This file is **vision and direction only**. Concrete design decisions live in:

- `docs/superpowers/specs/2026-05-14-kalshi-workstation-design-v2.md` — **active spec**, supersedes v1. LIP rebate as primary edge, capital scale, sanctioned-mode as default.
- `docs/superpowers/specs/2026-05-14-kalshi-workstation-design.md` — original v1, kept for context.
- `master_spec.md` — comprehensive build spec with non-negotiables (hard loss caps in code, cancel-on-disconnect, dead-man switch, paper-trade default, infra choices).
- `EV_FORMULA_v2_SPEC.md` — the rewrite-the-math spec that drove the Phase A/B/C v2 EV rollout.
- `INFO_VELOCITY_TAGGING.md` — bootstrap series velocity classification feeding `estimate_share`.
- `docs/superpowers/plans/` — per-phase implementation plans.
- `docs/superpowers/specs/2026-05-16-bracket-arb-scanner-design.md` — bracket arbitrage scanner design (separate Phase 1 module, not LIP-related).

When the spec and this file disagree, the spec wins. When v2 and v1 disagree, v2 wins.

## Working principles

- **Safety before cleverness.** Small capital, real money, real exchange. Read-only code paths cannot place orders (enforced by module split: `api/read.py` vs eventual `api/write.py`). Risk limits live in `kalshi_ws/risk.py` and cannot be bypassed without editing it.
- **YAGNI hard.** Resist abstractions, frameworks, and infrastructure beyond what the current phase needs.
- **Modular and Claude-driven.** Clean module boundaries so each piece can be built, tested, and reasoned about in isolation. The operator drives most implementation through Claude Code; design with that in mind.
- **Human in the loop by default.** Autonomous loops exist only as explicitly sanctioned, time-bounded, capital-bounded scopes the operator starts and can kill.
- **Trust magnitudes.** Real LIP opportunities are 1–4% daily on capital. Anything above 5% is the formula breaking, not an edge. The magnitude-anomaly gate exists because v1 of the EV math hallucinated 473%-daily plays.

## What's shipped

Phase 0 — repo skeleton, auth, API client (read-side), SQLite ledger bootstrap, smoke test. ✅
Phase 1 — LIP scanner with EV v2 (A+B+C complete), velocity tagging, scan-lip-loop with atomic CSV + meta.json sidecar, FastAPI dashboard (parallel agent). ✅

**EV v2 rollout, complete:**
- Phase A: discount × uptime, two-sided capital, share hardcap, ANOMALY gate at 5% daily-return ceiling.
- Phase B: replaced share hardcap with category × `info_velocity` competitor model; spread × velocity structural gate.
- Phase C: full decomposition (lip_rebate + spread_capture − adverse_selection − fees − opp_cost), uncertainty bounds (ev_low, ev_high), WATCH decision for mid-positive/low-negative cases.
- Phase D (empirical calibration) blocks on executor — needs realized fill data.

**Today's live scan:** ~20 markets pass cheap gates. 0 PLAY, a few WATCHes (mid EV ~$1.30/day with negative low bounds), a few ANOMALYs (Drake-class markets still above 5% daily). This is the desired honest state — borderline markets surface as WATCH, no auto-deploys until Phase D recalibrates the share/adverse model from real fills.

## Current direction

**Phase 2 — confirm-mode execution.** Add `api/write.py` (signed POST/DELETE) behind `risk.py` enforcement, build `/kalshi-quote` and `/kalshi-take` with full pre-trade brief + Y/N operator confirm, capture every fill in the SQLite ledger. This unblocks Phase D calibration data.

Spec being written next (this session). Phase 3 (sanctioned auto-quoting) and Phase 4 (metrics/rebate tracking) follow.

## Parallel sessions

The dashboard work runs in a **separate concurrent agent** (under the same git user). Coordination rules:
- Backend session owns: `kalshi_ws/api/`, `kalshi_ws/intel/`, `kalshi_ws/state/`, `kalshi_ws/risk.py`, `kalshi_ws/config.py`, `kalshi_ws/cli/__init__.py`.
- Dashboard session owns: `kalshi_ws/dashboard/`, `tests/dashboard/`.
- CSV schema in `data/lip_candidates.csv` is the contract; columns can be added freely, renames are coordinated.
- Race condition observed on git commits — always check `git diff --cached` before commit, don't trust `-a`.

## Status (2026-05-16)

Phase 0 ✅. Phase 1 ✅ (LIP scanner + dashboard). Phase 2 spec in progress. EV v2 A+B+C shipped. Live scanner running with honest math — no PLAY hallucinations.
