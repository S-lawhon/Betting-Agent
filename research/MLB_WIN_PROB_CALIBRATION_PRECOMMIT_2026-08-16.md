# MLB win-prob calibration — pre-read commitment

**Written 2026-08-16, BEFORE `evaluate` was run. No outcome has been read.**
`outcomes_read: false` in the last `status` call, reproduced in §2.

This file does **not** amend `research/MLB_WIN_PROB_CALIBRATION_RULE.md` and
must never be edited to do so — the rule's SHA-256 is pinned in the study
manifest and the evaluator refuses a mismatch. Everything below either
restates the locked rule or commits to a follow-up action the rule leaves open.

---

## 1. Why this file exists

The study was armed 2026-08-07T20:10:26Z and its read-not-before passed
2026-08-14T20:10:26Z. It was not read for two days, and by 2026-08-16 the
sample stood at 123 book-eligible events against a 60-game first look.

The question that raised: **does a sample of 123 collapse the rule's two-stage
design into a single terminal read?** If it did, the branch would have to be
resolved before seeing the number, or resolving it afterwards would be a
textbook rationalisation.

**It does not.** Verified by reading the evaluator, not by assuming:

* `mlb_wp_calibration.py:546` — `look_size = FINAL_LOOK_GAMES if prior else
  FIRST_LOOK_GAMES`. The branch is chosen by the presence of a prior result,
  **not** by the accrued game count. No prior result exists, so this read is
  the **first look at 60**.
* `:563-564` — candidates are sorted by `(scheduled_start, event_ticker)`
  ascending, so the 60 games are the **earliest 60**, chosen by schedule and
  ticker only. Outcome-independent.
* `:597-598` and `:605` — the scoring loop breaks at 60 and the list is
  truncated to 60. The surplus games are untouched and remain the reserve for
  the extension look.

**Therefore the delay cost no design integrity.** Its only effect is that if
the first look returns `NO_DECISION`, the extension to 120 can run immediately
instead of waiting on further games.

## 2. Pre-read state, recorded verbatim

Manifest hashes verified identical on the droplet and locally, and identical to
the values pinned at arm time:

```
model  src/mlb_win_prob.py                        93f50dbc7fe4a1b8...9e9b38887d
rule   research/MLB_WIN_PROB_CALIBRATION_RULE.md  e07513ed29ea610d...11262b9dd
```

Outcome-blind `status`, 2026-08-16T16:31:36Z:

```
events_seen                    158
events_with_pregame_anchor     137
events_book_eligible           123      (first look needs 60)
checkpoint_count_distribution  {0: 35, 2: 5, 3: 10, 4: 108}
row_exclusions                 {invalid_mid: 5114}
outcomes_read                  false
status                         COLLECTING
```

## 3. What is committed, per verdict

The verdicts themselves are the rule's (§8) and are not restated as if they
were chosen here. What follows is the **action** taken on each, committed
before the number is known.

| first-look verdict | committed action |
|---|---|
| **PASS** | Record it. It authorises **nothing** to trade — §9 of the rule and §2's standing statement both bind. The next step is a *new* opportunity card under the factory chain, which must carry its own fill estimate before any execution claim. It is not authority to revive P-016 or P-018. |
| **KILL** | Record it as terminal for this frozen model version. `src/mlb_win_prob` is not re-tuned and re-armed to obtain a different answer. Any future model is a new manifest, a new sample from zero, and a new hash. |
| **NO_DECISION** | Run the extension to 120 **once**, without modifying anything, and treat its output as terminal per §8. |

**Not committed, and deliberately so:** no threshold, no statistic, no
admissibility rule is touched by this file. Those are locked.

## 4. The standing prohibition, restated so it is on the record before the read

A PASS here says the model beats the mid on paired Brier loss over captured
`KXMLBGAME` markets. It says nothing about executable edge after spread, fee,
latency, or adverse selection — the last of which is what killed P-016 v1,
underpowered P-017M, and produced P-018's −3.25¢ residual.

P-018 remains **KILLED by its mechanism gate** and no result under this rule
can change that. Maker/fade archetypes are 0 for 6 in this fund, and the
standing rule from the 2026-07-30 AFT review applies to anything downstream of
a PASS: **never propose a variant without a fill estimate first.**

## 5. Coverage, which binds on every verdict

Book capture drops 33.9% of discovered in-play markets, lowest-volume-first,
and the limiter is the exchange rather than the config. Every verdict produced
under this rule is a verdict about the **liquid third-to-two-thirds of the
in-play book**, and the bias runs in the favourable direction.
