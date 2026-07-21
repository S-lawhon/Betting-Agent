# Strategy 2: Implied Probability Consistency Arbitrage — Architecture

## Core Thesis

Kalshi lists multiple market tiers for NBA and NCAAB basketball:

| Tier | Series Ticker | Example | Settlement |
|------|--------------|---------|------------|
| Game winner | `KXNBAGAME` / `KXNCAAMBGAME` | "Will Celtics beat Knicks?" | Single game result |
| Playoff qualifier | `KXNBAPLAYOFF` | "Will Celtics make playoffs?" | End-of-season standings |
| Conference champion | `KXNBAEAST` / `KXNBAWEST` | "Will Celtics win Eastern Conference?" | Conference finals result |
| NBA champion | `KXNBA` | "Will Celtics win NBA title?" | Finals result |

Each market tier embeds an implied probability for the same team. These probabilities **must be internally consistent** — a team that's 92% to make the playoffs but only 4% to win the title implies roughly 4.3% conditional championship probability given playoffs. If individual game markets imply the Celtics will win 70% of remaining games but the season-level markets imply they need to win 55%, there's a structural inconsistency, and the outlier is tradeable.

**Key advantage:** This is a structural, no-speed-required edge. Kalshi's market tiers are priced by different liquidity pools (game bettors vs. futures bettors) with little cross-market arbitrage pressure. Sportsbooks have sophisticated models that keep their equivalent markets consistent; Kalshi doesn't.

---

## Kalshi Market Hierarchy

The Kalshi API organizes markets in a three-tier hierarchy:

```
Series (template)  →  Event (instance)  →  Market (binary contract)
  KXNBAPLAYOFF     →  KXNBAPLAYOFF-26   →  KXNBAPLAYOFF-26-BOS (Celtics: YES/NO)
                                          →  KXNBAPLAYOFF-26-LAL (Lakers: YES/NO)
                                          →  ... (one per team)
```

**API access:**
- `GET /markets?series_ticker=KXNBAPLAYOFF&status=open` → all open playoff qualifier contracts
- `GET /markets?series_ticker=KXNBA&status=open` → all open championship contracts
- `GET /markets?series_ticker=KXNBAGAME&status=open` → all open game-winner contracts

Each market has `yes_bid`, `yes_ask`, `volume`, `ticker`, `title`, and crucially `event_ticker` and `series_ticker` for grouping.

---

## Architecture: Two Options

### Option A — Consistency Checker Module (Recommended)

A **standalone module** (`src/consistency_checker.py`) that runs as a second analysis pass after the existing scan loop. It doesn't replace the Scanner — it adds a parallel signal source.

**Why standalone:** The existing Scanner pipeline is optimized for game-by-game evaluation (match Kalshi game → Odds API event → compute edge). Consistency arbitrage requires a fundamentally different data flow: ingest ALL markets across ALL tiers for ALL teams simultaneously, then cross-reference. Bolting this onto _evaluate_match() would be architecturally wrong.

**Integration point:** The consistency checker produces `ConsistencySignal` objects. These feed into a new scan path in the main loop that evaluates them through the existing RiskManager and Executor pipeline.

### Option B — Scanner Extension

Extend the existing Scanner with a `_scan_consistency()` method called after `_scan_sport()`. Simpler but couples two very different analysis patterns into one class.

**Recommendation: Option A.** The data flow is sufficiently different that a clean module boundary is warranted. The consistency checker needs to see all teams × all tiers simultaneously, while the Scanner processes one sport → one match at a time.

---

## Option A: Detailed Design

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Main Loop (_run_paper_loop)                                │
│                                                             │
│  1. executor.run_cycle()          ← existing game-by-game   │
│  2. consistency.scan_cycle()      ← NEW: cross-tier check   │
│     a. Fetch all Kalshi markets by series_ticker            │
│     b. Group by team                                        │
│     c. Extract implied probabilities per tier               │
│     d. Check consistency constraints                        │
│     e. Return tradeable inconsistencies                     │
│  3. For each signal: risk check → place/skip → log          │
└─────────────────────────────────────────────────────────────┘
```

### New Files

#### 1. `src/consistency_checker.py`

**Data Classes (frozen):**

```python
@dataclass(frozen=True)
class TeamMarketSnapshot:
    """All Kalshi market data for one team across all tiers."""
    team: str
    game_markets: list[GameMarketInfo]     # upcoming individual games
    playoff_prob: float | None             # implied P(make playoffs)
    conference_prob: float | None          # implied P(win conference)
    championship_prob: float | None        # implied P(win title)
    playoff_ticker: str | None
    conference_ticker: str | None
    championship_ticker: str | None

@dataclass(frozen=True)
class GameMarketInfo:
    """One upcoming game market for a team."""
    ticker: str
    opponent: str
    implied_win_prob: float       # mid-price = implied probability
    commence_time: datetime
    volume: int

@dataclass(frozen=True)
class ConsistencySignal:
    """A detected inconsistency between market tiers."""
    team: str
    signal_type: str              # "GAME_VS_SEASON" | "PLAYOFF_VS_CONFERENCE" | "CONFERENCE_VS_CHAMPIONSHIP"
    description: str
    implied_game_win_rate: float  # from individual game markets
    implied_season_rate: float    # from season-level market
    divergence: float             # absolute difference
    recommended_side: str         # "YES" or "NO"
    recommended_ticker: str       # which market to trade
    recommended_price: float      # current mid-price
    confidence: float             # 0.0-1.0, based on divergence magnitude and data quality
    supporting_data: dict         # additional context for logging
```

**ConsistencyChecker class:**

```python
class ConsistencyChecker:
    def __init__(
        self,
        kalshi_client,
        series_tickers: dict,       # mapping of tier → series_ticker
        min_divergence: float,      # minimum inconsistency to flag (e.g. 0.05 = 5%)
        min_game_markets: int,      # minimum upcoming games needed for game-rate estimate
        min_volume: int,            # per-market minimum volume
        _now_fn=None,
    ): ...

    @classmethod
    def from_config(cls, config, kalshi_client): ...

    def scan_cycle(self) -> list[ConsistencySignal]:
        """Full consistency check across all teams and tiers."""

    def _fetch_tier_markets(self, series_ticker: str) -> list[dict]:
        """Fetch all open markets for one series ticker."""

    def _group_by_team(self, all_markets: dict[str, list]) -> dict[str, TeamMarketSnapshot]:
        """Build per-team snapshots from raw market dicts across all tiers."""

    def _extract_team_from_ticker(self, ticker: str, title: str) -> str | None:
        """Parse team abbreviation from market ticker or title."""

    def _check_game_vs_season(self, snapshot: TeamMarketSnapshot) -> list[ConsistencySignal]:
        """Compare aggregate game-win rate to playoff/season implied rate."""

    def _check_playoff_vs_conference(self, snapshot: TeamMarketSnapshot) -> list[ConsistencySignal]:
        """P(conf champion) should be ≤ P(make playoffs)."""

    def _check_conference_vs_championship(self, snapshot: TeamMarketSnapshot) -> list[ConsistencySignal]:
        """P(NBA champion) should be ≤ P(conf champion)."""

    def _implied_season_win_rate(self, playoff_prob: float, games_remaining: int, current_wins: int) -> float:
        """Back out the per-game win rate needed to achieve a given playoff probability."""
```

#### 2. `tests/test_consistency_checker.py`

Test groups:
- **Team grouping:** Markets correctly grouped across tiers by team name/abbreviation
- **Probability extraction:** Mid-price → implied probability conversion
- **Game-vs-season consistency:** 70% game win rate but 40% playoff prob → signal
- **Tier ordering:** P(champion) ≤ P(conference) ≤ P(playoffs) — violations flagged
- **Minimum data guards:** Skip teams with < min_game_markets upcoming games
- **Volume filter:** Skip low-volume markets
- **Edge cases:** Team with only one tier of markets, missing data, zero volume

### Modified Files

#### 3. `config.yaml` — New section

```yaml
# ── Consistency Arbitrage (Strategy 2 — cross-tier probability) ──────────────
consistency:
  enabled: true
  sports:
    - basketball_nba
  series_tickers:
    game: KXNBAGAME
    playoff: KXNBAPLAYOFF
    conference_east: KXNBAEAST
    conference_west: KXNBAWEST
    championship: KXNBA
  min_divergence: 0.05          # 5% inconsistency threshold
  min_game_markets: 3           # need ≥ 3 upcoming game markets for reliable estimate
  min_volume: 200               # per-market volume floor for consistency check
  season_context:
    total_games: 82             # NBA regular season length
    playoff_threshold_wins: 42  # approximate wins needed for playoff contention
```

#### 4. `src/main.py`

Add to `_build_components()`:
```python
from src.consistency_checker import ConsistencyChecker

consistency = None
if config.get("consistency", {}).get("enabled", False):
    consistency = ConsistencyChecker.from_config(config, kalshi)
```

Add to return dict: `"consistency": consistency`

#### 5. `src/main.py` — `_run_paper_loop()`

After `executor.run_cycle()`, add a consistency scan:
```python
if components.get("consistency"):
    signals = components["consistency"].scan_cycle()
    # Evaluate each signal through risk manager
    for signal in signals:
        # ... risk check, log, paper-place
```

This is a bigger question — the consistency signals need to flow through the executor/risk pipeline. Two sub-options:

**5a. Minimal:** ConsistencyChecker writes its own log entries directly (similar to Scanner's `_make_entry` + `_write_log`). Pros: zero changes to Executor. Cons: duplicated logging logic.

**5b. Full integration:** Create `ConsistencyExecutor` or extend `Executor` with a `execute_consistency_signals()` method. Pros: unified risk/execution pipeline. Cons: more code to change.

**Recommendation: 5a for initial implementation**, upgrade to 5b if the strategy proves profitable.

---

## Consistency Math

### Check 1: Game Win Rate vs. Playoff Probability

**Inputs:**
- `game_win_rate`: average implied win probability across upcoming game markets
- `playoff_prob`: implied P(make playoffs) from the KXNBAPLAYOFF market
- `games_remaining`: number of games left in the season
- `current_wins`: team's current win count (from external source or approximation)

**Logic:**
```
expected_remaining_wins = game_win_rate × games_remaining
projected_total_wins = current_wins + expected_remaining_wins

# Historical playoff threshold is ~42 wins for NBA
# If projected wins >> 42 but playoff_prob is low → playoff market is underpriced
# If projected wins << 42 but playoff_prob is high → playoff market is overpriced
```

**Simplification for v1:** Instead of modeling the full playoff probability distribution, compare the game-implied win rate to the "break-even" win rate that the playoff probability implies. If the divergence exceeds `min_divergence`, flag it.

The break-even rate is approximated as:
```
# If P(playoffs) = 0.80, historically teams with 80% playoff probability
# have a win rate of ~0.60 (48-49 wins pace)
# This mapping is calibrated from historical NBA data
```

**Important caveat:** This is an approximation. A full model would need to account for schedule strength, conference standings, tiebreakers, etc. For v1, we use a simplified lookup table calibrated from historical data.

### Check 2: Tier Monotonicity

These must always hold:
```
P(NBA champion) ≤ P(conference champion) ≤ P(make playoffs)
```

Any violation is a pure structural arbitrage:
- If `P(champion) > P(conference)` → the championship market is overpriced OR the conference market is underpriced.
- Trade the side with higher volume/liquidity (usually the overpriced market — sell YES).

### Check 3: Conference vs. Championship Consistency

For a specific team:
```
P(NBA champion) = P(conference champion) × P(win finals | won conference)
```

`P(win finals | won conference)` is harder to estimate but historically averages ~0.50 with variance based on conference strength. If we observe:
- P(champion) = 0.30
- P(conference) = 0.35
- Implied P(win finals | won conf) = 0.30 / 0.35 = 0.857

That's unrealistically high — suggests either championship is overpriced or conference is underpriced.

---

## Team Name Resolution

A critical challenge: matching team identifiers across different Kalshi market tiers.

**Game markets:** Tickers like `KXNBAGAME-26FEB23BOSKNK-BOS` contain team abbreviations.
**Playoff markets:** Tickers like `KXNBAPLAYOFF-26-BOS` also use abbreviations.
**Championship:** Titles like "Will Boston Celtics win the NBA Championship?"

**Approach:** Build a `TeamResolver` (or a lookup dict) that maps:
- Full name → abbreviation (e.g., "Boston Celtics" → "BOS")
- Abbreviation → full name
- Kalshi title patterns → team identity

This can be a static mapping for NBA (30 teams) and NCAAB (top ~60 teams that appear on Kalshi). The existing `_infer_yes_side()` and matcher fuzzy logic can be reused for ambiguous cases.

---

## External Data Needs

### Current Season Standings

To compute "games remaining" and "current wins," we need live standings data. Options:

1. **Infer from Kalshi:** Count how many game markets exist for each team in the future → approximate games remaining. Rough but zero-API-cost.

2. **Free NBA API:** `https://data.nba.com/` or `https://cdn.nba.com/static/json/` endpoints provide standings data (no API key needed). These are unofficial but reliable.

3. **The Odds API:** Already used — check if it provides season context. (It doesn't directly provide standings.)

4. **Manual config:** Set `current_wins` and `games_remaining` in config or a JSONL state file, updated periodically.

**Recommendation for v1:** Option 1 (infer from Kalshi). Count future game markets per team. For current wins, use a simplified heuristic: `(82 - games_remaining) × league_average_win_rate_adjustment`. This avoids new API dependencies. Upgrade to option 2 if precision matters.

**Better v1.1:** Add an optional `standings_source` that fetches from the free NBA CDN endpoint. This is a simple GET request with no auth, and the data is structured JSON.

---

## API Cost Analysis

**Current scan loop:** Fetches all Kalshi markets via paginated `list_markets()` — these markets are already available. The consistency checker can reuse the same market data fetched by the Scanner in the same cycle.

**Additional API calls for consistency only:**
- If we reuse the Scanner's fetched markets: **0 extra Kalshi calls**
- If we fetch by `series_ticker` separately: **~5 extra calls** (one per series)

**Recommendation:** Share the Kalshi market data between Scanner and ConsistencyChecker. The Scanner already fetches up to 4,000 markets per cycle. The consistency checker just needs to filter and group them differently.

**Implementation:** Pass the `kalshi_markets` list from `scan_once()` to `consistency.scan_cycle(kalshi_markets)` rather than fetching again.

---

## Implementation Phases

### Phase 1: Core Module (standalone, testable)
1. `src/consistency_checker.py` — TeamMarketSnapshot, ConsistencySignal, ConsistencyChecker
2. `src/team_resolver.py` — static NBA team name ↔ abbreviation mapping
3. `tests/test_consistency_checker.py` — unit tests for all consistency checks
4. `config.yaml` — add consistency section

### Phase 2: Integration
5. `src/main.py` — wire ConsistencyChecker into component graph
6. Modify scan loop to pass Kalshi markets to consistency checker
7. ConsistencyChecker writes to its own log file: `data/trade_logs/consistency_log.jsonl`

### Phase 3: Risk Integration
8. Route ConsistencySignal through RiskManager for position sizing
9. Use existing fingerprint/dedup logic to prevent re-trading
10. Paper-mode execution via Executor

### Phase 4: Refinement
11. Add standings data source (NBA CDN or manual config)
12. Calibrate divergence thresholds from historical Kalshi data
13. Add NCAAB support (if Kalshi has enough NCAAB tier markets)

---

## Risk Considerations

1. **Illiquidity:** Season-level markets (playoff, championship) may have very thin order books. The mid-price may not be executable. **Mitigation:** Check orderbook depth via `get_orderbook()` before signaling.

2. **Settlement timing mismatch:** Game markets settle in hours; season markets settle in months. A consistency signal might be "correct" but capital is locked for months. **Mitigation:** Apply a time-value discount — prefer trading the game-market leg when the signal favors it.

3. **Small sample bias:** If a team only has 3 upcoming game markets, the implied game-win rate is noisy. **Mitigation:** Require `min_game_markets ≥ 3` and weight by volume.

4. **Correlated positions:** If we trade both a game market AND a season market for the same team, they're correlated. **Mitigation:** The RiskManager's `max_positions` and `max_exposure_pct` limits apply, but we should also add a per-team exposure cap.

---

*Document created: February 2026*
*Project: Kalshi Basketball Inter-Game Mispricing Bot*
