# P-017A same-direction maker — PRE-DECLARATION

**Written 2026-07-28, BEFORE any maker effect number was computed.**
Commit this file first. Any amendment must be made in the open, with its
reason, in the manner of the P-022 widening Amendment 1.

Origin brief: "P-017A as a same-direction maker" (07-30 queue). The brief cites
`fee_parabola_research/REPORT_Fee_Parabola_2026-07-28.md`; **that file does not
exist in this repo and was not available to this study** (see §9). The brief's
own quoted figures are used where needed and are attributed to the brief, not
to a source this study verified.

---

## 1. The question

P-017 Leg A lifts the ask at the pre-tournament anchor and pays a taker fee.
`KXPGATOP10` / `KXPGATOP20` are `quadratic`, so a maker pays zero.

> Does resting a YES **bid** at the anchor beat lifting the ask, once non-fill
> is paid for?

This is the same **direction** as Leg A (long YES). It is not Leg B, which
rested a YES *offer* (short YES, a fade).

## 2. Statistic

Let `Q = 25` contracts be the intended size per market, identical for both
arms. For each posted market *m*:

- **taker arm**: buys `Q` at the anchor ask `a_m`, pays the taker fee.
  `net_taker(m) = Q · (sv_m − a_m − fee_taker(a_m))`
- **maker arm**: rests a bid for `Q` at `q_m`; `f_m ≤ Q` contracts fill.
  `net_maker(m) = f_m · (sv_m − q_m)`; maker fee is zero. Unfilled contracts
  earn nothing — the forgone taker edge is the cost of making.

**Primary statistic — the paired difference, normalised per intended contract:**

```
Δ  =  Σ_m [ net_maker(m) − net_taker(m) ]  /  ( Q · |posted markets| )
```

Reported in cents. Dividing by `Q` puts Δ in the same units as Leg A's
+7.14¢/ct headline and as the fee, so the decision thresholds are directly
comparable. The un-normalised "net cents per posted market" is `Q · Δ` and is
also reported.

The taker arm fills all `Q` **by assumption**, not by measurement (§8). That
assumption is generous to the taker, so it makes an ADVANCE conservative and a
KILL less so; it is flagged in the report as a limitation of a KILL.

## 3. Comparison — paired, never two independent means

Leg A's CI is [+4.30, +9.90]¢, a 5.6¢ span around an effect of order 1¢. Two
unpaired means cannot resolve it.

Both arms are computed **on the same market, from the same anchor candle**.
The per-market difference `net_maker(m) − net_taker(m)` is the unit of
analysis. It is bootstrapped **clustered by tournament** (5000 resamples,
seed 12345, contract-weighted where a weight applies), matching house
methodology. **The sign and CI of Δ is the finding**; the two levels are
context.

## 4. Universe and anchor — identical to Leg A, one variable changed

- Series `KXPGATOP10`, `KXPGATOP20`.
- Anchor: latest two-sided daily candle with days-to-close ∈ [4.0, 10.0],
  bid > 0, ask < 1, ask ≥ bid — Leg A's `anchor_candle` / `two_sided` rule,
  from Leg A's own `golf_research/candles.jsonl`.
- Screens: anchor spread ≤ 0.06; anchor **ask** ∈ [0.08, 0.45] (the SHIPPED
  band, per `p017_params.json`).
- 12 tournaments.

**Verified before writing this file (structural counts only, no P&L):** this
rule yields **n = 1149** admissible markets, reproducing the `n=1149` recorded
in `p017_params.json` for the 0.08–0.45 band exactly. Result distribution is
320 `yes` / 829 `no`; **zero `scalar`**, so settlement is binary $1/$0 for
every market and is identical across both arms. This independently confirms
the params-file note that scalar does not touch this leg.

**The only variable changed is execution.** Same markets, same anchor candle,
same settlement.

## 5. Fill model — reuse, do not rebuild

`golf_quirks_research/quirks_common.replay()` **already implements
`side="buy_yes"`**: it rests a YES bid, fills only on `taker_side="no"` prints
(a seller hitting bids) whose price is **strictly through** the quote, caps
per-market fills at `quote_size`, and books `pnl = sv − quote_px` at zero maker
fee. No inversion of the sell-side logic is required and no third harness is
written.

One **additive** change is made to `replay()`: an optional
`quote_px_by_ticker` mapping that supplies a per-market quote price and skips
markets absent from it. Default `None` reproduces existing behaviour exactly.
It is needed because "rest at the anchor **bid**" is a per-market price that no
scalar offset off the anchor mid can express. **`backtest_fade_fills.validate()`
will be re-run and must still reproduce the published P-022 / P-023 cells**;
if it does not, the study aborts.

Leg A's anchor candle is handed to `replay` as the market's single candle, so
`post_at_anchor=True` posts the quote at the exact moment Leg A's anchor was
observed. Both arms therefore transact off the same observation.

- Tape: `golf_quirks_research/data/topn_full_trades/` (tick prints:
  `epoch`, `yes_price`, `count`, `taker_side`).
- `quote_size = 25.0` contracts — a research cap, not a sizing recommendation.
- Strictly-through fills are the headline; the friendlier at-or-through
  convention is reported as a secondary cell.

## 6. Quote placement grid — reported in full

Reported in full; no cell is selected after the fact.

- **`bid`** — rest at the anchor bid (the primary placement).
- **`mid − 0.00`, `mid − 0.02`, `mid − 0.04`** — the same offset grid the
  P-023c fade study used, off the anchor mid.

Resting window, pre-registered:

- **Primary: post at the anchor, cancel at close − 96h** — the far edge of Leg
  A's own 4–10 day window. Measured before writing this file: anchor
  days-to-close is 4.04–4.88d (median 4.82d), so the primary window is a median
  ~19.7h of resting time and the quote **never sits inside the tournament**.
  This keeps the experiment one-variable; resting past 96h becomes an in-play
  maker, which is Leg B's question.
- Secondary cells: cancel at close − 48h, and rest to close.

## 7. Break-even arithmetic — stated in advance

From published inputs only (Leg A +7.14¢/ct; median anchor spread measured at
1.0¢; taker fee ≈1.0–1.5¢ over the band), a filled maker contract gains the
full spread **plus** the fee relative to the taker — roughly **2.1¢** — while
every unfilled contract forgoes Leg A's whole +7.14¢. Ignoring adverse
selection, with `φ` = fraction of intended contracts filled:

```
Δ ≈ φ·(7.14 + 2.12) − 7.14
```

so **Δ ≥ 0 needs φ ≳ 77%**, and **Δ ≥ +1.0¢ needs φ ≳ 88%**. This is recorded
in advance so the result is read against a stated expectation.

**Correction to the brief's stated ceiling.** The brief asserts the
theoretical ceiling on Δ is +1.04¢ (the fee) and that "a result above +1.5¢ is
a red flag". That holds only if the maker quotes at the taker's price. Resting
at the **bid** also captures the bid–ask spread, so the correct per-filled-
contract ceiling is **fee + spread ≈ 2.1¢**, and the per-posted-market ceiling
is that times `φ` less the non-fill cost. The red-flag line is therefore raised
accordingly: **Δ > +2.2¢ is the look-ahead alarm**, not +1.5¢. Anything at or
above that is investigated for look-ahead in the fill logic before it is
reported as a result. This correction is made here, before any effect is
computed, precisely so it cannot be mistaken for rescuing a number.

## 8. Admissibility, and a known selection

The tick tape was pulled by `pull_trades.py` for the `topn_full` cohort under
its **own** screen — anchor at **48h** before close within 0.08–0.45 — not
under Leg A's 4–10 day screen. Consequences, measured before this file was
written:

- Of the 1149 Leg A-admissible markets, **866 (75.4%) have tape** and 283 do
  not. Every one of the 12 tournaments is represented in the intersection.
- A missing tape file therefore does **not** mean the market never traded. It
  chiefly means the name had drifted out of 0.08–0.45 by 48h before close.
  That is a **survivorship-flavoured selection**: a name priced 0.20 ten days
  out that collapsed to 0.03 by tournament start is preferentially absent.

Pre-registered handling:

- **The paired Δ is computed on the 866-market intersection.** Pairing is
  within-market, so the selection shifts both arms together and Δ is the
  quantity most protected from it.
- The taker baseline is reported on **both** 1149 and 866 so the selection is
  visible rather than assumed away.
- **Declared diagnostic:** the realised settlement rate of the 283 dropped
  markets is reported against the 866 retained. If they differ materially the
  levels are labelled selection-affected.
- Markets in the intersection with zero qualifying prints in the window count
  as **posted, zero fills** — they are not dropped.

## 9. Anchor contemporaneity

Reported per the house rule (the make-cut study's "48h anchor" was a median
68h-old price and produced a pure +9.5¢ artifact). Both arms transact at the
anchor observation instant, so any staleness is shared, but the distribution of
`anchor_epoch → close` and of `anchor_epoch → first qualifying print` is
reported.

## 10. Decision rule — fixed here

| Δ (maker − taker), per posted market, per intended contract, tournament-clustered | verdict |
|---|---|
| **≥ +1.0¢ with CI excluding zero** | **ADVANCE** — present to Sam as evidence for an execution change |
| CI includes zero | **NO DECISION** — record and stop |
| **≤ −1.0¢ with CI excluding zero** | **KILL** — the fee is worth paying; close the question |
| contract fill fraction `φ` **< 25%** | **KILL regardless of Δ** |

The brief's fill-rate floor is applied to **markets** (`filled/posted`) and,
additionally, to **contracts** (`φ`); both are reported and either falling
below 25% triggers the KILL.

`Δ > +2.2¢` → investigate for look-ahead before reporting (§7).

## 11. Mandatory reported quantities

posted markets · filled markets · fill rate (markets) · contract fill fraction
φ · net ¢/ct **on fills** (secondary) · **net ¢ per posted market per intended
contract (primary)** · Δ with clustered CI · the full placement × window grid ·
`E[sv | posted]` vs `E[sv | filled]` (the adverse-selection split) · leave-one-
tournament-out on Δ · the anchor-contemporaneity distributions · the 283-market
selection diagnostic.

If fills are systematically the losers, that is **Leg B repeating** and it is
named as such in the report, not buried in a per-contract mean.

## 12. Stop rule

**STOP at the report.** No pod change, no config change, no deploy. An ADVANCE
does not flip the live pod: the report must state, from the locked document,
whether changing execution mid-gate resets P-017's settled-tournament count.
That trade is Sam's decision, not the study's.

## 13. Deliverables

- this file, committed **before** any effect number
- `golf_research/REPORT_P017_Maker_2026-07-30.md`
- `golf_research/p017_maker_params.json`
- `golf_research/backtest_p017_maker.py`
- `bash scripts/check_research_committed.sh` clean
