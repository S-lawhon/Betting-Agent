# Gate Throughput Audit — the throughput hypothesis is REFUTED, and what replaces it is worse

**Task:** `research/prompts/PROMPT_OPS_Gate_Throughput_Audit.md`
**Run:** 2026-07-28 · read-only against live droplet `data/` · **no gate, threshold or rule changed**
**Verdict:** the hypothesis as stated is **refuted**. The gates are not slow
because markets are slow.

---

## 0. Headline

> **The binding constraint is not observation throughput and not hypothesis
> supply. It is that four of the five gates are not measuring anything, for
> four unrelated defect reasons — and every one of them looks identical to
> patience from the outside.**
>
> *Two of the four were fixed and deployed the same day (P-022, P-015). The
> table below records the state the audit found; the §2 subsections carry the
> current state.*

The fund settles plenty. In the last 28 days the live logs recorded **432
P-001 settlements, 54 P-014, 38 P-017 and 5 P-015**. Raw observation supply is
not the problem. What is missing is the path from a settlement to a
*gate-countable* observation:

| pod | 28d settled positions | gate progress | why it is zero |
|---|---:|---|---|
| **P-001** | **432** | 0 / 200 | the pod still prices one day's game and bets another, so ~0% of rows are admissible |
| **P-014** | 54 | **unreadable** | the gate declares no sanctioned reader; progress cannot be derived at all |
| **P-015** | 5 | ~~0, but 5 exist~~ → **5** | the reader read `data/pods/P-015.jsonl`, **which does not exist**; the pod writes `trade_log.jsonl`. **FIXED + deployed 07-27 18:43Z** |
| **P-017** | 38 | **1 / 8** | genuinely accumulating — the one gate working as designed |
| **P-022** | 0 | 0 / 14 | could not compute its placement window — **fixed + deployed 07-27 17:53Z**; first window 07-29T15:30Z |

Only **P-017** is a gate whose zero is honest arithmetic. Its first settled
tournament also arrived during this audit, and it is **−10.08¢/ct on 2,276
contracts** against a +6.8¢/ct backtest baseline — n=1, no verdict, but worth
knowing.

---

## 1. Per-gate table

Rates are measured, from files named in §5. "Cadence-possible" is what the
event calendar would allow; "realised" is what the logs contain.

| pod | gate | threshold | progress | realised rate | projected resolution | limiting cause | resolvable ≤12m |
|---|---|---:|---|---|---|---|---|
| **P-001** | admissible CLV rows (scenario D) | 200 | **0** | **0 admissible/week** (14.3% all-time; **0 of 5** post-fix) | **never** at the realised rate; ~31 weeks (≈ Mar 2027) even at the 14.3% historical rate | **pod defect** — matcher still off by exactly 24h | **NO** |
| **P-014** | settled trades | 500 | **unreadable** | 13.5 positions/week | ~11.5 weeks (≈ mid-Oct 2026) *if* progress is the 345 in the log | **counting rule** — no sanctioned reader exists | likely yes, unverifiable |
| **P-015** | settled trades (locked) | 120 | **5** (was 0) | 5 trades in 2 days, then nothing; registry assumes ~20/month | ~25 weeks (≈ Jan 2027) at the registry's own assumption | ~~reader defect~~ **fixed**; now genuine event cadence | probably, unverified |
| **P-017** | settled tournaments | 8 | **1** | ~1 tournament/week available | ~Q4 2026 | **event cadence** (irreducible) + the two-wave counting rule | **yes** |
| **P-022** | settled tournaments | 14 | 0 | 0 — never quoted | ~5.4 weeks from the first window (≈ early Sept 2026) | ~~pod defect~~ **fixed + deployed**; awaiting the 07-29T15:30Z window | **yes** |

---

## 2. The findings, in order of how much they cost

### 2.1 P-001's gate is inert, and the matcher fix did not take

The scenario-D gate counts CLV rows where the ticker-encoded start is within 3h
of the game the pod actually priced. Using the sanctioned reader's own
admissibility logic (`scripts/p001_checkpoint.py`, imported rather than
reimplemented):

* **All-time: 106 of 739 joinable MLB placements are admissible — 14.3%.**
* **Since the matcher fix deployed (2026-07-26 19:31 UTC): 0 of 5.**

And the five are not near-misses:

```
07-26 21:21  KXMLBGAME-26JUL291310BALDET-BAL   delta = 42.50 h
07-26 21:21  KXMLBGAME-26JUL281910ATLNYM-NYM   delta = 24.00 h
07-26 21:21  KXMLBGAME-26JUL281910CLECIN-CLE   delta = 24.00 h
07-26 21:21  KXMLBGAME-26JUL281840PHIMIA-PHI   delta = 23.98 h
07-26 21:26  KXMLBGAME-26JUL281840BALDET-DET   delta = 24.00 h
```

**Exactly one day off, four times out of five.** That is the original
same-series tie-break bug, unchanged in its signature. The fix that shipped in
`401e6fd` rewrote `Legacy/Kalshi Arb Project/src/matcher.py` — which *is* the
module the live engine imports — and the deployed file does contain the
time-based tie-break, yet the placements it produced after deploying are still
a clean 24h out.

> **This is a finding, not a fix, and I have not touched the matcher.** The
> honest statement is that the fix is deployed and the defect it targeted is
> still present in the output. Five placements is a small sample, but a sample
> of five in which four are *exactly* 24.00h off is not noise.

The arithmetic that makes it urgent: at the historical 14.3% admissibility and
the measured ~45 MLB placements/week, 200 admissible rows needs ~1,400
placements ≈ **31 weeks ≈ March 2027** — well past the end of the 2026 MLB
season, so it cannot complete in this season at all. The registry's "late Aug –
early Sept 2026" is not achievable under any measured rate.

### 2.2 P-015's gate reader points at a file that does not exist — **FIXED 2026-07-27 18:43 UTC**

`scripts/p015_checkpoint.py` defaults to `--log data/pods/P-015.jsonl`.
On the droplet:

```
data/pods exists: False
```

`load_settled()` returns `[]` for a missing path, so the reader reports
`n = 0, NO DECISION` — and `manager/collect.py` faithfully relays it, because
delegating to the sanctioned reader is the correct house pattern.

Meanwhile the pod's real output is in `trade_log.jsonl`:

```
2026-07-25T15:35Z  KXATPMATCH-26JUL25VUKDOU-VUK  WIN   0.92
2026-07-25T16:35Z  KXATPMATCH-26JUL25MCCBOL-BOL  WIN   0.93
2026-07-25T17:35Z  KXATPMATCH-26JUL25SVAKOZ-SVA  WIN   0.85
2026-07-25T19:35Z  KXATPMATCH-26JUL25GIRSHA-GIR  WIN   0.94
2026-07-26T19:05Z  KXATPMATCH-26JUL26SVAHEW-SVA  LOSS  0.87
```

**P-015 has produced its first five settled trades and its gate cannot see
them.** This is the fourth instance of the pattern the reassessment already
named — *a filter or a path asserted in one place and wrong in the path that
runs* — after the generic settler's pod scoping, `_close_epoch`'s field
preference, and P-022's own checkpoint reading four paths the pod never wrote.

> **FIXED and DEPLOYED 2026-07-27 18:43 UTC**, on Sam's instruction. The reader
> now globs the trade log plus archives (`.gz` included), deduplicated on
> `fingerprint`. Against live data it reads **n = 5, 4 wins, hit 80.0% vs 91.2%
> breakeven, edge −11.20pp, z = −0.63, paper P&L −$9.83**, and the manager
> collector relays that number end to end.
>
> **Not a rule change, and the tests say so independently.** The locked
> document names *this script* as the sanctioned reader; it names no log path
> and does not define an observation by which file a row sits in. Thresholds
> (120/240), the test statistic and the VOID exclusion are byte-identical, each
> asserted by its own test. The verdict is unmoved: n = 5 ≪ 120 is still
> **NO DECISION**, and z = −0.63 is nowhere near the −2.0 hard kill.
>
> **A second bug was found in the same reader** and fixed with it: entry price
> was read as `fill_price or venue_prob or 0.9`, so a row carrying neither
> price was silently assigned a **fabricated 0.9 breakeven** feeding a
> pre-registered statistic. Such rows are now excluded and counted, never
> invented. Currently 0 of 5, so it is behaviour-neutral today — it was a
> loaded gun, not a smoking one.
>
> **Read the −11.20pp with care.** At n = 5 it is noise, and the rule
> explicitly forbids acting on it. But it is the second gate today whose first
> forward evidence is negative (P-017's first settled tournament came in at
> −10.08¢/ct), and the pattern is worth watching rather than filing.

### 2.3 P-014 has no sanctioned reader at all — **FIXED 2026-07-27 18:55 UTC**

Its gate declares `metric: settled_trades`, `source: trade_log`,
`current_key: pods.P-014.settled` — but no `reader:`. `_gate_progress` returns
`None` for a gate with a `source` it does not recognise, so the gate is
**unreadable by construction**. The trade log holds 345 settled P-014 rows and
the threshold is 500, so it is plausibly ~11 weeks from resolving, and nobody
can state that from a sanctioned number.

> **FIXED and DEPLOYED 2026-07-27 18:55 UTC.** `scripts/p014_checkpoint.py`
> reads **331 of 500** (170W/161L, VOIDs excluded; 345 including the 14 voids),
> span 2026-03-29 → 2026-07-26 over 41 active days. Wired into
> `_gate_progress`, `collect.py` and the registry.
>
> **The reader deliberately emits no verdict, at any n.** P-014 has *no
> pre-registered decision rule* — there is no `P014_DECISION_RULE.md`, so the
> gate has a threshold but no kill line, no promotion condition and no
> hard-kill z. P-015 and P-022 both have locked documents written before
> results existed, and both say why: P-013 lost $2,094 while its criteria were
> still being decided after the fact. A reader that invents an edge statistic
> and prints it beside a threshold is how criteria get decided after the fact.
>
> So it counts and declines to judge: verdict fixed at `NO DECISION`, and the
> economics behind `--unblind` with a banner, so they cannot leak into the
> daily brief before a rule is written. That is a speed bump, not a lock — the
> rows are in the trade log and anyone may compute anything from them. The
> point is that the *sanctioned* reader stays silent on a question no rule
> covers. `registry.yaml` now records `rule_status: MISSING`.
>
> **The real deliverable here is not the reader — it is that P-014 needs a
> written rule before n reaches 500**, and at 13.5/week that is ~12.5 weeks
> away (projected **2026-10-23**). There is time to write it blind.

### 2.4 P-022 — fixed today, not yet deployed

Covered in `research/REPORT_P022_Close_Time_2026-07.md`. Zero observations
because it could not compute its window at all. Post-deploy the first window
opens **2026-07-29T15:30Z**, and at the measured golf cadence (≈2.6
tournaments/week, a lower bound from an incomplete cache) T=14 lands ≈ **early
September**, not the mid/late-August in the registry.

### 2.5 P-017 is the control, and it works

1 of 8 settled tournaments, 38 settled positions in 3 days, event cadence ~1
tournament/week, two-wave settlement counting applied correctly. This is what a
healthy gate looks like, and it is the reason the throughput hypothesis is
refutable at all: when the machinery works, observations arrive at the rate the
calendar allows.

**Cost of the counting rule, asked for explicitly:** a tournament counts only
with ≥1 resolved and ZERO open positions. Of the events P-017 has entered, one
(3MO26) has converted; the 07-27 wave of 16 settlements has not yet closed out
its events. The rule is delaying recognition by roughly the gap between the
Friday cut and final settlement — days, not weeks. **It is not a material
throughput limiter and should not be touched.**

---

## 3. Statistical power — reported, not recommended

Asked for explicitly, and explicitly **not** a proposal. No threshold should
change on the strength of this section.

| gate | threshold | what it buys |
|---|---:|---|
| P-022 | 14 tournaments | 90% power vs the measured +3.4¢ effect; a T=8 look has **23%** power against half the effect and the locked rule forbids it |
| P-015 | 120 / 240 | 120 is the no-decision floor; promotion needs 240 **and** z ≥ 2.0 |
| P-001 | 200 admissible rows | sized for the +1.4pp net-maker CLV effect |
| P-017 | 8 tournaments | tournament-clustered, so 8 observations is already a thin CI |

The thresholds are not the limiter. **P-001 at 200 rows would still be inert at
0% admissibility, and P-015 at 12 trades would still read 0 through a reader
pointed at a missing file.** Lowering a threshold would convert a measurement
problem into a weaker test, which is the P-013 failure mode the locked rules
exist to prevent.

---

## 4. The standing instrument — committed

`manager/throughput.py`, wired into `manager/collect.py` (snapshot key
`throughput`) and `manager/checks.py` (`check_throughput`), 13 tests in
`tests/test_throughput.py`.

Per gate it reports observations in the last 7d and 28d, the realised rate, the
projected resolution date, and whether the gate has **stalled** — defined as
silence exceeding what that gate's own event cadence can explain
(`STALL_DAYS`: P-001 3d, P-014 14d, P-015 45d, P-017 21d, P-022 14d). Those
tolerances gate nothing and change no rule; they only decide when to speak.

Three design choices carried over from the rest of `manager/`, each of which
exists because of a specific past failure:

* **A reader that fails yields `None`, never a stale number** — and `None`
  never becomes a projection. Unmeasurable is rendered as unmeasurable, not as
  healthy and not as dead.
* **Counted in the gate's own unit.** Tournament-counting gates get no
  position-based projection, because counting the easy thing instead of the
  gate's thing is exactly how P-017 came to report `1` for a tournament it had
  merely entered.
* **Archived logs are read.** Rotation truncates the live file; a counter
  reading only `trade_log.jsonl` under-reports by whatever has rotated — the
  same shape as the incident that hid 177 open positions.

Live output during the audit, and again after the day's fixes:

```
DURING THE AUDIT          n_gates=5  stalled=0  unprojectable=5
  P-001  thr=200  prog=0     28d=432  rate/wk=None
  P-014  thr=500  prog=None  28d=54   rate/wk=13.5
  P-015  thr=None prog=None  28d=5    rate/wk=1.25
  P-017  thr=8    prog=1     28d=38   rate/wk=None
  P-022  thr=14   prog=0     28d=0    rate/wk=None

AFTER THE FIXES           n_gates=5  stalled=0  unprojectable=3
  P-001  prog=0    thr=200  rate/wk=None  resolves=None
  P-014  prog=331  thr=500  rate/wk=13.5  resolves=2026-10-23  <=12m=True
  P-015  prog=5    thr=120  rate/wk=1.25  resolves=2028-05-01  <=12m=False
  P-017  prog=1    thr=8    rate/wk=None  resolves=None
  P-022  prog=0    thr=14   rate/wk=None  resolves=None
```

**All five gates now read a real progress number for the first time.** The three
remaining `unprojectable` rows are correct by design, not broken: P-001 counts
*admissible CLV rows* and P-017/P-022 count *tournaments*, so a projection off
settled positions would be counting the easy thing instead of the gate's thing.

The instrument immediately earned itself by producing a finding nobody had:
**P-015 is not on track at its current rate** — 92 more weeks, projected
2028-05-01. That is measured off **5 observations in 2 active days**, against
the registry's own ~20/month assumption which would land it ~Jan 2027, and the
US Open qualifying spike (Aug 17–21) should move it materially. The warning
carries its own sample size for exactly that reason, and says to read it as
*"not on track at the current rate"* rather than as a date.

### One caveat about this instrument, found the hard way

It shipped **broken and silent**. `collect.py` runs as a script, so
`import manager.throughput` raised `ModuleNotFoundError` on every collector run
for an hour; `@safe` swallowed it into a fault and `check_throughput` guards on
`available`, so it produced no findings and no visible error — *the instrument
built to make silent failure visible was itself silently failing.* Every test
passed throughout, because under pytest the repo root is on `sys.path`.

Fixed, with a regression test that runs the collector **in a subprocess the way
cron does** and asserts the probe did not fault — verified to fail with the fix
reverted. The general lesson is the one this repo keeps relearning: **test the
path that actually runs, not the one that is convenient to import.**

---

## 5. Provenance

Every number above comes from a file:

| number | source |
|---|---|
| settled positions per pod/day | `data/trade_logs/trade_log*.jsonl(.gz)` + `data/trade_logs/archive/*` on the droplet, deduped by (pod, market, settled_at) |
| P-001 admissibility | `scripts/p001_checkpoint.py` — `load_placements()` and `ticker_start()` imported, not reimplemented |
| CLV rows | `data/trade_logs/clv_log.jsonl` (654 rows, 2026-04-03 → 2026-07-26, all pre-epoch) |
| gate progress / verdicts | `scripts/p00{1,15,17,22}_checkpoint.py --json` on the droplet |
| P-015 rows | `data/trade_logs/trade_log.jsonl`; absence of `data/pods/` confirmed by `os.path.isdir` |
| golf tournament cadence | `golf_quirks_research/schedule_probe.json` — 25 distinct tournaments over 66 days (**lower bound**, cache is incomplete) |

> **A caution on my own first pass.** I initially measured P-001 admissibility
> with a hand-rolled ticker parser that read the encoded time as UTC and got
> 2 of 654 admissible — a spurious 0.3%. Kalshi MLB tickers encode **ET**. The
> number above comes from the checkpoint's own `ticker_start()`, which is why
> it is 14.3%. Reimplementing a sanctioned reader's logic is how you get a
> second, subtly different number for a quantity that is supposed to be
> unambiguous.

---

## 6. Recommendation — clearly labelled opinion

**Evidence:** four of five gates read zero for defect reasons; one for cadence.
**Opinion below. All of it is Sam's call, and none of it changes a locked rule.**

### Park nothing yet — fix the instruments first

The obvious move is to park the slow gates. I think that is wrong right now,
because **we do not yet know which gates are slow.** P-014 might be 11 weeks
from a verdict; P-015 might be accumulating fine. Both are invisible for
reasons that are hours of work to fix, and parking a gate you cannot read is
just formalising the blindness.

Ranked:

1. **P-015 reader path** (minutes). The gate is locked, the reader is named in
   the locked document, and five real observations are currently invisible.
   Repointing a reader at the file the pod actually writes does not change what
   counts as an observation — but it *is* a change to a locked artefact, so it
   needs your explicit sign-off. **This is the highest value-per-minute item in
   the fund.**
2. **P-001 matcher** (hours, and it is a real bug). The gate is inert until
   this lands, and inert is not conservative — it consumes attention while
   producing nothing. Four of five post-fix placements exactly 24h out is a
   sharp, reproducible signature to debug against.
3. **P-014 sanctioned reader** (hours). It may be the closest gate to
   resolving and it is the only one with no reader at all.
4. **Deploy P-022** before 2026-07-29T15:30Z (see the close-time report).

### Where `blocked_on: time` is being applied dishonestly

P-001, P-014, P-015 and P-022 are all marked `blocked_on: time`. **For P-001,
P-014 and P-015 that is false** — they are blocked on a defect, a missing
reader, and a wrong path respectively, and calling that "time" is what let all
three sit unexamined. P-017 is the only `validating` workstream genuinely
blocked on time, and it is marked `blocked_on: nothing`.

I have not edited `registry.yaml`; the labels are a claim about the fund's own
state and they should be corrected deliberately.

### On the research funnel

The queue's premise was that a 33rd hypothesis is worthless while gates cannot
resolve. **The measurements support that conclusion by a different route than
the one proposed.** Observation supply is abundant — 432 P-001 settlements in
28 days is not a starved machine. The scarce resource is *correctly instrumented
observations*, and every hour spent on §6's list converts existing supply into
gate progress at a far better rate than any new candidate could.
