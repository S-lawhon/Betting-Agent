# P-023c — The Over-Priced Top-N Flip Side (fade / short study)

**Date:** 2026-07-26 · **Prompt:** `research/prompts/PROMPT_P023c_TopN_Overpriced_Fade.md` (Task 8)
**Paper/demo only. No pod built, no config touched, nothing deployed, no order placed.**

## VERDICT: **KILL** — every cohort, both routes

| cohort | route | headline | verdict |
|---|---|---|---|
| **CONTROL PGA top-10/20** | maker fade | **−1.7¢/ct, CI [−6.4, +1.7], 5/11 tourn, 0/11 LOO positive** | **KILL** |
| CONTROL PGA top-10/20 | taker fade | +0.2¢/ct (from +3.2¢ gross, after bias+spread+fee) | **KILL** |
| PGA top-5 | maker fade | +0.6¢/ct, CI [−3.1, +3.7], 6/11 tourn | **MARGINAL — no action** |
| PGA top-40 | maker fade | −13.5¢/ct, CI [−14.8, −12.5], 0/2 tourn | **KILL** (2 tournaments) |
| **R1 top-5** | maker fade | **−18.2¢/ct, CI [−37.5, −3.2], 3/10 tourn, 0/10 LOO** | **KILL** |
| **R1 top-10** | maker fade | **−21.6¢/ct, CI [−32.9, −11.4], 1/10 tourn, 0/10 LOO** | **KILL** |
| **R2 top-10** | maker fade | **−21.9¢/ct, CI [−40.2, −6.4], 1/9 tourn, 0/9 LOO** | **KILL** |
| **R3 top-10** | maker fade | **−10.4¢/ct, CI [−31.6, +5.4], 4/9 tourn, 0/9 LOO** | **KILL** |

**Do not build a P-023c pod.** The −3.2¢ / −5-to-16¢ "over-pricing" is not a short opportunity.
It is, in order of size: a measurement artifact in the anchor, the round-trip cost of
transacting, and — on the maker route — a one-way option written to the informed side.

The prompt pre-registered four sceptical priors. **All four fire.** See §4.

## 0. Harness validation and replication — both PASS

`backtest_fade_fills.py --validate` → `HARNESS VALIDATION: PASS` (364/364 universe, all seven
published P-022 Phase-2 cells, 16/19 positive, LOO-USO26 +0.0304). Re-confirmed before any new
number was produced and again after the `quirks_common.py` edit.

**Replication of the P-023a buy-side gate: all seven published cells reproduce exactly:**

| cohort | anchor | n (pub) | tourn | mean px (pub) | realized (pub) | buy edge (pub) |
|---|--:|--:|--:|--:|--:|--:|
| CONTROL PGA top-10/20 | H48 | 1627 / **1627** | 12 / **12** | 0.199 / **0.199** | 0.167 / **0.167** | −0.032 / **−0.032** |
| PGA top-5 | H48 | 351 / **351** | 12 / **12** | 0.159 / **0.159** | 0.123 / **0.122** | −0.037 / **−0.037** |
| PGA top-40 | H48 | 190 / **190** | 3 / **3** | 0.290 / **0.290** | 0.305 / **0.305** | +0.015 / **+0.015** |
| R1 top-5 | H12 | 224 / **224** | 10 / **10** | 0.133 / **0.133** | 0.085 / **0.085** | −0.048 / **−0.048** |
| R1 top-10 | H12 | 375 / **375** | 10 / **10** | 0.205 / **0.205** | 0.139 / **0.139** | −0.066 / **−0.066** |
| R2 top-10 | H12 | 125 / **125** | 9 / **9** | 0.255 / **0.255** | 0.232 / **0.232** | −0.023 / **−0.023** |
| R3 top-10 | H12 | 82 / **82** | 9 / **9** | 0.272 / **0.272** | 0.159 / **0.159** | −0.114 / **−0.114** |

**Correction to the record:** the published report describes a uniform "48h" anchor, but the
**round-based cohorts only reproduce at H = 12h**. At H=48 they collapse to n=69/134/15/16. The
H=12 reconstruction matches all four cells to four decimals — a documentation slip in
`REPORT_Golf_Quirks_2026-07.md` §4.2, not a data problem.

## 1. Phase 1 — the four-way decomposition (this is where it dies)

Fade gross = `anchor − realized`, the mirror of the published buy-side number. Then price what
it actually costs to get short.

| cohort | n | trn | anchor | bid | ask | spread | realized | **gross ¢** | @bid ¢ | fee ¢ | **@bid net ¢** | @ask (maker) ¢ |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| CONTROL PGA top-10/20 | 1502 | 12 | 0.200 | 0.176 | 0.203 | 0.027 | 0.165 | **+3.2** | +1.1 | 0.94 | **+0.2** | +3.8 |
| PGA top-5 | 319 | 12 | 0.161 | 0.134 | 0.158 | 0.025 | 0.129 | **+3.7** | +0.5 | 0.76 | **−0.3** | +3.0 |
| PGA top-40 | 103 | 3 | 0.266 | 0.220 | 0.281 | 0.060 | 0.262 | −1.5 | −4.2 | 1.08 | −5.2 | +1.9 |
| R1 top-5 | 31 | 8 | 0.133 | 0.083 | 0.150 | 0.067 | 0.097 | +4.8 | −1.4 | 0.50 | −1.9 | +5.3 |
| R1 top-10 | 39 | 6 | 0.178 | 0.143 | 0.195 | 0.052 | 0.103 | +6.6 | +4.0 | 0.82 | +3.2 | +9.2 |

*(n counts markets whose anchor candle also carried a tight two-sided quote; the round cohorts
mostly do not, so their @bid columns are indicative only. Full samples used everywhere else.)*

### 1a. Term 1 — the anchor is itself biased high (measurement artifact)

The P-023a anchor prefers the candle's **executed close**. On boundary top-N names the tape is
overwhelmingly one-directional buying, so that print is usually an offer being lifted, not a
fair value:

| cohort | n traded+quoted | anchor | contemporaneous mid | **anchor − mid** | % anchor == ask | % anchor ≤ bid |
|---|--:|--:|--:|--:|--:|--:|
| CONTROL PGA top-10/20 | 1339 | 0.2083 | 0.1959 | **+0.0124** | **44%** | 18% |
| PGA top-5 | 297 | 0.1630 | 0.1473 | **+0.0157** | **46%** | 18% |
| PGA top-40 | 64 | 0.2945 | 0.2703 | +0.0242 | 17% | 19% |
| R1 top-5 | 14 | 0.1500 | 0.1125 | +0.0375 | 50% | 0% |
| R1 top-10 | 14 | 0.2100 | 0.1854 | +0.0246 | 57% | 7% |

**Roughly 1.2–1.6¢ of the control's 3.2¢ is the anchor sitting on the offer.** Re-pricing the
same markets three ways:

| cohort | n | at anchor | **at mid** | at bid |
|---|--:|--:|--:|--:|
| CONTROL PGA top-10/20 | 1502 | +3.5 | **+2.4 [+0.5, +4.0]** | +1.1 |
| PGA top-5 | 319 | +3.2 | **+1.7 [−0.2, +3.6]** | +0.5 |
| PGA top-40 | 103 | +0.4 | −1.1 [−9.6, +4.6] | −4.2 |
| R1 top-5 | 31 | +3.7 | +2.0 [−11.0, +10.1] | −1.4 |
| R1 top-10 | 39 | +7.5 | +6.6 [+3.3, +13.4] | +4.0 |

Corroborating split (full samples): the fade gross lives almost entirely in traded-bar anchors.
PGA top-5 **+4.3¢ [+2.8,+5.8]** from trades vs **−5.4¢** from two-sided mids; R1 top-5 +5.7¢ vs
−5.7¢; R1 top-10 +7.1¢ vs −0.0¢. (Mid subsamples are 17–25 markets, so this is support; §1a's
direct measurement on 1,339 markets is the primary evidence.)

### 1b. Terms 2 and 3 — spread and fee finish it

```
CONTROL PGA top-10/20, taker fade
  fade gross at the P-023a anchor          +3.2c
  − anchor print-side bias (anchor→mid)    −1.1c   (artifact, not tradeable)
  − half-spread (mid→bid)                  −1.3c
  − taker fee at the bid                   −0.9c
  ========================================
  executable taker fade                    +0.2c   (measured directly: +0.2c)
```

**The decomposition closes to the cent. The published −3.2¢ is 100% artifact plus round-trip
cost.** PGA top-5 goes to −0.3¢, PGA top-40 to −5.2¢.

### 1c. Term 4 — the mechanical term points the WRONG WAY

This is the gate the prompt pre-registered: *"the fade only has a thesis if a mechanical
component survives."* It does not — the certified rulebook mechanic is a **headwind** for a
fade.

GOLFFINISH tie/boundary clauses pay YES **in full** on a tie, so more markets settle YES than
the nominal N, inflating the realized value a short must pay out:

| cohort | nominal N | events | mean YES | YES/N | excess share | realized | no-tie counterfactual | **fade headwind ¢/ct** |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| CONTROL PGA top-10/20 | 15 | 24 | 17.29 | 1.153 | 13.3% | 0.167 | 0.145 | **−2.2** |
| PGA top-5 | 5 | 12 | 5.67 | 1.133 | 11.8% | 0.123 | 0.108 | −1.4 |
| PGA top-40 | 40 | 4 | 42.75 | 1.069 | 6.4% | 0.305 | 0.286 | −2.0 |
| R1 top-5 | 5 | 11 | 7.18 | 1.436 | 30.4% | 0.085 | 0.059 | −2.6 |
| R1 top-10 | 10 | 11 | 14.64 | 1.464 | 31.7% | 0.139 | 0.095 | **−4.4** |
| R2 top-10 | 10 | 11 | 12.27 | 1.227 | 18.5% | 0.232 | 0.189 | −4.3 |
| R3 top-10 | 10 | 11 | 11.09 | 1.109 | 9.8% | 0.159 | 0.143 | −1.6 |

*(Counterfactual assumes excess YES spread proportionally across the in-band cohort. Boundary
ties concentrate on exactly these bubble names, so the true headwind is if anything larger.)*

**So the structure of P-023c is the exact inverse of P-017 and P-022.** Those work because the
rulebook *guarantees* the price is wrong in the direction they trade. Here the rulebook
guarantees it is wrong in the direction *against* the trade, and the residual over-pricing —
2.2¢ smaller than the mechanic running the other way, and smaller still than the cost of
touching it — is a pure behavioural story about retail buying boundary names. That is the
category this fund is **0-for-4** on.

### 1d. Segmentation — no stable cell, and the pattern is price-path selection

Fade gross by anchor horizon (tournament-clustered; `@bid` = executable taker):

| cohort | | | | |
|---|---|---|---|---|
| CONTROL PGA top-10/20 | H72: −1.0 @bid −4.5 | H48: +3.2 @bid **+0.2** | H24: +7.0 @bid +3.6 | H12: +9.4 @bid +3.2 |
| PGA top-5 | H72: +1.9 @bid −0.7 | H48: +3.7 @bid −0.3 | H24: +6.6 @bid +1.4 | H12: +7.9 @bid +0.5 |
| PGA top-40 | H72: −12.7 | H48: −1.5 | H24: +9.2 | H12: +10.4 |
| R1 top-5 | H24: +3.0 | H12: +4.8 | H6: +5.6 | H3: +8.5 |
| R1 top-10 | H24: +5.8 | H12: +6.6 | H6: +11.4 | H3: +12.2 |
| R2 top-10 | H24: −17.1 | H12: +2.3 | H6: +9.6 | H3: +14.7 |
| R3 top-10 | H24: +4.8 | H12: +11.4 | H6: +14.3 | H3: +16.5 |

The apparent fade grows **monotonically as the horizon shortens** and flips sign at the longest
horizon (control −1.0¢ at H72). That is the signature of **band-selection on the price path**,
not a stable structural edge: the 0.08–0.45 band at a late horizon selects names the market has
already started to mark down, and conditions on survival in the band. The executable `@bid`
column does not follow the monotone pattern at all — which is what a cost-dominated series
looks like.

Price bands show no coherent structure either: the control runs +1.2 / +3.9 / +3.6 / +9.6
across 0.08–0.15 / 0.15–0.25 / 0.25–0.35 / 0.35–0.45, while PGA top-5 runs +4.7 / +4.4 /
**−10.6** / +8.8. No band survives as a target.

### 1e. Scalar rows — no sensitivity

On top-N, `result="scalar"` is a **withdrawal refund at the last fair price**, not a dead-heat
split. Including them at their true `settlement_value_dollars` changes nothing — 7 rows in the
control band, 1 in R1 top-10, 0 elsewhere; every cohort moves by <0.05¢. The P-022 settler
scalar issue does not touch this study.

## 2. Anchor-staleness diagnostics (mandatory per Task 7)

| cohort | n | median lag_h | H | **median staleness_h** | % stale >2h | % stale >12h |
|---|--:|--:|--:|--:|--:|--:|
| CONTROL PGA top-10/20 | 1627 | 67.7 | 48 | **19.7** | **100%** | **96%** |
| PGA top-5 | 351 | 67.5 | 48 | 19.5 | 100% | 94% |
| PGA top-40 | 190 | 68.5 | 48 | 20.5 | 100% | 99% |
| R1 top-5 | 224 | 19.0 | 12 | 7.0 | 78% | 22% |
| R1 top-10 | 375 | 18.7 | 12 | 6.7 | 82% | 19% |
| R2 top-10 | 125 | 15.7 | 12 | 3.7 | 67% | 9% |
| R3 top-10 | 82 | 17.0 | 12 | 5.0 | 74% | 21% |

**The full-tournament cohorts carry exactly the make-cut pathology** — a median 68h-old price
presented as a "48h anchor", 19.7h stale on 96–100% of markets. Cause: those series were cached
with **daily** candles (mean 8 bars/market) while the round-based series have hourly bars (mean
110–128).

All Phase-2 headline rows therefore post **contemporaneously with the anchor observation**.
**The staleness bias runs the opposite way here from make-cut**: control `_staleT` −4.3¢
[−9.6,−0.7] vs contemporaneous −1.7¢ [−6.4,+1.7]. Task 7's check therefore matters in *both*
directions — it is not a "stale looks better" bias, it is simply wrong.

**Second correction to the record.** `REPORT_Golf_Quirks_2026-07.md` §4.2 describes the
full-tournament anchor as *"48h, before R1"*. It is not:

| cohort | covered | anchor after end of R1 | after R2 | after R3 |
|---|--:|--:|--:|--:|
| CONTROL PGA top-10/20 | 1527 | **58%** | 4% | 0% |
| PGA top-5 | 325 | **71%** | 7% | 0% |
| PGA top-40 | 150 | 12% | 0% | 0% |
| R1 top-5 / R1 top-10 | 224 / 375 | 0% | 0% | 0% |
| R2 top-10 | 125 | 83% | 0% | 0% |
| R3 top-10 | 82 | 94% | 89% | 0% |

The round-based anchors *are* cleanly pre-round (0% inside their own determining round). The
full-tournament "pre-tournament" anchor is **post-R1 for 58–71% of the control cohort**. Still
information-clean with respect to the final outcome, but not the pre-event price the report
claims.

## 3. Phase 2 — maker fill replay and the adverse-selection measurement

Phase 1 fails the pre-registered gate, so the taker route is dead. The one route left is a
**maker fade** — rest a YES offer at `anchor + offset`, fee 0 on these `quadratic` series. Phase
1 says that offer earns **+3.8¢/ct on the control if every posted quote filled at its price.**

New tick cache: **2,982 markets**, 234,107 prints. Fills strictly through, 25 ct/name cap,
tournament-clustered contract-weighted bootstrap.

### 3.1 The tape is one-directional, which is the whole story

| cohort | markets | prints | **% taker_side = "yes" (buyers lifting offers)** |
|---|--:|--:|--:|
| CONTROL PGA top-10/20 | 1532 | 160,469 | **75%** |
| PGA top-5 | 325 | 65,792 | 71% |
| PGA top-40 | 149 | 3,144 | 77% |
| R1 top-5 | 216 | 1,752 | **87%** |
| R1 top-10 | 368 | 3,296 | **90%** |
| R2 top-10 | 124 | 1,034 | 89% |
| R3 top-10 | 81 | 622 | 80% |

A maker fade is the passive side of a book where 75–90% of volume is people buying. **You will
get filled. That is precisely the problem.**

### 3.2 E[settle | filled] vs E[settle | posted]

Headline row per cohort (offset +0.02, contemporaneous post, strictly through):

| cohort | posted | filled | fill% | cts | **net ¢/ct** | 95% CI | trn | +trn | **E[s\|post]** | **E[s\|fill]** | naive ¢ |
|---|--:|--:|--:|--:|--:|---|--:|--:|--:|--:|--:|
| CONTROL PGA top-10/20 | 1532 | 1059 | 69% | 24,390 | **−1.7** | **[−6.4, +1.7]** | 11 | 5 | **0.161** | **0.251** | +5.8 |
| PGA top-5 | 325 | 225 | 69% | 5,291 | +0.6 | [−3.1, +3.7] | 11 | 6 | 0.123 | 0.189 | +5.8 |
| PGA top-40 | 149 | 100 | 67% | 2,310 | −13.5 | [−14.8, −12.5] | 2 | 0 | 0.289 | 0.465 | +2.0 |
| R1 top-5 | 216 | 54 | 25% | 1,191 | **−18.2** | **[−37.5, −3.2]** | 10 | 3 | **0.088** | **0.361** | +6.6 |
| R1 top-10 | 368 | 128 | 35% | 2,748 | **−21.6** | **[−32.9, −11.4]** | 10 | 1 | **0.141** | **0.466** | +8.4 |
| R2 top-10 | 124 | 61 | 49% | 1,394 | **−21.9** | **[−40.2, −6.4]** | 9 | 1 | 0.234 | 0.520 | +4.3 |
| R3 top-10 | 81 | 35 | 43% | 765 | −10.4 | [−31.6, +5.4] | 9 | 4 | 0.161 | 0.425 | +13.0 |

**Adverse selection does not shave these edges — on the round book it flips them by 25–30¢.**
On R1 top-5 the names you actually get short settle at **0.361** while the names you posted on
settle at **0.088** — a **4.1×** ratio, second-worst measured in this fund after LIV top-N's
19×. The control's gap is smaller in ratio (1.6×) but still converts +5.8¢ into −1.7¢.

**The mechanism is the mirror of Task 7's make-cut kill, and it was predictable.** A resting YES
*offer* on a market that trades through its own determining period is a one-way option written
to the informed side: the price only comes *up* through your offer when the player is climbing
the leaderboard, and it never comes up at all on the ones who fade. Make-cut died on a resting
*bid*; this dies on a resting *offer*; both die for the same reason. **Being on the
"over-priced" side of the mispricing bought us nothing.**

### 3.3 Robustness

| cohort | LOO band ¢/ct | jackknives positive |
|---|---|--:|
| CONTROL PGA top-10/20 | [−2.6, −0.4] | **0 / 11** |
| PGA top-5 | [−0.2, +1.5] | 9 / 11 |
| PGA top-40 | [−14.8, −12.5] | 0 / 2 |
| R1 top-5 | [−24.1, −13.6] | **0 / 10** |
| R1 top-10 | [−27.2, −19.3] | **0 / 10** |
| R2 top-10 | [−26.9, −16.7] | **0 / 9** |
| R3 top-10 | [−15.8, −3.7] | **0 / 9** |

Per-tournament, control (¢/ct): CHSC26 −0.9, COPC26 −23.4, GESO26 +1.3, ISC26 −14.5, JODC26
+4.7, RBBCAN26 −3.5, THCCBN26 −3.3, THMTPBW26 +5.1, THOC26 0.0, TRAV26 −5.6, USO26 +5.4.
R1 top-10: CHSC26 −32, GESO26 −26, ISC26 −57, JODC26 −38, RBBCAN26 −19, THCCBN26 −14,
THMTPBW26 +1, THOC26 −13, TRAV26 −27, USO26 −56.

Sensitivity across the 11-cell control grid: offsets +0.00/+0.02/+0.04 give −0.7 / −1.7 / −1.7¢;
at-or-through gives −0.3¢; `_staleT` gives −4.3¢. **No configuration reaches +2¢.** The only
non-negative cells anywhere are post-hoc `cancel` rows with CIs straddling zero — **5 favourable
cells out of ~60.**

### 3.4 PGA top-5 specifically

+0.6¢/ct with 9 of 11 jackknives positive is the least-dead cell and deserves a straight
statement: **it does not clear the gate.** The CI [−3.1,+3.7] spans zero, the point estimate is
a third of the +2¢ bar, two tournaments (COPC26 −13.3¢, ISC26 −9.5¢) carry the loss, and it
survives only by being the cohort where adverse selection happened to be mildest. It is +0.6¢
*before* any slippage, queue priority, or cancellation risk a live maker would face.
**MARGINAL — no action.**

## 4. The four sceptical priors, answered directly

1. **"Pure fee/spread drag."** — **Confirmed, and the largest term.** Control +3.2¢ gross →
   +0.2¢ executable, decomposing to −1.1¢ anchor bias, −1.3¢ half-spread, −0.9¢ fee. Closes to
   the cent.
2. **"An anchor artefact."** — **Confirmed.** The anchor is +1.2 to +1.6¢ above the
   contemporaneous mid and sits exactly at the ask on 44–46% of markets. The anchor
   *construction* is sound (bare asks correctly rejected in Phase 1) — the bias is that on this
   surface an executed print is usually a lift.
3. **"It may not be fillable."** — **Worse: it is fillable, and that is the problem.** 67–69%
   fill on the full-tournament book, and fills are selected against by 1.6× to 4.1× on
   settlement value.
4. **"The sell side of a cheap contract is tail-heavy."** — **Confirmed.** Every cohort's worst
   single market is a −$20 to −$22.50 loss on a 25-contract clip (a 10–20¢ name settling $1).
   P-019-shaped, with no positive mean to pay for it.

## 5. Capacity — recorded, though moot

The control is genuinely liquid, unlike make-cut: **105 prints per market, 160,469 prints across
1,532 markets over 11 tournaments.** Capacity is not the binding constraint — the edge is.

| cohort | contracts | collateral | P&L | return | per tournament |
|---|--:|--:|--:|--:|--:|
| CONTROL PGA top-10/20 | 24,390 | $18,669 | **−$404** | −2.2% | ~$1,700 |
| PGA top-5 | 5,291 | $4,258 | +$33 | +0.8% | ~$390 |
| PGA top-40 | 2,310 | $1,547 | −$312 | −20.2% | ~$770 |
| R1 top-5 | 1,191 | $978 | −$217 | −22.2% | ~$98 |
| R1 top-10 | 2,748 | $2,063 | −$595 | −28.8% | ~$206 |
| R2 top-10 | 1,394 | $974 | −$306 | −31.4% | ~$108 |
| R3 top-10 | 765 | $520 | −$80 | −15.3% | ~$58 |

The single best hypothetical cell is **+$185 over 11 tournaments on $18.7k of collateral** (~1%)
on an estimate whose CI spans zero.

Note the correlated-exposure hazard: 1,532 markets across 11 tournaments means ~139 simultaneous
shorts per tournament, all decided by one leaderboard — the exact shape of the July 25 halt.

## 6. Method notes

- **Universe:** band 0.08–0.45; anchor H=48h (full-tournament), H=12h (round-based) — pinned by
  exact replication of all seven published cells, not fitted.
- **Statistics:** tournament-clustered bootstrap, 5,000 resamples, seed 12345; contract-weighted
  for fill replays. **Never per-contract.**
- **Prices:** executed prints or two-sided quotes ≤0.10 wide only; never bare asks; `last_price`
  never used. Settlement from `settlement_value_dollars`.
- **Fees:** taker 0.07·P·(1−P); maker **0**, verified live 2026-07-26 for KXPGATOP5/10/20/40 and
  KXPGAR{1,2,3}TOP{5,10,20}.
- **Sample:** 12 PGA tournaments late-May → mid-July 2026 for Phase 1; **11 for Phase 2** —
  PGC26 returns no trade prints. Kalshi history reaches to ~2026-05-20, the edge of this sample.

## 7. Needs Sam's decision / attention

1. **`src/kalshi_fees.py` has drifted a FOURTH time.** `_SERIES_MAKER_FEE` is missing every
   round-based top-N prefix — `KXPGAR1TOP5/10/20`, `KXPGAR2TOP5/10`, `KXPGAR3TOP5/10`.
   Longest-prefix matching falls through to `KXPGA` (which charges), so the code bills
   0.0175·P·(1−P) on series Kalshi bills at **zero**. Same class as Task 3's `*LEAD` finding and
   Task 7's `KXDPWORLDTOURMAKECUT`/`KXLIVTOP5`/`KXLIVTOP10` finding. **Three agents have now
   found three slices of one drift — the table wants a generated-from-`/series` fixture and a
   test, not another hand patch.**
2. **Two documentation errors in `REPORT_Golf_Quirks_2026-07.md` §4.2**, both found by exact
   replication: (a) round-based top-N cells are anchored at **H=12h**, not 48h; (b) the
   full-tournament "48h, before R1" anchor is **post-R1 for 58–71%** of the control and is a
   median 68h old. Neither changes a verdict; both should be corrected in place.
3. **Adopt the anchor-contemporaneity check as standard** — second study running, second time it
   moved a number materially, and here in the *opposite* direction from make-cut. It belongs in
   `quirks_common.replay` as a default, not an option.
4. **Nothing to build.** P-023c is closed. P-022 remains the only green-lit unbuilt edge;
   nothing here touches it.

---

*P-023c is the fourth maker/fade hypothesis killed by measuring adverse selection properly. The
pattern is now unambiguous: on Kalshi golf surfaces, a resting quote on a market that trades
through its own determining period is a written option, and only a rulebook mechanic pointing
the same way as the trade (P-017, P-022) has ever paid for it.*

---

*Verification note: the orchestrating session re-ran `backtest_fade_fills.py --validate` after
this task's `quirks_common.py` edit and confirmed `HARNESS VALIDATION: PASS` — the P-022
reproduction is unaffected. Tick caches archived gzipped under `archive/`.*
