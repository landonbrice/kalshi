# Kalshi Workstation

Agent-assisted trading workstation for Kalshi. See `docs/superpowers/specs/` for design.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # then fill in real values
```

## Smoke test (Phase 0)

```bash
python -m kalshi_ws hello KXNBA-25MAR03-NBA  # or any valid ticker
```
