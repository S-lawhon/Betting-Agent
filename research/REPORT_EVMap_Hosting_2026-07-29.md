# EV-Map weather collectors: moved to the droplet — 2026-07-29

**Verdict: FIXED for four of five jobs. The 30-day clock starts
`2026-07-28T03:11Z`, projecting completion `2026-08-27`. The weekly archiver
is BLOCKED and its timer is deliberately disabled.**

---

## 1. What was wrong, corrected in two places

The brief's diagnosis was right about the cause and wrong about its reach.

**Confirmed:** `data/cron_paper.log` and `data/cron_weather.log` are full of

```
/Library/Developer/CommandLineTools/usr/bin/python3: can't open file
'src/weather_paper_maker.py': [Errno 1] Operation not permitted
```

`cd` succeeded and python ran — it is the *script* python could not open.
macOS TCC denies `cron` read access under `~/Desktop`. **It fails awake.**

**Correction 1 — the weekly archiver failed a DIFFERENT way.**
`data/cron_archive.log` does not exist *at all*, while the other two logs
exist and are full of TCC errors. A cron line that ran and failed would have
created its log. So the archiver line **never executed**: it fires Sunday
03:17 local, and cron does not run jobs missed while the Mac is asleep. Two
failure modes, one host, one fix.

**Correction 2 — the archiver has never succeeded ANYWHERE, ever.**
`settled_archive.parquet` and `archive_state.json` have never existed. This is
not "≥2 missed cycles (07-19, 07-26)" — there is no archive to have gaps in.

**Also measured:** the local weather archive holds 37 ensemble files, all
stamped `20260719`, and `weather_fair_*.csv` stops at `2026-07-20`. The
pipeline last produced anything on **2026-07-20**, so the dead window is
07-21 → 07-27, one day longer than the brief's 07-22 → 07-27.

**Permanently lost:** ~1,200 point-in-time quotes/day × 7 days. The public
horizon is 90 days and quotes are point-in-time; they cannot be re-pulled.

---

## 2. New host and scheduling

**Host:** the droplet, `129.212.176.202`, as `bettingbot`, under
`/opt/betting-pod-shop/kalshi-ev-map/`.

**Mechanism: systemd timers, not cron.** Cron on this droplet had already
produced one silent-failure class, and a timer gives `systemctl status` and
`systemctl list-timers` a real answer.

| unit | schedule | what |
|---|---|---|
| `evmap-weather-sheet.timer` | `*-*-* 06:53 America/Chicago` | ensemble → bracket fair values |
| `evmap-weather-depth.timer` | `*-*-* 09,12:23 America/Chicago` | book-depth snapshot |
| `evmap-paper-maker.timer` | `*-*-* 07..19:13,43 America/Chicago` | **the 30-day clock** |
| `evmap-paper-eval.timer` | `*-*-* 07:37 America/Chicago` | previous day vs the public tape |
| `evmap-archive-settled.timer` | `Sun *-*-* 03:17 America/Chicago` | **DISABLED — see §5** |

Two scheduling decisions worth stating:

* **`America/Chicago`, not UTC.** These reproduce the Mac crontab's local
  schedule exactly, and the weather jobs are keyed to US local calendar days —
  `weather_paper_maker` only quotes a day's market before 10:00 local.
  Rewriting them in UTC would silently shift the whole schedule at every DST
  change. Validated with `systemd-analyze calendar` against each expression.
* **`Persistent=true`.** A droplet does not sleep, but a reboot would
  otherwise drop a run with no trace — which is the archiver's Mac failure
  mode reproduced on new hardware.

Dependencies installed into the existing venv: `pandas 3.0.5`, `numpy 2.5.1`,
`pyarrow 25.0.0`. Continuity state copied across (~92 KB + a 1.7 MB ensemble
archive): `weather_station_params.json`, `paper_quotes.parquet`,
`weather_depth.parquet`, `paper_eval_2026-07-19.parquet`, two
`weather_fair_*.csv`, and all 37 archived ensembles. **The 3.2 GB of bulk
research parquet stayed on the Mac** — none of it is an input to these jobs.

---

## 3. The alarm — the actual point of the task

`scripts/evmap_job.py` wraps every run and appends one record to
`kalshi-ev-map/data/job_status.jsonl`:

```json
{"job": "paper_maker", "ok": true, "exit_code": 0, "duration_s": 12.0,
 "rows_before": 3708, "rows_after": 3822, "rows_added": 114,
 "consecutive_failures": 0, "stderr_tail": [...]}
```

**Rows are measured from the job's own output before and after — not inferred
from stdout, and never from the exit code.** A run that exits 0 having written
nothing it was contracted to write is recorded as `ok: false`. That is the
half of the 139 an exit code could never have seen.

`manager/collect.py::evmap_jobs` surfaces the last record per job plus a
same-day row total; `manager/checks.py::check_evmap` alarms on **three
conditions that were all indistinguishable from health**:

| condition | severity |
|---|---|
| job failed, 1 consecutive | `warn` (alert.py needs two runs to page) |
| job failed, ≥2 consecutive | **`critical`** — the pattern that ran to 139 |
| job has not run inside its own cadence | **`critical`** |
| the DAY collected 0 rows, every run exit 0 | `warn` |
| status file missing entirely | `warn`, reported as *unmeasured*, never as fine |

**14 tests**, including the two the live run itself taught (§4).

---

## 4. Verified by data, not by exit code

First runs on the droplet, read back from the status log:

| job | exit | rows | verdict |
|---|---:|---|---|
| `weather_sheet` | 0 | **+114** (`weather_fair_2026-07-28.csv`, 19 new ensembles) | OK |
| `weather_depth` | 0 | **+120** (180 → 300) | OK |
| `paper_maker` | 0 | **+114 quote intents** (3,708 → 3,822) | **OK — the clock starts** |
| `paper_eval` | 0 | +0 | OK — 2026-07-27 has no quotes to evaluate, because the pipeline was dead. Correct, not a failure. |
| `archive_settled` | −9 | +0 | **FAILED — OOM. §5** |

Then through the full manager path on the droplet — collector → `status.json`
→ checks — with `faults: []` and exactly one finding:
`WARN EV-Map archive_settled failed (1 consecutive)`. Everything healthy is
silent, which is the contract.

**Two bugs the live run caught in the alarm itself**, both now pinned by tests:

1. **A per-day output cannot be measured as a delta.** `weather_pipeline`
   writes one CSV per target day; yesterday's had 114 rows and today's has
   114, so `after − before = 0` and the first live run was reported as a
   *failure* on a perfectly healthy pass. Per-day jobs now count only a file
   whose mtime is at or after the run's start.
2. **The freshness test had 1 s of slack**, which let yesterday's file satisfy
   a run that wrote nothing — reintroducing the exact blindness being fixed.
   Removed; both timestamps come from the same clock.

---

## 5. The archiver: BLOCKED, and stopped on purpose

```
first run : OOM-killed at 433 s, 1.66 GB RSS on a 2 GB box
            "Out of memory: Killed process 2322468 (python)" in dmesg
```

Cause: it accumulated **every settled market of the window** in one list and
built one DataFrame, applying the filter that drops zero-volume MVE parlay
husks only *afterwards*. On a 16 GB Mac that was merely wasteful.

I made the accumulation chunked — 20,000 rows at a time, filtering per chunk.
**That is a memory fix, not a methodology change**, and it is exactly
equivalent rather than an approximation: `build_frame` is row-wise and the MVE
filter is row-wise, so filtering per chunk and concatenating yields the
identical frame. No field, threshold or semantic changed.

**It was still not enough.** The re-run was past 1.4 GB used with 95 MB free
at 35 minutes and still climbing. **I stopped it and disabled its timer.** The
same 2 GB box runs `betting-pod-shop` and `betting-round-leader-fade`, and
letting the OOM killer choose between the archiver and P-022 the night before
P-022's first placement window is not a trade worth making. Memory returned to
700 MB used / 1,267 MB available; both units verified `active` afterwards.

**The remaining problem is the window, not the loop.** With no
`archive_state.json`, `min_close` defaults to *now − 10 days* and the job
pulls a ten-day slice of all settled Kalshi markets, the large majority of
which are husks. That will be true of every weekly run, not just the first.

**Per the stop rule this is reported, not fixed.** Two options, both real
changes to the collector and therefore Sam's:

* write the parquet incrementally per chunk so nothing but one chunk is ever
  resident, or
* seed `archive_state.json` so the window is a day rather than ten, then let
  the weekly cadence keep it small.

Until then the 90-day horizon keeps rolling and whatever falls off it is gone.
This is a real, ongoing loss — but it is the *calibration* archive, not the
Build 2 evidence clock, which is now running.

---

## 6. The Mac side — one thing I could not finish

**The Mac crontab is UNCHANGED.** `crontab <file>` hung indefinitely from this
session — the same class of macOS permission gate that broke the jobs in the
first place, and not something I can clear non-interactively. Backups were
written first: `~/mac_crontab.bak.2026-07-29` (original) and the prepared
replacement in the session scratchpad.

**There is no race, and the reason is the bug itself.** All five Mac entries
are inert: TCC prevents python from opening the scripts, so they will keep
failing exactly as they have 139 times and cannot write to any file the
droplet also writes. The archiver line has never executed at all. The risk of
leaving them enabled is log noise, not data corruption.

To finish it, one command:

```bash
crontab ~/mac_crontab.bak.2026-07-29 && crontab -l | grep kalshi-ev-map
```

— or edit interactively with `crontab -e` and comment the five
`kalshi-ev-map` lines. **Do this before granting cron Full Disk Access**, or
the Mac jobs come alive and both hosts append to the same parquet files.

**The old local data stays on the Mac** and is not deleted: 3.2 GB under
`kalshi-ev-map/data/`, of which the droplet received only the ~1.8 MB of
continuity state. The `paper_quotes.parquet` lineage is continuous — the
droplet's copy starts from the Mac's 3,708 rows and has grown to 3,822.

---

## 7. Registry

`manager/registry.yaml`: the two Mac job entries (`weather_paper_maker`,
`weather_archiver`) are replaced by five droplet entries with their timer
names, and the header comment that blamed sleep is corrected to TCC. The
R-EV-MAP gate now carries the clock explicitly:

```yaml
metric: days_live
threshold: 30
clock_start_utc: "2026-07-28T03:11:00Z"   # first verified droplet run, +114 by row count
projected_completion_utc: "2026-08-27"
note: >
  Days are counted from the paper maker's own rows, not from calendar: a day
  on which evmap_paper_maker collected zero rows is not a day live.
```

That last line matters. The previous clock was calendar-based, and calendar
time is exactly what accrued through 139 failed runs.

---

## 8. Deploy

```bash
bash scripts/deploy.sh 129.212.176.202          # sync only, NO restart
```

* `betting-pod-shop` — **not restarted** (unchanged)
* `betting-round-leader-fade` — **not restarted** (unchanged)
* new units installed to `/etc/systemd/system/`, `daemon-reload`, four enabled
  and started, `evmap-archive-settled.timer` **disabled**
* venv gained `pandas`, `numpy`, `pyarrow`

## 9. Decisions for Sam

1. **Disable the Mac crontab** (§6, one command). Mandatory before any Full
   Disk Access grant.
2. **How to unblock the archiver** (§5): incremental parquet writes, or seed
   `archive_state.json` to shrink the window. Every week it stays off, more of
   the 90-day horizon is lost — but it has never worked, so nothing is
   *newly* broken.
3. **The droplet is a 2 GB box now running six workloads.** The archiver found
   the ceiling; it is worth knowing before something with a deadline finds it.
