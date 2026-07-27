# P-028 — Golf Template Sweep

**Task:** `research/prompts/PROMPT_P028_Golf_Template_Sweep.md`
**Run:** 2026-07-28 · Phase 1 only · **no pod, no config, no service, no deploy, no orders**
**Verdict: KILL on both leads and on every other untested family.** Nothing
reached the ≥5¢ gross gate, so Phase 2 was not run.

---

## 0. Headline

> **Lead 2's mechanic does not exist.** The prompt's premise — that a golf H2H
> tie pays YES `$0`, so `fair(A) + fair(B) = 1 − P(tie) < $1` — is a **misquote
> of the governing PDF**. `GOLFH2H.pdf` says a tie pays **$0.50 to YES *and*
> $0.50 to NO**, and all 102 settled tie legs paid exactly that. The pair
> therefore sums to **$1.00 exactly**. Ties are common (9.4% of matchups) and
> completely symmetric: there is no tie mass to collect.
>
> **Lead 1's mechanic is real and cannot be measured.** `$1/N` is verified
> verbatim in `GOLFCATEGORYLEADER.pdf` and in settlement — but the entire
> settled history is **8 events from 2 tournaments, both majors**. At ~4 majors
> a year that is ~4 tournament-clusters per year; reaching P-022's T = 14 would
> take **≈3.5 years**.

Both kills are clean, and neither needed a backtest. The first was decided by
reading the contract; the second by counting events.

---

## 1. Lead 2 — `GOLFH2H` · **KILL: no mechanic**

### The rule, read from the PDF rather than from the prompt

`KXPGAH2H`, `KXLIVH2H`, `KXDPWTH2H` and `KXGOLFH2H` are all governed by
`https://assets.kalshi.com/contract_terms/GOLFH2H.pdf`. Verbatim:

> "**Tie (identical total stroke count):** If both `<golfer 1>` and `<golfer 2>`
> complete the same number of rounds and finish with identical total stroke
> counts, **"Yes" holders shall receive $0.50 per share and "No" holders shall
> receive $0.50 per share.**"

and its own worked example:

> "Both finish with a 72-hole total of 275. The market does not resolve Yes;
> instead **it resolves 50/50** because their total stroke counts are identical.
> **Both markets resolve to $0.50.**"

The prompt quoted this clause as *"'Yes' holders shall receive **$0**"*. That is
not what the document says. Everything downstream of it — "both sides lose on a
tie", "fair(A) + fair(B) = 1 − P(tie)", "the tie mass is free" — follows from
the misquote and does not survive it.

### Settlement agrees with the PDF exactly

| measure | value |
|---|---|
| settled markets | 1,082 |
| matchup events | 541 (exactly 2 markets each) |
| events resolving `(yes, no)` | 490 |
| events resolving `(scalar, scalar)` | **51** |
| distinct `settlement_value_dollars` on all 102 scalar legs | **`0.5000`, no exceptions** |

**Every tie resolved both legs at $0.50.** There is no discrepancy between the
published terms and the exchange's behaviour — I checked for one, and there
isn't one.

### The arithmetic that kills it

```
fair(A) + fair(B) = P(A wins)·1 + P(B wins)·1 + P(tie)·(0.50 + 0.50)
                  = P(A) + P(B) + P(tie)
                  = 1.00     exactly, for every matchup
```

**P(tie) = 51/541 = 9.4%** — genuinely large, and genuinely irrelevant. It is
paid out in full and split symmetrically, so it shifts value between the two
legs and creates none. Selling one leg is fairly priced; selling both is fairly
priced twice and pays two fees.

Fees make it strictly worse. Evenly-matched H2H sits at P ≈ 0.50, the exact
maximum of the fee parabola:

| price | taker fee `0.07·P·(1−P)` |
|---:|---:|
| 0.50 | **1.75 ¢/ct** |
| 0.20 | 1.12 ¢/ct |
| 0.05 | 0.33 ¢/ct |

A zero-mechanic trade at the fee maximum is a guaranteed loss of 1.75¢/ct taking,
or 0¢ resting (these series are `quadratic`, maker-free) with no edge to collect
either way.

### What is genuinely attractive here, and why it still doesn't help

`KXPGAH2H` has the **best event cadence of any golf family** — 541 events in 64
days, **59 events/week**, against 1.3/week for the round-leader series P-022
trades. If a mechanic existed here it would resolve a gate in weeks rather than
months. It doesn't.

**The one surviving possibility is explicitly out of scope:** the market could
systematically mis-estimate P(tie) — pricing `fair(A) = 0.82` when the truth is
0.85. That is a *forecasting* edge ("we have better information"), not a
structural one, and the standing prior on that class is **0 for 7**. It would
need its own anchor study and a reason to believe we can out-predict the book on
tie frequency. Nothing in this sweep suggests we can.

---

## 2. Lead 1 — `GOLFCATEGORYLEADER` · **KILL: real mechanic, unreachable sample**

### The rule verifies

From `GOLFCATEGORYLEADER.pdf`, verbatim:

> "…with **ties resolved on a $1/N basis** as described above."
> "If all members of `<golfer category>` miss the cut, the `<entity>` with the
> best `<score>` at the time of the cut wins the category…"

Both clauses are real. This *is* structurally the P-022 mechanic in a different
wrapper, exactly as the prompt argued.

### The data does not exist

Of the four series the prompt names, **three have never settled a single
market**:

| series | settled markets | events |
|---|---:|---:|
| `KXGOLFCAT` | **0** | 0 |
| `KXPGACAT` | **0** | 0 |
| `KXPGAWINNERWITHOUT` | **0** | 0 |
| `KXPGAPLAYERCAT` | 314 | **8** |

And the 8 events come from **two tournaments only**, both majors:

| event | N (category size) | ties |
|---|---:|---|
| `KXPGAPLAYERCAT-USO26APAC` | 15 | — |
| `KXPGAPLAYERCAT-USO26EUR` | 33 | — |
| `KXPGAPLAYERCAT-USO26LIV` | 13 | **2-way tie, $0.50 / $0.50** |
| `KXPGAPLAYERCAT-USO26USA` | 95 | — |
| `KXPGAPLAYERCAT-THOC26APAC` | 25 | — |
| `KXPGAPLAYERCAT-THOC26EUR` | 62 | — |
| `KXPGAPLAYERCAT-THOC26LIV` | 16 | — |
| `KXPGAPLAYERCAT-THOC26USA` | 55 | — |

The categories are geographic/tour groupings (USA, EUR, APAC, LIV), sized 13–95
— not the "five named golfers" the PDF's examples suggest, so the prompt's
expectation of a *small* field is wrong too.

### Why this is a kill and not an "await more data"

**One tie in eight events, and the house statistics rule is
tournament-clustered — so this is n = 2, not n = 8.** Two observations support
no CI worth writing down.

Worse, the cadence is structural: `KXPGAPLAYERCAT` has only ever listed at
**majors**. There are four majors a year.

* ~4 tournament-clusters per year.
* P-022's T = 14 threshold, applied here, lands in **≈3.5 years**.
* This fails clause #5 of the six-clause survivor profile — *recurring events* —
  which is precisely the clause the prompt claimed golf satisfies. It does for
  round-leaders (1.3 events/week); it does not for this family.

The all-miss-the-cut second mechanic is unmeasurable for the same reason: **zero
instances observed in 8 events.**

---

## 3. Every other untested family — **KILL at the census**

Settled data across the whole available window (Kalshi history reaches back to
~2026-05-22; these spans are the full window, not a truncated pull — the largest
family returned 1,660 rows against a 2,400-row cap):

| series | settled | events | ev/week | scalar % | verdict |
|---|---:|---:|---:|---:|---|
| `KXPGAROUNDSCORE` | 1,213 | 27 | 2.9 | **0.5%** | KILL — mechanic ~0.5¢, an order of magnitude under the 5¢ bar |
| `KXPGAROUNDLOW` | 68 | 10 | 2.3 | 0.0% | KILL — no tie mechanic observed |
| `KXPGALOWSCORE` | 21 | 3 | 0.7 | 0.0% | KILL — sample |
| `KXPGASTROKEMARGIN` | 14 | 2 | 0.5 | 0.0% | KILL — sample |
| `KXPGAPLAYOFF` | 12 | 12 | 1.3 | 0.0% | KILL — no mechanic |
| `KXPGAWINNINGSCORE` | 11 | 2 | 0.5 | 0.0% | KILL — sample |
| `KXGOLFNEXTWIN` | 9 | 1 | 0.1 | 0.0% | KILL — sample |
| `KXPGACUTLINE` | 9 | 1 | 7.0 | 0.0% | KILL — sample |
| `KXPGAWINNERREGION` | 8 | 2 | 0.5 | 0.0% | KILL — sample |
| `KXPGAUNDERPAR` | 6 | 1 | 7.0 | 0.0% | KILL — sample |
| `KXLIVH2H` | 4 | 2 | — | 0.0% | KILL — sample; and no mechanic (see §1) |
| `KXDPWTH2H` | 2 | 1 | — | 0.0% | KILL — sample; and no mechanic (see §1) |
| `KXPGA1STTIMEWIN` | 2 | 2 | 0.5 | 0.0% | KILL — sample |
| `KXPGAWINMARGIN`, `KXPGAROUNDBIRDIES`, `KXPGAGOLFERSCORE`, `KXPGABIRDIES`, `KXPGAREGION`, `KXPGARETURN`, `KXPGAAGECUT` | **0** | 0 | — | — | KILL — never settled a market |

`KXPGAROUNDSCORE` is the only one with real volume, and its 0.5% scalar rate
puts the mechanic near half a cent — below the taker fee at every price band,
before any spread. **Screened out without a study, which is what the friction
screen is for.**

### Controls

Counted the same way, the three already-tested families reproduce their known
shape, which is the check that the counting method is sound:

| control | settled | events | scalar % |
|---|---:|---:|---:|
| `KXPGAR1LEAD` (P-022 ✓) | 1,660 | 12 | 1.7% |
| `KXPGATOP10` (P-017 ✓) | 1,660 | 12 | 2.8% |
| `KXPGAMAKECUT` (P-023 ✗) | 1,588 | 11 | 2.9% |

---

## 4. Capacity, anchors and Phase 2 — not reached, and why

The prompt's method calls for anchor contemporaneity, top-of-book size and a
tournament-clustered bootstrap. **None was run, deliberately:** Phase 1's gate is
"STOP unless the mechanic clears ≥5¢ gross", and

* Lead 2's mechanic is **exactly 0¢** by construction — no measurement of
  anchors or depth can change an identity;
* Lead 1 has **two tournament-clusters**, so a clustered bootstrap would be
  arithmetic theatre;
* every other family is under the bar on the mechanic or has < 70 settled rows.

Spending API budget and a fill replay on any of these would have produced a
number, and the number would have meant nothing. Recording that as a decision
rather than an omission.

---

## 5. Verdicts

| family | mechanic | verified how | verdict |
|---|---|---|---|
| `GOLFH2H` (`KXPGAH2H` + 3) | **none** — tie pays 0.50/0.50, pair sums to $1.00 | PDF verbatim + 102/102 settled legs at $0.5000 | **KILL** |
| `GOLFCATEGORYLEADER` (`KXPGAPLAYERCAT` + 3) | real ($1/N) | PDF verbatim + 1 observed tie | **KILL** — 8 events, 2 tournament-clusters, majors only (~3.5 yrs to T=14) |
| `GOLFROUNDSCORE` / `LOWROUNDSCORE` | ~0.5¢ | 6 scalar in 1,213 | **KILL** — under the fee bar |
| margin, region, milestones, return, next-win, cut-line, under-par, playoff, 1st-time-win | none observed | census | **KILL** — no mechanic and/or ≤ 68 settled rows |

**No family advances to Phase 2.**

---

## 6. What this run is actually worth

The honest prior in the prompt was right: two of three golf templates working
did not make the fourth work. But the two kills are cheap and durable, and one
of them corrects a document error that would otherwise have been inherited by
the next study:

1. **`GOLFH2H` ties pay 0.50/0.50, not $0.** Any future hypothesis built on
   "golf H2H ties are asymmetric" is dead on arrival. This is now in
   `CLAUDE.md`.
2. **Verify a quoted rule against the PDF even when the quote is in the task
   brief.** The prompt was explicit that rules must be read verbatim from the
   source, and following that instruction is what killed its own lead in about
   ten minutes. Had I trusted the quote, the natural next step was a tick replay
   for a mechanic that does not exist.
3. **Cadence is a screening criterion, not a footnote.** `KXPGAPLAYERCAT`
   passes on mechanic and fails on being a majors-only listing. Counting
   distinct *tournaments* — not markets, not events — would have screened it in
   one query, and is the cheapest addition to the friction screen.

---

## Appendix — reproduce

```bash
python3 -m golf_quirks_research.p028_census        # settled census per family
```

Artifacts: `golf_quirks_research/p028_census.py`, `golf_quirks_research/p028_census.json`.

Contract terms read directly (not via the brief):
`https://assets.kalshi.com/contract_terms/GOLFH2H.pdf` ·
`https://assets.kalshi.com/contract_terms/GOLFCATEGORYLEADER.pdf`
