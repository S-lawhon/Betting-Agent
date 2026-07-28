# PROMPT — P-016: give it a reader, or retire it properly

**Found by the new gate instrumentation standard on its first run. That is the
standard working.**

## The finding

P-016 has **no sanctioned reader** and an **unresolvable `source: maker_fills`**,
while being labelled **`blocked_on: nothing`**. That is P-014's exact defect —
the one that hid 331 gate-countable observations for months — on a pod whose
registry entry claims it is waiting for nothing at all.

Context that complicates it: **P-016 v1 was retired 2026-07-21** and
`betting-live-maker` is inactive. It also **relies on the general 0.0175 maker
rate** because it makes on `KXMLBGAME`, which does charge — `src/kalshi_fees.py`
documents that dependency explicitly and warns it must not be perturbed while a
gate sample is running. The live fee bill shows **691 maker fills, $20.58 at
0.3564¢/ct**, so something did trade.

## The question to answer first

**Is P-016 a live pod with a broken reader, or a retired pod with a stale
registry entry?** Everything downstream depends on which, and the honest answer
may be "retired, and the entry lied about it."

Establish it from evidence, not from documents:

- Is the unit running on the droplet? Is anything writing `maker_fills`?
- When did the last fill land, and does it postdate the 2026-07-21 retirement?
- Does `_PENDING_SERIES`/fee behaviour still matter to anything live?

## Then, one of two paths

### If it is retired
Retire it **properly**: registry entry to a terminal state with a reason and a
date, `blocked_on` set to something true, the gate marked closed rather than
pending, and the `kalshi_fees.py` warning updated if the dependency is now dead.
**Do not delete the trade history** — the 691 fills are evidence, and the
2026-07-25 harness loss is the precedent for why nothing gets deleted here.

### If it is live
Give it what P-014 got: a **sanctioned reader** that points at the file the pod
actually writes, returns `None` when it cannot read, emits progress and
threshold, and is round-trip tested. Then say plainly whether it has a
**pre-registered decision rule** — and if it does not, add it to the list of pods
that need one before their n matters.

## Either way

Re-run `scripts/check_gate_instrumentation.py` and confirm P-016 now passes or is
correctly excluded. **A pod that the standard cannot classify is a gap in the
standard**, not just in the pod — if you have to special-case P-016 to make the
check pass, the check needs the change, and say so.

## Stop rule

No trading behaviour changes. **Do not perturb the maker-fee path** while any
gate sample is running — if retiring P-016 would change fee treatment for
anything still live, stop and report.

## Deliverable

`research/REPORT_P016_Status_2026-07-28.md`: the live-or-retired evidence, the
path taken, the standard's before/after verdict, and whether the standard itself
needed amending.
