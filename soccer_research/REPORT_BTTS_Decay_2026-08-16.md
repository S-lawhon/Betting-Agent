# First-half BTTS — Phase 2, the late-half maker fade

**Rule:** `soccer_research/BTTS_1H_DECAY_RULE.md` (`032b15d`) + **Amendment 1** (`f9fc788`), both committed before `btts_decay.py` existed and before any P&L was computed
**Harness:** `soccer_research/btts_decay.py` — cache-first, offline replay
**Sample:** 908 settled first-half BTTS markets across 20 series

---

## VERDICT: **PASS** — and under §8.5 that authorises a capacity study, nothing else

| statistic | value |
|---|---|
| net ¢/contract, **equal weight per match** (the gate estimator) | **+4.52** |
| 95% CI, match-clustered bootstrap, 5,000 reps, seeded | **[+2.12, +6.91]** |
| `z` | **+3.71** |
| fill rate | **15.45%** (8,037 fills / 52,024 offers) |
| matches carrying fills | **768** of 908 |
| pooled ¢/contract (reported separately, never as the headline) | **+3.16** |

**This is the first PASS after a long run of kills, so it gets more scrutiny,
not less.** §4 of this report is the attempt to break it.

## 1. What the strategy actually is

Offer to sell YES one tick inside the prevailing ask, **every open in-play
minute** — no close-anchored window (Amendment 1) — and fill only when the
minute's trade range prints **strictly through** the offer. Maker, so fee-free
on `quadratic` series, verified live rather than assumed.

**The mechanism, decomposed:**

| | matches | mean offer | median fills/match | mean total ¢/match |
|---|---:|---:|---:|---:|
| resolved **NO** | 591 (77%) | 0.198 | 5 | **+218** |
| resolved **YES** | 177 (23%) | **0.331** | 4 | **−583** |

We collect ~20¢ five times in the 77% of matches where both teams don't score
before half-time, and pay ~67¢ four times in the 23% where they do. Equal
weight per match: `0.77 × 218 + 0.23 × (−583) = +33 ¢/match`, which is the
+4.52¢/contract headline seen from the other side.

**The naive read does not reconcile, and that is worth stating.** Selling at a
median 18¢ into a 23% YES rate looks like −5¢/contract. It isn't, because the
offer price in YES matches averages **0.331 rather than 0.198** — as the market
rises toward the second goal we keep selling, higher each time. That is a real
feature and also a real risk: **the strategy scales into its losers.**

## 2. Amendment 1 — the leak that would have manufactured this result

`close_time` on these markets is **outcome-dependent**: a YES market closes at
the moment of the second goal, a NO market at half-time. Any "last N minutes
before close" window therefore selects the run-up to the deciding goal in one
arm and the run-up to half-time in the other — **the outcome defining the
sample.** Caught while writing the fill model, before any P&L existed, and
fixed by anchoring to "market open and in-play" instead.

The "late in the half" refinement is **deferred, not dropped**: it needs a
kickoff anchor, and soccer tickers carry **date only, no HHMM**, while
`occurrence_datetime` is a one-hour-resolution estimate of match *end*. That is
the gap `src/golf_schedule.py` fills for golf; its soccer analogue does not
exist in this repo and must not be improvised inside this harness.

## 3. The honest prior, restated

§1 of the rule corrected this candidate's description **before** the test:
stripped of a goal-state feed, this is a **calibration / favourite-longshot
claim**, not a rulebook mechanic. **P-019 killed FLB in our universe and the
"we price it better than Kalshi" archetype is 0 for 8.** A PASS against that
prior is surprising and should be treated as such.

## 4. Attempts to break it — four checks, none of which did

| check | result |
|---|---|
| **Look-ahead** — is `yes_ask.open_dollars` really the start-of-minute value? | `ask.close[i] == ask.open[i+1]` on **51,741 of 51,753** consecutive candle pairs (100%). The offer uses only information available at the time. |
| **Wide-book artifact** — are we filling on stale, empty books? | Spread at fill: **median 2.0¢**, p10 2.0, p90 11.0. Tight, not the one-lot-on-an-empty-book artifact the satellites census warns about. |
| **Size** — is there anything to fill against? | Volume in the filling minute: **median 158 contracts**, p10 13, p90 2,658. A 25-lot fills in the median case. |
| **Concentration** — is one series carrying it? | Leave-one-series-out ranges **+3.93 to +5.27**. `KXWC1HBTTS` (−0.26) and `KXLIGAMX1HBTTS` (−3.53) are negative and dropping them *raises* the estimate; dropping the largest positive contributors barely moves it. |

**Estimator check** (the 2026-08-02 correction): gate **+4.52** vs pooled
**+3.16**. Both positive, same order of magnitude. The headline is the gate
statistic — `mean(x_m)`, equal weight per match — and the pooled figure is
labelled, never substituted.

## 5. What is NOT established, and what would kill it

* **Queue position is not modelled.** We assume a fill whenever price trades
  strictly through an offer that improves the best ask by one tick. Real
  queueing behind other makers can only reduce the 15.45% fill rate.
* **Size is not capped.** Per-contract edge is unaffected, but no capacity
  number should be quoted from this run — that is what a capacity study is for.
* **The loss tail is real and the strategy adds to it.** Skew **−1.25**, worst
  match **−87.68¢/fill**, mean **−583¢** on a YES match, and the average short
  price *rises* within losing matches. Normal-theory intervals would understate
  this; the bootstrap is clustered on matches for that reason, and the P-022
  Amendment 2 lesson (rare large losses breaking normal-theory power) applies
  directly.
* **The sample is one rolling month.** Kalshi settled history rolls off at
  ~30 days, so this cannot be extended backwards — only forward. The candle
  cache is committed gzipped for exactly that reason.
* **Seasonality.** The top-5 European leagues have only just resumed;
  `KXEPL1HBTTS` had **zero** settled markets in this sample. The mix is skewed
  toward MLS, Brasileirão, USL, and the UEFA competitions.

## 6. What §8.5 permits

**A capacity study. Not a pod, not a quote, not a deploy.** The satellites
precedent is a mechanic that survived every filter and addressed **$390** of
capacity. The next question is what this is worth at realistic size against a
median 158-contract minute, and how the fill rate degrades when the offer is
not assumed to be first in queue.
