# P-017 — Pre-Registered Decision Rule (Golf Top-N taker, Leg A)

**Status: NOT LOCKED. Written 2026-07-28.**
**Not blind, and this document does not claim to be. Read §0 first.**

---

## 0. What is already known at writing time — stated up front, not buried

**P-017 has a result and no rule. That is the worst possible order of events**,
and the only honest response is to say exactly what has been seen and then
derive every threshold from something that is independent of it.

| known at writing | value | source |
|---|---|---|
| settled tournaments | **1 of 8** | `scripts/p017_checkpoint.py`, run on the droplet 2026-07-28 |
| the tournament | **3MO26** (3M Open) | same |
| its net edge | **−9.89 ¢/ct** | same, after the JDAY scalar re-book |
| its size | **38 positions, 2,319 contracts** | same |
| in flight, not counted | ROC26 — 6 open, 0 settled | same |
| backtest baseline (shipped band) | **+7.14 ¢/ct**, 12 tournaments, CI [+4.30, +9.90] | `backtest_results_extended.json` |
| backtest baseline (10-event cache, reproduced today) | +5.85 ¢/ct contract-weighted; **+6.38 ¢/ct** equal-weight across tournaments | `backtest_golf.leg_pre_tourn_yes` re-run |

**Every threshold below carries a provenance line, and each is one of:**

* **[inherited]** — copied verbatim from `P022_DECISION_RULE.md` §4 or
  `tennis_research/P015_DECISION_RULE.md`, both locked before either pod had
  an observation;
* **[registered]** — already in `manager/registry.yaml`'s P-017 gate, written
  at the 2026-07-26 re-scope, before 3MO26 settled;
* **[backtest-derived]** — solved from the **backtest's** effect and
  dispersion, never from 3MO26.

**No threshold is derived from the −9.89 ¢.** A reader who has never seen that
number can re-derive this entire document from the four sources named above.
That is the test this rule is written to pass.

---

## 1. The claim being tested

> Buying cheap YES on Kalshi PGA top-N props 4–10 days before a tournament, in
> the 8–45 ¢ band, earns a positive **taker-fee-net** edge, measured per
> contract, with each tournament counting once.

## 2. Unit of observation: the TOURNAMENT

**[registered]** — the gate's own `decision` text, 2026-07-26:
*"treating EACH TOURNAMENT as one observation (not each bet — bets within a
tournament are correlated through one leaderboard; per-bet weighting is how
P-017M produced a phantom +9.1 ¢/ct)."*

**2,319 contracts in one tournament is ONE observation, not 2,319.**
Contract-weighted **within** a tournament, equal weight **across**.

**Effective n today: 1.** T = 8 is the threshold, so the gate is 1/8 complete.

## 3. Test statistic

**[inherited from P-022 §3]**

* Net **¢/contract**, fee-net **at the actual traded price**, via
  `src.kalshi_fees.fee_per_contract(price, maker=False)`. Never a hard-coded
  rate.
* **The taker fee is real here and this is the one place in golf where it
  is.** Leg A pays ≈ **1.04–1.20 ¢/ct** (0.07·P·(1−P) at P = 0.08–0.45).
  Everywhere else in this fund's golf work the maker fee is zero because the
  series are `quadratic`; that does not apply to a taker.
* Tournament-clustered bootstrap, 5,000 resamples, seeded, resampling
  **tournaments** with replacement.
* `z = mean / SE`, SE from the clustered bootstrap. **`z` is undefined at
  T = 1** and the reader says so rather than printing a number.
* The all-in number is computed and reported first, before any split.

## 4. PASS / KILL / NO DECISION

| verdict | condition | provenance |
|---|---|---|
| **PASS** | `z ≥ 2.0` **and** mean **≥ +3.57 ¢/ct** **and** T ≥ 8 | `z` **[inherited]**; +3.57 ¢ **[registered]** = half the +7.14 ¢ backtest baseline, which is the gate's own question — *"Does forward net CLV hold above half the backtest baseline?"* — written 2026-07-26; T = 8 **[registered]** |
| **HARD KILL** | `z ≤ −2.0` at any **T ≥ 4** | `z` **[inherited]**; T ≥ 4 **[backtest-derived]**, see below |
| **KILL at T** | T = 8 reached and mean **< 0** with `z ≤ −1.0` | **[backtest-derived]**, see §6 |
| **NO DECISION** | anything else. Pod continues in paper. | |

**Why the hard kill needs T ≥ 4 [backtest-derived].** With the backtest's
tournament SD of **5.27 ¢**, `z ≤ −2.0` at T = 4 requires a running mean
**≤ −5.27 ¢/ct** — which is *below the backtest's own worst single
tournament* (**−3.71 ¢**, TRAV26). So at T = 4 the hard kill cannot fire on
any sequence the backtest itself could plausibly have produced. At T = 3 it
would require ≤ −6.09 ¢ and at T = 2 the SE is barely estimable. **T ≥ 4 is
the smallest floor at which a hard kill means something.** No part of this
uses 3MO26.

**A single tournament cannot kill this pod, and that is deliberate.** At
T = 1 there is no SE, so `z` does not exist. The rule is silent at T = 1 by
construction, which is the correct response to the situation it was written
into.

## 5. Power — the re-solve the prompt asks for

**[backtest-derived]** From the reproduced 10-tournament distribution:
mean **+6.38 ¢** equal-weight, **SD 5.27 ¢**, **9 of 10 positive**, range
−3.71 ¢ (TRAV26) to +16.07 ¢ (THCCBN26).

| T | SE | smallest effect detectable at `z ≥ 2` |
|---:|---:|---:|
| 3 | 3.05 ¢ | +6.09 ¢ |
| 5 | 2.36 ¢ | +4.72 ¢ |
| **8** | **1.86 ¢** | **+3.73 ¢** |
| 12 | 1.52 ¢ | +3.05 ¢ |
| 16 | 1.32 ¢ | +2.64 ¢ |

**T required to detect the backtest's own +6.38 ¢ at `z ≥ 2`: T = 3.**

> **T = 8 is therefore already conservative — nearly three times what the
> power criterion demands — and it does NOT need raising.** It is left
> exactly where it is. **T is not lowered under any circumstances** (this
> task's stop rule, and §7.6 below).

**The number worth Sam's attention is the coincidence in the last column.**
T = 8 detects **+3.73 ¢**, and the gate's registered PASS bar is **+3.57 ¢**
(half the baseline). Those were set independently — one by a power
calculation done today, one by a question written on 2026-07-26 — and they
agree to within 0.16 ¢. **T = 8 is exactly the sample size at which the gate's
own question becomes answerable.** That is a good sign about the original
design and it is worth recording.

**The caveat, and it is not small.** All of the above uses the *backtest's*
dispersion. If live tournament-to-tournament dispersion is larger — and the
one live observation sits far outside the backtest's range, which is at least
consistent with that — the table degrades fast:

| if live SD is… | T = 8 detects | T = 16 detects | T = 24 detects |
|---|---:|---:|---:|
| 5.27 ¢ (backtest) | +3.73 ¢ | +2.64 ¢ | +2.15 ¢ |
| **10.5 ¢ (2×)** | **+7.46 ¢** | +5.27 ¢ | +4.31 ¢ |

At 2× dispersion, **T = 8 can only detect an effect roughly equal to the full
backtest baseline** — it could not distinguish "half the baseline" from zero.
**This is a pre-registered trigger, not a hedge:** see §8.

## 6. The question this rule must not dodge

> **Is one settled tournament at −9.89 ¢ against a +6.8 ¢ backtest inside the
> expected distribution, or not?**

**Computed, not asserted.** Against the reproduced 10-tournament distribution
(mean +6.38 ¢, SD 5.27 ¢), treating 3MO26 as one new draw from the same
population, using a *predictive* t with 9 degrees of freedom (which inflates
the SD by √(1+1/n) for the uncertainty in the mean, and has fatter tails than
a normal — both corrections make the answer *less* alarming):

| | |
|---|---|
| **P(a new tournament ≤ −9.89 ¢)** | **0.0082 — about 1 in 122** |
| normal approximation, for comparison | 0.0010 — 1 in 982 |
| empirically | **0 of 10** backtest tournaments were ≤ −9.89 ¢ |
| the backtest's own worst | **−3.71 ¢** (TRAV26) — 3MO26 is **2.7× worse** |

**Read this as it is written.** ≈1 in 122 is uncommon, not astronomical, and
it is **one observation**. This is not a verdict and §4 gives it no weight:
`z` does not exist at T = 1 and the rule is deliberately silent.

**But it is not an unremarkable draw either, and it would be dishonest to
present it as one.** 3MO26 sits outside the entire observed backtest range,
and the honest summary is: *the backtest distribution does not comfortably
contain this observation, on a sample of one, and the next three tournaments
decide whether that was a tail draw or a sign that the backtest does not
describe live.* Both readings remain fully open.

**Three specific reasons the two numbers may not be comparable at all**, each
of which would need its own check before anyone concludes anything:

1. The reproduced backtest uses the **10-event** local candle cache
   (+5.85 ¢ contract-weighted); the **12-event** published figure is +7.14 ¢
   from a refreshed pull whose data is not in this tree. The dispersion above
   is therefore the 10-event dispersion.
2. The backtest is **contract-equal within a tournament** (each bet one
   contract); 3MO26's −9.89 ¢ is contract-weighted over 2,319 contracts on 38
   positions. A tournament whose losses concentrate in the largest positions
   scores worse under weighting than under counting.
3. 3MO26 was P-017's **first live event**, run through the
   `max_event_exposure_pct` cap added on 2026-07-25 and through a log rotation
   that orphaned 16 positions before they were recovered. Neither is in the
   backtest.

**None of these is an excuse and none is offered as one.** They are the three
things that must be checked before −9.89 ¢ is treated as an estimate of live
edge — and checking them is not authorised by this document either.

## 7. Admissibility

1. **A tournament counts only when it has ≥ 1 resolved position and ZERO
   still-open positions.** **[registered]** Golf top-N settles in two waves —
   missed cuts at the Friday cut, the rest days later — so a partially
   resolved event is not an observation. ROC26 (6 open, 0 settled) does not
   count today.
2. **`result="scalar"` is a PARTIAL PAYOUT at `settlement_value_dollars`,
   never a void, never $0.** **[inherited]** Two regimes produce it and both
   settle the same way; booking one as a void deletes a real loss.
3. **VOIDs are excluded** (no risk taken). **[registered]**
4. **The JDAY precedent — the gate keys off `action`, not `outcome`.**
   **[registered, and the reason it is here]** The 2026-07-27 correction moved
   the tournament from −10.08 ¢ to −9.89 ¢ and took **two passes**, because
   the first pass fixed a field the reader does not read. **RULE: any
   correction to a settled row must be verified by re-running
   `scripts/p017_checkpoint.py` and observing the gate number move. A
   correction that does not move the reader's number has not been applied,
   whatever the log says.**
5. **A row with a missing or unparseable fill price is EXCLUDED, never
   defaulted.** **[inherited]** P-014 wrote `null` fill prices for four
   months; the backfill only worked because nothing had defaulted them to
   zero.
6. **On cap breaches: P-017 HAS NO EXCLUSION CLAUSE, and this rule does not
   invent one.** P-022 §7 excludes a tournament from T if any collateral cap
   is breached. **P-017 has no equivalent** — it has caps
   (`max_open_positions: 30`, `max_event_exposure_pct: 0.08`, added
   2026-07-25 after one basket put 16% of bankroll on a single correlated
   event) but **no clause saying a breached tournament is excluded, and no
   code that records a breach.** Writing one now, after the first tournament
   has settled negative, would be indistinguishable from creating an
   escape hatch. **Recorded as a gap for Sam (§9), not filled.**

## 8. What happens at T = 8 — pre-registered, so it is not renegotiated in the moment

**[backtest-derived]**

* **PASS** → the pod is a candidate for real money. This document does not
  authorise that; it authorises the *verdict*.
* **KILL** → stop. Under §9.6 a kill is not a prompt to re-tune the band.
* **NO DECISION at T = 8** — the case §5's caveat makes likely if live
  dispersion is larger than the backtest's:
  * **The measured live SD is computed and reported at T = 8.** If it exceeds
    **8.0 ¢** — 1.5× the backtest's 5.27 ¢, the point at which T = 8's
    detectable effect (+5.66 ¢) exceeds the backtest baseline's own lower CI
    bound (+4.30 ¢) — **T extends once to 16, and only once.**
  * That extension is **pre-registered here, before T = 2**, and is the same
    move P-022's Amendment 1 made at T = 0. Extending a threshold before
    evidence accumulates is conservative; extending it after seeing an
    unfavourable result is not, which is exactly why it is written down now.
  * If the measured SD is at or below 8.0 ¢ and the verdict is still NO
    DECISION at T = 8, **the pod stops and the verdict is NO DECISION** — it
    does not drift on indefinitely. An unfalsifiable strategy is not promoted
    by default (**[inherited]**, P-022 §8.4).

## 9. Anti-rationalisation

**[inherited verbatim from P-022 §8]**

1. **No mid-flight parameter changes** — band, days window, series set, caps.
   Any change resets T to 0 under a new pod ID.
2. **No cherry-picking sub-slices.** Not "it works if you drop the majors",
   not "TOP20 only". The all-in number is the number.
3. **Scalar settlements counted at realised value.**
4. **Silence is not confirmation.**
5. **A HARD KILL is final** — it does not trigger "try band 0.10–0.35".
6. **T is never lowered.** Not for any reason.
7. **No threshold in this document may be revised after a tournament
   settles.** §8's single extension is the only pre-authorised movement and
   its trigger is a *dispersion*, not an *edge*.

## 10. The sanctioned reader

`scripts/p017_checkpoint.py` — **[registered]**, `source: p017_checkpoint`.

Verified today on the droplet against the file the pod actually writes:

```
P-017 gate — settled tournaments: 1 of 8
  verdict: NO DECISION — 1 of 8 settled tournaments
  edge   : -9.89c/ct   (se needs T>=2)
  SETTLED:   3MO26  -9.89c/ct  38 positions  2319 contracts
  IN FLIGHT: ROC26  6 open, 0 settled
```

It separates SETTLED from IN FLIGHT, it refuses to compute an SE at T = 1
rather than printing a number, and locally — where the trade log is absent —
it returns `0 of 8` and explains itself instead of failing open.

**`scripts/check_gate_instrumentation.py` against P-017, run today:**

> **P-017 FAIL (1 of 9 checks failing)** — and the one failure is
> **`4_decision_rule`: "no rule document anywhere, no rule_document, no
> rule_status."**

**This document is that missing artifact.** P-017 passes the other eight
checks and was the audit's *readability* control. Once the registry gate
NAMES this file — `rule_document: research/P017_DECISION_RULE.md` — check 4
should pass. Naming it is a registry edit, not a threshold change, and is
listed in §11.

## 11. What is owed, and by whom

**Sam:**

1. **Accept or amend this rule.** It is NOT LOCKED. Once locked, §9.7 binds.
2. **The §7.6 gap:** P-017 has caps but **no cap-breach exclusion clause and
   no breach recording**, where P-022 has both. Adding one is a real decision
   with a real cost — it can only ever *remove* tournaments from a gate that
   needs 8 of them — and it must not be decided by whoever is holding the
   worst tournament at the time.
3. **§8's SD trigger (8.0 ¢)** — pre-registered here at T = 1. Confirm it now,
   while nobody knows what the SD will be.

**Not Sam — mechanical, and not done here because this task's stop rule
forbids changing anything:**

4. Add `rule_document: research/P017_DECISION_RULE.md` and
   `rule_status: WRITTEN` to the P-017 gate block in `manager/registry.yaml`,
   which closes `4_decision_rule`.
5. Re-run `backtest_golf.py` against a refreshed pull so the 12-event
   dispersion — the one the +7.14 ¢ baseline actually rests on — is available,
   rather than the 10-event dispersion §5 and §6 had to use.

**Nothing in this document changes a threshold, and it reaches no verdict.**
The verdict at T = 1 is, and remains, **NO DECISION**.
