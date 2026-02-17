# Kalshi Arbitrage Agent

A Python-based trading agent that identifies mispricings between Kalshi prediction markets and sharp sportsbook odds, then executes trades when edge exceeds configurable thresholds. Includes a learning system that improves performance over time.

## Features

- **Market Scanning**: Continuously scans Kalshi sports markets and matches them to sportsbook odds
- **Edge Detection**: Calculates edge using consensus sharp sportsbook probabilities vs Kalshi prices
- **Risk Management**: Position sizing via fractional Kelly criterion, exposure limits, drawdown protection
- **Learning System**: Bayesian calibration, rolling win rate tracking, and ML-based trade scoring
- **Paper Trading**: Full simulation mode for safe testing before risking real money
- **Terminal Dashboard**: Live view of positions, P&L, opportunities, and system stats
- **Backtesting**: Replay historical data to evaluate strategy performance

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A Kalshi account (start with demo: https://demo.kalshi.co)
- An Odds API key (free tier: https://the-odds-api.com)

### 2. Installation

```bash
cd kalshi-arb-agent
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configuration

```bash
# Copy the environment template
cp .env.example .env

# Edit .env with your API keys
# IMPORTANT: Start with KALSHI_ENV=demo
```

Edit `config.yaml` to adjust trading parameters. The defaults are conservative and safe for paper trading.

### 4. Run in Paper Mode (Default)

```bash
python -m src.main
```

The agent will:
1. Scan Kalshi markets matching configured sports
2. Fetch sportsbook odds from The Odds API
3. Match markets and calculate edge
4. Log trade opportunities (no real orders in paper mode)
5. Track simulated P&L

### 5. View the Dashboard

In a separate terminal:

```bash
python -m scripts.dashboard
```

### 6. Run Tests

```bash
pytest tests/ -v
```

## Switching to Live Trading

**Do NOT switch to live trading until you have:**
1. Run paper trading for at least 2 weeks
2. Verified the matcher is correctly pairing markets
3. Reviewed trade_log.json to confirm edge calculations make sense
4. Accumulated 50+ paper trades with a positive win rate
5. Understood and accepted the financial risks

To switch:
1. Set `KALSHI_ENV=prod` in `.env`
2. Set `trading.mode: "live"` in `config.yaml`
3. Start with very small position sizes (`max_position_size_usd: 50`)
4. The agent will prompt for confirmation on first live trade

## Project Structure

```
kalshi-arb-agent/
├── config.yaml              # Trading parameters
├── .env.example             # API key template
├── requirements.txt         # Dependencies
├── src/
│   ├── main.py              # Agent orchestration loop
│   ├── kalshi_client.py     # Kalshi API wrapper
│   ├── odds_client.py       # The Odds API wrapper
│   ├── matcher.py           # Market matching (Kalshi ↔ sportsbooks)
│   ├── edge_calculator.py   # Edge and EV computation
│   ├── risk_manager.py      # Position sizing and limits
│   ├── executor.py          # Order execution
│   ├── learner.py           # ML/statistical learning
│   ├── logger_setup.py      # Structured logging
│   └── utils.py             # Odds conversion helpers
├── data/
│   ├── trade_log.json       # Trade history
│   ├── edge_history.csv     # Edge calculation log
│   ├── model_state.json     # Learner state
│   └── market_snapshots/    # Periodic snapshots
├── tests/                   # pytest test suite
└── scripts/
    ├── dashboard.py         # Terminal dashboard
    ├── backtest.py          # Strategy backtesting
    └── export_results.py    # P&L export
```

## Configuration Reference

See `config.yaml` for all parameters with inline documentation. Key settings:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `trading.mode` | `paper` | `paper` or `live` |
| `trading.min_edge_pct` | `3.0` | Minimum edge % to trade |
| `trading.max_position_size_usd` | `500` | Max per-trade size |
| `trading.max_daily_loss_usd` | `500` | Daily stop-loss |
| `learning.enabled` | `true` | Enable adaptive learning |
| `learning.edge_adjustment_method` | `bayesian` | Learning algorithm |

## How the Learning System Works

The agent improves over time using three complementary approaches:

1. **Bayesian Calibration**: Maintains Beta distribution priors for each (sport, market_type) pair. Updates posteriors after each trade outcome to adjust edge thresholds per category.

2. **Rolling Win Rate**: Tracks recent win rates per category. Increases minimum edge for underperforming categories, decreases for outperforming ones.

3. **Feature-Based ML** (after 200+ trades): Trains a gradient boosting classifier on trade features to predict win probability, used to adjust position sizing.

## Safety Features

- Defaults to paper trading mode
- Confirmation prompt required for live trading
- Daily loss limits with automatic stop
- Drawdown protection (50% size reduction at 10% drawdown)
- Correlation checking to avoid concentrated risk
- Maximum edge filter to skip stale/suspicious data
- Rate limiting for all API calls
- All decisions logged for full auditability

## License

MIT
