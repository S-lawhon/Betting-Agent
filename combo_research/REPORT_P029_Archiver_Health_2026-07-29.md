# REPORT — P-029 archiver daily-run verification (Task 3, 2026-07-29)

**Verdict: the FIXED archiver is healthy — two complete runs today, exit 0, peak RSS
623.8M against the 1G cap, zero swap, 2h40m against a 6h timeout, day-over-day growth
confirmed. But today's 09:39 TIMER run was the old wedged code and had to be killed by
hand; the first timer-triggered run of the fixed code fires 2026-07-30 09:34:45 UTC and
should be checked after it completes.**

## Timeline reconstructed from the journal (all 2026-07-29 UTC)

| run | start → end | outcome | peak mem | swap |
|---|---|---|---|---|
| manual (fixed code) | 03:17 → 06:18 | success, 3h01m | — | — |
| **timer (OLD code)** | 09:39 → 13:35 | **killed, SIGTERM after 3h56m** | **1.0G (pinned)** | **1.0G** |
| manual (fixed code) | 14:07 → 16:47 | success, 2h40m, exit 0 | 623.8M | 0B |

The 09:39 run is the incident already described in HANDOFF §8.5 — it was pinned against
`MemoryMax` swapping, and was stopped manually at 13:35 during the fix session (the
`status=15/TERM` is the operator's stop, not an OOM kill). The 14:07 run is the SeenIndex
fix doing what it claims: memory flat well under the cap, no swap.

## Health criteria, checked

- **Reaches `inactive (dead)`, not eternal `activating`** — yes (14:07 run deactivated
  cleanly; `Result=success`).
- **Completes inside `TimeoutStartSec=6h`** — yes, 2h40m.
- **Counts grow day over day** — yes: report shows 8 days, 635 parts, 7,647,318 rows,
  0.49 GB; the 14:07 run added 143,112 new rows (4,209,696 already archived, 24,638 API
  calls, **0 errors**), and 2026-07-29 alone carries 272 parts / 1,953,921 rows.
- **Peak RSS under the 1G cap** — yes, 623.8M with 0 swap. (~62% of cap: not generous.
  If a fat slate pushes it over, the failure mode to rule out FIRST is memory — check
  `memory peak` in the journal before touching the timeout.)

## Outstanding

The one thing this session cannot verify is the thing the run queue asked for: a healthy
**timer-triggered** run of the fixed code. The next fires **2026-07-30 09:34:45 UTC**
(confirmed via `list-timers`), expected done by ~12:30 UTC. Check:

```bash
ssh root@143.198.162.120 "journalctl -u p029-archive.service --since today --no-pager | tail -8"
```

Healthy looks like: `Deactivated successfully` + `Finished`, memory peak well under 1G,
0B swap, and `done: ... 0 errors` in `/var/lib/p029/archive.log`.
