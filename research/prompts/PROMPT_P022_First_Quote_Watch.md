# Claude Code Task — P-022: Verify the First Live Quote (CRITICAL — the only edge in the fund)

> The pod ran for three days writing nothing and **nothing noticed**. It is fixed, but the fix is verified only by tests and settled payloads — never by a live quote. This task makes the difference between "working" and "silently doing nothing" observable.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper/demo; **no real orders, ever**).

P-022 (Round-Leader Dead-Heat Fade) is the **only validated edge in the fund** and the entire forward pipeline. State as of 2026-07-26 22:36:52 UTC:

- `betting-round-leader-fade` restarted onto reconciled code; **T = 0 of 14** (90% power vs the measured +3.4¢/ct; rule §5).
- Gate **LOCKED** in `golf_quirks_research/P022_DECISION_RULE.md`, progress **derived** by `scripts/p022_checkpoint.py` — it cannot be hand-typed.
- Live params: bankroll $1,000, band (0.03, 0.12), offset +0.02, caps $5/$50/$150 = 0.5%/5%/15%, window [12h, 24h], 13 series, **not** in `pods.active` (own loop).

**The known gap, recorded in `registry.yaml`:** there were **no open round-leader markets at the restart** (tournaments concluded, next week unlisted), so `discover()` returned 0. The `_close_epoch()` fix — `close_time` rather than the far-future `occurrence_datetime` placeholder — has never been exercised against a live, open market.

**Why this is urgent rather than routine.** The pod's failure mode is *silence*, and silence is also its correct behaviour between tournaments. The registry deliberately carries **no heartbeat file** for exactly that reason. So the one thing that distinguishes "healthy and waiting" from "structurally incapable of quoting" is whether it quotes **when a quotable window actually opens** — and nobody is currently watching for that.

## Task

### 1. Establish when the next window opens — before anything else
Query Kalshi for open `KX*R?LEAD` markets across the 13 configured series. For each, record `close_time`, `occurrence_datetime`, and the delta between them (expected: the placeholder runs 13–18 days later). Compute exactly when the pod's [12h, 24h] pre-round window opens for the next round. **State the UTC timestamp.** If no markets are listed yet, state when the tour schedule says they should list and stop there — do not fabricate a test.

### 2. Make silence loud — a quotable-window detector
The gap is that "no quotes" is unobservable. Build `scripts/p022_window_check.py` (read-only):
- Determine whether a quotable window is **currently open** (any discoverable market whose `close_time` puts it in [12h, 24h] with a mid inside the band).
- Cross-check against the pod's actual quote log.
- **Window open AND zero quotes in the last cycle → this is the alarm condition.** Emit it loudly.
- Window closed and zero quotes → correct behaviour, silent.

Register it in `manager/registry.yaml` as a **job** (not a heartbeat — the reasoning in the existing services comment stands). Severity `warn`. This is the check that would have caught the three dead days.

### 3. Verify the fix end-to-end against a live open market
Once a market is genuinely open and in-window, confirm on the droplet: `discover()` returns >0; `_close_epoch()` returns `close_time` not the placeholder; `_mid()` returns a real mid (not `None` from a settled book); a quote is actually rested; and `AggregateRiskGuard.reserve_trade` holds **worst-case collateral**, with `release_reservation` firing on pull so an abandoned quote cannot starve the pod.

If the window is not open during this session, **do not simulate and declare success.** Leave the detector running, say so plainly, and hand back.

### 4. Confirm the first fill books correctly — the loss-tail check
The first time a quote fills, verify end-to-end that a `result="scalar"` settlement books as a **partial payout** and not a $0.00 void. The deployed settler fix should handle this; confirm it on real data rather than trusting it. **This is the check that stands between us and a gate that passes a money-losing strategy** — 532 scalar markets were previously booked at $0.00 and 0 of 532 actually settle at zero.

### 5. Cap-breach accounting
Under rule §7 a tournament with any cap breached is **EXCLUDED from T**, and raising a cap **resets T to 0 under a new pod ID**. Confirm `p022_checkpoint.py` actually implements the exclusion, and that a breach is recorded at the time it happens rather than reconstructed later. A gate condition that is only checked retrospectively is not a gate condition.

## Guardrails
- **Do not change any P-022 parameter.** Band, offset, caps, and window are frozen by a locked rule; changing any of them resets T to 0. If something looks wrong, report it — do not tune it.
- Read-only against live `data/`. Any correction goes to a new file.
- Deploys are Sam's call. If a fix is needed, commit it and put it on the deploy list.

## Definition of done
`research/REPORT_P022_First_Quote_2026-07.md` committed stating: the next quotable window in UTC; whether a live quote was observed (or explicitly that the window had not opened); the window-check script committed and registered; the collateral-reservation and cap-exclusion behaviour verified; and the scalar-booking path confirmed on real data if a fill occurred. **No parameter changes, no deploy.**
