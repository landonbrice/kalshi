# Kalshi Trading Workstation

## What this project is

An **agent-assisted trading workstation** for Kalshi prediction markets — a Python toolkit invoked from Claude Code, not an autonomous bot. The operator (Landon) is the strategist; Claude + a set of scanners, decision briefs, executors, and a ledger are the hands.

The line between this and a "bot": nothing runs continuously without explicit operator sanction. Strategy lives in the operator's head and the ongoing Claude conversation, not in config files.

## Operating context

- **Operator:** Landon Brice — solo, learning Kalshi market microstructure while building.
- **Capital:** $700 seed. Production Kalshi account, API key in hand.
- **Where it runs:** Local machine, scheduled hours (not 24/7).
- **Edge thesis:** Wide-spread market making in low-volume weather markets + stale-quote / mispricing taking in niche entertainment markets. Kalshi MM rebates captured opportunistically where eligible markets overlap, not as the primary driver.

## Where decisions live

This file is **vision only**. Concrete design decisions (architecture, risk limits, phasing, tech stack, repo layout, out-of-scope items) live in:

- `docs/superpowers/specs/2026-05-14-kalshi-workstation-design.md` — the approved design doc.

Implementation plans will live alongside it under `docs/superpowers/plans/`.

When a question has a real answer, it belongs in the spec or a plan, not here. When the spec and this file disagree, the spec wins.

## Working principles

- **Safety before cleverness.** Small capital, real money, real exchange. Read-only code paths must be incapable of placing orders. Risk limits live in one file and cannot be bypassed without editing it.
- **YAGNI hard.** This is a $700 system, not a hedge fund. Resist abstractions, frameworks, and infrastructure beyond what the current phase needs.
- **Modular and Claude-driven.** Clean module boundaries so each piece can be built, tested, and reasoned about in isolation. The operator drives most implementation through Claude Code; design with that in mind.
- **Human in the loop by default.** Autonomous loops exist only as explicitly sanctioned, time-bounded, capital-bounded scopes the operator starts and can kill.

## Status

Pre-implementation. Spec approved; no code written yet. Phase 0 (repo skeleton, API client stub, SQLite bootstrap) is the next concrete step once the spec is signed off and the repo is initialized with git.
