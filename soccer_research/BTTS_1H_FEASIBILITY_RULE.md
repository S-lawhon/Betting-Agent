# First-half soccer BTTS — Phase 0 feasibility gate

**Written 2026-08-16 BEFORE `btts_1h_census.py` was run.** This is a gate on
whether the *instrument* exists, not on whether an edge exists. No outcome and
no settlement is read at this phase.

## 1. Where this came from, and what it is NOT

`research/REPORT_Paper_Review_InPlay_Soccer_AFT_2026-07-30.md` found two holes
in the one-line kill of soccer BTTS. The kill reads, in full: *"Soccer BTTS:
#3, sits at P = 0.483, the exact maximum of the fee parabola."*

1. **Clause #3 constrains a TAKER.** BTTS is maker-free on soccer series, so a
   maker is not paying the fee the kill is about. Confirmed against the
   generated fixture: **139 BTTS series, 0 charging maker fees.**
2. **First-half BTTS was never priced or tested.** The review measured
   full-match BTTS at mean mid 0.5166 (n=216) — reproducing the kill — and
   noted first-half BTTS sits at **mean mid 0.2588, median 0.2150**, a genuine
   tail price. That measurement was a by-product of a paper review, on a single
   snapshot, at the **seasonal trough** (2026-07-30, top-5 European leagues out
   of season).

**This phase does not test an edge and no hypothesis is being advanced yet.**
Naming a mechanism before knowing the instrument is tradeable is how this fund
produced P-023c and the satellites studies: three correct rulebook mechanics,
each worth less than the tick and the spread.

**The standing prior is hostile and is recorded here rather than discovered
later.** A tail price at 21–26¢ on a 1¢ tick needs only a 2–3¢ spread to be
10–14% wide. The satellites census died exactly there — *"the mechanic is real
and correctly documented, but the $0.01 minimum tick and the bid–ask spread are
each larger than the mechanic is worth."* Favourite-longshot bias is also
already dead in our universe (P-019), so "the tail is mispriced" is not an
available hypothesis without new evidence.

## 2. What is measured

Read-only, live books only. For every listed first-half BTTS market and its
full-match counterpart on the same fixture:

* best YES bid / ask and top-of-book size on both sides,
* mid and spread in cents,
* whether the market is genuinely two-sided (bid > 0, ask < 1, size both sides),
* the series' **live** `fee_type` from `/series`, not the fixture.

The live fee check is mandatory, not belt-and-braces: the same AFT review found
**18 soccer series missing from the generated fixture**, falling back to the
general maker rate and charging a phantom 0.44¢ on maker-free markets.

## 3. Feasibility gate — pre-registered

| condition | verdict |
|---|---|
| ≥ 30 first-half markets listed **and** ≥ 25% genuinely two-sided **and** median spread ≤ 4¢ | **PROCEED** to design a mechanism test |
| ≥ 30 markets listed but two-sided < 25% **or** median spread > 6¢ | **STOP — instrument too thin.** Record and close, as the satellites census did. |
| < 30 first-half markets listed | **NO DECISION — seasonal.** Re-run once the top-5 European leagues are fully in season. Not a verdict. |
| anything else | **NO DECISION**, stated with the reason |

**A PROCEED authorises writing a decision rule. It does not authorise a pod, a
quote, or a backtest**, and it is not evidence of edge.

## 4. Committed in advance

* **No mechanism will be reverse-engineered from this census.** If the
  instrument passes, the hypothesis is written and pre-registered separately,
  and it must be structural — the "our model beats the mid" archetype is
  **0 for 8** on this exchange and is not available here.
* **A single snapshot is not a liquidity measurement.** Any PROCEED is
  provisional on a second snapshot ≥ 1 h later, following the satellites
  persistence check (F5), which is what caught 112 of 118 apparent violations
  as stable artifacts of one- and five-lot resting orders.
* **Seasonality is disclosed, not corrected for.** 2026-08-16 is mid-resumption
  for the European leagues; a thin count is as likely to mean "not listed yet"
  as "not liquid".
