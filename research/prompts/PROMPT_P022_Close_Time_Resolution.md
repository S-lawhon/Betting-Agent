# Claude Code Task — P-022: Resolve the Real Round Close Time (THE blocker — nothing else matters until this lands)

> This pod has now failed to quote **twice for the same underlying reason**, and the second fix was aimed at the wrong field. Read the diagnosis before writing code.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper/demo; **no real orders, ever**).

P-022 is the only validated edge in the fund and the entire forward pipeline. It has been live since 2026-07-23 and has **never emitted a quote**. T = 0 of 14.

**What happened.** Golf relisted 2026-07-27T00:10Z; the runner discovered **346 markets across 3 events** at 00:22:20Z and quoted nothing. The pod places only in a **[12h, 24h] pre-round window**, which requires knowing when the round actually closes — and *Kalshi does not expose that while the market is open.*

**Verified live, 2026-07-27, `KXPGAR1LEAD-ROC26-TMOO`:**
```
open_time                 = 2026-07-27T00:10:00Z   <- real
created_time              = 2026-07-27T00:00:27Z   <- real
close_time                = 2026-08-16T00:00:00Z   <- placeholder
expected_expiration_time  = 2026-08-16T00:00:00Z   <- placeholder
expiration_time           = 2026-08-16T00:00:00Z   <- placeholder
latest_expiration_time    = 2026-08-16T00:00:00Z   <- placeholder
occurrence_datetime       = 2026-08-16T00:00:00Z   <- placeholder
can_close_early           = True
early_close_condition     = "This market will close and expire after a winner is declared."
```
**All five time fields collapse to one ~20-day fallback** on a tournament whose Round 1 is this week. `close_time` is rewritten to the true early-close stamp **only at close**.

**The methodological trap that cost two days, now recorded in `CLAUDE.md`:** the 2026-07-26 reconciliation measured `occurrence_datetime` vs `close_time` on **SETTLED** markets — where Kalshi has already rewritten `close_time` — and inferred open-market behaviour from it. **Never measure a time field on a settled market and generalise to open markets.** Confirmed exchange-wide against an MLB control whose game had already started while the market sat open carrying a `close_time` three days later.

## Task 1 — Get a real round time (the actual fix)

The handle exists in the event payload:
```json
"product_metadata": {"competition": "Rocket Classic", "competition_scope": "Round 1 Leader"},
"settlement_sources": [{"name": "the Governing League", "url": "https://www.pgatour.com/"}]
```

Build `src/golf_schedule.py` resolving `(competition, round_number) → round start/end UTC`, across all 13 configured series (PGA / LIV / LPGA / DPW / Champions).

**Primary: an external schedule source.** Evaluate and pick one, and say why in the report — PGA Tour's own site/feeds, ESPN's golf scoreboard endpoints, or another public source. Requirements: covers all five tours, gives per-round dates (tee times ideally), is free, and is stable enough to run unattended. **Cache aggressively and degrade safely: if the source is unavailable the pod must NOT quote** — a wrong round time is worse than no quote, because it produces fills outside the validated window and those tournaments would have to be excluded from T anyway.

**Secondary, and also your validation set: the empirical open→close offset.** For **settled** round-leader markets, `close_time` *has* been rewritten to the truth, and `open_time` is real. So you can measure the actual `open_time → true close_time` distribution directly from history (Kalshi history reaches back to at least 2026-05-20).
- If that offset is tight (say ±3h) per series and round number, it is a legitimate fallback and possibly a simpler primary.
- If it is loose, say so — that is the finding, and it means the external source is mandatory.
- **Either way this is how you validate the external source**: reconstruct what your resolver *would* have said for each settled market and compare against the true rewritten `close_time`. Report the error distribution. **A resolver that cannot hit the true close within the tolerance the [12h, 24h] window requires is not usable — say so rather than shipping it.**

Name the tolerance explicitly: with a 12-hour-wide placement window, what resolver error is acceptable before quotes land outside it?

## Task 2 — Cron the detector (small, and it is the thing that catches silence)

`scripts/p022_window_check.py` is deployed and correctly alarming `CLOSE_REF_PLACEHOLDER` on all 3 listed events — but it is **not crontabbed**, so the registry job reads stale. Install the schedule. It deliberately does not reuse the pod's window arithmetic (a detector that agrees with the pod when the pod is wrong would have sat silent through all three dead days *and* through 2026-07-27) — **preserve that independence** when you touch it.

## Task 3 — Close the two §7 gate-integrity holes

Both are recorded in `research/REPORT_P022_First_Quote_2026-07.md` and both can silently corrupt T:

**(a) Books do not survive a restart.** All three collateral caps re-arm from zero while paper exposure persists — a real §7 breach path. Persist book state, or make the cap computation derive from the trade log rather than in-memory state.

**(b) The checkpoint implements no cap-breach exclusion.** Rule §7 says a tournament with any cap breached is **EXCLUDED from T**. That requires a breach to be *recorded when it happens* — a gate condition only checkable retrospectively is not a gate condition. Record breaches at quote time; make `p022_checkpoint.py` honour them.

## Guardrails
- **Do not change any P-022 parameter.** Band (0.03, 0.12), offset +0.02, window [12h, 24h], caps 0.5%/5%/15%. Changing any resets T to 0 under a new pod ID. If the schedule resolver suggests the window is wrong, **report it — do not tune it.**
- **The rule is locked.** `golf_quirks_research/P022_DECISION_RULE.md` governs. Fixing the pod so it can quote is not a rule change; altering what counts as an observation is.
- Deploys are Sam's call, **except** that this pod is the fund's only forward evidence and has been dead for four days — if the fix is verified by tests and against real payloads, put it on the deploy list with a clear recommendation and let Sam pull the trigger.
- **Do not declare success without a live quote.** The last two fixes were both "verified" and both failed live. Success = a real quote rested in a real window, or an explicit statement that the window has not yet opened.

## Definition of done
`src/golf_schedule.py` with tests; the resolver validated against settled markets with its error distribution reported and the acceptable-tolerance question answered explicitly; the detector crontabbed; book persistence and cap-breach recording landed; `research/REPORT_P022_First_Quote_2026-07.md` updated with whether a live quote was observed. **No parameter changes.**
