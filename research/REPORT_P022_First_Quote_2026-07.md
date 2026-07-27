# P-022 — First Live Quote Watch

**Task:** `research/prompts/PROMPT_P022_First_Quote_Watch.md`
**Run:** 2026-07-27, ~00:20–01:10 UTC · read-only against live `data/`
**Verdict:** **No live quote was observed, and none will be.** The pod is
still structurally incapable of quoting. Two further defects were found
behind it, either of which would independently hold T at 0.

> ## UPDATE 2026-07-28 — Defect 1 is FIXED; still no live quote, for a
> ## calendar reason rather than a structural one
>
> `src/golf_schedule.py` resolves the round close from ESPN's public golf API
> for all five tours, validated against 72 settled round-leader events with a
> **one-sided-early error on 72 of 72** (min +0.16h, median +1.58h). The three
> listed events now carry real close references — ROC26 `07-30T18:30Z`,
> AIGWO26 `07-30T15:30Z`, POI26 `07-31T16:00Z` — instead of one shared
> `2026-08-16T00:00:00Z` placeholder, and all 351 markets are in the book.
>
> **A live quote has still NOT been observed, and the window has not yet
> opened.** The earliest opens **2026-07-29T15:30Z** (AIG Women's Open R1),
> ~47h after the fix. That is the honest statement the guardrail asks for: the
> fix is verified by tests (1,571 pass) and against real live payloads, and it
> is **not** verified by a fill.
>
> §5's two remaining holes are also closed — books now survive a restart
> (`rebuild_from_log()`) and cap breaches are recorded at the moment they
> happen and excluded from T by `p022_checkpoint.py`. The detector is
> crontabbed on the droplet (`*/30`).
>
> **Nothing is deployed.** See `research/REPORT_P022_Close_Time_2026-07.md`
> §8 for the deploy list and the recommendation to ship before
> 2026-07-29T15:30Z.

> **No P-022 parameter was changed and nothing was deployed.** Defect 1 is
> reported, not tuned — it is a design decision and it is Sam's. Defects 2 and
> 3 were fixed on request: neither depends on the Defect 1 decision, neither
> touches a §7 parameter, and neither changes the quoted population. Defect 2
> is read-side only; Defect 3 was **deployed 2026-07-27 16:00 UTC** and
> verified in place.

---

## 0. Headline

The 2026-07-26 reconciliation fixed a real bug and did not fix the pod.
`_close_epoch()` was changed to prefer `close_time` over
`occurrence_datetime` on the strength of a 10-of-10 measurement — but that
measurement was taken on **settled** markets, and the pod only ever reads
**open** ones. On an open round-leader market the two fields are the same
value. Preferring one over the other changes nothing.

Golf relisted at **2026-07-27T00:10:00Z**, six hours ahead of schedule for
this task. The runner discovered **346 markets across 3 events** at
00:22:20 UTC and quoted nothing — for the same reason as before, with a
different field.

Three independent breaks, each sufficient on its own to hold **T = 0 forever**:

| # | Defect | Status |
|---|---|---|
| **1** | Close reference on an OPEN market is a ~20-day fallback placeholder → the [12h, 24h] window opens ~2.5 weeks after the round settles | ~~blocks quoting~~ **FIXED 2026-07-28 (needs deploy)** |
| **2** | `p022_checkpoint.py` reads four log paths, none of which the pod writes; its rows also lack the `outcome` and `contracts` fields the reader requires | ~~blocks the gate reading~~ **FIXED** |
| **3** | §7's `AggregateRiskGuard` precondition is unsatisfied in the running process — `risk_guard` is `None` live | ~~precondition recorded as met, isn't~~ **FIXED + DEPLOYED** |

---

## 1. The next quotable window, in UTC

**As the pod actually computes it:** `2026-08-15T00:00:00Z → 2026-08-15T12:00:00Z`
for all three listed events. That is the arithmetic answer and it is useless
— it is roughly 17 days *after* the rounds it is nominally for, by which time
the markets are settled and `_mid()` returns `None`.

**As it should be:** not computable from Kalshi. See §2.

Observed live state (`scripts/p022_window_check.py`, 2026-07-27T00:5xZ):

| event | markets | close reference | h to close-ref | listing span |
|---|---:|---|---:|---:|
| `KXPGAR1LEAD-ROC26` (Rocket Classic) | 141 | 2026-08-16T00:00:00Z | 479.4 | 19.99 d |
| `KXLPGAR1LEAD-AIGWO26` (AIG Women's Open) | 127 | 2026-08-16T00:00:00Z | 479.4 | 19.99 d |
| `KXCHAMPTOURR1LEAD-POI26` (Portugal Invitational) | 78 | 2026-08-16T00:00:00Z | 479.4 | 19.99 d |

Three tournaments, three tours, one identical close instant. That alone is
the tell.

---

## 2. Defect 1 — the close reference is a placeholder on every open market

### What the API actually returns

On all 346 open markets, **all five time fields collapse to the same value**:

```
open_time                 2026-07-27T00:10:00Z
close_time                2026-08-16T00:00:00Z
occurrence_datetime       2026-08-16T00:00:00Z
expected_expiration_time  2026-08-16T00:00:00Z
expiration_time           2026-08-16T00:00:00Z
latest_expiration_time    2026-08-16T00:00:00Z
```

The market carries `can_close_early: true` and
`early_close_condition: "This market will close and expire after a winner is
declared."` `close_time` is the **scheduled fallback**, and it is only
rewritten to the true value **at the moment the market closes**.

Confirmed on settled markets of the same series, where `close_time` has moved
to a to-the-second early-close stamp while `expiration_time` still holds the
untouched fallback:

| market | open_time | close_time (real) | expiration_time (fallback) | listing span |
|---|---|---|---|---:|
| `KXPGAR1LEAD-3MO26` | 07-22T16:10Z | **07-24T00:11:10Z** | 08-09T00:00Z | 1.33 d |
| `KXPGAR1LEAD-COPC26` | 07-16T16:10Z | **07-16T23:40:01Z** | 08-02T00:00Z | 0.31 d |
| `KXPGAR1LEAD-THOC26` | 07-13T19:11Z | **07-16T21:02:30Z** | 08-02T04:00Z | 3.08 d |

### Why §11b's measurement was right and its conclusion was wrong

§11b measured `occurrence_datetime − close_time` = +13.2 to +18.2 days on
10 of 10 markets, and concluded `close_time` is the real round end. Every one
of those measurements was on a **settled** market — the only state in which
`close_time` has been corrected. In the state the pod reads, the delta is
**zero**. The reconciliation swapped one placeholder for the same placeholder.

This is the third instance in four days of the process rule the reassessment
already wrote down. The generalised form:

> **Measure a field in the state your code will actually read it in.** A
> settled market and an open market are different objects wearing the same
> schema.

### It is exchange-wide, not a golf quirk

Control, on a series where Kalshi demonstrably knows the schedule:

```
KXMLBGAME-26JUL261920NYYPHI-NYY   open 07-23T23:40Z   close 07-29T23:20Z
```

That game's first pitch was 2026-07-26 19:20 ET — already played at the time
of this query — and the market was still open carrying a close_time three
days later. Kalshi does not publish real close times prospectively for
early-closing sports markets, for golf or anything else. The real game time
is in the *ticker*; the round-leader ticker (`KXPGAR1LEAD-ROC26-ZDOU`) has no
date in it.

### What this means for the locked rule

§7's posting window — "quote only at H ≈ 12–24h pre-round" — is defined
against a quantity **Kalshi does not publish in advance**. The Phase-2
backtest could use it because `quirks_common.anchor_at()` anchors on
`rec["close_time"]` from *cached settled* markets, where it is the real
early-close stamp. The live pod cannot reproduce that timing from any field
observed at quote time.

Sources checked and found not to carry the round date prospectively:
`/markets` (all six time fields), `/events/{ticker}` (`strike_date: null`,
`strike_period: ""`), `rules_primary` / `rules_secondary`, the ticker string,
and the sibling golf series for the same tournament (`KXPGA`, `KXPGATOP5/10/20`,
`KXPGAMAKECUT` — all have zero open markets for ROC26 as of this run).

**This is a design decision, not a fix, and it is explicitly out of scope
here.** The options, stated neutrally:

1. **External schedule** (DataGolf via the existing `src/datagolf_client.py`,
   or a static tour calendar). Preserves the locked window exactly. Adds a
   dependency; `DATAGOLF_API_KEY` is not set.
2. **Re-anchor the window on `open_time`** instead of close. Cheap and
   dependency-free, but observed listing spans are 0.31 / 1.33 / 3.08 days —
   too variable to stand in for a 12h precision requirement, and it is a
   **parameter change that resets T to 0 under a new pod ID (§8.1)**.
3. **Accept that the window cannot be implemented** and re-register P-022
   with a window that is observable.

Note also, for whichever is chosen: `COPC26` listed **7.5 hours** before its
round ended. Under a [12h, 24h] window, that tournament was never quotable at
all. If that is typical, the ~15–19 qualifying tournaments/month assumed in
§5's cadence arithmetic — and therefore the "T = 14 in 3–4 weeks" estimate —
is optimistic.

---

## 3. Defect 2 — the gate reader cannot see the pod's output — **FIXED 2026-07-27**

Found while verifying §4. Independent of Defect 1, and it survives fixing it.

`p022_checkpoint.py` reads four patterns:

```
data/pods/P-022.jsonl          data/round_leader_fade/*.jsonl
data/trade_logs/trade_log.jsonl        data/trade_logs/archive/*.jsonl
```

The pod writes to **`data/trade_logs/round_leader_fade_fills.jsonl`**, which
matches none of them. `data/pods/` does not exist on the droplet at all.
Worse, the shape does not match either: the reader keeps only rows whose
`outcome` is `WIN`/`LOSS` and sizes them by `contracts`; the pod's `SETTLE`
row has neither field (it writes `result`, `payout`, `pnl_usd`, `qty`).

Demonstrated end-to-end — the reader pointed **directly** at a file holding
two genuinely settled P-022 rows:

```
SETTLE rows in file: 2
$ python3 -m scripts.p022_checkpoint --log <that file>
  settled trades : 0   tournaments: 0
  VERDICT: NO DECISION — no settled P-022 trades yet
```

The checkpoint's own docstring claims *"It reads the row shape the pod will
write; if the pod ships a different shape, fix the LOADER here rather than the
RULE."* It was written before the pod's shape existed and never reconciled
against it. Same family as the tennis and golf settlers, which both read
`data/pods/` and both found it empty (`tests/test_tennis_settler.py`,
`tests/test_golf_settler.py:462`).

Consequence: had Defect 1 not existed, the pod would have quoted, filled and
settled correctly, and **the registry's derived gate progress would still have
read 0** — with the reassessment's new "derived, not hand-typed" machinery
faithfully reporting it. Deriving a number from a reader that cannot see the
data is not better than typing it; it is the same failure with more
credibility.

### The fix

Fixed as a **loader change, not a rule change** — which the docstring already
sanctioned — because it does not depend on the close-time decision and should
land either way.

`normalise_fade_settle()` converts an engine `SETTLE` row into the canonical
record shape at load time, so `tournament_key()` and `per_contract_cents()`
— which are the actual implementation of §2 and §3 — are **untouched**.
`DEFAULT_LOGS` now points at `data/trade_logs/round_leader_fade_fills*.jsonl`
(plus an archive glob); the four old patterns are kept, since a settler-written
P-022 row would still land in the shared trade log.

Three details worth recording, each of which could have quietly distorted the
verdict:

- **`event` is the event CODE** (`ROC26`), not the event ticker, so R1/R2/R3
  of one tournament already pool into one observation exactly as §2 requires.
- **Countability does not run through `outcome`.** A settlement worth exactly
  $0 is a real observation of taken risk, and a WIN/LOSS sign test would drop
  it. It cannot arise under the locked parameters — fills are ≤ ~0.14¢ and
  every non-zero payout exceeds that — but the reader should not depend on
  that staying true. `outcome` is still populated for display.
- **Dedup is on `fill_id|ticker`**, one row per fill, so a rotated log or the
  two overlapping globs cannot double-weight a tournament.

Verified by re-running the exact demonstration that failed above — the reader
pointed at a file of two genuinely settled rows:

```
$ python3 -m scripts.p022_checkpoint --log <that file>
  tournaments (T): 2   contracts: 10
  edge           : -44.00 c/contract      paper P&L: $-4.40
  VERDICT: NO DECISION — T=2 < 14 tournaments; underpowered
```

Against live data it still correctly reports `T = 0` (nothing has settled).
`tests/test_p022_checkpoint.py`, 11 tests: the regression itself, round
pooling, contract-weighting within a tournament against equal-weighting
across, scalar counted at realised value (including a three-way tie),
non-`SETTLE` rows ignored, other pods' rows ignored, dedup, and the legacy
`trade_store` shape with `VOID` still excluded. Full suite 1,534 green.

**Not fixed here:** the §7 cap-breach exclusion is still absent from the
reader (§5). That one needs a breach to be *recorded* before it can be read,
so it is a pod change as well, and it is listed as owed.

---

## 4. What *does* work — verified

### Scalar settlement books as a partial payout, on real data ✓

Task step 4 asked for confirmation on real data rather than trust. Verified by
driving the pod's own `_maybe_settle()` against **live payloads** for the
three genuinely scalar round-leader markets that exist
(`KXPGAR1LEAD-COPC26-TCLE`, `KXLPGAR1LEAD-ISPHWSO26-{LAUCOU,JENSHI}`, all
`settlement_value_dollars = 0.5000`, `settlement_value = null` — the two-way
dead heat, exactly as documented):

```
fade sold at 6c x 5 contracts, real payload result=scalar sv=$0.50
BOOKED: payout=0.5  maker_fee=0.0  pnl_usd=-2.20     (= -44c/contract)
booked at $0.00 (the old void bug)? no
```

**The loss tail is preserved.** A 6¢ fade hit by a two-way tie books −$2.20,
not $0.00. This is the specific thing that would have let the gate pass a
money-losing strategy, and on the P-022 path it is correct. Maker fee is 0 as
required, and `--check-fees` now reports **OK — all \*LEAD series free** (the
§3 OPEN DEFECT is closed).

### Discovery ✓

`discover()` returns 346 (>0) against live open markets, and the event-level
MIN-close logic behaves. The half of the fix that could be verified, is.

---

## 5. Defect 3 and the cap questions (task steps 3 and 5)

### `AggregateRiskGuard` — §7 precondition was not met in the running process — **FIXED 2026-07-27 (needs deploy)**

§7: *"Before P-022 can quote, it must be wired into `AggregateRiskGuard` with
reservations."* §11c records this as ✓ and §11d discusses its cross-process
limits. But **no guard is ever constructed**: `run_round_leader_fade.py` calls
`from_config(config)`, `from_config` never passes `risk_guard`, and there is
no `risk_guard` key in the P-022 config block. Live, `self.risk_guard is
None`, so `_reserve()` returns `True` unconditionally and
`release_reservation` is never called.

The mechanism is implemented and unit-tested; nothing wires it up. This is
**not an open exposure hole** — the in-process collateral caps in
`_cycle_book` still bind and are what actually limit the book — but §11d's
"✓" describes code that does not run. Same shape as the settler's pod filter:
*asserted in a docstring, applied in a path that isn't the live one.*

Worst-case-collateral sizing itself is correct where it is computed:
`per_ct_coll = 1 - quote_px` and the quote is sized to
`min(room_name_ct, room_name_coll/c, room_event/c, room_total/c)` — collateral,
not premium, and it binds before the 25-contract secondary bound.

### The fix, and the trap inside it

`from_config` now builds `AggregateRiskGuard.from_config(config)` unless a
caller passes `risk_guard=` explicitly. Built in `from_config` rather than in
the runner deliberately: "implemented in one path, absent from the one that
runs" is the failure this pod has now produced three times.

**The second half matters more than the first.** A standalone loop has no
`update_post_cycle`, which is what drains reservations in the 5-minute engine.
Wiring a guard without a lifecycle would ratchet reserved collateral upward
forever until the guard refused everything — **muting the pod permanently,
which is strictly worse than not wiring it at all**, and reintroducing the
exact failure this workstream exists to detect. So the fix also adds:

- release on settlement (a settled market owes nothing further);
- `_sweep_reservations()` at the end of every cycle, release-only by
  construction so it can never itself be refused, covering books that are
  done, gone, or holding neither a quote nor an open fill;
- reservations that cover **already-filled** collateral, not just the new
  quote — reservations are idempotent by `market_id`, so re-quoting with only
  the quote's figure would replace, and so erase, everything already filled
  on that name.

**Behaviour-neutral, verified by arithmetic against the merged droplet
config:** P-022's own §7 caps (aggregate 15% of bankroll = $150) are strictly
tighter than every guard exposure limit (pod 25% = $250, venue 30%, total
50%), so the §7 caps still bind first and the quoted population is unchanged.
The one guard limit that could bind first is `max_open_positions` — live value
200, against at most ~30 P-022 reservations, since a 0.5% per-name cap inside
a 15% aggregate admits ~30 names. A regression test asserts the guarded and
unguarded engines quote at the same price and size.

**Deliberately NOT wired: the daily-loss halt.** Settled markets are released
with `release_reservation`, not `close_position` — `close_position` calls
`record_pnl`, which halts the guard at a 5% daily loss, and the cooldown that
clears a halt is applied in `check_pre_cycle`, which a standalone loop never
calls. A halt would therefore be **permanent until restart** and would
silently exclude tournaments from T. That is a gate-affecting behaviour the
locked rule does not register; §7 asks for reservations, and that is what this
provides. Adding a halt is a separate decision.

Tests: 7 new in `tests/test_round_leader_fade.py` (23 → 30). Mutation-checked
— disabling the settle-release and the sweep fails three of them, including
the starvation regression, which quotes a full tournament, settles it, and
asserts a second tournament still quotes the same number of names on the same
guard. Suite 1,541 green.

> **Deployed 2026-07-27 16:00 UTC.** `betting-round-leader-fade` restarted onto
> the new code and verified in place: `from_config` yields a real
> `AggregateRiskGuard` with `reserve_trade` / `release_reservation`, the sweep
> is present, and band / offset / window / caps / series read back
> byte-identical (`(0.03, 0.12)`, `+0.02`, `[12h, 24h]`, `$5 / $50 / $150`, 13
> series). 49 P-022 tests pass on the droplet. Main engine redeployed and
> healthy in the same pass.

### Cap-breach exclusion — not implemented, and there is a live breach path

§7: *"A tournament run with any cap breached is excluded from T."*
`p022_checkpoint.py` contains **no cap-breach concept at all** — no exclusion,
no breach field read, nothing. A breach recorded nowhere and checked nowhere
is not a gate condition.

In fairness, the engine is built so caps cannot be breached *within a
process*: it sizes each quote to the remaining room and pulls when room is
zero. But there is a real breach path it does not cover:

> **Books do not survive a restart.** `self.books = {}` on init, and nothing
> ever reads `round_leader_fade_fills.jsonl` back. After any restart —
> deploy, reboot, crash — `book.collateral` is 0 for names the pod is still
> short, and all three caps re-arm from zero while the paper exposure
> persists. A restart mid-tournament can therefore double the per-name and
> per-tournament collateral, silently.

The service has already been restarted twice this week. Under §7 that would
exclude the tournament from T; nothing detects it, and per Defect 2 nothing
would count it either way.

---

## 6. Deliverable — the window detector

**`scripts/p022_window_check.py`** (read-only, exit 1 = alarm), registered in
`manager/registry.yaml` as job `p022_window_check`, severity `warn`, output
`data/p022_window_check/status.jsonl`, `max_stale_hours: 3`. A **job, not a
heartbeat** — the existing reasoning in the services comment stands.

The design point that matters: **it does not reuse the pod's window
arithmetic.** A detector that decides "is a window open?" the way the pod does
agrees with the pod when the pod is wrong, and would have sat silent through
all three dead days and through tonight. Instead it takes the pod's own
`_close_epoch` (imported, not reimplemented, so it tracks the pod) and asks an
independent question of the answer: *is that reference even a real timestamp?*

The discriminator is the listing-to-close span — observed real spans 0.31 /
1.33 / 3.08 d against fallbacks of 16.3 / 17.3 / 20.0 d, so the 7-day default
sits in a wide empty gap. Field-collapse is deliberately **not** used as the
test, because Kalshi collapses `close_time` onto `expiration_time` even when
it knows the schedule (the MLB control), so collapse means only "not yet
closed".

| state | alarm | meaning |
|---|---|---|
| `NO_MARKETS` | no | between tournaments — correct silence |
| `WAITING` | no | listed, real close reference, not yet in window |
| `WINDOW_OPEN_QUOTING` | no | working |
| `WINDOW_OPEN_NO_QUOTES` | **yes** | the condition the task was written to catch |
| `CLOSE_REF_PLACEHOLDER` | **yes** | **the live state** — no window can open while these markets are tradeable |

Every run also banks the per-event close reference it saw, so the remaining
open question — *does Kalshi ever correct `close_time` before the round?* — is
answerable from the status log's own history without a second script. My
reading of the MLB control is that it does not, but that is an inference from
one family and the log will settle it.

Tests: `tests/test_p022_window_check.py`, 8 new. Full suite green.

---

## 7. What is owed

Nothing here was fixed, per the guardrails. In priority order:

1. ~~**Defect 1 is a decision, not a patch**~~ — **decided and built
   2026-07-28**: option 1 (external schedule), via ESPN rather than DataGolf,
   which needs no key and covers all five tours. The locked window is
   preserved exactly and T is not reset by the fix itself.
   See `research/REPORT_P022_Close_Time_2026-07.md`.
2. ~~**Defect 2**~~ — **done 2026-07-27** (§3). Loader-side only, no deploy
   needed, no rule change. The reader now sees the pod's rows.
3. ~~**Defect 3**~~ — **done 2026-07-27** (§5), with the reservation lifecycle
   a standalone loop needs. **On the deploy list**, and §11c/§11d should be
   amended to say the wiring was absent until now.
4. ~~**Restart-safe books**~~ — **done 2026-07-28** (§5).
   `rebuild_from_log()` restores unsettled fills before the first cycle, and
   `check_caps()` records a `CAP_BREACH` row that `p022_checkpoint.py` now
   honours as a §7 exclusion.
5. ~~**Crontab the detector.**~~ — **done 2026-07-28**, installed in root's
   crontab (previous crontab backed up to `/root/crontab.bak.2026-07-28`) and
   confirmed appending to the status log as `bettingbot`. The line:
   `*/30 * * * * cd /opt/betting-pod-shop && ./venv/bin/python -m scripts.p022_window_check`
   as `bettingbot`.

### On T

`t_start_utc: 2026-07-26T22:36:52Z` is currently recording the start of a
period in which the pod cannot trade. Whether to leave it (T = 0 is honest
either way) or reset it once Defect 1 is resolved is Sam's call; §12 does not
cover a T that started against a pod that could not quote. My read: reset it
to whenever the pod first demonstrably quotes, and record why — the counter
should measure exposure to the hypothesis, not uptime.

---

## Appendix — reproduce

```bash
python3 -m scripts.p022_window_check            # exit 1, CLOSE_REF_PLACEHOLDER
python3 -m scripts.p022_checkpoint --check-fees # OK; NO DECISION, T=0
python3 -m pytest tests/test_p022_window_check.py tests/test_round_leader_fade.py -q
```

Live evidence, droplet journal:

```
Jul 26 22:36:52  P-022 round-leader fade-maker starting (paper).
Jul 27 00:22:20  P-022: discovered 346 new round-leader markets
                 (no quote log has ever been written)
```
