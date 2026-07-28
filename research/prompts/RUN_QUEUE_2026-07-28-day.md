# Claude Code Run Queue — Tuesday 2026-07-28, DAYTIME

> **How to use:** paste THIS file into a fresh Claude Code session in `~/Desktop/Betting Fund Project`. Work in order. Tasks 1–3 are **pre-window** and must land, be deployed, and be verified **before 2026-07-29T15:30Z**.
>
> Context: `research/RUN_SUMMARY_2026-07-29.md` (read first), `research/REPORT_P022_PreFlight_2026-07-29.md`, `research/P022_RULE_DECISIONS_2026-07-29.md`, `docs/GATE_INSTRUMENTATION_STANDARD.md`.

## Where things stand

The overnight queue ran all 8 tasks and four more. **P-022 is ARMED** — driven at an injected `2026-07-29T15:31:00Z`, its own `discover() → cycle()` path **places 13 quotes** on `KXLPGAR1LEAD-AIGWO26`. First demonstrated path from *window open* to *order would be placed* in this workstream's history. Silent failure now pages within ~23 minutes at `critical`.

Also landed: **P-001 is a LIVE gate, not inert** (post-fix admissibility 3 of 3, the 24.00h fingerprint stops 4m47s before the fix deployed, 152 → 0). **P-017A same-direction maker is a KILL** — Δ = **−6.59¢/posted market**, CI [−9.24, −4.03], fill fraction **2.2%** against a 25% floor, all 18 grid cells negative. The fee saving was real (+2.62¢, within 0.05¢ of the pre-declared ceiling) and simply unreachable. **The question is closed permanently.** 125 commits are on origin, verified from a clone that never touched the laptop.

**Two corrections to earlier documents, both from measurement:**

- **The tennis fee claim in `fee_parabola_research/` is WRONG.** It reported `KXATPMATCH` VWAP 0.519 / 1.156¢ from the candle cache, with the OHLC field ordering flagged as unverified. The live bill measures **0.213 / 0.835¢** (`KXWTAMATCH` 0.291 / 1.109¢), and P-001's book-wide VWAP is **0.208 / 0.794¢**. **Nothing in the live book trades near the fee peak.** The notional paper fee bill to date is **$1,829.34**.
- **P-022's added-block −12.33¢ is not decay.** It is **two fills** where the faded longshot won; the other ten are +0.85¢ and dropping the two gives +10.01¢. Under the published 3.5% base rate, P(≥2 winners in 12 fills) = 0.064 — uncommon, not extraordinary.

### The clock

| deadline | what |
|---|---|
| **2026-07-29T15:30Z** (Wed 10:30 CDT) | **P-022's first real quotes** — AIGWO26 R1. Tasks 1–3 must be deployed and verified before this. |
| 2026-07-29T18:30Z | ROC26 R1 window opens |
| **2026-07-30T16:00Z** | POI26 R1 — close reference is **uncalibrated** (`LAG_DAY_H["KXCHAMPTOUR"]` on n=1, a US event; this is the first Champions Tour event ever staged in Europe) |
| ~2026-07-30 | P-018's data gate — and **its gate #1 cannot discriminate as specified** |
| 2026-08-17 | MLB props, zero slack |
| 2026-08-27 | EV-Map, clock started 2026-07-28T03:11Z — **archiver still blocked** |

## Global guardrails

- **Paper/demo only. No real orders, ever.**
- **P-022 §8.1 resets T for changes to offset, band, window.** Task 1 is a **§7 accounting fix**, not a spec change — but it changes what gets quoted, so **state the §8.1 analysis explicitly in the report** and do not assume it.
- **T = 24, not 14** (Amendment 1, authorised at T=0 with zero forward observations). The criterion did not change; only its input did.
- Fees from `src/kalshi_fees.py` with `series_ticker` passed. Never a hard-coded rate, never a reimplementation.
- Statistics tournament/event-clustered. **Two-sided quotes only** — Task 2 exists because the live pod is currently violating this.
- **`deploy.sh` restarts only `betting-pod-shop`. P-022 is `betting-round-leader-fade`** and must be restarted explicitly.
- **Commit as you go.** `bash scripts/check_research_committed.sh` clean at the end.

## The queue

| # | Task | Prompt | Effort | Deadline |
|---|---|---|---|---|
| **1** | **§7: reserve QUOTED collateral, not just filled** | `PROMPT_P022_Quoted_Collateral.md` | medium | **pre-window** |
| **2** | **The one-sided-book question** | `PROMPT_P022_OneSided_Books.md` | medium | **pre-window (decide)** |
| **3** | **POI26 close reference — verify or exclude** | in Task 2's prompt, §D | short | before 07-30T16:00Z |
| **4** | **P-018 decision rule + gate #1 redesign** | `PROMPT_P018_Rule_And_Gate1.md` | medium+ | this week, blind |
| **5** | **P-017 decision rule, written blind** | `PROMPT_P017_Decision_Rule.md` | medium | this week |
| **6** | **P-016 reader, or retire it properly** | `PROMPT_OPS_P016_Reader.md` | short–medium | yes |
| **7** | **Ops backlog** (push · crontab · archiver OOM · branch) | `PROMPT_OPS_Backlog_0728.md` | short | yes |
| **8** | **P-001's falling placement rate** | `PROMPT_P001_Placement_Rate.md` | medium | yes |

## Why this order

**1 and 2 are first because they expire.** Both are properties of the *first tournament of a 24-tournament gate*, and both become unfixable the moment a quote rests. Task 1 is a gate condition being lost to an accounting inconsistency — 13 quotes carrying **$60.45 against a $50 (5%) limit** — and Task 2 is the pod contradicting the fund's own two-sided-quotes rule on **every** placement it will make tomorrow.

**4 is the week's real research risk.** P-018's backtest reports **+9.09¢/ct, game-clustered CI [+5.33, +13.10]** over 677 game-days and 5,676 fills — and its pre-registered cheapest kill **cannot run**, because both low-surprise buckets are empty *by construction*: `surprise_hi = 0.06` is the threshold at which the strategy quotes at all. A headline that large with its own discriminating test structurally disabled is exactly the shape of every finding this fund has later had to retract.

**5 because P-017 is the only pod with live-shaped evidence and no locked line** — one settled tournament at **−9.89¢/ct** against a +6.8¢ backtest. Every day it stays unruled is a day the rule can be fitted to the result.

**8 because it now matters.** P-001's placement rate is falling **66.2 → 53.5 → 36.0 per week**, unexplained. When it was thought inert this was cosmetic; now that it is a live gate resolving on placement volume, it is the gate's clock.

## Standing priors

- **Forward gates resolved: zero.** No gate has ever passed or failed.
- **Maker/fade is now 0 for 5**, and P-017A closed it with the cleanest kill yet: the edge existed, was measured to within 0.05¢ of its theoretical ceiling, and was **unreachable at 2.2% fill**. Record the pattern — *a real edge you cannot fill is not an edge* — and stop proposing maker variants without a fill estimate first.
- **The survivor profile:** large mechanic (≥5¢) · single-leg · hold-to-settlement · tail-priced · recurring · **deep at the traded leg**. That last clause is the one P-017A died on.
- **"A filter asserted in a docstring and applied in one call path is not a filter."** Six occurrences. Task 1 is the seventh in waiting: §7's cap is asserted in the spec and enforced against the wrong quantity.
- **Compute tick + spread + fee + fill rate BEFORE writing any study.**

## Summary (mandatory, last action)

Write `research/RUN_SUMMARY_2026-07-28-day.md`:

- **First line: is P-022 inside its §7 cap, and does it quote off two-sided books?** Whatever the answer.
- One line per task: verdict + headline number.
- A consolidated deploy list naming **both** systemd units, with the **verification that the running process picked the change up** — not just that the file shipped.
- Decisions still owed by Sam, carrying forward items **2, 3, 5, 6, 7, 10, 11, 12, 13** from `RUN_SUMMARY_2026-07-29.md`.
- Anomalies and any permanent data loss.

**Expected shape of a good run:** P-022 quoting inside its own cap off books it is allowed to quote off, P-018's headline either defended or disarmed before anyone acts on it, and two more pods with rules written while nobody can see the answer. **Do not manufacture an ADVANCE.**
