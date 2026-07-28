# Claude Code Task — P-022: Widen the Backtest Backwards (cheapest open lead in the folder)

> **Pre-declare before you run.** Write the extension plan and commit it BEFORE looking at any new numbers. The reasoning is in §Discipline below and it is not optional.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper/demo; **no real orders, ever**).

P-022's Phase 2 rests on **19 tournaments**, and every write-up says the sample is capped because "Kalshi trade history reaches back ~1 month." **That belief is wrong.** The 2026-07-26 run found history reaching back to **at least 2026-05-20** — which means the sample can be widened backwards for **minutes of API budget instead of months of calendar**, on the only validated edge in the fund.

The harness exists and is trustworthy: it was rebuilt from scratch on 2026-07-26 and reproduced every published Phase-2 cell, including two *incidental* fingerprints nothing in the report explains (posted counts falling to 342 and 272 at offset 0.00, from cent-rounding). Validate any time with `python3 golf_quirks_research/backtest_fade_fills.py --validate`.

## Discipline — read this before touching data

Widening a sample after seeing a favourable result is how backtests get fitted. Three protections, all mandatory:

1. **Pre-declare in writing, committed, before the first new pull:** exactly how far back you will go, which series, which tournaments qualify, and **what result would count as a refutation**. If the wider sample weakens the edge, that is the finding — it does not license a third window.
2. **Report old and new separately before pooling.** The original 19 must remain individually visible. A pooled number that hides a deteriorating out-of-sample block is worse than no number.
3. **Parameters are frozen.** Band (0.03, 0.12), offset +0.02, the [12h, 24h] window. This is a *sample* extension, not a re-parameterisation. Do not sweep anything. If you find yourself wanting to re-optimise on the wider data, stop and report that impulse instead of acting on it.

**This does NOT touch the forward gate.** `P022_DECISION_RULE.md` governs the forward test and stays locked at T = 14 with T = 0 today. A wider backtest changes our *prior*, not the gate. If the wider sample materially moves the effect size, the correct response is to **report that T = 14's power calculation was derived against a now-superseded effect** and let Sam decide — **not** to silently re-derive the threshold.

## Task
1. **Establish the true history horizon empirically.** Do not trust ~1 month, and do not trust 2026-05-20 either — measure it per-series. Report the actual earliest retrievable print for each of the 13 series.
2. **Enumerate qualifying tournaments** in the extended window, applying the *same* inclusion rules as the original study. Report how many are added and how many are lost to gaps.
3. **Re-run the Phase-2 fill replay** unchanged on the extended set. Report: per-tournament table, tournament-clustered bootstrap CI, leave-one-out, and the **original-19 vs added-N split**.
4. **Check anchor contemporaneity** on every added tournament — the rule earned from make-cut, where a "48h anchor" was a median 68h-old price and produced a pure +9.5¢ artifact. Report staleness distribution for the added block; drop stale anchors rather than correcting them.
5. **Cache everything** into `golf_quirks_research/data/` and **commit the caches gzipped** — this window rolls off the API permanently.
6. Re-check the two documented anchor errors in `REPORT_Golf_Quirks_2026-07.md` §4.2 against the wider data (round-based cells are H=12h not 48h; the "48h before R1" anchor is post-R1 for 58–71% of the control cohort) and correct the report if the wider sample confirms them.

## Gate
- **STRENGTHENED** — effect holds or tightens, added block consistent with the original 19. Report the revised effect size and flag the T = 14 power implication for Sam.
- **WEAKENED** — added block materially worse. **This is a real finding and must be reported as prominently as a positive one.** It does not by itself kill a live pod running a locked forward gate, but Sam needs it immediately.
- **INCONCLUSIVE** — too few clean tournaments added, or staleness kills the added block.

## Definition of done
Pre-declaration committed **before** the first new pull (separate commit, earlier timestamp); `golf_quirks_research/REPORT_P022_Widened_2026-07.md` with the old/new split visible; caches committed gzipped; `--validate` still reproducing the original cells; **no parameter changes, no gate changes, no pod changes.**
