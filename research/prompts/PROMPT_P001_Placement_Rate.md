# PROMPT — P-001's falling placement rate

**This stopped being cosmetic on 2026-07-29.** P-001 was re-measured as a **LIVE
gate** — post-fix admissibility **3 of 3**, worst error 7 minutes, the 24.00h
tie-break fingerprint going **152 → 0** four minutes and forty-seven seconds
before the fix deployed. The cleanest before/after this fund has produced.

A live gate resolves on **placement volume**. So this is now the gate's clock:

> **66.2 → 53.5 → 36.0 placements per week. Unexplained.**

A 46% decline in the input to the fund's fastest-resolving gate is either a
seasonal fact, a data-supply fact, or a defect. Nobody knows which.

## Establish the fact before explaining it

1. **Confirm the series is real, not an artefact of the window.** Re-derive it at
   daily granularity from the droplet log, with the window boundaries stated. A
   trailing partial week reads as a decline in every dataset ever built.
2. **Check the denominator.** Placements can only fall because *opportunities*
   fell or because *conversion* fell. Split it: markets scanned → matched to a
   sharp-book line → passing the edge threshold → placed. **The stage that
   dropped is the answer**, and it is a different investigation depending on
   which one it is.

## Then work the candidates, cheapest first

- **Seasonality.** MLB is the bulk of P-001's book. Are there simply fewer games,
  or fewer with usable consensus lines, in the measured weeks? Count games, not
  impressions.
- **Odds API supply.** Sam is on a paid tier, so credits are not the constraint —
  but coverage, book mix, or line availability can still move. Did the set of
  contributing books change? Pinnacle's presence in particular drives the
  ensemble.
- **The matcher fix itself.** It deployed inside this window. It was *supposed*
  to reject wrong-day matches — **152 of them, and it did.** Quantify how much of
  the decline is exactly that: correctly-rejected bad matches are a *fix*, not a
  regression, and the placement rate falling is then the expected consequence of
  the pod becoming honest. **Check this before anything else.** It is the most
  likely explanation and the only one that is good news.
- **Threshold or config drift.** Diff the effective config over the window.
- **Silent failure.** Any rise in exceptions, timeouts, or 429s in the scan path.

## Re-project the gate, either way

P-001's gate is **200 admissible CLV rows**. With admissibility now measured at
3 of 3 rather than the all-time 14.3%, the projection changes completely — and
the placement rate is the other half of that arithmetic.

**Report the projected resolution date at each of the three rates** (66.2, 53.5,
36.0/wk) so the cost of the decline is visible as a date rather than a
percentage. If the honest answer is "still resolves this season at 36/wk", that
materially lowers the urgency and should be said. If it does not, say that.

Use the checkpoint's own reader for admissibility. **Do not hand-roll a ticker
parser** — Kalshi MLB tickers are **ET**, and reading them as UTC has already
produced one spurious figure in this project.

## Stop rule

**Diagnose, do not tune.** No threshold change, no matcher change, no config
change. If the cause is a defect, report it with a proposed fix and stop.

## Deliverable

`research/REPORT_P001_Placement_Rate_2026-07-28.md`: the daily series with stated
windows, the funnel split showing which stage fell, the matcher-rejection
quantification, the ranked candidate causes with evidence for and against each,
and the re-projected gate date at all three rates.
