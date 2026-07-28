# P-022 — the one-sided-book question, and POI26's close reference

**Run:** 2026-07-28, Task 2 of `RUN_QUEUE_2026-07-28-day.md`.
**Investigation only. The pod's pricing behaviour is unchanged.**

---

## 🛑 STOP-THE-LINE — the answer is the bad one, but it is narrower than feared

> **The validated edge was never once measured off a one-sided ask. The pod
> will price every quote tomorrow off exactly that.**
>
> `quirks_common.candle_price` — the function that produces every anchor in
> the Phase-2 backtest — **rejects a one-sided book outright**, and says why
> in its own docstring: *"illiquid golf props park phantom asks … and using
> them fabricates edge."* An anchor is either an **executed trade price** or
> the mid of a **tight two-sided quote**. There is no third route.
>
> Live, on AIGWO26 R1 today: **143 of the event's 146 markets carry no
> resting YES bid at any price** (`n_yes_bid_levels = 0`), and **23 of the 24
> in-band candidates** are priced off the bare ask.
>
> **This is a genuine out-of-sample claim, and Sam should decide before
> `2026-07-29T18:32Z` whether to quote.** It is not, however, a demonstration
> that the edge is absent — see §B, where the discriminating split is weak
> in the predicted direction with a CI that spans zero.

**Two things that make it less bad than the headline:**

1. **One-sided books are not absent from the backtest — they are half of it.**
   181 of 364 posted markets (50%) had a one-sided-ask book at the anchor
   candle. The harness used those markets; it just took their price from a
   *trade print*, never from the ask. So the *population* overlaps heavily.
   What differs is the *reference price*.
2. **The edge in the overlapping class is positive**: markets whose anchor
   came from a trade on a one-sided-ask book returned **+3.90 ¢/ct
   [+0.33, +7.19]**, statistically indistinguishable from the two-sided
   +4.47 ¢ [+1.69, +7.27].

**The class that is genuinely untested is narrower and it is the live one:**
a book with *no trade in the bar* and *no bid*, priced off the ask alone.

---

## A. Classifying the backtest's own references

Method: the harness's own `anchor_at()` is asked where its anchor is, and
**that candle** is then classified. No anchor logic is reimplemented — the
P-001 lesson. `backtest_fade_fills.py --validate` was run first and
reproduces all seven published cells exactly (`HARNESS VALIDATION: PASS`),
so the classification sits on a harness known to be the published one.

Universe: the pinned 364. Cell: **H = 12, offset +0.02**, the headline —
**364 posted / 171 filled / 4,061 contracts / +3.41 ¢/ct [+1.65, +5.13]**,
**0 markets skipped for want of a clean anchor**.

### A1 — which route produced the anchor the harness actually used

| anchor route | posted | share |
|---|---:|---:|
| **`traded_one_sided_ask`** — trade print; book one-sided | **181** | **49.7%** |
| `traded_two_sided` — trade print; book two-sided | 145 | 39.8% |
| `two_sided_tight` — no trade; mid of a tight two-sided quote | 38 | 10.4% |
| *one-sided ask used as a price* | **0** | **0.0%** |

**The last row is the finding.** It is zero by construction, not by accident.

### A2 — what the book looked like at T = close − 12 h (the live-comparable view)

`anchor_at` returns the latest *clean* candle at or before T, which may
predate T. This row asks a different question: what was on the screen at T,
usable or not?

| book at T | markets | share |
|---|---:|---:|
| `traded_two_sided` | 135 | 37.1% |
| **`one_sided_ask`** (no trade, no bid) | **117** | **32.1%** |
| `traded_one_sided_ask` | 77 | 21.2% |
| `two_sided_tight` | 23 | 6.3% |
| `two_sided_wide` (rejected by `max_spread`) | 12 | 3.3% |

**32.1% of the backtest's markets showed the live picture at T** — and for
every one of those the harness *could not price it*, so it walked back to an
earlier clean candle. That is where the staleness lives: **median lag 0.98 h,
p90 40.1 h, max 249.2 h (10.4 days).**

**This is a second, independent out-of-sample gap.** On that class the
backtest quotes `(a trade price from up to ten days earlier) + 2¢`; the pod
quotes `(today's best ask) + 2¢`. Neither is the other.

## B. The edge split by book type, tournament-clustered

### B1 — by anchor route

| anchor route | posted | filled | contracts | net ¢/ct | 95% CI |
|---|---:|---:|---:|---:|---|
| `traded_two_sided` | 145 | 94 (65%) | 2,258 | **+4.47** | [+1.69, +7.27] |
| `traded_one_sided_ask` | 181 | 54 (30%) | 1,262 | **+3.90** | [+0.33, +7.19] |
| `two_sided_tight` | 38 | 23 (61%) | 541 | **−2.21** | [−16.16, +7.21] |
| **all** | **364** | **171 (47%)** | **4,061** | **+3.41** | [+1.65, +5.13] |

### B2 — by book-at-T class (the live-comparable split)

| book at T | posted | filled | contracts | net ¢/ct | 95% CI |
|---|---:|---:|---:|---:|---|
| `traded_one_sided_ask` | 77 | 36 (47%) | 821 | **+6.79** | [+6.13, +7.51] |
| `traded_two_sided` | 135 | 91 (67%) | 2,183 | **+4.39** | [+1.51, +7.30] |
| `two_sided_tight` | 23 | 18 (78%) | 436 | +1.21 | [−15.69, +7.74] |
| **`one_sided_ask`** ← **the live class** | **117** | **18 (15%)** | **441** | **−1.48** | **[−11.93, +8.15]** |
| `two_sided_wide` | 12 | 8 (67%) | 180 | −6.67 | [−9.17, +5.84] |

**Read this carefully, in both directions.**

* **The live class is the weakest cell in the table** — the only sizeable one
  with a negative point estimate, and by far the lowest fill rate (**15%**
  against 67% for two-sided). It contributes **441 of 4,061 contracts
  (10.9%)**; the pooled +3.41 ¢ is carried by the traded classes.
* **It is not a refutation.** The CI is [−11.93, +8.15] on 18 filled markets
  across a handful of tournaments. This is an *absence of evidence*, which is
  what you should expect from a class the harness was built to exclude.
* **The fill rate is the number I would weight most.** 15% is the shape
  P-017A died on last night — *a real edge you cannot fill is not an edge* —
  and it is measured here on the exact population the pod is about to trade.

**Verdict on the question the prompt says decides everything:** the edge does
**not** demonstrably live in the live class. It lives in the traded classes,
which together are 89% of the measured contracts. The reassuring answer — the
classes agree — is not what the data says.

## C. What `_mid()` actually does on a one-sided book

```python
b, a = ob.get("yes_bid"), ob.get("yes_ask")
if a is not None and 0 < a < 1 and (b is None or b <= 0):
    return a                       # ← the reference IS the ask
```

**There is no `None`-to-zero coercion anywhere in the path.** `yes_bid` is
`None` when the YES side of the book has no levels (`KalshiPublic.orderbook`
returns `_best([])` → `None`), and `_mid` tests `b is None` explicitly. A
coercion would have produced `(0 + ask)/2 = ask/2` — half the reference and a
2¢-lower quote. Pinned by
`test_one_sided_reference_is_the_ask_not_a_none_coerced_mid`.

**The arithmetic, on the live books:**

| | two-sided | one-sided |
|---|---|---|
| reference | `(bid + ask)/2` | **`ask`** |
| quote | `round(ref + 0.02, 2)` | `round(ask + 0.02, 2)` |
| e.g. | bid .04 / ask .06 → ref .05 → **sell YES at .07** | ask .05, no bid → ref .05 → **sell YES at .07** |

So the reported "sell YES at 7¢ against a 5¢ reference" is `0.05 + 0.02`,
where 0.05 is the **best ask**, not a mid. **The semantics are intended, not
accidental** — the docstring says so and always has: *"Accept a one-sided ask
as the reference when there is no bid, since that is the price retail lifts."*

But the two branches are not the same estimator. On a two-sided book the
reference sits *between* bid and ask; on a one-sided book it sits *at the
offer*, which is an upper bound on any notion of fair value. The one-sided
branch therefore quotes **half a spread higher** than the two-sided branch
would on the same underlying value — safer on price, worse on queue position.

Note also `yes_ask = 1 − best_no_bid`: an ask exists because someone is
**bidding NO**, and there is no YES bid at all. Our quote is a NO bid at
`1 − 0.07 = 0.93` behind someone else's at `0.95`. We fill only when an
aggressive YES buyer sweeps through.

## D. Depth — recorded, not fixed

**The pod has no size or depth screen.** Confirmed by reading the code, not
inferred: `_cycle_book` screens window → band → caps → price sanity, and
nothing reads `ask_qty`, `bid_qty` or any level count.

The quantity that matters for a resting YES ask is not `ask_qty` but **the
total size resting at prices strictly better than our quote** — a taker
lifting YES consumes the cheapest asks first, so all of it must clear before
we fill. Measured live on the 24 in-band AIGWO26 candidates:

| | value |
|---|---|
| size resting ahead of the quote — **min** | **12** |
| — median | **13** |
| — max | **1,122** |
| markets with `n_yes_bid_levels == 0` | **23 of 24** (event-wide: **143 of 146**) |

The population is **bimodal**: 13 names sit behind ~12–13 contracts (a 1-lot
top-of-book plus a thin ladder), and 9 sit behind **800–1,122** contracts.
For a 5-contract quote:

* **the thin ones are reachable** — a 15-contract sweep fills us;
* **the 800-lot ones need a ~805-contract sweep** on a name priced at 6–10¢
  in a 144-player field. That is not a fill, it is a lottery.

**A depth screen would materially change the quoted population**, and the
direction is not obviously the good one — the deep-ahead names are the ones
with real market-maker presence. Adding one is an §8.1 change and is **not
done here.**

### The pre-flight's "junk book" characterisation does not replicate

The 07-29 pre-flight described the 131 above-band names as junk
(`bid 0.01 / ask 0.97`, mid 0.49) that a depth screen would also have caught.
Today, of the 122 out-of-band AIGWO26 markets:

* **120 are one-sided asks; only 2 carry any bid at all;**
* **zero** match the junk pattern (`bid ≤ 0.02 and ask ≥ 0.90`);
* the asks cluster tightly — 0.42 (31 names), 0.49 (60), 0.43 (13) — spanning
  0.13 to 0.51, **not** near 0.97.

They are excluded by the **band on price**, which is doing the work correctly.
A depth screen would not have been what caught them. The pre-flight's
description was accurate for the book as it stood on 07-28 02:30Z and is not
accurate now — these books re-formed when tee times published.

## E. POI26's close reference — **excluded, pre-registered**

Searched again, three ways. **No R1 tee time or schedule of play exists.**

* **ESPN**: `/champions-tour/scoreboard?dates=20260731` → event `401832063`,
  `STATUS_SCHEDULED`, **0 competitors**. `tee_times` and `r1_tee_anchor`
  cannot run; the resolver falls to `tour_day_offset`.
* **The tournament's own site**: dates and field only — "31st July to 2nd
  August, 2026", 78 players. No times of play.
* **Press/tour**: field, purse, venue, history. No first tee, no broadcast
  window.

**Every other listed event has upgraded and POI26 has not.** Live
`close_source` across all 958 open books: `tee_times` 586, `r1_tee_anchor`
293, `tour_day_offset` **79** — and the 79 are exactly POI26's markets. The
mitigation the pre-flight was counting on fired for the two events that did
not need it.

**Pre-registered, before the window opens at `2026-07-30T16:00Z`:**
`golf_quirks_research/P022_POI26_PREREGISTRATION_2026-07-28.md`.

> POI26 is **excluded from T** — no observation, favourable or unfavourable —
> unless its QUOTE rows carry a `close_source` other than `tour_day_offset`.

**No code change.** The pod is *not* made to skip POI26: letting it quote and
discarding it from the **gate** still yields a measured `lag_h` for
`KXCHAMPTOUR` in a European frame, taking that constant from n = 1 to n = 2.
The exclusion is from the gate, not from the data.

## F. What shipped — observability only

Per the stop rule. No change to `_mid()`, no depth screen, no band change;
every parameter byte-identical (T1 §7 verifies this from the running process).

* **Every `QUOTE` row now carries the book it was priced off**: `book_side`
  (`two_sided` / `one_sided_ask` / `unpriceable` / `no_book`), plus raw
  `yes_bid`, `yes_ask`, `bid_qty`, `ask_qty`. `mid` alone cannot distinguish
  "0.05 the mid" from "0.05 the ask", and those are different claims about
  what was quoted. The classification can now be re-derived from the log
  rather than trusted.
* `close_source` was **already** on every QUOTE row and in the detector's
  per-event `status.jsonl` output — verified, not added.
* 3 new tests (60 in the two P-022 files). Suite **1,745 passed, 2 skipped**.

New research harnesses, committed:
`golf_quirks_research/classify_anchor_books.py` (A + B) and
`live_book_census.py` (D).

## G. What Sam has to decide, before `2026-07-29T18:32Z`

1. **Quote or don't.** The edge was measured on traded and two-sided
   references; tomorrow's quotes are priced off bare asks, a class worth
   10.9% of the measured contracts at **−1.48 ¢ [−11.93, +8.15]** and a **15%
   fill rate**. My reading: **quote anyway, and treat the first tournaments
   as measuring that class rather than confirming the headline.** The gate has
   zero observations, paper costs nothing but time, and the cell is
   under-powered rather than refuted — but this is a real change of claim and
   it is Sam's call, not mine.
2. **Whether the research rule "two-sided quotes only" gets a scoped
   exception** for this family, written down. It cannot silently not apply.
   Note it was never binding on the *backtest* — the harness enforced
   something stricter — so what is needed is an exception for the **pod**.
3. **Whether a depth screen is worth an §8.1 reset.** The bimodal 12-vs-800
   split says the quoted population contains two very different fill regimes.
4. **POI26's exclusion** — pre-registered above; confirm or override.
