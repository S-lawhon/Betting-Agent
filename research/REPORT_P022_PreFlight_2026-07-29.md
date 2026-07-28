# P-022 pre-flight and silent-failure alarm — 2026-07-29

**Run:** 2026-07-28 02:19 → 03:05 UTC (Task 1 of `RUN_QUEUE_2026-07-29.md`).
**Verdict: ARMED.**

> **Is P-022 armed to quote at 15:30Z, and what will tell us within 30 minutes
> if it is not?**
>
> **Yes — driven at an injected wall-clock of 2026-07-29T15:31:00Z, the pod's
> own `discover() → cycle()` path places 13 quotes** on `KXLPGAR1LEAD-AIGWO26`
> (sell YES at 7¢ against a 5¢ reference, 5 contracts each, $60.45 of
> worst-case collateral). This is the first time in this workstream that the
> path from *window open* to *order would be placed* has been demonstrated
> rather than inferred from a state check.
>
> **If it is not, the `WINDOW_OPEN_CANDIDATE_NO_QUOTE` alarm pages within
> ~23 minutes of the window opening** — detector at `8,23,38,53`, collector and
> alerter at `*/15`, `critical` severity so `manager/alert.py` sends
> immediately with no confirmation window. That alarm was rehearsed against
> live market data at the real window-open instant and fired, naming the same
> 13 tickers.

**One thing the queue's framing gets wrong, and it matters for the watch:**
15:30Z is only the window-open instant *while the coarse `tour_day_offset`
path is in force*. All seven listed events resolve through it right now
because ESPN publishes no tee times three days out. The runner re-resolves
every 900 s, and when pairings publish the close moves **later** by an
estimated 3–5 h on all three tournaments — so the window opens later too.
**"Did P-022 quote at 15:30Z?" is the wrong question; "did it quote at
`resolved_close − 24h`, whatever that was at the time?" is the right one, and
the alarm asks that one.**

---

## Part A — resolver ground truth

Seven events are listed (957 markets), up from three (351) at the last
deploy: R1/R2/R3 for two tournaments plus POI26 R1.

| event | markets | competition | resolver says | source | window OPENS | window CLOSES |
|---|---:|---|---|---|---|---|
| `KXLPGAR1LEAD-AIGWO26` | 144 | AIG Women's Open | `2026-07-30T15:30Z` | `tour_day_offset` | **`2026-07-29T15:30Z`** | `2026-07-30T03:30Z` |
| `KXPGAR1LEAD-ROC26` | 149 | Rocket Classic | `2026-07-30T18:30Z` | `tour_day_offset` | **`2026-07-29T18:30Z`** | `2026-07-30T06:30Z` |
| `KXLPGAR2LEAD-AIGWO26` | 144 | AIG Women's Open | `2026-07-31T15:30Z` | `tour_day_offset` | `2026-07-30T15:30Z` | `2026-07-31T03:30Z` |
| `KXCHAMPTOURR1LEAD-POI26` | 78 | Portugal Invitational | `2026-07-31T16:00Z` | `tour_day_offset` | `2026-07-30T16:00Z` | `2026-07-31T04:00Z` |
| `KXPGAR2LEAD-ROC26` | 149 | Rocket Classic | `2026-07-31T18:30Z` | `tour_day_offset` | `2026-07-30T18:30Z` | `2026-07-31T06:30Z` |
| `KXLPGAR3LEAD-AIGWO26` | 144 | AIG Women's Open | `2026-08-01T15:30Z` | `tour_day_offset` | `2026-07-31T15:30Z` | `2026-08-01T03:30Z` |
| `KXPGAR3LEAD-ROC26` | 149 | Rocket Classic | `2026-08-01T18:30Z` | `tour_day_offset` | `2026-07-31T18:30Z` | `2026-08-01T06:30Z` |

`product_metadata` is populated and unambiguous on all seven —
`competition` = "AIG Women's Open" / "Rocket Classic" / "Portugal
Invitational", `competition_scope` = "Round N Leader" or "End of Round N
Leader". Round extraction agrees with the ticker on all seven. Kalshi's own
`close_time` and `expiration_time` are `2026-08-16T00:00:00Z` on **every one
of the 957 markets** — the placeholder collapse, unchanged.

**All seven use the coarse path.** ESPN's leaderboard endpoint returns zero
competitors for all three tournaments, so `tee_times` and `r1_tee_anchor`
cannot run. This is exactly what `golf_schedule.py`'s docstring predicts and
is why the day-offset lag is load-bearing rather than decorative.

### Signed error against independently published schedules

ESPN gave the venues, which is what makes the check possible:

| tournament | venue | local zone |
|---|---|---|
| AIG Women's Open | Royal Lytham & St Annes, Lancashire | BST (UTC+1) |
| Rocket Classic | Detroit Golf Club, Michigan | EDT (UTC−4) |
| Portugal Invitational | The Els Club Vilamoura, Algarve | WEST (UTC+1) |

**`AIGWO26` R1 — resolver EARLY by ≥3.5 h. SAFE.**
Predicted `2026-07-30T15:30Z`. Sky Sports' published R1 schedule has Featured
Groups from 09:00 BST, main coverage from 13:00 BST, and one-hour highlights
at 23:00 BST — on a 144-player major field at a links course. Play therefore
continues well past 19:00 BST (18:00Z) and realistically ends ~20:00–21:00
BST (19:00–20:00Z). The prediction sits close to the *last tee time*, not the
round end.

**`ROC26` R1 — resolver EARLY by ~4 h. SAFE.**
Predicted `2026-07-30T18:30Z`. Racing Post: *"The first round at Detroit Golf
Club begins at 11.45am on Thursday, July 30"* — a UK publication, so 11:45
BST = **06:45 EDT = 10:45Z first tee**, the standard Detroit two-tee start.
A ~156-player two-tee field cannot complete in the 7 h 45 min between that
first tee and the predicted close; last group off ~13:40 EDT finishes ~18:40
EDT = 22:40Z.

**`POI26` R1 — NOT VERIFIABLE tonight, and the sign is not guaranteed. FLAG.**
Predicted `2026-07-31T16:00Z`. No tee sheet exists anywhere: the tournament's
own site, PGA Tour Champions and ESPN all return nothing, and no broadcast
times have been published. The single published timing fact is the
tournament's own schedule page — *gates open 09:00 and close 18:00 daily*,
local — which bounds the round end at **≤ 17:00Z** but supplies **no lower
bound**. A 78-player, no-cut, two-tee field finishing at ~16:00 local would
be 15:00Z, which would put the resolver **1 h LATE**.

Two things make this the weakest of the three, and they compound:

* `LAG_DAY_H["KXCHAMPTOUR"] = 12.0` is calibrated on **n = 1** — the file says
  so — and that single observation is a **US** event (min residual 20.01 h).
  This is the first PGA Tour Champions event ever staged in Europe. Portugal
  is 5 h ahead of ET, so the one calibration point is not merely thin, it is
  in a different clock frame. The same reasoning the file already applies to
  UK venues applies here and has never been exercised.
* Champions events are 54 holes with a small field, so the round is *shorter*
  than the 12.5 h median span the lags were fitted against.

**Reported, not fixed** — per the prompt's stop rule, and because
`LAG_DAY_H` is the schedule resolver, not observation surface.

**The mitigation is already in the design and is now observable.** POI26's
window opens `2026-07-30T16:00Z`, roughly 24 h before its round. ESPN
normally carries tee times a day or two out, which would upgrade the event to
the `tee_times` path (`LAG_TEE_H = 4.0` against a min observed residual of
4.16 h over 69 events — the well-calibrated path). The detector records
`close_source` per event every 15 minutes, so **whether the upgrade happens
before the window opens is now a fact in the log rather than a hope.**

---

## Part B — dry-run of the placement decision

The pod's real `from_config → discover() → cycle()` path, with `_now_fn`
injected and logs redirected to a scratch directory. No order submission of
any kind — this client cannot place orders. Books were pruned to the
in-window set before `cycle()` purely to bound orderbook calls; pruned books
hold zero collateral, so no cap or decision changes.

### Clock A — `2026-07-29T15:31:00Z`, the first window-open instant

| stage | count |
|---|---:|
| markets discovered | 957 |
| close resolved | 957 |
| inside `[12h, 24h]` | **144** (`KXLPGAR1LEAD-AIGWO26`, h = 23.98) |
| priced | 144 |
| inside band `(0.03, 0.12)` | **13** |
| passes caps / quote-price sanity | 13 |
| **WOULD PLACE** | **13** |

13 quotes, sell YES at **$0.07** against a $0.05 reference, **5 contracts
each** (per-name collateral cap 0.5% × $1,000 = $5.00; $5.00 / $0.93 = 5.37 →
5). Total 65 contracts, **$60.45** worst-case collateral. No pulls, no cap
breaches.

### Clock B — `2026-07-29T18:31:00Z`, ROC26 R1's window opens

293 in window across two events → 18 in band → **18 would place** (13 AIGWO26
at 7¢, 5 ROC26 at 6–11¢). $83.20 collateral.

### Clock C — `2026-07-30T18:31:00Z`, three events in window

371 in window (`ROC26` R2, `AIGWO26` R2, `POI26` R1) → **0 in band → 0 would
place.** This is a **price-staleness artifact, not a finding**: R2/R3 and POI
books are not yet quoted, so 369 of the 371 read as junk (see below) and 2 as
sub-3¢. **The aggregate-cap path is therefore untested.** Prices in every
clock above are as of 2026-07-28 02:30Z; only the window arithmetic time
travels.

### Three observations from the funnel that are not in any prior report

**1. Every in-band reference is ONE-SIDED.** All 13 AIGWO26 candidates have
`yes_bid = null`, `yes_ask = 0.05`, `ask_qty = 10`. `_mid()`'s one-sided-ask
branch — *"Accept a one-sided ask as the reference when there is no bid"* —
is what drives **every placement in the first tournament**. The pod is not
quoting off a two-sided mid at all. Phase 2's replay measured tick prints;
the live population at window-open does not resemble that. Worth Sam's
attention before the first fills are interpreted.

**2. The 131 "above band" names are junk books, correctly excluded.** They
read either `bid 0.01 / ask 0.97` (mid 0.49) or a one-sided ask of 0.81–0.83.
A 144-player field cannot have 131 names above 12¢; these are unquoted
listings. The band screen is doing real work.

**3. The pod has no size or depth screen.** The prompt's funnel includes a
"passes size/depth screen" stage; **that stage does not exist in the pod.**
`ask_qty` is 10 on the AIGWO26 candidates and 250 on `ROC26-JQUI`, and
nothing looks at either. Against the queue's own standing guardrail —
*"screen on top-of-book size, not spread"* — this is an omission, not a
setting. Adding one is an §8.1 change.

---

## Part C — the alarm (the deliverable)

`scripts/p022_window_check.py` now distinguishes the states the system could
not distinguish, and the result reaches Sam instead of a log file.

| state | meaning | alarm |
|---|---|---|
| `NO_MARKETS` | nothing listed in any of the 13 series | no |
| `NO_WINDOW` | listed, none within `[12h, 24h]` | no |
| `WINDOW_OPEN_GRACE` | candidates exist, window open < grace | no |
| `WINDOW_OPEN_NO_CANDIDATE` | window open, nothing clears band/caps — funnel logged | no |
| `WINDOW_OPEN_CANDIDATE_NO_QUOTE` | window open, a name clears **every** screen, no quote written | **YES** |
| `QUOTED` | ≥1 quote written since that event's window opened | no |
| `CLOSE_REF_PLACEHOLDER` | close reference is a fallback | **YES** (unchanged) |
| `SCHEDULE_UNRESOLVED` | pod failing closed | **YES** (unchanged) |
| `CHECK_FAILED` | the checker could not measure | **YES** (new) |

Old names are carried in `legacy_state` so the existing `status.jsonl` history
stays comparable. `WAITING` split into three of the states above — that
collapse is the reason the detector could not have caught a
window-open-but-nothing-quoted condition.

**Four design decisions worth stating, because each removes a way the alarm
could have been wrong:**

* **"Has it quoted?" is per ticker, against that event's window-open instant
  — not a fixed lookback.** P-022 writes a `QUOTE` row when it places or
  re-prices, then rests the quote through the round writing nothing. An
  hour-lookback would have flipped to ALARM within the hour on a perfectly
  healthy placement. A quote from *before* this window does not clear it
  either, or last tournament's log would silence this one.
* **A 10-minute grace period.** For a market already in the pod's book — the
  normal case, since these list 2.7–4.9 days ahead — the pod needs one 20 s
  cycle. The grace exists only for a market listed after the last 900 s
  rediscover pass.
* **The detector duplicates the pod's post-band screens rather than importing
  them.** A detector that agrees with the pod by construction agrees when the
  pod is wrong — which is how three dead days passed. The duplication is
  pinned by `test_screen_agrees_with_the_engines_own_decision`, which drives
  a **real** `RoundLeaderFadeMakerEngine` over six cap and price-sanity
  states and asserts both answers match. It also calls the pod's own
  `rebuild_from_log()` so the cap screen reads live exposure rather than an
  empty book.
* **A checker failure is a FAILURE, never a skip.** A total listing outage
  previously printed a stderr warning and then reported `NO_MARKETS` —
  "healthy, between tournaments". It is now `CHECK_FAILED` and alarms, and a
  raised exception in `main()` writes a `CHECK_FAILED` row rather than dying
  with no row at all.

**Delivery.** `manager/collect.py` gains a `p022_window` probe (last status
row, its age, the funnel, the named candidates) and `manager/checks.py` gains
`check_p022_window`, wired into `run_checks`. `WINDOW_OPEN_CANDIDATE_NO_QUOTE`
/ `CLOSE_REF_PLACEHOLDER` / `SCHEDULE_UNRESOLVED` are `critical` — `alert.py`
pages criticals immediately, with no confirmation window. `CHECK_FAILED` and
a stale or missing status row are `warn`. A missing file is reported as
unmeasured, never defaulted to healthy.

**Detection latency, worked rather than asserted.** Detector at
`8,23,38,53` (moved from `*/30`, and deliberately 7 minutes ahead of each
collector); collector and alerter at `*/15`; alarm requires window open ≥ 10
min. Worst case — window opens at `:08:01` — the first qualifying run is
`:23`, it completes in ~44 s (measured, 144 book pulls), the collector runs
`:30`, the alerter `:30:45`: **22.7 minutes.** Best case ~18 minutes.

**Rehearsed against live data, not only unit tests.** With the clock injected
to the real window-open instant the detector reports `WINDOW_OPEN_GRACE` at
+1 min and **`WINDOW_OPEN_CANDIDATE_NO_QUOTE`, `alarm=True`, at +31 min**,
listing the same 13 tickers the pod's dry run would quote. The funnel it
reports (957 → 957 → 144 → 144 → 13 → 13) is identical to the pod's.

**Tests:** 25 (14 detector + 11 manager), covering all eight states plus the
one that matters most — window open, candidate present, no quote. Full suite
**1,665 passed, 1 skipped** locally; the two new files pass on the droplet.

---

## Findings for Sam — reported, not acted on

1. **`POI26` R1's close reference has no verified margin** and its per-tour
   lag rests on a single US observation for the first European Champions
   event ever played. Its window opens `2026-07-30T16:00Z`. Watch
   `close_source` for that event in `status.jsonl`; if it is still
   `tour_day_offset` when the window opens, the quote is being timed off an
   uncalibrated constant.
2. **§7's per-tournament cap is not enforced against QUOTED exposure.** At
   clock A the 13 quotes carry $60.45 of worst-case collateral against a 5%
   ($50) per-tournament limit, because sizing subtracts only *filled*
   collateral. If ≥11 of the 13 fill, the cap breaches and **AIGWO26 is
   excluded from T under §7** — a gate condition lost to an accounting
   inconsistency. The pod already reserves worst-case collateral with the
   risk guard, so it treats a resting quote as at-risk for the guard and as
   free for its own caps. Fixing it is a sizing change: Sam's call.
3. **A cap refusal on a book with no resting quote writes nothing.** `_pull`
   returns early when `ask_quote is None`, so the *first* cap refusal on a
   name is invisible in the logs. The detector now computes the screens
   itself and reports `screen_refusals`, but the pod-side blind spot remains.
4. **No size or depth screen exists** (Part B, observation 3).
5. **Every first-tournament placement runs through the one-sided-ask branch
   of `_mid`** (Part B, observation 1).
6. **The window-open instant moves.** Expect it ~3–5 h later than the times
   in Part A once ESPN publishes pairings. Do not read a quiet 15:30Z as a
   failure without checking `close_source` first.

## What did not change

`band (0.03, 0.12)` · `offset +0.02` · `window [12h, 24h]` · caps
`0.5 / 5 / 15 %` ($5 / $50 / $150) · bankroll $1,000 · `max_contracts_per_name
25` · 13 series — **verified byte-identical before and after, and
`git diff` against `src/round_leader_fade_maker.py`, `src/golf_schedule.py`,
`config.yaml` and `config_multi_pod.yaml` is empty.** `t_start_utc` untouched
(Task 2).

## Deploy

Synced **without restarting anything** — the entire diff surface is
cron-invoked observation code, and an unnecessary restart of
`betting-pod-shop` carries the known log-rotation/orphaned-position hazard.

```bash
bash scripts/deploy.sh 129.212.176.202          # sync only, NO restart
```

* `betting-pod-shop` — **not restarted** (code unchanged)
* `betting-round-leader-fade` — **not restarted** (code unchanged; still up
  since 2026-07-27 17:53:24 UTC)
* crontab: `*/30` → `8,23,38,53` for `scripts.p022_window_check`; previous
  crontab backed up to `/root/crontab.bak.2026-07-29`
* verified on the droplet: detector `NO_WINDOW` exit 0 → collector
  `p022_window` populated, `faults: []` → `alert.py --dry-run` renders the
  finding at `info`
