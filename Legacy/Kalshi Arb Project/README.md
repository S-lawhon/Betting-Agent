# Betting Agent

Automated edge-detection and execution agent for Kalshi prediction markets,
using The Odds API as a fair-value reference.

> **Status:** Phase 0 — repo skeleton only. No live trading capability yet.

## Quick start

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Configure secrets
cp .env.example .env
# edit .env — add KALSHI_EMAIL, KALSHI_PASSWORD, ODDS_API_KEY

# 3. Run (Phase 0 just logs "startup_ok" and exits)
python -m src.main

# 4. Tests
pytest -q
```

## Project structure

```
├── src/                  # Application source
│   ├── main.py           # Entry point + orchestration
│   ├── logger_setup.py   # Structured JSON logging (structlog)
│   ├── utils.py          # TODO Phase 1: odds conversions, implied prob
│   ├── edge_calculator.py# TODO Phase 1: edge, EV, Kelly
│   ├── risk_manager.py   # TODO Phase 2: position sizing, daily loss guard
│   ├── kalshi_client.py  # TODO Phase 3: API wrapper (demo mode)
│   ├── odds_client.py    # TODO Phase 4: Odds API + 5-min cache
│   ├── matcher.py        # TODO Phase 5: Kalshi ↔ Odds API fuzzy matching
│   └── executor.py       # TODO Phase 7: order placement (live-gated)
├── tests/                # pytest test suite
├── scripts/              # CLI utilities (dashboard, backtest, export)
├── cowork/               # Cowork task / monitor / workflow YAMLs
├── data/
│   ├── schema/           # JSON Schema definitions for all JSONL outputs
│   │   ├── trade_log.schema.json
│   │   ├── unmatched_markets.schema.json
│   │   └── edge_history.schema.json
│   └── trade_logs/       # Runtime outputs (gitignored except .gitkeep)
├── config.yaml           # All non-secret configuration
└── .env.example          # Secret template — copy to .env
```

## Configuration

All tuneable parameters live in `config.yaml`. Secrets (API keys, passwords)
belong in `.env` only and are **never** committed.

Key settings:

| Key | Default | Notes |
|-----|---------|-------|
| `environment` | `demo` | Set to `live` + env var gate to enable execution |
| `edge.min_edge_pct` | `0.03` | 3% minimum edge to surface a bet |
| `risk.kelly_fraction` | `0.25` | Quarter Kelly stake sizing |
| `risk.max_daily_loss_pct` | `0.05` | 5% loss triggers 60-min cooldown |
| `odds_api.cache_ttl_seconds` | `300` | Protects monthly API quota |

## Development phases

| Phase | Status | Description |
|-------|--------|-------------|
| 0 | ✅ | Repo skeleton, config, logging |
| 1 | ⬜ | Edge calculator + odds utils |
| 2 | ⬜ | Risk manager + P&L ledger |
| 3 | ⬜ | Kalshi API client (demo) |
| 4 | ⬜ | Odds API client + caching |
| 5 | ⬜ | Matcher (moneyline) |
| 6 | ⬜ | Full paper-mode scan loop |
| 7 | ⬜ | Live executor (hard-gated) |
| 8 | ⬜ | Dashboard + Excel exports |
| 9 | ⬜ | Backtester |
| 10 | ⬜ | Bayesian / rolling learner |
| 11 | ⬜ | Cowork automation YAMLs |

## Safety

- Live trading requires `environment: live` in config **and**
  `I_UNDERSTAND_LIVE_TRADING=true` in `.env`.
- Daily loss breaker halts trading automatically (see `risk.max_daily_loss_pct`).
- All trades (paper and live) are logged with idempotency fingerprints.
