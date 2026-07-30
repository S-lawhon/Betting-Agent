# RUN SUMMARY — 2026-07-30 dashboard deploy (resumed with Sam present)

Continuation of the halted overnight run (`research/RUN_SUMMARY_2026-07-30-dashboard.md`,
commit `32fd0dc`). Sam resolved the P-022 config state in a separate session
(`ece217b` + `e6ed22a`, pushed and deployed) and authorized this resumption
interactively, including two mid-run decisions recorded below.

## Headline

**The deploy landed. `betting-pod-shop` is `active`. The standalone dashboard
answers on `127.0.0.1:8081` with every source available.**

**⚠ HEADLINE FINDING — the engine-down test tripped its own hard-stop
condition:** with the engine stopped and the dashboard itself reporting
`liveness: down`, `pnl.open.bankroll` still returned **`1000.0` with
`available: true`** — the phase-2 spec and the overnight brief both say this
must be `null`. Detail in the engine-down section; root cause in the
adversarial findings (F1). It is NOT the old hardcoded-10000 fabrication — it
is the last real snapshot rendered without any "engine is down" qualifier on
the open-exposure block — but it violates the stated invariant and Sam should
read F1 before trusting the open-exposure tile during an outage.

Phase 3 (cutover) was not run. Caddy was not touched. `:8081` is
loopback-only.

## What ran, in order, with observed numbers

1. **Preflight** (~13:20Z): tree clean except `.claude/settings.local.json`
   (session permission entries — committed with the assets). `main` in sync
   with origin at `e6ed22a`. `dashboard-rebuild` branch + bundle present.
2. **Assets commit** — `dd65d30`: six `.claude/commands/dashboard-*.md`,
   `docs/DASHBOARD_RUNBOOK.md`, settings.
3. **Merge** — `--ff-only` was impossible (main had legitimately advanced by 4
   commits over the branch base `b4fc21b`). **Sam approved a merge commit
   in-session.** Pre-verified with `git merge-tree`: conflict-free; merged
   config keeps `min_top_size: 0` (line 570) and gains the `dashboard:` block
   (line 679). Merge = `c27ef90`, pushed. Bundle and `_to_delete/` removed.
4. **Suite** — branch: **2146 passed, 2 skipped** (expected 2145/3). Explained
   exactly: `tests/test_scalar_correction.py:119` skips only when the
   gitignored audit file
   `research/corrections/P-017_scalar_corrections_20260726.jsonl` is absent
   (author's container); on this Mac it exists, so the test RUNS and PASSES.
   Same 2148 collected. **Sam approved proceeding in-session.** Merged tree:
   same 2146/2. Back-compat gate: **147 passed** (`test_web_dashboard.py` +
   `test_dashboard.py`), untouched.
5. **Phase 0 deploy** (03:11Z, before the pause): `deploy.sh 129.212.176.202
   restart` — tests passed so no prompt fired; rsync + chown + restart +
   health check PASSED in 5s. Engine wrote `data/dashboard/` from 03:11:50Z
   onward, all night.
6. **Engine unit cap** (13:40Z): `/etc/systemd/system/betting-pod-shop.service`
   was a stale standalone copy from March — nothing installs the repo's unit
   file, so `MemoryMax` was `infinity`. Installed the repo unit with
   `__PYTHON__` substituted (backup at
   `betting-pod-shop.service.pre-rebuild`), daemon-reload, restart. Verified:
   `MemoryMax=671088640` (640M), `MemoryCurrent≈212M`, `active`.
7. **Phase 0 verification**: snapshots advanced across two cycles —
   `engine_state.json` mtimes **13:40:20 → 13:45:13 → 13:50:14** (~293s
   cadence). `/api/status` (with auth — the rebuilt handler basic-auths
   everything except `/health`+`/healthz`) carries all six required keys:
   `cycle, engine_status, pods, risk, settlement, trades` — none missing, none
   renamed. No additive keys visible at top level (`trades_truncated`
   presumably conditional).
8. **Phase 1 rollup** (13:47Z): full backfill, niced — see cross-check table.
   Timer installed and enabled; first fire 14:07:12Z (result below).
9. **Phase 2 minus Caddy** (13:49Z): `betting-dashboard.service` enabled;
   `active`, 13.7M / 192M cap; binds `127.0.0.1:8081` only (verified with
   `ss`; the engine's pre-existing `0.0.0.0:8080` bind is unchanged).
   `/healthz`: **every source `available: true`, none stale** (engine_state
   252s, open_positions 252s, manager_status 265s, rollup 104s, p022_window
   679s, clv 52732s — all within their thresholds).
10. **Engine-down test** — its own section below.
11. **Adversarial review** — findings below, worst first.
12. **/dashboard-verify** — output below.

## Rollup cross-check table (phase 1)

| figure | rollup --full (written) | rollup --dry-run (re-run) | collector 24h (`trade.actions`) | zgrep spot-check |
|---|---|---|---|---|
| rows_total | 2,319,268 | 2,319,268 | — | — |
| this run | 2,304,339 archive + 15,095 live | 0 archive + 15,095 live | — | — |
| placed | 8,539 lifetime | 8,539 | 25 (24h) | 222 / 164 / 107 per recent daily archive |
| settled | 7,360 lifetime | 7,360 | WIN 15 + LOSS 10 (24h) | — |
| skipped | 2,303,156 | 2,303,156 | 14,968 SKIPPED_* (24h) | — |
| realized P&L | +p$7,871.81 | +7,871.81 | — | — |
| days / pods | 107 / 7 pods | same | — | — |
| clv_rows | 663 | 663 | — | — |
| archives_complete | 14 | 14 | — | — |
| pruned_sources_counted | **0** | 0 | — | — |
| carried_placed_rows_skipped | **166** | 166 | — | — |

Agreement assessment: the dry-run reproduces the written run exactly, and
`archive_rows_added_this_run: 0` on the second pass proves archives are
consumed exactly once. The collector's 24h total (15,027 actions) matches the
active-log re-read (15,095 rows — the log spans slightly >24h by rotation
design). The archive `zgrep -c PLACED` counts (which include carried
duplicates the rollup dedupes) are magnitude-consistent with 8,539 lifetime
placed over 107 days. **No disagreement beyond window/dedup effects — the
numbers agree.** `pruned_sources_counted = 0`, so no history is yet
irrecoverable; the `--backup` flag on the timer writes `rollup.json.bak.gz`
(existence confirmed after the first timer run, below). Note: neither
`rollup.json` nor its `.bak.gz` is in any off-box backup — nothing currently
backs up `manager/state/` either. Flagged to Sam.

## The engine-down test, verbatim

Probe 1 — 30s after `systemctl stop betting-pod-shop` (13:54Z):

```
liveness: live
reason  : None
bankroll: 1000.0
open avail: True
gates n: 16
pnl realized avail: True
funnel avail: True
page: 200
```

Not a defect by itself: both liveness inputs are files (snapshot 4 min old,
under its 15-min threshold; collector's systemd view refreshes every 15 min),
so 30s after a stop the data plane cannot know yet. The honest test requires a
collector tick to land during the outage, so the stop was held ~9 minutes
(13:53:30→14:02:30) across the 14:00 cron.

Probe 2 — after the 14:00:57Z collector tick (14:01:30Z):

```
liveness: down
reason  : systemd reports the unit is inactive
systemd : inactive
bankroll: 1000.0
open avail: True
eng_src stale: False | avail: True
gates n: 16
funnel avail: True
page: 200
```

- `liveness` **down** ✓ — sourced from systemd's own view, not silence.
- page **200** ✓; gates (16), P&L, funnel all still render from their own
  sources ✓.
- `pnl.open.bankroll` **1000.0 — the hard-stop value.** See F1.

Restart: `systemctl start betting-pod-shop && sleep 25 && is-active` →
**`active`** (first try, 14:02:55Z). Verified again at run end.

## Adversarial review findings — worst first

**F1 — CONFIRMED (headline). The open-exposure block never goes unknown while
the snapshot file parses, even when the dashboard itself knows the engine is
down.** `src/dashboard_api.py:530` (`_pnl`): the open block keys solely on
`_available(sources, "engine_state") and risk` — file readability — and never
consults the `liveness` that `_engine()` computes twenty lines earlier. A
stopped engine leaves a valid snapshot on disk, so `bankroll`, `exposure_usd`
and `positions` render with `available: true, reason: null` for at least
15 min (until the staleness label flips — and even then `available` stays
true, only `sources.engine_state.stale` changes). The author's own phase-2
spec says bankroll must be `null` here. Empirically confirmed by probe 2.
What breaks: during any outage, the open-exposure tile shows confident
last-known numbers while positions may be settling; the "unknown vs measured"
invariant the rebuild exists to enforce is violated on its flagship panel.
How to confirm: probe 2 above reproduces it on demand. Suggested shape of the
fix (NOT applied — more than one line and no covering test): `_pnl` should
degrade the open block to `available: false, reason: "engine is down —
last-known values as of <written_at>"` whenever engine liveness is `down` (or
the snapshot is stale), optionally carrying the last-known values under a
separately-named key.

**F2 — CONFIRMED. `dashboard_server.py` SIGTERM handler deadlocks; every
stop/restart burns the full 90s systemd timeout and ends in SIGKILL.**
`src/dashboard_server.py:120-128`: `_shutdown` calls `server.shutdown()` from
the signal handler, which executes on the main thread — the same thread inside
`serve_forever()`. `shutdown()` blocks until the serve loop exits, and the
serve loop cannot proceed while the handler is on its stack. Classic
`socketserver` deadlock. Empirically confirmed: `time systemctl restart
betting-dashboard` = **1m33s** (90s TimeoutStopSec + SIGKILL + 3s
ExecStartPre). Harm is low (read-only process, no state to corrupt) but every
deploy of the dashboard will look hung for 90s. One-line fix exists
(`threading.Thread(target=server.shutdown).start()` in the handler) but no
existing test covers signal handling, so per the run rules it is recorded,
not fixed. Ops-side mitigation: `TimeoutStopSec=5` in the unit.

**F3 — PLAUSIBLE (code-read, not triggered). `roi_pct` can fabricate 0.0%.**
`src/dashboard_api.py:518`: `roi = round(100.0 * (realized["lifetime"] or
0.0) / gw, 3)` — if `lifetime.realized_pnl` were ever null while
`gross_wagered_usd` is present, ROI renders `0.0%` instead of unknown. The
rollup always writes both today, so it is latent; it is also the only `or 0.0`
on a *displayed* number found in the API layer. Confirm by feeding `build_v2`
a rollup with `realized_pnl: null` (pure-function test, no I/O).

**F4 — PLAUSIBLE. Coverage counter `rows_missing_day` silently resets across
rollup runs.** `scripts/build_dashboard_rollup.py:546` restores
`rows_missing_day` from `coverage.rows_missing_day`, but `core()` (line ~480)
never writes that key — so the archive-accumulated count is lost on every
restore and the published figure undercounts (tail-only). Display-only
honesty counter; the trade counters are unaffected. Confirm: two consecutive
runs after archiving rows with missing dates; the count drops.

**F5 — NOTE. A settlement row with no `pnl_usd` books 0.0 P&L silently.**
`build_dashboard_rollup.py:309` `_f()` coerces missing/None to 0.0; the row
still increments `settled`. No coverage counter records it. If the schema ever
drops/renames `pnl_usd`, realized P&L freezes at prior totals while
settlements keep counting — a plausible-wrong-number, not a visible gap.

**F6 — NOTE. `source_fingerprint` (ETag input) omits `clv_log.jsonl`**
(`src/dashboard_sources.py:507-533`), so a client polling with If-None-Match
can 304 through a CLV update. In practice the engine snapshot changes the
fingerprint every 5 min, capping the staleness at one cycle. Cosmetic.

**F7 — NOTE. Open rows missing `pod_id` are attributed to P-001**
(`dashboard_api.py:580`, mirroring `TradeLogSchema._DEFAULTS`; the rollup does
the same but *counts* the attribution in `rows_missing_pod_id`, the API layer
does not). Auditable in the rollup, silent in the open-positions view.

Checked and clean: no external asset anywhere in `dashboard_v2.html` (grep for
`http`, `cdn`, `<script src`, `<link`, `fetch(` — only the local API fetch);
formatters render null as "— unknown" with provenance titles, never 0 (the
`|| 0` hits are bar-width geometry only); gate verdicts are passed through
verbatim (`reader_verdict`) with `progress_state` deliberately a separate UI
label; `progress: null` never coerced; unknown severities sort near the top
and are included in alarms; memory: every read is size-capped
(`_MAX_JSON_BYTES` 32MB, tails 256KB, chunked 4MB reads in the rollup), all
three units carry MemoryMax (engine 640M, dashboard 192M, rollup 256M), and
the rollup's live totals stayed identical across written and dry runs.

## Rollup timer — first timer-driven run

Fired 14:07:12Z, `dashboard-rollup.service` exited **0/SUCCESS** at 14:07:26Z
(419ms CPU). Journal totals consistent with the hand run: placed/settled
8,539 / 7,360; skipped 2,303,262 (+106 new live rows since the hand run —
correct incremental behavior); realized +7,871.81; rotation guard 166.
`--backup` wrote `rollup.json.bak.gz` (235,846 bytes, same second). Next fire
15:07:41Z. The 06:07-picks-up-the-06:00-rotation check is tomorrow's open
item, as the phase 1 command anticipates.

## /dashboard-verify output (14:09–14:16Z)

| source | exists | content age | verdict |
|---|---|---|---|
| `data/dashboard/engine_state.json` | yes | 2.1 min (`written_at` 14:07:36) | OK (<5 min) |
| `data/dashboard/open_positions.json` | yes | 2.1 min (`written_at` 14:07:36; 39 rows, not truncated) | OK |
| `data/dashboard/rollup.json` | yes | 2.2 min (`built_at` 14:07:26, timer-written) | OK (<1 h) |
| `data/dashboard/rollup.json.bak.gz` | yes | 2.2 min | OK |
| `manager/state/status.json` | yes | 9.6 min (`collected_at` 14:00:02) | OK (<15 min) |
| `data/p022_window_check/status.jsonl` | yes | last row 14:08:07, `state: NO_WINDOW` | OK (window opens 16:00Z) |
| clv_log.jsonl freshness | yes | 15.0 h by content stamp | OK (48 h threshold) |

Units: `betting-pod-shop` **active**, `betting-dashboard` **active**; timer
armed (last 14:07:26 SUCCESS, next 15:07:41).

`/healthz`: all six sources `available: true`, `stale: false`. One expected
artifact: `engine_liveness` read `down` from 14:01 to 14:15 because the
collector's 14:00:57 tick landed mid-engine-down-test and its snapshot is the
liveness source; after the 14:15:02 tick recorded `betting-pod-shop=active`,
`engine_liveness` returned to **`live`** (observed 14:16). Incidentally this
proves the designed precedence: a systemd-down verdict beats a fresh engine
snapshot for up to one collector interval (≤15 min) after a restart — worth
knowing when reading the chip right after a deploy.

Integrity counters (dry-run at 14:13): `rows_total 2,319,427` ·
`pruned_sources_counted 0` · `carried_placed_rows_skipped 166` · placed/
settled/skipped 8,539 / 7,360 / 2,303,315 · realized +7,871.81. Cross-check:
collector 24h `trade.actions` = SKIPPED_EDGE 14,962 + SKIPPED_DUPLICATE 95 +
SKIPPED_CLV_GATE 17 + PLACED 25 + WIN 15 + LOSS 10 + DATA_COLLECTION 9 =
15,133, vs rollup active-log re-read 15,254 rows (log spans slightly >24h).
Same shape, same magnitudes, lifetime counters stable — **agree**.

Could-not-check: nothing on the checklist was skipped. The only inference
made anywhere is that `trades_truncated` appears conditionally (it was absent
from `/api/status` with 39 open rows; the cap is 400).

## What I did NOT do

- **Caddy untouched** — no file under `/etc/caddy/` read or written, no
  reload. The new dashboard is reachable ONLY via loopback/SSH tunnel.
- **Cutover (phase 3) not run** — engine still serves the old dashboard on
  `:8080` with `--web`; `deploy.sh`'s health gate still points at `:8080`.
- **`:8081` is loopback-only** — no firewall change, no `0.0.0.0` bind.
- **F1/F3–F7 not fixed** — recorded only; none met the "one line + covered by
  an existing test" bar. F2's fix is one line but uncovered.
- `betting-live-maker` observed `inactive` — not investigated (out of scope).
- The monthly `rotate_trade_logs.py` cron registration (the runbook's "one
  decision left for you") — deliberately left to Sam.
- No off-box backup arranged for `rollup.json`/`rollup.json.bak.gz` — flagged:
  nothing backs up `manager/state/` today either, and once an archive is
  pruned (12 kept), `rollup.json` is the only copy of that history.

## For Sam — next commands

View the dashboard now:

```bash
ssh -L 8081:127.0.0.1:8081 root@129.212.176.202
```

then open http://localhost:8081/ (basic-auth: same credentials as the old
dashboard — first line of `.dashboard_auth`).

When you've lived with it and want the TLS front door moved:
`/dashboard-deploy-phase2` steps 3–4 (Caddy), then later `/dashboard-cutover`.
Worth deciding soon: F1 (a small P-022b-style patch to `_pnl` + a test), F2
(`TimeoutStopSec=5` or the one-line thread fix), and the `rollup.json` backup
story. Tomorrow: confirm the 06:07 timer run picks up the 06:00 rotation
(phase 1 step 5 — left open by design).
