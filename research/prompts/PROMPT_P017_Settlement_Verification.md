# Claude Code Task — P-017: Verify Settlement Actually Resolves (silent-failure check, short)

> Time-sensitive and cheap. The registry flagged this as a **silent** failure mode with an expected signal date of ~2026-07-28. It is now past due for a check.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper-mode; **no real orders, ever**).

P-017 (Golf Top-N taker) is live in paper and trading its gate: **8 tournaments, currently 1** (3M Open, entered 2026-07-21, 38 PLACED rows). It **shipped with no settler at all**; `src/kalshi_golf_settler.py` was added 2026-07-20 to fix that. Kalshi top-N markets sit `status="closed"` with an empty `result` for ~a day post-tournament, so the first genuine settlement signal was not expected until ~2026-07-28.

The registry's own warning: *"If PLACED keeps climbing and settled stays 0 past that, the settler is not wired in — the failure mode is silent."* A pod that places but never settles accrues gate progress that means nothing.

## Task
1. **Read the live state on the droplet** (`129.212.176.202`, `/opt/betting-pod-shop/`; see the SSH setup note in project memory / `scripts/deploy.sh` for access). Do **not** modify live `data/`. Report:
   - P-017 PLACED vs SETTLED vs still-open row counts from the trade log.
   - Whether `KalshiGolfSettler` appears in the settler rotation at all (it runs at `settlement.interval_cycles: 6`) and when it last logged.
   - The `status` / `result` values Kalshi currently reports for the 3M Open markets P-017 holds.
2. **Distinguish the three possible worlds** and say which one we are in:
   - (a) Settler wired, markets genuinely not yet resolved by Kalshi → nothing wrong, report the expected date.
   - (b) Settler wired but silently erroring / not reached → find the error, fix it, add a regression test.
   - (c) Settler not wired into the rotation on the droplet at all → the deployed code differs from local. Report the diff; do **not** deploy (Sam's call).
3. **Check the stale-timeout backstop.** The settler auto-voids positions N days after close. Confirm it has not silently voided real settlements while waiting for a result — a void and a loss are not the same observation, and voids must not count toward the 8-tournament gate.
4. **Confirm the gate counter is honest.** Verify that "tournaments" is counted as *settled* tournaments, not *entered* ones. If the counter increments on entry, that is a gate-integrity bug — report it loudly.
5. **Cross-check the correlated-exposure cap.** `max_event_exposure_pct: 0.08` was committed (`287cf89`) after the 2026-07-25 halt (one tournament lost −$171 paper, 16% of bankroll on a single event, tripping the 5% daily limit). Confirm whether the droplet is running it. If not, that is part of the pending deploy — report, do not deploy.

## Interaction with P-022
`result="scalar"` handling in this same settler is **wrong** and is fixed by `PROMPT_P022_Settler_Scalar_Fix_And_Gate.md`. If you find scalar results already appearing in P-017's settled rows, **stop and flag it** — it means P-017's realised numbers are contaminated too, and that task becomes urgent rather than merely queued.

## Definition of done
`research/REPORT_P017_Settlement_Check_2026-07.md` committed stating which of the three worlds we are in, the true settled-tournament count against the 8-tournament gate, whether any stale-timeout voids occurred, whether the event cap is live, and any scalar contamination found. Any code fix committed with a regression test. **No deploy, no live `data/` mutation, no config promotion.**
