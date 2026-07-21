# 01 — Kalshi Universe Survey

**Snapshot:** 2026-07-18 (Fri), ~18:00–19:00 ET, via public trade API v2 (`api.elections.kalshi.com`). All figures `[verified]` from live pulls unless flagged. Raw pulls cached under `data/raw/`; reproduce with `src/pull_universe2.py`.

**Seasonality caveat:** this snapshot lands in the week of the 2026 Men's World Cup final and mid-MLB season. World Cup series inflate sports volume materially; treat sports 24h numbers as near a seasonal high-water mark.

## Universe scale

| Metric | Value |
|---|---|
| Listed series | 11,999 |
| Series with ≥1 open market | 2,940 |
| Open non-parlay markets | 68,561 |
| Auto-generated parlay (MVE) markets | ~358k created in a **90-minute window** [verified]; steady-state population not enumerable at reasonable cost |

Kalshi machine-generates parlay ("multivariate event", MVE) markets at ~4,000/minute. In the sampled 90-min creation cohort, only 13% ever traded. MVE markets are one-sided (exchange-quoted parlays, `price_level_structure: deci_cent`), have no public resting book to make into, and are excluded from the tradable-universe analysis below. MVE aggregate flow is real but structurally inaccessible to a maker/arb strategy — you can only be the retail side of it.

## Fee schedule (verified from kalshi.com/docs/kalshi-fee-schedule.pdf, effective 2026-07-07)

- **Taker:** `roundup(M × 0.07 × C × P × (1−P))` — max 1.75¢/contract at P=50¢; fee+cost rounds up to a centicent.
- **Maker:** `roundup(M × 0.0175 × C × P × (1−P))` with **default M=0** — resting orders are **free** on the general universe. ~75 listed series (all major economics, major-sport games/championships, index yearly ranges, tennis, awards) carry maker M=1 (≈0.44¢ at 50¢).
- 10 novelty series (KXBTCY, KXETHY, KXGREENLAND, …) are **entirely fee-free** (taker M=0).
- No settlement fee. Perps have a separate bps tier schedule (12bp taker at tier 0).
- Fee asymmetry is the single most important structural fact in this survey: **taker pays 3.5¢ round-trip at mid-prices; maker pays 0 on most of the board.** Any strategy that can rest orders starts 2–3.5¢/contract ahead of one that crosses.

## Family-level liquidity & structure table

Volume/OI in contract-units (≈$ notional ceiling of $1/contract). Spread/depth from a stratified 1,375-market orderbook sample (top-40 by 24h volume + 15 random per family) — i.e., these describe each family's *liquid head*, not its median market. `depth3` = $ at top 3 levels.

| Family | Open mkts | Series | 24h vol | OI | Med spread (top mkts) | Med bid depth3 | Med ask depth3 | Typical duration | Settlement source |
|---|---|---|---|---|---|---|---|---|---|
| sports_futures | 6,276 | 108 | 75.8M | 485M | 0.3¢ | $219 | $479k† | months | league official |
| sports_props | 1,955 | 36 | 38.0M | 76M | 1¢ | $600 | $42k† | days–2wk | league official |
| sports_headline | 706 | 20 | 22.4M | 21M | 1¢ | $7.1k | $18.8k | days–2wk | league official |
| sports_other | 22,744 | 607 | 11.1M | 37M | 2¢ | $513 | $1.6k | weeks | varies |
| elections | 10,530 | 587 | 4.2M | 256M | 1¢ | $792 | $8.1k | ~16 mo | AP/official |
| politics | 1,708 | 391 | 2.2M | 44M | 1¢ | $197 | $1.0k | months | varies/rules-heavy |
| weather_temp | 489 | 41 | 1.8M | 1.2M | 2¢ | **$0**‡ | $13.3k‡ | 1 day | NWS CLI reports |
| crypto_hourly | 1,422 | 5 | 1.7M | 1.0M | 2¢ | $2.4k | $347 | hours | CF Benchmarks RTI |
| mentions | 899 | 33 | 1.7M | 4.1M | 2¢ | $34 | $575 | days | rules-heavy |
| entertainment | 7,027 | 308 | 1.5M | 15M | 1¢ | $56 | $451 | months | varies |
| rotten_tomatoes | 325 | 7 | 1.4M | 3.8M | 2¢ | $433 | $293 | ~6wk | RT site at fixed time |
| crypto_15m | 5 | 5 | 0.5M | 0.4M | ~1¢* | n/a* | n/a* | 15 min | CF Benchmarks RTI |
| tech_science | 744 | 110 | 0.5M | 22M | 1¢ | $106 | $576 | months | varies |
| econ_other | 2,534 | 205 | 0.5M | 7.1M | 2¢ | $166 | $213 | months | govt data |
| fed_rates | 336 | 16 | 0.4M | 26M | 1¢ | $55 | $1.0k | months | FOMC statement |
| financials_other | 2,938 | 261 | 0.4M | 7.9M | 1¢ | $98 | $529 | months | varies |
| crypto_daily_range | 111 | 18 | 0.2M | 6.5M | 1¢ | $155 | $3.2k | 2wk | CF Benchmarks |
| forex_metals | 620 | 31 | 0.2M | 3.9M | 1¢ | $463 | $269 | 2wk | standard fixes |
| weather_other | 435 | 43 | 0.1M | 1.1M | 3¢ | $27 | $272 | months | NOAA/NHC |
| commodities | 354 | 15 | 0.06M | 3.1M | 2¢ | $385 | $210 | days | EIA/official |
| inflation | 266 | 7 | 0.03M | 0.5M | 2¢ | $13 | $164 | ~2mo | BLS CPI release |
| labor_econ | 413 | 10 | 0.01M | 1.6M | 2¢ | $238 | $278 | months | BLS/DOL |
| index_yearly | 57 | 2 | 0.01M | 6.1M | 1¢ | $73 | $7.5k | ~6mo | index close |
| index_intraday | 3,230 | 2 | **2.6k** | 2.6k | **28¢** | $153 | $223 | 1 day | index level |
| companies | 257 | 28 | 2.8k | 0.4M | 6¢ | $104 | $166 | ~20mo | varies |

† Grossly asymmetric books in futures/props: enormous resting offers (often market-maker inventory on longshots), thin bids. Effective *exit* capacity is the bid side.
‡ Weather books are one-sided at sample time (evening — after the day's high is largely realized). Intraday depth needs a morning sample before concluding capacity.
\* 15-min crypto markets cycle too fast for an EOD book snapshot; their traded volume (~0.5M units/day across 5 coins) is the better liquidity measure.

## Capacity filter — first cut

**Real capacity (≥$1M/wk deployable plausible):** sports_headline, sports_props (World-Cup-skewed), sports_futures (OI-rich but one-sided books), elections (huge OI, 16-month duration), crypto_hourly/15m (high turnover, small size per event but ~30k settlements/yr).

**Marginal ($100k–$1M/wk):** weather_temp (needs intraday depth check), rotten_tomatoes, mentions, politics, crypto_daily_range, forex_metals.

**Low-capacity regardless of edge (flagged per methodology):** inflation (!), labor_econ, fed_rates (**surprisingly dead on Kalshi despite being its flagship** — $55 median bid depth, $0.4M/day), index_intraday (dead: 28¢ spreads), companies, weather_other, index_yearly.

The macro/rates families that a rates-literate trader would naturally target are, on current form, **too thin to be a business** on Kalshi — they are calibration exercises, not P&L lines, unless depth returns around FOMC/CPI event windows (event-window depth sampling is listed in the roadmap as a required follow-up pull).
