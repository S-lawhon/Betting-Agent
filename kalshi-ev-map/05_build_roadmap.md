# 05 — Build Roadmap (the deliverable)

Sequencing principle: build the thing with measured edge first, reuse its plumbing for everything after, and never build a model for a family whose book can't pay for the build.

---

## Build 1 — MLB props engine (totals first) — **start now**

**Why first:** the only *measured, net-of-fee, statistically defensible* edge in this survey (03): in-play MLB totals favorites realized 41% vs 80¢ priced; cheap prop longshots underpriced across every series tested. Year-round liquidity (World Cup flow disappears in two weeks; MLB runs through October, then NBA/NFL props use the same engine). Aligns with the existing Kalshi-sports pivot plan (MLB first, CLV-validated).

**Phase 0 — kill-tests (3–5 days, do before any model):**
1. Early-close selection audit: re-run the totals calibration with horizons anchored to scheduled game end (Statcast/MLB API game clocks), not market close. If the edge halves, halve conviction; if it vanishes, the finding was an artifact and Build 1 becomes the longshot-side only.
2. Fill realism: replay the 90-day trades tape (`src/pull_trades_sample.py` generalizes; trades endpoint supports per-ticker history) against candle quotes — measure achievable size at the printed prices.
3. Refresh the settled pull for Aug–Sep persistence before scaling beyond pilot size.

**Data inputs:** Kalshi candles + trades (free, built); MLB Statcast/park factors/lineups (free, statsapi); market totals lines from a sharp book for fair-value anchor (The Odds API tier, ~$100/mo) — Bloomberg irrelevant here.

**Model:** run-environment simulator (park, lineup, pitcher, weather) → distribution over final totals → bracket probabilities, blended with sharp-book line as prior; in-play state machine (score, inning, base-out) for the live fade.

**Done =** paper CLV positive over 200+ trades AND live pilot ($50/trade) net ROI > +10%/trade over 100 trades.

**Expected economics [estimated]:** +20–60%/trade on ~20¢ collateral, holds of hours, 5–15 trades/day in season → even at the conservative end and $300 average size, ~$1.5–4k/wk P&L on <$25k working collateral; annualized return on collateral in the hundreds of percent *at small size*. Capacity ceiling near ~$30–50k/wk deployed before you are the book.

## Build 2 — Weather maker (2 weeks, parallel-friendly)

**Why:** verified structural retail longshot bias + free professional-grade input data + mechanical settlement + cheap build. It's also the lowest-risk live laboratory for maker infrastructure (Build 3's prerequisite), because books are slow.

**Data inputs:** GEFS/ECMWF ensembles (NOMADS/open-data, free), NWS CLI settlement reports, station history for bias correction. **Required new pull:** morning orderbook depth sampling (books were ask-only in my evening sample — capacity conclusion depends on it).

**Model:** per-station ensemble post-processing (quantile mapping against 5y of CLI highs) → bracket probabilities → post two-sided quotes at fair ± edge, sized to bracket; never cross the spread (maker M=0 → zero fees in this family).

**Done =** 30 days live across ≥10 cities, realized maker ROI > +5%/week on deployed collateral, adverse-selection (post-fill drift) measured < half of captured edge.

**Expected economics [estimated]:** $20–60k deployable, +3–8%/wk on that in season (temperature dispersion is seasonal — verify summer vs winter separately). A solid five-figure annual book, not more.

## Build 3 — Sports maker platform (the scaling bet, 4–6 weeks)

Quote Kalshi headline + prop books around external fair values (Polymarket mid, sharp-book devig) inside the fee corridor. No forecasting edge required — the measured corridor (±1.7¢ taker fee) plus free maker status on most series is the business. This is where capacity 5 lives (sports_headline: $7–19k top-3 depth, $22M/day family volume).

**Gate before building:** 2 weeks of quote capture (websocket) measuring: fill rates at ±1¢ around Poly mid, post-fill adverse selection, effective spread capture. If capture < 1¢/contract after adverse selection, stop at Build 1+2.

**Data inputs:** Kalshi websocket (free), Polymarket CLOB websocket (free), one sharp odds feed.

**Done =** live Sharpe > 2 on 4 weeks of MLB games at $500/market inventory caps.

## Standing watchlist (trades, not builds)

- **Fed front-meeting RV:** verify KXFEDDECISION vs FF futures on Bloomberg (WIRP) — if the 5–6¢ vs 13.3% July gap is real at your quote time, it's a one-ticket +7¢ EV trade with $60k+ of depth. Re-check each meeting; the far complex is untradeable.
- **Post-shock latency:** during Build 3's capture fortnight, log book staleness after goals/data releases; if human-quoted families lag > 30s, add a news-reactive taker module.
- **Crypto 15-min calibration at T-10min** (one candle pull away) — only matters if Build 3 infrastructure succeeds.

## Do-not-build (looked attractive, killed)

| Family | Why killed |
|---|---|
| Index intraday vs options digitals | The natural Bloomberg trade, but the product is dead on Kalshi: $2.6k/day volume, 28¢ spreads. Nothing to trade against. |
| CPI/payrolls/GDP vs consensus & swaps | Real signal edge available, $13–240 book depth. The market isn't there. Revisit if Kalshi's econ liquidity returns. |
| Elections/politics long-dated | Huge OI but 16-month cash-collateral holds crush annualized ROC; settlement/rules tail risk; Polymarket is deeper and sharper on the same events — you'd be the dumb venue's dumb money. |
| MVE parlays | The volume monster (≈4k markets/min created), but exchange-priced with no public book to make into — no entry point for an arb; only the retail side exists. |
| Cross-venue arb (Kalshi↔Polymarket) | Verified fee-bounded: basis lives inside the ±1.7¢ taker corridor. As a *standalone* strategy it's dead; as a fair-value input it powers Build 3. |
| Bracket-sum / monotonicity arb | 5,185 events scanned: zero executable violations; the static book is machine-tight. |
| Mentions / rules-heavy novelty | Settlement is transcript-lawyering; edge accrues to whoever wrote the rules. |
| Sports futures market-making | Books are one-sided MM inventory; passive fills there are pure adverse selection. |

## Infrastructure debt to pay early
Rate-limit-aware async client (current one 429s above ~7 req/s), websocket capture daemon, and a settled-market archiver on cron — the 90-day API history horizon means **every week you don't archive is calibration data lost forever**.
