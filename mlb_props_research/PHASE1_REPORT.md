# Phase 1 — MLB Props Measurement Studies: Results & Go/No-Go

**Date:** 2026-07-20 (data collected 2026-07-19)
**Scope:** The four open questions from `research/kalshi-mlb-props-efficiency-research.md`, answered with primary data.
**Data:** 259,765 settled Kalshi MLB markets (90d) · 18.8M trade prints on 46,871 markets (45d) · 6,329 MLB game logs (2024–2026) · 127 live order-book snapshot cycles across a full Sunday slate (~4,100 markets each, 550 full-depth books).
**Code:** everything in `mlb_props_research/` (collector, pullers, five analysis scripts — all rerunnable).

---

## Verdict: GO — but the plan inverts the original ranking

The satellite book is viable, with three changes to the July 18 design:

1. **Batter props (hits, total bases, HRR) and team totals are the maker edge — not strikeouts.** Pregame makers *earned* +2.9 to +4.0¢/contract in batter props and team totals, while pregame makers in strikeout props *lost* −1.4¢/contract. K props are where the informed flow lives (every public props model prices pitcher Ks); batter-prop taker flow is retail buying YES at the ask. The original plan's "KS first" pick — chosen for liquidity — targets the one prop pool that's sharp.
2. **The capacity constraint is softer than the July 18 snapshot suggested.** Prop books converge hard into first pitch: by T−1h, hits/TB/HRR/KS books are 1–4¢ wide with 1,000–6,700 contracts of median top-of-book depth. The 12–37¢ spreads in the original report were far-from-game-time books (the 12–48h window, where team-total spreads reach 64¢).
3. **The correlation edge is real but smaller than the NFL illustrations, and its exploit is repricing speed, not static arbitrage.** Measured MLB lifts are 1.10–1.54× (vs the +30–33% NFL copula examples). Logical cross-contract arbitrage does not exist at executable size (73 gross violations all day → 7 net-positive, all sub-penny-scale). The tradeable form: when a total book moves, K-prop fairs shift by a computable 2–4¢ and prop books complete the adjustment over ~10 minutes.

---

## Q1 — Same-game correlations (measured, n=6,329 games)

Spearman highlights and joint-vs-independence lifts (95% CI):

| Pair | Lift vs independence |
|---|---|
| ML home × F5 home ahead | **1.58×** [1.53, 1.63] |
| 3+ HR × Over 8.5 | **1.54×** [1.48, 1.60] |
| YRFI × Over | 1.24× [1.19, 1.28] |
| Starter Ks over × game Under | **1.10–1.24×** (all strike combos significant) |
| ML home × home SP Ks over | 1.17× [1.12, 1.22] |
| Both starters Ks over | 0.98× — independent |
| F5 leader × Under | 0.96× — independent |

Working example: P(starter >5.5 Ks) = 39.6% unconditional → **46.1% given Under 8.5, 32.8% given Over** (~13pp conditional swing). A total-book move of 15¢ implies a 2–4¢ K-prop fair move.

**Cross-contract consistency (live scan, 127 cycles):** ladder monotonicity, exhaustive-set pricing (F5 home/away/tie; ML pairs), and implication constraints (win-by-N ⇒ win; team ≥N ⇒ total ≥N) are respected wherever books are quoted. 7 net-positive violations all day, at 0.3–0.8¢ on 0.4–2 contracts — dust. **No free money; the edge must come from statistical repricing, not logic.**

**Repricing lag (exploratory):** regressing prop-mid changes on copula-predicted changes after ≥2¢ total-book moves: beta ≈ +0.63 at lag 0, +0.93 at lag 2 cycles (~10 min), ≈0 after. Direction and timing support the lag-trading thesis; per-event r is small (~0.03–0.05) at 5-min sampling. **Needs websocket-grade data before sizing.**

## Q2 — Calibration of pregame prices (45d of trades)

Method note: `last_price` on settled markets is settlement-contaminated (one-directional in-game flow strands illiquid losers at mid prices) — all calibration below uses the *last pregame trade* per market, cut at the scheduled start time parsed from the ticker.

Per-series bias (hit rate − price, pregame):

| Series | Bias | Significance |
|---|---|---|
| Hits | **+4.9pp** | ~7σ |
| Total bases | +2.4pp | ~3.7σ |
| HRR | +1.7pp | ~2.5σ |
| Home runs | +1.4pp | ~3.5σ |
| Strikeouts | +0.2pp | flat — efficiently priced |
| Team totals | −1.3pp | ~2σ |
| Game totals | −3.7pp | ~2.8σ |
| Moneyline | −0.6pp | flat (baseline) |

Pooled props show a **reverse favorite-longshot bias**: cheap YES strikes underpriced by +2 to +3.8pp (10–50¢ buckets), rich YES overpriced by −1 to −1.7pp (60–90¢). The gross taker edge on cheap props is roughly fee-sized — a filter for a model, not a standalone strategy. (Team-derivative favorites >90¢ show −15pp, but that bucket is thin, stale-last-trade-prone, and contradicted by the maker data; treat as artifact.)

## Q3 — Maker economics (18M trades; maker P&L = counterparty of every taker fill; prop maker fees = $0)

| Series | Pregame ¢/contract | Live ¢/contract | Pregame maker pool ($/day) |
|---|---|---|---|
| Team totals | **+4.0** | +1.7 | ~$9,400 |
| Total bases | **+3.8** | +0.4 | ~$3,500 |
| Hits | **+2.9** | −1.9 | ~$3,100 |
| HRR | **+2.9** | +0.3 | ~$3,400 |
| Stolen bases | +5.3 (thin) | +0.7 | ~$180 |
| Home runs | +0.7 | +0.9 | ~$5,900 |
| First-inning run | +0.1 | +1.5 | — |
| **Strikeouts** | **−1.4** | +0.9 | negative pregame |
| Moneyline / totals (baselines, gross of maker fee) | +0.8 / −0.2 | +0.5 / +0.5 | — |

Read: the *average* resting order in batter props and team totals beats the flow that hits it, by multiples of the taker fee, with zero maker fees. In strikeouts, resting orders get adversely selected pregame. These are averages over all makers — a quoter with fair-anchored prices and news-pull discipline should beat the average; one who quotes stale should do worse.

Capacity (median pregame contracts traded per market): HR ~2,600 · KS ~790 · HRR ~510 · hits ~430 · TB ~350 · team totals ~320 · **RFI ~82,000** (RFI is a hyper-liquid retail favorite — $146M volume/90d on 852 markets — and prices near-flat: a quasi-mainline market suited to the CLV pipeline, not a softness play).

## Q4 — Liquidity ramp (127 cycles, full Sunday slate)

Median spread / top-of-book depth by time to first pitch:

| Series | 24–48h | 12–24h | 2–4h | 0–1h |
|---|---|---|---|---|
| Hits | 2¢ / 199 | 3¢ / 391 | 3¢ / 1,095 | **2¢ / 1,441** |
| Total bases | 4¢ / 107 | 3¢ / 300 | 3¢ / 762 | **3¢ / 1,285** |
| HRR | 4¢ / 539 | 3¢ / 1,035 | 3¢ / 625 | **4¢ / 1,084** |
| Strikeouts | 5¢ / 450 | 3¢ / 404 | 1¢ / 827 | **2¢ / 1,025** |
| Home runs | 3¢ / 1,542 | 1¢ / 1,564 | 1¢ / 3,386 | **1¢ / 6,706** |
| Team totals | **64¢ / 86** | 4¢ / 134 | 2¢ / 834 | **1¢ / 2,274** |

Two regimes: a thin, wide **early window (12–48h)** where quoting earns big spreads but bears overnight/lineup news risk, and a tight, deep **final ramp (0–4h)** where execution is cheap but competition (a programmatic quoter clearly switches on) compresses edge. Lineup announcements (~2–5h pre-pitch) sit at the boundary — that's when batter-prop fairs move most and stale quotes get picked off.

---

## Recommended Phase 2 — the satellite book, reshaped

1. **Paper maker pod for batter props + team totals** (integrates with betting-pod-shop): quote hits/TB/HRR/team-total ladders inside the observed spread in the 2–24h window at empirical-fair-anchored prices; hard-pull all quotes on lineup releases, scratches, and weather flags; re-quote after. Target: beat the +2.9–4.0¢/contract average maker take. Realistic scale at current volumes: low hundreds of $/day paper P&L (a few % of the total maker pool), scaling with Kalshi prop growth.
2. **Correlation overlay:** inventory acquired via maker fills hedged with the deep total/ML books using the measured lift matrix; total-move-triggered repricing of prop quotes (the 2–4¢ conditional adjustment, currently completed by the market over ~10 min).
3. **Strikeouts: model-first or not at all.** Only quote/take KS with the fund's own pitcher model (shares inputs with the moneyline model); never rest naive KS quotes pregame.
4. **RFI to the CLV pipeline** as a third mainline-grade market alongside moneyline/totals.
5. **Data upgrade before sizing the lag edge:** 1-minute or websocket book capture for a two-week window.

## Caveats

Single Sunday for the ramp study (day-game-heavy; weekday-night profiles may differ). 45-day trades window, one season regime — Winkelmann-style single-sample caution applies; the maker-edge numbers need a second non-overlapping window before real money. Maker P&L is the average over all resting orders (composition unknown; includes sophisticated quoters). Trades universe excludes markets with volume <20 (the softest, least capturable books). Live-phase maker P&L ignores the latency/infrastructure needed to actually run in-game quotes. Kalshi could extend designated market-making or maker fees to prop series at any time, which would compress this edge quickly.
