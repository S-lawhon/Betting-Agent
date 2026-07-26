# P-017 Settlement Verification — silent-failure check

**Date:** 2026-07-26 · **Prompt:** `research/prompts/PROMPT_P017_Settlement_Verification.md` (Task 2)
**Scope:** read-only on the droplet; no deploy, no live `data/` mutation, no config promotion.

## Verdict — WORLD (b), with a twist

**The settler is wired, was reached, and worked correctly for four days — then went
silently dead on 2026-07-25 at 15:05 UTC and has done nothing since.**

Not world (a): the markets are not merely waiting on Kalshi. Not world (c): deployed code
is byte-identical to local HEAD. It is world (b) — silently not reached — but the fault is
*not* in `kalshi_golf_settler.py`. It is one layer down, at the seam between daily log
rotation and `TradeStore`. Every unit in the chain is individually correct.

### Mechanism

1. P-017 placed 38 rows for the 3M Open on **2026-07-21 03:22–03:31 UTC**.
2. Droplet cron `0 6 * * * scripts/rotate_active_log.sh` fired **2026-07-22 06:00**. It
   gzips `data/trade_logs/trade_log.jsonl` then **truncates it**:
   `gzip -c "$LOG" > "${LOG%.jsonl}.archive_${TS}.jsonl.gz"` then `: > "$LOG"`.
   All 38 PLACED rows now exist only inside `trade_log.archive_20260722_060001.jsonl.gz`.
3. The running process was unaffected — its `TradeStore` was already in memory. 48 golf
   settler cycles/day on Jul 22–24, 52 on Jul 25 to 14:42 UTC. It correctly booked
   **21 LOSS + 1 VOID**.
4. Service **restarted 2026-07-25 15:05:16 UTC**. `TradeStore.load()` reads *only* the
   active log. Journal, verbatim:
   `15:05:18 src.trade_store: trade_store: loaded 39893 entries (22 placed, 39 settlements, 5 open, 0 errors skipped)`
   **22 placed** — those are the 22 settlement rows the settler itself wrote *after*
   rotation. The 38 placements are gone. (Compare the pre-restart load on Jul 21:
   `118 placed, 118 open`.)
5. `store.get_open_trades("P-017")` → `[]`. `settle_cycle()` returns at its first guard,
   which logs via `logger.debug` — **nothing is emitted at all**. No error, no warning, no
   cycle line.

Golf-settler `cycle done` lines per day: Jul 22 → 48 · Jul 23 → 50 · Jul 24 → 48 ·
Jul 25 → 52 (last 14:42) · **Jul 26 → 0**. Last line read `checked=16 settled=0`.
The pod itself is still alive and scanning (`P-017: scanned 132 top-N markets, 0 placed`
at 02:00 UTC today) — which is exactly why the failure is invisible from the outside.

**The stale-timeout backstop is dead too.** `_stale_record()` is called *inside* the loop
over `open_entries`; with an empty dict the body never executes, so the 10-day guard can
never fire for an orphaned position — it fails in exactly the case it exists to cover.

### Wider blast radius

The same 3-archive window holds **177 orphaned open positions, $1,458.10** invisible to
`TradeStore`, `AggregateRiskGuard` and every settler: P-002 78 · P-006 44 · P-001 39 ·
**P-017 16 ($146.96)**. Any pod holding a position longer than the ~1.5-day rotation
interval is one restart away from a silently orphaned book.

The `_open_placed_entries` docstring asserts the opposite of the truth — "The store is also
the only source that survives log rotation." It does not; it is *loaded from* the file that
rotation truncates.

## The required answers

**1. World (b).** Registration is healthy — journal 15:05:18:
`build_shared_deps: KalshiGolfSettler loaded`, `settlement.interval_cycles: 6`.
`kalshi_golf_settler.py`, `engine.py`, `golf_topn_pod.py`, `config_multi_pod.yaml` are all
md5-identical droplet↔local. **No wiring or registration code was changed; none was broken.**

**2. True settled-tournament count: 0 of 8 — not 1.** The 3M Open is still in progress and
only 58% settled: 38 PLACED / 21 LOSS (−$171.10) / 1 VOID (−$0.53) / **16 still open
($146.96)**. Verified against Kalshi (38/38 markets fetched, throttled ~1.2 s, cached): the
21 LOSS are `finalized`, `result=no`, `settlement_value_dollars=0.0000`,
`close_time≈2026-07-25T00:35Z` — **missed-cut** players closed early at the Friday cut, all
caught by the settler within ~7 minutes. The 16 open are `status=active`, `result=""`,
`close_time=2026-08-23` (the far-future default) — the players who **made the cut**,
resolving today/tomorrow.

The decisive half of the tournament is unsettled and under current state will never be
booked. **The −$171.63 on the board is the all-miss half with the survivors censored out** —
it is not a result, it is a truncated sample, and it is what tripped the 2026-07-25
daily-loss halt.

**3. Stale-timeout voids: none, and none are possible.** Zero `stale_timeout` rows; the
guard has never fired and now cannot for orphans. Nothing spurious entered the gate — by
luck, not design.

**4. Gate counter counts ENTERED tournaments. This is a gate-integrity bug.**
`manager/registry.yaml` carries `progress: 1  # 3M Open (2026-07-21), in flight` —
hand-maintained. No P-017 collector exists in `manager/collect.py` (only P-016 has one);
`manager/checks.py:577` reads `gate.progress` straight from YAML. It was set to 1 on entry
day and the comment says "in flight". As written, P-017 reaches 8/8 once it has *placed
into* eight tournaments, potentially with most positions never resolved — **a gate
satisfiable without a single observation of the thing it measures.** Under a
settled-tournament definition it is **0**. `registry.yaml` was not edited; changing a
pre-registered gate is Sam's call.

**5. Event cap IS live.** Config md5 `93cfb2fd…` matches HEAD, `max_event_exposure_pct: 0.08`
line 500, `basketball_ncaaw` removed line 540. File mtime **14:56:14 UTC** predates the
**15:05:16 UTC** restart, so the *running process* has it loaded, not merely the disk.
`golf_topn_pod.py` md5 `b6019b73…` matches and consumes it (`from_config` L675, budget
L267). Not yet *exercised* — no golf event entered since the restart. (`287cf89` is
timestamped 16:59 UTC, after the restart; the deploy went out before the commit.)

**6. Scalar contamination: YES, 1 row — confirmed live.**
`KXPGATOP20-3MO26-JDAY` — Kalshi reports `status=finalized  result=scalar
settlement_value_dollars=0.2400  close_time=2026-07-23T19:55:01Z`. The settler booked it
`VOID / kalshi_withdrawn`, gross $0.00. Realised payout was $0.24/contract. Position 43 ct
@ 0.230, fee $0.0124/ct:

| | gross | fees | net |
|---|---|---|---|
| Booked (void) | $0.00 | $0.5332 | **−$0.53** |
| Correct (43×(0.24−0.23)) | +$0.43 | $0.5332 | **−$0.10** |

**Live confirmation of the P-022 thesis.** The dollar error is small ($0.43; total moves
−$171.63 → −$171.20) only because the fill and the settlement value happened to sit one cent
apart — on a 5¢ fill settling at $0.50 the sign flips. The **code path is fixed by Task 3**
(`scalar_settlement_value()` / `ScalarSettlementValueMissing`); that file was not touched
here. What remains is a **data correction**: the written JDAY row still carries
`outcome=VOID, pnl_gross_usd=0.0` and will not be retroactively re-booked. It must count as
a resolved trade, not a void.

## Fix delivered (not deployed)

**`scripts/rotate_active_log.py`** (new) — drop-in replacement for the droplet's
`rotate_active_log.sh`. Identical behaviour (50 MB cap, gzip archive, keep 12) plus: the
rewritten active log retains the **still-open PLACED rows, verbatim**. Verbatim matters —
`TradeStore._index_entry` pairs settlements to placements by `fingerprint` and settlement
records are `{**placed_entry, "action": outcome}`, so a carried row still closes correctly
and re-appending is idempotent. Also provides
`--recover-open-from-archives N --pod P-017` to repair an already-orphaned book.

**`--pod` is not optional in practice.** Validated read-only against the real droplet
archives: the logic finds exactly the 16 P-017 orphans ($146.96), matching an independent
count — but an *unscoped* run would also resurrect 161 rows / $1,311 from P-002 and P-006,
which have no settler. That exposure would enter the risk guard and never drain, precisely
what `settled_pod_ids` exists to prevent.

**`tests/test_rotate_active_log.py`** (new) — 18 tests, all passing, including
`test_truncating_rotation_orphans_positions_carrying_rotation_does_not` (reproduces the
production failure and its fix side by side) and
`test_settler_sees_carried_positions_after_rotation` (real `TradeStore` + real
`KalshiGolfSettler` over a rotated log — the bug lived at that seam, so the test crosses
it). Existing `test_trade_store` / `test_settler_construction` / `test_settlement_cadence` /
`test_main` still pass (87).

## Needs Sam's decision

1. **The 16 live P-017 positions are unsettleable right now and the tournament finishes
   today.** Recovery needs a re-append to the droplet's active log plus a restart — both
   live-data actions outside this task's authority. If not done, the 3M Open is permanently
   censored at the cut line and tournament 1 of 8 is unusable.
2. **Replace the rotation script and repoint the cron** to `rotate_active_log.py`. Until
   then this recurs at every rotation-plus-restart for every pod holding >~1.5 days.
3. **Re-book the JDAY scalar row** at $0.24/ct (net −$0.10), not a void.
4. **Redefine the P-017 gate metric** as settled tournaments, derived from the trade log.
   Progress today is 0 of 8.
5. **Reword the registry `watch` item.** It predicted "PLACED climbing and settled stays 0".
   Reality: settled went to 22 and *then* the mechanism died. The next check should look for
   *open positions with no settler cycles*, not for zero settlements.

### Evidence appendix

Droplet `129.212.176.202`, `betting-pod-shop` active since 2026-07-25 15:05:16 UTC (PID
2266842; prior 2206876). `data/trade_logs/trade_log.jsonl` 47,223 lines +
`trade_log.archive_20260722_060001.jsonl.gz`. P-017 in window: 38 PLACED, 37,449
SKIPPED_RISK, 21 LOSS, 1 VOID. `trade_store.max_load_entries: 50000`; the load was **not**
truncated (39,893 of 39,893 scanned) — the tail cap is not implicated, rotation is. Kalshi:
38 markets via `GET /trade-api/v2/markets/{ticker}`, ~1.2 s apart, cached.

Independently re-verified by the orchestrating session: golf-settler journal lines per day
(48/50/48/52, zero on Jul 26), the `39893 entries (22 placed, … 5 open)` load line, the
verbatim `: > "$LOG"` truncation at `scripts/rotate_active_log.sh:11`, and the
`0 6 * * *` cron entry.
