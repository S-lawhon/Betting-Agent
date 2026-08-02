# Dashboard — source-of-truth map

Rebuilt 2026-07-30. This file answers one question: **for any number on the
dashboard, which process produced it and which file is it in?**

If you are debugging a wrong or missing figure, start here, not in the template.

---

## The rule the whole design serves

> A value is rendered only when its source is available *and* the field is
> non-null. Everything else renders `— unknown` with the source path in the
> tooltip.

`docs/GATE_INSTRUMENTATION_STANDARD.md` §3 forbids a reader returning a
fabricated default. The old dashboard broke that rule in JavaScript —
`dashboard.html:879` fell back to `bankroll = 10000` whenever the risk snapshot
was absent, so ROI, Sharpe, drawdown and return-on-bankroll all rendered against
a number that did not exist. `tests/test_dashboard_template.py` now fails the
build if any `|| 0` / `?? 0` fallback reappears in a formatter.

And §1: **a dashboard tile is not a verdict.** Gate numbers are displayed, never
recomputed. The reader's own verdict string is passed through verbatim as
`reader_verdict`; the separate `progress_state` field is a UI grouping label and
is named differently so it can never be mistaken for one.

---

## Processes

| Process | Unit / schedule | Writes | Never does |
|---|---|---|---|
| **Engine** | `betting-pod-shop.service`, every 300 s | `data/dashboard/engine_state.json`, `data/dashboard/open_positions.json` | — |
| **Manager collector** | cron, every 15 min | `manager/state/status.json` | *unchanged by this rebuild* |
| **Rollup** | `dashboard-rollup.timer`, hourly at :07 | `data/dashboard/rollup.json` (+ `.bak.gz`) | never builds a `TradeStore` |
| **Research intake + triage** | `research-intake.timer`, daily | `data/research_intake/metrics.json`, `data/research_triage/latest_manifest.json` | never invokes an agent or claims edge |
| **Dashboard** | `betting-dashboard.service`, `127.0.0.1:8081` | **nothing** (`ReadOnlyPaths`, no `ReadWritePaths`) | never fetches a price, never evaluates a gate |
| **Caddy** | `caddy.service` | TLS + basic auth in front of 8081 | — |

The dashboard holds no reference to the engine. That is what makes it work while
the engine is down — and it reports "down" from systemd's own view in
`status.json` rather than inferring it from silence.

---

## Panel → source

| Panel | Field(s) | Comes from |
|---|---|---|
| Paper ribbon | `mode.all_paper`, `pod_modes` | `status.json:invariants` |
| Engine liveness | `engine.liveness` (4-valued) | `engine_state.json._meta.written_at_utc` + `status.json:services[betting-pod-shop]` |
| Kill switches | existence only | `data/KILL_*` (`stat`, no read) |
| Last cycle | `cycle.*` | `engine_state.json` (the engine's own `CycleReport`) |
| Gate board / Gates | progress, threshold, verdict, `blocked_on`, rate, projection | `status.json:throughput.records` + `status.json:<pod>.checkpoint` + `workstreams` |
| Alarms / Ops findings | severity, title, fix | `manager.checks.run_checks(status.json)` — in-process, nothing reimplemented |
| Realized P&L, equity curve, per-pod attribution | daily/lifetime counters | `rollup.json` |
| Open exposure, bankroll | `risk.*` | `engine_state.json` **only** — never back-derived |
| Open positions | rows | `open_positions.json` (capped at 400) |
| CLV | means per pod | `rollup.json:clv` (log fields `pinn_fair_close` / `clv_net_maker`, as written on disk — **not** `src/clv.py`'s `CLV_FIELDS`, which differ) |
| Research operations | lifetime funnel, 24h activity, per-agent pending/overdue queues, oldest task, X pilot yield and conservative cost | `data/research_intake/metrics.json` |
| P-022 funnel | 8 stages, state, refusals | `data/p022_window_check/status.jsonl`, live tail |
| Placement rate by week | placed / skipped / rate | `rollup.json:weekly_by_pod` |
| Skip reasons | 30-day window + lifetime | `rollup.json:skip_reasons` |
| Services, jobs, faults | — | `status.json` |
| Brief tab | whole page | `GET /manager`, server-rendered by `manager/brief.py` in an iframe |

Every block in `/api/v2/dashboard` also carries a `source` string, and
`sources{}` carries `{available, path, mtime_utc, age_seconds, stale, reason}`
per source. The Ops tab renders that table directly, so "why is this blank?" is
answerable from the page itself.

The Research Operations panel deliberately labels a dispatch as task creation,
not agent execution. Until a model invocation/claim event is implemented,
"started" remains explicitly untracked; only a durable
`ResearchDisposition` counts as reviewed or completed. The same contract feeds
the server-rendered daily brief and its email, preventing the dashboard and
email from presenting different research progress.

---

## Code

| File | Role |
|---|---|
| `src/dashboard_sources.py` | The **only** filesystem reader. One loader per source, each returning `(payload, meta)`. Never raises; size-guarded. |
| `src/dashboard_api.py` | `build_v2(sources) -> dict`. **Pure function, zero I/O** — which is what makes every degradation path a unit test. |
| `src/dashboard_server.py` | `python -m src.dashboard_server --port 8081`. Builds `FileBackedState`, reuses the existing handler. |
| `src/web_dashboard.py` | `DashboardState` (v1, unchanged semantics) + `write_snapshot()` + `FileBackedState` + the new routes. |
| `src/templates/dashboard_v2.html` | The SPA. Self-contained: no CDN, no `<script src>`, no web font. |
| `scripts/build_dashboard_rollup.py` | The rollup producer. |

---

## Routes

| Route | Auth | Notes |
|---|---|---|
| `/health` | open | literal `ok`. `scripts/deploy.sh` gates on it — do not change the body. |
| `/healthz` | open (loopback-bound) | JSON: version, per-source ages, engine liveness. **Stays 200 when a source is stale** — a dashboard that refuses to load because the collector died is worse than one that tells you the collector died. |
| `/` | basic | the SPA |
| `/api/status` | basic | **v1, strict superset.** Additive keys only: `engine_state_available`, `engine_state_age_seconds`, `engine_state_path`, `engine_state_reason`, `dashboard_version`, `dashboard_mode`, `trades_truncated`. |
| `/api/v2/dashboard` | basic | primary feed; `?section=now,gates,pnl,pipeline,ops,work`; ETag → 304 |
| `/api/v2/rollup` | basic | raw rollup, for debugging |
| `/manager`, `/api/manager` | basic | unchanged |

**Why v1 still exists:** `src/health_check.py` and 56 tests in
`tests/test_web_dashboard.py` pin it — the `engine_status` vocabulary
(`starting|running|halted`), the absent-until-populated `risk`/`cycle`/`pods`/
`settlement` blocks, and percentages-rather-than-fractions. The UI renders only
v2.

**One known soft spot, documented rather than hidden:** with no snapshot file,
v1 must return `engine_status: "starting"`, so a long-dead engine reads as
merely booting on v1. Mitigated three ways — `engine_state_available` and
`engine_state_age_seconds` carry the truth, v2 has the 4-valued `liveness`, and
`health_check.check_engine_freshness()` FAILs past `3 × interval`.

---

## Operating it

```bash
# is the dashboard itself healthy?
curl -s localhost:8081/healthz | python3 -m json.tool

# rebuild the rollup by hand (first run, or after suspecting the checkpoint)
nice -n 19 ionice -c3 venv/bin/python -m scripts.build_dashboard_rollup \
    --root /opt/betting-pod-shop --full

# what did the last rollup actually do?
venv/bin/python -m scripts.build_dashboard_rollup --dry-run --json
```

### The rollup's two load-bearing decisions

1. **The checkpoint lives inside `rollup.json`**, under `_checkpoint` — not in a
   sidecar. A sidecar opens a torn-state window: write the rollup, crash before
   the checkpoint, and the next run re-adds the same delta, producing a
   permanent double-count with nothing to detect it against.

2. **Archives are immutable and counted once; the active log is re-read in full
   every run.** Byte offsets into the active log are *not* a safe checkpoint:
   `rotate_active_log.rotate()` gzips the log verbatim into an archive and then
   rewrites the log with the still-open PLACED rows (same fingerprints). The
   first version of this script kept `(dev, inode, offset)` and double-counted —
   measured on a fixture, placed went 4 → 6 and realized P&L 5.25 → 10.50 after
   one rotation, because rows already consumed from the active log were counted
   again when the archive containing those same bytes appeared as a "new" source.
   PLACED rows are therefore deduplicated by fingerprint against the open set at
   the last archive boundary. `tests/test_dashboard_rollup.py` guards this
   directly — **that test must never be skipped.**

### ⚠ Back up `rollup.json`

Once `rotate_active_log` prunes an archive (`KEEP_ARCHIVES = 12`), the rows it
held survive **only** in these counters and are not derivable from anything on
disk. `--full` can rebuild only from surviving archives, so a full rebuild
silently shortens history — it prints a warning saying so. The timer passes
`--backup`, which writes `rollup.json.bak.gz`.

The cheapest way to reduce this exposure is to register
`scripts/rotate_trade_logs.py` (monthly `archive/YYYY-MM.jsonl.gz`) as a cron —
it is currently the only writer of that convention and is **not** in
`manager/registry.yaml`. Out of scope for the rebuild; noted as a decision.

---

## Deliberate non-goals

- **No mark-to-market.** The equity curve is realized settlements only, and says
  so in its caption. Marking open positions would need live prices, which a
  file-reading dashboard must not fetch.
- **No re-derived health verdicts.** `manager/checks.py` is the only judge. If
  the dashboard computed its own view of "healthy" it could disagree with the
  alerting path, and then neither number would be trustworthy.
- **No `manager/brief.py` port to JavaScript.** The Brief tab is an iframe over
  `/manager` for exactly that reason.
- **`manager/collect.py` untouched.** Its `trade_activity()` reverse-scan
  survives only because of a hard 24 h early exit inside an 80 MB budget;
  extending it to lifetime figures would put an unbounded read on the critical
  path of the process that pages when production is down. That is what the
  rollup job is for.
