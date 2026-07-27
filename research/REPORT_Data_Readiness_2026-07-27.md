# Data-Readiness Audit — one workstream is ahead, one is exactly on time, one has been dead since the day it was installed

**Task:** `research/prompts/PROMPT_OPS_Data_Readiness_Audit.md`
**Run:** 2026-07-28 · **read-only** · nothing restarted, no cron changed, no deploy
**Verdict:** P-018 **ahead of schedule** · MLB props **on time, with zero slack** · EV-Map Build 2 **DEAD, 100% of runs failing, and not for the reason anyone expected**

---

## 0. The table

| workstream | claimed gate | measured progress | source | alive? | true expected date |
|---|---|---|---|---|---|
| **P-018** in-play ticks | backtest needs ≥10 game-days | **27,307 in-play ticks · 80 games · 7 game-days** | `data/book_capture/*.jsonl(.gz)` (droplet) | **ALIVE** | gate runnable **~2026-07-30**, five days *ahead* of the Aug 4 estimate |
| **MLB props** exec test | 27 clean ET game-days by 2026-08-17 | **5 complete clean + 1 in progress** (9 total, 3 contaminated) | `/opt/mlb-props/mlb_props_research/data/live/snapshots_*.jsonl` | **ALIVE** | **2026-08-17 exactly** — zero slack, one missed day slips it |
| **EV-Map Build 2** weather | 30 days live, ≥10 cities | **3 days** (07-19/20/21), 19 cities | `kalshi-ev-map/data/paper_quotes.parquet` (Mac) | **DEAD** | **never**, at the current state |

And one finding the task did not ask for, which outranks two of the three:

> **P-018's entire implementation is not in the repository.** 1,688 lines across
> 7 files — `src/inplay_surprise.py`, `src/inplay_sport_adapter.py`,
> `inplay_research/`, and 29 tests — exist only on the unmerged branch
> `p018-inplay-fade-core` (commit `4ff5bea`). `git merge-base --is-ancestor
> 4ff5bea HEAD` → **NO**. They are absent from `HEAD`, absent from the droplet,
> and absent from the 1,593-test suite. The data this workstream is waiting for
> is arriving on schedule; the code that consumes it is not on the branch
> anyone is working on.

---

## 1. P-018 — in-play tick sample: **AHEAD**, and it will be ready before Aug 4

### Genuine in-play ticks, not rows

`book_capture` records carry no `in_play` flag, so liveness was derived from the
ticker's own encoded ET start (`KXMLBGAME-26JUL211840LADPHI-LAD` → 18:40 ET) and
a tick counted as in-play when `0 ≤ ts_utc − start ≤ 4.5h`.

| date | in-play ticks | markets | games | pre-game rows |
|---|---:|---:|---:|---:|
| 2026-07-21 | 704 | 16 | 8 | 30,533 |
| 2026-07-22 | 8,022 | 54 | 27 | 65,827 |
| 2026-07-23 | 3,716 | 26 | 13 | 70,897 |
| 2026-07-24 | 1,732 | 26 | 13 | 75,777 |
| 2026-07-25 | 5,868 | 58 | 29 | 61,000 |
| 2026-07-26 | 7,059 | 50 | 25 | 67,291 |
| 2026-07-27 (partial) | 206 | 2 | 1 | 44,435 |
| **total** | **27,307** | **160 distinct** | **80 distinct** | — |

**The harness's own gate is `n_game_days >= 10`.** Seven game-days are banked
after seven days of capture, so the tenth lands **~2026-07-30** — five days
earlier than the Aug 4 estimate. Of the 447,628 BOOK records captured, 6.1% are
in-play; the rest are pre-game, which is expected given a 60-second cadence
across a whole slate.

### The coverage gap, measured from the DISCOVERY records rather than assumed

Every truncation is written into the data. Across all **581 DISCOVERY records**:

```
discovered = 45,774    captured = 30,245    dropped = 15,529    →  33.9% dropped
```

Dropping is **lowest-volume-first**, so the surviving sample is biased toward
liquid markets. For a maker strategy that is arguably the right bias, but it is
a bias and the backtest must state it: P-018's replay will be over the *liquid
third to two-thirds* of each slate, not the slate.

### Can the rate be raised toward 5.0 req/s? **No — it is already being pushed down.**

The configuration has in fact already been raised twice (visible in the
DISCOVERY records themselves):

| `rate_per_s` | `max_markets` | DISCOVERY records |
|---:|---:|---:|
| 2.5 | 45 | 19 |
| 3.0 | 54 | **538** |
| 4.0 | 72 | 24 |

But the 429s did **not** stop when `kalshi.rate_limit` was root-caused:

```
Jul 27 16:30:17  HTTP 429 — throttling down to 0.86 req/s (126 total)
Jul 27 17:00:29  HTTP 429 — throttling down to 1.20 req/s (127 total)
Jul 27 17:01:29  HTTP 429 — throttling down to 0.60 req/s (128 total)
```

**128 cumulative 429s, 48 in the last 24 hours.** The daemon's own back-off is
driving it to 0.6–1.2 req/s — well *below* its configured 3.0 — and it logs
"Capture yields to the trading services," which is the correct priority.

> **Recommendation: do not raise the rate.** It would not increase throughput
> (the limiter is the exchange, not the config) and it would compete with the
> live pods for the same budget. The gate arrives early anyway. If more coverage
> is wanted later, the lever is **cadence** (60s → 90s across more markets), not
> request rate.

---

## 2. MLB props — **on time to the day, with no slack at all**

Re-bucketed from each record's own UTC `ts` into ET, not from filenames:

| ET day | rows | ET window | status |
|---|---:|---|---|
| 2026-07-19 | 305,338 | 13:59 → 23:59 | contaminated (pre-fix) |
| 2026-07-20 | 337,235 | 00:04 → 23:59 | contaminated |
| 2026-07-21 | 359,616 | 00:04 → 23:42 | contaminated (fix landed ~20:15 UTC) |
| 2026-07-22 | 348,027 | **10:00** → 23:44 | **clean** |
| 2026-07-23 | 239,509 | **10:00** → 23:43 | **clean** |
| 2026-07-24 | 470,514 | **10:00** → 23:42 | **clean** |
| 2026-07-25 | 185,056 | **10:00** → 23:43 | **clean** |
| 2026-07-26 | 206,200 | **10:00** → 23:41 | **clean** |
| 2026-07-27 | 21,465 | **10:00** → 13:17 | in progress |

**9 distinct ET game-days total; 3 contaminated, 5 complete clean, 1 running.**
Every clean day opens at exactly 10:00 ET (the timer) and runs past 23:40 ET, so
the West-Coast truncation is genuinely fixed — that was the specific failure
mode and it is gone.

**No gaps.** Days 07-22 through 07-27 are consecutive. The service is healthy
(`mlb-props-collector.timer` active since 2026-07-21, last run 2026-07-27
14:00:01 UTC) and `Persistent=true` is set, so a reboot or missed window is
recovered.

### The date

Counting clean days only, from 07-22: **27 clean days lands on 2026-08-17** —
exactly the claimed date, with **zero days of slack**. Any single missed day
pushes the gate to Aug 18 or later.

> The registry currently reads `game_days_collected: 3  # 07-19, 07-20, 07-21`.
> That is hand-typed, four days stale, and counts the three *contaminated* days
> toward a target that should be clean days. I have not edited it — but it is
> the same hand-typed-progress pattern that made P-017 report `1`, and it should
> be derived.

---

## 3. EV-Map Build 2 — **DEAD, and the diagnosis is not "the Mac slept"**

The task flagged this as most likely quietly broken, against the hypothesis that
Mac cron silently skips while asleep. That hypothesis is **wrong, and the truth
is worse**: the jobs fail on **every** run, awake or asleep.

```
$ wc -l kalshi-ev-map/data/cron_paper.log        →  139
$ grep -c "Operation not permitted" .../cron_paper.log →  139       (139 of 139)

/Library/Developer/CommandLineTools/usr/bin/python3: can't open file
    'src/weather_paper_maker.py': [Errno 1] Operation not permitted
```

Same for `cron_weather.log` (`weather_depth.py`). `cron_archive.log`
**does not exist at all** — the weekly archiver has never produced a single line
of output.

**Root cause: macOS TCC.** `cron` has no Full Disk Access, so it cannot read
files under `~/Desktop`. It is not a sleep problem, not a Python problem and not
a path problem — the process is denied the file. `cron_paper.log` was last
written **2026-07-27 12:13**, which proves cron is firing on schedule and the
job is failing every time.

### What is actually banked

`kalshi-ev-map/data/paper_quotes.parquet`: **3,708 rows across 3 distinct days**
— 2026-07-19 (912), 07-20 (2,454), 07-21 (342) — over **19 series/cities**
(`KXHIGHAUS`, `KXHIGHCHI`, `KXHIGHDEN`, `KXHIGHLAX`, `KXHIGHNY`, …) and 342
tickers. Those three days predate the cron install or were run by hand.

* **Cities: 19 ≥ 10 — satisfied.**
* **Days: 3 of 30.**

### Permanent data loss, quantified

* **6 days of weather paper quotes: 2026-07-22 → 2026-07-27**, gone. Against a
  90-day API horizon, quotes cannot be reconstructed after the fact.
* **At least 2 weekly archive cycles** (Sundays 2026-07-19 and 07-26 at 03:17).
  `kalshi_settled.parquet` is dated **Jul 19 14:17** — a manual run, not the
  cron. The absence of `cron_archive.log` confirms zero scheduled runs.
* Loss accrues at **1 day per day** until this is fixed.

### Proposed fix — **not applied** (read-only task)

Two options, in the order I would take them:

1. **Move it to the droplet** (recommended). The droplet already runs six cron
   jobs and two systemd timers reliably, is always on, and has no TCC. This is
   the same conclusion the prompt anticipated, and the audit supports it: the
   two workstreams that are alive both run on the droplet, and the one that is
   dead is the only one on the Mac. A systemd timer with `Persistent=true` also
   recovers missed windows, which cron never will.
2. **Grant `/usr/sbin/cron` Full Disk Access** (System Settings → Privacy &
   Security → Full Disk Access). One checkbox, keeps the current layout, but it
   leaves the workstream on a laptop that sleeps — so it converts a
   100%-failure into an intermittent one.

The 30-day clock **has not started**. Starting today, the earliest completion is
~2026-08-27; every day of delay moves it one day.

---

## 4. The finding the task did not ask for: P-018's code is on an orphan branch

```
$ git merge-base --is-ancestor 4ff5bea HEAD   →  NO — orphaned
$ git branch --contains 4ff5bea               →  p018-inplay-fade-core
```

Commit `4ff5bea` "P-018 core: surprise-gated in-play fade maker (paper,
pre-backtest)" adds 1,688 lines across 7 files, including 29 tests. None of it
is on `HEAD`, none is on the droplet, and `config_multi_pod.yaml` on the
mainline has no P-018 block.

**It is not lost** — the branch is intact locally — but it is invisible, and
merging is not a formality: the branch tip has since accumulated unrelated,
disruptive commits, among them *"Extract legacy Kalshi Arb Project to a separate
archived repo"*, which would remove the very module P-001's live scanner imports
(`src/engine.py` puts `Legacy/Kalshi Arb Project/src` on `sys.path`). A naive
merge would take P-018's code and P-001's removal together.

> **Recommendation: cherry-pick `4ff5bea` onto the working branch** rather than
> merging `p018-inplay-fade-core`, and do it *before* 2026-07-30, when the
> ≥10-game-day gate opens. Otherwise the data arrives on time and there is
> nothing on the mainline to run against it. **Not done here** — it is a
> branch-topology decision with a P-001-shaped hazard attached, and it is Sam's.

---

## 5. Every permanent data loss found

| loss | quantity | recoverable? |
|---|---|---|
| Weather paper quotes, 2026-07-22 → 07-27 | 6 days × ~1,200 quotes/day | **No** — 90-day API horizon, quotes are point-in-time |
| Weekly settled-market archives | ≥2 cycles (07-19, 07-26) | Partially — `archive_settled.py` can re-pull inside the horizon, but the horizon is finite |
| MLB props contaminated days 07-19/20/21 | 3 days, unusable as clean slate days | No — but they are not needed; the clean count starts 07-22 |
| P-018 in-play ticks | **none** — capture has been continuous since 07-21 | n/a |

---

## Appendix — reproduce

```bash
# P-018 in-play census + coverage gap (on the droplet)
ssh root@129.212.176.202 'cd /opt/betting-pod-shop && ./venv/bin/python /tmp/bc2.py'
journalctl -u betting-book-capture --since "24 hours ago" | grep -ci 429

# MLB props ET game-days
ssh root@129.212.176.202 'python3 /tmp/props.py'
systemctl list-timers mlb-props-collector.timer --all

# Weather
wc -l kalshi-ev-map/data/cron_paper.log
grep -c "Operation not permitted" kalshi-ev-map/data/cron_paper.log

# P-018 code location
git merge-base --is-ancestor 4ff5bea HEAD && echo reachable || echo orphaned
```
