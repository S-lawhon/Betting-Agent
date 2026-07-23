# P-019 Longshot Maker — Calibration Gate REPORT
*2026-07-22. Step-1 go/no-go from [`backtest_longshot.py`](backtest_longshot.py) on live Kalshi settled data (365-day lookback, pulled 2026-07-22).*

## Verdict: **NO-GO**

The favorite-longshot bias P-019 is designed to harvest **does not exist as a harvestable maker edge in our target universe.** Kalshi's politics + sports-futures markets are well-calibrated across the price range. Both proposed legs are refuted:

- **Longshot leg (sell YES 3–10¢):** the `0.03–0.10` band is *calibrated* — realized 6.6% vs implied 5.8%, CI [5.0%, 8.2%] contains the implied price. Realized sits **above** implied, so selling this band would break even at best and lose at worst. No sell-side edge.
- **Favorites leg (buy YES 85–95¢):** the `0.80–0.97` bands are *calibrated* — if anything favorites are mildly *over*priced (0.80–0.90: realized 80.9% vs implied 84.8%), the **opposite** of the FLB's "favorites underpriced" prediction. No buy-side edge.

This is a data-driven kill, exactly what step 1 exists to produce. **No engine, risk extension, or systemd unit was built** (build order stops here per §10 / §9 "kill if flat").

## Method
Settled Kalshi contracts in the P-019 universe (**all politics** + an **audited keep-list of sports season-futures / top-N / awards / drafts / season-standings** families, all zero-maker-fee `quadratic` series), closing within the last **365 days**. Reference "posted" price = **bid/ask mid at market mid-life** (halfway between first candle and close) — the price a maker would rest a quote at while the market is still genuinely uncertain. Rejected alternatives: lifetime VWAP (contaminated by convergence-to-resolution drift), fixed T-7d horizon (unavailable on shorter markets). Only contracts with cumulative candle volume ≥ 20 kept (a mid on an untraded book is a stale quote, not a market price). Realized-rate CIs bootstrap **whole events** (contracts within an event correlate).

- **29,476** settled binary contracts pulled across **2,595** series; **19,389** in the audited universe (646 events); **5,192** usable after volume + priced filters (1,330 politics, 3,862 sports-futures).
- **Universe hygiene mattered.** A first fuzzy keyword pass was swamped by short-horizon **per-set tennis** (`KXATPSETWINNER` 4,400 contracts, `KXWTASETWINNER` 3,914) and **per-quarter** WNBA markets — 2-outcome, near-resolution, near-calibrated markets that are the *opposite* of the FLB target. These are explicitly excluded; sports inclusion is an audited prefix keep-list (see `SPORTS_FUTURES_SERIES` in the script), not a keyword heuristic.

## Price-decile calibration (reference price = mid-life mid)
FLB present ⟺ realized YES **< implied** at low prices (longshots overpriced) and **> implied** at high prices (favorites underpriced). `edge` = realized − implied (positive ⇒ a BUYER of the band profits).

| price band | n | events | mean price (implied) | realized YES | 95% CI (event-clustered) | edge (real−impl) |
|---|---|---|---|---|---|---|
| 0.00–0.03 | 658 | 209 | 0.015 | 0.003 | [0.000, 0.008] | **−0.012** |
| 0.03–0.10 | 1201 | 359 | 0.058 | 0.066 | [0.050, 0.082] | +0.008 |
| 0.10–0.20 | 839 | 311 | 0.142 | 0.132 | [0.110, 0.155] | −0.010 |
| 0.20–0.35 | 737 | 299 | 0.264 | 0.278 | [0.244, 0.313] | +0.014 |
| 0.35–0.50 | 630 | 239 | 0.429 | 0.451 | [0.397, 0.500] | +0.022 |
| 0.50–0.65 | 423 | 189 | 0.561 | 0.575 | [0.526, 0.628] | +0.013 |
| 0.65–0.80 | 262 | 137 | 0.724 | 0.699 | [0.641, 0.754] | −0.025 |
| 0.80–0.90 | 183 | 105 | 0.848 | 0.809 | [0.735, 0.880] | −0.040 |
| 0.90–0.97 | 130 | 83 | 0.935 | 0.939 | [0.871, 0.986] | +0.003 |
| 0.97–1.00 | 129 | 62 | 0.986 | 0.969 | [0.933, 0.993] | −0.017 |

Every band's CI contains its implied price **except `0.00–0.03`**: realized 0.3% vs implied 1.5%, CI upper bound 0.8% < 1.5%. That extreme tail is the *only* statistically significant mispricing anywhere in the book.

## Robustness — three reference prices, four key bands
Significance = does the realized-rate CI **exclude** the implied price. `SIG` = mispriced; `ns` = calibrated.

| band | px_open (early / long-horizon) | px_midlife (primary) | px_t1d (near close) |
|---|---|---|---|
| 0.00–0.03 | impl .015 real .009 — **ns** | impl .015 real .003 — **SIG** | impl .013 real .003 — **SIG** |
| 0.03–0.10 *(sell target)* | impl .060 real .066 — ns | impl .058 real .066 — ns | impl .057 real .052 — ns |
| 0.80–0.90 *(buy target)* | impl .845 real .831 — ns | impl .848 real .809 — ns | impl .847 real .851 — ns |
| 0.90–0.97 | impl .932 real .863 — ns | impl .935 real .939 — ns | impl .937 real .898 — ns |

Two things kill the thesis:

1. **P-019's own bands are calibrated under every reference price.** The `0.03–0.10` sell target and the `0.80–0.90` buy target never reach significance — there is no edge to harvest in the bands the spec proposes to quote.
2. **The long-horizon test fails hardest.** P-019's core claim is that the bias is *worst* at long horizons where attention hasn't arrived (§1). But at `px_open` — the earliest, most-uncertain, longest-horizon price — even the 0–3¢ tail is *not* significant (CI [0, 3.2%] contains implied 1.5%). The only significant tail overpricing appears at `px_t1d`, ~1 day before resolution. That is the well-documented **dying-longshot** effect (a 2¢ contract that mostly resolves NO as the clock runs out), **not** a durable long-horizon inattention edge — and by construction a long-horizon resting maker book cannot capture a T-1-day-only effect. The single real bias in the data points in the direction that P-019's design specifically cannot trade.

## Even the one real signal isn't harvestable
The 0–3¢ tail is genuinely overpriced (~+1.2¢/contract gross, maker fee zero). But:
- It sits **outside** P-019's `0.03–0.10` band, and it is the classic pennies-in-front-of-a-steamroller trade the spec's own §4 flags: selling a ~1.5¢ YES collects 1.5¢ against ~98.5¢ downside (**~65:1**, worse than the 19:1 the spec modeled for 5¢).
- ~1.2¢ gross edge is a fraction of the ≥5¢ the thesis assumed, and it must survive adverse selection (you fill when the longshot is live), thin tail liquidity, and the mandatory 0.5%/event + 5%/cluster caps that (correctly) throttle exactly this trade. Net of those, expected return rounds to noise.

## Decision & recommendation
**Kill P-019.** The founding premise — a favorite-longshot bias harvestable by a long-horizon maker — is refuted in our universe on 5,192 event-clustered settled contracts. This matches the house pattern (P-016 premise refuted; the 13-month tennis-favorite edge evaporated): Kalshi's liquid multi-outcome markets are more calibrated than the imported academic FLB literature assumes.

Do **not** build the engine, the `AggregateRiskGuard` cluster extension, the runner, or the systemd unit on this premise. The cluster-correlation risk dimension (§4.2) is the one genuinely reusable idea and can be revisited independently if a *different* pod ever needs it — but nothing here justifies standing up P-019.

## Caveats (honest boundaries of this result)
- **Sample skew.** The marquee long-horizon markets P-019 most wants (elections, season champions, award futures) are disproportionately **still open** — `KXNBACHAMP`, `KXSECAGRO`, etc. returned zero settled markets. The settled sample therefore leans toward shorter-cycle resolved questions and multi-outcome sports fields. It is possible a bias lurks in the long-horizon marquee books that have not yet settled; this study cannot see them. But the burden of proof is on the thesis, the `px_open` long-horizon slice we *can* see disconfirms it, and "the edge is only in the data we can't measure yet" is not a basis to deploy capital.
- **Mid, not fill.** The reference price is a mid, not an executed maker fill net of adverse selection. That makes this a test of whether the *bias* exists (it doesn't), which is strictly upstream of whether a maker edge would be *achievable* (it wouldn't, on a calibrated book).

## Reproduce
```
cd longshot_research
python3 backtest_longshot.py --lookback-days 365 --per-event-cap 25 --rate 0.12
python3 backtest_longshot.py --calibrate-only --price-field px_open   # robustness
```
Artifacts: this REPORT, [`p019_params.json`](p019_params.json) (verdict + full band table), cached raw data under `data/` (`settled_markets.jsonl`, `market_prices.jsonl`).
