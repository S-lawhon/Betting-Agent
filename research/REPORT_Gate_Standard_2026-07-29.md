# Gate instrumentation standard — built and validated backwards, 2026-07-29

**Verdict: BUILT. It re-detects every bug that motivated it, and it does not
pass the control on one axis — which turned out to be a true fact about the
control, not a defect in the checklist.**

Deliverables: `docs/GATE_INSTRUMENTATION_STANDARD.md` (nine conditions, each
tied to a failure that actually happened here),
`scripts/check_gate_instrumentation.py` (the enforcer),
`tests/test_gate_instrumentation.py` (15 tests + a ratchet).

---

## 1. The confusion matrix

Run against three trees. The historical tree is a real `git worktree` at
`62c3c12` (2026-07-26 19:49), with `data/` symlinked so the comparison is
apples-to-apples.

### 2026-07-26 — must fail P-014, P-015 and P-022

| gate | verdict | failing checks | is this the right reason? |
|---|---|---|---|
| **P-014** | **FAIL** | `1_sanctioned_reader`, `7_source_resolvable`, `9_blocked_on_vocabulary`, `4_decision_rule` | **Yes, exactly.** No reader existed; `source: trade_log` resolves to nothing in `_gate_progress`; and `blocked_on: time` is contradicted by the absence of a reader — *"it is blocked on a DEFECT, and `time` says a sample is accruing at a known rate."* |
| **P-015** | **FAIL** | `5_emits_progress_and_threshold` (**`progress=None threshold=None`**), `4_decision_rule`, `8_projection_not_invented` | **Yes.** The reader ran and produced *nothing*, which is the observable signature of a reader globbing a file that has never existed. |
| **P-022** | **FAIL** | `2_reads_the_real_file`, `4_decision_rule` | **Partially — see §3.** It fails, but not for its real 2026-07-26 reason. |
| **P-016** | FAIL | `1_sanctioned_reader`, `7_source_resolvable`, `4_decision_rule` | **A gate nobody had audited.** `source: maker_fills` with no reader at all — the same shape as P-014, and it was not in the original table of five. |
| **P-017** (control) | **FAIL, 1 check** | `4_decision_rule` only | **See §2.** |

### Today, on the droplet — where the data actually is

| gate | failing checks | reading |
|---|---|---|
| P-001 | `4_decision_rule` | reader, paths, progress/threshold, key and label **all now pass** |
| P-014 | `4_decision_rule`, `9_blocked_on_vocabulary` | **`1`, `5` and `7` now PASS** — independent confirmation of last night's fix |
| P-015 | `4_decision_rule`, `8_projection_not_invented` | **`5` now emits `progress=0 threshold=120`** where it emitted `None/None` — independent confirmation |
| P-016 | `1`, `4`, `7` | unchanged; nobody has touched it |
| P-017 | `4_decision_rule` | unchanged |
| P-022 | `2_reads_the_real_file`, `4_decision_rule` | see §3 |
| | **11 failing checks across 6 gates** | (16 on the 07-26 tree) |

**The two fixes made by hand on 2026-07-28 are visible as check transitions,
which is the strongest form of the validation**: nobody told the enforcer what
was fixed, and it recovered both.

> **Run it on the droplet.** On a developer checkout `2_reads_the_real_file`
> reports UNKNOWN (no `data/`) or fails spuriously against a stale local log —
> the Mac run shows 15 failures against the droplet's 11 for exactly that
> reason. The check needs the logs, like `check_fee_fixture.py` needs the
> network.

---

## 2. The control does not pass, and the checklist is not wrong

The prompt says: *"Run it against P-017, the known-good control. It must pass.
If it passes any of [the broken ones], the checklist is wrong."* P-017 fails
**one** check: `4_decision_rule`.

I checked before weakening anything. **There is no `P017_DECISION_RULE.md`
anywhere in the repository**, and the registry gate carries no
`rule_document` and no `rule_status`. P-017 has an 8-tournament threshold,
`blocked_on: nothing`, one settled tournament at **−9.89 ¢/ct** against a
+6.8 ¢/ct backtest — **and no kill line, no promotion condition and no
hard-kill z.**

So P-017 was the control for *readability*, which is what the throughput audit
measured, and it passes all eight readability checks. It is not a control for
*being fully instrumented*, and the standard is the first thing to have asked.

**Weakening check 4 to make the control pass would have been the wrong move**,
and it is the exact failure mode the prompt warns about in the other
direction. Reported instead as a finding: **P-017 needs a decision rule, and
its first forward evidence is already negative.**

---

## 3. What it cannot catch, stated now

**P-022's 2026-07-26 failure is invisible to a static checklist.** Its problem
was not instrumentation — the reader was fine — it was that the *pod* could
not compute its own placement window and therefore produced no observations at
all. Perfect instrumentation, nothing to instrument.

The enforcer does flag P-022, on `2_reads_the_real_file`, but for the adjacent
reason: **there are no P-022 rows in the trade log, because P-022 has never
traded.** The check cannot distinguish *"the reader points at the wrong
file"* (P-015) from *"the pod has genuinely never produced a row"* (P-022).
It fails either way, deliberately — a reader never proven against a real row is
exactly what P-015 looked like for months — and the message says so:

> *"either the path is wrong, or the pod has never produced a row — both need
> a human."*

**The detector for the P-022 class is a live-behaviour watch**, not a static
checklist: `scripts/p022_window_check.py`, extended tonight under Task 1. The
two are complements and the standard document says so.

---

## 4. Two false positives found by running it, and fixed

A screen that cries wolf gets ignored, and an ignored screen is the thing
being prevented. Both were found on the first live run, not in review:

1. **A per-host false failure.** `2_reads_the_real_file` failed on every gate
   on the Mac, because a developer checkout has no `data/`. It now returns
   **UNKNOWN** when the declared paths exist but the host carries no trade data
   at all, and FAILS only when the paths do not exist (P-015's real bug) or the
   logs hold rows for other pods but none for this one.
2. **`verdict` counts as an explanation.** `6_gate_key_matches` fires when rows
   exist, the reader counts zero, and it gives no reason — the JDAY signature.
   P-001 explains its zero in `verdict` (*"clean forward sample is empty; the
   epoch is 2026-07-26 21:31Z"*), not in `reason`, and was flagged wrongly.

A third design choice came from the same instinct: **check 7 reads the source
map out of `manager/checks.py` rather than hard-coding it.** Duplicating the
mapping would make the check agree with a copy instead of with the code that
runs — the same mistake in a new place.

---

## 5. The `blocked_on` vocabulary

Added to the standard and enforced. `time` was, on 2026-07-28, honest for
exactly two of five pods.

| value | means |
|---|---|
| `time` | a sample is accruing **at a known rate** toward a defined gate |
| `defect` | something is broken; calendar will not fix it |
| `measurement` | the accrual rate is **not yet measured**, so no date can be stated |
| `backtest` | code exists and is inert, waiting on its own validation study |
| `decision-rule` | observations are accruing but **no pre-registered rule exists** |
| `human` / `data` / `external` / `nothing` | unchanged |

The enforcer rejects a label it can contradict from data: `time` with no
reader is `defect`; `time` with no decision rule is `decision-rule`. It fires
on P-014 today, correctly — the rule written tonight is **not locked**, so
`rule_status` still reads `MISSING`.

Two new values are already in use from tonight's other tasks: `backtest`
(P-018) and `measurement` (P-001).

---

## 6. CI wiring

* `tests/test_gate_instrumentation.py` — **15 tests**, each reconstructing a
  real historical failure: P-014's missing reader, P-015's nonexistent path,
  P-017's hand-typed `progress: 1`, the JDAY key mismatch, P-001's undeducible
  forward date. Plus the control (a fully instrumented gate is silent) and the
  discipline cases (a data-less checkout is UNKNOWN not FAIL; refusing loudly
  is CORRECT; a historical `locked_date` is not a projection).
* **A ratchet.** `GATE_INSTRUMENTATION_CHECK=1` runs the enforcer against the
  real registry and fails if the failing-check count rises above the baseline.
  It may fall, never rise.
* **A read failure is a FAILURE, never a skip** — an unreadable registry exits
  1, asserted by test. A checker that exits 0 because it could not run is
  indistinguishable from a clean bill of health.

---

## 7. What Sam should take from this

1. **P-017 has no decision rule** and one negative settled tournament. It is
   the only pod in the fund with live money-shaped evidence and no locked line.
2. **P-016 has no reader and an unresolvable `source: maker_fills`** — the
   same defect as P-014, on a pod that was not in the original table of five
   because nobody was looking at it. It is `blocked_on: nothing`, which reads
   as healthy.
3. **Every gate fails `4_decision_rule` today**, though for four different
   reasons: none exists (P-016, P-017, P-001), one exists but is not locked
   (P-014), or one exists and is locked but the gate does not *name* it
   (P-015, P-022). The last is a one-line registry fix each and would take the
   failing count from 11 to 9.
4. **Nothing here changed a gate, a threshold or a decision rule.** The stop
   rule was observed: this task built a check, not a verdict.
