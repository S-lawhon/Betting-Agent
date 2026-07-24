# Claude Code Task — P-025: Music-Chart Mid-Week Edge (Backtest + Forward Log)

> Paste this whole file into Claude Code as the task. Background: `Deep Research R4 - Terms Census & Chart Markets 2026-07.md` (project root, §1). Phase 1 only, then STOP and report.

## Role & context
You are working in the **Betting Pod Shop** repo (`~/Desktop/Betting Fund Project`), a paper-mode Kalshi trading engine. Paper/demo only — **no real orders, ever**. Read `CLAUDE.md` and `PROJECT_STATUS.md` first.

Thesis (house archetype: external info in an unwatched corner): the Billboard #1 album/song is largely knowable **days before Kalshi's chart markets close** from free public data, and Kalshi's stan-retail books lag it. Verified so far: settled winners traded 85–93¢ mid-tracking-week and 87–89¢ *after* the tracking week closed (7–15¢ gaps vs a ≤1¢ taker fee); the reference (Talk of the Charts) self-audits 52/52 on final #1 calls; and `KXTOPSONG-26AUG08-CHO` printed 85/88 on a 68-day incumbent this week.

Key market mechanics (verified): series **KXTOPSONG** (Hot 100 #1) and **KXTOPALBUM** (Billboard 200 #1), weekly, rules `RANKLISTBILLBOARDCHARTS.pdf` (settle on Billboard's official chart; post-expiration revisions ignored; ties split). Tracking week runs Fri–Thu; **Kalshi books stay open until Sunday 23:59 ET**; Billboard announces ~Tuesday. Entertainment series are `quadratic` → zero maker fee; taker fee `0.07·P·(1−P)` ≈ 0.6¢ at 90¢.

## Non-negotiable guardrails
- **Backtest + forward-log FIRST. Build no pod/config/service until the gate passes and I approve.** (House discipline: P-019/P-021 died at this gate; P-022 passed it and only then got a spec.)
- Read the rules PDF yourself (`https://assets.kalshi.com/contract_terms/RANKLISTBILLBOARDCHARTS.pdf`) and quote the operative settlement/tie clauses in the REPORT.
- **Kalshi API gotchas:** list endpoints null bid/ask and settled-list volume — use candlesticks (`/series/{S}/markets/{T}/candlesticks`, `period_interval=60`, prices in `price.close_dollars`, size in `volume_fp`). Shared 2 req/s rate budget — cache everything to disk.
- **Fill realism in thin books:** never credit more size than the entry-hour `volume_fp`; compute PnL at the candle close ± half the observed spread, and flag any week where the assumed entry price had zero traded volume.
- **Survivorship guard on the reference:** grade Talk of the Charts calls ONLY from timestamped original posts; grade HITS mid-weeks from dated archive URLs (`/charts/midweek-20/YYYY-MM-DD`). If a week's reference can't be reconstructed from a timestamped source, EXCLUDE the week (log it) rather than back-fill.
- Cluster all statistics by **chart-week** (one chart = one cluster).

## Phase 1a — Settled-data backtest
Create `chart_research/backtest_charts.py` + `chart_research/data/` cache.

1. Enumerate all **settled** KXTOPSONG and KXTOPALBUM events (paginate; expect ~20–30 weeks since launch).
2. For each week, reconstruct the signal at three decision times:
   - **T-mid** (Tue/Wed of tracking week): HITS Midweek 20 archive for that week (scrape the dated URL; the chart rows are embedded in the page payload) + ToTC early call if a timestamped post exists.
   - **T-close** (Fri, tracking week just ended): kworb daily archive (full-week streaming picture) + ToTC final call.
   - **T-late** (Sat/Sun, book still open): same data, outcome near-deterministic.
3. Pull hourly candlesticks for the leading strike(s) at each decision time; record price, spread proxy, `volume_fp`.
4. Compute `gap = signal_implied_prob − kalshi_price` per decision time. Simulate: take YES when `gap > fee + 0.03`, size capped at entry-hour volume; settle on the actual chart result.
5. Metrics, week-clustered: hit rate of the signal at each decision time (re-graded from originals — report YOUR count, not ToTC's claim); net-of-fee ¢/ct with bootstrap CI; total capturable $ per week at the volume caps (the capacity number); and the **contested-week split** (weeks where HITS margin <20% vs ≥20%) — the thesis says restrict to wide-margin weeks, verify that's where the edge is.

## Phase 1b — $0 forward log (runs alongside, 6 weeks)
Create `chart_research/forward_log.py` (cron-able, read-only): every Tue and Fri, snapshot {HITS midweek leader + margin, kworb #1 + margin, Kalshi ask on the corresponding strikes}. Append JSONL. This is the decay/pick-over detector: if Friday asks sit ≥97¢ every week and Tuesday asks ≥93¢, the corner is watched — that finding kills the pod even if the backtest passes on older weeks.

## Gate (report explicitly)
- **ADVANCE** if: re-graded signal accuracy at T-close ≥95% on wide-margin weeks AND net edge ≥3¢/ct with week-clustered CI excluding zero AND capacity ≥$500/week at honest volume caps.
- **KILL** if the gaps in settled data disappear under the volume-cap fill model (i.e., the visible 7–15¢ gaps carried no tradeable size), or signal accuracy re-grades materially below the self-audit.
- **MARGINAL** → keep the forward log running and re-run in 6 weeks.

Deliver `chart_research/REPORT_Charts_2026-07.md` (quoted rules, re-graded accuracy table, gap/PnL/capacity tables by decision time, contested-week split, verdict) + `p025_params.json`. **STOP after the REPORT — no pod until I approve.**

## Definition of done (Phase 1)
REPORT committed with explicit verdict; forward-log collector running (read-only, local or VPS per `scripts/` conventions); raw pulls cached; nothing built beyond `chart_research/`; nothing placed live.
