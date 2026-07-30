---
description: "Phase 1 — build the lifetime rollup by hand, then enable its hourly timer"
---

# Phase 1 — the rollup

`rollup.json` holds the lifetime numbers the P&L and Pipeline tabs render. It
cannot be computed on demand: `rotate_active_log` caps the active log at 50 MB and
rewrites it to hold only still-open PLACED rows, keeping 12 gzipped archives —
about 1.5 days of live traffic.

Read `@docs/DASHBOARD.md` §"The rollup's two load-bearing decisions" before
starting.

## Preconditions — refuse if any fails

1. Phase 0 is done: `data/dashboard/engine_state.json` exists on the VPS and is
   fresh.
2. **You are NOT inside a deploy window and the engine is not mid-restart.** The
   first pass streams every surviving archive — potentially hundreds of MB — on a
   2 GB box running six workloads.
3. Confirm with me before starting.

## Steps

1. **Run the full backfill by hand, niced.** Do NOT let the timer discover this
   work on its own first:
   ```bash
   ssh root@129.212.176.202 'cd /opt/betting-pod-shop && nice -n 19 ionice -c3 venv/bin/python -m scripts.build_dashboard_rollup --root /opt/betting-pod-shop --full'
   ```
   It prints a warning that a full rebuild cannot recover already-pruned
   archives. That is expected on a first run and is the reason for step 4.

2. **Cross-check the output against something independent before trusting it.**
   Do not just confirm the file exists:
   ```bash
   ssh root@129.212.176.202 'cd /opt/betting-pod-shop && venv/bin/python -m scripts.build_dashboard_rollup --dry-run --json'
   ssh root@129.212.176.202 'cd /opt/betting-pod-shop && python3 -c "import json;print(json.load(open(\"manager/state/status.json\"))[\"trade\"][\"actions\"])"'
   ssh root@129.212.176.202 'cd /opt/betting-pod-shop && zgrep -c PLACED data/trade_logs/trade_log.archive_*.jsonl.gz | tail -3'
   ```
   Report the rollup's counts, the collector's 24-hour `trade.actions`, and the
   archive spot-check **side by side**. These are computed by different code over
   the same data, so a disagreement is real signal — surface it rather than
   picking whichever looks nicer.

3. **Install and enable the timer:**
   ```bash
   scp scripts/systemd/dashboard-rollup.service scripts/systemd/dashboard-rollup.timer root@129.212.176.202:/etc/systemd/system/
   ssh root@129.212.176.202 'systemctl daemon-reload && systemctl enable --now dashboard-rollup.timer && systemctl list-timers dashboard-rollup.timer --no-pager'
   ```
   Then confirm one run actually succeeded:
   ```bash
   ssh root@129.212.176.202 'systemctl status dashboard-rollup.service --no-pager | head -20; journalctl -u dashboard-rollup -n 20 --no-pager'
   ```

4. **Back it up, and tell me where the backup lives.**

   ⚠ Once `rotate_active_log` prunes an archive (it keeps 12), the rows it held
   survive **only** in `rollup.json`'s counters and cannot be rebuilt from disk.
   The timer passes `--backup`, which writes `rollup.json.bak.gz` beside it.
   Confirm that file exists, and tell me plainly whether it is included in
   whatever backs up `manager/state/`. If it is not, say so — this is the one file
   in the system that is genuinely unrecoverable.

5. **Confirm the 06:07 run picks up the 06:00 rotation.** The timer fires at :07
   specifically so a freshly rotated archive is counted in the same hour it
   appears. Check tomorrow, or note it as an open item.

6. Run `/dashboard-verify`.

## Rollback

```bash
ssh root@129.212.176.202 'systemctl disable --now dashboard-rollup.timer'
```
Nothing reads `rollup.json` yet, so leaving the file is harmless. **Do not delete
it** — if it already counted a pruned archive, deleting it destroys history.
