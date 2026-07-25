# P-024 — MLB F5 / RFI Thin-Corner Sharp-Reference — Phase-1 Gate REPORT
*Generated 2026-07-25 16:07 UTC. `backtest_f5.py` on live Kalshi settled `KXMLBF5TOTAL` +
`KXMLBRFI` candlesticks vs Odds API historical Pinnacle-eu per-event `totals_1st_5_innings`
/ `totals_1st_1_innings`. Backtest-first, kill-gated. No pod.*

## Verdict: **KILL**

- F5-total (t15): Brier sharp 0.2494 vs Kalshi 0.2488 (gain -0.0005)
- F5-total gap->outcome: b=-1.974 (t=-1.37, n=442, 57 day-clusters) -> not significant
- F5-total drift (Kalshi own move ~ sharp gap): b=+0.087 (t=+0.30, n=442, 57 day-clusters)
- F5-total print/touch net edge: -5.56c/ct CI[-22.55,+12.75] n=28 -> fails
- F5-total CLV vs Kalshi close: -0.46c/ct (n=28)
- F5-total median executable $2853/mkt -> >=100
- RFI usable near-lock prints: 743 in 57 days -> ~2346/season -> >=300

## Interpretation — why KILL
1. **The thin corners are as sharp as the headline — P-021 repeats.** On F5-total
   the near-lock Brier is tied (sharp 0.2494 vs Kalshi 0.2488; Kalshi marginally
   better) and the gap→outcome regression is insignificant (t=−1.37). Kalshi's
   pregame F5-total price already contains what Pinnacle's F5 line does — exactly
   the efficient-headline result that killed P-021, now reproduced one derivative
   deeper. Size is NOT the problem (median executable $2.8k/market); *edge* is.
2. **The lead-lag did NOT concentrate here — it collapsed.** The whole P-024 thesis
   was that Pinnacle's lead over Kalshi's drift (headline: b=+0.91, t=+3.21) would
   be *larger* where attention thins. It is smaller to absent: F5-total drift
   b=+0.087 (t=+0.30), RFI b=+0.009 (t=+1.51) — both far below the headline. The
   print/touch net edge is *negative* on both (F5 −5.6¢, RFI −8.6¢) with CIs that
   include or sit below zero. Attention does not visibly lag into these corners.
3. **On RFI, Kalshi is the SHARPER book.** The RFI gap→outcome coefficient is
   significant but *negative* (t=−3.72) and Kalshi's Brier beats Pinnacle's
   (0.2457 vs 0.2463), so trading toward the Pinnacle prob loses — CLV is
   −0.48¢/ct with the whole CI below zero ([−0.86, −0.20]). Prints and size are
   abundant (~2,346 usable/season, $29k/market) but there is nothing to harvest:
   RFI is a Kalshi-native, heavily-traded market that leads the sharp book, not
   the other way round.

**Bottom line: KILL.** Both maker-free thin corners are at least as efficient as
the P-021 headline, the hypothesised lead-lag amplification is absent, and the
only executable edges are negative. Liquidity exists; edge does not. No pod,
collector, or config is built.

## The hypothesis under test
P-021 killed the LIQUID headline total (`KXMLBTOTAL`): Kalshi's pregame price was
as sharp as Pinnacle (Brier tied), the only signal a lead-lag (Pinnacle leads
Kalshi's drift, t=+3.21) worth **+1.8¢ CLV** — too small to clear the taker fee.
P-024 asks whether that lead-lag is **materially larger** in the maker-free thin
"innings" corners, where attention concentrates on the headline market. The
lead-lag amplification test is the drift regression + CLV below; the value test
is the gap→outcome regression + Brier.

## Protocol (why this is not a mid-based backtest)
- Every price is the candlestick **touch** (`yes_bid`/`yes_ask`) or an executed
  **print** (`price` OHLC + per-minute `volume_fp`); bare mids are never used to
  admit a trade. A market with a one-sided touch or **zero entry-hour volume is
  dropped** — a bare ask on an empty book is not a fillable price.
- A trade is admitted only when the sharp prob is **strictly through the touch**
  net of the taker fee (buy YES iff `p_sharp − ask − fee > margin`; NO symmetric).
- Simulated size is haircut to the **entry-hour `volume_fp`**; the REPORT gives
  the **median executable \$/market** (= entry-hour contracts × fill price).
- **Conservative lag-align**: the sharp snapshot is taken **10 min before** the
  Kalshi decision horizon (commence−70m vs Kalshi T−60m; commence−25m vs Kalshi
  T−15m), so any measured Pinnacle lead survives handing the sharp feed a 10-min
  head-start. Odds API Pinnacle-eu "may incur a delay"; this neutralises it.
- All SEs **day-clustered** (cluster-robust OLS + whole-game-day bootstrap CIs).

## F5-moneyline (`KXMLBF5`) — sharp-reference coverage
Across **1495** event-snapshots, `h2h_1st_5_innings` appeared on
any eu book **1488** times and on **Pinnacle 0**
times. Pinnacle-eu carries the F5 total and RFI but posts the F5 *moneyline* on
only a small share of pregame snapshots (soft books carry it more often) — and
`KXMLBF5` is the thinnest Kalshi market of the set (verified: no T−15m print on
the sampled game). With no reliable *sharp* reference and the thinnest book, F5
moneyline is reported here for coverage but is **out-of-scope for the gate**;
`KXMLBF5TOTAL` is the primary corner.

## F5 Total — the primary
### KXMLBF5TOTAL vs Pinnacle-eu totals_1st_5_innings

**T−1h (sharp @ commence−70m)** — n=430 strikes / 430 games / 57 days; mean pregame vol 21519 contracts; match kinds {'pin_exact': 365, 'con_exact': 65}
- Brier: sharp 0.2490 | Kalshi print 0.2498 | 50/50 0.2500 (gain +0.0008)
- gap→outcome (day-clustered): b=+1.262 (t=+0.59, n=430, 57 day-clusters)
- gap→Kalshi drift-to-close: b=+0.048 (t=+0.47, n=430, 57 day-clusters)
- print/touch net-of-fee edge: **+12.40¢/ct** CI[-9.89,+32.71] (28 trades)
- CLV vs Kalshi close: -0.18¢/ct CI[-0.59,+0.23] (n=28)
- median executable size: $492/market

**T−15m near lock (sharp @ commence−25m)** — n=442 strikes / 442 games / 57 days; mean pregame vol 21260 contracts; match kinds {'pin_exact': 370, 'con_exact': 72}
- Brier: sharp 0.2494 | Kalshi print 0.2488 | 50/50 0.2500 (gain -0.0005)
- gap→outcome (day-clustered): b=-1.974 (t=-1.37, n=442, 57 day-clusters)
- gap→Kalshi drift-to-close: b=+0.087 (t=+0.30, n=442, 57 day-clusters)
- print/touch net-of-fee edge: **-5.56¢/ct** CI[-22.55,+12.75] (28 trades)
- CLV vs Kalshi close: -0.46¢/ct CI[-2.52,+1.43] (n=28)
- median executable size: $2853/market

*Line alignment: Kalshi F5-total strikes are X.5; Pinnacle often posts an integer
line. `p_sharp` at a Kalshi strike is Pinnacle's devigged P(over) when it posted
that exact X.5 line (`pin_exact`), else the eu consensus at that exact line
(`con_exact`), else a straddle interpolation across the two nearest posted lines
(`*_interp`). The `pin_exact` subset is the cleanest sharp comparison.*

## Lead-lag amplification test (task step 4)
The P-024 thesis is that P-021's Pinnacle→Kalshi lead-lag (headline `KXMLBTOTAL`:
drift b=+0.91, t=+3.21, CLV +1.8¢) is *larger* in these thin corners. Comparing
the near-lock (t15) drift regression + CLV directly:

- **f5total** (t15): drift b=+0.087 (t=+0.30) vs P-021 b=+0.91 (t=+3.21); CLV -0.46¢ CI-lo -2.52¢ vs P-021 +1.8¢ → **NOT amplified (≤ P-021)**
- **rfi** (t15): drift b=+0.009 (t=+1.51) vs P-021 b=+0.91 (t=+3.21); CLV -0.48¢ CI-lo -0.86¢ vs P-021 +1.8¢ → **NOT amplified (≤ P-021)**

"AMPLIFIED" requires BOTH a stronger drift t-stat (>3.21) AND a larger positive
CLV lower bound (>+1.8¢). Anything else means the corner is no leakier than the
headline — the lead-lag did not concentrate here.

## RFI — print-based near lock only
### KXMLBRFI vs Pinnacle-eu totals_1st_1_innings (Over 0.5 == RFI Yes)

**T−1h (sharp @ commence−70m)** — n=741 strikes / 741 games / 57 days; mean pregame vol 136154 contracts; match kinds {'pin_exact': 741}
- Brier: sharp 0.2466 | Kalshi print 0.2463 | 50/50 0.2500 (gain -0.0003)
- gap→outcome (day-clustered): b=-2.847 (t=-2.18, n=741, 57 day-clusters)
- gap→Kalshi drift-to-close: b=-0.009 (t=-0.21, n=741, 57 day-clusters)
- print/touch net-of-fee edge: **-5.48¢/ct** CI[-21.65,+14.39] (29 trades)
- CLV vs Kalshi close: -0.31¢/ct CI[-0.69,+0.07] (n=29)
- median executable size: $2808/market

**T−15m near lock (sharp @ commence−25m)** — n=741 strikes / 741 games / 57 days; mean pregame vol 136154 contracts; match kinds {'pin_exact': 741}
- Brier: sharp 0.2463 | Kalshi print 0.2457 | 50/50 0.2500 (gain -0.0006)
- gap→outcome (day-clustered): b=-1.639 (t=-3.72, n=741, 57 day-clusters)
- gap→Kalshi drift-to-close: b=+0.009 (t=+1.51, n=741, 57 day-clusters)
- print/touch net-of-fee edge: **-8.57¢/ct** CI[-20.26,+3.82] (41 trades)
- CLV vs Kalshi close: -0.48¢/ct CI[-0.86,-0.20] (n=41)
- median executable size: $29222/market

Usable near-lock prints: **743** of 743
settled RFI games over 57 game-days
(13.0/day) → projected **~2346
usable prints/season** (×180 game-days). Gate needs ≥300.

## Gate
- **ADVANCE** iff (F5-total near-lock) gap→outcome b>0 & |t|>1.96 day-clustered
  **AND** print/touch net edge ≥2¢/ct with a day-clustered CI clearing 0 **AND**
  median executable ≥\$100/market; RFI additionally needs ≥300 usable prints/season.
- **KILL** if the corners are as sharp as the headline (P-021 repeating: tied
  Brier, insignificant gap) or the edge lives only on unfillable quotes.

## Odds API spend
This run: **45144 credits** over 1959 calls
(per-event period markets = 10 cr/market/snapshot; 2 markets × 2 snapshots/game +
1 events-list). Budget stated in the run: **200000 credits** of the
key's ~4.9M remaining. Lookback 60 days.

## Caveats
- F5-total main lines are often integers while Kalshi strikes are X.5; the
  interpolated subset carries a light monotonicity assumption (see line note).
  The `pin_exact` / `con_exact` subsets are assumption-free and reported via the
  match-kind counts.
- Kalshi F5-total settles at end of the 5th; RFI at the end of the 1st. The
  pregame anchors are candlestick touches at the ticker-decoded first pitch minus
  the horizon; markets with no two-sided touch there are dropped (correctly — not
  liquid enough to trade).
- Pinnacle "near-lock" = snapshot at commence−25m (the conservative-lag sharp
  side of the Kalshi T−15m horizon); a few games may have the line pulled early.
