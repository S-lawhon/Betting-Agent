# Claude Code Task — P-026: Stat-Leader Dead-Heat Fade — $0 Test FIRST, then the monitor

> **REFRESHED 2026-07-26.** Reordered so the **free falsification runs before the build**. The original version led with the monitor; that inverts house discipline (test cheaply, build only if it survives). Background: `Deep Research R4 - Terms Census & Chart Markets 2026-07.md` §2.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper-mode; **no real orders, ever**).

Verified: KXLEADER season stat-leader markets settle ties as **$1/n splits** (rule confirmed in live market rules — same mechanism as green-lit P-022). Computed 30-season tie rates: **NFL INT 47%, MLB pitcher wins 30%, NFL rush/rec TDs 20%** each; MLB HR/RBI/saves ~7%. **Never-fade list** (structurally cannot tie): sacks (half-sacks), stolen bases, yardage, rate stats, NBA per-game. **MLB leader series resolve ~Oct 15, 2026** — the first-ever KXLEADER settlements, and a free empirical confirmation of the split rule.

## Step 1 — THE $0 PRE-TRADE TEST (run this today, before writing the monitor)

The whole thesis reduces to one observable: **if the market ignored the split, co-leaders' YES mids will sum to ≳100¢**, because each is being priced as if it pays a full $1.

1. Pull the current open KXLEADER events for the target stats. Use **orderbook mids with both sides present** — bare asks on thin books fabricate edges, and these books are thin.
2. For each market, compute the sum of the top-k names' mids and compare against the tie-adjusted fair value using the round-4 formula `fair = q·[1 − s·(1 − E[1/n])]`, with the per-stat historical `s` and `E[1/n | tie] ≈ 0.43`.
3. **Report the answer before building anything:**
   - Sums ≳100¢ on the high-tie stats → the market is ignoring the split; the thesis is alive and the monitor is worth building.
   - Sums already ≤100¢, or the discount already tracks the per-stat tie rate → **the market prices it**, this is a KILL, and you have saved a build. Write the KILL and stop.
   - Books too thin / no two-sided quotes to read → **INCONCLUSIVE**, not "alive". Say so plainly and stop.

This step costs nothing but API calls. **Do not skip it and do not build past a KILL.**

## Step 2 — the monitor (ONLY if Step 1 says the thesis is alive)
Build `scripts/leader_split_monitor.py` (read-only, cron-able):

1. Target series (from `GET /series`, LEAGUELEADER template): MLB wins, HR, RBI, saves; NFL INT, rushing TDs, receiving TDs, passing TDs. **Skip the never-fade list entirely.**
2. Daily run: for each target series' open event, pull the strike list + orderbook mids for the top ~8 names; pull current stat standings from a public source (MLB: statsapi — `src/mlb_statsapi.py` may already help; NFL in-season later: nflverse/ESPN public endpoints; cite what you use).
3. Emit to `data/leader_monitor/YYYY-MM-DD.jsonl` per market: {stat, names, standings gap in stat units, time remaining, each name's mid, **sum of co-leader mids**, tie-adjusted fair value}.
4. Alert condition (**log-only, no orders**): visible co-leadership (top-2 gap ≤ a stat-specific threshold, e.g. ≤1 win / ≤1 INT with ≤3 weeks left) AND sum-of-mids ≥ 0.98 → write an ALERT row with the computed fade edge per name.
5. systemd timer unit mirroring `mlb-props-collector.timer` conventions (daily is fine), but **do NOT deploy** — leave the unit file in `scripts/` for Sam.

## Step 3 — the falsification artifact (build now, runs in October)
One-shot `scripts/leader_settlement_check.py`: after 2026-10-15, read the settled MLB leader markets' `settlement_value_dollars` and report any **scalar** (split) prints. A tie resolving `scalar 0.50 / 0.33` is direct empirical confirmation of the $1/n rule at zero cost. Note in the script's docstring that `result="scalar"` must **not** be read as a void (see the P-022 settler fix).

## Sizing constraint to record now, not later
Only **~6–8 independent stat-seasons per year**, with total within-stat correlation (one season's INT race is one observation, however many names you trade). Any future pod caps at **1–2% bankroll per stat-season**. Write this into the report so it is not rediscovered under pressure.

## Definition of done
Step-1 result reported with an explicit ALIVE / KILL / INCONCLUSIVE verdict **before** any build; monitor + settlement-check scripts committed only if Step 1 cleared, with a sample day's JSONL as proof of function; unit file present but NOT deployed; the capacity constraint recorded; **no orders, no pod**.
