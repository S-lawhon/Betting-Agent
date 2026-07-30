---
description: Read-only health check of the whole dashboard data plane (safe anytime)
allowed-tools: Read, Grep, Glob, Bash(ssh root@129.212.176.202 *), Bash(curl *), Bash(git status *), Bash(git log *), Bash(git diff *), Bash(python3 -m pytest *), Bash(python3 -m scripts.build_dashboard_rollup --dry-run *)
---

# Verify the dashboard data plane

**This command is READ-ONLY.** It must not restart a service, write a file on the
VPS, install a unit, or touch Caddy. If you find yourself wanting to fix
something, report it and stop — the fixes live in `/dashboard-deploy-phase0`,
`/dashboard-deploy-phase1`, `/dashboard-deploy-phase2`.

Read `@docs/DASHBOARD.md` first so you know which producer owns which file.

## What to check

Work through these and report a compact table of findings. **Report what you
actually observed — never infer a value you did not see, and never fill a gap
with a plausible number.** That habit is the entire reason this dashboard was
rebuilt (the old one rendered ROI against a hardcoded `bankroll = 10000`).

1. **Producers are producing.** For each, report the path, whether it exists, and
   the age of its *content* (not just mtime — a job can rewrite a file without
   adding anything, which is how `clv_settlement` looked healthy for 31 hours
   while its newest row was 50 hours old):
   - `data/dashboard/engine_state.json` — should be < 5 min old (300 s cycle)
   - `data/dashboard/open_positions.json` — same
   - `data/dashboard/rollup.json` — should be < 1 h old (hourly timer at :07)
   - `manager/state/status.json` — should be < 15 min old
   - `data/p022_window_check/status.jsonl` — last row

   ```bash
   ssh root@129.212.176.202 'cd /opt/betting-pod-shop && ls -la data/dashboard/ manager/state/ && date -u'
   ```

2. **The dashboard's own view of itself.** This is the fastest single check —
   it reports per-source ages and engine liveness in one payload:

   ```bash
   ssh root@129.212.176.202 'curl -s localhost:8081/healthz' | python3 -m json.tool
   ```

   Note: `/healthz` deliberately returns **200 even when a source is stale**. Read
   the `sources` block; do not treat a 200 as "everything is fine".

3. **Units and timer.**
   ```bash
   ssh root@129.212.176.202 'systemctl is-active betting-pod-shop betting-dashboard; systemctl list-timers dashboard-rollup.timer --no-pager'
   ```

4. **The rollup's integrity counters.** Report `rows_total`,
   `pruned_sources_counted`, and `carried_placed_rows_skipped`:
   ```bash
   ssh root@129.212.176.202 'cd /opt/betting-pod-shop && venv/bin/python -m scripts.build_dashboard_rollup --dry-run --json'
   ```
   - `carried_placed_rows_skipped` climbing after each 06:00 rotation is the
     double-count guard **working correctly** — it is not an error.
   - `pruned_sources_counted` > 0 means `rollup.json` now holds history that
     exists nowhere else. Confirm `rollup.json.bak.gz` is present and recent.

5. **Cross-check one number against an independent source.** Compare the
   rollup's 24-hour action counts against the collector's own `trade.actions` in
   `status.json`. They are computed by different code over the same log, so a
   disagreement is real signal. Report both numbers side by side.

## Report

Finish with:
- a table: source · exists · content age · verdict
- anything stale or missing, with the exact command that would fix it
- **explicitly say if you could not check something**, rather than omitting it
