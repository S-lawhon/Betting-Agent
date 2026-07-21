# Kalshi Tennis: Is There Positive EV Beyond the Match Line?

**Research assessment — July 18, 2026**
*Scope: Kalshi-listed tennis contracts. Data: live Kalshi snapshot (518 open markets, full orderbooks), 22,066 settled Kalshi tennis markets (May 13 – Jul 18, 2026), pre-match price histories for ~6,300 of them, and 23,654 ATP/WTA matches 2022–2026 with closing odds and set-level scores (tennis-data.co.uk). Pricing engine: exact DP over the point→game→set→match hierarchy, validated against Monte Carlo.*

---

## Executive summary

**The efficiency-gradient hypothesis is directionally supported but the tradable version of it is mostly about liquidity, not price.** Kalshi's tennis prop prices are not systematically wrong at the mid — across 212 live prop quotes the median gap between the empirical fair price and the quoted mid was ≈ 0¢ in every prop family, and a scan of every dominance/adding-up relation across 518 orderbooks found **zero executable multi-leg arbs** net of fees. What actually distinguishes props from the match line is that they are quoted 2–20× wider, with ~1/40th the volume and ~1/5th to 1/50th the depth. The market's "inefficiency" manifests as a wide no-trade band inside which nobody is forced to be wrong, plus occasional idiosyncratic quotes that stray outside it.

Where the edge concretely lives, in order of confidence:

1. **Heavy favorites on the match line — the one signal that survives every control.** Across 627–804 settled Kalshi matches (May–Jul 2026), pre-match prices show the classic tennis favorite-longshot bias: favorites priced 90–99¢ won 98.6% (implied 94.3–94.5%, ~3σ, robust to measuring 2h before the match), and 80–90¢ favorites won 87.5–90.6% (implied ~84%). Buying a 95¢ favorite that wins 98.6% nets ≈ +3¢/contract after the 0.33¢ taker fee — ~3% ROI per trade on the deepest, tightest books Kalshi tennis has. This replicates 20 years of published tennis FLB findings, now visible in Kalshi's own two months of data.
2. **Favorite-longshot bias echoes in prop books.** Heavy favorites' set-1 and 2-0 markets tend to be quoted below empirical fair (buy-side edge on favorites, consistent with 20 years of tennis FLB literature). Live snapshot: +1–3¢ net after fees, sizes of tens to hundreds of contracts.
3. **Making, not taking.** Prop series charge **no maker fee**; the match series do (25% of taker). With 6–11¢ spreads on set/exact markets and real fill flow (median 29–65 trades per prop market), quoting both sides at empirical fair ± 2–3¢ is the structurally advantaged way to monetize the width. The capacity, however, is small (median $0.7–1.9k within 5¢ of mid).
4. **Serve-profile mispricing (the Klaassen–Magnus identification gap).** The match line pins down only the skill *difference* (pa − pb); totals, tiebreak and set-score markets load on the serve *sum* (pa + pb). A market maker projecting props off the match line alone must misprice big-server and extreme-grinder profiles. This is the theoretically soundest edge, but exploiting it requires player-level serve/return inputs, and (crucially) the empirical validation below shows the naive iid model gets the joint distribution wrong in ways that dwarf this effect if uncorrected.

**Honest bottom line:** the props are *harder* to beat than the match line, not easier. In a 28-day backtest of 895 pre-match prop prices, Kalshi's quoted mids were better calibrated than a 23,654-match empirical benchmark in **all four prop families**, and a taker strategy harvesting apparent edges vs that benchmark **lost money at every threshold tested**. The efficiency gradient exists — but it is a *liquidity* gradient, and the wide spreads it produces protect the quoters rather than exposing them. The exploitable residue is (1) the match-line FLB above, (2) fee-free market-making inside 6–11¢ prop spreads, and (3) genuinely idiosyncratic stale quotes worth a few cents on tens-to-hundreds of contracts. None of this scales past roughly hundreds of dollars of EV per tournament week at current depth.

---

## 1. What Kalshi actually lists (and what it's worth)

149 tennis-tagged series exist; the live tradable structure on any given day:

| Family | Series | Open mkts (Jul 18) | Median spread | Median vol (settled) | Depth ≤5¢ of mid (med / p90) |
|---|---|---|---|---|---|
| Match winner ATP | KXATPMATCH | 52 | 4¢ | **483,731** | $3,305 / $43,368 |
| Match winner WTA | KXWTAMATCH | 40 | 1¢ | 243,368 | $7,605 / $22,715 |
| Set winner (1 & 2) | KXATP/WTASETWINNER | 184 | 6–11¢ | 3,251–4,727 | $1,595–1,871 / ~$3,000 |
| Exact set score | KXATPEXACTMATCH | 104 | 6–7¢ | 268 | $698 / $1,281 |
| Total games | KXATPGTOTAL | 69 | **78¢** | 391 | $61 / $3,669 |
| Game spread | KXATPGSPREAD | 69 | 30¢ | 365 | $1,167 / $3,698 |
| Tournament futures | KXATP/KXWTA + slam series | 67 | wide | — | — |

Also in the catalog (dormant or sporadic): tiebreak-occurrence, total sets, any-set, set-sweep, aces, per-game micro-markets (median volume ~0), reach-round, qualification. WTA prop coverage is materially thinner than ATP (no WTA totals/exact/tiebreak currently).

Settled history (all series, May 13 – Jul 18): 1,924 ATP + 1,910 WTA match markets, 4,456 + 3,842 set-winner, 4,406 exact-score, 2,771 totals, 2,757 spreads. The **volume gradient is a factor of ~100–1,000**: the match line does half a million contracts; exact-score and totals do a few hundred.

Settlement nuances that matter to EV:
- Pre-match cancellation (walkover): markets void at "fair price" per the rules.
- **Mid-match retirement:** props that are already unconditionally decided settle normally; undecided props resolve at a discretionary "Fair Market Price" set by the exchange. An over that has already hit stays a winner; an under that could still have gone either way gets FMP'd. This is *more* benign than most sportsbook grading, and it means retirement risk does **not** explain cheap overs — but FMP discretion is a real tail risk for anything unsettled at stoppage.

## 2. The pricing engine

`tennis_pricer.py` — exact dynamic programming, no simulation error:

- point → game: closed-form hold probability (O'Malley/Klaassen–Magnus form, `p_hold(0.6)=0.7357` reproduces the literature values);
- tiebreak: DP with the exact 1-then-2 serve rotation, gambler's-ruin closed form from 6-6;
- set: DP tracking the **joint** distribution of (winner, games A, games B, tiebreak occurred), with correct serve alternation;
- match: convolution across sets with the who-serves-first bookkeeping done exactly; Bo3 and Bo5;
- outputs: match winner, set-1/set-2 winner, all exact set scores, the full total-games distribution, the full game-margin distribution, any-tiebreak, straight-sets;
- `implied_holds()` inverts the market match line back to (pa, pb) given a serve-sum assumption; `price_props_mixture()` prices under Gaussian uncertainty in both the serve-sum and the skill-gap (3×3 Gauss–Hermite), which is what real day-to-day form heterogeneity requires (see §3);
- validated against an independent 200k-path Monte Carlo: all props agree within z < 0.6.

## 3. The most important modeling finding: the iid model is wrong in ways that matter for props

Fitting the single serve-sum parameter so the model matches the empirical mean total games (per tour × surface, 2022–2026, n = 20,442 completed Bo3 matches), then comparing the model's joint distribution to reality within favorite-probability buckets:

- **Real tennis produces far more straight-set wins than the iid model.** ATP hard, 0.7–0.8 favorites: 52.6% of favorite wins are 2-0 vs 45.1% modeled. WTA clay, 0.8–0.9 favorites: 70.6% vs 56.5%. The gap widens with favorite strength and is bigger on the WTA side.
- **ATP tiebreak frequency is drastically above the totals-calibrated model** (43.3% of ATP hard matches see a TB vs 28.7% modeled), *while mean totals match* — impossible under any single (pa, pb).
- The effective serve-sum that matches totals (~1.05–1.06, i.e. 52–53% serve points won) is far below the physically measured ~64% (ATP) — iid at real serve percentages produces sets that are too long.

Interpretation: matches are a **mixture** — day-to-day form variance and matchup heterogeneity make the two players' parameters random, which simultaneously (a) fattens the 2-0 tail, (b) raises tiebreak incidence in serve-dominant draws, and (c) shortens the average match relative to iid at the same hold rates. The mixture extension (random s and d, 3×3 Gauss–Hermite) reconciles these moments — and, tellingly, it recovers *physically sensible* serve levels where the iid fit could not:

| Segment | fitted s₀ (spw) | σ_s | σ_d | E[total] emp/model | P(TB) emp/model | P(2-0) emp/model |
|---|---|---|---|---|---|---|
| ATP Hard | 1.28 (.640) | 0.04 | 0.08 | 23.79 / 23.99 | .419 / .403 | .446 / .385 |
| ATP Clay | 1.20 (.600) | 0.10 | 0.08 | 23.65 / 23.36 | .350 / .335 | .400 / .389 |
| ATP Grass | 1.32 (.660) | 0.13 | 0.08 | 24.57 / 24.81 | .429 / .492 | .448 / .382 |
| WTA Hard | 1.16 (.580) | 0.04 | 0.11 | 22.54 / 22.21 | .271 / .255 | .444 / .396 |
| WTA Clay | 1.16 (.580) | 0.04 | 0.11 | 22.18 / 22.19 | .215 / .255 | .419 / .401 |

(0.6–0.7 favorite bucket shown.) The fitted serve sums now match the tour's measured serve-points-won (ATP hard ≈ 64%, grass ≈ 66%, clay ≈ 60%, WTA ≈ 57–58%) — the hierarchy is right once it's fed a *distribution* over parameters. One residual bias survives even the mixture: empirical P(2-0) runs ~4–6 points above the model across segments. That is genuine within-match momentum (the set-1 winner outperforms the pre-match parameters in set 2 — the Klaassen–Magnus non-iid effect), and it means any straight-sets/set-2 pricing needs the empirical correction, not just parameter dispersion.

Consequence for the hypothesis: several of the "mispricings" a naive point-model flags on Kalshi are the *model's* error, not the market's. In the first pass, the raw iid engine (tour-average serve levels) flagged "buy the over" in essentially every live match; the empirically calibrated fair prices erased that signal almost entirely. **Any prop strategy priced off an uncorrected hierarchical model will systematically buy overs and 2-1 scores and lose.** This is where the "exploit the hierarchy" intuition needs discipline: the hierarchy is right, but it must be fed a distribution over parameters, not a point estimate.

## 4. Live cross-section (212 sanely-quoted props, Jul 18 snapshot)

Using empirical conditional fair prices (23k matches, smoothed logistic curves in the devigged match line, exact scores renormalized to cohere with the match price):

| Prop family | n | median(fair − mid) |
|---|---|---|
| exact score | 95 | +0.2¢ |
| set-1 winner | 78 | +0.2¢ |
| totals | 20 | +0.0¢ |
| spreads | 19 | −0.4¢ |

**Mids are collectively unbiased.** 18 of 212 quotes showed a net-of-fee taker edge, median ≈ +2¢, concentrated in: set-1 markets of heavy WTA favorites (quoted 3–7¢ below empirical fair — the FLB signature), a couple of stale totals/spread quotes, and thin books with size 10–100. The largest was +6.5¢ (an over-25.5 quoted at 48–49 when empirical fair was ~40) with 800 contracts of depth — ~$50 of expected profit.

Model-free consistency checks across all 46 matches: exact-score mids sum to 0.93–1.02 (fine given spreads); set-1 complements sum to 0.92–1.01; match complements' asks sum to 1.01–1.05 (no complement arb); the handful of >1.4 sums are unquoted junk books, not opportunities.

## 5. Multi-leg / correlation structures (the angle-2 hypothesis)

Scanned every match for depth-aware executable combinations, net of taker fees on every leg:

- R1 buy all four exact-score YES (guaranteed $1 payout)
- R2 NO(match) + YES(2-0) + YES(2-1) hedge identities
- R3 complements on match/set books
- R4 totals-ladder dominance (YES over L₁ + NO over L₂, L₁<L₂)
- futures vs next-match dominance (tournament price ≤ next-match price)

**Result: zero executable arbs.** The books are internally coherent to within fees + spread everywhere. The theoretically interesting correlation trade — exploiting that match winner, set score, totals and tiebreak are all functions of the same (pa, pb) — has no riskless expression at current quotes; it only exists as a *statistical* trade (buy the leg your model says is cheap conditional on the others), which collapses to the single-prop problem in §4 with its capacity limits.

The deeper reason: Kalshi's prop books appear to be quoted by market makers who *do* derive them from the match line with a coherent joint model (or copy sportsbook derivatives that do). The correlation structure is priced; what's left is parameter disagreement, not ignored correlation — and the backtest below confirms the market wins those disagreements.

## 6. Historical backtest (May–July 2026 Kalshi prices)

Method: for every settled tennis market in the 28-day window with hourly candle history (5,937 markets pulled; 627–804 match lines and 895 prop observations joined after quality filters), take the last quote before the scheduled match start, compute the empirical fair given the concurrent Kalshi match line, then score. ATP Wimbledon observations excluded (Bo5 vs Bo3 curve support).

**A. Gaps.** Median (empirical fair − market mid): set-1 +0.2¢, exact +0.1¢, totals −2.6¢, spreads +1.6¢. No prop family shows a systematic exploitable bias; the totals tilt says Kalshi overs were, if anything, slightly *rich* relative to history (the opposite of what a naive iid serve model claims).

**B. Calibration — the decisive test.** Brier scores against realized outcomes:

| family | n | Brier(market mid) | Brier(empirical fair) | winner |
|---|---|---|---|---|
| set-1 winner | 309 | 0.2080 | 0.2085 | market |
| exact score | 431 | 0.1714 | 0.1776 | **market** |
| totals | 80 | 0.2359 | 0.2510 | **market** |
| spreads | 75 | 0.2064 | 0.2260 | **market** |

The market's prop prices predict outcomes better than a 23k-match conditional-frequency benchmark in every family. A 50/50 blend beats the pure benchmark everywhere and beats the market only marginally on set-1. Kalshi's tennis prop mids carry real information beyond the match line — they are not naive projections.

**C. Taker simulation.** Buying whatever the empirical benchmark flagged as ≥2/4/6¢ net-of-fee cheap: **negative PnL in 11 of 12 family × threshold cells** (e.g. exact @2¢: 50 trades, 30% hit rate, −7.0¢/contract; totals @4¢: 17 trades, −10.9¢/contract). The single positive cell (set-1 @4¢: +1.0¢ on 26 trades) is noise. Apparent taker edges against these books are adverse selection, fully consistent with §4's live-snapshot finding that mids are unbiased: when a quote strays far enough from empirical fair to look tradable, it is usually the quote that knows something (lineup news, in-form serve data, delayed start) — not you.

**D. Match-line calibration (the FLB).** Same window, pre-match prices:

| implied bucket | n | implied | won | at T−2h (n=804) |
|---|---|---|---|---|
| 0.5–0.6 | 161 | .551 | .540 | .553 → .537 |
| 0.6–0.7 | 155 | .649 | .665 | .648 → .642 |
| 0.7–0.8 | 135 | .752 | .704 | .749 → .729 |
| 0.8–0.9 | 106 | .844 | .906 | .843 → .875 |
| 0.9–1.0 | 70 | .945 | **.986** | .943 → **.986** |

Longshots lose more than implied, 90¢+ favorites win ~4 points more than implied (~3σ, stable at T−2h). This is the textbook tennis favorite-longshot bias, live on Kalshi today, and it is the cleanest positive-EV signal this study found — on the most liquid contract, not the props.

## 7. Frictions: what eats the edge

**Fees** (0.07 quadratic, verified against the July 7, 2026 schedule):

| price | taker fee | % of stake | maker fee (props) |
|---|---|---|---|
| 10¢ | 0.63¢ | 6.3% | 0 |
| 30¢ | 1.47¢ | 4.9% | 0 |
| 50¢ | 1.75¢ | 3.5% | 0 |
| 90¢ | 0.63¢ | 0.7% | 0 |

Hold-to-settlement costs one taker fee (settlement is free); a round trip costs two. Prop series charge **no maker fee**; KXATPMATCH/KXWTAMATCH and slam futures charge makers 25% of the taker fee. The fee curve punishes exactly the mid-priced (30–70¢) contracts where most prop edges live: a 2¢ gross edge at 50¢ is a 0.25¢ net edge taken, but a 2¢ + free-fill edge made.

**Spreads:** the effective one-way cost of crossing is half the spread — 0.5–2¢ on match lines, 3–5.5¢ on set/exact, 15–39¢ on totals/spreads at the snapshot. Almost no prop edge survives crossing a totals book pre-match; those books only tighten in-play.

**Depth/capacity:** median executable size within 5¢ of mid — $3.3k (ATP match), $7.6k (WTA match), $1.6–1.9k (set winner), $0.7k (exact), $1.2k (spread), $61 (totals!). A realistic prop edge of 3¢ × $1,000 of depth ≈ **$30 expected value per opportunity**, maybe 2–5 such opportunities per tournament day. Order-of-magnitude: low hundreds of dollars per week of expected value for a taker strategy run across the whole tour, before adverse selection.

**Adverse-selection reality check:** the wide prop quotes are wide *because* the quoters know the match line moves and they will be picked off; a taker strategy earns the quoted edge only when the quote is stale for reasons other than news you don't have. Volume data (match markets: 1,000+ trades; props: 20–65) says the props are where retail is absent and MMs quote defensively — the classic sign that visible "edges" there are partly compensation for pick-off risk.

## 8. Where the hypothesis fails, plainly

The hypothesis was: *match lines are efficient; sub-markets carry systematically higher mispricing and therefore higher EV potential.* The evidence:

1. **"Props are priced worse than the match line" — false at the mid.** Live cross-section: median fair-vs-mid gap ≈ 0¢ in every family. Backtest: prop mids beat a 23k-match empirical benchmark on calibration in all four families. The props are *informationally* efficient; they are just *transactionally* expensive.

2. **"Joint/correlation mispricing creates synthetic positive-EV positions" — not found.** Zero executable multi-leg arbs across 518 books; every adding-up, complement, dominance and futures-vs-path relation holds within fees. Whoever quotes these books uses a coherent joint model. The correlation angle survives only as a statistical disagreement trade, and the taker simulation shows the market wins those disagreements on average.

3. **"A point-level model can out-price the market" — not with a point estimate, and not obviously at all.** The iid hierarchy misfits observable tennis (P(2-0), tiebreak rate, totals shape) worse than the market misprices anything. After mixture correction and empirical calibration, the model matches the market — it doesn't beat it. The model's real value is as *infrastructure*: pricing in-play states, checking quotes before making markets, and flagging genuinely stale books — not as a standalone alpha source.

4. **What actually survives:**
   - **Match-line FLB on heavy favorites** (~3–4 points of underpricing at 90¢+, ~3σ, replicating the literature): small but real, on the deepest books. Worth systematizing: buy ATP/WTA favorites ≥88¢ pre-match, expected ~2–4% per trade, capacity thousands of dollars per match rather than hundreds.
   - **Fee-free prop market-making**: quote both sides of set-1/exact books at empirical fair ±2–3¢, earn the spread with zero maker fees, using the DP engine + match-line-anchored fairs for risk control. Realistic but operationally serious (needs quote management and in-play hedging via the match line — which is exactly what makes the correlation structure *useful* rather than *tradable-against*).
   - **Occasional stale/idiosyncratic quotes** (a few cents on 10–800 contracts): pocket money, not a strategy.

5. **Capacity is the binding constraint everywhere.** Median prop depth within 5¢ of mid is $61 (totals) to $1.9k (set winner). Even a systematically right 3¢ edge is ~$30–60 of EV per opportunity. The FLB trade is the only one whose venue depth ($3–43k) supports meaningful size.

**Verdict: the efficiency-gradient hypothesis is half-right for the wrong reason.** Props do sit further from efficient-market conditions — but the slack shows up as spread, thin depth, and defensive quoting, not as harvestable mispricing at the touch. The taker's version of this thesis fails; the maker's version, plus the match-line favorite bias, is what remains. If the next step is live validation, the right experiment is the one this fund already runs elsewhere: paper-trade (a) the ≥88¢ favorite rule and (b) a small set-1/exact market-making loop, and judge both by CLV against Betfair/Pinnacle closers rather than raw PnL over a noisy month.

## 9. Data & method caveats

- The Kalshi backtest window is 28 days (May's API history is retrievable but the candle pull was scoped to recency); FLB magnitudes have ~1.3–4 point standard errors. The direction matches two decades of published results, but the *size* deserves a longer sample before betting real bankroll — the `/historical/` API tier can extend this.
- Empirical fair curves are Bo3 tour-level; Kalshi's qualifying/challenger matches sit slightly outside the training distribution (weaker players, different retirement rates). The curves validated out-of-sample (2025–26 holdout: calibration gaps ≤0.7¢, beats base rates on Brier everywhere), but level-specific curves would tighten them.
- "Pre-match" quotes come from the last hourly candle before *scheduled* start; late starts can leak early in-play information into that quote. The FLB result was re-run at T−2h and held; prop results were not re-run (contamination would *favor* the market, but the market also won at the unlagged timestamp in the live snapshot, where contamination is impossible).
- Retirement/walkover handling: Kalshi FMP discretion on undecided props is a settlement tail-risk this analysis prices at zero.
- All comparisons use Kalshi's own devigged match line as the conditioning variable. If that line is itself biased (per §6D it is, at the extremes), prop fairs conditioned on it inherit a small bias; the FLB and prop analyses are therefore not fully independent tests.

## Appendix: reproduction

- `tennis_pricer.py` — DP engine + mixture pricing + fee math (self-test: `python3 tennis_pricer.py`)
- `analyze_live.py` / `live_empirical_check.py` — live snapshot vs model / empirical fairs
- `empirical_fair.py`, `build_empirical.py`, `fit_mixture.py` — 2022–26 empirical curves and mixture calibration
- `backtest_candles.py` — settled-market pre-match price backtest
- `arb_scan.py` — depth-aware multi-leg arb scanner
- Scratch data: Kalshi snapshots (open/settled/orderbooks/candles), tennis-data.co.uk workbooks
