# 04 — Opportunity Ranking

Scores 1–5 (5 best). **Composite = (Edge × Durability × Capacity)^(1/3) × 0.6 + (Data + Settlement + Competition)/3 × 0.25 − BuildCost×0.15**, i.e., geometric core on the three things that make a business (you can't average your way past a zero), quality adjustments second-order, build cost a penalty. Judgments below each score; flagged `[estimated]` where the input data is thin.

| Family | Edge | Durability | Capacity | Build cost (5=cheap) | Data avail | Settlement risk (5=clean) | Competition (5=empty) | **Composite** |
|---|---|---|---|---|---|---|---|---|
| **sports_props (MLB totals + longshots)** | 5 | 3 | 3 | 3 | 4 | 5 | 4 | **3.9** |
| **weather_temp** | 4 | 4 | 2 | 4 | 5 | 4 | 3 | **3.5** |
| **sports_headline (maker vs Poly/books)** | 2 | 4 | 5 | 3 | 4 | 5 | 2 | **3.3** |
| **crypto_hourly/15m (maker)** | 2 | 3 | 4 | 2 | 4 | 5 | 2 | **2.9** |
| fed_rates (front meeting only) | 4? | 2 | 2 | 4 | 5 | 4 | 3 | 2.9 |
| rotten_tomatoes | 3? | 3 | 2 | 4 | 3 | 3 | 4 | 2.8 |
| mentions | 3? | 3 | 2 | 3 | 2 | 2 | 4 | 2.5 |
| elections (long-dated) | 3 | 4 | 4 | 2 | 3 | 3 | 2 | 2.5* |
| sports_futures | 2 | 3 | 3 | 2 | 4 | 5 | 2 | 2.4 |
| forex_metals | 3? | 2 | 2 | 3 | 5 | 5 | 3 | 2.4 |
| index_yearly | 2 | 3 | 2 | 4 | 5 | 5 | 3 | 2.3 |
| inflation / labor_econ | 3 | 3 | 1 | 4 | 5 | 4 | 3 | 1.9 |
| index_intraday | — | — | 1 | 5 | 5 | 5 | 5 | dead |
| MVE parlays | — | — | — | — | — | — | — | inaccessible |

\* elections composite penalized for return-on-collateral: months-to-settlement at cash collateral makes even real edge annualize poorly.

## Verdicts

**sports_props — build first.** The only family where this session *measured* a large net-of-fee edge on executable quotes: MLB totals in-play favorites realized 41% vs 80¢ priced (fade nets ~+60%/trade, t=3.2), plus cheap-longshot underpricing across every prop series tested (+1.6 to +4.9pts). Mechanism is coherent: parlay-driven retail flow leans on favorite legs and sells lottery legs; prop books have fewer professional quoters than headline books. Durability 3 (retail flow persists; the specific totals dislocation may compress). Capacity 3 (real but bounded; ~$40k+ top-3 ask depth on top events, $600 median). Kill-tests in 03 must pass first.

**weather_temp — build second.** Classic longshot bias, verified (sell 1–10¢ brackets: +0.8%/day on collateral, t=1.9, and buying them loses −55%). Free input data (GEFS/ECMWF ensembles) vs. a retail crowd reading consumer forecasts; NWS CLI settlement is clean and mechanical. Cheap build (~days: ensemble → station bias correction → bracket probabilities). The binder is capacity: ~$30–100k/day family volume, ask-heavy books, and you must post (not cross) to earn the fee asymmetry. A disciplined maker in 15 cities is a solid small book — tens of $k/week, not a fund. Durability 4: retail lottery demand is structural.

**sports_headline as a maker platform — build third, as infrastructure.** No exploitable calibration error net of fees (the market is good), but: deepest books, maker fee 0.44¢, and free external fair values (Polymarket CLOB, sharp sportsbook lines) inside a ±1.7¢ taker corridor. The edge is microstructure, not forecasting: quote both sides around external mid, harvest retail crossing. Unproven fill economics (H2 falsification plan in 02) — but it shares 90% of its infrastructure with the props strategy, which is why it's third and not lower.

**crypto high-frequency — conditional fourth.** Perfectly calibrated at T-1h (no model edge), enormous settlement count (600k+/90d), tight spreads, real turnover. Pure maker/latency game against the exchange's own quoters and other bots; competition 2. Only attack it if the sports maker infrastructure works and you want more surface for it. 15-min calibration at T-10min remains unmeasured (roadmap pull).

**fed_rates — watchlist, not a build.** The July hike leg (5–6¢ vs 13.3% FF-implied `[estimated, stale source]`) is the one live RV of size ($65k+ available) — but it's a *trade*, not a family: away from the front meeting the books are $900 deep and the internal 9-pt KXFED/KXFEDDECISION gap is undeployable. Verify on Bloomberg, take the trade if it holds, don't build a system.

**Do-not-build (details in 05):** index_intraday (dead product), inflation/labor (no depth), MVE parlays (no entry point), elections (collateral drag + 16-month duration + political tail risk on settlement), forex_metals (suggestive miscalibration but n≈70 and thin books), mentions (rules-lawyer settlement risk on transcript interpretation), sports_futures (one-sided MM inventory books; your fill is adverse selection by construction).
