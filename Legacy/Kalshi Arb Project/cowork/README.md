# Cowork Automation — Betting Agent

This directory contains Cowork task and workflow YAML files for automating
the day-to-day operation of the betting agent.  Each file defines one
automation: what it does, when it runs, the individual steps, and how to
handle failures.

---

## Files at a glance

| File | Category | Default trigger | Purpose |
|------|----------|-----------------|---------|
| `run-paper-loop.yaml` | Trading | Every 5 min (8 am–midnight) | Core scan-and-decide cycle |
| `health-check.yaml` | Monitoring | Every 30 min (8 am–midnight) | Verify environment health |
| `daily-report.yaml` | Reporting | 9:00 am daily | Morning P&L briefing + CSV exports |
| `run-backtest.yaml` | Analysis | 11:00 pm daily | Replay recorded trades; export results |
| `retrain-learner.yaml` | ML | On demand | Retrain logistic calibration model |
| `weekly-data-export.yaml` | Reporting | 6:00 am every Monday | Archive week's data; rotate logs |

---

## Quick-start

### 1. Prerequisites

Before running any automation:

```bash
# Install Python dependencies
pip install -r requirements.txt

# Create .env from the template and add your credentials
cp .env.example .env
# edit .env: add KALSHI_EMAIL, KALSHI_PASSWORD, ODDS_API_KEY
```

### 2. Verify the environment

Run the health check manually to confirm everything is in order:

```bash
# Manual health-check equivalent
python3 -c "import yaml, dotenv, structlog, numpy; print('All deps OK')"
```

Or trigger `health-check.yaml` from the Cowork interface.

### 3. Start paper trading

Trigger `run-paper-loop.yaml` manually for a single scan cycle, or let the
scheduled trigger run it every 5 minutes.  The agent runs in **demo mode**
by default — no real money is at risk until you explicitly set
`environment: live` in `config.yaml` and add
`I_UNDERSTAND_LIVE_TRADING=true` to `.env`.

---

## YAML schema reference

Each workflow file uses the following top-level keys:

```yaml
name: kebab-case-identifier          # machine name
display_name: "Human Readable Name"  # shown in the Cowork UI
description: >                       # what the workflow does
  ...
version: "1.0"
category: trading | monitoring | reporting | analysis | ml

trigger:
  type: scheduled | manual
  cron: "cron expression"     # only when type: scheduled
  timezone: local             # cron uses the user's local timezone

environment:
  working_directory: "."
  python_executable: python3
  required_files:             # preflight check — abort if missing
    - config.yaml

steps:
  - id: step-id
    name: "Step name"
    description: >
      What this step does.
    command: python3
    args: [...]
    on_failure: abort | continue | notify
    # abort   — stop the workflow immediately
    # continue — log the error and move to the next step
    # notify  — alert the user but keep going

notifications:
  on_success: true | false
  on_failure: true | false
  success_message: "..."
  failure_message: "..."
```

---

## Workflow details

### `run-paper-loop.yaml`

The core operational loop.  On each trigger:

1. **Pre-flight** — checks `.env` for the three required secrets.
2. **Scan** — runs `python -m src.main` (the full scan-and-decide cycle
   when Phase 6/7 are wired up).
3. **Log tail** — surfaces any WARNING/ERROR lines from the last 50 log
   entries so problems are visible without digging into raw JSON.

**To disable temporarily**: remove or comment out the `cron` line and set
`type: manual`.

---

### `health-check.yaml`

A lightweight sanity check that runs every 30 minutes.  It does **not**
touch live credentials; it only checks their existence.  Steps:

- Import check: `pyyaml`, `python-dotenv`, `structlog`, `numpy`
- Config validation: required YAML keys and types
- Secret existence check (values are never printed)
- Directory write-access probe
- Log freshness: alerts if the last log entry is > 10 minutes old
- Cooldown status: alerts if the daily loss breaker is active
- Disk usage report for `data/`

---

### `daily-report.yaml`

Morning briefing at 9 am.  Steps:

- **Trade log summary**: today's W/L/void/pending counts and P&L
- **Learner stats**: global bias, per-sport biases, model status
- **CSV exports**: `data/exports/trade_log.csv` and
  `data/exports/unmatched_markets.csv`
- **Daily summary append**: one row added to
  `data/exports/daily_summary.csv` for week-over-week trending

---

### `run-backtest.yaml`

Runs every night at 11 pm.  Steps:

- **Data check**: confirms `edge_history.csv` has at least one data row
- **Recorded replay**: backtests known WIN/LOSS outcomes, prints full stats
- **Simulated replay**: resolves PENDING rows via fair_prob Bernoulli draws
  (supplementary — failures are non-blocking)
- **Export confirmation**: checks `equity_curve.csv` and
  `backtest_report.csv` were written to `data/exports/`

---

### `retrain-learner.yaml`

Manual trigger (run after a batch of new resolved trades).  Steps:

- **State check**: confirms `model_state.json` has ≥ `min_trades_for_adjustment`
  records (default 200 from `config.yaml`)
- **Before stats**: prints current weights and global bias
- **Retrain**: calls `Learner.train()` and saves updated weights
- **After stats**: shows new weights and per-sport biases
- **Brier score**: computes mean-squared-error before/after calibration to
  quantify the improvement

To enable the learner in production, set `learner.enabled: true` in
`config.yaml` (disabled by default while accumulating data).

---

### `weekly-data-export.yaml`

Runs every Monday at 6 am, archiving the previous ISO week.  Steps:

- **Week label**: computes the ISO week string (e.g. `2024-W03`)
- **Trade log archive**: `data/exports/archive/trade_log_YYYY-WXX.csv`
- **Unmatched archive**: `data/exports/archive/unmatched_YYYY-WXX.csv`
- **Backtest archive**: `equity_curve_YYYY-WXX.csv` and
  `backtest_report_YYYY-WXX.csv` in `data/exports/archive/`
- **Week-over-week summary**: prints this week vs prior week from
  `daily_summary.csv`
- **Log rotation**: removes entries older than 90 days from
  `trade_log.jsonl` if the file exceeds 10 MB (the archive CSV is the
  permanent record)

---

## Outputs reference

| Path | Written by | Content |
|------|-----------|---------|
| `data/trade_logs/trade_log.jsonl` | Agent (paper loop) | One JSON object per trade event |
| `data/trade_logs/unmatched_markets.jsonl` | Agent (paper loop) | Kalshi markets with no Odds API match |
| `data/edge_history.csv` | Agent (paper loop) | Resolved trade records for backtesting |
| `data/model_state.json` | Learner | Persisted logistic regression weights |
| `data/exports/trade_log.csv` | `daily-report` | Full trade log in spreadsheet format |
| `data/exports/unmatched_markets.csv` | `daily-report` | Unmatched markets in spreadsheet format |
| `data/exports/daily_summary.csv` | `daily-report` | Appended daily P&L summary rows |
| `data/exports/equity_curve.csv` | `run-backtest` | Running bankroll per resolved trade |
| `data/exports/backtest_report.csv` | `run-backtest` | Per-sport performance summary |
| `data/exports/archive/` | `weekly-data-export` | Week-stamped historical archives |

---

## Customisation

**Change scan frequency**: edit the `cron` line in `run-paper-loop.yaml`.
Five-minute scans are appropriate for liquid markets; reduce to 15–30 min
if Odds API quota is a concern.

**Turn off during off-season**: set `type: manual` (remove `cron`) in
`run-paper-loop.yaml` and `run-backtest.yaml` when sports are not in season.

**Enable live trading**: set `environment: live` in `config.yaml` AND add
`I_UNDERSTAND_LIVE_TRADING=true` to `.env`.  The executor's live gate will
then allow real order placement.  Start with a small bankroll.

**Enable the learner**: set `learner.enabled: true` in `config.yaml` once
you have ≥ 200 resolved trades and have run `retrain-learner.yaml`.
