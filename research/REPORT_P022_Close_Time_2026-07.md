# P-022 — Resolving the Real Round Close Time

**Task:** `research/prompts/PROMPT_P022_Close_Time_Resolution.md`
**Run:** 2026-07-27/28 · **Verdict: FIXED — pending deploy and a live quote**
**No P-022 parameter changed.** Band `(0.03, 0.12)`, offset `+0.02`, window
`[12h, 24h]`, caps `0.5% / 5% / 15%`, 13 series — all verified byte-identical
after the change.

---

## 0. Headline

> **Did P-022 quote? Not yet — and for the first time that is a calendar fact
> rather than a defect.** The pod now resolves a real round close for all
> three listed tournaments. The earliest placement window opens
> **2026-07-29T15:30Z** (AIG Women's Open R1), ~47h after this run. Nothing
> is quotable before then, and the detector says so in words rather than by
> being silent.

The blocker is closed. `src/golf_schedule.py` resolves
`(competition, round) → round-end UTC` from ESPN's public golf API for all
five tours, validated against **72 settled round-leader events** where Kalshi
*has* rewritten `close_time` to the truth. The resolver's error is
**one-sided early on 72 of 72** — minimum `+0.16h`, median `+1.58h` — which is
the only direction that does not put quotes into a live round.

| | before | after |
|---|---|---|
| close reference on a listed event | `2026-08-16T00:00:00Z` (identical on all 3 events, all 5 fields) | ROC26 `07-30T18:30Z` · AIGWO26 `07-30T15:30Z` · POI26 `07-31T16:00Z` |
| listing→close span | 19.3 / 19.3 / 20.0 d | 3.1 / 3.0 / 4.7 d |
| detector state | `CLOSE_REF_PLACEHOLDER` (alarm) | `WAITING` (silent, correctly) |
| markets with a usable close | 0 of 351 | 351 of 351 |

---

## 1. The Kalshi-only fallback is dead — measured, not assumed

The task asked whether the empirical `open_time → true close_time` offset is
tight enough to stand in for an external source. **It is not, and it is not
close.** Over the 72 settled events (2026-05-21 → 2026-07-25):

| | n | min | median | max | sd |
|---|---:|---:|---:|---:|---:|
| open → true close, hours | 72 | 15.0 | 98.0 | 797.0 | 168.2 |

Per `(tour, round)` it stays useless — `KXPGA R3` alone spans 22.7h to 797h
(sd 226h), because Kalshi lists some events the week before and some a month
before. **The external source is mandatory.** This section exists so that
conclusion is on the record with a number attached, rather than being
re-litigated in three weeks.

---

## 2. Source chosen: ESPN's public golf API

Two unauthenticated endpoints, one call per tour per season plus one per
tournament:

```
GET site.api.espn.com/apis/site/v2/sports/golf/{league}/scoreboard?dates=2026
GET site.api.espn.com/apis/site/v2/sports/golf/leaderboard?league={league}&event={id}
```

`{league}` ∈ `pga` · `lpga` · `champions-tour` · `eur` (DP World) · `liv` —
all five tours P-022 quotes, 48 / 32 / 29 / 30 / 15 events in the 2026 season.

**Why not the alternatives.**

| source | why not |
|---|---|
| **DataGolf** (`src/datagolf_client.py` already exists) | Paid; `DATAGOLF_API_KEY` is not set. It is a model/odds feed, not a schedule feed. |
| **PGA Tour's own site** | Covers PGA and Champions only. LPGA, DP World and LIV each need a separate scraper, against HTML rather than a documented feed. |
| **Kalshi** | This is the problem being solved. `/markets` (all six time fields), `/events` (`strike_date: null`), `rules_primary/secondary` and the ticker string were all checked on 2026-07-27 and none carries the round date prospectively. |

**The join key** is Kalshi's own `product_metadata.competition` — the
human-readable tournament name — matched against ESPN's `name`, disambiguated
by date. It matched **72 of 72** settled events.

### Three sources, in precedence order

| # | source | anchor | lag | when it runs |
|---|---|---|---:|---|
| 1 | `tee_times` | last `linescores[].teeTime` for that round | +4.0h | round is 1–2 days out |
| 2 | `r1_tee_anchor` | this event's R1 last tee + (n−1)·24h | +1.0h | R2/R3 before their own pairings publish |
| 3 | `tour_day_offset` | ESPN start day @04:00Z + (n−1)·24h + per-tour lag | 5.5–14.5h | **at listing time** |

**Source 3 is load-bearing, not decorative.** Checked live on 2026-07-27
against all three then-listed tournaments: ESPN returned **zero competitors**
and no tee times three days out. Every one of the 351 markets currently in the
pod's book is timed by the coarse path. `discover()` therefore **re-resolves
on every pass** and upgrades a book to `tee_times` as soon as ESPN publishes
them — a close resolved once at discovery would freeze the coarse answer.

Per-tour day lags, calibrated below: PGA 14.5 · LPGA 11.5 · DP World 12.0 ·
LIV 5.5 · Champions 12.0. The spread between tours is venue timezone: ESPN
stamps every tournament at 04:00Z (midnight ET) wherever it is played, so a UK
event's round ends ~5h "earlier" in that frame than a US one.

---

## 3. The tolerance question, answered explicitly

> *"With a 12-hour-wide placement window, what resolver error is acceptable
> before quotes land outside it?"*

Let `e = true_close − predicted_close`. The pod places new quotes when the
predicted hours-to-close is in `[12, 24]`, so the **realised** hours to the
true close at placement are `[12 + e, 24 + e]`.

**Strictly, the acceptable error is zero** — any `e ≠ 0` shifts the whole
placement interval off the validated one. That is not a usable answer, so the
real answer is that the tolerance is **asymmetric, and only one direction
matters**:

* **`e < 0` (predicted LATE) has effectively no tolerance.** It drags the
  latest placement below `H = 12h`. The measured round span — true close minus
  the *first* tee of the round — is **12.54h median** over 69 events, so the
  locked window's 12h lower edge sits essentially **exactly at the first tee**.
  It is not a safety margin; it is the boundary. An `e` of even −1h puts
  quotes into a live round, which is the regime Phase 2 measured directly
  (H = 6h) and where the edge collapses to marginal.
* **`e > 0` (predicted EARLY) is tolerable and its cost is bounded.** The pod
  posts earlier than validated — untested above 24h, so a real deviation, but
  strictly *further* from the round rather than into it.

**So: the acceptable tolerance is `e ≥ 0`, and every lag in
`src/golf_schedule.py` is set at or below the observed minimum residual to
force that.** A test asserts it (`test_every_lag_is_below_the_observed_
minimum_residual`) so a future "improvement" that centres the estimate on the
median cannot land quietly.

### One number that reframes the whole question

Under a **perfect** resolver, `H = 12h` is already inside a live round on
**46 of 69 events (67%)**. The conservative calibration cuts that to
**6 of 69**. The early bias does not merely avoid harm — it moves realised
placements *toward* the pre-round regime the rule intends, and away from the
one Phase 2 showed does not work.

---

## 4. Validation — the resolver replayed over settled ground truth

`golf_quirks_research/schedule_probe.py` (build the truth) and
`golf_quirks_research/validate_schedule_resolver.py` (replay the shipped
resolver over it). 72 settled events: PGA 36 · LPGA 15 · DP World 14 · LIV 6 ·
Champions 1.

**Live resolver, end to end — `e = true − predicted`, hours:**

| | n | min | median | max | **e < 0** |
|---|---:|---:|---:|---:|---:|
| **ALL** | 72 | **+0.16** | **+1.58** | +51.80 | **0** |
| KXPGA | 36 | +0.16 | +1.57 | +15.79 | 0 |
| KXLPGA | 15 | +0.25 | +1.65 | +18.50 | 0 |
| KXDPWORLDTOUR | 14 | +0.76 | +1.49 | +51.80 | 0 |
| KXLIV | 6 | +1.08 | +1.67 | +5.76 | 0 |
| KXCHAMPTOUR | 1 | +1.32 | +1.32 | +1.32 | 0 |

Resolved 72/72; source mix 69 `tee_times`, 3 `tour_day_offset`. **Each source
forced**, so the fallbacks are measured rather than assumed:

| source | n | min | median | max | e < 0 |
|---|---:|---:|---:|---:|---:|
| per-round tee times | 69 | +0.16 | +1.58 | +51.80 | 0 |
| R1 tee anchor + (n−1)d | 69 | +0.23 | +4.44 | +54.80 | 0 |
| tour day offset | 72 | +0.49 | +5.60 | +52.55 | 0 |

**What the pod would actually have done:**

| source | latest placement H (med / min) | earliest placement H (med / max) | placements reaching into a live round |
|---|---|---|---:|
| tee times | 13.6 / 12.2 | 25.6 / 75.8 | 6 / 69 |
| R1 anchor | 16.4 / 12.2 | 28.4 / 78.8 | 0 / 69 |
| day offset | 17.6 / 12.5 | 29.6 / 76.6 | 4 / 72 |

**The `+52h` tail is weather.** Nine of 69 events had a round suspended and
completed the next morning — `KXDPWORLDTOURR2LEAD-SOO26` (+51.8h),
`KXDPWORLDTOURR3LEAD-KLO26` (+31.8h), `KXPGAR1LEAD-USO26` (+15.8h) and six
others. No schedule source predicts a suspension; the resolver's error is in
the safe direction when one happens, and the quote simply rests longer.
Realistically these tournaments should be watched in the forward sample, not
excluded — the fill happened pre-round in every case.

### Two ground-truth traps found while building the validation set

Both would have produced double-digit-hour phantom "resolver errors":

* **`min(close_time)` across an event picks up WITHDRAWALS.** A competitor who
  pulls out has their own market closed days early —
  `KXPGAR1LEAD-3MO26` carries two markets closed `07-21T18:49Z` against a real
  round end of `07-24T00:11Z`.
* **`mode(close_time)` picks up THE CUT on R3 events.** After R2 roughly half
  the field is eliminated and their R3-leader markets all close at the R2
  stamp — frequently the *larger* cluster (`KXLPGAR3LEAD-MEILCFSG26`: 76 of
  149 markets). The round end is the **last cluster of any real size**.

A third was pure cache artefact: `settled_meta.jsonl` was pulled mid-settlement
for three 3M Open events and held only their withdrawal rows. Repaired from the
API rather than dropped (`repair_thin_events`), recovering three real
observations.

---

## 5. Throughput, flagged for Task 2 rather than answered here

In the cached sample, **69 of 72** round-leader events were listed at least 24h
before the resolved close, so the window can open on 96% of them. The three
exceptions are late-listed R2 events (`AUAOPBKT26` +17.8h, `SOO26` +18.6h,
`LIGK26` +9.3h) — never quotable under a `[12h, 24h]` window, which is a
property of the locked rule, not of the resolver.

The sample holds **25 distinct tournaments over 66 days ≈ 2.6/week**, against
the ~15–19/month (~4/week) assumed in §5 of the decision rule's cadence
arithmetic. **This is a lower bound and should not be used as a throughput
verdict** — the cache is demonstrably incomplete (one Champions event against
29 in ESPN's 2026 season). It is the right input for the gate-throughput audit,
and it points the same way the report of 2026-07-27 did: *"T = 14 in 3–4 weeks"
is optimistic.*

---

## 6. The two §7 gate-integrity holes — both closed

### (a) Books did not survive a restart — **FIXED**

`self.books = {}` on init and nothing ever read the fills log back, so after
any restart all three collateral caps re-armed from zero while the paper
exposure persisted. A restart mid-tournament could silently double the
per-name and per-tournament collateral, and the service had already been
restarted twice that week. Under §7 that **excludes the tournament from T**,
and nothing detected it.

`RoundLeaderFadeMakerEngine.rebuild_from_log()` restores every unsettled fill
(FILL rows with no matching SETTLE, deduped by `fill_id`) before the first
cycle; the runner calls it before discovery. The close reference comes from the
last `QUOTE` row for the ticker — which now records `close_ref` and
`close_source` — and defaults to `0.0`, meaning *"already past close, poll
settlement"*, when there is none. **A restored book with no schedule anchor is
never treated as quotable.** `discover()` re-resolves it if the market is
still open.

### (b) Cap-breach exclusion did not exist — **FIXED**

§7: *"A tournament run with any cap breached is EXCLUDED from T."*
`p022_checkpoint.py` had no breach concept at all — no exclusion, no field
read, nothing. A breach recorded nowhere and checked nowhere is not a gate
condition.

* `RoundLeaderFadeMakerEngine.check_caps()` runs every cycle and after every
  restore, and writes a `CAP_BREACH` row (`event`, `kind`, `observed`,
  `limit`) into the fills log the moment it observes one — once per
  `(tournament, cap kind)`, not once per cycle. An aggregate breach excludes
  every tournament with live exposure at the time.
* `p022_checkpoint.load_breached_events()` reads them back, and `evaluate()`
  drops those tournaments **before any statistic is computed**, so they cannot
  contribute to T, to the edge, or to the contract count. Excluded tournaments
  are printed by name and reason.

### (c) The detector is now crontabbed — **DONE**

```
*/30 * * * * cd /opt/betting-pod-shop && sudo -u bettingbot ./venv/bin/python -m scripts.p022_window_check >> /var/log/p022_window_check.log 2>&1
```

Installed in root's crontab on the droplet 2026-07-28 (previous crontab backed
up to `/root/crontab.bak.2026-07-28`), run once by hand to confirm it appends
to `data/p022_window_check/status.jsonl` as `bettingbot`. It currently exits 1
with `CLOSE_REF_PLACEHOLDER`, which is **correct against the code presently on
the droplet** and will clear on deploy.

**Its independence is preserved and extended.** It still imports the pod's own
resolution (`resolve_event_close`) rather than reimplementing it, and still
asks independent questions of the answer. It now asks three:

| state | alarm | meaning |
|---|---|---|
| `NO_MARKETS` | no | between tournaments — correct silence |
| `SCHEDULE_UNRESOLVED` | **yes** | **new** — markets listed, the external schedule resolved none, the pod is failing closed. Correct behaviour, indistinguishable from health, therefore loud. |
| `CLOSE_REF_PLACEHOLDER` | **yes** | the reference is a fallback (listing span ≥ 7d) — also catches a regression to reading Kalshi's own fields |
| `WAITING` | no | listed, real close reference, not yet in window |
| `WINDOW_OPEN_QUOTING` | no | working |
| `WINDOW_OPEN_NO_QUOTES` | **yes** | the original condition |

---

## 7. One correctness fix that came with the conservative close

`_cycle_book` used to `return` as soon as `now >= close_epoch`. With a
deliberately early close that would have **stopped processing fills for the
rest of the round** — median 1.6h of it, up to 52h on a suspension — on a
quote that is supposed to *rest* through the round. That is where Phase 2's
fills, and its edge, come from. The book now keeps working until the market
genuinely settles; settlement polling is throttled to once per 300s per book so
351 books do not hammer `/markets/{t}` every 20s cycle.

---

## 8. Deploy list — recommended, Sam's call

Everything below is committed, tested (**1,571 tests pass**, 55 of them P-022's)
and exercised against real payloads. Nothing has been deployed.

1. `src/golf_schedule.py` (new) — the resolver.
2. `src/round_leader_fade_maker.py` — schedule-driven discovery, mandatory
   resolver, restart-safe books, cap-breach recording, the fill-processing fix.
3. `scripts/run_round_leader_fade.py` — calls `rebuild_from_log()` on start.
4. `scripts/p022_window_check.py` — resolved close + `SCHEDULE_UNRESOLVED`.
5. `scripts/p022_checkpoint.py` — §7 cap-breach exclusion.

**Recommendation: deploy before 2026-07-29T15:30Z.** That is when the first
window opens; a deploy after it costs the AIG Women's Open R1 observation and
the next one is ROC26 three hours later. Standard path:

```bash
bash scripts/deploy.sh 129.212.176.202 restart
```

Post-deploy the detector should read `WAITING` with three resolved events and
`tour_day_offset` sources, and exit 0.

### Decisions that are yours

1. **`t_start_utc`.** It currently reads `2026-07-26T22:36:52Z` and records the
   start of a period in which the pod could not trade. My read is unchanged
   from 2026-07-27: reset it to the first demonstrated quote and record why —
   the counter should measure exposure to the hypothesis, not uptime. §12 does
   not cover this case.
2. **Weather-suspended tournaments.** The resolver's error runs to +52h on
   them and the quote is placed pre-round regardless, so I would **keep** them
   in T. Say if you want them excluded — that is a rule question, not a code
   one, and it should be written down before any of them settle.
3. **Posting above H = 24h.** The conservative calibration means the first
   quote goes out at a true H of ~25.6h (tee times) or ~29.6h (day offset)
   rather than 24h. This is a deviation from the validated window in the safe
   direction and I have not tuned it away, because narrowing the pod's own band
   to compensate would be a §8.1 parameter change that resets T to 0.

---

## Appendix — reproduce

```bash
python3 -m golf_quirks_research.schedule_probe             # build ground truth
python3 -m golf_quirks_research.validate_schedule_resolver # replay the resolver
python3 -m scripts.p022_window_check --no-write            # live state
python3 -m pytest tests/test_golf_schedule.py tests/test_round_leader_fade.py \
                  tests/test_p022_window_check.py tests/test_p022_checkpoint.py -q
```

Artefacts: `golf_quirks_research/schedule_probe.json`,
`golf_quirks_research/schedule_resolver_validation.json`, caches archived as
`golf_quirks_research/archive/schedule_probe_caches.tar.gz`.

Live state at the end of this run, from the pod's own `from_config` path:

```
schedule: GolfScheduleResolver   guard: AggregateRiskGuard
band (0.03, 0.12)  offset 0.02  window [12.0, 24.0]  caps [5.0, 50.0, 150.0]  series 13
discovered 351 books; unresolved: {}
  KXLPGAR1LEAD-AIGWO26      tour_day_offset  n=131  close 2026-07-30T15:30Z  window opens in 46.8h
  KXPGAR1LEAD-ROC26         tour_day_offset  n=142  close 2026-07-30T18:30Z  window opens in 49.8h
  KXCHAMPTOURR1LEAD-POI26   tour_day_offset  n= 78  close 2026-07-31T16:00Z  window opens in 71.3h
```
