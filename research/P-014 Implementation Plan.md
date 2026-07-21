# P-014: Live Game Agent — Implementation Plan

**Date:** March 28, 2026
**Pod ID:** P-014
**Status:** Planning
**Priority:** High — NBA/MLB actively in-season

---

## Architecture Overview

```
P-014: LiveGamePod (extends BasePod)
  ├── LiveOddsPoller        — Polls Odds API for live odds every N seconds during active games
  ├── GameStateTracker      — Tracks score, inning/quarter, time remaining per active game
  ├── LiveFairValueEngine   — Builds game-state-aware fair value from sharp consensus
  ├── MomentumDetector      — Detects overreaction to scoring events on Kalshi
  ├── KalshiLiveTrader      — Places/manages orders on Kalshi live markets via API
  └── LiveRiskManager       — Per-game and cross-game exposure limits
```

---

## Implementation Phases

### Phase 1: Foundation (Days 1-3)
**Goal:** Get live data flowing and matched to Kalshi markets

**Tasks:**
1. **LiveOddsPoller** (`src/live_odds_poller.py`)
   - Async polling loop that hits Odds API `/v4/sports/{sport}/odds` for active games
   - Configurable poll interval (default: 15 seconds)
   - Filters to only in-play games (commence_time < now, not completed)
   - Extracts live moneyline, spread, totals from all available books
   - Caches last-known odds to detect meaningful changes
   - Cost-aware: tracks credit usage, pauses if approaching limits

2. **GameStateTracker** (`src/game_state.py`)
   - Polls `/v4/sports/{sport}/scores` for live scores
   - Maintains in-memory game state: score, period/inning, time remaining
   - Emits "game events" when state changes (score change, period change)
   - Detects game start/end to trigger polling start/stop

3. **Kalshi Live Market Discovery**
   - Extend existing Kalshi client to discover live/in-game markets
   - Map Odds API game identifiers to Kalshi market tickers
   - Handle market suspension states

4. **Config integration**
   - Add P-014 section to `config_multi_pod.yaml`
   - Parameters: poll_interval, min_edge, sports, max_exposure_per_game, etc.

### Phase 2: Fair Value & Signal Generation (Days 3-5)
**Goal:** Generate live fair value estimates and detect trading signals

**Tasks:**
5. **LiveFairValueEngine** (`src/live_fair_value.py`)
   - Adapts existing `FairValueEstimator` for live context
   - Methods:
     - `consensus_live`: Pinnacle-weighted multi-book consensus (primary)
     - `pinnacle_only_live`: Pinnacle line as fair value (backup)
   - Game-state weighting: as game progresses, consensus becomes more reliable (less variance)
   - Confidence scoring: higher confidence with more books reporting + later in game

6. **MomentumDetector** (`src/momentum_detector.py`)
   - Tracks rate of Kalshi price change after game events
   - Detects when Kalshi's implied probability moves faster/further than sharp consensus
   - Generates "overreaction score" = |Kalshi delta| - |consensus delta| after events
   - Signal: when overreaction score exceeds threshold → flag as trading opportunity

7. **Signal combiner**
   - Primary: consensus edge (Kalshi price vs. fair value) — same as P-001 but live
   - Secondary: momentum reversion (overreaction detected)
   - Combined signal weights configurable

### Phase 3: Execution & Risk (Days 5-7)
**Goal:** Place paper trades on Kalshi with proper risk management

**Tasks:**
8. **KalshiLiveTrader** (`src/kalshi_live_trader.py`)
   - Extends existing Kalshi client for live order management
   - Limit orders (post to order book at target price, don't market-buy)
   - Order lifecycle: place → monitor fill → manage position → settle
   - Cancel stale orders (unfilled after N seconds in fast-moving market)
   - Track multiple open positions per game

9. **LiveRiskManager** (`src/live_risk.py`)
   - Per-game exposure limit (e.g., max $500 per game)
   - Max concurrent games (e.g., 5 games simultaneously)
   - Portfolio-level live exposure cap (integrates with AggregateRiskGuard)
   - No doubling down: if already positioned on a game, don't add unless edge increases
   - Stop-loss: exit position if edge flips negative by >X%

10. **Integration with engine**
    - Register P-014 in pod registry
    - Wire into PodRunner scan cycle (separate faster loop for live pods)
    - Ensure TradeStore handles live trade logging with game-state context

### Phase 4: Paper Trading & Iteration (Days 7-14)
**Goal:** Run live paper trading, measure performance, iterate

**Tasks:**
11. **Paper trading deployment**
    - Enable P-014 in paper mode on VPS
    - Separate scan loop (faster cycle) from pre-game pods
    - Monitor resource usage (API credits, CPU, memory)

12. **Metrics & dashboarding**
    - Track: signals generated, trades placed, fill rate, edge at entry, outcome, P&L
    - Track: latency (signal detection to order placement)
    - Track: Kalshi vs consensus divergence over time (is the edge real?)
    - Add P-014 section to existing dashboard

13. **Backtesting framework**
    - Log all live odds snapshots to `data/live_odds_snapshots/` for offline analysis
    - Build replay engine to backtest signals against historical live data
    - Measure: what would have happened if we traded every signal above X% edge?

14. **Iteration targets**
    - Tune thresholds (min_edge, momentum_threshold, poll_interval)
    - Sport-specific parameters (NBA vs MLB have very different dynamics)
    - Evaluate whether to add spreads/totals beyond moneyline

---

## Configuration Schema

```yaml
pods:
  P-014:
    name: "Live Game Agent"
    enabled: true
    environment: demo  # paper mode
    venue: kalshi
    sports:
      - basketball_nba
      - baseball_mlb
    scan:
      poll_interval_seconds: 15
      score_poll_interval_seconds: 30
      max_concurrent_games: 5
    fair_value:
      method: consensus_live  # consensus_live, pinnacle_only_live
      pinnacle_weight: 0.5
      min_books_for_consensus: 3
      confidence_floor: 0.6
    signals:
      min_edge_pct: 2.0  # minimum edge to trigger trade
      momentum_threshold: 5.0  # overreaction score threshold
      consensus_weight: 0.7  # weight on consensus signal
      momentum_weight: 0.3  # weight on momentum signal
    execution:
      order_type: limit  # limit orders only
      limit_offset_cents: 1  # post 1 cent inside fair value
      stale_order_timeout_seconds: 30
      max_open_orders_per_game: 3
    risk:
      max_exposure_per_game_usd: 500
      max_total_live_exposure_usd: 2500
      kelly_cap: 0.05  # max 5% of bankroll per trade
      stop_loss_pct: -10.0  # exit if edge flips negative by 10%
    odds_api:
      tier: paid  # need paid tier for live polling volume
      regions: ["us"]
      markets: ["h2h"]  # start with moneyline only
```

---

## New Files

| File | Purpose |
|---|---|
| `src/live_odds_poller.py` | Async live odds polling from Odds API |
| `src/game_state.py` | Game state tracking (scores, periods) |
| `src/live_fair_value.py` | Game-state-aware fair value engine |
| `src/momentum_detector.py` | Overreaction detection on Kalshi |
| `src/kalshi_live_trader.py` | Live order management on Kalshi |
| `src/live_risk.py` | Per-game and cross-game risk limits |
| `src/pods/live_game_pod.py` | P-014 pod implementation (extends BasePod) |
| `tests/test_live_odds_poller.py` | Unit tests |
| `tests/test_game_state.py` | Unit tests |
| `tests/test_live_fair_value.py` | Unit tests |
| `tests/test_momentum_detector.py` | Unit tests |
| `tests/test_live_risk.py` | Unit tests |
| `tests/test_live_game_pod.py` | Integration tests |

---

## Dependencies

- Existing: `aiohttp`, `asyncio`, Kalshi client, Odds API integration, BasePod, TradeStore
- New: None expected — all built on existing stack
- Odds API: Will need to upgrade to paid tier (~$30-99/month)

---

## Success Metrics (Paper Trading Phase)

| Metric | Target | Rationale |
|---|---|---|
| Signals detected per day | 10-50 | Enough volume to learn from |
| Avg edge at entry | >2% | Minimum for profitability |
| Fill rate | >50% | Limit orders should fill half the time |
| Signal-to-trade latency | <15 seconds | Fast enough for momentum reversion |
| Win rate (settled trades) | >55% | Meaningful edge above random |
| Kalshi vs consensus divergence | Documented | Prove the thesis with data |

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| Odds API costs spike | Credit tracking, auto-pause, poll only during games |
| Kalshi live markets too thin | Monitor order book depth, widen limit offsets, skip thin markets |
| Latency too high for edge | Focus on momentum reversion (longer windows) over pure consensus |
| False signals in volatile moments | Require minimum consensus confidence + multiple books agreeing |
| VPS resource contention with other pods | Separate async loop, monitor CPU/memory |

---

## Timeline

| Day | Milestone |
|---|---|
| 1-2 | LiveOddsPoller + GameStateTracker working, logging live data |
| 3 | Kalshi live market discovery + matching working |
| 4-5 | LiveFairValueEngine + MomentumDetector generating signals |
| 6-7 | KalshiLiveTrader + LiveRiskManager placing paper trades |
| 8-14 | Paper trading on VPS, collecting data, iterating on thresholds |
| 15+ | Analyze results, decide on live trading readiness |
