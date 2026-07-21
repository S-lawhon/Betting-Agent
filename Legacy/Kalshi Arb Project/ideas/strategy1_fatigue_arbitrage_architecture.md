# Strategy 1: Schedule-Adjusted Fatigue Arbitrage — Architecture Deep Dive

## The Core Thesis

Sportsbooks (Pinnacle, DraftKings, FanDuel) adjust lines for fatigue factors quickly because sharp bettors hammer stale lines. Kalshi's thinner, retail-heavy order books don't have this corrective mechanism. When a team plays Tuesday night and again Wednesday night, the sportsbook consensus for Wednesday's game already reflects the back-to-back discount — but the Kalshi price for Wednesday's game often lags hours or even a full day behind.

The bot doesn't need to *model* fatigue from scratch. It just needs to detect when the gap between Kalshi and consensus is *wider than usual* on games where fatigue is a factor, and trade into those wider gaps.

---

## How It Fits Into the Existing Pipeline

The current pipeline flows like this:

```
Scanner.scan_once()
  → for each sport:
      OddsClient.get_events(sport)         # fetch sportsbook consensus
      Matcher.match_all(kalshi, odds)       # fuzzy-match markets
      for each match:
        _evaluate_match()                   # edge calc → risk check → PLACED/SKIPPED
```

The fatigue scanner doesn't replace this — it **augments** it with a parallel signal. Two possible integration points:

### Option A: Fatigue-Adjusted Edge Boost (Recommended)

Add a `FatigueScanner` module that runs *before* the main scan loop. It pre-computes a "fatigue context" dict keyed by team name, containing rest days, miles traveled, and back-to-back flags. The existing `_evaluate_match()` method then checks this context and applies an **edge confidence multiplier** — effectively telling the system "this consensus-vs-Kalshi gap is more trustworthy because we know *why* it exists."

This is the lightest integration: the existing edge/risk pipeline stays unchanged, you just feed it better information about *which* edges to trust.

### Option B: Standalone Fatigue Scanner (More Ambitious)

A completely separate scanner that:
1. Pulls the full NBA schedule (next 7 days)
2. Identifies all games with a fatigue flag (back-to-back, 3-in-4, long road trip)
3. For each flagged game, compares Kalshi price to sportsbook consensus
4. If the gap is wider than a fatigue-adjusted threshold, trades immediately

This is cleaner architecturally but duplicates some Scanner logic.

**Recommendation: Start with Option A, graduate to Option B once you have data on how fatigue edges perform.**

---

## New Modules

### 1. `src/schedule_client.py` — NBA Schedule Data

```
ScheduleClient
├── get_team_schedule(team: str, days_ahead: int = 7) → List[ScheduledGame]
├── get_all_games(date: date) → List[ScheduledGame]
└── _fetch_schedule(season: str) → raw JSON

ScheduledGame (dataclass):
├── game_id: str
├── date: date
├── home_team: str
├── away_team: str
├── start_time: datetime
├── venue: str
└── is_national_tv: bool
```

**Data source options (in order of preference):**

1. **NBA API (free, unofficial):** `https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json` — full season schedule, no auth needed, updates daily. Downside: undocumented, could break.

2. **balldontlie.io API (free tier):** 60 req/hr, has schedule + game results. Good enough for a 7-day lookahead fetched once/hour.

3. **ESPN API (free, unofficial):** `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD` — daily scoreboard with schedule.

4. **The Odds API (already integrated):** Doesn't have schedule data per se, but `commence_time` on events gives you game dates. You could reconstruct back-to-back detection from the events list alone — no new API needed.

**Recommendation: Start with option 4** (zero new dependencies). The `OddsClient.get_events("basketball_nba")` response already contains `commence_time` for every upcoming game. Group by team name + sort by date, and you can detect back-to-backs directly. Graduate to a dedicated schedule API later if you need venue/travel data.

### 2. `src/fatigue_analyzer.py` — Fatigue Factor Computation

```
FatigueAnalyzer
├── analyze_team(team: str, game_time: datetime) → FatigueContext
├── analyze_matchup(home: str, away: str, game_time: datetime) → MatchupFatigue
└── _compute_rest_days(team: str, game_time: datetime) → int

FatigueContext (dataclass):
├── team: str
├── rest_days: int              # days since last game (0 = back-to-back)
├── games_in_last_4_days: int   # 3-in-4 detection
├── games_in_last_7_days: int   # weekly load
├── is_back_to_back: bool       # rest_days == 0
├── is_second_of_b2b: bool      # playing today AND played yesterday
├── is_road_b2b: bool           # second of b2b AND away team
├── opponent_rest_advantage: int # opponent_rest - this_team_rest
├── fatigue_score: float         # composite 0.0 (fresh) → 1.0 (exhausted)

MatchupFatigue (dataclass):
├── home_fatigue: FatigueContext
├── away_fatigue: FatigueContext
├── rest_differential: int       # home_rest - away_rest (positive = home fresher)
├── fatigue_edge_multiplier: float  # 1.0 = no fatigue signal, >1 = stronger conviction
```

**Fatigue score formula (initial version):**

```python
def _fatigue_score(ctx: FatigueContext) -> float:
    """
    Composite fatigue score: 0.0 (fully rested) → 1.0 (maximum fatigue).

    Factors and weights based on NBA rest-day research:
    - Back-to-back: biggest single factor (~3.5 point ATS impact historically)
    - Road back-to-back: additional penalty (~1.5 points)
    - 3-in-4 nights: moderate fatigue even without true B2B
    - 4-in-6 nights: cumulative load
    """
    score = 0.0

    if ctx.is_second_of_b2b:
        score += 0.50                    # base B2B penalty
    if ctx.is_road_b2b:
        score += 0.20                    # road B2B additional
    if ctx.games_in_last_4_days >= 3:
        score += 0.15                    # 3-in-4 penalty
    if ctx.games_in_last_7_days >= 4:
        score += 0.10                    # heavy week
    if ctx.rest_days >= 3:
        score -= 0.10                    # extra rest bonus

    return max(0.0, min(1.0, score))
```

**Edge multiplier logic:**

The fatigue analyzer doesn't change the fair probability — the sportsbook consensus already prices in fatigue. Instead, it answers: "How confident are we that Kalshi hasn't caught up to the consensus adjustment yet?"

```python
def fatigue_edge_multiplier(matchup: MatchupFatigue) -> float:
    """
    When fatigue is a factor, the consensus-Kalshi gap is more likely to be
    a genuine edge (books adjusted, Kalshi hasn't) rather than noise.

    Returns a multiplier for edge confidence:
      1.0  = no fatigue signal, treat edge normally
      1.2  = moderate fatigue factor, slightly more conviction
      1.5  = strong fatigue factor (B2B), high conviction the gap is real
    """
    diff = abs(matchup.rest_differential)

    if diff == 0:
        return 1.0     # both teams equally rested, no fatigue signal

    if matchup.home_fatigue.is_second_of_b2b or matchup.away_fatigue.is_second_of_b2b:
        return 1.5     # clear B2B situation — highest conviction

    if diff >= 2:
        return 1.3     # meaningful rest differential

    return 1.1          # slight rest advantage
```

### 3. Integration into `Scanner._evaluate_match()`

The lightest touch integration point. After computing the edge but before the risk check:

```python
# In Scanner._evaluate_match(), after line 517 (best = self._edge.best_side(...)):

# ── Fatigue adjustment (Strategy 1) ────────────────────────────────
if self._fatigue_analyzer and sport in ("basketball_nba", "basketball_ncaab"):
    matchup_fatigue = self._fatigue_analyzer.analyze_matchup(
        home=cp.home_team,
        away=cp.away_team,
        game_time=match.odds_event.commence_time,
    )
    fatigue_mult = fatigue_edge_multiplier(matchup_fatigue)

    if fatigue_mult > 1.0:
        # Lower the min_edge threshold for fatigue-flagged games
        # (we're more confident the edge is real, not noise)
        adjusted_min_edge = self._edge.min_edge_pct / fatigue_mult
        adjusted_min_ev = self._edge.min_ev / fatigue_mult

        # Re-check if the edge clears the adjusted thresholds
        if best is None:
            # Original evaluation said no edge — but with fatigue context,
            # the lower threshold might let it through
            best = self._edge.best_side(fair_yes_prob, price)
            # Apply adjusted thresholds manually
            if best and (best.raw_edge < adjusted_min_edge or best.ev < adjusted_min_ev):
                best = None

        # Log the fatigue signal for analysis
        log.info("fatigue_signal",
                 ticker=ticker,
                 rest_diff=matchup_fatigue.rest_differential,
                 multiplier=fatigue_mult,
                 home_b2b=matchup_fatigue.home_fatigue.is_second_of_b2b,
                 away_b2b=matchup_fatigue.away_fatigue.is_second_of_b2b)
```

### 4. Config additions to `config.yaml`

```yaml
# ── Fatigue Scanner (Strategy 1) ───────────────────────────────────────────
fatigue:
  enabled: true
  sports:                           # only analyze fatigue for these sports
    - basketball_nba
    - basketball_ncaab
  schedule_source: odds_api         # odds_api | nba_cdn | balldontlie
  schedule_cache_ttl_seconds: 3600  # refresh schedule hourly
  lookback_days: 7                  # how far back to check for recent games
  lookahead_days: 3                 # how far ahead to scan for upcoming games

  # Edge adjustment thresholds
  b2b_multiplier: 1.5              # edge confidence boost for back-to-back games
  rest_diff_multiplier: 1.3        # boost when rest differential >= 2 days
  min_rest_diff: 2                 # minimum rest day difference to trigger signal

  # Logging
  log_all_fatigue: true            # log fatigue context even for non-triggered games
```

---

## Data Flow (Complete)

```
Every scan cycle (60s):
│
├── OddsClient.get_events("basketball_nba")
│   └── Returns List[OddsEvent] with commence_time for each game
│
├── FatigueAnalyzer.build_context(odds_events)
│   ├── Group events by team
│   ├── Sort by commence_time
│   ├── For each team: compute rest_days, B2B flags, weekly load
│   └── Returns Dict[team_name → FatigueContext]
│
├── Matcher.match_all(kalshi_markets, odds_events)
│   └── Returns List[MatchResult]
│
└── For each MatchResult:
    ├── EdgeCalculator.best_side(fair_prob, kalshi_price)
    │   └── Returns EdgeResult or None
    │
    ├── FatigueAnalyzer.analyze_matchup(home, away, game_time)
    │   └── Returns MatchupFatigue with edge multiplier
    │
    ├── If fatigue_mult > 1.0:
    │   ├── Lower min_edge threshold (more permissive)
    │   ├── Log fatigue signal
    │   └── Re-evaluate if edge was borderline
    │
    ├── RiskManager.can_trade(size)
    │   └── Standard position/exposure/loss checks
    │
    └── Log PLACED / SKIPPED with fatigue metadata
```

---

## The "Zero New API" Bootstrap

The fastest path to production uses only data you already have. Here's how to detect back-to-backs from the Odds API alone:

```python
def detect_b2b_from_odds_events(events: List[OddsEvent]) -> Dict[str, FatigueContext]:
    """
    Build fatigue context for every team using only OddsClient data.

    The Odds API returns commence_time for all upcoming events.
    Group by team, sort by time, check for games within 28 hours of each other.
    """
    # Build team → sorted list of game times
    team_games: Dict[str, List[datetime]] = defaultdict(list)
    for event in events:
        team_games[event.home_team].append(event.commence_time)
        team_games[event.away_team].append(event.commence_time)

    for team in team_games:
        team_games[team].sort()

    # For each team's next game, compute fatigue context
    contexts: Dict[str, FatigueContext] = {}
    now = datetime.now(tz=timezone.utc)

    for team, games in team_games.items():
        # Find this team's next game
        future_games = [g for g in games if g > now]
        past_games = [g for g in games if g <= now]

        if not future_games:
            continue

        next_game = future_games[0]

        # Rest days = time since last game
        if past_games:
            last_game = past_games[-1]
            rest_hours = (next_game - last_game).total_seconds() / 3600
            rest_days = int(rest_hours / 24)
            is_b2b = rest_hours < 28  # less than 28 hours between tip-offs
        else:
            rest_days = 99  # no recent game data
            is_b2b = False

        # Games in last N days (using all available data)
        four_days_ago = next_game - timedelta(days=4)
        seven_days_ago = next_game - timedelta(days=7)
        games_in_4 = sum(1 for g in games if four_days_ago <= g < next_game)
        games_in_7 = sum(1 for g in games if seven_days_ago <= g < next_game)

        contexts[team] = FatigueContext(
            team=team,
            rest_days=rest_days,
            games_in_last_4_days=games_in_4,
            games_in_last_7_days=games_in_7,
            is_back_to_back=is_b2b,
            is_second_of_b2b=is_b2b,  # same thing from odds-only data
            is_road_b2b=False,         # can't determine from odds data alone
            opponent_rest_advantage=0,  # computed at matchup level
            fatigue_score=0.0,         # computed after all fields set
        )

    return contexts
```

**Limitation of the Odds API approach:** The Odds API only returns *upcoming* events, not completed ones. So for a Tuesday/Wednesday back-to-back, you'd see both games on Monday (both are upcoming), but by Wednesday morning the Tuesday game has dropped from the API. You'd need to **cache recent events** (append to a local JSONL file) to maintain the lookback window.

**Solution:** Add a `schedule_cache.jsonl` that appends every event seen by the OddsClient. On each cycle, read the last 7 days of cached events + current API events to build the full picture.

---

## Trade Log Extensions

Add these fields to trade_log entries for fatigue-triggered trades:

```json
{
  "...existing fields...",
  "fatigue_context": {
    "home_rest_days": 2,
    "away_rest_days": 0,
    "rest_differential": 2,
    "away_is_b2b": true,
    "fatigue_multiplier": 1.5,
    "fatigue_triggered": true
  }
}
```

This lets the backtester and learner correlate fatigue signals with actual outcomes.

---

## Testing Strategy

### Unit Tests (`tests/test_fatigue_analyzer.py`)

1. **B2B detection:** Two games 24h apart → `is_second_of_b2b = True`
2. **3-in-4 detection:** Three games in 4 days → `games_in_last_4_days = 3`
3. **Rest differential:** Home has 3 rest days, away has 0 → `rest_differential = 3`
4. **Edge multiplier:** B2B → 1.5, rest diff ≥ 2 → 1.3, no signal → 1.0
5. **Threshold adjustment:** With multiplier 1.5, min_edge of 3% becomes ~2%

### Integration Tests

1. **Scanner with fatigue:** Mock OddsClient returns events with B2B scheduling → verify fatigue metadata appears in trade log
2. **Borderline edge promotion:** Set up a match with 2.5% edge (below 3% min) but with B2B fatigue → verify it gets PLACED with the adjusted threshold
3. **No false positives:** Non-B2B game with same edge → verify it stays SKIPPED

### Paper Trading Validation

Run the fatigue scanner in paper mode alongside the existing scanner for 2-4 weeks. Compare:
- How many additional trades does fatigue flagging surface?
- What's the paper P&L on fatigue-triggered trades vs. non-fatigue trades?
- Are the fatigue-flagged edges larger on average? (They should be, if the thesis is correct)

---

## Implementation Phases

### Phase 1: Data Collection (1-2 days)
- Add `schedule_cache.jsonl` — persist every OddsEvent seen
- Add a simple script to verify B2B detection accuracy against known NBA schedule
- No trading changes, just logging

### Phase 2: Fatigue Analysis Module (2-3 days)
- Implement `FatigueAnalyzer` with the zero-new-API approach
- Unit tests for all fatigue computations
- Add fatigue context to trade log (logging only, no threshold changes)

### Phase 3: Edge Integration (1-2 days)
- Wire `FatigueAnalyzer` into `Scanner._evaluate_match()`
- Implement fatigue-adjusted edge thresholds
- Paper trade for 2 weeks, collect performance data

### Phase 4: Calibration (ongoing)
- After 50+ fatigue-triggered paper trades, analyze:
  - Win rate vs. expected win rate
  - Optimal multiplier values (are 1.5/1.3 too aggressive or too conservative?)
  - Whether to graduate from threshold adjustment to probability adjustment
- Feed results into the Learner (Phase 10) as a feature

### Phase 5: Dedicated Schedule API (optional)
- If the Odds API approach has gaps (missing games, stale data), add a proper NBA schedule API
- Enables road-B2B detection, travel distance computation, altitude factors
- Enables NCAAB fatigue analysis (conference tournament scheduling is brutal)

---

## Risk Considerations

1. **Overfitting to B2B narrative:** The 3.5-point historical B2B impact is a population average. Individual games vary enormously. The fatigue multiplier should be modest (1.3-1.5x) — it's an edge *filter*, not an edge *generator*.

2. **Books already price it in:** If Pinnacle has already adjusted, the fair value from consensus already reflects fatigue. The multiplier doesn't add edge — it increases *conviction that Kalshi hasn't caught up*. This is a subtle but important distinction.

3. **Sample size:** NBA back-to-backs happen ~2-3 times per team per month during the regular season. With 30 teams, that's maybe 40-50 B2B games per month total. Of those, maybe 10-15 will have a Kalshi market with sufficient liquidity. Of those, maybe 3-5 will show a meaningful consensus-Kalshi gap. Expect low frequency.

4. **NCAAB opportunity:** College basketball has even more scheduling chaos (mid-week conference games, tournament weekends) and Kalshi's NCAAB markets are even thinner. This could be the richer hunting ground.

---

*Document created: February 2026*
*Parent document: inter_game_mispricing_strategies.md*
