# Paper review: market-calibrated AFT model for in-play football

**Paper:** arXiv:2605.16066v1 [stat.AP], 15 May 2026. *A market-calibrated accelerated
failure time model for in-play football forecasting.*
**Reviewed:** 2026-07-30
**Verdict: NO EDGE. Do not build. The paper's own authors agree with this reading.**

The claim under review is the abstract's: *"A betting simulation against Betfair in-play
odds yields a 4.5% return on investment (Sharpe ratio 5.94) over 17,458 bets, suggesting
an inefficiency within in-play football markets."*

It does not survive the house standard. Five independent reasons follow, then the parts
of the work that are worth keeping.

---

## 1. The reported Sharpe ratio is a t-statistic that assumes 17,458 independent bets

The paper reports Sharpe values without a formula, a unit of observation, an annualisation
statement, or a risk-free rate. Reverse-engineering all six rows of Table 6 recovers the
construction exactly.

For each row, solve `sd = ROI · sqrt(N) / Sharpe`:

| model | staking | N bets | ROI % | Sharpe | implied sd per bet |
|---|---|---|---|---|---|
| Weibull ψ | Unit | 13,455 | −6.1 | −6.46 | 1.095 |
| Weibull ψ | Kelly | 18,247 | −0.8 | −0.93 | 1.162 |
| Weibull κ,ψ | Unit | 13,455 | −3.4 | −4.34 | 0.909 |
| **Weibull κ,ψ** | **Kelly** | **17,458** | **+4.5** | **+5.94** | **1.001** |
| Maia κ,ψ | Unit | 13,455 | −12.3 | −15.11 | 0.944 |
| Maia κ,ψ | Kelly | 17,077 | +1.3 | +1.60 | 1.062 |

Every implied per-bet standard deviation lands between 0.91 and 1.16, which is the payoff
standard deviation of a unit-stake binary bet. So the quantity called "Sharpe ratio" is
`mean / (sd / sqrt(N))`. That is a t-statistic, already scaled by the root of the bet count.

The bet count is not the sample. There are 140 matches and 13,832 prediction points, so
98.8 evaluation minutes per match, and Kelly places 124.7 bets per match. A bet on the home
win at minute 40 and the same bet at minute 41 resolve on one realised scoreline. Within a
match the bets are close to the same bet.

Rescaling the t-statistic to a match-clustered sample:

| effective n | t |
|---|---|
| 140 (one obs per match, the house convention) | **0.53** |
| 280 | 0.75 |
| 560 | 1.06 |
| 1,000 | 1.42 |

The house convention on this is already written down: *bootstrap CIs for backtests cluster
by event, because within-event outcomes correlate, so treat each event as one observation.*
Applied here, 5.94 becomes 0.53. The paper reports no bootstrap, no clustered standard
error, and no confidence interval on the 4.5%. There is no discussion of dependence between
bets anywhere in it.

**+4.5% ROI over 140 matches is not distinguishable from zero.**

## 2. Bets are struck at the last traded price, and no spread is ever crossed

> "Betfair Exchange freely provides minute-by-minute historical data for football markets,
> recording the last traded price for each outcome."

A whole-paper search for *back*, *lay*, *bid-ask*, *spread*, and *liquidity* returns no
matching sentence. There is no depth check and no size check. Bets are placed at a print,
which is a mid-ish synthetic, not at an offer that could be taken.

This is the exact failure the fund has already paid for. The revised edge law from the
2026-07-28 fee-parabola audit reads: *smaller than tick + spread + fee + the cost of being
filled only when you are wrong*, and that last term is the largest one every time it gets
measured. P-017A's fee saving was real to within 0.05¢ of prediction and unreachable at a
2.2% fill fraction. Maker and fade variants are 0 for 5. The standing rule that came out of
it applies directly here: never propose a variant without a fill estimate first. This paper
contains no fill model at all.

Commission is handled (2% on net match winnings, per the Table 6 caption). Commission was
never the problem.

## 3. The model loses to the market on both proper scoring rules

Table 5, 140 matches, 13,832 prediction points each:

| model | accuracy | RPS | log-loss |
|---|---|---|---|
| Weibull | 0.677 | 0.1353 | 0.7109 |
| Weibull ψ | 0.682 | 0.1347 | 0.7091 |
| Weibull κ,ψ (headline) | 0.702 | 0.1294 | 0.6933 |
| Zou | 0.661 | 0.1473 | 0.7844 |
| Zou κ | 0.682 | 0.1412 | 0.7569 |
| Maia ψ | 0.688 | 0.1375 | 0.7206 |
| Maia κ,ψ | 0.694 | 0.1303 | 0.6963 |
| **Betfair** | **0.706** | **0.1254** | **0.6714** |

Betfair wins on accuracy by 0.004, on RPS by 0.0040, and on log-loss by 0.0219. The paper
says so: *"Betfair retains the best RPS and log-loss."* A model that is worse than the
market on every metric is asserted to make money betting into that market. No ensemble or
model-versus-market blend is tested anywhere.

The paper's real finding is in its own conclusion: *"calibration to market prices is the
dominant driver of predictive accuracy."* Strip the calibration and log-loss goes 0.6933 to
0.7091. The model's own contribution beyond the market price is small.

## 4. The profit is a staking-rule artifact

One of six configurations is profitable. The same model, on the same signals, over the same
matches, loses 3.4% under unit staking and makes 4.5% under Kelly. Unit staking wins 55% of
bets and loses money. Kelly wins 49% and makes money.

Kelly here is full Kelly with the bankroll reset to one unit before every bet, so stakes do
not compound. Implied total staked is 158.15 / 0.045 = 3,514 units across 17,458 bets, so a
mean stake of 0.201 units. There is no fractional multiplier, no stake cap, and no
bankroll-fraction limit. A non-compounding full-Kelly stake is a weighting scheme, and the
log-growth argument that justifies Kelly does not apply to the stake-weighted return that
gets reported. So "4.5% ROI" is a weighted average of the same bets that lose 3.4% unweighted.

## 5. The authors caveat it themselves, and the covariate feed is dead

Verbatim from section 11:

> "Our evaluation relies on Betfair's last-traded prices, which approximate the available
> odds but may not reflect executable prices at the time of prediction."

> "The 140-match sample is also small for betting evaluation, where ROI and Sharpe figures
> are sensitive to outliers and sampling variance."

> "For this reason, we consider the predictive accuracy results more robust than the betting
> returns, and believe that methodical replication is needed to validate profitability
> claims in sports forecasting."

> "Furthermore, PSxG values were sourced from FBref, which has since lost access to the
> underlying Opta statistics."

That last one matters operationally. Post-shot expected goals is the paper's only genuine
in-play information covariate (β̂_psxg = −0.10, SE 0.02), and the free source for it is gone.
Reproducing the model now means buying Opta or an equivalent.

## Venue: the strategy has nowhere to run as written

Betfair is inaccessible to US persons, and the old NV/DE/NJ TVG-brand route no longer offers
exchange or lay functionality (`live_agent_research/REPORT_Live_InPlay_Agent_2026-07-19.md`).
The repo has no Betfair integration, only the string `"betfair"` as a CLV odds-source key in
a test. Any transfer runs on Kalshi, against a book that is thinner than Betfair rather than
sharper, which puts it squarely under "our model beats the market." That archetype is
**0 for 7** on this exchange, and P-021 (Kalshi versus sharp consensus in MLB totals) was
already killed.

---

## What the review turned up that IS worth keeping

None of this is the paper's claim. It came out of checking whether the instrument exists.

### Kalshi in-play soccer is real, and it is the best in-play book in our universe

This was measured, not inferred, at 2026-07-30 02:14Z.

- **1,092 soccer-tagged series**, the largest sport on the exchange by series count
  (Basketball 496, Football 409, Baseball 198, Tennis 127, Golf 112). 283 have an open
  market; 8,911 open soccer markets, 7,808 of them per-match.
- **Markets stay open through the match by rule.** `early_close_condition` on moneyline
  reads *"This market will close and expire after a winner is declared"*, the same phrasing
  CLAUDE.md documents for golf round-leaders. BTTS closes on the second goal. All
  `can_close_early: true`, `settlement_timer_seconds: 30`.
- **Minute-by-minute trading is continuous.** World Cup final (ESP-ARG, 2026-07-19):
  230 of 230 one-minute candles carry non-zero volume, 17:30Z to 21:19Z. Settled
  `close_time` is 21:18:52Z, the final whistle. Across 760 settled soccer legs, the median
  share of in-play minutes carrying at least one trade is **97.4%**.
- **83.1% of per-match dollar volume trades in-play** (median; mean 77.5%). Median per-match
  volume across 346 matches is $351,334. KXWCGAME median $7.99M, KXEPLGAME $455K,
  KXMLSGAME $638K, KXUCLGAME $110K.
- **Fees:** only 17 of 1,092 soccer series charge maker fees. Every prop family and every
  non-top-5-league moneyline (KXMLSGAME, KXBRASILEIROGAME, KXLIGAMXGAME, KXUELGAME,
  KXUECLGAME, KXNWSLGAME) is `quadratic`, so maker-free.
- **Liquidity, per market, KXMVE excluded.** Moneyline `*GAME`, n=733: 40.2% have ≥$100 on
  both sides, 9.8% have ≥$1,000, median spread 3¢. That is better than the 43%/15% figure
  for even the top-60 liquid series exchange-wide.
- **In-play top-of-book is thin, and flow replenishes it.** Live non-degenerate markets
  sampled twice 75s apart, n=39: only 2 of 39 had ≥$100 both sides, median $10 bid / $18
  ask, median spread 8¢, but 26 of 39 books moved inside 75 seconds. Throughput per leg per
  in-play minute: median $345/min excluding the World Cup, p90 $3,159. This is a maker
  profile. Size does not come in one clip.
- Caveat on all of the above: 2026-07-30 is the seasonal trough. The top-5 European leagues
  resume mid-August and the World Cup just ended, so the live sample was Brazilian, NWSL,
  Argentine, and the MLS All-Star game.

### Soccer BTTS was killed on a pricing argument, not a backtest, and the argument has a hole

The R5 kill is one line in a graveyard retrodiction table: *"Soccer BTTS: #3, sits at
P = 0.483, the exact maximum of the fee parabola."* No BTTS report exists.

The price claim reproduces. 216 open two-sided full-match BTTS markets have mean mid 0.5166,
median 0.5150, which puts the taker fee at 1.748¢ against a 1.750¢ theoretical maximum.

Two things that line does not cover. Clause #3 constrains a **taker**, and BTTS is maker-free
on every soccer series. And **first-half BTTS sits at mean mid 0.2588, median 0.2150**, which
is a genuine tail price. It was never tested.

### Timing fields behave differently in soccer than in golf. Add this to CLAUDE.md

- Soccer tickers embed **date only** (`KXMLSGAME-26AUG01PORSEA`), no HHMM. The KXMLBGAME
  "the real time is in the ticker" trick does not transfer.
- Unlike golf, **`occurrence_datetime` is NOT collapsed**: 189 distinct values across 8,911
  open markets, equal to `expected_expiration_time` on 8,264/8,489 open and 310/310 settled.
  Measured against true close on 310 settled game markets, `occurrence_datetime − close_time`
  has median **+54 min** (p10 −22, p25 +45, p75 +58, p90 +62), errs late 83.5% of the time,
  and is within ±60 min 75.5% of the time. It is a usable one-hour-resolution estimate of
  match **end**.
- `close_time` behaves exactly as CLAUDE.md warns: conservative while open (Aug 13/16 for an
  Aug 1 match), rewritten at close. `latest_expiration_time` stays at the far-future default
  even on settled markets, 310/310.
- A minute-by-minute soccer model still needs an **external kickoff schedule**, same pattern
  as `src/golf_schedule.py`. ESPN's soccer API is the analogue.

### R5's unverified tick-size question is now settled

R5 flagged non-1¢ tick regimes as "could not reproduce, settle before trusting." They are
real. `tick_size` is None on 8,911 of 8,911 open soccer markets, confirming it is unusable.
From `price_ranges`: 8,721 `linear_cent` (0.0100), 114 `deci_cent` (0.0010), 76
`tapered_deci_cent`. All 7,808 per-match markets are 1¢. Sub-cent occurs only on World Cup
futures (KXWC, KXWCW, KXWCQUAL), so it does not change any per-match friction calculation.

### Live fee-fixture drift, and it touches a market that traded in-play tonight

`src/fixtures/kalshi_series_fees.json` (generated 2026-07-27, 12,199 series) is missing **18
soccer series** Kalshi has listed since: all of `KXLEAGUESCUP*`, all of `KXMLSAST*`, and
`KXMLSSKILLS`. They are absent from `all_series`, so `fee_per_contract` falls back to the
general maker rate and charges 0.44¢, while live `/series` reports `quadratic`, which is free.

The direction is the documented conservative one, so nothing is broken. It is live now and it
matters: `KXMLSASTGAME-26JUL29MLSLMX-MLS` was trading in-play tonight with $64,522 on the leg
sampled, and charging a phantom 0.44¢ maker fee on a maker-free market kills a marginal
quote. Fix is the documented one. Regenerate and commit:

```bash
python3 -m scripts.generate_fee_fixture   # then commit the fixture
python3 -m scripts.check_fee_fixture      # should be flagging these
```

`check_fee_fixture` is specified to stay quiet for new maker-free series, which is why these
did not alarm. Worth deciding whether that rule should hold for series in a category we now
have a live interest in.

---

## Recommendation

**Do not queue this paper.** It fails the house standard on execution price, on clustering,
and on the archetype scoreboard, and the authors say the accuracy results are the robust part.

R5's standing recommendation still holds: no new broad hunts, effort on the four live gates,
above all making P-022 actually quote. Note that P-022's POI26 window opens
**2026-07-30T16:00Z, today**, on an uncalibrated close reference
(`LAG_DAY_H["KXCHAMPTOUR"] = 12.0` on n=1, a US event, and POI26 is the first Champions Tour
event ever staged in Europe).

Two cheap items the review generated, neither of which is a new hypothesis:

1. **Regenerate the fee fixture** (minutes). Live drift, affects a maker-free in-play market.
2. **Point `book_capture` at soccer before mid-August.** `src/book_capture.py` line 223
   hardcodes `series: ["KXMLBGAME"]` and `config_multi_pod.yaml` has no `book_capture:` block,
   so it captures MLB only. Soccer has 83% of volume in-play against MLB's book, and P-018's
   `SportAdapter` protocol already anticipates a drop-in `SoccerAdapter` (`MlbAdapter` live,
   `NbaAdapter` raises). This is data collection on the deepest in-play book available to us,
   and the top-5 European leagues resume mid-August. It commits us to nothing.

One thing to watch on item 2: `book_capture` runs a **60-second cadence**, which
`REPORT_P018_Gate1_2026-07-28.md` already flags as giving a stale mid. For minute-by-minute
soccer that is the whole resolution of the signal, so the cadence needs a decision before the
capture is worth anything. The droplet is a 2 GB box already running six workloads.

---

## Sources

- [arXiv:2605.16066 abstract](https://arxiv.org/abs/2605.16066)
- [arXiv:2605.16066v1 PDF](https://arxiv.org/pdf/2605.16066v1)
- [arXiv:2605.16066v1 HTML](https://arxiv.org/html/2605.16066v1) (truncates mid-section 5.5 on fetch)
- [alphaXiv mirror](https://www.alphaxiv.org/abs/2605.16066)

Tables 5 and 6 were retrieved byte-identical from two independent routes. Table 6 is
arithmetically self-consistent: all three unit-staking rows satisfy ROI = profit / 13,455 to
two decimals.

Repo cross-references: `CLAUDE.md` (Kalshi timing and fee gotchas),
`research/REPORT_Fee_Audit_2026-07-27.md`, `Deep Research R5 - Five-Way Hunt 2026-07.md`
(six-clause survivor profile, BTTS line at :100), `research/REPORT_P018_Gate1_2026-07-28.md`,
`research/REPORT_Data_Readiness_2026-07-27.md`,
`live_agent_research/REPORT_Live_InPlay_Agent_2026-07-19.md`.
