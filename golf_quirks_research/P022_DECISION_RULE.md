# P-022 — Pre-Registered Decision Rule

**LOCKED 2026-07-26, before the pod exists and before a single contract
has been quoted. Do not renegotiate.**

> ## AMENDMENT 1 — 2026-07-28: decision threshold T = 14 → **T = 24**
>
> **Authorised by Sam. Made at T = 0, with zero forward observations in
> existence** — the only moment this change can be made without touching
> evidence.
>
> **What changed:** §4's decision point and §5's `T = 14`. Everything else —
> band, offset, window, series set, every §7 cap, the HARD KILL at z ≤ −2.0,
> and the single-extension structure — is **untouched**.
>
> **Why, and why this is not renegotiation.** §5 fixes the *criterion* as
> "the smallest sample with **90% power against the effect actually
> measured**", and then solves it. T = 14 was `ceil(13.3)`, the answer for the
> Phase-2 estimate of **+3.4¢/ct**. The 2026-07-28 widening
> (`REPORT_P022_Widened_2026-07.md`, pre-declared and Sam-approved) revised the
> pooled estimate to **+2.57¢/ct [+0.1, +4.5]**. Re-solving the *same criterion*
> with the *same* σ = 3.781¢:
>
> ```
> T = ((2.0 + 1.2816) · 3.781 / 2.57)²  =  23.3   →  ceil = 24
> ```
>
> **§5's own table already contained this row**: `24 | 3.30 | 90%` against the
> ¾-effect column. The criterion is unchanged; only its input moved. At T = 14
> the power against +2.57¢ would be **71%** — below the 90% bar §5 sets, and
> close to the 71%-at-T=8 look §5 explicitly forbids as "noise".
>
> **The direction is conservative.** This raises the bar: more evidence is now
> required before a PASS. It is the opposite of the P-013 failure this document
> was written to prevent, which was criteria *loosened* after the fact.
>
> **§8.1 is not triggered.** It resets T for changes to "offset, band, window,
> series set, caps" — the *quoting* parameters. The decision threshold is not
> among them, the pod's behaviour is byte-identical, and T is 0 regardless.
>
> **OPEN, and deliberately not decided here: `T = 40` no longer means what §5
> says it means.** Its stated rationale is "80% power against **half** the
> measured effect". Half of +2.57¢ is +1.29¢, and the same solve gives
> **T ≈ 70**, not 40. At T = 40 the power against half the *revised* effect is
> ~53%, not 80%. Raising the extension is a second decision and it has not been
> made — `MIN_T_EXTENSION` stays 40, now **inconsistent with its own
> rationale**, until Sam rules on it.

> ## AMENDMENT 2 — 2026-08-02: T = 24 → **T = 33**, extension 40 → **98**, new §4b clustering guard
>
> **Authorised by Sam, 2026-08-02, in writing.** Full derivation and the
> rejected alternatives: `golf_quirks_research/DRAFT_AMENDMENT_2_2026-08-02.md`.
>
> **⚠ Made at T = 2, NOT at T = 0.** Amendment 1 could claim it touched no
> evidence. **This one cannot.** Two forward observations existed when it was
> made, and they were **negative** (edge −2.18¢/ct, 1/2 positive, z = −0.44).
> The protection is direction, not timing: **this amendment RAISES every
> threshold** (24 → 33, 40 → 98). It makes a PASS harder at a moment when the
> pod is not passing, which is the opposite of the P-013 failure. The
> friendlier reading available from the same correction — `T = 19`, implied by
> the corrected *published* sample — was **rejected on these grounds**, despite
> being defensible arithmetic. A gate may be tightened with evidence in hand;
> it may not be loosened.
>
> **What changed:** §4's decision point (24 → 33), §4a's extension (40 → 98),
> §5's arithmetic basis, and a new §4b. Everything else — band, offset, window,
> series set, every §7 cap, `z ≥ 2.0` to pass, the HARD KILL at z ≤ −2.0, and
> the single-extension structure — is **untouched**. §8.1 is not triggered: no
> quoting parameter moves and the pod's behaviour is byte-identical.
>
> **Why. `T = 24`'s stated justification was void.** It solved §5 with
> `d = +2.57¢` and `σ = 3.781¢`. Both came from the **pooled** estimator, which
> `quirks_common` reported as its headline until it was fixed on 2026-08-02
> (`e47d8e9`). `d` should have been the rule's own §2/§3 statistic (**+1.45¢**
> on that sample), and `σ` was back-derived from a bootstrap CI that was the
> *pooled* statistic's interval and so too narrow — measured directly it is
> **10.44¢**. Both errors understate the required sample. Re-solving §5 as
> written gives **T = 556**, and shows the live gate had **9.3% power** against
> the effect it was supposedly powered for.
>
> **But 556 is not a real requirement — it is §5's formula failing.**
> `z = d√T/σ` and `Φ(z − 2.0)` are normal-theory. The per-tournament statistic
> is skewed **−2.60** (median +5.00¢ vs mean +1.45¢) because the payoff is a
> rare large loss: across 183 filled markets there are **7** adverse events
> (3.83%), each costing **12.1×** the credit — −$159.50 against +$110.12 total.
> One of them lands in `KIN26`, which aggregates to −38.49¢/ct and single-
> handedly doubles σ. It cannot be screened out (53.7 filled contracts, so it
> survives any contract filter) and stricter screens make it worse: at ≥50
> contracts the mean goes to **−0.06¢**.
>
> **The unit that carries the information is the filled market, not the
> tournament.** Estimating the price of the tail directly:
>
> ```
> edge = q − p = 0.0766 − 7/183 = +3.83¢    SE 1.42¢    z = 2.70
> 95% CI [+1.05, +6.61]¢
> T for 90% power = 270 filled markets (183 in hand)
>                 ≈ 33 tournaments at the observed 8.3 filled markets/tournament
> ```
>
> This agrees with the corrected *published* equal-weight figure (+3.80¢) by an
> independent route, and it is the instrument
> `analyze_p022_added_block.py` already names **"THE RIGHT TEST"**, against the
> clustered permutation on a thin block which it calls **"THE WRONG TEST … an
> artifact"**.
>
> **Relaxing tournament clustering for this quantity is conditional, and §4b is
> the condition.** Clustering guards against correlated adverse events. In 183
> filled markets that correlation is **absent** — the 7 adverse events fall in
> **7 distinct tournaments, exactly one each**. Seven events cannot *prove*
> independence, so it is monitored rather than assumed.
>
> **Resolves the item Amendment 1 left open.** `MIN_T_EXTENSION` is no longer
> inconsistent with its own rationale. Solving the SAME criterion — "80% power
> against **half** the measured effect" — on the market-level basis:
>
> ```
> half-effect = +1.92¢     n = (2.0 + 0.8416)² · p(1−p) / half²  =  808 filled
>                          808 / 8.32 filled per tournament       =  T = 98
> ```
>
> **A first draft of this amendment asserted 55 without solving it. That was
> wrong**: at T = 55 the power against half-effect is **55.5%**, which
> reproduces almost exactly the ~53% defect Amendment 1 flagged at T = 40 —
> the very thing this amendment claims to resolve. Corrected to 98 before
> authorisation took effect. The number is large because half of a +3.83¢ edge
> is a small effect against a 3.83% event rate; that is the honest cost of the
> extension, not a reason to round it down.

This document exists because P-013 lost **$2,094** while its criteria
were still being decided after the fact. P-015's rule was locked in
advance for the same reason. P-022 is written down before it can trade
at all — the pod, its config block, and its runner do not exist yet, and
this rule is a precondition of building them, not a follow-up to it.

`scripts/p022_checkpoint.py` is the **only sanctioned reader** of P-022's
results. A P&L chart, a dashboard tile, an eyeballed jsonl tail, or a
per-name breakdown is not a verdict. If the checkpoint script has not
printed it, it has not happened.

---

## 1. The claim being tested

From `REPORT_Golf_Quirks_Phase2_P022_2026-07.md` §2 (tick-print maker-fill
replay, pessimistic strictly-through fills, measured adverse selection):

> Selling YES on cheap golf round-leader names (12h pre-round anchor in
> [0.03, 0.12]) at `anchor + 0.02`, posted at **H = 12–24h pre-round**,
> returned **+3.4¢/contract net**, tournament-clustered 95% CI
> **[+1.7, +5.1]**, **16 of 19 tournaments positive**, robust to
> leave-one-out. Fill rate 0.47 (171 of 364 posted names, 4,061
> contracts). Maker fee is **zero** — all 13 round-leader series are
> `quadratic`.

The mechanism is a certified contract term, not a probability
disagreement: a tie for the round lead **splits the payout $1/n**
(PGAROUNDLEADER, quoted verbatim in `REPORT_Golf_Quirks_2026-07.md`).
30% of golf rounds tie, producing a 37% conditional payout haircut. This
is a settlement-mechanic hypothesis — the category that is 3-for-3 in
this operation, against 0-for-9 for behavioural and better-information
stories.

**Prior, stated honestly.** Phase 2 is a backtest with a known
selection risk (see §6) and a one-month effective sample. Regression
toward zero is the expected outcome. We are testing a
plausible-and-well-constructed effect, not deploying a known one.

---

## 2. Unit of observation: the TOURNAMENT

**One tournament = one observation. Always. No exceptions.**

Names within a tournament are correlated through the leaderboard: one
hot wave, one multi-way tie, or one faded name running away with the
round moves every position in that event together. Treating each
contract or each name as an independent observation is exactly how
P-017M produced a phantom **+9.1¢/ct** that corrected to +3.34¢ once
weighting was fixed.

- Within a tournament, the per-tournament statistic is the
  **contract-weighted** mean net ¢/contract across all filled contracts
  in that tournament.
- Across tournaments, each tournament enters the aggregate with
  **equal weight**. A 900-contract tournament and a 40-contract
  tournament count the same.

A "tournament" is one `event_ticker` **per round**: `KXPGAR1LEAD-XXX26`
and `KXPGAR2LEAD-XXX26` are two rounds of one golf event and are
**one observation**, pooled, not two. Rounds of the same event share the
same field and the same leaderboard state.

---

## 3. Test statistic

For each filled contract (P-022 sells YES / makes NO):

```
settlement_value = 1.00                     if result == "yes"
                 = 0.00                     if result == "no"
                 = settlement_value_dollars if result == "scalar"   # $1/n dead heat

pnl_per_contract = quote_px - settlement_value - maker_fee(series, quote_px)
```

`maker_fee` comes from `src.kalshi_fees.fee_per_contract` **with the
series ticker passed** — it is expected to be 0 on every `*LEAD` series,
and the checkpoint asserts that rather than assuming it
(`python3 -m scripts.p022_checkpoint --check-fees`). The
`_SERIES_MAKER_FEE` table has drifted twice.

> **OPEN DEFECT, found 2026-07-26 while writing this rule.**
> `--check-fees` currently reports **DRIFTED**. `_SERIES_MAKER_FEE`
> contains only `KXPGAR1LEAD` / `R2LEAD` / `R3LEAD`, so
> `KXDPWORLDTOUR*LEAD`, `KXLIV*LEAD`, `KXLPGA*LEAD` and
> `KXCHAMPTOURR1LEAD` fall through to the charging default and are
> billed 0.0175·P·(1−P) that Kalshi does not charge. Verified against
> `GET /series/?category=Sports&limit=200`: **all 63 `*LEAD` series
> return `fee_type=quadratic`, i.e. maker-fee-free.** At P = 0.08 the
> phantom fee is 0.129¢/ct — small, but it is a systematic drag on the
> non-PGA tours, which supplied the majority of Phase 2's 19
> tournaments. **This must be fixed before T starts counting**, or the
> forward estimate is biased downward against a rule calibrated on a
> fee-free backtest. Fixing it is a `src/kalshi_fees.py` change with its
> own test, not a P-022 change, and it does not alter this rule.

Then, with `T` tournaments:

```
x_t  = contract-weighted mean pnl_per_contract within tournament t   (¢/ct)
edge = mean(x_t)                     # equal weight per tournament
se   = stdev(x_t) / sqrt(T)
z    = edge / se
```

VOIDs (a market that never resolved) are excluded — no risk was taken.
**Scalar settlements are NOT voids and are never excluded**; they are the
outcome the pod exists to harvest, and the settler fix of 2026-07-26
(`src/kalshi_golf_settler.py`) is what makes them countable.

---

## 4. Decision schedule

| Tournaments (T) | Rule |
|---|---|
| **any** | **HARD KILL** if `z ≤ −2.0` (significantly negative). Stop immediately; no extension, no re-parameterisation. |
| **T < 33** | **NO DECISION** — underpowered. Do not act, do not raise caps, do not report a verdict. *(was T < 14, then T < 24; see Amendments 1 and 2)* |
| **T ≥ 33** | **KILL** if `edge ≤ 0`. |
| **T ≥ 33** | **CONTINUE (single extension)** if `edge > 0` and `z < 2.0` — see §4a. |
| **T ≥ 33** | **PASS** if `edge > 0` and `z ≥ 2.0`. |
| **T ≥ 98** | **KILL** if `edge > 0` and `z < 2.0`. The extension is spent. *(and ≥808 filled markets, same both-gates rule)* |
| **any** | **§4b clustering guard** — if adverse events per affected tournament > 1.5, the market-level basis of Amendment 2 is void; revert to the tournament-level threshold computed at that time before reading any verdict. |

**T = 33 is reached only when BOTH `T ≥ 33` and `filled markets ≥ 270` hold**
(whichever lands later). The threshold was derived on filled markets; the
tournament count is the convenient proxy at the observed 8.3 filled
markets/tournament, and the proxy must not be allowed to arrive early if the
pod fills more thinly than the backtest did.

"PASS" means the forward test replicated. It authorises **more paper
allocation within the caps in §7 and a written promotion proposal** — it
does **not** authorise live money. Live is a separate decision by Sam,
against the fund's live-promotion bar, and nothing in this document
grants it.

### 4a. The marginal-result rule, registered in advance

Modelled on P-016's: **one extension, at unchanged parameters.**

If at T = 33 the estimate is positive but not separable from zero
(`0 < edge`, `z < 2.0`), the pod continues to **T = 98 and no further**,
with `offset`, the H = 12–24h posting window, the [0.03, 0.12] anchor
band, the series set, and every cap in §7 **byte-identical**. At T = 98
the verdict is PASS (z ≥ 2.0) or **KILL**. There is no second extension
and no "it was close, let's give it one more month."
*(was T = 24 → 40; see Amendment 2, which also resolves the inconsistency
Amendment 1 flagged and left open.)*

### 4b. Clustering guard — the falsifier for Amendment 2's basis

Amendment 2 derives its thresholds from a **market-level** adverse-event rate,
which is legitimate only while adverse events do not cluster within
tournaments. At the time it was written they did not: 7 adverse events in 7
distinct tournaments, exactly 1.00 per affected tournament.

**At every checkpoint, report `adverse events / affected tournaments`.** If
that ratio **exceeds 1.5**, the market-level basis is void: the thresholds in
§4 revert to the tournament-level requirement computed from the data in hand
at that moment, and **no verdict may be read until it has been recomputed**.

This is a falsifier, not a formality. Seven events cannot prove independence —
they can only fail to contradict it. The guard is what makes relying on it
honest.

Changing any parameter mid-flight is a **new hypothesis (P-022b)** with
its own registration and its own counter reset to T = 0. Tuning on live
results is the same error as fitting the original slice.

---

## 5. Why 14 and 40 — the arithmetic

The Phase-2 tournament-clustered CI is the input. From
`[+1.7, +5.1]` at T = 19:

```
SE(19)  = (5.1 - 1.7) / 2 / 1.95996        = 0.867 ¢/ct
sigma   = SE(19) * sqrt(19)                = 3.781 ¢/ct   (between-tournament SD)
z(T, d) = d * sqrt(T) / sigma
power   = Phi( z(T, d) - 2.0 )             (one-sided, critical z = 2.0)
```

| T | z if effect = +3.4¢ (full) | power | z if +2.55¢ (¾) | power | z if +1.7¢ (half) | power |
|---:|---:|---:|---:|---:|---:|---:|
| 6 | 2.20 | 58% | 1.65 | 36% | 1.10 | 18% |
| 8 | 2.54 | 71% | 1.91 | 46% | 1.27 | 23% |
| 10 | 2.84 | 80% | 2.13 | 55% | 1.42 | 28% |
| **14** | **3.37** | **91%** | 2.53 | 70% | 1.68 | 37% |
| 19 | 3.92 | 97% | 2.94 | 83% | 1.96 | 48% |
| 24 | 4.41 | 99% | 3.30 | 90% | 2.20 | 58% |
| **40** | 5.69 | 100% | 4.27 | 99% | **2.84** | **80%** |
| 60 | 6.97 | 100% | 5.22 | 100% | 3.48 | 93% |

Solving `T = ((2.0 + z_power) * sigma / d)^2`:

| target | vs +3.4¢ | vs +2.55¢ | vs +1.7¢ |
|---|---:|---:|---:|
| 80% power | 10.0 | 17.8 | 39.9 |
| **90% power** | **13.3** | 23.7 | 53.3 |

> **SUPERSEDED AS THE BASIS BY AMENDMENT 2 (2026-08-02).** Everything in
> this section is normal-theory (`z = d√T/σ`, `power = Φ(z − 2.0)`) and the
> per-tournament statistic is skewed −2.60, so the solve below is not a
> reliable requirement in either direction. It is retained as the record of
> how T = 14 and T = 24 were set. The live thresholds (T = 33 / 55) come from
> the market-level adverse-rate derivation in Amendment 2. **The σ = 3.781¢
> used throughout this section was back-derived from a POOLED bootstrap CI
> and is too narrow; measured directly it is 5.04¢ (published) / 10.44¢
> (widened).**

- **T = 14** is `ceil(13.3)`: the smallest sample with **90% power against
  the effect actually measured**. Below it a null result is
  uninformative, which is why T < 14 is NO DECISION rather than a weak
  KILL. At T = 8 — a tempting "one month in" look — power is only 71%
  against the full effect and 23% against half of it; that look is noise
  and is forbidden.
- **T = 40** is `ceil(39.9)`: 80% power against **half** the measured
  effect, the realistic regression case. It is the point past which
  continuing to run a marginal pod stops buying information at a
  sensible rate — going from 80% to 90% power at half-effect costs
  another 13 tournaments.
- Neither number is round, and neither was chosen after seeing a forward
  result.

**Cadence. ~~~15–19 qualifying tournaments per month~~ — CORRECTED
2026-07-28 to a MEASURED 13.7/month (3.16 event codes/week).** Phase 2
covered 19 tournaments in roughly one month across PGA / DP World / LIV /
LPGA / Champions round-leader series, and this section inferred ~15–19
per month from that. Counting listed event codes directly across all 13
series gives **3.16 per week = 13.7 per month** — the original figure was
~25% high at its low end and ~40% high at its top
(`research/P022_RULE_DECISIONS_2026-07-29.md` §4.3). It is an **upper
bound on gate throughput**: a listed tournament only becomes an
observation if the pod quotes it, fills, and it settles.
At the measured rate T = 14 lands ~4.4 weeks after the first quote and
T = 40 lands ~3 months. If the paper runner quotes a narrower set or misses
posting windows, the calendar stretches — and **silence is not
confirmation** (§8.4).

---

## 6. Sample caveat, recorded up front

**Kalshi's public trade history reaches back only ~1 month.** Phase 2's
19 tournaments are therefore all late-June → July 2026. Consequences,
accepted now rather than argued later:

1. There is **no temporal holdout** in the backtest. The forward test
   *is* the replication.
2. The sample is one seasonal slice. Summer PGA fields, weather, and
   scoring conditions are not the whole year. A forward result that
   disagrees with Phase 2 is **evidence about the effect**, not evidence
   that the forward window was unrepresentative.
3. **If forward disagrees with backtest, the backtest loses.** The rule
   in §4 is not renegotiated, reweighted, or re-run on a
   "more comparable" subset. Phase 2 does not get a vote at the
   checkpoint.

---

## 7. Mandatory collateral caps — these are GATE CONDITIONS

The tail is real: Phase 2 showed losses concentrate in the tournaments
where a faded name actually leads (all 10 leaders in the universe filled
the fade), and the worst single-name outcome is −94¢/contract. The live
proof that per-event caps are mandatory is **2026-07-25**, when one
P-017 golf basket put 16% of bankroll on a single correlated event, all
of it missed, and the −$171 loss tripped the 5% daily-loss halt and
wedged the engine.

| cap | limit | scope |
|---|---|---|
| **per-name** | **≤ 0.5% of bankroll** | collateral at risk on one strike |
| **per-tournament** | **≤ 5% of bankroll** | all names, all rounds, one golf event |
| **aggregate** | **≤ 15% of bankroll** | all live P-022 collateral, all tournaments |
| **posting window** | **quote only at H ≈ 12–24h pre-round** | no new quotes inside 12h; existing quotes rest to close |

Sizing is on **collateral at risk**, never contract count — a fade sold
at 5¢ posts 95¢ of collateral per contract.

These are **conditions of the gate, not tuning knobs**:

- A tournament run with any cap breached is **excluded from T** and from
  the aggregate. It cannot contribute a favourable observation.
- Raising any cap resets T to 0 and requires re-registration
  (**P-022b**). A cap increase is a new strategy, because the tail it
  bounds is the dominant risk.
- The posting window is a cap too. Phase 2 showed H = 6h collapses to
  marginal (+0.5 to +1.7¢, CI includes zero) because the through-fills
  by then are almost purely eventual leaders. Quotes placed inside 12h
  are **excluded from T**.
- Before P-022 can quote, it must be wired into `AggregateRiskGuard`
  with **reservations** (`reserve_trade`), not post-cycle registration.
  P-017 places its whole book in one scan and the guard rejected nothing
  until reservations were added; P-022 posts a whole tournament's names
  in one window and has the identical failure mode.

---

## 8. Anti-rationalisation clauses

1. **No mid-flight parameter changes.** Offset, band, window, series
   set, caps. Any change resets T to 0 under a new pod ID.
2. **No cherry-picking sub-slices.** The verdict is computed on every
   filled P-022 contract in every counted tournament. "It works if you
   drop LIV" / "if you exclude the majors" / "if you only count R1" is
   not a result — it is a new hypothesis needing its own registration.
   The checkpoint script prints the all-in number first and refuses to
   filter.
3. **Scalar settlements are counted, at their realised value.** They are
   partial payouts, not voids. Excluding them, or booking them at $0,
   deletes the entire mechanism under test. This was a live bug in
   `KalshiGolfSettler` until 2026-07-26.
4. **Silence is not confirmation.** If the pod never reaches T = 33, the
   verdict stays NO DECISION and it stays in paper indefinitely. An
   unfalsifiable strategy is not promoted by default.
5. **No CLV substitute.** Round-leader markets have no sharp external
   closing line and the fade's edge is a settlement mechanic, not a
   price disagreement. Realised settlement is the only validator. Do not
   import a CLV argument to promote early.
6. **A HARD KILL is final.** `z ≤ −2.0` stops the pod. It does not
   trigger "let's try offset +0.04."
7. **Capacity is small and that is not a reason to loosen anything.**
   Phase 2 projects ~$140 P&L on ~$3.8k collateral per month at 25
   contracts/name. If that is judged too small to bother with, the
   correct action is to not build the pod — not to raise the caps.

---

## 9. What is authorised right now

- **Nothing trades.** As of this locking, `src/round_leader_fade_maker.py`,
  `scripts/run_round_leader_fade.py`, the `pods.P-022` config block, and
  any systemd unit **do not exist** and are not authorised by this
  document.
- The scalar settler fix (`src/kalshi_golf_settler.py`, 2026-07-26) and
  this rule are the two preconditions. Building the pod is a separate
  approval from Sam.
- When the pod is built, its first action must be to append its own
  registration line here confirming the parameters it shipped with match
  §7, and to leave this file otherwise unchanged.

---

## 10. Reading the result

```bash
python3 -m scripts.p022_checkpoint            # human-readable verdict
python3 -m scripts.p022_checkpoint --json     # machine-readable
```

That script implements §3 and §4 and nothing else. It is the only
sanctioned reader. Reference: this file.

---

## 11. Correction to §9, and the pod's registration

**Recorded 2026-07-26, during the reconciliation Sam approved.**

### 11a. §9 was factually wrong when it was written

§9 states: *"As of this locking, `src/round_leader_fade_maker.py`,
`scripts/run_round_leader_fade.py`, the `pods.P-022` config block, and any systemd
unit **do not exist**."*

They existed. The pod was committed on **2026-07-23** (`546d601`, on branch
`p022-p023-golf-deadheat-fade`), and `betting-round-leader-fade.service` had been
**running live on the droplet since 2026-07-23 21:15 UTC** — three days before this
rule was locked. The author of the rule checked the working branch, where the files
are genuinely absent, and did not check the droplet.

This is corrected rather than quietly edited because the error is instructive: a
pre-registration document that misdescribes what is already running is not a
pre-registration. **Check the runtime, not the branch.**

### 11b. What the pod was actually doing: nothing

It wrote **zero quotes and zero fills** in three days, and its books
(`round_leader_fade_{quotes,fills}.jsonl`) were never created. The journal has four
lines, all from startup.

Root cause: `_close_epoch()` preferred `occurrence_datetime` over `close_time`. On
round-leader markets `occurrence_datetime` is a far-future **placeholder** — measured
2026-07-26 on one settled market per series across all five tours, it ran **13.2 to
18.2 days later than `close_time`, on 10 of 10**. With `close_epoch` ~2 weeks late,
the `[12h, 24h]` placement window opened long after the round had ended and settled,
and `_mid()` returns `None` on a settled book. **The pod was structurally incapable
of ever placing a quote.**

The code comment asserted the opposite of the truth. It reasoned by analogy from the
top-N event-timing quirk in `CLAUDE.md`, where the far-future field really is
`close_time` — on this family the two are **reversed**.

**Consequence for the gate: T = 0, and no observation was lost.** Nothing was quoted,
so nothing needs excluding.

### 11c. Registration — parameters as shipped after reconciliation

Per §9, confirming the shipped parameters against §7:

| §7 requirement | shipped before | shipped now |
|---|---|---|
| per-name ≤ 0.5% bankroll | 25 contracts ≈ **$23.25 = 2.3%** ✗ | `pct_per_name = 0.005` → $5.00 ✓ |
| per-tournament ≤ 5% | $50 fixed ✓ (coincidentally) | `pct_per_tournament = 0.05` → $50.00 ✓ |
| aggregate ≤ 15% | $150 fixed ✓ (coincidentally) | `pct_total = 0.15` → $150.00 ✓ |
| sizing on collateral, not contracts | per-name was a **contract count** ✗ | collateral binds; 25-ct cap is a secondary bound ✓ |
| quote only at H ≈ 12–24h | `fade_start_h=24`, `no_new_quote_h=12` ✓ | unchanged ✓ |
| anchor band [0.03, 0.12] (§1) | **(0.03, 0.10)** ✗ | `(0.03, 0.12)` ✓ |
| offset +0.02 | `quote_offset = 0.02` ✓ | unchanged ✓ |
| 13 `*LEAD` series | ✓ | unchanged ✓ |
| `AggregateRiskGuard` **with reservations** | **absent** ✗ | `reserve_trade` on quote, `release_reservation` on pull ✓ (see 11d) |

The two fixed-dollar caps happened to match §7 at a $1,000 bankroll and would have
silently breached it at any other. They are now derived from percentages, so the
percentages are the single source of truth and a bankroll change moves all three
together.

**Caps are gate conditions (§7): raising any of these percentages resets T to 0 under
a new pod ID (§8.1).**

### 11d. A §7 requirement that is only partially satisfiable

§7 requires wiring into `AggregateRiskGuard` with reservations. That is now
implemented — but **P-022 runs as its own process**, so a guard instance it holds is
*not* the 5-minute engine's. It enforces P-022's limits against P-022's own book; it
does not see engine positions, and the engine does not see P-022's.

True cross-process aggregate risk needs shared state that does not exist in this
codebase. The reservation wiring delivers what §7 was actually worried about — a
whole tournament's names going out in one window without the guard seeing the
cumulative exposure — but the "aggregate ≤ 15% of bankroll" cap is enforced against
**P-022's own collateral only**. Recorded here rather than marked satisfied.

### 11e. Verification

`tests/test_round_leader_fade.py`: 10 → **21 tests**. The 11 new ones cover the
close-time defect (both directions, including the fallback), caps derived from
bankroll, collateral binding before the contract cap, the §1 band, and the
reservation lifecycle. **All 11 fail against the pod as it shipped.** Full suite
1,513 passing.

**Still not authorised by this document:** the fixed pod has NOT been deployed, and
`betting-round-leader-fade.service` is still running the 2026-07-23 code that cannot
quote. Starting T is Sam's call.

---

## 12. T-START — the counter is running

**Sam approved starting T on 2026-07-26, having read §11 in full.**

| | |
|---|---|
| **T starts** | **2026-07-26 22:36:52 UTC** (`betting-round-leader-fade` restarted onto the reconciled code) |
| T before this | **0** — the service had been up since 2026-07-23 but could not quote, so no observation was made or lost |
| Threshold | **T = 33** AND ≥270 filled markets (§4), single extension to T = 98 (§4a), §4b clustering guard *(was T = 14, then 24; Amendments 1 and 2)* |
| Reader | `scripts/p022_checkpoint.py` — the only sanctioned reader (§10) |
| Expected T=14 | ~~~3–4 weeks at 15–19/month~~ → **~4.4 weeks at the measured 3.16 event codes/week**, from the FIRST QUOTE (not from T-start: the pod could not quote until 2026-07-29). Corrected 2026-07-28. |

Parameters live, read back from the merged droplet config:

```
bankroll  $1,000     band (0.03, 0.12)     offset +0.02
caps      per-name $5.00 (0.5%)   per-tournament $50.00 (5%)   total $150.00 (15%)
window    [12h, 24h]              series 13                    NOT in pods.active
```

Registered in `manager/registry.yaml` with `source: p022_checkpoint`, so progress is
**derived** and cannot be hand-typed — the P-017 failure mode. The systemd unit is
now monitored, which it was not while it sat dead for three days.

### One thing NOT verified, recorded as a known gap

There were **no open round-leader markets at the restart** — golf tournaments had
concluded and the next week's were unlisted, so `discover()` returned 0. The
close-time fix is verified by unit test *and* against three real Kalshi payloads
(`close_time` chosen over an `occurrence_datetime` 16–17 days later), but **not yet
by an actual live quote.**

`watch` on the registry entry says so explicitly: if markets relist and no quote
appears, investigate before assuming the window logic is right. A pod that cannot
quote is exactly the failure this reconciliation existed to fix, and it would be
careless to call it closed on a day when nothing was listed to quote against.
