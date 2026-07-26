# P-022 — Pre-Registered Decision Rule

**LOCKED 2026-07-26, before the pod exists and before a single contract
has been quoted. Do not renegotiate.**

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
| **T < 14** | **NO DECISION** — underpowered. Do not act, do not raise caps, do not report a verdict. |
| **T ≥ 14** | **KILL** if `edge ≤ 0`. |
| **T ≥ 14** | **CONTINUE (single extension)** if `edge > 0` and `z < 2.0` — see §4a. |
| **T ≥ 14** | **PASS** if `edge > 0` and `z ≥ 2.0`. |
| **T ≥ 40** | **KILL** if `edge > 0` and `z < 2.0`. The extension is spent. |

"PASS" means the forward test replicated. It authorises **more paper
allocation within the caps in §7 and a written promotion proposal** — it
does **not** authorise live money. Live is a separate decision by Sam,
against the fund's live-promotion bar, and nothing in this document
grants it.

### 4a. The marginal-result rule, registered in advance

Modelled on P-016's: **one extension, at unchanged parameters.**

If at T = 14 the estimate is positive but not separable from zero
(`0 < edge`, `z < 2.0`), the pod continues to **T = 40 and no further**,
with `offset`, the H = 12–24h posting window, the [0.03, 0.12] anchor
band, the series set, and every cap in §7 **byte-identical**. At T = 40
the verdict is PASS (z ≥ 2.0) or **KILL**. There is no second extension
and no "it was close, let's give it one more month."

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

**Cadence.** Phase 2 covered 19 tournaments in roughly one month across
PGA / DP World / LIV / LPGA / Champions round-leader series, i.e. ~15–19
qualifying tournaments per month if the pod quotes the full series set.
At that rate T = 14 lands ~3–4 weeks after the runner starts and T = 40
lands ~2.5–3 months. If the paper runner quotes a narrower set or misses
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
4. **Silence is not confirmation.** If the pod never reaches T = 14, the
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
