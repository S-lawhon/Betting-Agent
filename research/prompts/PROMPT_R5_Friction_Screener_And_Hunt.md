# Claude Code Task — R5: Build the Friction Screener, Validate It on the Graveyard, Then Hunt

> The Tier-1 queue is empty and the last six kills shared one cause. **Build the screen before the next study, not after it.**

## Context
Repo: `~/Desktop/Betting Fund Project` (paper/demo; **no real orders, ever**).

Twenty-seven hypotheses have been tested. The current scoreboard:

- **Settlement / structural mechanics: 3 for 6** (P-015, P-017, P-022 live; P-026, P-027 and three satellites dead)
- **"We have better information": 0 for 7** (P-016, P-019, P-020, P-021, P-024, P-025, EV-Map Build 1)
- **Maker / fade: 0 for 4** once adverse selection is measured honestly

**The six kills on 2026-07-26 died the same death: the mechanic was real but smaller than the tick and the spread.**

- P-026: co-leader **bid**-sum maxed at 99.0¢ against a hard 100¢ ceiling — the mid-sum signal fired in 4 of 10 events and was **100% accumulated half-spread in 4 of 4**.
- P-023c: +3.2¢ gross decomposed to **+0.2¢ executable**, with the rulebook mechanic running *against* the trade.
- Satellites: best award-tie trade **+0.93¢ on 99¢ of collateral**; **0 of 1,514** ladder pairs executable.

Every one of those was knowable *before* the study was written. That is the opportunity.

## Part 1 — Build the screener (do this first)

Write `scripts/friction_screen.py`. Given a candidate series or family, it computes and reports:

1. **Tick** — the minimum price increment, and what it costs as a fraction of the hypothesised edge.
2. **Spread** — measured from **two-sided quotes only**, never bare asks or mids on empty books. Report the distribution, not a point estimate; report how often two-sided quotes exist at all (a market that rarely has both sides is untradeable regardless of edge).
3. **Fee** — from the fixture, not the hand-maintained dict (see `PROMPT_OPS_Fee_Table_Fixture.md`; coordinate so the fixture exists first).
4. **Depth** — contracts available within the band the strategy would trade, and therefore the **capacity ceiling in dollars**.
5. **Required edge** = tick + spread + fee, with a stated margin. Compare against the hypothesised mechanical edge.
6. **Verdict: SCREENED OUT / WORTH A STUDY**, with the arithmetic shown.

Two design requirements:
- **Report `INSUFFICIENT DATA` rather than a number when the book is too thin to measure.** "Too thin to measure" is itself a screen-out, and must not be laundered into an optimistic estimate — that is the exact error the three killed satellites made.
- Take the hypothesised edge as an **explicit input**. The screen answers "can this edge clear this friction", not "is there an edge".

## Part 2 — Validate against the graveyard (mandatory before any new use)

A screen that has not been calibrated is a new source of error, not a filter. Run it over the historical record:

- **It must SCREEN OUT** P-026, P-023c, and the three satellites — the cases where friction demonstrably ate the edge.
- **It must PASS** P-015, P-017, and P-022 — the three that survived to live paper.
- **A screen that kills the survivors is wrong** and must be recalibrated before use, not shipped with a caveat.
- Report where it is ambiguous. The boundary cases are the most informative thing this task will produce, and they tell you how much margin the screen needs.

Also run it against **P-016, P-019, P-021, P-024** — the "better information" kills. It probably will *not* retrodict those, because they died of sharp counterparties rather than friction. **Confirming the screen's blind spot is as valuable as confirming its hits** — it tells us what the screen does not cover, so nobody over-trusts it later.

## Part 3 — Hunt, screened

Only now generate new candidates. Constraints from the record:

- **Weight toward settlement/structural mechanics** — the only category with survivors — but note it is 3 for 6, not 3 for 3. A verified mechanic is **necessary, not sufficient**.
- **Do not propose another "we have better information" edge** unless you can name why Kalshi is not already the sharper venue. That category is 0 for 7, and P-020 found Kalshi is both deeper *and* sharper outside the liquid sports head.
- **Screen every candidate before writing a study.** Any candidate that survives the screen gets a one-page spec; anything else gets one line in a rejected list with its screen arithmetic.
- Use the two cheap tools banked on 2026-07-26: **`settlement_ts − close_time`** as a first-pass settlement-quirk screen (one field, no external data), and the fact that settled markets carry **`status="finalized"`, not `"settled"`** (a `status=="settled"` filter returns zero rows across all 197 ECONSTAT series — the same silent shape as the golf `status="closed"` trap).

## The honest prior — state it in the report
After 27 hypotheses, the base rate of a new idea surviving is low. **The screener's main value may be killing candidates in an hour instead of a night, and that is a good outcome, not a disappointing one.** A run that screens out twenty candidates and specs zero is a success if the arithmetic is right. Do not manufacture an ADVANCE.

## Definition of done
`scripts/friction_screen.py` committed with tests; `research/REPORT_Friction_Screen_2026-07-27.md` showing the graveyard validation table (including the blind-spot check) and an explicit statement of where the screen is unreliable; a screened candidate list with arithmetic for every rejection; **at most** one-page specs for survivors. **No pod, no config, no service, no deploy.**
