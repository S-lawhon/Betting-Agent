# P-018 core cherry-picked into the tree — 2026-07-29

**Verdict: DONE. Code in the tree, suite green, P-001 verified intact, pod
inert, backtest NOT run.**

P-018's data gate opens ~2026-07-30 with 27,307 genuine in-play ticks across 80
games already captured. Until tonight its implementation — 1,688 lines and 29
tests — existed only on `p018-inplay-fade-core`, absent from HEAD, the droplet
and the test suite.

---

## 1. The trap, verified before touching git

**The claim is TRUE, and the mechanism is more specific than "the branch tip
removes it".** `p018-inplay-fade-core`'s tip is four commits past `4ff5bea`:

```
0426e9c Archive pre-pivot root docs into docs/archive/
3fd3e10 Extract legacy Kalshi Arb Project to a separate archived repo   <-- this one
e2e5e1d Remove orphaned src modules with zero importers
35eac86 Clean up superseded duplicates and stale artifacts
4ff5bea P-018 core                                                       <-- what we want
```

`git show --stat 3fd3e10` deletes **41 files under `Legacy/Kalshi Arb
Project/`**, including `src/scanner.py`, `src/matcher.py` and `src/settler.py`.

**The import path P-001 depends on**, confirmed by reading it rather than
assuming it — `src/engine.py:34-40`:

```python
# The Scanner, Settler, KalshiClient, EdgeCalculator, etc. live under
# Legacy/Kalshi Arb Project/src/ and need to be on sys.path for bare
# ``from scanner import Scanner`` imports to resolve.
_LEGACY_SRC = str(Path(__file__).resolve().parent.parent / "Legacy" / "Kalshi Arb Project" / "src")
```

and `src/engine.py:152`, inside `build_shared_deps`:

```python
from scanner import Scanner        # existing system
```

So merging the branch would delete the module the running engine puts on
`sys.path` at import time and then imports by bare name, and `build_shared_deps`
would fall through to line 207 — `"legacy scanner not available; P-001
skipped"` — **silently disabling the fund's highest-volume pod with a log line,
not an error.**

**One correction to the framing, since a corrected finding beats a followed
instruction:** `git diff HEAD p018-inplay-fade-core` reports 343 files and
140,115 deletions, but most of that is *the branch being 6 days behind HEAD*,
not deletions it performs. The real deletions the branch would apply are those
in `3fd3e10`, `e2e5e1d`, `35eac86` and `0426e9c`. The Legacy removal is genuine
and is in `3fd3e10`. Cherry-picking `4ff5bea` alone avoids all four.

---

## 2. Files the cherry-pick touched

Seven files, +1,688 lines, all of them P-018's own except the config:

| file | status | lines |
|---|---|---:|
| `src/inplay_surprise.py` | added | 451 |
| `src/inplay_sport_adapter.py` | added | 284 |
| `tests/test_inplay_fade_maker.py` | added | 302 |
| `inplay_research/backtest_inplay_fade.py` | added | 571 |
| `inplay_research/REPORT_InPlay_Fade_2026-07.md` | added | 29 |
| `inplay_research/p018_params.json` | added | 19 |
| `config_multi_pod.yaml` | **CONFLICT — resolved** | +32 |

### The one conflict

`config_multi_pod.yaml` conflicted at the point where `4ff5bea` appends its
`P-018:` block, because HEAD now has the `P-022:` block in the same place —
added six days after the branch was cut. **Both sides were kept**: HEAD's
`P-022` block verbatim, then the picked `P-018` block. Nothing outside P-018's
own keys was taken from the branch side.

Verified after resolution:

```
pods declared : P-001 P-002 P-004 P-006 P-009 P-010 P-012 P-013 P-014
                P-015 P-016 P-017 P-017M P-018 P-022
pods.active   : ['P-001', 'P-014', 'P-015', 'P-017']      <- unchanged, no P-018
P-018.enabled : False
P-022 band    : [0.03, 0.12]        caps 0.005 / 0.05 / 0.15, max_ct 25
```

**P-022's gate-condition block survived the conflict byte-identical.** That was
the specific risk of resolving a conflict in this file tonight.

---

## 3. Suite delta

| | tests | skipped |
|---|---:|---:|
| before (local, this session) | 1,665 | 1 |
| after | **1,694** | 1 |
| delta | **+29** | 0 |

**+29 is exactly `tests/test_inplay_fade_maker.py`.** No existing test changed
behaviour, none was added or removed elsewhere, and the single skip is the
pre-existing one.

> The prompt's baseline of 1,593 is the droplet's count as of the 2026-07-27
> deploy. It has moved to 1,665 since, from work committed earlier tonight
> (Task 1's 25 detector/manager tests) and from the fee-fixture and throughput
> work of 2026-07-28. The +29 delta is what this task is answerable for.

---

## 4. P-001 asserted explicitly, not inferred from the suite

Run against the working tree after the cherry-pick:

```
A. legacy Scanner + Matcher import through engine's sys.path shim   OK
B. src.engine.build_shared_deps(cfg) -> deps["scanner"] is a Scanner  OK
C. P-001 constructed via the engine's own dependency:
     KalshiMoneylinePod, venue "kalshi"                              OK
   (the scanner even loaded its live state: scan_seen count=247)
D. registered pod ids: P-001 P-002 P-004 P-006 P-009 P-010
                       P-012 P-013 P-014 P-015 P-016 P-017
```

`Legacy/Kalshi Arb Project/src` is present on disk and on `sys.path`;
`matcher` and `settler` import by bare name. (`fair_value` does not exist under
that name — it is not one of the engine's imports and nothing depends on it.)

This is asserted directly because the suite would **not** have caught the
failure: `tests/test_matcher_wrong_day.py` carries an explicit
`skipif(..., reason="Legacy Kalshi Arb Project sources not present")`, so
deleting Legacy turns those tests green-by-skip rather than red.

---

## 5. Auto-discovery — P-018 cannot be picked up

`src/pod_registry.py:67-76` discovers pods by `pkgutil.iter_modules` over
**`src/pods/`** and importing each module so `@register_pod` decorators fire.

**Four independent reasons P-018 is inert:**

1. Its modules are `src/inplay_surprise.py` and `src/inplay_sport_adapter.py` —
   in `src/`, **not** `src/pods/`. Discovery never imports them.
2. Neither file contains `@register_pod`. The only mention of `BasePod` is a
   comment saying P-018 deliberately is not one.
3. `config_multi_pod.yaml` → `pods.P-018.enabled: false`.
4. `pods.active` is `['P-001', 'P-014', 'P-015', 'P-017']` — no P-018.

Measured, not asserted: after `discover_pods()` the registry contains
**P-001, P-002, P-004, P-006, P-009, P-010, P-012, P-013, P-014, P-015, P-016,
P-017 — and no P-018.**

No config change was needed to disable it; the block shipped inert in `4ff5bea`
and stayed that way. There is also no engine, no runner and no systemd unit —
`4ff5bea`'s own message says the live plumbing was deliberately not built.

---

## 6. Registry entry

Added to `manager/registry.yaml` as `stage: build`, `tier: build`,
**`blocked_on: backtest`**, `owner_dir: inplay_research`, with **no `gate`
block**.

`blocked_on: backtest` is a new value; the header enum was extended to define
it:

> `backtest` → Code exists and is INERT; waiting on its own validation study,
> not on calendar. Distinct from `time` on purpose: `time` means a sample is
> accruing toward a defined gate, and labelling a pre-backtest pod `time`
> implies a decision rule that does not exist.

It matches no branch in `checks.py::check_workstreams` or `brief.py`, so P-018
appears in neither the action list nor the accumulating list — which is the
intended behaviour, not an oversight.

**Verified that the throughput instrument does not project it.**
`manager/throughput.py:156-159` skips any workstream that is not
`tier: validating` or has no `gate` block; P-018 is both. Run live:

```
gates throughput will project: ['P-001', 'P-014', 'P-015', 'P-017', 'P-022']
P-018 present? False
```

---

## 7. The coverage caveat, stated in full and on the record now

Written into `src/inplay_surprise.py`'s own module docstring — the pod's file,
not a report — on the night the code landed and **before any backtest number
exists**, so that it is already on the record when it becomes inconvenient.
Reproduced here in full:

> **Measured 2026-07-28 from the 581 DISCOVERY records in the book-capture
> daemon's own log: 33.9% of discovered in-play markets are DROPPED from
> capture, and the drop is LOWEST-VOLUME-FIRST.** The replay sample is
> therefore skewed toward liquid markets, and any backtest built on it
> **overstates what is achievable across the full book** — the thin markets
> that go uncaptured are exactly the ones where a resting maker quote is
> hardest to fill and most exposed to adverse selection.
>
> **The rate cannot be raised.** 128 cumulative 429s, 48 in the last 24 h, with
> the daemon self-throttling to 0.6–1.2 req/s. The limiter is the exchange, not
> the config, so this is not a bug to fix before the backtest — it is a
> permanent property of the sample.
>
> Any report produced from this module's replay must state that bias **in its
> own words, in the result section, not in a footnote.**

The same text is in the registry entry's `notes`, so it reaches the daily brief
and does not depend on anyone opening the source file.

---

## 8. Stop rule observed

* **The backtest was NOT run.**
* **P-018 was NOT deployed** — no `deploy.sh`, no droplet sync, no service.
* The pod is inert on four independent counts (§5).

## 9. What Sam needs to decide

1. **P-018 has no pre-registered decision rule** — the same hole Task 5 exists
   to close for P-014. Its data gate opens ~2026-07-30 and
   `research/SPEC_P018_InPlay_Fade_Maker.md` §8 gate #1 ("the surprise-bucket
   table must not be flat") is a direction, not a rule: no threshold, no kill
   line, no clustering unit, no minimum n. **Write it before the backtest runs,
   not after** — running the study first and writing the rule second is
   exactly the P-013 failure mode, and this is the last week it can be done
   blind.
2. **Whether P-018 ships to the droplet at all before that rule exists.** My
   read: no. There is nothing for it to do there — no engine, no runner — and
   shipping inert code to production buys nothing while adding a file that
   looks like a pod.
