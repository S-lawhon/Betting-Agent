# Claude Code Task — P-001: Diagnose the CLV Capture Failure (highest-value unglamorous test)

> This is a **diagnostic**, not a strategy test. It may be quietly making a live pod's gate unreachable, which would mean P-001 can never resolve either way.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper-mode; **no real orders, ever**). Read `CLAUDE.md` first.

P-001's pre-registered gate is **200 forward CLV rows** in `data/trade_logs/clv_log.jsonl`, produced by the daily `scripts/clv_settlement.py` cron on the droplet. That job was repaired on 2026-07-21 (it had been structurally guaranteed to find zero settlements on any day the trade log rotated). The repair worked — but left a recorded open question:

> "Of 14 newly-found settled bets, only **4 produced records** — `close_fair()` returned nothing for 10 games. Cause unknown (Odds API historical depth, quota, or team-name normalisation). Worth a look; it caps capture rate."

Nobody has looked. At ~29% capture the 200-row gate may be years away, which means **the pod's validation is silently unfalsifiable** — the exact failure mode P-015's locked decision rule exists to prevent.

## What to test — `close_fair()` has FIVE silent `return None` paths
Read `scripts/clv_settlement.py`. Every one of these returns `None` indistinguishably:

1. **HTTP/exception** on the Odds API historical call (`except: return None`) — quota exhaustion, timeout, 4xx.
2. **No name match**: `{norm(home), norm(away)} == {names[0], names[1]}` fails. `norm()` strips everything but lowercase letters — brittle against accents, "St." vs "St", relocations, and any Kalshi-vs-OddsAPI naming drift.
3. **>3h commence_time delta** between the ticker-derived expected start and the nearest candidate.
4. **No Pinnacle bookmaker** in that historical snapshot (Pinnacle coverage is not guaranteed at every timestamp).
5. **Pinnacle present but** no `h2h` market, or team names absent from the outcomes dict.

**Additional hypothesis to test explicitly — possibly the biggest one:** the job is **MLB-only by construction** (`parse_mlb_ticker_start`, and the endpoint hardcodes `sports/baseball_mlb`). P-001 trades the global `odds_api.sports` list (NFL, NBA, NCAAB, NHL, MLB, tennis). If non-MLB settled bets are entering the candidate set and dying at path 2, **the 200-row gate is denominated against MLB volume alone** and the gate's timeline assumption is wrong. Determine whether non-MLB bets are being fed to `close_fair()` at all, and whether the gate should be re-scoped or the job generalised.

## Task
1. **Instrument, don't guess.** Refactor `close_fair()` to return a `(result, reason)` pair or raise typed sentinels, so each failure path is distinguishable. Keep the public behaviour identical for callers.
2. **Replay offline against the existing corpus.** Build `scripts/diagnose_clv_capture.py` that re-runs the candidate-selection logic over the active log **plus all archives** (not just `CLV_ARCHIVE_LOOKBACK`), and produces a **failure-reason histogram**: how many settled P-001 bets die at each of the five paths, split by sport and by date. **Cache every Odds API response to disk** and re-run from cache — historical calls cost real credits. Sample if needed; log spend in the report.
3. **Quantify the ceiling.** Given the measured per-reason loss rate, project the realistic rows/week and the date the 200-row gate would actually be reached. State it plainly.
4. **Fix what is cheaply fixable.** Almost certainly: a real team-name alias map instead of `norm()`, and widening the Pinnacle requirement to a de-vigged sharp-consensus fallback **only if** you can show it does not change the meaning of the metric. Any change to what "closing fair" means is a **material change to a pre-registered gate metric** — do not make it unilaterally; propose it in the report and stop.
5. **Backfill.** If a fix recovers historical rows, backfill them (the job dedupes by fingerprint, so this is safe) and report the new row count.

## Gate / decision
Produce `research/REPORT_CLV_Capture_2026-07.md` with:
- The failure-reason histogram, by sport and date.
- The projected 200-row date at current and at post-fix capture rates.
- A clear recommendation among: **(a)** capture is fine, gate is reachable, no action; **(b)** fixable — here is the fix and the backfilled count; **(c)** structurally unreachable — the gate needs re-scoping, and here is the proposed re-scope for Sam to approve.

**Do NOT change the P-001 gate threshold or its metric definition yourself.** Re-scoping a pre-registered gate is Sam's decision — this task produces the evidence for it.

## Definition of done
Diagnostic script + instrumented `close_fair()` committed; report committed with the histogram and the projected gate date; any safe fix applied and backfilled; any gate-semantics change proposed but NOT applied; Odds API spend logged.
