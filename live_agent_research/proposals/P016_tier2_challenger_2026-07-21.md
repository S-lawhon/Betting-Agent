# P-016 Tier-2 challenger proposal — 2026-07-21

**Status:** PROPOSAL. Nothing has been applied. No config, parameter, or pod state was modified by this run.
**Generated:** 2026-07-21T18:06:19.091200+00:00 by `scripts/maker_challenger_report.py`
**Tape:** `/private/tmp/claude-501/-Users-samlawhon-Desktop-Betting-Fund-Project/6ad90491-d9a6-440f-ab39-1c49101781b4/scratchpad/maker_fills.jsonl` — champion fills only, +300s markout, fee-adjusted (general maker rate).

## Champion (frozen)

- Fills with a +300s markout: **263** across **9** games
- Mean fee-adjusted markout: **-1.67c** (sd 11.93c)
- ICC by game 0.0346, design effect 1.98 → effective n ≈ 133

The champion is mid-gate and MUST NOT be altered. Every arm below is a conservative transform evaluated offline against the tape the champion already produced; none of them quoted, and none of them could have changed a champion fill.

## Arms

| arm | fills | retention | mean markout | Δ vs champion | 98.3% CI (Bonferroni k=3) | P(better) | mean markout of fills given up |
|---|---:|---:|---:|---:|---|---:|---:|
| champion | 263 | 100% | -1.67c | — | — | — | — |
| postevent_suppress_15s | 239 | 91% | -0.96c | +0.71c | [-0.12, +1.51]c | 0.89 | -8.78c |
| postevent_widen_30s | 239 | 91% | -1.51c | +0.16c | [-0.37, +0.77]c | 0.65 | -2.53c |
| base_widen_1c | 111 | 42% | +0.43c | +2.10c | [-0.14, +4.64]c | 0.98 | -2.47c |

- **postevent_suppress_15s** — Glosten-Milgrom: stop quoting for 15s after an OBSERVED game state change, where our StatsAPI feed lag concentrates informed flow. The doc's named response to a negative low-staleness markout bucket.
- **postevent_widen_30s** — Same mechanism, softer instrument: widen by 1c for 30s after an observed state change instead of going dark. Tests whether the pick-off is priceable rather than only avoidable.
- **base_widen_1c** — The doc's 'wide vs narrow base' contrast — wide side only, since the narrow side is both Tier 3 and unobservable. Uniform +1c half-width, no event conditioning.

## Power

At the champion's observed sd of 11.93c and design effect 1.98, with Bonferroni correction for k=3 arms, the minimum detectable difference at 80% power is:

| arm fills | min detectable Δ |
|---:|---:|
| 263 | 4.73c |
| 500 | 3.43c |
| 1,000 | 2.43c |
| 2,000 | 1.72c |
| 4,000 | 1.21c |

These use the UNPAIRED formula and are therefore an upper bound: arms share the champion's tape, so game-level shocks partly cancel in the difference and the realised bootstrap CIs above are tighter.

## Verdict

- **No arm clears zero** on its multiple-comparison-corrected interval. No change is proposed. The correct action is to keep collecting tape.

## Standing constraints

1. The champion is frozen until the 500-fill gate resolves. Even an arm that wins here must not be applied before then — swapping parameters mid-gate destroys the pre-registered sample.
2. Only conservative arms are evaluable: the tape records only prints that crossed the champion's quotes, so a tighter-quoting arm's fills do not exist in it. Tightening is Tier 3 regardless.
3. RECURSIVE_REVIEW_DESIGN.md requires hard `[min, max]` bounds per auto-adjustable parameter but does not record them. None were invented. Numeric bounds are a human decision and are a prerequisite for any future auto-apply layer.
