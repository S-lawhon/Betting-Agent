# First-half BTTS — Phase 2 decay rule (late-half maker fade)

**Written 2026-08-16 BEFORE `btts_decay.py` existed and before any P&L,
settlement-conditional statistic, or fill was computed.** Liquidity was
measured first (§3) and is outcome-independent.

**Authorises nothing to trade.**

---

## 1. Correction to how this candidate was described, made before it is tested

`BTTS_CONTAINMENT_RULE.md` §7 and the Phase 1 report both called this
*"genuinely structural rather than a forecast."* **That was too generous and is
corrected here rather than after the result.**

To price the bound at minute *t* you need a goal-state feed or a scoring rate.
Stripped of that, the claim is *"the market overprices a decaying longshot late
in the window"* — which is a **calibration / favourite-longshot claim**, not a
rulebook mechanic. That matters because:

* **P-019 killed favourite-longshot bias** in our Kalshi universe at the
  calibration gate, and
* the *"we price it better than Kalshi"* archetype is **0 for 8**.

The prior is therefore poor, and it is recorded here so that a positive result
faces the scrutiny it deserves and a negative one surprises nobody.

**What makes it worth running anyway** is cost, not promise: the tape is
retrospective and free (§3), so this is hours of work, not a collection
programme.

## 2. The claim, falsifiably

> In the closing minutes of the first half, with the payout criterion not yet
> met, the Kalshi first-half BTTS **ask** sits above the realised frequency of
> the event by more than the cost of standing in front of it. Offering YES
> there — **maker, therefore fee-free** — earns a positive net edge.

The direction is deliberately the **sell** side. Phase 1 established the buy
side is uninteresting and the satellites precedent is that the profitable
direction is often the one that cannot be taken.

## 3. Liquidity, measured before the rule was written (outcome-independent)

18 settled markets across `KXMLS1HBTTS`, `KXBRASILEIRO1HBTTS`,
`KXLIGAMX1HBTTS`; 343 in-play minutes in the 30 minutes before close:

* **92%** of minutes carry a **positive YES bid**
* **48%** of minutes carry **non-zero traded volume**

The instrument is executable in-play. This is the fact that made Phase 2
possible without in-play book capture, and it is stated before any edge number
so it cannot later be presented as a result.

## 4. Fill model — pessimistic, and it IS the experiment

**P-017A measured its edge correctly and died at a 2.2% fill rate.** The
standing rule out of that, and out of the 2026-07-30 AFT review, is: *never
propose a variant without a fill estimate first.* So the fill model is not a
detail of this test, it is half of it.

* We rest an offer to **sell YES** at the prevailing best ask, **improving it
  by one tick** where the tick allows.
* **A fill occurs only when the market trades THROUGH the offer** — strictly
  through, never at touch. Same rule as the P-016/P-018 harnesses.
* Volume in the candle is the evidence of a trade; a minute with zero volume
  can never fill.
* Fees: **maker on a `quadratic` series is zero**, verified live on 52 soccer
  series, but the fee call is still made through
  `src.kalshi_fees.fee_per_contract(price, maker=True, series_ticker=...)` and
  never hard-coded to 0. The fixture has drifted five times.

**The fill rate is reported as a headline number, not a footnote**, and a
strategy that cannot fill is dead regardless of its edge.

## 5. Unit of observation: the MATCH

**[outcome-independent]** One fixture is one observation. Minutes within a
match are the same bet repeated — the exact error the AFT paper review scored
as turning a Sharpe of 5.94 into a t of 0.53, and the reason
`P018_DECISION_RULE.md` §2 moved P-018's cluster from ticker to game.

Both halves of a fixture, if both list, are **one cluster**.

## 6. Statistic

**[inherited from P-022 §3 / P-014 / the 2026-08-02 estimator correction]**

* Net **¢/contract**, fee-net at the actual offered price.
* **Equal weight per match** — `edge = mean(x_m)` over matches. **NOT** the
  contract-weighted pooled mean. `bootstrap_weighted` was deleted from
  `quirks_common` on 2026-08-02 precisely because the pooled estimate and the
  gate estimate diverged with sample imbalance, and the divergence is invisible
  until it matters.
* Match-clustered bootstrap, 5,000 resamples, seeded.
* `z = mean / SE`. The all-in number is computed and reported **first**.

## 7. PASS / KILL / NO DECISION

| verdict | condition |
|---|---|
| **PASS** | `z ≥ 2.0` **and** fill rate ≥ 10% **and** M ≥ 40 matches |
| **HARD KILL** | `z ≤ −2.0` at any M ≥ 20 — final, no re-parameterisation |
| **KILL** | fill rate < 2% at M ≥ 40 — unfillable, as P-017A was, regardless of edge |
| **NO DECISION** | anything else |

The `z` thresholds are quoted from documents written before this question
existed. **The 10% fill floor is set above P-017A's fatal 2.2%** and is
outcome-independent — it is a statement about what is worth operating, not
about what the data shows.

## 8. Anti-rationalisation

1. **No mid-flight parameter changes.** The window length, the tick
   improvement, and the strictly-through fill rule are fixed here.
2. **No sub-slicing.** Not "it works in Liga MX", not "only when the score is
   0-0". The all-in number is the number.
3. **A minute is not an observation.** See §5.
4. **The decay being real is not the finding.** Prices obviously fall as the
   half runs out; that is arithmetic. The finding would be that they fall
   *slower than the truth*, net of fills.
5. **A PASS does not authorise a pod.** It authorises a capacity study, because
   the satellites precedent is a surviving mechanic worth $0 of capacity.
