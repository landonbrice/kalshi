# Kalshi Workstation

Agent-assisted trading workstation for Kalshi. See `docs/superpowers/specs/` for design.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # then fill in real values
```

`.env` must point `KALSHI_PRIVATE_KEY_PATH` at an RSA PKCS8 PEM file (e.g., `./secrets/kalshi-private-key.pem`). The `secrets/` directory is gitignored.

## Phase 0 complete

- [x] Repo skeleton (`pyproject.toml`, ruff, mypy strict, pytest)
- [x] Config loader (`kalshi_ws/config.py`)
- [x] Risk limits (`kalshi_ws/risk.py`)
- [x] RSA-PSS-SHA256 signing (`kalshi_ws/api/auth.py`)
- [x] Async Kalshi client (`kalshi_ws/api/client.py`)
- [x] SQLite ledger bootstrap (`kalshi_ws/state/`)
- [x] `python -m kalshi_ws hello` prints a real Kalshi market

## Smoke test

```bash
python -m kalshi_ws hello
```

Authenticates, prints one real market, and creates `data/kalshi.db` with the 7 ledger tables.

## Testing

```bash
pytest                                    # offline (httpx.MockTransport)
mypy kalshi_ws && mypy tests
ruff check .
```

Cassette infra was removed in Phase 1 prereqs. It will return when the first test needs to replay a real Kalshi response.

## Layout

```
kalshi_ws/
├── api/           # Kalshi REST/WS (auth, async client)
├── state/         # SQLite ledger (schema, connection)
├── cli/           # typer entrypoints
├── config.py      # pydantic-settings (.env loader)
├── risk.py        # hard limits (spec §4)
└── __main__.py    # `python -m kalshi_ws`
```

Phases 1-4 (intel, decision, execution, dashboard) are in `docs/superpowers/specs/` and will be planned independently.
