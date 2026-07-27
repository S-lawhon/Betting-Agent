# Fee Table Audit — the hand-maintained dict is gone, and the blast radius is zero

**Task:** `research/prompts/PROMPT_OPS_Fee_Table_Fixture.md`
**Run:** 2026-07-27/28 · **Verdict: FIXED — and a FIFTH drift found in the process**
**No deploy.**

---

## 0. Headline

`_SERIES_MAKER_FEE` is deleted. Fee classification now comes from a generated
fixture built from Kalshi's own `/series` (12,199 series, one call), matched
**exactly** on the series ticker rather than by longest prefix.

Two findings, and they point in opposite directions:

1. **The table had drifted a fifth time, and nobody had noticed** —
   `KXCHAMPTOUR`, `KXLIVTOUR` and `KXLPGATOUR` were all hand-marked as charging
   maker fees, and **none of the three does.** This drift runs the *opposite*
   way from the previous four: the table over-charged.
2. **No committed verdict's sign or gate outcome changes.** Not one. The
   damage was entirely latent — every study that could have been hurt had
   already worked around the code by hard-coding the fee it verified live.

That second point is the uncomfortable one. The reason five drifts caused no
measurable harm is that **every researcher who touched a maker study
distrusted the fee table and bypassed it**, each recording that they had done
so in their own report. The table was wrong for months and the mechanism that
protected the numbers was individual suspicion, not the code.

---

## 1. What was built

| piece | what it does |
|---|---|
| `scripts/generate_fee_fixture.py` | Pulls `GET /series` and writes `src/fixtures/kalshi_series_fees.json`. The **only** thing that writes the fixture. |
| `src/fixtures/kalshi_series_fees.json` | 12,199 series; 130 charging maker fees; 13 with `fee_multiplier: 0`. Committed. |
| `src/kalshi_fees.py` | Reads the fixture. Exact matching. `_SERIES_MAKER_FEE` deleted. |
| `scripts/check_fee_fixture.py` | Regenerates against live Kalshi and exits 1 on drift. |
| `tests/test_fee_fixture_current.py` | Offline structural + collision properties (always run) and the online drift check (opt-in). |

### Exact matching, and the one way it could still have failed

Prefix matching was never intuitive and produced four of the five drifts:
`KXPGATOP` does **not** cover `KXPGAR1TOP5` (their shared prefix is `KXPGA`,
which charges), and `KXMLB` < `KXMLBHR` < `KXMLBHRDERBY` <
`KXMLBHRDERBYR1LEAD` alternate charge / free / charge / free. Under exact
matching none of that can happen.

But exact matching has its own trap, and it was caught by the collision test
rather than by reasoning: **148 series tickers contain a hyphen** —
`KXMLBWINS-MIL`, `KXNFLWINS-SF`, `KXWO-GOLD`. The obvious implementation
(`ticker.split("-")[0]`) truncates those to a *different* series. Resolution
therefore tries the full string first and the leading segment second.

That fallback is only safe while the two agree. **86 hyphenated series have a
leading segment that is also a series, and zero of the 86 differ on fee type**
— asserted as a property against the whole live inventory
(`test_hyphenated_series_cannot_collide_with_their_leading_segment`), so if
Kalshi ever creates such a pair the suite fails instead of the fee silently
going wrong.

### What is left by hand, and how it expires

One thing: `_PENDING_SERIES`, currently `KXCHAMPTOURR2LEAD` and
`KXCHAMPTOURR3LEAD` — series that **do not exist yet** (the Champions Tour
lists R1 only) but which P-022 would quote the moment they list. Pre-registered
on the exhaustively verified `*LEAD` family pattern so a mid-season launch
cannot reintroduce a phantom fee.

`test_pending_series_expire_once_kalshi_lists_them` fails the moment either
appears in the fixture, forcing the entry to be **deleted** rather than left to
rot into a second hand-maintained table. That was the specific risk of keeping
any hand-written fee data at all.

### The drift check, demonstrated live

Run during this session, it reported `+1 new free series` and **did not
alarm** — which is the designed split. Kalshi lists maker-free series
constantly; alarming on those would train the alarm to be ignored, which is the
same failure as not having one. It alarms only on:

1. a series present in both snapshots changing its fee classification,
2. a **new** series that charges,
3. an unknown `fee_type`,
4. a `_PENDING_SERIES` entry becoming real.

**A network failure is exit 2 / test FAILURE, never a skip.** A check that
passes when it could not run is worse than no check, and a silently-skipped
check is the failure mode already in place.

---

## 2. The fifth drift

| series | hand table said | Kalshi says |
|---|---|---|
| `KXCHAMPTOUR` | charges | **free** (`quadratic`) |
| `KXLIVTOUR` | charges | **free** |
| `KXLPGATOUR` | charges | **free** |

A third copy of the same wrong data also existed in
`golf_research/backtest/golf_fees.py`, which kept its own local prefix tuple
carrying all three. That local table is now deleted and the module imports the
canonical lookup, so a backtest and the live engine can no longer disagree
about what a trade cost.

Also worth recording: the "conservative default = charge" rule means the old
prefix table over-charged **574 of 624** sports series it did not enumerate.
That is safe in direction but it is not a small number, and it is why the
fixture stores the *whole* inventory rather than just the charging list — so
the runtime can tell "known and maker-free" from "never seen, be careful".

---

## 3. Blast radius, audited backwards — **no verdict changes**

### The delta is bounded and one-directional

Every one of the five drifts made a maker-free series look like it charged, so
the phantom fee is `0.0175·P·(1−P)`:

| price | phantom fee |
|---:|---:|
| 0.05 | 0.083 ¢/ct |
| 0.08 | 0.129 ¢/ct |
| 0.12 | 0.185 ¢/ct |
| 0.20 | 0.280 ¢/ct |
| 0.50 | **0.438 ¢/ct** (maximum) |

**Direction matters more than magnitude.** A phantom fee makes net edge look
*worse* than reality. It can therefore turn a real PASS into a spurious KILL —
it can never turn a KILL into a spurious PASS. So the question to answer
backwards is only ever "did a kill sit within ~0.44¢ of its line, computed
through this code path?"

### Which numbers actually ran through the table

The audit is narrow because the consumption surface is narrow. `fee_per_contract`
only consults the series table on the **maker** path *and* when a
`series_ticker` is passed. Everything else is untouched: the taker fee
`0.07·P·(1−P)` has no series dependence and has never drifted.

| consumer | fee path | affected? |
|---|---|---|
| `crossvenue_research`, `satellites_research`, `golf_research/backtest`, `mlb_f5_research`, `golf_quirks_research/backtest_topn_fade.py` | `maker=False` (taker) | **no** — taker fee is series-independent |
| `golf_quirks_research/backtest_topn_fade_fills.py`, `backtest_makecut_fills.py`, `quirks_common.py` | hard-coded `"maker_fee": 0.0`, sourced to a live `/series` check | **no** — bypassed the table |
| `src/pods/live_maker_pod.py` (P-016), `src/maker_challenger.py`, `src/pods/kalshi_moneyline.py` (P-001) | `maker=True` with **no** `series_ticker` → general 0.0175 | **no** — never reaches the table |
| `src/golf_fade_maker.py` (P-017M) | `maker=True` **with** series ticker | **yes, in principle** |
| `src/round_leader_fade_maker.py` (P-022) | `maker=True` **with** series ticker | **yes, in principle** |

### And the two that could have been hurt booked nothing

Every settled row carrying a `maker_fee_per_contract` across the live droplet
logs and archives:

```
Counter({('P-016', fee>0): 557})     total fees booked: $14.96
```

**P-016 is the only pod that has ever booked a maker fee**, it passes no
`series_ticker`, and it makes on `KXMLBGAME` — which genuinely charges. Those
557 fees are correct. P-017M and P-022 have **zero** settled rows, so neither
ever realised the drift they were exposed to.

### The reports

Each maker study states its fee assumption explicitly, and each verified it
live rather than trusting the code:

* **P-022 Phase 2** — "maker fee 0 (quadratic series)".
* **P-023 make-cut Phase 2** — "Maker fee 0, **verified live** 2026-07-26
  (`fee_type=quadratic` for all four series), **not assumed**" — and §171 of
  that report explicitly flags `KXLIVTOP10` as missing from
  `_SERIES_MAKER_FEE`, i.e. it names the code bug while not using the code.
* **P-023c top-N fade** — "fee 0 on these `quadratic` series", and §342
  reports the fourth drift as a finding.
* **P-026 stat-leader** — "all `fee_type = quadratic` (maker fee 0)".

> **Finding: no committed verdict's sign or gate outcome would change.** The
> nearest thing to a scare is P-023c, whose +3.2¢ gross decomposed to **+0.2¢
> executable** — well inside the 0.44¢ phantom-fee band, and therefore a KILL
> that *would* have been sensitive to this error. It is safe only because that
> report computed the fee at 0 from a live `/series` check rather than from the
> code. Had it used `src/kalshi_fees.py`, its verdict would have been decided
> by a bug.

That is the actual lesson of the audit: the process survived on researchers
distrusting the code, and P-023c is the case that shows how thin that margin
was.

---

## 4. What is owed

Nothing blocking. Two recommendations, both Sam's call:

1. **Schedule the drift check.** It is the only thing here that catches Kalshi
   moving, and it currently runs only when invoked. It is read-only and cheap
   (one API call). I did **not** install it, because the run queue authorised a
   cron for the P-022 detector specifically and not for this. Suggested line:
   ```
   17 6 * * * cd /opt/betting-pod-shop && ./venv/bin/python -m scripts.check_fee_fixture >> /var/log/fee_fixture_check.log 2>&1
   ```
2. **`fee_multiplier` is not modelled.** 13 series carry `fee_multiplier: 0`
   (no fee at all, taker included) — `KXBTCY`, `KXTRUMPOUT`, `KXGREENLAND` and
   ten others. None is in any pod's universe, and all 12,186 others are
   multiplier 1, so this is recorded in the fixture and ignored by the runtime.
   If a pod ever trades one of those series, the taker fee will be overstated.

---

## Appendix — reproduce

```bash
python3 -m scripts.generate_fee_fixture --diff    # what would change
python3 -m scripts.check_fee_fixture              # exit 1 on drift
KALSHI_FEE_CHECK=1 python3 -m pytest tests/test_fee_fixture_current.py -q
python3 -m pytest tests/ -q                       # 1,580 pass, 1 skipped
```
