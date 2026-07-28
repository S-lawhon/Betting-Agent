# PROMPT — Cherry-pick P-018 core (`4ff5bea`)

**Dated: P-018's data gate opens ~2026-07-30.**

## The situation

P-018 (surprise-gated in-play fade maker) has **1,688 lines and 29 tests on `p018-inplay-fade-core`, absent from HEAD, absent from the droplet, and absent from the test suite.** Meanwhile its data collection is **alive and ahead of schedule**: 27,307 genuine in-play ticks across 80 games in 7 days, and the harness's ≥10-game-day gate opens ~2026-07-30. The gate is about to open on a pod whose implementation is not in the tree.

## The trap — read before touching git

**Cherry-pick `4ff5bea` only. Do NOT merge the branch.** That branch's tip also **removes the Legacy Kalshi Arb Project that P-001's live scanner imports.** Merging it would silently break the fund's highest-volume pod while you are trying to help a different one.

Verify this claim yourself before acting — `git show` the tip, confirm the deletion, and confirm P-001's import path — then proceed. If the claim turns out to be wrong, say so; a corrected finding is worth more than a followed instruction.

## Steps

1. Cherry-pick `4ff5bea` onto the working branch. Resolve conflicts in favour of HEAD for anything outside P-018's own files, and **enumerate every file the cherry-pick touches** in the report.
2. Run the **full** suite, not just P-018's 29 tests. Baseline is 1,593 pass / 1 skipped on the droplet. Report the new numbers and any delta, with the cause of each.
3. Confirm P-001's live scanner still imports and runs — this is the specific thing the merge would have broken, so assert it explicitly rather than assuming the suite covers it.
4. Check the pod is **not** auto-enabled by pod auto-discovery on import. P-018 is pre-backtest; it must not begin trading, paper or otherwise, because its code landed in the tree. If auto-discovery would pick it up, disable it in config with an explicit comment and say so loudly.
5. Register it with the manager registry as `blocked_on: backtest` (not `time`), so the throughput instrument does not project a resolution date for a pod that has no decision rule.

## The coverage caveat that must survive into the backtest

The data-readiness audit measured, from 581 DISCOVERY records, a **33.9% coverage drop, lowest-volume-first**. The replay sample is therefore **biased toward liquid markets** and any backtest built on it must state that in its own words, not in a footnote. **The rate cannot be raised** — 128 cumulative 429s, 48 in the last 24h, daemon self-throttling to 0.6–1.2 req/s. The limiter is the exchange.

Write that caveat into the pod's own docstring and into `research/` now, while it is nobody's inconvenient finding.

## Stop rule

**Do not run the backtest.** Do not deploy P-018 to the droplet. This task ends with the code in the tree, the suite green, P-001 verified intact, and the pod inert.

## Deliverable

`research/REPORT_P018_Cherrypick_2026-07-29.md`: files touched, suite delta, P-001 assertion, auto-discovery status, and the coverage caveat stated in full.
