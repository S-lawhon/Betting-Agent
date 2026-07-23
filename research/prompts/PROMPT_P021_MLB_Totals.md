# Claude Code Task — P-021: MLB Totals/Run-Line vs Sharp-Book Consensus

> Paste this whole file into Claude Code as the task. Full spec lives at
> `research/SPEC_P021_MLB_Totals_Sharp_Consensus.md` — read it first, it is the source of truth.

## Role & context
You are working in the **Betting Pod Shop** repo (`~/Desktop/Betting Fund Project`), a paper-mode Kalshi trading engine. Everything is paper/demo — **no real orders, ever** (`KALSHI_ENVIRONMENT=demo`, no `I_UNDERSTAND_LIVE_TRADING`). Read `CLAUDE.md` and `PROJECT_STATUS.md` for conventions before writing code.

We are extending **P-001** (our one validated edge: Kalshi game moneylines vs a Pinnacle-weighted sportsbook consensus) to Kalshi's **maker-free MLB derivative series**. Confirmed live 2026-07-22: `KXMLBTOTAL` is `quadratic` fee-type → **zero maker fee**.

## Non-negotiable guardrails
- **Backtest FIRST. Do not build a pod until the backtest gate passes.** Our last two ideas were tested this way; one (P-019) was killed at exactly this gate. Expect the same discipline here.
- **This is NOT "our model beats props."** A public box-score model was already tried and failed — it carries no info Kalshi lacks. The reference here is a **sharp book's line** (Pinnacle/consensus), which aggregates sharp money. Do not build a stats model.
- Reuse existing modules; do not reinvent: `Legacy/Kalshi Arb Project/src/scanner.py` + its Odds API client (the P-001 path), `src/devig.py`, `src/kalshi_public.py`, `src/cross_venue_matcher.py`, `src/kalshi_fees.py`, `src/clv.py`.
- **Fees:** always call `fee_per_contract(price, maker=..., series_ticker=...)` WITH the series ticker (these MLB derivative series return maker fee 0 — never hardcode).
- **Kalshi API gotcha:** list endpoints null out bid/ask/volume; use the `orderbook` endpoint / candlesticks per market for prices.
- Cluster all statistics by **game-day**, not by contract (same-day games share pitcher/weather shocks). This clustering discipline is mandatory — it's what makes our kills/promotes honest.

## Phase 1 — Cheapest falsification (DO THIS ALONE FIRST, then STOP and report)
Create `mlb_totals_research/backtest_totals.py` (mirror the harness pattern in `longshot_research/backtest_longshot.py`).

1. Universe: `KXMLBTOTAL` only, ~200 recent **settled** games.
2. For each: pull the **Pinnacle-eu closing total** from The Odds API (`ODDS_API_KEY`; totals are cheap featured markets — note historical props cost 10×, but totals are fine), devig via `src/devig.py` → `p_sharp` per strike. Build BOTH a Pinnacle-eu-only and a no-vig multi-book consensus (EXCLUDE all DFS books — PrizePicks/Underdog `us_dfs` are not sharp).
3. Pull Kalshi price near close (`orderbook` mid / `trades_since` at ~T-1h) → `p_kalshi`.
4. Tests: (i) Brier/log-loss of `p_sharp` vs `p_kalshi` on realized outcomes; (ii) regress realized 0/1 on the gap `(p_sharp − p_kalshi)` with **day-clustered SEs** — a positive, significant gap coefficient means the sharp line has info Kalshi lacks; (iii) simulated net-of-fee PnL taking when `p_sharp − p_ask > fee + margin`, flat-sized; (iv) **CLV vs Kalshi's own close** (the true survival criterion, same as P-001).
5. Write `mlb_totals_research/REPORT_MLB_Totals_2026-07.md` (day-clustered gap coefficient, Brier comparison, net-of-fee PnL, CLV) and persist `mlb_totals_research/p021_params.json`.

**GATE — stop and report the verdict:**
- **KILL** if devigged Pinnacle totals do NOT beat Kalshi on Brier AND the gap coefficient is insignificant. (The softer player props won't rescue it; do not proceed.)
- **ADVANCE** only if totals beat Kalshi with positive, day-clustered CLV.

Do not start Phase 2 until I approve based on the REPORT.

## Phase 2 — Pod build (only after I approve the Phase-1 REPORT)
- `src/pods/mlb_totals_value.py` — `BasePod`, `@register_pod("P-021")`, modeled on `src/pods/kalshi_moneyline.py` (P-001). Discover Odds API MLB events → devig totals/spreads → match to Kalshi via `cross_venue_matcher` → emit `ScanResult` when `net_edge(fair, ask, maker/taker, series_ticker=...) > min`. Conservative shade toward Kalshi (lower-CI convention like P-017). Depth-capped; sized to `aggregate_risk.max_pod_exposure_pct` (0.25).
- Settlement: reuse the Odds API `/scores` settler. CLV via `src/clv.py`.
- `tests/test_mlb_totals.py`: devig correctness; DFS-book exclusion; fee=0 via `series_ticker`; match logic; net-edge gate; taker-vs-maker path.
- Config: add a `P-021` block to `config_multi_pod.yaml` but **leave it OUT of `pods.active`** until forward paper validates.

## Definition of done (Phase 1)
`REPORT_MLB_Totals_2026-07.md` committed with the day-clustered gap coefficient, Brier comparison, net-of-fee PnL, and CLV vs Kalshi close; an explicit KILL-or-ADVANCE verdict; `p021_params.json` written; no pod added to `pods.active`; nothing placed live.
