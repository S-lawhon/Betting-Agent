# Claude Code Task — P-023 Make-Cut Phase 2: Maker-Fill Realism Replay

> **REFRESHED 2026-07-26.** The original version of this prompt assumed the P-022 harness was on disk. **It is not — the `.py` harnesses were lost on 2026-07-25 and must be rebuilt first.** Read Step 0 before anything else.
>
> Background: `golf_quirks_research/REPORT_Golf_Quirks_2026-07.md` §4 (Phase-1 ADVANCE: PGA make-cut +4.8¢/ct buying bubble YES, 10 tournaments, CI [+2.3, +7.7]) and `REPORT_Golf_Quirks_Phase2_P022_2026-07.md` (the fill-realism method to reuse). Phase 2 only, then STOP.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper-mode Kalshi engine; **no real orders, ever**). Phase 1 proved PGA make-cut bubble names (40–60¢, 48h pre-tournament anchor) are underpriced because cut-line ties + MDF + post-cut WD/DQ all pay YES. What settled averages cannot answer: can a **maker resting a YES bid** actually get filled without adverse selection eating the edge? P-022's Phase 2 answered exactly this question for the fade side and passed.

## Step 0 — REBUILD THE LOST HARNESS (do this first)
`golf_quirks_research/pull_trades.py` and `backtest_fade_fills.py` were **never committed to git and are gone from disk**. What survives:
- **The methods, documented in full** in `REPORT_Golf_Quirks_Phase2_P022_2026-07.md` (§1 Method onward) — this is your specification.
- **The data caches**, intact, in `golf_quirks_research/data/` (including `data/leader_trades/`). Do not re-pull what is already cached; Kalshi trade history only reaches ~1 month back, so these caches may be the **only** surviving copy of that window.

Rebuild both scripts from the report, then **validate the rebuild by reproducing P-022's published Phase-2 numbers**: +3.4¢/ct at offset +0.02, tournament-clustered CI [+1.7, +5.1], 16 of 19 tournaments positive, leave-one-out robust. **If you cannot reproduce those figures, STOP and report the discrepancy** — a harness that does not replicate a known result cannot be trusted to produce a new one. Do not proceed to make-cut on an unvalidated harness.

**Commit the rebuilt harness immediately, before running anything with it.** That is the whole lesson of the file-loss incident.

## Task
1. Extend `pull_trades.py` to cache tick prints for `KXPGAMAKECUT` (and `KXDPWORLDTOURMAKECUT`) markets whose 48h anchor sat in the 40–60¢ band. Kalshi trade history reaches ~1 month back — take what exists and report the effective sample.
2. Build `backtest_makecut_fills.py` mirroring `backtest_fade_fills.py`, flipped: at T = 48h/24h pre-close of R2 (the cut round), rest a **YES bid** at `anchor − offset` (offsets 0.00/0.02/0.04); fill ONLY on a print with `taker_side="no"` strictly through the bid, capped at 25 ct/name (research cap). PnL/ct = `settlement_value − fill_px`; maker fee 0 (quadratic — confirm per series, don't assume).
3. Adverse-selection diagnostic exactly as P-022's: E[settlement | filled] vs E[settlement | posted]. For make-cut the risk is inverted — sellers hit your bid disproportionately on names *falling out of* the cut; measure whether the +4.8¢ survives.
4. Tournament-clustered bootstrap CIs; leave-one-out; per-tournament table.
5. Also run the same replay on DPW make-cut and (if any tick data exists) LIV top-N — the MARGINAL Phase-1 candidates — reported separately, **NOT pooled** with PGA.
6. **Settlement correctness note:** make-cut markets can also produce `result="scalar"`. If `src/kalshi_golf_settler.py` has not yet been fixed by `PROMPT_P022_Settler_Scalar_Fix_And_Gate.md`, do not rely on the settler here — read `settlement_value_dollars` directly, and say in the report which you used.

## Gate
- **ADVANCE** if PGA make-cut net ≥ +2¢/ct with tournament-clustered CI excluding zero under strictly-through fills.
- **KILL** if adverse selection flips the sign. **MARGINAL** → keep caching ticks weekly and re-run at 15+ tournaments.

Deliver `golf_quirks_research/REPORT_MakeCut_Phase2_2026-07.md` + updated `p022_p023_params.json`, **plus** a short note confirming the harness rebuild reproduced P-022's Phase-2 figures. **No pod, config, or service. STOP at the REPORT.**
