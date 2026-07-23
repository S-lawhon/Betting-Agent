# P-021 — MLB Totals vs Sharp-Book Consensus — Phase-1 Gate REPORT
*Generated 2026-07-23 01:39 UTC. Cheapest falsification from `backtest_totals.py` on live
Kalshi settled `KXMLBTOTAL` data + Odds API historical Pinnacle-eu / consensus
closing totals. Backtest-first, kill-gated (SPEC_P021 §4).*

## Verdict: **KILL**

- Brier sharp 0.2477 vs Kalshi 0.2484 (gain +0.0008) -> tied (no info)
- gap->outcome coef b=+0.268 (t=+0.23, 28 day-clusters) -> not significant
- net-of-fee taker: 50 trades, avg -2.32c/ct CI[-14.76,+9.90] -> indistinguishable from 0
- CLV vs Kalshi close: +1.79c/ct CI[+0.20,+4.80] (n=50) -> POSITIVE

## Method
For each settled `KXMLBTOTAL` game in the last **20 days**, the
sharp reference is the **Pinnacle-eu** closing total (Odds API historical
snapshot at first-pitch − 5 min; regions=eu), devigged to P(total > line) with a
multiplicative de-vig; an **ex-DFS multi-book consensus** (mean of per-book
devigged P(over) across the eu board) is kept as a fallback reference. The Kalshi
price is the **pregame** bid/ask **mid at T−1h before first
pitch** (`k_entry_mid`), with the bid/ask used for the taker sim. Comparison is at
the **main line only** — the Pinnacle-posted total matched to the Kalshi strike at
the same number. Realized 0/1 = Kalshi's settled `result`. **All statistics are
clustered by game-day** (same-day games share weather/pitching shocks); the gap
regression uses cluster-robust SEs and PnL/CLV CIs bootstrap whole game-days.

Sample: **455 matched strikes** across **364 games** on
**28 game-days**. Mean Kalshi pregame volume/strike: 128747 (fp).

## (i) Calibration — Brier / log-loss vs realized
| reference | Brier | log-loss |
|---|---|---|
| Pinnacle/consensus sharp (`p_sharp`) | 0.2477 | 0.6884 |
| Kalshi pregame mid (`p_kalshi`) | 0.2484 | 0.6900 |
| 50/50 baseline | 0.2500 | — |

Lower is better. If the sharp line carries information Kalshi lacks, `p_sharp`
should have the lower Brier.

## (ii) Gap regression — realized ~ a + b·(p_sharp − p_kalshi)
- **b = +0.2683**, cluster-robust SE 1.1771,
  **t = +0.23**, n=455 over 28 day-clusters.
- A positive, significant `b` means: when the sharp fair prob exceeds Kalshi's, the
  contract hits YES more often — the gap has predictive content.

## (ii-b) Does the sharp gap predict Kalshi's OWN drift to close?
- b = +0.9079 (t=+3.21, 28 clusters):
  regressing Kalshi's entry→close move on the sharp gap. Positive ⇒ Kalshi
  converges toward the sharp line by first pitch (the CLV mechanism).

## (iii) Net-of-fee taker PnL (flat 1 contract, margin 0)
- Take YES at ask when `p_sharp − ask − fee > margin`; else take NO symmetrically.
- **50 trades**, total -1.161, avg
  **-2.32¢/ct**, day-clustered 95% CI
  [-14.76, +9.90]¢.
- Fee = taker 0.07·P·(1−P) (KXMLBTOTAL is `quadratic`; maker fee 0, but this is a
  taker sim). Half-spread is implicit (entry at the ask/1−bid).

## (iv) CLV vs Kalshi's own close — the survival criterion (P-001 north star)
- **+1.79¢/ct**, day-clustered 95% CI
  [+0.20, +4.80]¢ (n=50).
- Positive ⇒ Kalshi's price moved toward our entry side by first pitch: we bought
  below the closing line, exactly the P-001 edge signature.

## (v) Taker margin sweep — does selectivity rescue the edge? (score-only, no API)
| min net-edge margin | trades | avg PnL ¢/ct | day-clustered 95% CI |
|---|---|---|---|
| 0.00 | 50 | −2.32 | [−14.76, +9.90] |
| 0.01 | 18 | +10.91 | [−11.72, +34.31] |
| 0.02 | 7 | +13.89 | [−24.31, +48.37] |
| 0.03 | 3 | +27.37 | [−42.69, +62.40] |
| 0.05 | 2 | +62.40 | [degenerate] |

The point estimate rises with selectivity **only because the sample collapses to
2–3 fills** — every CI spans zero (or is degenerate) until n is too small to mean
anything. There is **no margin with both a usable sample and a CI clearing zero.**
This is the signature of fitting noise, not an edge.

## Interpretation — why KILL
1. **The value thesis is refuted.** The whole P-021 charter was: *a sharp book's
   closing total carries information Kalshi's pregame price lacks.* Both direct
   tests say no — Brier is tied at the coinflip (0.2477 vs 0.2484; both barely
   beat 0.25 because the **main line is set to ≈50/50**), and the gap→outcome
   regression is dead (t=+0.23). Kalshi's `KXMLBTOTAL` pregame market is as sharp
   as Pinnacle. This is exactly the risk the spec pre-registered ("Kalshi may
   also be near-efficient on totals").
2. **The one real signal is a lead-lag, not value, and is not taker-monetizable.**
   Pinnacle *leads Kalshi's own pregame drift* (§ii-b: b=+0.91, **t=+3.21**), which
   is why CLV is mildly positive. But Kalshi drifts toward Pinnacle *whether or not
   Pinnacle is right* (the outcome regression is null) — pure line-chasing. The
   drift (~1.8¢) is smaller than taker fee + spread, so taking it **loses money net
   of fees** (−2.3¢/ct at the only statistically usable sample).
3. **The residual maker angle is off-charter and a known trap.** Capturing a ~1.8¢
   lead-lag would require *making*, not taking — resting on the side Pinnacle
   favors. On a ~1¢-spread market that is a momentum-maker exposed to adverse
   selection (you fill exactly when the market runs past you): the same failure
   mode that retired **P-016 v1**. Not worth building; not what P-021 was chartered
   to test.

**Bottom line: KILL the P-021 taker thesis.** The player-prop leg (softer sharp
reference on a *less* efficient Kalshi series) does not rescue a totals leg that
died on the efficient series — per the spec's own gate, do not spend the 10×-cost
historical prop pulls. No pod, collector, or config is built.

## Gate
- **ADVANCE** iff sharp beats Kalshi on Brier **and** the gap coef is positive &
  significant (|t|>1.96, day-clustered) **and** day-clustered CLV lower bound > 0.
- **KILL** if sharp does not beat Kalshi on Brier **and** the gap coef is
  insignificant (the softer player props won't rescue it — do not proceed).
- **MARGINAL** otherwise → widen the sample (more lookback days) before deciding.

## Caveats
- Main-line-only: one strike/game keeps observations independent within the
  clustering but caps sample size; alternate-total strikes (correlated) are a
  Phase-1b extension, not this gate.
- Kalshi `KXMLBTOTAL` settles at game end; the pregame anchor is reconstructed
  from candlesticks at T−1h before the ticker-decoded first
  pitch. Games with no pregame quote at that horizon are dropped (not liquid
  enough to trade), which is the correct conservative treatment.
- Pinnacle "closing" = snapshot at first pitch − 5 min; a handful of games may
  have had the line pulled early or the snapshot mis-aligned (logged as misses).
