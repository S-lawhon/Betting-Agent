# Run Summary — 2026-07-26 Research Queue

**Queue:** `research/prompts/RUN_QUEUE_2026-07-26.md` (10 tasks) · **All 10 completed.**
**Standing invariants held:** paper/demo only, no orders, no pod built, nothing added to
`pods.active`, **no deploy performed**, no live `data/` mutated. 14 commits, `f90cfb2..HEAD`.

The queue predicted "three clean KILLs, one integrity fix, and one ADVANCE is a good run."
The actual result: **six KILLs, three integrity fixes, zero ADVANCEs** — and the two
integrity findings are considerably more serious than the brief anticipated.

## One line per task

| # | Task | Verdict | Headline number |
|---|---|---|---|
| 1 | OPS repo hygiene | **FIXED** | 208 MB of caches → 10 MB archived; guard found **2 more** at-risk artifacts nobody knew about |
| 2 | P-017 settlement verification | **WORLD (b) — BLOCKED on Sam** | Settler silently dead since Jul 25 15:05 UTC; true gate progress **0 of 8**, not 1 |
| 3 | P-022 settler scalar fix | **FIXED** | **532** scalar markets booked at $0.00; **0 of 532** actually settle at zero |
| 4 | P-001 CLV capture | **FIXED / KILL** | Capture is 96.9%, not 29% — but **86%** of bets were priced off a different day's game |
| 5 | P-026 stat-leader dead-heat | **KILL** | Max co-leader **bid**-sum 99.0¢ against a hard 100¢ ceiling |
| 6 | P-020 Poly→Kalshi cross-venue | **KILL** | Brier-minimising weight on Polymarket is **0.0** (monotone sweep) |
| 7 | P-023 make-cut Phase 2 | **KILL** | −13.8¢/ct, CI [−23.9, −4.1], 0/10 jackknives; **harness reproduces P-022 exactly** |
| 8 | P-023c top-N over-priced fade | **KILL** | +3.2¢ gross decomposes to **+0.2¢** executable; the rulebook mechanic runs *against* the trade |
| 9 | P-027 ECONSTAT flips | **KILL** | **0 flips in 164** settled events; max settlement lag 7.15 h |
| 10 | Satellites (3 sub-studies) | **KILL / KILL / KILL** | Best award-tie trade +0.93¢ on 99¢ collateral; **0 of 1,514** ladder pairs executable |

## Reports produced

- [REPORT_P017_Settlement_Check_2026-07.md](REPORT_P017_Settlement_Check_2026-07.md)
- [REPORT_P022_Settler_Scalar_Fix_2026-07-26.md](../golf_quirks_research/REPORT_P022_Settler_Scalar_Fix_2026-07-26.md)
- [REPORT_CLV_Capture_2026-07.md](REPORT_CLV_Capture_2026-07.md)
- [REPORT_P026_Leader_DeadHeat_2026-07-26.md](REPORT_P026_Leader_DeadHeat_2026-07-26.md)
- [REPORT_CrossVenue_2026-07.md](../crossvenue_research/REPORT_CrossVenue_2026-07.md)
- [REPORT_MakeCut_Phase2_2026-07.md](../golf_quirks_research/REPORT_MakeCut_Phase2_2026-07.md)
- [REPORT_P023c_TopN_Overpriced_Fade_2026-07.md](../golf_quirks_research/REPORT_P023c_TopN_Overpriced_Fade_2026-07.md)
- [REPORT_ECONSTAT_Flips_2026-07.md](../econstat_research/REPORT_ECONSTAT_Flips_2026-07.md)
- [REPORT_Satellites_2026-07.md](../satellites_research/REPORT_Satellites_2026-07.md)
- Gate: [P022_DECISION_RULE.md](../golf_quirks_research/P022_DECISION_RULE.md) — **LOCKED**

---

## ⚠️ Live production, time-critical

**P-017's settler has been silently dead since the 2026-07-25 15:05 UTC restart, and the
3M Open finished today with 16 positions ($146.96) that cannot settle.**

`rotate_active_log.sh` truncates the active trade log (`: > "$LOG"`, cron `0 6 * * *`).
The running process is unaffected — its `TradeStore` is in memory — so nothing surfaces
until a restart. Across the restart the store went from `118 placed, 118 open` to
`22 placed, 5 open`. `get_open_trades("P-017")` returns empty, `settle_cycle()` bails at
its first guard, and **that guard logs at `debug`** — so the settler emitted *nothing*:
48/50/48/52 cycles Jul 22–25, then zero, while the pod kept scanning normally. The
stale-timeout backstop sits inside the same loop, so it cannot fire for an orphan either.

**Blast radius: 177 orphaned positions, $1,458.10** across P-001/002/006/017, invisible to
`TradeStore`, every settler and `AggregateRiskGuard`.

Recovery needs a live-log append plus a restart — both outside this queue's authority.
`scripts/rotate_active_log.py` is written and tested (18 tests). **`--pod P-017` is not
optional**: unscoped, it also resurrects ~$1,311 of P-002/P-006 exposure that has no
settler and would never drain.

---

## Consolidated deploy list

**Nothing was deployed. This is the complete list for Sam to action.**

### Correction to the brief: the two config items it lists as pending are already live

`config_multi_pod.yaml` on the droplet is **byte-identical to local HEAD**.
`max_event_exposure_pct: 0.08` (line 500) and the `basketball_ncaaw` removal (line 540)
are both deployed and loaded in the running process (config mtime 14:56 UTC precedes the
15:05 UTC restart). The cap needs a live golf event to confirm it binds, not a deploy.

### A. Live-correctness — deploy changes behaviour, and each is currently wrong in production

| Item | Why it matters |
|---|---|
| `src/kalshi_golf_settler.py` | Until deployed, P-017 keeps booking every `result="scalar"` as a $0.00 void |
| `scripts/rotate_active_log.py` **+ repoint the cron** off `rotate_active_log.sh` | Otherwise the orphaning recurs at every rotation-plus-restart, for every pod holding >~1.5 days |
| `Legacy/Kalshi Arb Project/src/matcher.py` | **Changes live P-001 trading behaviour.** Gating item for any P-001 verdict |
| `src/kalshi_fees.py` | Four families were billed a maker fee on maker-free series |

### B. Safe / additive

`scripts/clv_settlement.py`, `src/clv_close.py`, `src/mlb_teams.py`,
`scripts/backfill_golf_scalar_corrections.py`, `scripts/p022_checkpoint.py`,
`scripts/check_research_committed.sh`, `tests/*`, and all research artifacts.

### C. Already committed, still not deployed (predates this run)

`manager/` daily-brief work — `brief.py`, `checks.py`, `collect.py`, README,
`tests/test_manager_collect.py` (commits `e4768c8`, `f90cfb2`), plus `mlb_f5_research/`.

### D. Two deploy hazards found while auditing — fix `deploy.sh` *before* the next deploy

1. **`.claude/worktrees/` is not rsync-excluded.** 98 MB of stale detached-HEAD repo
   copies (three of them, all clean) have been shipped to `/opt/betting-pod-shop/.claude/`,
   where 20 MB currently sits containing **old versions of every source file**. A future
   droplet debugging session can grep stale code as if it were live.
2. **`manager/state/status.json` is gitignored but not rsync-excluded**, so a deploy
   **overwrites the droplet's live fund-manager state** with whatever is on the Mac.

Note rsync runs without `--delete`, so adding exclusions will not clean what is already
on the droplet — that is a separate manual step.

---

## Needs Sam's decision

1. **Recover the 16 orphaned P-017 positions** (above). Time-critical — the tournament has
   finished. If not done, the 3M Open is permanently censored at the cut line and
   tournament 1 of 8 is unusable.
2. **Approve (or not) the P-022 pod build.** Still the only validated unbuilt edge. The
   blocking settler bug is now **fixed and tested**, the forward gate is **pre-registered
   and locked**, and this run reproduced P-022's Phase-2 numbers from a from-scratch
   harness. Only the settler fix and gate registration were in scope; the pod was not built.
3. **Re-scope the P-001 gate.** Recommend **scenario D** — clean forward sample, n=200,
   post-fix only, landing late Aug–early Sept 2026. Add an admissibility rule
   (`|ticker start − priced game_time| ≤ 3 h`) that filters *which trades are eligible*
   without touching the metric. **Do not read the current 650 rows as gate progress.**
4. **Redefine the P-017 gate metric as *settled* tournaments.** `registry.yaml` carries a
   hand-maintained `progress: 1` set on entry day; there is no P-017 collector. As written
   the gate is satisfiable by *placing into* eight tournaments with most positions never
   resolving. True progress today is **0 of 8**.
5. **Re-book the JDAY scalar row** — `KXPGATOP20-3MO26-JDAY` settled `scalar` at $0.24/ct
   and was booked VOID. Correct net is −$0.10, not −$0.53.
6. **Prune the three stale worktrees** locally and on the droplet, and add the two
   exclusions to `deploy.sh`.
7. **Re-scope H2 in `kalshi-ev-map/02_edge_hypotheses.md`** to "fee-bounded *in the liquid
   sports head*" — P-020 shows it is false outside sports (see below).
8. **Fix the fee table properly.** It has drifted four times, three slices in this run
   alone. It wants a fixture generated from `/series` plus a CI check, not a fifth hand
   patch.

---

## Findings worth more than their verdicts

**The P-001 gate was about to pass on a number that measures nothing.** The fear was that
the 200-row gate was unreachable. The reality is worse: it is **already met at 650 rows
reading +1.39¢/ct**, almost exactly the +1.4pp target it was written against. The matcher
broke fuzzy-score ties by list order, and every game of an MLB series has identical team
names — so the pod priced one day's game and bought another's. Correctly-matched rows carry
**+7.65¢/ct**; mismatched rows carry **+0.19¢/ct**.

**And that bug is invisible where you would look for it.** `clv_log.jsonl`'s own `commence`
field agrees with the ticker in **650 of 650** rows, because settlement re-derives it from
the ticker's time. Auditing that log alone gives a clean bill of health. The mismatch only
appears when joined to the trade log's `game_time`.

**Booking `scalar` as a void deletes exactly the loss tail.** In the LEAD universe, 61
scalars with a $0.342 mean split payout were all recorded at $0.000. A fade sold at 5¢ and
hit by a two-way tie owes 50¢ and was booked as costing nothing — **forward P&L would have
been premium collected with the entire loss tail removed, strictly positive whether or not
the edge was real. The gate would have passed a money-losing strategy.**

**P-022 is now independently reproduced.** A from-scratch harness matched every published
Phase-2 cell, including two *incidental* fingerprints nothing in the report explains
(posted falling to 342 and 272 at offset 0.00, from cent-rounding). Nobody could fit those
by targeting the headline. Verify any time with
`python3 golf_quirks_research/backtest_fade_fills.py --validate`.

**Two new standing methodology rules, both earned the hard way:**

- **Check anchor contemporaneity in every maker replay.** On make-cut the "48h anchor" was
  a median 68h-old price (20.3 h stale, 100% of markets); posting it yielded +9.5¢/ct with
  a CI excluding zero — the only such cell in that study, and a pure artifact. P-023c then
  found the bias runs the *opposite* way on its cohort, so this is not a "stale looks
  better" correction — stale is simply wrong.
- **`settlement_ts − close_time` is a cheap first-pass screen** for any settlement-quirk
  hypothesis: one field, no external data, answers "did this market settle on the release
  it closed for?"

**Three tests would have confirmed their own hypotheses if run as specified.** P-026's
mid-sum fires in 4 of 10 events and is 100% accumulated half-spread in 4 of 4. P-020's
matcher had a **37% false-match rate** before its gates. The Satellites RT drift test has a
naive version that returns a tight CI and is a pure construction artifact. In each case the
guardrail — two-sided quotes only, measured false-match rate, executable prices not mids —
is what produced the kill.

## Anomalies and things worth a second look

- **~36,000 contracts of open interest sit long on award "tie" strikes at 1¢** (Game Awards
  21,276; Oscars Best Picture 14,899). Negative-EV on the measured base rates — someone
  else appears to be running the hypothesis Task 10 just killed.
- **Kalshi trade history reaches back to at least 2026-05-20, not ~1 month** as previously
  believed (and as I wrote in Task 1 before Task 7 corrected it). **P-022's 19 tournaments
  can be widened backwards for minutes of API budget instead of months of calendar** —
  the cheapest open lead in the folder.
- **Two documentation errors in `REPORT_Golf_Quirks_2026-07.md` §4.2**, found by exact
  replication: round-based top-N cells are anchored at H=12h, not 48h; and the
  full-tournament "48h, before R1" anchor is **post-R1 for 58–71%** of the control cohort.
- **The KXLEADER split clause is conditional** — it applies only where the league "does not
  declare a single winner through official tiebreaker procedures". Every prior write-up
  states it unconditionally.
- **`GOLDENGLOBESNOM.pdf` is not an award template** — it serves RANKLIST, whose ties pay
  pro-rata $1/n. Two opposite dead-heat regimes sit one PDF apart under similar tickers.
- **The "~Oct 15 first-ever KXLEADER settlement" is wrong** — `KXWCGOALLEADER` (54 markets)
  and `KXLEADERUCLGOALS` (6) already settled, both outright at $1.0000. The split has still
  never been observed.
- **No S&P Global flash PMI series exist on Kalshi at all** — half of P-027's hypothesised
  qualifying set was never listed.
- **Two Kalshi traps banked:** settled ECONSTAT markets carry `status="finalized"`, not
  `"settled"` (a `status=="settled"` filter returns zero rows across all 197 series — same
  silent shape as the golf `status="closed"` trap); and `settlement_ts` uses a variable
  number of fractional-second digits, which makes Python 3.9's `fromisoformat` raise and
  silently dropped 164→144 events on one pass.

## Rate limits and data

No 429s anywhere, despite up to six concurrent agents sharing the 2 req/s per-IP budget
with the live VPS. Odds API spend: **162 credits** of 4,867,826 (P-020 and the satellites
used zero). Every task cached its pulls; caches are committed gzipped where they cannot be
re-pulled — `leader_trades` (Kalshi trade history), `leader_research` orderbook snapshots
(live top-of-book state at one instant), and `crossvenue` (Polymarket retains only ~30
days, so the June/July window is already gone from the API). 271 MB → 11 MB on that last
one; `.git` grew 6.2 MB → 30 MB in total.

## Scoreboard after this run

- **Settlement / structural mechanics: 3 for 6.** P-015, P-017, P-022 still stand; P-026,
  P-027 and the three satellites all died — every one because the mechanic was real but
  smaller than the tick and the spread.
- **"We have better information": 0 for 7** (P-020 joins P-016, P-019, P-021, P-024,
  P-025, EV-Map Build 1).
- **Maker / fade: 0 for 4 once adverse selection is measured honestly** (P-016, make-cut,
  LIV top-N, P-023c).

The Tier-1 never-tested queue is now **empty**. P-020, P-023b, P-023c, P-026, P-027 and the
satellites have all been run to verdicts; none survived. **P-022 remains the only validated
unbuilt edge, and the forward pipeline behind it is now bare** — which is itself the most
important strategic fact this run produced.
