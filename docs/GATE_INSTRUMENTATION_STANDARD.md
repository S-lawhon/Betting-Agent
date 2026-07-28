# Gate Instrumentation Standard

**Adopted 2026-07-29.** Enforced by `scripts/check_gate_instrumentation.py`.

A pod may not accumulate toward a gate until every condition below holds.
These are not principles; **each one is a failure that has already happened in
this repository**, and each is named next to its check.

---

## The finding this exists to prevent

The 2026-07-28 throughput audit set out to test *"the bottleneck is observation
supply"* and refuted it. The fund settles plenty — **432 P-001, 54 P-014, 38
P-017 and 5 P-015 settled positions in 28 days.**

> **The scarce resource is not observations and not hypotheses. It is
> correctly instrumented observations.**

Four of five gates were not measuring anything, each for a different reason:

| pod | 28d settled | gate read | why |
|---|---:|---|---|
| P-001 | 432 | 0 / 200 | CLV rows lag settlement; admissibility unmeasured |
| P-014 | 54 | unreadable | **no sanctioned reader existed at all** |
| P-015 | 5 | 0 / 120 | reader globbed `data/pods/P-015.jsonl`, **which has never existed** |
| P-017 | 38 | 1 / 8 | working — the control |
| P-022 | 0 | 0 / 14 | the pod could not compute its own placement window |

Three were fixed by hand. **Nothing prevented the fourth recurrence**, and
this repo has now produced the same class of defect five times:

> *"A filter asserted in a docstring and applied in one call path is not a
> filter."*

---

## The nine conditions

### 1. A sanctioned reader exists and the gate names it

The gate's `reader` (or a `source` resolving to `scripts/<source>.py`) must be
a real file, and it must be the **only** path to the gate number. A P&L chart,
a dashboard tile, an eyeballed jsonl tail, or a per-name breakdown is not a
verdict.

> **P-014, 2026-07-28.** Declared `metric: settled_trades`,
> `source: trade_log`, `threshold: 500` — and **no reader**. 345 settled rows
> sat in the log against a 500 threshold and nobody could state progress from
> a sanctioned number.

### 2. The reader points at the file the pod actually writes

Every path the reader declares must exist, and at least one must contain rows
for that pod.

> **P-015, 2026-07-28.** The reader globbed `data/pods/P-015.jsonl`. That file
> has never existed. It reported **0 against 5 real settlements** and looked
> exactly like a pod that had not traded.

*Host note:* this check needs the data. On a checkout with no `data/` it
returns **unknown**, not pass — and it must be run where the logs are.

### 3. The reader returns `None`, or an explained zero, when it cannot read

Never a stale YAML value, never a fabricated default. **Refusing loudly is
correct**; inventing a number is not. What this forbids is a number that came
from somewhere other than an observation.

> **P-015's `or 0.9`.** A price fallback that fabricated a breakeven for any
> row missing a price — i.e. fabricated the input to the statistic.
> **P-017's `progress: 1`**, typed by hand on the day it entered its first
> tournament, while 16 of that event's 38 positions were still open. A gate
> that counts entries rather than settlements is satisfiable without a single
> observation of the thing under test.

### 4. A pre-registered decision rule exists

Written **blind**, before n is decisive, and **named by the gate**. A
threshold with no kill line, no promotion condition and no hard-kill z is not
a gate.

> **P-013 lost $2,094** while its criteria were still being decided after the
> fact. A rule that exists but is joined to the gate only by filename
> convention is a weaker version of the same problem, and is reported
> separately from having no rule at all.

### 5. The reader emits progress AND threshold

A gate must be **readable**, not merely computable. If the manager cannot
quote a number and the number it is measured against, the gate does not exist
operationally.

### 6. The gate keys off the field the pod actually sets

> **The JDAY re-book, 2026-07-28.** The correction set `outcome`, while
> `p017_checkpoint` keys off **`action`**. The gate did not move and the fix
> looked applied. It was caught only because someone checked the gate instead
> of assuming.

Signature: rows for the pod exist, the reader counts zero, and it offers no
reason why. *(Partially mechanised — a reader that explains its zero passes.)*

### 7. Every consumer of the gate number is enumerated

Mechanised as: `manager/checks.py::_gate_progress` must be able to **resolve
the declared `source`**. A gate naming a source the manager maps to nothing is
unreadable by the manager whatever its reader says — P-014's `trade_log`, and
P-016's `maker_fills` today.

> This is the five-time failure. Before trusting a component, **enumerate
> every consumer and check the path that actually runs.**

### 8. A projection exists, or the gate is explicitly unprojectable

Never a made-up date. `manager/throughput.py` projects only `tier: validating`
gates with a reader, and renders everything else `None` — deliberately.
What this check rejects is a **hand-written forward date living beside a gate
that nothing derives it from**.

> **P-001's registry said "late Aug – early Sept 2026."** At the measured
> 14.3% admissibility rate the gate needed ~31 weeks — March 2027, past the
> end of the MLB season. The date was not derived from anything.

### 9. `blocked_on` uses the vocabulary, and the data must not contradict it

| value | means |
|---|---|
| `time` | a sample is accruing **at a known rate** toward a defined gate |
| `defect` | something is broken; calendar will not fix it |
| `measurement` | the accrual rate is **not yet measured**, so no date can be stated |
| `backtest` | code exists and is inert, waiting on its own validation study |
| `decision-rule` | observations are accruing but **no pre-registered rule exists** |
| `human` / `data` / `external` / `nothing` | as before |

The enforcer rejects a label it can contradict: `time` with no reader is
`defect`; `time` with no decision rule is `decision-rule`.

> `blocked_on: time` was, on 2026-07-28, honest for exactly two of five pods.
> It is the label that let a gate which **could not resolve** be reported as
> one patiently waiting.

---

## Running it

```bash
python3 -m scripts.check_gate_instrumentation            # human-readable
python3 -m scripts.check_gate_instrumentation --json
python3 -m scripts.check_gate_instrumentation --root /path/to/a/checkout
```

Exit 0 = every gate instrumented. Exit 1 = at least one is not.
**A read failure is a FAILURE, never a skip** — the same rule the fee fixture
follows. A checker that exits 0 because it could not run is indistinguishable
from a clean bill of health.

**Run it on the droplet.** Check 2 needs the trade logs, and a developer
checkout has none.

CI: `tests/test_gate_instrumentation.py` runs the enforcer's logic against
synthetic trees on every suite run, and carries a **ratchet** — the number of
failing checks against the real registry may go down but not up. Set
`GATE_INSTRUMENTATION_CHECK=1` to run the full enforcer against this checkout
as part of the suite.

## What this is not

It does not judge a gate's verdict, threshold, or decision rule. It asks only
whether the gate is **capable of measuring** the thing it claims to measure.
A gate can be fully instrumented and still be a bad gate.

**Known limitation, stated rather than discovered later:** it cannot detect
"the pod is structurally incapable of generating observations." That was
P-022's 2026-07-23 → 07-28 failure — perfect instrumentation, nothing to
instrument. The detector for that class is a live-behaviour watch like
`scripts/p022_window_check.py`, not a static checklist.
