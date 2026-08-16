# First-half soccer BTTS — Phase 1 containment test

**Rule:** `soccer_research/BTTS_CONTAINMENT_RULE.md`, committed in `3cffb2e` **before** `btts_containment.py` existed and before any pair was matched
**Phase 0:** `BTTS_1H_FEASIBILITY_RULE.md` → PROCEED (33 markets, 97% two-sided, median spread 3.5¢)
**Data:** `soccer_research/archive/btts_1h_snapshot_1_20260816.json.gz` — offline, no network call

---

## VERDICT: **KILL** (provisional pending snapshot 2 — see §5)

> The 1H/full-match BTTS complex is **internally coherent by a wide margin**.
> Across 33 matched pairs there were **zero mid inversions**, **zero
> executable violations**, and the best executable credit was **−28¢**. The
> closest pair sat **19.5¢** from violating containment.
>
> This is not a near miss. A violation would require a pricing error roughly
> the size of the first-half contract itself.

---

## 1. Gate 0 — settlement containment, read not assumed. **PASSES, with one documented leak.**

The rule required reading the contract terms rather than trusting the
entailment. Two findings, and the first is worth keeping regardless of the
verdict.

**Finding 1: `SOCCERBTTS.pdf` and `SOCCERHBTTS.pdf` are the SAME DOCUMENT.**
Both URLs return 45,213 bytes and the extracted bodies are **byte-identical**.
One rulebook governs full-match, first-half and second-half BTTS. This is the
opposite of the `GOLDENGLOBESNOM` trap — there, two similar-looking tickers hid
*opposite* regimes; here, two differently-named templates are one regime.
**Both patterns are only visible by reading the document.**

**Finding 2: strict containment holds in every enumerated scenario.** The
governing clause, verbatim:

> "If \<soccer game\> is cancelled or abandoned after the start of the game, is not due to restart and occurs before ninety (90) minutes of regular-time matchplay… **all markets excluding those for which the identified \<time period\> of consideration has elapsed (which will resolve to the result for that \<time period\>) or those able to be settled due to the Payout Criterion being reached** for the Contracts in question, will resolve to the last fair price as determined by the Exchange in its sole discretion."

The test is whether any scenario lets `BTTS_1H` settle YES while `BTTS_full`
settles NO. It cannot:

| scenario | 1H | full | containment |
|---|---|---|---|
| normal completion | per result | per result | 1H YES ⟹ both scored ⟹ full YES ✓ |
| abandoned after HT, both scored in 1H | period elapsed → **YES** | payout criterion **reached** → YES | ✓ |
| abandoned at 30', both scored | period not elapsed → last fair price | criterion reached → YES | ✓ (full = $1 ≥ 1H) |
| abandoned after HT, one team scored | period elapsed → **NO** | last fair price ≥ 0 | ✓ |
| reached 90' then abandoned | settled on events to that point | same | ✓ |
| suspended >48h, not resumed | criterion satisfied/unsatisfiable resolves; else last fair price | same | ✓ |

The payout criterion for full-match BTTS is **monotone** — once both teams have
scored it can never become unsatisfied — which is what makes the entailment
survive every abandonment branch. Extra time does not break it either: the
template offers both "entire game… including extra time" and "regulation time
only", and `1H ⊆ regulation ⊆ regulation + ET` under both. First-half stoppage
goals count as first-half goals (§Stoppage Time Clarifications), so the
boundary is closed, and a VAR goal is attributed to the period of its initial
occurrence rather than the review.

**The one leak, stated because it changes what the trade is.** In the
discretionary branches — cancellation *before* commencement, replay-from-the-
beginning, delay > 48 h, forfeit before play — **both** legs resolve to "the
last fair price as determined by the Exchange in its sole discretion." Neither
leg settles YES or NO, so strict containment is not violated, but the position
is no longer a guaranteed non-negative payoff: it is exposed to a discretionary
mark. The exposure is **bounded at roughly the two crossed half-spreads**
(≈3.25¢ at the observed medians), not unbounded.

So the honest description is **"arbitrage with a bounded discretionary-mark
risk on abandonment,"** not "riskless arbitrage." That distinction did not end
up mattering — nothing was executable — but it would have been the first thing
to get overstated if something had been.

## 2. The funnel — inherited verbatim from the WINS scanner

Snapshot 1, 2026-08-16T18:51Z. Every first-half market matched a full-match
market on the same fixture (33 of 33), so nothing was lost to pairing.

| stage | definition | count |
|---|---|---:|
| **F0** | fixtures with a full-match BTTS market | 245 |
| **F0 pairs** | (1H or 2H) × full on the same fixture | **33** |
| **F0′** | mid inversions, `mid(half) > mid(full)` | **0** |
| **F1** | both legs genuinely two-sided | 32 |
| **F2** | **executable** — `bid(half) − ask(full) > 0` | **0** |
| **F3** | depth ≥ 20 ct per leg | 0 (vacuous) |
| **F4** | clears taker fee on both legs | 0 (vacuous) |

## 3. How far from a violation — the number that settles it

| statistic | value |
|---|---|
| mid gap `mid(full) − mid(half)` | min **+0.195**, p10 +0.254, median **+0.330**, max +0.400 |
| executable credit `bid(half) − ask(full)` | max **−0.280**, median −0.370 |

The three closest pairs, all Brasileirão Série C:

| pair | half mid | full mid | bid(1H) | ask(full) | gap |
|---|---:|---:|---:|---:|---:|
| `KXBRASILEIROC1HBTTS-26AUG16FLOAFC` | 0.195 | 0.390 | 0.09 | 0.41 | +0.195 |
| `KXBRASILEIROC1HBTTS-26AUG16BRRITA` | 0.145 | 0.375 | 0.11 | 0.39 | +0.230 |
| `KXBRASILEIROC1HBTTS-26AUG16CAXFIG` | 0.205 | 0.450 | 0.09 | 0.46 | +0.245 |

**F4 never came into play.** The fee hurdle computed in the rule (~2.8¢/pair)
is an order of magnitude smaller than the 28¢ shortfall, so this was not killed
by fees — it was killed by the market being right.

## 4. Against the prior, and the reason that did not save it

Rule §5 recorded one honest reason this could differ from the WINS result:
WINS pairs are adjacent strikes **inside one ladder** that a single maker quotes
jointly, whereas 1H and full-match BTTS are **different series** with nothing
forcing one maker to quote both. That reason was real and it still failed —
and it failed in the *stronger* direction:

| | WINS ladder (2026-07 / 08) | BTTS containment |
|---|---:|---:|
| pairs | 1,514 / 1,658 | 33 |
| mid inversions | 118 / 152 | **0** |
| executable | 0 / 0 | **0** |

**Cross-series consistency turned out to be enforced better than within-ladder
consistency.** The WINS ladder threw 118 mid inversions (stable one- and
five-lot artifacts on empty books); the BTTS complex threw none at all. The
mechanism for drift existed and the drift did not.

**The dutch-book archetype is now 0 for 3 on this exchange.**

## 5. What completes this, and what it does not cover

**Provisional on snapshot 2.** Rule §4's KILL requires `F2 = 0` in **both**
snapshots and §6.3 says one snapshot is not a result. Snapshot 2 is scheduled
for ~19:56Z with the containment re-run chained to it. Given a −28¢ margin the
outcome is not in doubt, but the rule is not being declared complete before it
has actually run.

**This does not test the other structural candidate**, and rule §7 said so in
advance: the **deterministic late-first-half time bound** — at minute 44 with
0–0, the probability that both teams score before half-time is near zero on any
model. That is genuinely structural rather than a forecast, and it is where an
edge would live if one does. It needs **in-play soccer book capture that does
not exist**: `src/book_capture.py:223` is hardcoded to `["KXMLBGAME"]` at a
60-second cadence, which the AFT review already flagged as fatal for a
minute-resolution soccer signal.

**Phase 1's KILL is not a verdict on that**, and per §6.5 it does not become
authorised because Phase 1 was disappointing. It is a data-collection decision
first.

## 6. What is banked regardless of the verdict

1. **`SOCCERBTTS.pdf` ≡ `SOCCERHBTTS.pdf`** — one rulebook, byte-identical.
   Worth adding to the CLAUDE.md terms notes alongside the `GOLDENGLOBESNOM`
   entry as the inverse pattern.
2. **The full-match BTTS payout criterion is monotone**, which is why the
   entailment survives abandonment. That is a reusable property for any future
   soccer market-pair test.
3. **139 BTTS series, 0 charging maker fees**, verified **live** on 52 of them
   rather than from the fixture. The original one-line kill's fee-parabola
   clause constrains a taker and does not bind a maker — that part of the AFT
   review's critique was correct, it just did not lead anywhere here.
4. **Kalshi soccer first-half books are genuinely liquid**: 97% two-sided,
   median depth 571 bid / 916 ask. The instrument is real; this hypothesis was
   not.
