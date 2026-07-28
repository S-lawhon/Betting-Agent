# P-016 — live pod with a broken reader, or retired pod with a stale entry?

**Run:** 2026-07-28, Task 6 of `RUN_QUEUE_2026-07-28-day.md`.

> **RETIRED — and the registry entry lied about it in three separate places.**
> The gate was never "unreadable and still counting" (P-014's defect). It had
> **already resolved**: 814 fills against a threshold of 500, verdict KILL,
> 2026-07-21. Nobody marked it done, so an answered question sat in the
> registry looking pending, on a pod labelled `blocked_on: nothing`.
>
> **The standard needed amending too.** It had no concept of a *closed* gate,
> so it demanded live instrumentation for a question answered five weeks ago —
> and separately, it was **skipping every pod with no gate block at all**,
> which is how P-018 never appeared in its output in any form.
>
> Failing checks across the registry: **15 → 12**. Suite **1,751 passed**.

---

## 1. Live or retired — established from evidence, not documents

Measured on the droplet today:

| evidence | finding |
|---|---|
| `systemctl is-active betting-live-maker` | **inactive** |
| `systemctl is-enabled betting-live-maker` | **disabled** |
| `ExecMainStartTimestamp` | **empty** — the unit has not run at all |
| `data/KILL_MAKER` | present, touched **2026-07-22 01:22** |
| `data/trade_logs/maker_fills.jsonl` | 1.7 MB, **3,700 rows**, last written **2026-07-22 03:44** |
| its composition | **814 FILL**, 2,329 MARKOUT, 557 SETTLE — all `pod_id: P-016` |
| last row | a `SETTLE` on `KXMLBGAME-26JUL212040WSHCOL-COL`, `2026-07-22T03:44:40Z` |

**Does the last fill postdate the 2026-07-21 retirement? Yes — by about a
day, and that is expected rather than alarming.** Sam's decision was
2026-07-21; the kill file was touched 2026-07-22 01:22; the maker pulled its
quotes within one cycle and then spent the next two hours **settling the books
it already held**. The final row is a settlement, not a fill. Nothing has been
written for six days.

### One thing that looked live and is not

`data/shadow_maker/quotes.jsonl` has an mtime of **today, 17:30**, and its
directory name is close enough to matter. It is **`scripts/shadow_maker.py`** —
a separate, separately-registered job (`manager/registry.yaml:177`), not P-016.
Checked rather than assumed, because "a maker thing wrote today" is exactly the
observation that would have flipped this verdict.

### The `retired:` block was already right

The registry's `retired:` block is complete and accurate — decision date,
decider, final sample, the two-number diagnosis (+4.08 ¢ spread capture against
−1.29 ¢ markout), how it was killed, and the v2 NO-GO. **The retirement was
recorded properly. What was not recorded is that the GATE was finished.**

## 2. What was actually wrong — three stale claims

| # | claim in the registry | truth |
|---|---|---|
| 1 | `service: betting-live-maker  # unit still runs, but KILL_MAKER pulls all quotes` | The unit is **inactive AND disabled**, with no start timestamp at all. It does not run. |
| 2 | `blocked_on: nothing` | Literally true, materially false. `nothing` means *"running normally"* in the standard's own vocabulary. On a killed pod it reads as **ready to go**. |
| 3 | a `gate:` block with `source: maker_fills`, no reader, `threshold: 500` | The gate **resolved on 2026-07-21 and returned KILL**. Presented as pending. |

**The prompt asked whether this is P-014's defect. It is not, and the
difference matters.** P-014's gate was unreadable *and still counting* — it hid
331 live observations for months. P-016's gate had already returned its
verdict; what was hidden was the *closure*. Same symptom in a checker, opposite
diagnosis.

## 3. The path taken — retire it properly

`manager/registry.yaml`, P-016:

* `blocked_on: nothing` → **`retired`**, a new vocabulary entry meaning
  *"terminal; the gate has resolved and there is nothing left to do."*
* `service:` comment corrected to `inactive AND disabled since the
  retirement`, with the measured evidence inline and an explicit note that
  `data/shadow_maker/` is a different job.
* The gate marked **`status: CLOSED`** with `resolved_on: 2026-07-21`,
  `verdict: KILL`, `resolved_by` (the threshold reached and failed), and
  `final_progress: 814`.

**`source: maker_fills` was deliberately NOT rewired.** It never resolved
through `manager/checks.py::_gate_progress`, and the file it names is frozen.
Building a reader now would be inventing a measurement path for a question
already answered — the opposite of the standard's intent.

**Nothing was deleted.** All 3,700 rows stay where they are. The 2026-07-25
harness loss is the precedent.

## 4. The standard needed two amendments, and the prompt predicted it

> *"A pod that the standard cannot classify is a gap in the standard… if you
> have to special-case P-016 to make the check pass, the check needs the
> change, and say so."*

**It did, and it is not a special case for P-016.**

### 4a. Check `0_closed_gate_is_substantiated` — a closed gate is not a pending gate

A gate may declare `status: CLOSED` and be exempt from the live-instrumentation
checks — **but only with all three of `resolved_on`, `verdict`, and a terminal
`stage`** (`killed` / `retired` / `shelved`).

**The obvious risk is that `status: CLOSED` becomes a way to silence any
inconvenient gate.** That is why the exemption is expensive to claim and why a
gate that claims closure without all three **FAILS loudly** rather than being
skipped. Pinned by six tests, of which the load-bearing one is
`test_a_live_pod_cannot_close_its_own_gate` — a `stage: paper` pod declaring
CLOSED fails on `stage is not terminal`.

### 4b. A pod with no gate block was passing BY ABSENCE

`run()` did `if not ws.get("gate"): continue`. **P-018 has no gate block** —
deliberately, so `manager/throughput.py` cannot project a date for a decision
nobody has defined — and it therefore **did not appear in the checker's output
in any form.** Silence read as health.

Gate-less pods are now reported as `0_has_a_gate`, status **unknown** (not
FAIL): a pod legitimately has no gate before its validation study exists. It
is now visible, with its `stage` and `blocked_on` printed, and the check text
says when it becomes a finding — *"the moment it starts accruing
observations."*

**This is the same failure mode as a reader returning 0 when it cannot read**,
one level up: the checker was defaulting to healthy on missing input.

### Before / after

| pod | before | after |
|---|---|---|
| **P-016** | FAIL, 3 of 9 (`1_sanctioned_reader`, `4_decision_rule`, `7_source_resolvable`) | **pass** — `CLOSED 2026-07-21 verdict=KILL stage=killed` |
| **P-018** | *absent entirely* | **visible** — `no gate block… (stage='build', blocked_on='backtest')` |
| total | **15 failing across 6 gates** | **12 failing across 11 gates** |

The gate count rising 6 → 11 is the point: five pods that the checker was not
looking at are now in its output.

## 5. The fee path — checked, not perturbed, and the note was about to mislead

The stop rule forbids perturbing the maker-fee path. **No fee behaviour was
changed.** But the dependency note in `src/kalshi_fees.py` said:

> *"P-016 relies on that fallback (it makes on KXMLBGAME, which does charge)
> and must not be perturbed while its gate sample is running."*

**P-016's gate sample is no longer running — so read literally, that sentence
now authorises changing the fallback.** It must not be changed, because the
dependency moved rather than ended:

| bare caller of `fee_per_contract(..., maker=True)` | feeds |
|---|---|
| `scripts/clv_settlement.py:118` | `clv_net_maker` → `clv_log.jsonl` → **P-001's LIVE CLV gate** |
| `scripts/clv_backfill.py:115` | the same field, historically |
| `src/maker_challenger.py:334` | markout comparisons |
| `scripts/maker_report.py`, `scripts/maker_diagnostics.py` | P-016 forensics |
| `src/pods/live_maker_pod.py:487` | P-016 itself (inert) |

The docstring is corrected to say exactly this: **the owner of the constraint
changed from P-016 to P-001; the constraint did not.** Documentation only —
`tests/test_kalshi_fees.py` 70 passed, unchanged.

## 6. Does P-016 need a decision rule?

**No.** It had one, it was pre-registered, and it fired:

> *≥ 500 fills AND positive fee-adjusted +5m markout AND robust to excluding
> the best single day → else kill.*

814 fills, +5m markout **−1.29 ¢/ct** fee-net, negative at every horizon
(+1m −2.26 ¢, +15m −1.94 ¢). It failed on the headline arm before the
robustness check was reached. **P-016 is the one pod in this fund whose gate
worked end to end**, which is worth saying out loud on a day when three other
gates needed repair.

## 7. Verification

* `manager/registry.yaml` parses; `python3 -m manager.checks` runs and renders.
* `scripts/check_gate_instrumentation.py`: 15 → **12** failing checks.
* Suite: **1,751 passed, 2 skipped** (from 1,745).
* **Nothing deployed, no service touched, no trading behaviour changed.**

**Pre-existing and NOT caused by this change** (present before it, unrelated to
P-016): `manager.checks` reports `P-001 / P-014 / P-015 / P-017 is in
pods.active but has no registry entry` — the check looks for a `pods:` key
while the entries live under `workstreams:`. Reported, not fixed; it is a
separate defect in the same family as everything else in this report.

## 8. For Sam

1. **P-016 is closed and correctly labelled.** Nothing owed.
2. **`p024-mlb-f5-research`** — unrelated, still yours to delete (carried).
3. **The gate-less-pod check will start mattering** the moment P-018 or any
   successor begins accruing observations. It is `?` today by design; it
   should become a FAIL when `stage` leaves `build`. Not encoded — that needs
   a rule about which stages must carry a gate, and that is your call.
4. **`src/kalshi_fees.py`'s general maker fallback is now P-001's dependency.**
   If the CLV gate's fee treatment is ever revisited, that is the one to touch
   carefully.
