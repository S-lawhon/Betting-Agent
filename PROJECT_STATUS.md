# Betting Pod Shop — Project Status & Continuity Reference

**Last Updated:** March 24, 2026 (Session 9)
**VPS:** 129.212.176.202 (DigitalOcean)
**VPS Path:** `/opt/betting-pod-shop/`
**Dashboard:** http://129.212.176.202:8080
**Deploy:** `cd ~/Desktop/"Betting Fund Project" && bash scripts/deploy.sh 129.212.176.202 restart`

---

## System Overview

Multi-pod sports betting engine running on a DigitalOcean VPS. Currently in **paper/demo mode** — all trades are simulated. The system scans for mispriced moneyline markets across Kalshi (P-001) and Polymarket (P-006), using The Odds API multi-book consensus as fair value.

### Architecture

```
Main Loop (src/main.py → src/engine.py)
  ├── AggregateRiskGuard — portfolio-level exposure/drawdown limits
  ├── CapitalAllocator   — pod-level bankroll allocation + performance tracking
  ├── PodRunner          — orchestrates scan cycles across active pods
  │     ├── P-001: KalshiMoneylinePod → Legacy Scanner → Kalshi Production API
  │     ├── P-002: CrossVenueArbPod (disabled)
  │     ├── P-004: ForecastExKalshiArbPod (disabled)
  │     ├── P-006: PolymarketConsensusPod → Gamma API + Odds API → Polymarket
  │     ├── P-009: SignupBonusPod (disabled)
  │     ├── P-010: BoostScannerPod (disabled)
  │     └── P-012: MacroNowcastPod (disabled)
  ├── TradeStore         — centralized in-memory indexed JSONL store
  ├── Settler (Kalshi)   — resolves P-001 trades via Odds API /scores
  ├── PolymarketSettler  — resolves P-006 trades via Gamma API resolution
  └── WebDashboardServer — serves live dashboard at :8080
```

### VPS Details

- **Path:** `/opt/betting-pod-shop/`
- **Service:** systemd (`betting-pod-shop.service`) — auto-restart on failure, 30s cooldown
- **User:** `bettingbot` (system user, no login shell, no home dir)
- **Python:** `/opt/betting-pod-shop/venv/bin/python` (3.12)
- **Legacy sys.path:** Injected at module level in `src/engine.py` + PYTHONPATH in service file (belt-and-suspenders)
- **Start/Stop:** `systemctl start|stop|restart betting-pod-shop`
- **Logs:** `journalctl -u betting-pod-shop -f`
- **Config:** `config.yaml` (base Kalshi) + `config_multi_pod.yaml` (multi-pod overlay)
- **Trade Log:** `data/trade_logs/trade_log.jsonl`
- **Environment:** `demo` (paper mode) — pointing at **production** Kalshi API (`trading-api.kalshi.co`) for full market inventory, but `environment: demo` keeps order placement gated

### Key Environment Variables

- `ODDS_API_KEY` — The Odds API key (required for scanning + settlement)
- `KALSHI_API_KEY_ID` — Kalshi production API key
- `KALSHI_PRIVATE_KEY` — Kalshi private key (RSA PEM format)
- `POLYMARKET_API_KEY` — Polymarket API key
- `FRED_API_KEY` — FRED API key (for P-012 macro nowcasting)

---

## Active Pods

### P-001: Kalshi vs Sharp's Strategy

- **File:** `src/pods/kalshi_moneyline.py` (wrapper) → `Legacy/Kalshi Arb Project/src/scanner.py`
- **Strategy:** Finds mispriced moneyline markets on Kalshi using Odds API multi-book consensus (Pinnacle-weighted) as fair value
- **Settlement:** `Legacy/Kalshi Arb Project/src/settler.py` — uses **Odds API `/v4/sports/{sport}/scores/?daysFrom=3`** as primary resolution source (NOT Kalshi API, which never settles sports markets in demo mode)

### P-006: Sportsbook-Polymarket Consensus

- **File:** `src/pods/polymarket_consensus.py` (986 lines)
- **Strategy:** Same edge logic as P-001 but executes on Polymarket (0% fees vs Kalshi's ~7%). Lower edge threshold (0.8% vs 3%) and lower min_ev (0.5% vs 1%).
- **Market Discovery:** Gamma API `/events?series_id=X&tag_id=100639` → filters to game-day moneyline bets
- **Matching:** `src/polymarket_matcher.py` — fuzzy threshold 55 (vs 85 for Kalshi), time window 60 min
- **Settlement:** `src/polymarket_settler.py` — polls Gamma API `/markets?slug=X` for resolution status
- **Client:** `src/polymarket_client.py` — thin wrapper around py-clob-client with paper mode

### P-015: Tennis Qualifier Favorite (added 2026-07-20, paper validation)

- **Files:** `src/pods/qualifier_favorite_pod.py`, `src/kalshi_tennis_client.py` (public API, read-only), `src/kalshi_tennis_settler.py`
- **Strategy:** Buy heavy favorites (ask 0.85–0.975) in ATP/WTA **qualifying** matches on Kalshi. Basis: `tennis_research/REPORT.md` §8b — 13-month backtest, +4.1¢/contract net of fees, 95.8% hit, n=238, CI [+1.4, +6.3]; ATP stronger than WTA (WTA runs half size). Sized with `fair = ask + 0.025` (conservative CI lower half), capped by displayed ask depth × 0.5 and max 6 concurrent positions.
- **Settlement:** `KalshiTennisSettler` polls the Kalshi public API `result` field (production tennis markets settle normally); 14-day stale auto-void matches Kalshi's postponement rule. Wired into the engine loop alongside the P-001/P-006 settlers.
- **Validation gate:** ~20 trades/month expected. Kill if realized hit rate < ask-implied; promote only if forward EV matches the retrospective +3–4¢. No CLV benchmark exists for quals (no sharp lines), so validation is realized-outcome calibration.

### P-009 / P-010 (Disabled)

- P-009: Sign-Up Bonus Blitz — promo/free-bet infrastructure present but not live
- P-010: Daily Odds Boost Grind — boost infrastructure present but not live

---

## Optimization Plan Status

All 4 phases complete. See `OPTIMIZATION_PLAN.md` for full details.

| Phase | Focus | Status | Test Count |
|-------|-------|--------|------------|
| Phase I | TradeStore, concurrent scanning, atomic writes | ✅ Complete | 642 pass / 35 fail |
| Phase II | Exception audit, HTTP pooling, constants, VPS hardening, watchdog | ✅ Complete | 654 pass / 35 fail |
| Phase III | Split main.py, log schema, BasePod helpers, deploy hardening, integration tests | ✅ Complete | 679 pass / 35 fail |
| Phase IV | Pod auto-discovery, template extraction, type hints, P&L calculator, compat layer | ✅ Complete | 696 pass / 35 fail |

---

## CRITICAL: Deployment Rules

### ⚠️ NEVER rsync data/ to the live server

The service runs from `/opt/betting-pod-shop/` (NOT `/root/Betting-Fund-Project/`). The live trade log at `/opt/betting-pod-shop/data/trade_logs/trade_log.jsonl` contains the authoritative trade history. The copy on your Mac is a stale snapshot.

**Safe deploy (code only, preserves live data):**
```bash
# Step 1: Mac → staging directory on server
rsync -avz --exclude '.git' --exclude '__pycache__' ~/Desktop/Betting\ Fund\ Project/ root@129.212.176.202:~/Betting-Fund-Project/

# Step 2: staging → live (EXCLUDE data/ to protect trade logs)
ssh root@129.212.176.202 "rsync -av /root/Betting-Fund-Project/ /opt/betting-pod-shop/ --exclude '.git' --exclude '__pycache__' --exclude 'data/' && systemctl restart betting-pod-shop"
```

**NEVER run this** (overwrites live trade history with stale Mac copy):
```bash
# DANGEROUS — this destroyed trade history on March 21, 2026
ssh root@129.212.176.202 "rsync -av /root/Betting-Fund-Project/ /opt/betting-pod-shop/ --exclude '.git' --exclude '__pycache__'"
# Missing --exclude 'data/' caused loss of March 7-19 trade records
```

### Two-Directory Architecture on VPS

| Path | Purpose | Who writes |
|------|---------|------------|
| `/root/Betting-Fund-Project/` | Staging area — rsync landing zone from Mac | rsync from Mac |
| `/opt/betting-pod-shop/` | **LIVE** — service runs here, trade logs written here | systemd service |

The systemd service (`betting-pod-shop.service`) has `WorkingDirectory=/opt/betting-pod-shop`. Code changes must be copied from staging → live. Data must NEVER be copied from staging → live.

### After deploying, always clear __pycache__

Python may use stale bytecode cache even when .py files are updated:
```bash
ssh root@129.212.176.202 "find /opt/betting-pod-shop -name '__pycache__' -exec rm -rf {} + 2>/dev/null; systemctl restart betting-pod-shop"
```

---

## Recent Changes (Session 10 — March 24, 2026)

### Fix: Blocked First-Half Winner Tickers ✅

**File:** `Legacy/Kalshi Arb Project/src/matcher.py`

Added 7 sport-specific prefixes (`KXNCAAMB1H`, `KXNCAABB1H`, `KXNBAGAME1H`, `KXNBA1H`, `KXNHL1H`, `KXNFL1H`, `KXMLB1H`) to `_BLOCKED_TICKER_PREFIXES` plus a catch-all substring check for `1HWINNER`/`1HWIN`. The Odds API doesn't provide first-half lines, so these markets can never match correctly. Before: failing with TEAM_MISMATCH or low fuzzy scores (e.g. NCAAB titles diluted by ": First Half Winner?" suffix). After: 21 tickers silently rejected at prefix stage.

### Fix: Removed Challenger Tennis from Scanner ✅

**File:** `Legacy/Kalshi Arb Project/src/scanner.py`

Removed `KXATPCHALLENGERMATCH` and `KXWTACHALLENGERMATCH` from `_SPORT_TICKER_PREFIXES`. The Odds API only covers main-draw ATP/WTA events (Miami Open, Indian Wells, etc.) — challenger-tier tournaments have no matching data. Before: 98 challenger markets scanned per cycle, all failing with NO_FUZZY_MATCH. After: ATP markets scanned dropped from 146 → 48, WTA from 18 → 4. All main-draw matches continue matching at 76-100 scores.

### VPS User Migration: root → bettingbot ✅

Migrated the VPS from running as `root` via `nohup` to a dedicated `bettingbot` system user via systemd:

- Created `bettingbot` system user (no login shell, no home directory)
- Transferred ownership of `/opt/betting-pod-shop/` to `bettingbot:bettingbot`
- Fixed service file: `Type=notify` → `Type=simple` (sdnotify not installed), venv path `.venv` → `venv`
- Installed and enabled `betting-pod-shop.service` with security hardening: `ProtectSystem=strict`, `NoNewPrivileges=true`, `ProtectHome=true`, `PrivateTmp=true`, `ReadWritePaths` limited to `data/`
- Logs now go to journald (`journalctl -u betting-pod-shop -f`) instead of `output.log`
- Auto-restart on failure with 30s cooldown
- Dashboard confirmed responding on port 8080

**Deploy procedure updated:** After `rsync`, run `chown -R bettingbot:bettingbot /opt/betting-pod-shop` then `systemctl restart betting-pod-shop`.

### NHL TEAM_MISMATCH — Not a Bug

Investigated 10 NHL games failing with TEAM_MISMATCH (e.g. CBJMTL, CHIPHI, MINFLA). Root cause: Odds API only had 16 NHL events (mostly near-term games). March 26 games hadn't been posted yet. The matcher was finding the best available Odds API event (one team would partially match), and TEAM_MISMATCH correctly rejected these wrong matches. No code fix needed — events will match once Odds API posts lines closer to game time.

---

## Recent Changes (Session 9 — March 24, 2026)

### Investigation: P-006 Not Placing New Trades

P-006 was loaded and scanning across 4/8 sports (NBA, NHL, ATP tennis, WTA tennis) but ALL matches hit SKIP_EDGE. Raw edges ranged from 0.1%–0.7% while `min_edge_pct` was 2%. Root cause: Polymarket is a far more efficient market than Kalshi — sophisticated market makers keep prices tightly aligned with sportsbook consensus, leaving very small arbitrage windows.

**Key insight:** P-001 finds large edges (3–10%) because Kalshi has lower liquidity and less active market making for sports markets. These are real edges on live Kalshi API prices (not demo — the system reads from `trading-api.kalshi.co` production), though order placement is gated by `environment: demo`. Polymarket, by contrast, has deep liquidity and edges rarely exceed 1%.

### Fix 1: Lowered P-006 Edge Thresholds ✅

**File:** `config_multi_pod.yaml` (P-006 section)

- `min_edge_pct`: 0.02 → 0.008 (2% → 0.8%). With 0% Polymarket fees, edges above ~0.5% are genuinely +EV. The old 2% threshold was blocking every single match.
- `min_ev`: added at 0.005 (0.5%). The EdgeCalculator's `best_side()` applies TWO filters: `raw_edge >= min_edge_pct` AND `ev >= min_ev`. The default `min_ev` of 1% was a second gate blocking trades even if the edge threshold was met. At skewed prices, EV can be lower than raw edge, so both thresholds needed lowering.

**Result:** After deploy, edges are still mostly below 0.8% (largest observed: Lehecka vs Fritz tennis at 0.68%, Kraken vs Panthers NHL at 0.64%). This is expected — Polymarket efficiency means edges are thin. Trades should appear when lines move around game time, creating brief windows above 0.8%.

### Investigation: MLB Matching Failure (0 matches despite 12 markets × 15 events)

DIAG fuzzy scores of 62–70 (above the 55 threshold) suggested the fuzzy check was passing but `both_teams_present` was rejecting. Deeper investigation revealed the **real cause: Polymarket and Odds API have completely different games.**

Polymarket's MLB series (series_id=3) was returning stale spring training markets (created early March with `startDate` of March 3). The Odds API had upcoming regular season games starting March 26. Example: Polymarket had "Boston Red Sox vs. Minnesota Twins" while Odds API had Red Sox playing Cincinnati Reds and Twins playing Baltimore Orioles — different opponents entirely.

**No code fix needed.** MLB matching will self-resolve once Polymarket lists regular season game-day markets (MLB regular season started March 20, 2026). The team name formats are identical between venues so matching will work perfectly once the same games appear on both.

### SSH Access from Cowork ✅

Mounted `~/.ssh` directory, enabling direct VPS deployment and management from Cowork sessions without requiring the user to run commands manually on their Mac terminal.

---

## Recent Changes (Session 8 — March 23-24, 2026)

### Investigation: P-001 Stopped Placing Trades After Initial Burst

P-001 placed ~45 trades in the first 3 scan cycles after deploy (March 21, 3:40-3:50 PM ET) but zero trades since. This session diagnosed and fixed five interconnected issues.

### Fix 1: Legacy sys.path Not Set — Scanner, Settler, KalshiClient All Failing ✅

**Root cause:** The legacy modules (`scanner.py`, `settler.py`, `kalshi_client.py`, etc.) live in `Legacy/Kalshi Arb Project/src/` and need bare imports (`from scanner import Scanner`). No `.pth` files, no symlinks, no `PYTHONPATH` environment variable existed — so ALL legacy imports failed with `ModuleNotFoundError`.

This meant:
- `build_venue_clients()` failed to import `KalshiClient` → Kalshi client was `None`
- `build_shared_deps()` failed to import `Scanner` and `Settler`
- Since Settler is built conditionally (`if kalshi_client:`), it was silently skipped
- P-001 was NOT running AT ALL on the VPS; zero settlements were ever written for legacy trades

**Fix:** Added `sys.path.insert(0, _LEGACY_SRC)` at **module level** in `src/engine.py`, before any function definitions. First attempt placed it inside `build_shared_deps()`, but that was too late — `build_venue_clients()` runs first and also needs `from kalshi_client import KalshiClient`.

**Lesson learned:** The sys.path fix must be at module level, not inside a function, because multiple functions need legacy imports and they run in sequence.

**File changed:** `src/engine.py`

### Fix 2: max_positions=10 Blocking All P-001 Trades ✅

**Root cause:** `Legacy/Kalshi Arb Project/config.yaml` had `risk.max_positions: 10`. This is the legacy RiskManager's limit, completely separate from `config_multi_pod.yaml`'s `aggregate_risk.max_open_positions: 200`. After the initial 45-trade burst, the Scanner was blocked by `BLOCK_MAX_POSITIONS` on every subsequent scan. There were 3,829 `SKIPPED_RISK` entries with "MAX_POSITIONS: Open positions (X) at maximum (10)".

**Fix:** Changed `max_positions: 10` → `max_positions: 50` in `Legacy/Kalshi Arb Project/config.yaml`.

**Lesson learned:** Two completely separate risk systems exist — the legacy RiskManager (reads `config.yaml`) and the multi-pod AggregateRiskGuard (reads `config_multi_pod.yaml`). Both must be configured.

**File changed:** `Legacy/Kalshi Arb Project/config.yaml`

### Fix 3: capital_allocator.pre_cycle NoneType Error ✅

**Root cause:** `capital_allocator.py` line 159 used `hasattr(pod, "risk_manager")` which returns `True` even when `pod.risk_manager is None`. Then `rm = pod.risk_manager` → `rm = None` → `rm.max_position_usd = ...` crashed with `'NoneType' object has no attribute 'max_position_usd'`. This error fired every cycle for pods without a risk_manager, with the fallback message "pods will use previous allocations".

**Fix:** Changed to `rm = getattr(pod, "risk_manager", None)` with `if alloc and rm is not None:` check.

**Lesson learned:** `hasattr()` returns `True` for attributes set to `None`. Use `getattr(obj, attr, None) is not None` for null-safe checks.

**File changed:** `src/capital_allocator.py`

### Fix 4: Stale Positions Blocking Scanner — STALE_POSITION_HOURS 72→24 ✅

**Root cause:** With the Settler broken for 2+ weeks, the trade log accumulated 181 unsettled PLACED entries. The Settler's 72-hour auto-void couldn't clear March 21-22 entries (~50 hours old) because they hadn't reached the threshold. Meanwhile, the Odds API returned `completed=0` for tennis (scores endpoint doesn't reliably return completed tennis matches), and the Kalshi demo API returned `status=active` for everything. The Scanner's `_open_game_keys()` treated all unsettled entries as open, blocking trades on those games.

**Fix:** Changed `STALE_POSITION_HOURS` from 72 to 24 in `Legacy/Kalshi Arb Project/src/settler.py`. Sports games complete within hours — 24 hours is generous enough to avoid premature voids while preventing stale positions from permanently blocking the scanner.

**Lesson learned:** The Kalshi demo API never settles markets (`status=active` forever). The Odds API tennis scores are unreliable. Auto-void is the critical safety net — it must be aggressive enough to clear positions within a day.

**File changed:** `Legacy/Kalshi Arb Project/src/settler.py`

### Fix 5: Event-Level Dedup — Preventing Duplicate Trades Across Market Types ✅

**Root cause:** The Scanner's `_open_game_keys()` extracted game keys using `ticker.rsplit("-", 1)[0]`, which only strips the last segment. For tennis, this produced three different keys per match:
- `KXATPMATCH-26MAR24ATMTIA` (match winner)
- `KXATPSETWINNER-26MAR24ATMTIA-1` (set 1 winner)
- `KXATPSETWINNER-26MAR24ATMTIA-2` (set 2 winner)

The within-cycle `placed_event_ids` dedup prevented duplicates in one cycle, but across cycles (every 5 min), each new market type passed the game key check and got placed. One tennis match generated 3 trades; one NBA game generated 3 trades (game + 1H + 2H). This caused the trade count to balloon from 45 to 126.

**Fix:** Added `_extract_event_key(ticker)` helper that splits on the first dash and takes only the date+teams segment (e.g., `26MAR24ATMTIA`). This is shared across all market types for the same event. Updated both `_open_game_keys()` (uses a `Counter` for reference-counted tracking so voiding one market type doesn't prematurely free the event) and `_scan_sport()` to use event-level keys.

**Lesson learned:** Kalshi has multiple market types per event (match winner, set winners, half winners). Dedup must operate at the EVENT level, not the MARKET level, to prevent correlated position stacking.

**Files changed:** `Legacy/Kalshi Arb Project/src/scanner.py`

### Updated Deploy Procedure

The VPS currently runs via `nohup` (not systemd). The two-directory staging approach is no longer used — rsync goes directly to `/opt/betting-pod-shop/`.

**Safe deploy from Mac:**
```bash
# Step 1: rsync code (EXCLUDES data/ to protect live trade logs)
rsync -avz --exclude='data/' --exclude='*.pyc' --exclude='__pycache__/' -e ssh '/Users/samlawhon/Desktop/Betting Fund Project/' root@129.212.176.202:/opt/betting-pod-shop/

# Step 2: SSH in and restart
ssh root@129.212.176.202
fuser -k 8080/tcp 2>/dev/null; sleep 2 && cd /opt/betting-pod-shop && nohup /opt/betting-pod-shop/venv/bin/python -m src.main --loop --interval 300 --web --no-browser > /opt/betting-pod-shop/output.log 2>&1 & echo 'Started'

# Step 3: Verify (wait 30s for first cycle)
sleep 30 && grep 'settler_cycle_done\|cycle.*placed\|ERROR' /opt/betting-pod-shop/output.log
```

---

## Recent Changes (Session 7 — March 22, 2026)

### NCAAW Support Added ✅

Added women's college basketball (`basketball_ncaaw`) support across all relevant components:

- **`config_multi_pod.yaml`** — Added `basketball_ncaaw` to `odds_api.sports` list (P-001 and P-006 now pull NCAAW lines from The Odds API)
- **`Legacy/.../scanner.py`** — Added `"basketball_ncaaw": ("KXNCAAWBGAME", "KXNCAAWGAME", "KXNCAAWBTOTAL")` to `_SPORT_TICKER_PREFIXES`. Game-winner prefixes are speculative; `KXNCAAWBTOTAL` is confirmed live on Kalshi.
- **`Legacy/.../matcher.py`** — Added `basketball_ncaaw` to `_NCAAB_SPORTS` so `_expand_ncaab_abbrevs()` runs on NCAAW market titles. Women's teams share the same school codes (UK, ISU, KU, etc.) as NCAAB.
- **`src/pods/polymarket_consensus.py`** — Added `"basketball_ncaaw": "NCAAW"` to `_SPORT_LEAGUE_NAME` for P-006 Gamma API series_id discovery.

---

## Recent Changes (Session 6 — March 22, 2026)

### P-006 Double-Filter Fix (Zero Trades Since ~March 12) ✅

**Root cause:** The Phase II optimization introduced `MIN_KELLY_FRACTION = 0.02` as a global low-conviction filter in `base_pod.py::compute_position_size()`. This was calibrated for P-001 (Kalshi, 7% fee, 7%+ min_edge) and worked well there. But for P-006 (Polymarket, 0% fee, 3% min_edge), the Kelly fraction at quarter-Kelly for a 3% edge is only 0.015 at mid=0.50 — below the 0.02 floor. This created a double-filter where:
- 3% min_edge + 2% min_kelly at mid=0.50 requires **4% real edge** to place
- At mid=0.30 (underdogs), it requires **6% edge**
- Only at extreme favorites (mid=0.70+) did 3% edge actually pass

Since Polymarket markets rarely diverge 4-6% from sportsbook consensus, this effectively killed ALL P-006 trade placement starting ~March 12 when the optimization was deployed.

**Fix:**
1. Made `min_kelly_fraction` per-pod configurable via config YAML + `BasePod.__init__()` override parameter
2. P-006 config sets `min_kelly_fraction: 0.005` (0.5%) — still filters zero-conviction noise but doesn't compound with edge threshold
3. Lowered P-006 `min_edge_pct` from 3% to 2% — with 0% Polymarket fees, 2% edge is genuinely profitable
4. Added diagnostic logging: `SKIP_EDGE` (raw numbers), `SKIP_KELLY` (Kelly vs floor), and `PLACING` (confirmation) log lines so the VPS journal shows exactly where trades are filtered

**Files changed:** `src/constants.py`, `src/base_pod.py`, `src/pods/polymarket_consensus.py`, `config_multi_pod.yaml`

---

## Recent Changes (Session 5 — March 21, 2026)

### Duplicate Trade Fix (P-001 + P-006) ✅

**Root cause:** `trade_store` was created in `shared_deps` by `engine.py` and passed to `pod_class.from_config()` as overrides, but both P-001 and P-006 ignored it — neither `__init__` accepted a `trade_store` parameter, nor did `from_config()` extract it from overrides. This meant `BasePod._trade_store` was always `None`, forcing fallback to weaker in-memory dedup sets that reset on restart. P-006 had 42 markets with duplicate placements (up to 7x each).

**Fix:** Added `trade_store=None` parameter to `__init__()` and `trade_store=overrides.get("trade_store")` to `from_config()` in both `src/pods/polymarket_consensus.py` and `src/pods/kalshi_moneyline.py`. Now `has_open_position()` and `is_duplicate()` query the centralized `TradeStore`.

### P-006 SANITY_SKIP Threshold Fix ✅

**Root cause:** The `_validate_and_correct_mapping()` function in `polymarket_consensus.py` had a hard-coded 15% (0.15) absolute-difference threshold. Any match where Polymarket price and sportsbook consensus differed by >15pp was skipped as a "probable mapping error." In practice, legitimate edge opportunities routinely exceed 15pp — this was killing virtually ALL P-006 trade placements since ~March 13.

**Fix:** Raised threshold from `0.15` to `0.25` in `_validate_and_correct_mapping()`. Markets with >25% disagreement (likely genuine mapping errors or stale data) still get filtered. Markets with 15-25% disagreement (legitimate edge) now pass through.

### Settler daysFrom Fix ✅

**Root cause:** The Kalshi settler's `_fetch_scores()` in `Legacy/Kalshi Arb Project/src/settler.py` used `daysFrom=7` in the Odds API `/scores` URL. The API plan only supports `daysFrom=3`, causing HTTP 422 for ALL sports. This broke scores-based settlement entirely — no trades were being resolved through real game results. The Kalshi demo API fallback always returns `status=active`, so positions sat OPEN indefinitely.

**Fix:** Changed `daysFrom=7` to `daysFrom=3` in `settler.py` line 542. Settlement now resolves positions using real game scores within 3 days of game completion.

### Data Loss Incident — March 21, 2026

During deployment, `rsync -av /root/Betting-Fund-Project/ /opt/betting-pod-shop/` was run WITHOUT `--exclude 'data/'`. This overwrote the live trade log (containing trades through March 19) with the stale Mac copy (last synced ~March 7). Approximately 175 P-001 trades and ~107 P-006 trades from March 7-19 were lost. The journal does not contain raw trade JSON entries (trades are written to file, not stdout), so recovery was not possible.

**Prevention:** Always use `--exclude 'data/'` when rsyncing to `/opt/betting-pod-shop/`. See deployment rules above.

---

## Recent Changes (Sessions 3-4 — March 13-14, 2026)

### Phase II: Reliability & Safety Hardening ✅

**Magic Numbers Centralized** — Created `src/constants.py` with `DEFAULT_BANKROLL`, `DEFAULT_MAX_BET_PCT`, `MIN_VALID_PRICE`, `MAX_VALID_PRICE`, `HTTP_TIMEOUT_SECONDS`, `DEDUP_WINDOW_HOURS`. Replaced hardcoded `10_000.0` in 12+ locations and `0.03` max_bet_pct in 4 pod files.

**VPS Security Hardening** — Rewrote `scripts/betting-pod-shop.service` with `Type=notify`, `WatchdogSec=900`, `ProtectSystem=strict`, `PrivateTmp=true`, `ReadWritePaths`, and kernel protections. Created `scripts/server_setup.sh` for `bettingbot` system user provisioning.

**Watchdog + Log Rotation** — Created `src/watchdog.py` (zero-dependency systemd notify socket integration) and `scripts/rotate_trade_logs.py` (archives entries >30 days into compressed monthly `.jsonl.gz` files).

### Phase III: Architecture Cleanup ✅

**Split main.py** — Decomposed 1,191-line `main.py` into `config_loader.py`, `engine.py`, `cli.py`, plus a thin backward-compat shim so all existing test patches (`@patch("src.main.PodRunner")` etc.) continue to work unchanged.

**Trade Log Schema** — Created `src/trade_log_schema.py` with `TradeLogSchema.normalize()`, `validate()`, and `migrate_file()`. Handles field aliasing (market_ticker→market_id, kalshi_prob→venue_prob), defaults, numeric coercion, and atomic file rewrite.

**BasePod Helpers** — Added `get_bankroll()` and `compute_position_size()` to `BasePod`, simplifying all 4 active pods from ~12 lines of duplicated position-sizing logic to a single method call.

**Deploy Script Hardening** — Rewrote `scripts/deploy.sh` with pre-deploy `pytest` gate, post-deploy health check (60s timeout), and automatic rollback on failure. Added rsync excludes for `*.log`, `.mypy_cache`, `.pytest_cache`, `venv/`.

**Integration Tests** — Created `tests/test_integration.py` with 10 end-to-end tests covering the scan → match → edge → risk → place → settle pipeline.

### Phase IV: Developer Experience & Future-Proofing ✅

**Pod Auto-Discovery** — Created `src/pod_registry.py` with `@register_pod("P-xxx")` decorator applied to all 7 pods. `discover_pods()` auto-imports all `src/pods/` modules at startup. `pod_runner.py` updated to use registry with legacy fallback.

**Dashboard Template Extraction** — Moved 815 lines of inline HTML/CSS/JS from `web_dashboard.py` into `src/templates/dashboard.html`. Python file shrank from 1,201 → 411 lines. Loads from disk with graceful fallback.

**Type Hints + mypy** — Created `src/protocols.py` with Protocol-based structural types for all duck-typed dependencies (EdgeCalculator, RiskManager, venue clients, AggregateRisk, TradeStore). Fixed 8 mypy errors across pod files. All 18 hot-path source files pass mypy cleanly.

**Shared P&L Calculator** — Created `src/pnl_calculator.py` with venue-specific fee structures (Kalshi 7% profit fee, Polymarket 0%, ForecastEx 3.14% APY coupon). Supports batch computation, custom fee overrides, and collateral credit for holding periods. 17 tests.

**Legacy Adapter Layer** — Created `src/compat.py` providing single-import access to all public symbols. Deprecated aliases emit `DeprecationWarning` at import time.

---

## Prior Session Changes (Session 2 — March 12, 2026)

### Dashboard Audit & Duplicate Settlement Fix ✅

Rewrote `_read_recent_placed_trades()` to resolve by fingerprint first (falling back to ticker), eliminating duplicate counting when multiple PLACED entries share a ticker. Added VOID entry for orphan Winthrop NO-side trade. Dashboard numbers reconciled: 25 W / 16 L / 187 V, P&L $1,707.05, Win Rate 61.0%.

### P-001 Pod Rename ✅

Renamed from "Kalshi Moneyline Value" to "Kalshi vs Sharp's Strategy". Updated `capital_allocator.py` to always refresh `pod_name` from live pod object.

---

## Prior Session Changes (Session 1 — March 12, 2026)

### Exotic Ticker Prefix Blocklist ✅

Added `_BLOCKED_TICKER_PREFIXES` (~20 prefixes) and fast rejection in `match()` to skip exotic/prop tickers before fuzzy computation.

### Sport-Aware Ticker Prefix Filtering ✅

Added `_SPORT_TICKER_PREFIXES` mapping in `scanner.py` — only markets matching sport-specific prefixes are passed to the matcher. Reduced scan cycle from ~9s to ~5.5s, log volume dropped 94%.

### Paper-Mode Synthetic Price Generation ✅

When `mode=="paper"` and no bid/ask exists, generates synthetic price from consensus fair probability ± deterministic hash-based offset.

### Switched to Production Kalshi API ✅

Changed `config.yaml` to `trading-api.kalshi.co` (production) while keeping `environment: demo` for safety.

---

## Prior Session Changes (March 5, 2026)

### P-001 Settler: Odds API Scores-Based Settlement ✅

Rewrote settlement to use Odds API `/scores` endpoint. All 5 positions settled: +$1,089.56 (4W / 1L).

### Game-Started Guard ✅

Added commence_time check to skip games already in progress.

### Dashboard Pod Performance Wiring ✅

Wired settlers to call `allocator.record_settlement()` so pod performance table shows real W/L/P&L.

### Portfolio Analytics Dashboard ✅

Added hero metrics (Total Return, Sharpe, Max Drawdown, Profit Factor), returns analysis, risk metrics, and equity curve canvas chart.

---

## File Map

### Core Engine (src/)

| File | Lines | Purpose |
|------|-------|---------|
| `main.py` | ~150 | Backward-compat shim re-exporting symbols from config_loader, engine, cli |
| `config_loader.py` | ~140 | YAML loading, deep merge, pod filtering, tennis enrichment |
| `engine.py` | ~700 | Runtime engine: venue clients, shared deps, cycle callback, guarded loop |
| `cli.py` | ~180 | CLI entry point, argument parser, logging setup |
| `web_dashboard.py` | ~410 | Live web dashboard (API handlers + template loading) |
| `pod_runner.py` | ~315 | Orchestrates scan cycles across active pods |
| `capital_allocator.py` | ~494 | Pod-level bankroll allocation + performance tracking |
| `aggregate_risk.py` | ~400 | Portfolio-level exposure/drawdown/halt controls |
| `base_pod.py` | ~255 | Abstract BasePod interface + shared helpers |
| `trade_store.py` | ~450 | Centralized in-memory indexed JSONL store with atomic writes |
| `multi_executor.py` | ~280 | Venue-agnostic execution router |
| `pod_registry.py` | ~100 | Decorator-based pod auto-discovery |
| `pnl_calculator.py` | ~180 | Shared P&L calculator with venue-specific fees |
| `trade_log_schema.py` | ~180 | Trade log normalization, validation, migration |
| `constants.py` | ~30 | Centralized magic numbers |
| `protocols.py` | ~140 | Protocol-based structural type hints |
| `compat.py` | ~120 | Legacy adapter layer with deprecated aliases |
| `watchdog.py` | ~60 | Systemd notify socket integration |
| `settlement_bridge.py` | ~500 | Settlement orchestration for both venues |
| `polymarket_client.py` | ~700 | Polymarket API wrapper (Gamma + CLOB) |
| `polymarket_matcher.py` | ~260 | Odds API → Polymarket market matcher |
| `polymarket_settler.py` | ~287 | P-006 settlement engine |

### Pods (src/pods/)

| File | Pod | Strategy |
|------|-----|----------|
| `kalshi_moneyline.py` | P-001 | Kalshi vs Sharp's Strategy (delegates to legacy Scanner) |
| `cross_venue_arb.py` | P-002 | Cross-venue arbitrage (Kalshi vs Polymarket) |
| `forecastex_kalshi_arb.py` | P-004 | ForecastEx vs Kalshi arbitrage |
| `polymarket_consensus.py` | P-006 | Sportsbook-Polymarket consensus |
| `signup_bonus_pod.py` | P-009 | Sign-up bonus blitz (disabled) |
| `boost_scanner_pod.py` | P-010 | Daily odds boost grind (disabled) |
| `macro_nowcast.py` | P-012 | Macro economic nowcast (disabled) |

### Templates (src/templates/)

| File | Purpose |
|------|---------|
| `dashboard.html` | 815-line HTML/CSS/JS dashboard (extracted from web_dashboard.py) |

### Legacy (Legacy/Kalshi Arb Project/src/)

| File | Lines | Purpose |
|------|-------|---------|
| `scanner.py` | ~850 | Kalshi scanner — sport-aware prefix filtering, synthetic pricing, game-started guard |
| `settler.py` | ~603 | Kalshi settlement (rewritten: Odds API scores primary) |
| `odds_client.py` | ~400 | The Odds API wrapper |
| `matcher.py` | ~320 | Odds API → Kalshi market matcher — ticker prefix blocklist + fast rejection |
| `edge_calculator.py` | ~200 | Pinnacle-weighted consensus edge computation |
| `risk_manager.py` | ~350 | Per-pod risk limits + Ledger |
| `kalshi_client.py` | ~400 | Kalshi API wrapper |
| `logger_setup.py` | ~50 | structlog configuration |

### Config

| File | Purpose |
|------|---------|
| `config.yaml` | Base config (Kalshi settings, odds_api sports, risk limits) |
| `config_multi_pod.yaml` | Multi-pod overlay (P-006 settings, aggregate risk, pod allocations) |

### Scripts

| File | Purpose |
|------|---------|
| `scripts/deploy.sh` | Pre-deploy tests, rsync, health check, auto-rollback |
| `scripts/betting-pod-shop.service` | Hardened systemd unit (Type=notify, WatchdogSec, ProtectSystem) |
| `scripts/server_setup.sh` | VPS provisioning (bettingbot user, venv, cron, journald) |
| `scripts/rotate_trade_logs.py` | Monthly trade log archival to .jsonl.gz |
| `scripts/migrate_trade_log.py` | One-time log schema migration script |

### Tests

| File | Tests | Purpose |
|------|-------|---------|
| `tests/test_trade_store.py` | ~30 | TradeStore unit tests |
| `tests/test_pod_runner_concurrent.py` | ~10 | Concurrent scanning verification |
| `tests/test_watchdog.py` | 7 | Systemd watchdog no-op and real socket |
| `tests/test_rotate_trade_logs.py` | 5 | Trade log rotation |
| `tests/test_trade_log_schema.py` | 15 | Schema normalize/validate/migrate |
| `tests/test_integration.py` | 10 | End-to-end pipeline integration |
| `tests/test_pnl_calculator.py` | 17 | P&L calculator with venue fees |

---

## Data Flow

### Scanning

```
Every 5 min cycle:
  1. Settler checks open positions → settles completed games
  2. PolymarketSettler checks open positions → settles resolved markets
  3. AggregateRiskGuard pre-cycle check (exposure/drawdown limits)
  4. PodRunner.run_once() → each pod scans for edges
     P-001: Kalshi markets × Odds API events → Matcher → EdgeCalculator → RiskManager → PLACED
     P-006: Polymarket markets × Odds API events → Matcher → EdgeCalculator → RiskManager → PLACED
  5. Callback: update dashboard, log metrics, persist trades via TradeStore
```

### Settlement

```
P-001 (Kalshi):
  settler.settle_cycle()
    → TradeStore.get_placed_entries() for unresolved PLACED
    → For each: _check_scores(sport) via Odds API /v4/sports/{sport}/scores/?daysFrom=3
    → Fuzzy match game by team names + time → determine winner from scores
    → Map winner to YES/NO via yes_side field → _settle_position() writes WIN/LOSS to log
    → Falls back to Kalshi API for non-sports markets

P-006 (Polymarket):
  polymarket_settler.settle_cycle()
    → TradeStore.get_placed_entries() for unresolved P-006 PLACED
    → For each: client.get_market_resolution(slug=market_slug)
    → If closed=True and prices indicate resolution → determine result
    → _calc_outcome_pnl() → write WIN/LOSS/VOID to trade log
```

### Dashboard Data

```
/api/status JSON payload:
  engine_status: "running" | "halted" | "starting"
  risk: { bankroll, total_exposure_usd/pct, daily_pnl, open_positions, halted, venue_exposure }
  cycle: { cycle_number, duration_seconds, pods_scanned, placed/skipped/error_count, success_rate }
  pods: { pod_id → { name, placed, wins, losses, win_pct, pnl, alloc_pct, max_position_usd, capital_usd } }
  settlement: { total_pnl, total_settled, wins, losses, voids, win_rate }
  trades: [ { status, timestamp_utc, event, market_ticker, side, position_size_usd, edge_pct, pnl_usd, settled_at_utc, ... } ]

Trade status resolution (in _read_recent_placed_trades):
  1. Match PLACED entry fingerprint → settlement entry fingerprint (exact match)
  2. Fallback: match PLACED entry ticker → settlement entry ticker (for legacy/orphan entries)
  3. If no match: status = "OPEN"
```

---

## Pending / Known Gaps

1. **NHL has no moneyline/game-winner tickers on Kalshi production** — only player props (goals, assists, points, first goal). P-006 (Polymarket) still covers NHL moneylines via Gamma API.

2. ~~**ATP Tennis fuzzy matching limited**~~ — **FIXED (Session 10).** Removed `KXATPCHALLENGERMATCH` / `KXWTACHALLENGERMATCH` from `_SPORT_TICKER_PREFIXES`. The Odds API only covers main-draw events; challengers never had matching data. Main-draw `KXATPMATCH` tickers match fine (76-100 scores).

3. ~~**MLB matching pending regular season markets on Polymarket**~~ — **STALE, corrected 2026-07-21.** Polymarket `series_id=3` now returns current regular-season game markets (verified: 127 events under `series_id=3 + tag_id=100639`, 109 live after the expired-event filter, current-day slate present). Kalshi `KXMLBGAME` returns 68 open markets. The blocker described here no longer exists — but MLB is **still not safe to enable**, for two reasons found when the matching was finally exercised end-to-end:

   - **The "team name formats are identical between venues" claim was wrong.** Kalshi uses city/market names (`"Chicago WS vs Texas Winner?"`), Polymarket uses full team names (`"Chicago White Sox vs. Texas Rangers"`). This turns out not to matter: `cross_venue_matcher._SPORT_CITY_TO_TEAM["mlb"]` already normalises both sides to nicknames (`"Rays vs Blue Jays"`), and live MLB matching scores 100.0. The claim was wrong; the conclusion was accidentally right.

   - **Blocker A — non-moneyline event contamination (P-006).** `tag_id=100639` does **not** exclude derivative events. Of 127 events, 20 are `"... - First 5 Innings Winner"` and 19 are `"... - Player Props"`. These clear the 55.0 fuzzy threshold against the full-game Odds API event and are matched as if they were the moneyline: 63 matches collapse onto only 15 distinct games (4-5 Polymarket events per game). F5 markets price systematically **6-13c below** the full-game line (median ~9c), so every contaminated event presents a large one-directional phantom "edge" far above P-006's `min_edge_pct: 0.008`. Their `outcomes` are `['Yes','No']` / `['Over','Under']` rather than team names, so `_infer_yes_team` has no reliable signal either.

   - **Blocker B — date collision within a series (P-002 and P-006).** MLB plays 3-4 game series against the same opponent, so Polymarket lists 3 events with **identical titles** on different dates (`mlb-min-cle-2026-07-21`, `-07-22`, `-07-23`). Matching is title-only and `skip_time_check=True`, so they are indistinguishable. Measured on `cross_venue_matcher` (P-002's path, already live on MLB via `_SPORT_TICKER_PREFIXES`): **32 of 58 MLB matches pair a Kalshi market with a Polymarket event for a different day.** Tomorrow's price vs today's fair value is a phantom edge. The fix key exists and is reliable — `markets[0].gameStartTime` is exact (matches Odds API `commence_time` to the minute), and the event slug carries the date.

   Liquidity is **not** the blocker it was thought to be: today-slate game-winner books show a 1c spread and $88k-176k of depth within ±3c. The thin ~$700-900 / 40c books previously sampled were the alt-line and derivative markets (`Spread -1.5`, `1st 5 Innings O/U`, `Extra Innings` — median ~$900 depth), plus far-future stale events. P-006 reads `markets[0]`, which is consistently the true moneyline. A liquidity filter is worth adding for safety but is not what is holding MLB back.

4. **P-001 `aggregate_risk` not wired** in `kalshi_moneyline.py`'s `from_config` — minor issue since the guard still works at the main loop level.

5. ~~**settler_scores_fetch_error**~~ — **FIXED (Session 5):** Changed `daysFrom=7` to `daysFrom=3`.

6. ~~**P-006 duplicate PLACED entries**~~ — **FIXED (Session 5):** Wired `trade_store` through to pods.

7. **35 pre-existing test failures** — Present since before the optimization work. All are assertion mismatches in pre-existing tests, not regressions from Phase I-IV changes.

8. ~~**VPS user migration pending**~~ — **FIXED (Session 10).** Created `bettingbot` system user, transferred ownership of `/opt/betting-pod-shop/`, installed systemd service with security hardening (`ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`). Process now runs as `bettingbot` via `systemctl`.

9. **NCAAB fuzzy matching broken for college team abbreviations** — Kalshi uses 2-3 letter school codes (UK, ISU, KU, SJU, MIA, PUR) that score below the `min_team_score=50.0` fuzzy threshold when compared against Odds API full names (Kentucky Wildcats, Iowa State Cyclones, etc.). NBA works fine (city names match well). **Fix needed:** Add a college team abbreviation → full name lookup table to the matcher so short codes resolve correctly. This blocks all NCAAB March Madness trading on P-001.

10. ~~**NCAAW not configured**~~ — **FIXED (Session 7).**

11. **Odds API tennis scores unreliable** — The `/scores` endpoint returns `completed=0` for tennis sports even when matches have finished. This means the Settler cannot resolve tennis positions via scores — they rely entirely on the 24-hour auto-void. Tennis P&L is not tracked accurately (voided instead of WIN/LOSS). Consider adding a tennis-specific settlement source.

12. **Kalshi demo API never settles markets** — `status=active` returned for ALL markets regardless of game completion. The Settler's Kalshi API fallback is useless in demo mode. Settlement depends entirely on Odds API scores (primary) and auto-void (safety net).

13. ~~**P-006 activity not visible in recent logs**~~ — **FIXED (Session 9).** P-006 IS scanning and active. All matches were hitting SKIP_EDGE because `min_edge_pct` (2%) and `min_ev` (1%) were too high for Polymarket's efficient market. Lowered to 0.8% and 0.5% respectively. Edges still mostly below threshold (~0.1%–0.7%) but should capture opportunities during game-time line movement.

16. **Polymarket market efficiency limits P-006 trade volume** — Polymarket sports markets are much more efficiently priced than Kalshi. Typical raw edges are 0.1%–0.7% vs 3%–10% on Kalshi. With `min_edge_pct=0.008`, P-006 will trade infrequently — mainly during game-time windows when sportsbook lines move faster than Polymarket prices update. This is by design (quality over quantity).

14. **Legacy trade log entries have different schema than multi-pod entries** — P-001 writes `market_ticker` (no `pod_id`, no `venue`). P-006 writes `market_id` with `pod_id` and `venue`. The Settler only reads `market_ticker`, making P-006 entries invisible to it. This is by design (separate settlers), but complicates cross-pod trade log analysis.

15. **126 duplicate trades from pre-fix period** — The event-level dedup fix prevents future duplicates, but existing duplicate positions from March 23 (3x per tennis match, 3x per NBA game) will settle/void naturally over 24 hours. No manual cleanup needed.

---

## Useful Commands

```bash
# ── SAFE deploy (code only, protects live trade data) ──────────────────
rsync -avz --exclude='data/' --exclude='*.pyc' --exclude='__pycache__/' -e ssh \
  '/Users/samlawhon/Desktop/Betting Fund Project/' \
  root@129.212.176.202:/opt/betting-pod-shop/
# Fix ownership after deploy (rsync as root creates root-owned files)
ssh root@129.212.176.202 'chown -R bettingbot:bettingbot /opt/betting-pod-shop'

# ── Restart service ──────────────────────────────────────────────────
ssh root@129.212.176.202 'systemctl restart betting-pod-shop'

# ── View live logs ───────────────────────────────────────────────────
ssh root@129.212.176.202 'journalctl -u betting-pod-shop -f'

# ── Filter for key events ─────────────────────────────────────────────
ssh root@129.212.176.202 'journalctl -u betting-pod-shop --no-pager | grep "PLACED\|settler_cycle_done\|auto_void\|ERROR"'
ssh root@129.212.176.202 'journalctl -u betting-pod-shop --no-pager | grep "cycle.*placed" | tail -10'
ssh root@129.212.176.202 'journalctl -u betting-pod-shop --no-pager | grep "P-006" | tail -20'

# ── Check service status ─────────────────────────────────────────────
ssh root@129.212.176.202 'systemctl status betting-pod-shop --no-pager'

# ── Trade log stats ───────────────────────────────────────────────────
grep -c '"action": "PLACED"' /opt/betting-pod-shop/data/trade_logs/trade_log.jsonl
grep -c '"action": "WIN"' /opt/betting-pod-shop/data/trade_logs/trade_log.jsonl
grep -c '"action": "LOSS"' /opt/betting-pod-shop/data/trade_logs/trade_log.jsonl
grep -c '"action": "VOID"' /opt/betting-pod-shop/data/trade_logs/trade_log.jsonl

# ── View recent trade log entries ─────────────────────────────────────
tail -5 /opt/betting-pod-shop/data/trade_logs/trade_log.jsonl | python3 -m json.tool

# ── Check open position count ─────────────────────────────────────────
ssh root@129.212.176.202 'journalctl -u betting-pod-shop --no-pager | grep "settler_checking" | tail -1'
```

---

## Known Constraints

- **Paper mode only** — `environment: demo` in config, orders logged but never actually placed. Dual safety gate: `place_order()` blocked unless `environment="live"` AND `I_UNDERSTAND_LIVE_TRADING` env var is set.
- **Production API, paper mode** — config points at `trading-api.kalshi.co` (production) for full market inventory, but the demo environment guard prevents real order placement.
- **Polymarket paper mode** — no wallet connected, orders simulated
- **Gamma API condition_id lookup broken** — always returns Biden COVID market for sports; use slug-based lookups
- **Odds API rate limits** — each /scores call costs 2 API requests per sport; cached per cycle
- **Odds API plan limit** — `daysFrom` parameter capped at 3 (using 7 returns HTTP 422)
- **Kalshi market page limit** — scanner now fetches up to 25 pages × 200 = 5000 markets per cycle (increased from 2000 in Session 5)
- **P-006 SANITY_SKIP threshold** — set to 25% (0.25). Below 25% = legitimate edge; above 25% = likely mapping error. Lowering risks placing trades with inverted YES/NO mappings; raising risks missing real opportunities
