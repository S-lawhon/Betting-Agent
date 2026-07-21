# REPORT — Golf Top-N Backtest (P-017 validation)

*2026-07-19. Backtest replay of the P-017 golf decision rules through a realistic execution model, on historical Kalshi data pulled today. Companion to `GOLF_KALSHI_RESEARCH.md` (the aggregate-calibration research) — this is the rigor step that runs the **actual per-bet decision logic** with correct series fees and pessimistic fills before any live wiring, in the same spirit as `tennis_research/REPORT.md` → P-015 and `live_agent_research/` → P-016.*

*Code: `backtest/backtest_golf.py` (canonical legs), `backtest/refine_golf.py` (window/band grids), `backtest/golf_fees.py` (series-aware fees + power de-vig). Data: `candles.jsonl` (12,796 prop markets across 10 tournaments), `trades.jsonl` (170k ticks, top-10/20 of 5 events). THOC26/COPC26 excluded — settlement not yet populated on pull date.*

---

## Verdict

| Leg | Rule | Net /contract | 95% CI (event-clustered) | Status |
|---|---|---|---|---|
| **A — pre-tournament cheap-YES** | Buy YES at ask, top-10/20, 8–45¢ band, 4–10 days before close | **+6.8¢** (taker fee paid) | **[+3.1¢, +10.2¢]** | ✅ **Validated** — 9/10 events positive |
| **B — fade maker** | Rest ask (sell YES), top-10/20, 36→6h before close, +3¢ over mid | **+9.1¢** (zero maker fee) | **[+4.5¢, +13.5¢]** | ⚠️ **Promising** — only 4 events of tick data |
| C — make-cut cheap-YES | Buy YES at ask, make-cut, cheap band | — | — | ⛔ Shelved — 12 quotes, insufficient |
| — top-5 | Buy YES at ask, top-5 | +0.5¢ | [−0.7¢, +1.8¢] | ⛔ Rejected — straddles zero |

The two structural legs are **positive without any model input** — pure market-price rules, so the edge (Leg A especially) needs no DataGolf subscription to capture. DataGolf would refine name selection, not create the edge.

## Leg A — the core, validated edge

Buying any top-10/20 contract priced 8–45¢ on the latest two-sided quote 4–10 days before close (the "Wednesday" anchor) returned **+6.8¢/contract net of the Kalshi taker fee**, over 926 bets in 10 tournaments. The bootstrap CI (resampling *tournaments*, since names within an event are correlated) is [+3.1¢, +10.2¢] — comfortably excludes zero. It is robust:

- **9 of 10 tournaments positive.** Range −4.9¢ (Travelers, TRAV26 — a signature event with a strong, heavily-watched field) to +15.6¢ (THCCBN26). The lone negative is a plausible "attention" exception: the softness comes from inattention, and signature events draw sharp attention.
- **Band-insensitive:** every band from 5–25¢ to 15–50¢ returns +6.6¢ to +7.4¢. Not a knife-edge fit.
- **Both series positive:** top-20 +8.3¢ [+3.6, +13.2], top-10 +4.9¢ [+2.3, +7.3]. Top-20 is the stronger book (more slots → more tie-inflation mass, more inattention).

Economic driver (from the research): the tie/dead-heat settlement quirk (top-20 markets averaged 22.7 yes-settlements vs the nominal 20, +13% mass) plus retail attention concentrating on stars leaves the mid-tier structurally cheap pre-tournament. Both are mechanical/behavioral and present on the exchange's own flow.

## Leg B — real but window-critical and underpowered

Resting an offer (selling YES) on top-10/20 names in-tournament, filling **only** on public prints that go strictly *through* the quote (the P-016 pessimistic-maker convention, which bakes in adverse selection), with zero maker fee (prop series are `quadratic`):

- **Window is everything.** 36→6h before close: **+9.1¢/ct [+4.5, +13.5]**. 48→6h: only +1.4¢ (dilutes with still-cheap 2–4d flow). **48→24h: −3.1¢** — fading Friday *loses* because YES is still being bid up. The tradeable window is Saturday through Sunday morning.
- Offset +3¢ over mid beats +1¢ (fills less, but avoids the worst adverse selection).
- **Caveat that caps confidence:** only 4 tournaments have tick data, so even the significant CI rests on 4 events. This is exactly what a P-016-style paper deployment resolves — collect fills live over 8–10 events before trusting it.

## What this changes vs the aggregate research

The aggregate analysis suggested makers could capture ~+8¢ in a "12–48h" window. The realistic replay sharpens this: the naive 48h version is break-even, and the *edge is entirely in the 36→6h window* — the aggregate number was right in magnitude but wrong on timing, and a pod that started fading too early would have bled. This is precisely the realized-vs-modeled shrinkage the literature warned about, caught before it cost anything.

## Recommended P-017 build (params in `p017_params.json`)

1. **Ship Leg A as the primary pod** — a BasePod taker pod, direct analog of `qualifier_favorite_pod` (P-015): discover top-10/20 markets, gate on 4–10-days-to-close + 8–45¢ band + spread ≤6¢, `fair_prob = ask + edge_bump` with a **conservative edge_bump = 0.04** (lower-CI convention) and `min_net_edge = 0.02`, depth-capped, paper mode. Initially skip signature-event weeks (the TRAV26 exception).
2. **Ship Leg B as a paper-only maker engine** mirroring P-016's `LiveMakerEngine`: quote asks at mid+3¢ on top-10/20 in the 36→6h window, pessimistic fills, zero maker fee, markout + settlement logging. Treat its 4-event edge as a hypothesis to confirm live, not a validated number.
3. **Shelve Leg C and top-5/40.**

## Honest limitations

10 tournaments (Leg A) / 4 (Leg B), H1-2026, correlated within-event outcomes — CIs are event-clustered but the event count is the real sample size. Structural edges (tie inflation) should persist; behavioral edges (inattention) can decay as Kalshi's maker ecosystem professionalizes — the paper-first gate and a kill criterion (net edge < half of baseline) guard against trading a dead edge. Leg B's fill model is pessimistic-but-simplified (quote anchored to the nearest prior daily candle mid, not a live book); the live P-016-style engine will be more faithful. Not financial advice; read each series rulebook for limits and eligibility before live trading.

---
---

# EXTENSION — 2026-07-20: THOC26 + COPC26 folded in

*Everything above this line is the ORIGINAL 10-event result and is left unmodified for audit. This section extends it. Data re-pulled 2026-07-20; results in `backtest_results_extended.json`.*

## Why a re-pull was needed

The original `candles.jsonl` / `trades.jsonl` / `settled_markets.json` **no longer existed on disk** (matched by the `*.jsonl` rule in `.gitignore`, never committed). The extension therefore required re-pulling every market from Kalshi, not just the two new events — which turned out to be a benefit, because it makes the original result independently reproducible.

**Reproduction check (the thing that makes the rest of this section trustworthy): the 10-event Leg A reproduced BIT-EXACT from the fresh pull** — n=926, net **+0.0681**, CI **[+0.0306, +0.1018]**, hit 26.9%, gross +0.0784, identical to `backtest_results.json` to 4 decimals. Per-series also reproduced (top-10 +4.88¢ [+2.32,+7.26]; top-20 +8.29¢ [+3.29,+13.17]). Candlestick history is immutable, so the harness and data path are verified faithful. Leg B reproduced only *approximately* (see below).

## Settlement verified populated

Both events are now fully settled with populated `result` (checked via `KalshiPublic`, LIST `status=settled`, confirmed against `get_market`). Nothing was inferred or fabricated:

| event | top-10 | top-20 | `scalar` (withdrew) |
|---|---|---|---|
| THOC26 | 143 no / 13 yes / 9 scalar | 129 no / 27 yes / 9 scalar | 9 per series |
| COPC26 | 134 no / 10 yes / 8 scalar | 121 no / 23 yes / 8 scalar | 8 per series |

**`result="scalar"` is a verified non-issue for Leg A.** The original code books `result == "yes" → 1.0 else 0.0`, which would treat a withdrawal as a full loss rather than a void. Of 134 scalar records across the 12 events, only 5 have a two-sided anchor candle and **zero** clear the full Leg-A gate. The mis-treatment therefore never touched a single bet: the scalar-void sensitivity is numerically identical to the as-coded run. No correction applied, and none needed for this leg.

## Leg A — the headline. Estimate essentially unchanged, CI modestly tighter

Same rule, same parameters, no changes (band 0.08–0.40, spread ≤0.06, 4–10 days, top-10/20, taker fee):

| | events | n | net /contract | 95% CI (event-clustered) | width | positive |
|---|---|---|---|---|---|---|
| **Original** | 10 | 926 | **+6.81¢** | [+3.06¢, +10.18¢] | 7.12¢ | 9/10 |
| **Extended** | 12 | 1117 | **+6.92¢** | [+3.79¢, +9.92¢] | 6.13¢ | **11/12** |

The two new tournaments are both positive: **THOC26 +11.72¢** (n=104) and **COPC26 +2.39¢** (n=87). TRAV26 (−4.87¢) remains the only negative event, consistent with the original "signature events draw sharp attention" reading.

**Be honest about how much this is worth.** The point estimate moved +0.11¢ — noise. The CI narrowed by 14% (7.12¢ → 6.13¢), which is roughly the √(10/12) ≈ 0.91 you would get mechanically from two more clusters even if the new events were statistically average; they were slightly better than average, so the observed narrowing is a hair more than mechanical. **This is confirmation, not new information.** Two events on a base of ten cannot materially change what is known, and it does not on its own satisfy the ~8-live-tournament go/no-go gate, which is about *live* paper fills, not more backtest history.

Secondary cuts, 12 events (all consistent with the original):
- **top-20 +9.15¢** [+4.68, +13.41] (n=631) vs **top-10 +4.04¢** [+1.57, +6.46] (n=486). Top-20 remains the stronger book; the gap widened slightly.
- **Band-insensitive:** +6.62¢ to +7.19¢ across bands 0.05–0.25, 0.08–0.40, 0.08–0.45, 0.10–0.35, 0.15–0.50. All CIs exclude zero. Still not a knife-edge fit.

## Leg B — degrades materially and no longer clears zero

*Numbers below are the CONTRACT-WEIGHTED figures produced by the fixed harness (see "Weighting bug — FIXED" below). The unweighted fill-event figures the original run reported are kept in the second table for audit.*

| | events | net /contract (weighted) | 95% CI (weighted) | fill rate |
|---|---|---|---|---|
| Reproduction | 4 | +5.85¢ | [−4.83, +11.08] | 0.657 |
| **Extended** | **6** | **+3.34¢** | **[−4.25¢, +8.61¢]** | 0.589 |

Superseded, unweighted (one entry per fill event, any size — the pre-2026-07-21 methodology):

| | events | net /contract (as coded) | 95% CI | fill rate |
|---|---|---|---|---|
| Original | 4 | +9.15¢ | [+4.52, +13.49] | 0.675 |
| Reproduction | 4 | +8.62¢ | [+2.91, +13.70] | 0.657 |
| Extended | 6 | +4.90¢ | [−1.69¢, +10.81¢] | 0.589 |

Both new events are negative: **THOC26 −2.07¢**, **COPC26 −8.47¢**. So 3/6 events positive, down from 3/4.

Investigated before reporting, per the "is it signal or artifact?" rule:

1. **Not a settlement artifact** — both events' results are fully populated and Leg A used the same settlement data to produce *positive* numbers for both.
2. **COPC26 is genuinely thin, not a truncated pull.** It has 2,317 trades across 304 markets (7.6/market) against 67–207/market for every other event, and 368 prints in the 36→6h window against 6,922–18,083. It is a low-tier/opposite-field event with real illiquidity; the pull completed with zero errors on the same code path. Its weight is small anyway — 555 of 9,277 contracts (6%). **Most of the degradation comes from THOC26** (1,938 contracts, −2.07¢), which is a major with the *most* trade data in the sample and cannot be dismissed.
3. **Leg B does not reproduce bit-exactly** (+8.62¢ vs +9.15¢ on the same 4 events), unlike Leg A. The trades endpoint is evidently not perfectly stable under re-pagination. Treat Leg B numbers as ±0.5¢ regardless of event count.

### Weighting bug in `leg_fade_maker` — FIXED 2026-07-21

The row labelled `mean_pnl_per_contract` was **not** per-contract. `backtest_golf.py:318` appended one entry to `per_bet` per *through-print fill event* regardless of how many contracts that print filled, and line 355 then **overwrote** the correctly contract-weighted `mean_per_contract` (computed at line 332) with that unweighted fill-event mean. Small fills counted as much as large ones.

**Now fixed.** `per_bet` entries carry their fill size as `(event, pnl_per_contract, contracts)`, and a new `bootstrap_ci_weighted` computes `Σ(pnl·contracts)/Σcontracts` with a cluster bootstrap that still resamples **tournaments** (per the project convention — outcomes within an event correlate). The dead intermediate CI at the old lines 334–336 and the overwrite at 355 are gone. The unweighted figure survives as `mean_pnl_per_fill_event_unweighted` for audit continuity only.

| | as coded (unweighted) | contract-weighted (correct) |
|---|---|---|
| 4 events | +8.62¢ [+2.91, +13.70] | **+5.85¢ [−4.83, +11.08]** |
| 6 events | +4.90¢ [−1.69, +10.81] | **+3.34¢ [−4.25, +8.61]** |

Two independent checks that the fix is right: the weighted means reproduce **exactly** the +5.85¢ / +3.34¢ computed by hand during the 2026-07-20 investigation, and every per-event contract count and net matches that hand computation to 4dp. Leg A is byte-for-byte unchanged (+6.81¢ / +6.92¢, same CIs), confirming the change is contained to Leg B.

**The correction is larger than the "Leg B degraded" finding it sits inside.** Note what the weighted 4-event row shows: on the *original* data, with no new events at all, the correctly weighted CI **already straddled zero** ([−4.83, +11.08]). Leg B's apparent significance was an artifact of the weighting, not something the two new tournaments destroyed. The +9.1¢ that reached `P-017_Golf_Pod_Spec.md` never had a CI excluding zero once size was accounted for.

So the honest read on Leg B is **+3.3¢ contract-weighted over 6 events, CI straddling zero** — well below the +9.1¢ in the spec, and below the maker half-baseline of +4.55¢. This does not kill Leg B, but it removes any claim to being "validated-adjacent" and makes paper collection mandatory rather than confirmatory.

Regression tests pinning the estimator: `tests/test_golf_backtest_weighting.py` (8 tests, including one where the unweighted mean gets the *sign* wrong).

## Scope limits of this re-run

Rate limiting (Kalshi throttles by **concurrency**, not aggregate rate — 12 threads produced constant 429s, a single connection at 0.15s produced none) meant only the series needed for the primary leg were fully re-pulled:

- **Complete:** KXPGATOP10 (1,677), KXPGATOP20 (1,677), and trades for all 6 tick-data events (1,724 markets, 0 errors).
- **Partial / not re-pulled:** KXPGATOP5 (1,051/1,677), KXPGAMAKECUT (0), KXPGATOP40 (0).

Therefore **top-5, top-40 and make-cut are NOT re-stated here** and their original verdicts stand unchanged (rejected / insufficient). The partial top-5 slice is a biased subset (jobs are ordered by event, so partial coverage drops whole tournaments) and must not be quoted.

`analyze_candles.py` was re-run on the new pull (per `GOLF_KALSHI_RESEARCH.md` §4) but covers only the 3 re-pulled series, not the original 11 — so its series table is not comparable to the original. Its pooled calibration (n=3,123, price on the day before settle) shows negative buy-YES edge in every bucket, which is the *late-window* favourite-longshot/hope-premium effect and is consistent with — not contradictory to — Leg A's positive edge 4–10 days out.

## Verdict change

- **Leg A: confidence UP, modestly.** Bit-exact reproduction of the original, stable point estimate, 11/12 events positive, CI tighter. The +6.8¢ baseline is sound; call it **+6.9¢ [+3.8, +9.9]**. The `edge_bump = 0.04` conservative lower-CI convention still holds comfortably.
- **Leg B: confidence DOWN, materially.** +9.1¢ → **+3.3¢ contract-weighted [−4.25, +8.61]**, CI straddles zero, 3/6 events positive. And the weighting fix shows it never cleared zero on the original 4 events either. Keep it paper-only; do not treat +9.1¢ as a live expectation.

## Band drift found — and resolved

The docs (`p017_params.json`, the original report body, `P-017_Golf_Pod_Spec.md`) describe Leg A's band as **8–45¢**, but the backtest that produced the +6.8¢ validation uses **8–40¢** (`band=(0.08, 0.40)`). The **shipped pod and config are 8–45¢** (`golf_topn_pod.py: ask_cap = 0.45`, `config_multi_pod.yaml: ask_cap: 0.45`), so the pod has been running a band that was described as validated but never actually was.

**This extension resolves it favourably.** On 12 events the 8–45¢ band returns **+7.14¢, CI [+4.30¢, +9.90¢]** (n=1,149) — slightly *better* than the 8–40¢ band and with a CI that also excludes zero. The shipped configuration is now validated on its own terms; no code or config change is needed. The residual issue is bookkeeping: the canonical backtest still runs 8–40¢, so future re-runs will keep reporting a number that is not the one the pod trades. Worth aligning `backtest_golf.py`'s canonical band to 0.45 (or parameterising it) so the reported figure tracks the deployed pod.
