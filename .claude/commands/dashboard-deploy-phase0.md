---
description: "Phase 0 — deploy the code; the engine starts writing snapshots. Nothing serves differently."
---

# Phase 0 — deploy the code

After this the engine writes two small files per cycle. **Nothing about what is
served changes**: the engine keeps `--web` on 8080, the old dashboard keeps
working, and `scripts/deploy.sh`'s health gate is untouched.

Read `@docs/DASHBOARD_RUNBOOK.md` for the full context.

## Preconditions — refuse if any fails

1. `main` contains the rebuild. Check `git log --oneline -1` and confirm
   `src/dashboard_sources.py` exists. If not, run `/dashboard-merge` first.
2. Working tree clean (tracked files).
3. Confirm with me before starting. This restarts the trading engine.

## Steps

1. **Deploy.** Use the project's own script — do not hand-roll an rsync:
   ```bash
   bash scripts/deploy.sh 129.212.176.202 restart
   ```

   **CRITICAL:** `deploy.sh` runs the test suite and stops at a
   `Continue with deploy? [y/N]` prompt. If it reports **any** failures,
   answer **N** and stop. Never answer `y` with failures outstanding — that gate
   is the only thing that caught a silent revert of a production settler fix, and
   without it P-014 would have lost its settler in production a second time.

2. **Confirm the snapshots appear and then advance.** One check is not enough —
   a file that exists but never updates is the failure mode that matters:
   ```bash
   ssh root@129.212.176.202 'cd /opt/betting-pod-shop && ls -la data/dashboard/ 2>&1'
   ```
   Wait out one full cycle (310 s) and check again. **The mtimes must have moved.**
   If the directory is missing entirely, the engine is not writing — check
   `journalctl -u betting-pod-shop -n 50` for "dashboard snapshot write failed".

3. **Confirm `/api/status` gained nothing but additive keys.** This is the
   contract that `src/health_check.py` and 147 tests depend on:
   ```bash
   ssh root@129.212.176.202 'curl -s localhost:8080/api/status' \
     | python3 -c "import json,sys; d=json.load(sys.stdin); print(sorted(d))"
   ```
   `engine_status`, `risk`, `cycle`, `pods`, `settlement`, `trades` must **all
   still be present**. New keys (`engine_state_*`, `dashboard_*`,
   `trades_truncated`) are expected. **A missing or renamed key is a hard stop —
   roll back.**

4. **Confirm the engine is healthy and the memory cap took.** This deploy adds
   `MemoryMax=640M` to a unit that previously had none:
   ```bash
   ssh root@129.212.176.202 'systemctl show -p MemoryMax -p MemoryCurrent betting-pod-shop; systemctl is-active betting-pod-shop'
   ```
   `MemoryCurrent` should sit well under the cap. If it is near 640M, tell me —
   that box OOM-crash-looped for five weeks in June and the cap needs raising
   before it bites, not after.

5. Run `/dashboard-verify` and report. Expect `rollup.json` to be **absent** at
   this point — that is correct, phase 1 creates it.

## Rollback

`deploy.sh` has its own auto-rollback on a failed health check. To undo manually:
```bash
ssh root@129.212.176.202 'systemctl stop betting-pod-shop && rsync -a /opt/betting-pod-shop.bak/ /opt/betting-pod-shop/ && systemctl start betting-pod-shop'
```
Check the exact backup suffix `deploy.sh` used before running that. The new files
are inert — nothing reads them yet — so leaving them in place is harmless.
