# P-022 §7 — the per-tournament cap now binds on QUOTED collateral

**Run:** 2026-07-28, Task 1 of `RUN_QUEUE_2026-07-28-day.md`. **Verdict: FIXED and DEPLOYED.**

> **P-022 is now inside its §7 per-tournament cap.** At AIGWO26 R1's real
> window-open instant it places **11 quotes carrying $49.47** against the $50
> (5% of $1,000) limit, where before the fix it placed **24 quotes carrying
> $111.65** and recorded no breach at all. Every parameter is byte-identical.
> The running `betting-round-leader-fade` process has been verified to carry
> the change, not merely to have received the file.

## ⚠️ First — the deadline in the queue is wrong, and in our favour

**ESPN published tee times between last night's pre-flight and this run.** Five
of the seven listed events have upgraded off the coarse `tour_day_offset` path,
and every window has moved **later**, exactly as the pre-flight predicted (it
estimated 3–5 h; measured 3 h 02 m and 3 h 52 m).

| event | close ref | source | **window OPENS** | was (07-29 pre-flight) |
|---|---|---|---|---|
| `KXLPGAR1LEAD-AIGWO26` | `2026-07-30T18:32Z` | **`tee_times`** | **`2026-07-29T18:32Z`** | 15:30Z |
| `KXPGAR1LEAD-ROC26` | `2026-07-30T22:22Z` | **`tee_times`** | **`2026-07-29T22:22Z`** | 18:30Z |
| `KXLPGAR2LEAD-AIGWO26` | `2026-07-31T18:32Z` | `tee_times` | `2026-07-30T18:32Z` | 15:30Z |
| `KXPGAR2LEAD-ROC26` | `2026-07-31T22:11Z` | `tee_times` | `2026-07-30T22:11Z` | 18:30Z |
| `KXLPGAR3LEAD-AIGWO26` | `2026-08-01T15:32Z` | `r1_tee_anchor` | `2026-07-31T15:32Z` | 15:30Z |
| `KXPGAR3LEAD-ROC26` | `2026-08-01T19:22Z` | `r1_tee_anchor` | `2026-07-31T19:22Z` | 18:30Z |
| **`KXCHAMPTOURR1LEAD-POI26`** | `2026-07-31T16:00Z` | **`tour_day_offset`** | `2026-07-30T16:00Z` | 16:00Z |

Live `close_source` census across all 958 books on the droplet:
`tee_times 586 · r1_tee_anchor 293 · tour_day_offset 79`.

**The 79 still on the coarse path are exactly POI26** — the one event whose lag
constant is uncalibrated (`LAG_DAY_H["KXCHAMPTOUR"]`, n = 1, a US event). The
upgrade that the pre-flight hoped would rescue POI26 has happened for
everything *except* POI26. That is Task 2 §D's question and it is now answered
by measurement rather than hope.

**Practical consequence:** the fix had until **18:32Z tomorrow**, not 15:30Z.
It is deployed now regardless.

---

## 1. Consumer enumeration — done BEFORE changing anything

The house pattern this task is the seventh instance of is *a filter asserted in
one place and applied in another*. So: every call path in the repository that
reads or writes P-022's collateral, reservation or cap state, enumerated first.

| # | consumer | reads | changed? |
|---|---|---|---|
| 1 | `_cycle_book` sizing (`room_name_coll` / `room_event` / `room_total`) | `book.collateral`, `tournament_collateral()`, `total_collateral()` | **YES** — now exposure, with self-exclusion |
| 2 | `check_caps()` → `_record_breach()` | `b.collateral` | **YES** — now `b.exposure` |
| 3 | `_reserve()` (guard reservation) | called with `book.collateral + size × per_ct_coll` | no — **already** quoted + filled. This was the inconsistency |
| 4 | `_pull()` release condition | `book.collateral <= 0` | no — `ask_quote` is already `None` there, so collateral == exposure |
| 5 | `_sweep_reservations()` | `book.ask_quote is None and book.collateral <= 0` | no — same identity |
| 6 | `_maybe_settle()` → `_release()` | marks fills settled, drops the book | no |
| 7 | `rebuild_from_log()` | rebuilds `fills` → `book.collateral`; then `check_caps()` | no — a restart has no resting quotes, so exposure == collateral |
| 8 | `_check_fills()` per-name room | `max_contracts_per_name - book.sold` (contracts) | no — contract bound, not collateral |
| 9 | **`scripts/p022_window_check.py::screen_after_band`** | duplicates the whole cap screen | **YES** — see §5, this one would have paged |
| 10 | `scripts/p022_checkpoint.py` (the sanctioned reader) | `CAP_BREACH` rows in the fills log | no — row schema unchanged |
| 11 | `MakerFill.collateral` / `MarketBook.sold` | per-fill arithmetic | no |
| 12 | `AggregateRiskGuard` (`reserve_trade` / `release_reservation`) | P-022's own instance | no |

**The enumeration paid for itself at #9.** The detector recomputes the pod's
post-band screens independently, holds no quotes of its own, and would
therefore have expected all 24 names to be quoted, seen 11, and paged
`WINDOW_OPEN_CANDIDATE_NO_QUOTE` at `critical` on the 13 the cap correctly
dropped — a false page, on the first tournament, within ~23 minutes of the
window opening. Fixing the pod without fixing the detector would have replaced
a silent accounting bug with a loud false alarm.

## 2. The defect, and its true size

`§7` sizes on *collateral at risk*. The code subtracted **filled** collateral
only, so every name in a tournament's window saw a book that looked empty.
Nothing had filled yet, so `check_caps()` — also reading filled collateral —
reported clean.

The pod meanwhile **reserved** worst-case *quoted* collateral with the risk
guard (line 729). It treated a resting quote as at-risk for the guard and as
free for its own caps. That is the whole bug, in one sentence.

**The pre-flight's figure was an understatement.** It measured 13 quotes /
$60.45. Re-measured today at the real window-open instant: **24 quotes /
$111.65 — 2.23× the cap**, because more names have quoted into the
`(0.03, 0.12)` band overnight. The direction of the drift is worth noting: this
number gets *worse* as the book fills in toward the round.

## 3. The fix

* `MakerQuote.collateral` — worst-case if the resting quote is fully lifted,
  `qty × (1 − price)`, derived from the quote, never a constant.
* `MarketBook.quoted_collateral` and `MarketBook.exposure` = filled +
  resting-quote worst case. `MarketBook.collateral` **keeps its old
  filled-only meaning**; `tournament_exposure()` / `total_exposure()` sit
  alongside `tournament_collateral()` / `total_collateral()`. The two questions
  — *what have I got on* and *what could I have on before I can cancel* — are
  genuinely different, and collapsing them into one accessor is how the caps
  came to be enforced against the wrong quantity in the first place.
* Sizing and `check_caps()` both bind on **exposure**.
* **Self-exclusion.** A book's own resting quote is excluded from the event and
  total room, because the quote being priced *replaces* it. Counting it would
  make each cycle size the quote against its own exposure and ratchet the book
  to zero over successive cycles — a mute that looks exactly like a cap
  working. Pinned by `test_a_quote_does_not_shrink_itself_on_the_next_cycle`.
* Exposure is **invariant across a fill** (a fill converts quoted collateral
  into filled collateral at the same price and quantity), so the cap cannot be
  breached by a quote merely being lifted. Pinned by
  `test_a_fill_does_not_change_exposure`.

## 4. The drop/shrink rule — a spec decision, written down

> **The per-tournament cap is allocated greedily in ascending Kalshi ticker
> order. A name is quoted at the largest whole-contract size the remaining room
> allows; once the room sizes a quote to zero contracts, the remaining names
> are not quoted.**

Ticker order is arbitrary with respect to edge — it is the golfer's name — and
that is the point. The alternatives, and why they were rejected:

| rule | rejected because |
|---|---|
| **dict / insertion order** (what it was) | insertion order is the order Kalshi's `/markets` response happened to arrive in. Not reproducible, and not a rule. |
| **best-edge first** | lets the cap select the sample on a quantity correlated with the outcome being measured. That is the fitting §8.2 exists to forbid. |
| **pro-rata across all in-band names** | keeps more names at smaller size (13 × 4 ct would fit), which is *better for the gate*. But it needs a price-everything-then-size pass the cycle does not have, and rebuilding the cycle is a larger change than a cap fix should be. **Recorded as the one thing worth reconsidering later** — it buys more observations per tournament at no extra risk. |

## 5. Before / after, at the real window-open instants

Driven through the pod's own `from_config() → discover() → cycle()` path with
`_now_fn` injected, logs redirected to a scratch directory, against **live**
Kalshi books. Reproducible: `scripts/p022_dry_run.py`, new in this commit,
because a funnel that has to be re-established by hand is not a baseline.

### AIGWO26 R1 — `2026-07-29T18:33:00Z`

| | before | after |
|---|---:|---:|
| markets discovered | 958 | 958 |
| inside `[12h, 24h]` | 146 | 146 |
| priced | 146 | 146 |
| in band `(0.03, 0.12)` | 24 | 24 |
| **quotes placed** | **24** | **11** |
| **worst-case collateral** | **$111.65** | **$49.47** ✓ ≤ $50.00 |
| per-quote size | 5 (all) | 5 × 10, **3 × 1** |
| §7 breaches recorded | 0 (wrongly) | 0 (correctly) |

**Shrunk:** `…-HANGRE`, 5 → 3 contracts — the boundary name.
**Dropped (13):** HYOKIM, JEETHI, JODEWA, JUSBOS, LAUCRU, LOTWOA, LYKO,
MANDER, MIYYAM, NELKOR, POLMAC, RUOYIN, VANBOU.

### ROC26 R1 — `2026-07-29T22:23:00Z` (both tournaments live)

| | before | after |
|---|---:|---:|
| inside window | 293 | 293 |
| in band | 25 | 25 |
| quotes placed | 25 | **12** |
| AIGWO26 collateral | $111.65 ✗ | **$49.47** ✓ |
| ROC26 collateral | $4.70 ✓ | **$4.70** ✓ |
| total | $116.35 | **$54.17** (aggregate cap $150 — not binding) |

**The fix does not starve the later tournament.** ROC26's single in-band name
is quoted at full size both before and after; the cap is per tournament and
AIGWO26 exhausting its own cap has no effect on ROC26's.

### POI26 R1 — `2026-07-30T16:01:00Z`

79 in window → **0 in band → 0 placed**, before and after. POI26's books are
not yet quoted, so nothing reaches the band. This is a price-staleness
artifact of the method (prices are live-as-of-now in every clock; only the
window arithmetic time-travels), **not** a finding, and it means the fix is
untested against POI26. The **aggregate** cap remains untested for the same
reason: nothing has come close to $150.

### The detector agrees exactly

Driven at the same injected instant, post-fix:

```
funnel  listed 958 → resolved 958 → in window 146 → priced 146
        → in band 24 → passes_every_screen 11 → quoted 0
refusals {'cap_sized_to_zero': 13}
state   WINDOW_OPEN_GRACE   alarm False
```

`passes_every_screen 11` matches the pod's 11 exactly, and the 13 refusals are
attributed to the cap rather than reported as missing quotes.

## 6. §8.1 — the required analysis, not assumed

The prompt states §8.1 as covering *offset, band, window*. **That is not what
the clause says, and the difference matters here.** Verbatim,
`P022_DECISION_RULE.md` §8.1:

> **No mid-flight parameter changes.** Offset, band, window, series set,
> **caps**. Any change resets T to 0 under a new pod ID.

So "caps" **is** in the enumerated list, and the honest question is whether
this counts as a change to one. Three observations, in order of weight:

1. **No cap value changed.** `pct_per_name 0.005`, `pct_per_tournament 0.05`,
   `pct_total 0.15`, `max_contracts_per_name 25`, bankroll $1,000 — read back
   from the merged droplet config after deployment, byte-identical (§8). What
   changed is the *quantity the existing cap is measured against*.
2. **§7 tells us which direction the clause is guarding.** Its own gloss is
   *"**Raising** any cap resets T to 0 and requires re-registration (P-022b). A
   cap **increase** is a new strategy, because the tail it bounds is the
   dominant risk."* This change is strictly **tightening** — it reduces quoted
   exposure from $111.65 to $49.47 and can never increase it. The hazard §8.1
   is written against is loosening, and this is its opposite.
3. **§7 also defines the quantity, and the old code did not match it.** *"Sizing
   is on **collateral at risk**, never contract count — a fade sold at 5¢ posts
   95¢ of collateral per contract."* A resting sell-YES quote is an
   unconditional obligation to take the position if lifted; on a real Kalshi
   account it locks the collateral. Filled-only was a mis-implementation of §7,
   not an alternative reading of it.

**Where a contrary reading exists, stated plainly:** this change alters *which
names get quoted* — 13 names that would have been quoted tomorrow will not be.
Someone could argue that the quoted population is itself a term of the strategy
and that changing it is a §8.1 event regardless of direction. I do not think
that reading survives point 2, but it is not absurd.

**It is moot in this instance, which is the decisive fact.** T is running from
`2026-07-26 22:36:52Z` and **no P-022 tournament has been observed** — the
quotes and fills logs do not exist on the droplet or locally, `discover()`
returned 0 at every prior window, and the first quote in this pod's history
would have been placed tomorrow. **T = 0 today.** So under *every* reading —
including the most conservative one, where §8.1 fires and T resets — the cost
is zero, because there is nothing to reset. Making the change *before* the
first observation is strictly better than making it after, whichever reading
is right.

**Recommendation: proceed, and record in §11 of the decision rule that §7's
caps are enforced against quoted-plus-filled exposure.** No pod-ID change.
If Sam prefers the conservative reading, the action is identical — re-register
as P-022 with T starting at the first quote tomorrow, which is what happens
anyway.

## 7. What did NOT change

* Band `(0.03, 0.12)` · offset `+0.02` · window `[12h, 24h]` · caps
  `0.5 / 5 / 15 %` ($5 / $50 / $150) · bankroll $1,000 ·
  `max_contracts_per_name 25` · 13 series — **read back from the live merged
  config on the running droplet** (§8), not from the source defaults.
* `config_multi_pod.yaml` and `src/golf_schedule.py` are **untouched** —
  absent from `git status`, so the diff is empty by construction.
* **No size or depth screen was added.** The pre-flight found the pod has none;
  per this task's stop rule that is Task 2's finding to report, not this task's
  to fix. (It is relevant: several quoted books have `ask_qty: 1`. Task 2.)

## 8. Deploy — and the proof the running process took it

```bash
bash scripts/deploy.sh 129.212.176.202          # sync only
ssh root@129.212.176.202 systemctl restart betting-round-leader-fade
```

`betting-pod-shop` was **not** restarted: P-022 is not in `pods.active`, runs
as its own unit, and an unnecessary restart of the 5-minute engine carries the
known log-rotation / orphaned-position hazard. `scripts/p022_window_check.py`
is cron-invoked and picks the change up on its next `8,23,38,53` run.

**A file on disk is not a deployed fix.** From the running process:

| check | evidence |
|---|---|
| file identity | local `sha256 c982a2d568c1…` = droplet `c982a2d568c1…` |
| ordering | file mtime `17:13:11Z` **<** process start `17:25:53Z` |
| PID / cwd | `MainPID 2337648`, `/proc/2337648/cwd → /opt/betting-pod-shop` |
| module loaded | `/opt/betting-pod-shop/src/round_leader_fade_maker.py` |
| fix present, by source inspection | `_cycle_book` contains `tournament_exposure` ✓ and `own_quote_coll` ✓; `cycle` contains `key=lambda b: b.ticker` ✓; `check_caps` contains `b.exposure` ✓ |
| new API | `MarketBook.exposure` ✓ `.quoted_collateral` ✓ `Engine.tournament_exposure` ✓ `.total_exposure` ✓ |
| params live | band `(0.03, 0.12)`, offset `0.02`, window `[12.0, 24.0]`, caps `5.0 / 50.0 / 150.0`, pct `0.005 / 0.05 / 0.15`, maxct `25.0`, 13 series |
| wiring live | `schedule = GolfScheduleResolver`, `risk_guard = AggregateRiskGuard` |
| markets live | `discovered 958 markets; 958 books` |
| reservations rebuild | `rebuild_from_log()` → **0 unsettled fills** (correct: nothing has ever filled) |
| exposure reads from the live book | `tournament_exposure("AIGWO26") = 0.0`, `total_exposure() = 0.0` |
| service log | `17:25:53 P-022 round-leader fade-maker starting (paper)` → `17:25:59 discovered 958 new round-leader markets` |

**Restart-recovery re-verified rather than trusted** (the 07-27 report recorded
it closed): `rebuild_from_log()` runs before the first `discover()` in
`run_round_leader_fade.py:51`, returns 0 here because no fill has ever
occurred, and `test_rebuild_from_log_restores_unsettled_fills` /
`test_recorded_breaches_survive_a_restart` cover the populated case. A restart
has no resting quotes by construction, so exposure == collateral at recovery
and no reservation can be stranded.

## 9. Tests

`tests/test_round_leader_fade.py`: **38 → 43.** Full suite **1,742 passed, 2
skipped** (from 1,723).

New:

* `test_quoted_collateral_counts_against_the_tournament_cap` — the regression.
  40 in-band names, nothing filled; asserts `tournament_collateral() == 0` (the
  old accounting's blind spot) while `tournament_exposure() ≤ $50`.
* `test_a_quote_does_not_shrink_itself_on_the_next_cycle` — the ratchet.
* `test_allocation_order_is_ticker_not_dict_order` — same result from a
  reversed listing order, and the survivors are a ticker-order prefix.
* `test_a_fill_does_not_change_exposure` — exposure invariance.
* `test_cap_breach_is_recorded_against_quoted_collateral` — `check_caps` sees
  an over-cap resting quote.

**One existing test changed, and it asserted the defect.**
`test_reservations_drain_so_the_pod_can_still_quote_later` required
`reserved_1 > 125.0` — i.e. it required one tournament to hold ~$150, which is
the §7 breach. Its *purpose* (reservations must drain or the pod starves) is
still valid, so rather than deleting the assertion I lowered the **guard's**
bankroll to $300 (per-pod limit $75) so that one tournament fits and two do
not. Left at the pod's $1,000 the test would have passed for the wrong reason:
$49.47 against a $250 per-pod limit leaves so much headroom that round 2 fits
whether or not round 1 drained. The assertion is now
`37.5 < reserved_1 ≤ 50.0` — §7's cap, honoured on quoted collateral.

## 10. Findings for Sam

1. **The window-open instants have moved ~3–4 h later** and five of seven
   events are now on the precise `tee_times` path. AIGWO26 R1 opens
   **18:32Z**, ROC26 R1 **22:22Z**.
2. **POI26 did not upgrade.** It is the only event still on
   `tour_day_offset`, and it is the one whose lag constant is calibrated on
   n = 1 from a different continent. The mitigation the pre-flight was
   counting on has now demonstrably not fired for the event that needed it.
   → Task 2 §D.
3. **The breach was 2.23×, not 1.2×**, and it grows as books fill in toward
   the round. Had this waited a day it would have been larger again.
4. **Pro-rata allocation is worth reconsidering** as a deliberate spec change:
   13 names at 4 contracts fits the same $50 and yields more observations per
   tournament at identical risk. Not done here — out of scope for a cap fix.
5. **The aggregate ($150) cap is still untested.** Nothing has approached it.
6. **Several quoted books have `ask_qty: 1`** against a 5-contract quote.
   Recorded here, analysed in Task 2.
