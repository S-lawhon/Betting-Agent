# P-017A as a same-direction maker — **KILL**

**Date:** 2026-07-28 (07-30 queue slot) · **Status:** closed, question settled
**Pre-declaration:** `golf_research/P017_MAKER_PREDECLARATION.md`, committed at
`927555f` **before** any effect number was computed
**Harness:** `golf_research/backtest_p017_maker.py` ·
**Params:** `golf_research/p017_maker_params.json` ·
**Raw:** `golf_research/p017_maker_results.json`

---

## Verdict

> **Δ = −6.59¢** per posted market per intended contract, tournament-clustered
> CI **[−9.24, −4.03]**, 11 of 12 tournaments negative, leave-one-out
> **[−7.42, −5.75]¢**.
>
> **KILL — on both pre-registered triggers independently.** The contract fill
> fraction at the primary cell is **2.2%**, far below the 25% floor; and
> Δ ≤ −1.0¢ with a CI excluding zero.

**The 1.04¢ taker fee is worth paying.** Every one of the **18** cells in the
placement × window × fill-convention grid is negative with a CI excluding zero;
the best cell anywhere is **−5.13¢**. There is no corner of this design that
works, so the question is closed permanently rather than parked.

---

## 1. What was compared

One variable. Same 866 markets, same anchor candle, same binary settlement:

- **taker (Leg A)** — buy Q=25 YES at the anchor **ask**, pay the taker fee.
- **maker** — rest a bid for Q=25 at the anchor **bid**, fill only on public
  prints that come to it, pay **zero** fee. Unfilled contracts earn nothing.

Primary statistic, pre-registered: `Δ = Σ(net_maker − net_taker) / (Q · posted)`,
in cents per intended contract, bootstrapped **on the per-market difference**,
clustered by tournament — because Leg A's own CI spans 5.6¢ and cannot resolve
a ~1¢ effect by differencing two independent means.

**Universe validation.** Leg A's rule on Leg A's own data reproduces the
published **n = 1149** at the shipped 0.08–0.45 band exactly, taker
**+7.14¢** [+4.19, +9.88] against the params file's +7.14¢ [+4.30, +9.90]
(mean exact; CI differs only by bootstrap implementation). The universe
contains **zero `scalar` rows**, so settlement is binary $1/$0 and identical
across both arms — independently confirming the params-file note that scalar
does not touch this leg.

---

## 2. The result

Primary cell in **bold**. Δ is per posted market per intended contract.

| placement | cancel | posted | filled mkts | fill % | **φ (contracts)** | net ¢/ct *on fills* | taker ¢/ct | **Δ ¢** | 95% CI | E[sv\|post] | E[sv\|fill] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **bid** | **96h** | **866** | **30** | **3.5%** | **2.2%** | **+13.69** | **+6.90** | **−6.59** | **[−9.24, −4.03]** | **0.270** | **0.396** |
| bid | 48h | 866 | 333 | 38.5% | 28.2% | +3.16 | +6.90 | −6.00 | [−8.17, −3.94] | 0.270 | 0.269 |
| bid | 0h | 866 | 574 | 66.3% | 56.2% | −1.15 | +6.90 | −7.54 | [−9.41, −5.71] | 0.270 | 0.186 |
| mid−0.00 | 96h | 866 | 113 | 13.1% | 8.9% | +3.94 | +6.90 | −6.55 | [−9.31, −3.86] | 0.270 | 0.274 |
| mid−0.00 | 48h | 866 | 389 | 44.9% | 34.3% | +2.31 | +6.90 | −6.10 | [−8.51, −3.72] | 0.270 | 0.256 |
| mid−0.00 | 0h | 866 | 595 | 68.7% | 59.0% | −0.78 | +6.90 | −7.36 | [−9.35, −5.27] | 0.270 | 0.196 |
| mid−0.02 | 96h | 866 | 6 | 0.7% | 0.6% | −25.29 | +6.90 | −7.05 | [−9.57, −4.56] | 0.270 | 0.000 |
| mid−0.02 | 48h | 866 | 292 | 33.7% | 24.4% | +2.88 | +6.90 | −6.19 | [−8.23, −4.13] | 0.270 | 0.258 |
| mid−0.02 | 0h | 866 | 554 | 64.0% | 53.5% | −1.22 | +6.90 | −7.55 | [−9.33, −5.68] | 0.270 | 0.176 |
| mid−0.04 | 96h | 866 | 2 | 0.2% | 0.2% | −24.00 | +6.90 | −6.95 | [−9.47, −4.47] | 0.270 | 0.000 |
| mid−0.04 | 48h | 866 | 238 | 27.5% | 20.1% | +4.97 | +6.90 | −5.90 | [−8.00, −3.97] | 0.270 | 0.274 |
| mid−0.04 | 0h | 866 | 520 | 60.1% | 49.5% | −0.08 | +6.90 | −6.93 | [−8.79, −5.12] | 0.270 | 0.171 |

**Robustness — the friendlier at-or-through convention** (a print *at* our
price fills us, i.e. we assume queue priority). The KILL is not an artifact of
the pessimistic strictly-through rule:

| placement | cancel | fill % | φ | net ¢/ct on fills | **Δ ¢** | 95% CI |
|---|---|---|---|---|---|---|
| bid | 96h | 18.8% | 13.7% | +7.19 | −5.91 | [−8.46, −3.43] |
| bid | 48h | 48.3% | 37.8% | +3.14 | −5.71 | [−7.98, −3.48] |
| bid | 0h | 69.4% | 60.8% | +0.28 | −6.73 | [−8.55, −4.72] |
| mid−0.00 | 96h | 21.0% | 15.0% | +9.02 | −5.55 | [−8.14, −2.97] |
| mid−0.00 | 48h | 52.9% | 41.7% | +4.24 | −5.13 | [−7.31, −3.01] |
| mid−0.00 | 0h | 71.9% | 63.2% | +1.19 | −6.14 | [−7.91, −4.34] |

Per-tournament Δ at the primary cell — CHSC26 −4.17, COPC26 −7.13, GESO26
−6.17, ISC26 −13.33, JODC26 −4.51, PGC26 −7.86, RBBCAN26 −7.58, THCCBN26
−15.55, THMTPBW26 −1.54, THOC26 −9.55, **TRAV26 +4.84**, USO26 −6.25. The lone
positive is TRAV26 — the same signature-event week that is Leg A's lone
negative, which is consistency, not encouragement.

---

## 3. Why it fails — the price advantage is real, and it is unreachable

**The maker's price advantage is exactly as large as predicted.** If every
posted market filled at the resting bid, it would earn **+9.52¢/ct** against
the taker's +6.90¢ on the same markets — a **+2.62¢** advantage. Decomposed:
the taker fee at the mean admissible ask (0.1909) is **1.08¢**, and the mean
ask-to-bid distance is **1.54¢**. That is fee + spread, and it lands within
0.05¢ of the ceiling this study pre-registered (§7 of the pre-declaration:
"fee + spread ≈ 2.1¢"). **There is no look-ahead** — the predeclared alarm was
Δ > +2.2¢ and nothing approaches it.

**The fee saving is genuinely recoverable. The strategy that recovers it is
not.** Two failure modes, and they are mutually reinforcing:

1. **You cannot fill.** At the pre-registered primary window — post at the
   anchor, cancel at close − 96h, a median ~19.7h of resting time entirely
   inside Leg A's own 4–10 day window — only **2.2% of intended contracts**
   fill. Four to five days before a tournament, almost nobody is selling
   through the bid on a 19¢ top-20 name. The pre-declaration computed the
   break-even in advance: with a +2.62¢ per-filled gain against a forgone
   +7.14¢ on every unfilled contract, **Δ ≥ 0 needs φ ≳ 77%** and **Δ ≥ +1¢
   needs φ ≳ 88%**. The highest φ anywhere in the grid, under the friendliest
   convention, is **63.2%**.

2. **The only lever that raises φ destroys the edge.** Fills come from resting
   longer, and resting longer walks the quote into the tournament — where the
   informed side hits it. Holding placement at the bid and extending the
   window: E[sv | filled] goes **0.396 → 0.269 → 0.186** at cancel 96h → 48h →
   0h, against E[sv | posted] = 0.270 throughout. Net ¢/ct on fills collapses
   **+13.69 → +3.16 → −1.15**. You buy fills with adverse selection at
   roughly the rate that cancels them out.

### This is Leg B repeating, and it is named as such

The pre-declaration required this call. **Fills are systematically the losers**
once the quote rests long enough to fill at all: at cancel 0h the filled cohort
settles at 0.186 against 0.270 posted — a 31% relative haircut, and the maker's
own edge on its fills goes negative. This is the same mechanic Leg B hit from
the opposite side of the book, and the same one the make-cut study hit (0.361
vs 0.558). A resting quote on a market that trades through its own determining
period is a one-way option written to the informed side. **The direction of the
quote does not change that** — Leg B rested an offer, this rests a bid, and
both are picked off.

The **+13.69¢/ct on fills** in the primary row is precisely the seductive
number the brief warned about: it is 30 markets and **2.2% of the capital**, and
it sits alongside Δ = −6.59¢. Reporting per-contract-on-fills alone would have
inverted this verdict.

---

## 4. Declared checks

**Anchor contemporaneity** (house rule). Anchor days-to-close: min 4.04, p10
4.43, **median 4.82**, max 4.88 — the daily-candle granularity plus Leg A's
"latest such candle" rule pins the anchor just past 4 days, never 5+. Median
anchor spread 0.010 (p90 0.040, max 0.060). Both arms transact at the anchor
instant (`post_at_anchor=True`), so staleness is **shared** and cannot bias Δ.
No repeat of the make-cut study's 68h-stale artifact.

**Selection (pre-declaration §8).** The tick tape was pulled under the P-023c
screen (anchor at **48h** within 0.08–0.45), not Leg A's 4–10 day screen, so
866 of 1149 admissible markets have tape. The 283 without are not untraded
markets — they are chiefly names that had drifted out of the band by tournament
start. Measured: they settle at **0.304** vs 0.270 for the retained, mean ask
0.214 vs 0.191, taker **+7.88¢** [+1.48, +17.13] vs **+6.90¢** [+4.42, +9.41].
The intersection is therefore *mildly unfavourable* to the taker baseline — it
makes the comparison slightly **generous to the maker**, which strengthens the
KILL rather than threatening it. Pairing is within-market regardless, so Δ is
insulated from this.

**The taker's 100% fill is an assumption, not a measurement** — Leg A models no
fill risk. It is generous to the taker, so it is a genuine limitation of a KILL
verdict. It does not rescue the result: closing a 6.6¢ gap would require the
live taker to miss roughly half its intended size, and the φ < 25% trigger
fires independently of Δ.

**Fees** come from `src/kalshi_fees.fee_per_contract` with `series_ticker`
passed — 0.07·P·(1−P) taker, **0.0** maker for `KXPGATOP10`/`KXPGATOP20`
(`quadratic`, resolved through the generated fixture). Nothing hard-coded,
including the zero.

---

## 5. Harness provenance

`quirks_common.replay()` **already implemented `side="buy_yes"`** — resting a
YES bid, filling on `taker_side="no"` prints strictly through the quote at zero
maker fee. No inversion of the sell-side logic was needed and **no third
harness was written**. Two strictly additive parameters were added:

- `quote_px_by_ticker` — a per-market quote price, because "rest at the anchor
  **bid**" cannot be expressed as a scalar offset off the anchor mid;
- `collect_per_market` — per-market fill detail, required to pair each market's
  maker outcome against its taker outcome.

Both default to the previous behaviour. **Verified inert:**
`backtest_fade_fills.py --validate` produces byte-identical output before and
after (checked by stashing the change and re-running).

Leg A's anchor candle is handed to `replay` as the market's sole candle, so the
replay's internal anchor resolves to exactly the candle Leg A priced off.

### Incidental finding — the P-022 harness validation is failing in the repo today

`backtest_fade_fills.py --validate` **FAILS on `main`, and did so before this
study touched anything.** The cached universe has grown to **404 markets / 22
tournaments** against the published **364 / 19**, so every published cell now
mismatches (e.g. H12 off+0.02: +2.6¢ [+0.1,+4.5] now vs +3.4¢ [+1.7,+5.1]
published). This is cache growth, not a code regression — but
`backtest_topn_fade_fills.py` **aborts** on it (`"ABORT: harness does not
reproduce P-022. Trust nothing."`) and is therefore un-runnable without
`--skip-validate`. Unrelated to this study, which uses none of that universe.
Flagged for separate triage: the validation targets need re-baselining to the
current cache, or the published figures need restating at n=404.

---

## 6. What this does **not** authorise — and the gate-reset answer

The stop rule was honoured: **no pod change, no config change, no deploy.**
The verdict is a KILL, so nothing is proposed. For completeness, on the
question the brief required be answered from the locked document rather than
assumed:

> **Does changing execution mid-gate reset P-017's settled-tournament count?**

**The locked documents do not state a reset rule explicitly** — that is the
honest answer, and it should not be paraphrased into one. But they answer the
underlying question by construction, in two places:

1. `golf_research/P-017_Golf_Pod_Spec.md` already treats the maker variant as a
   **separate leg with its own gate criteria**: "P-017 validates if forward net
   CLV / realized net edge stays > half the backtest baseline; **P-017M**
   validates on fill quality (**≥30% of quoted names fill**) AND positive
   markout-implied net." A maker execution is P-017M's gate, not P-017's.
2. `RESEARCH_TESTING_BRIEF_2026-07-26.md` §4: "**Pre-register the forward gate**
   (n tournaments, metric, kill rule) *before the first fill* — the P-013
   lesson, **non-negotiable**." P-017's gate metric is denominated in *half the
   taker backtest baseline*; a maker leg has a different baseline, so the
   existing gate would no longer measure what it was registered against.

Read together: switching P-017A to maker execution would constitute a new leg
requiring its own pre-registered gate — i.e. **the count would restart**. That
reading is an inference from the two locked passages, not a quoted rule, and if
Sam wants it binding it should be written into the spec.

Worth recording anyway: at the primary cell this design fills **3.5% of quoted
names**, so it would fail **P-017M's own ≥30% fill-quality gate** by an order of
magnitude, independently of Δ.

---

## 7. Two corrections to the brief, both recorded before the run

1. **The origin report does not exist.** The brief cites
   `fee_parabola_research/REPORT_Fee_Parabola_2026-07-28.md`; there is no such
   file or directory in this repo, and no commit references one. Its figures
   (1.04¢/ct from the Leg A backtest, 1.20¢/ct from the tape, 13.3% of in-band
   volume filling a resting bid, +0.23¢ vs −1.41¢ by flow side) could not be
   verified here and are **not relied on** by this study. The 1.04¢ fee is
   independently confirmed: 1.08¢ at the mean admissible ask, computed from
   `src/kalshi_fees.py`.

2. **The brief's ceiling on Δ was too low.** It states the ceiling is +1.04¢
   (the fee) and flags >+1.5¢ as a look-ahead red flag. That holds only if the
   maker quotes at the taker's price; resting at the **bid** also captures the
   spread, making the per-filled-contract ceiling **fee + spread ≈ 2.1¢**. The
   alarm was raised to +2.2¢ in the pre-declaration, before any number was
   seen. This mattered: the measured price advantage is **+2.62¢/ct**, which
   under the brief's threshold would have been mis-read as evidence of a bug
   rather than as the correct and expected structural gain.

---

## 8. Companion task — still blocked

The live fee bill by pod (P-001 / P-015 trade near the fee peak on maker-
**charging** tennis series: VWAP 0.519, taker 1.156¢/ct, maker 0.289¢/ct) needs
the droplet trade log. The local log is from 2026-03-13. **Not attempted** — it
requires the deploy key and is outside this study's stop rule.
