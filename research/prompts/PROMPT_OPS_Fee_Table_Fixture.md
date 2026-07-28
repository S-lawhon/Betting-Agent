# Claude Code Task — OPS: Fix the Fee Table Properly (it has drifted four times)

## Context
Repo: `~/Desktop/Betting Fund Project` (paper/demo; **no real orders, ever**).

`src/kalshi_fees.py`'s `_SERIES_MAKER_FEE` has now drifted **four separate times — three slices in the 2026-07-26 run alone** (round top-N, non-PGA make-cut, and a prefix trap documented in `7806d7c`). Each drift means a series is billed a maker fee it does not charge, or vice versa.

**Why this is not cosmetic.** The fee is subtracted inside every backtest's net-edge calculation. A wrong fee does not produce an obviously broken number — it produces a *plausible* one, shifted by a fraction of a cent, in exactly the range where these hypotheses live and die. Several of our verdicts sit within a cent of their kill line. A fifth hand patch will drift a fifth time.

There is also a known **prefix trap**: series names share prefixes, so a naive `startswith` match assigns the wrong slice. That is what `7806d7c` documents.

## Task
1. **Generate a fixture from the source of truth.** Write `scripts/generate_fee_fixture.py` that pulls `GET /series` and derives, per series, the actual fee regime (`quadratic` = maker-zero vs the maker-fee list) into a committed fixture file. Fixture generation and consumption must be separate: the runtime reads the fixture, the script regenerates it.
2. **Kill the prefix matching.** Match on exact series ticker, or on a rule you can demonstrate is unambiguous across the full 10,555-series inventory. Add a test that fails if any two series would collide under the matching rule.
3. **A CI check that catches Kalshi moving.** `tests/test_fee_fixture_current.py` — regenerate against live `/series` and assert no diff against the committed fixture. It must fail loudly when Kalshi's fee schedule changes, which is the actual event we keep missing. If a network-dependent test is unacceptable in the suite, mark it as a separate opt-in target and document how to run it, but **do not** silently skip on network failure — a skipped check is the failure mode we already have.
4. **Audit the blast radius backwards.** For each of the four historical drifts, determine which committed REPORT verdicts were computed with the wrong fee, and by how much. **Report any verdict whose sign or gate outcome would change.** Do not re-run whole studies — compute the fee delta and check whether it crosses a decision boundary. If one does, flag it as a finding; several kills sat close to their thresholds.
5. Delete `_SERIES_MAKER_FEE` as a hand-maintained dict once the fixture path is live, so there is nothing left to hand-patch.

## Definition of done
Fixture generator + committed fixture + collision test + drift-detecting CI check, all passing; the hand-maintained dict removed; `research/REPORT_Fee_Audit_2026-07-27.md` stating the backwards blast radius and naming any verdict whose outcome the fee error would have changed. No deploy.
