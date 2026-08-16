# MLB win-probability calibration — the last P-018 residue, closed

**Rule:** `research/MLB_WIN_PROB_CALIBRATION_RULE.md` (locked blind 2026-08-07)
**Pre-read commitment:** `research/MLB_WIN_PROB_CALIBRATION_PRECOMMIT_2026-08-16.md`, committed in `d46d0f5` before `evaluate` ran
**Evaluator:** `inplay_research/mlb_wp_calibration.py` (the only sanctioned producer)
**Artifacts:** `inplay_research/calibration/mlb_wp_calibration_{manifest,result}.json`

---

## VERDICT: **KILL** — terminal for this frozen model version

> `src/mlb_win_prob` does **not** beat the contemporaneous Kalshi mid. It is
> slightly **worse**, on both lag paths, and it degrades further as the state
> feed is slowed — which is the direction a real model would move, so the
> harness is behaving coherently while the model is losing.
>
> The rule kills on `mean delta <= 0` at the 30-second primary path. Measured
> **−0.00202**. No significance test was required and none rescues it: the
> interval crosses zero, so this is "no detectable skill", not "significantly
> unskilled". The kill is an **opportunity-cost decision**, exactly as §8 of
> the rule frames it.

## 1. Result, 60 games, equal-weighted, game-clustered bootstrap

| state lag | mean paired delta | 95% CI | model Brier | market Brier |
|---|---:|---|---:|---:|
| **30 s (primary)** | **−0.002024** | [−0.008428, +0.004872] | 0.174433 | **0.172409** |
| 60 s (robustness) | −0.004045 | [−0.010310, +0.002808] | 0.176454 | **0.172409** |

Positive delta means the model is better. Both are negative. The market's Brier
is identical across rows because the benchmark does not depend on state lag —
a built-in consistency check, and it holds.

**The model beat the market in 22 of 60 games.** Not a near-miss distribution.

**Doubling the state lag costs 0.0020 of Brier**, almost exactly the margin by
which the model trails the market at 30 s. The model is sensitive to state
freshness in the expected direction and still cannot close the gap.

## 2. Why this was worth running at all

P-018's mechanism gate killed the surprise thesis on 2026-07-28 but left one
number unexplained: within the top tercile of model/market disagreement, fills
earned **+30¢/contract**, at every surprise level. `REPORT_P018_Gate1_2026-07-28.md`
§5 named three candidate explanations and refused to pick one — genuine model
skill, a stale mid from the 60-second capture cadence, or a broken pregame
anchor when capture starts mid-game.

**This study discriminates them, and it selects against model skill.** A model
that is worse than the mid on a clean forward sample cannot be the source of a
+30¢ replay edge. What remains is the pair of measurement artifacts, both of
which the rule's §2 identified as orientation defects in the P-018 replay and
which this harness does not share:

* it complements away-team YES mids rather than applying home-win probability
  to every ticker, and
* it requires a **true** pregame anchor between 6 h and 5 min before first
  pitch, excluding the event otherwise — 21 events dropped on exactly that.

## 3. Admissibility and coverage

```
events_seen                    158
events_with_pregame_anchor     137
events_book_eligible           123      (first look consumed the earliest 60)
exclusions   invalid_mid                5114 rows
             schedule_no_match            24
             no_pregame_anchor            21
             too_few_book_checkpoints     14
             too_few_state_checkpoints     2
```

The 60 games are the **earliest 60 by scheduled start**, selected before any
outcome was fetched. 63 book-eligible events were never scored and remain
untouched; the extension look is moot under a terminal first-look KILL.

**Coverage bias binds on this verdict as on every other**: book capture drops
33.9% of discovered in-play markets, lowest-volume-first, limited by the
exchange rather than the config. This is a verdict about the liquid
third-to-two-thirds of the in-play book. Here the bias direction is worth
stating plainly — it runs *in the model's favour*, since liquid markets are the
ones with the most reliable mids to be scored against, and the model lost anyway.

## 4. What this closes, and what it does not

**Closes:** the surviving question recorded on P-018's registry entry. There is
no model-calibration case for an in-play MLB pod built on `src/mlb_win_prob`.
Taken with the 07-28 mechanism gate, in-play MLB is done: the surprise thesis
is refuted and the model that produced the headline is not better than the
price it was trading against.

**Does not close, and must not be read as closing:** whether some *other*
baseball model could beat the Kalshi mid. §8 of the rule says so in its own
words — this is terminal for one frozen model version, not proof that no richer
model exists. Any such model is a new manifest, a new sample from zero, and a
new hash, per §3 of the pre-read commitment.

**Authorises nothing.** P-018 and P-016 stay retired. Per the pre-read
commitment §4, even a PASS would have carried no execution claim; a KILL
plainly carries none.

## 5. Process note — this study was invisible for nine days

It armed on 2026-08-07, its read date passed on 2026-08-14, and it was found on
2026-08-16 only by tracing P-018's surviving question by hand. It has **no
workstream in `manager/registry.yaml`** and therefore no `gate` block, so
`manager/throughput.py` never projected a date and the daily brief never listed
it. That is the same *passes-by-absence* defect
`REPORT_P018_Gate1_2026-07-28.md` §6.2 reported against the gate-instrumentation
standard, recurring in the workstream registry rather than the standard.

The defect is now moot for this study — it is terminal — but the next armed
study with no registry entry will be invisible the same way.
