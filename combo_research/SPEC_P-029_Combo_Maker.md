# SPEC — P-029 Kalshi Combo Maker

**Status:** BLOCKED on pre-registered Gate 1 (fill rate). Do not implement before it returns.
**Research:** `combo_research/REPORT_Combo_MM_2026-07-28.md`
**Date:** 2026-07-28

---

## 1. Thesis

Kalshi combo (multi-leg parlay) buyers overpay, and the seller's side is **maker-fee-free**.
Measured in the target zone (2–4 legs, 10–35¢), contract-weighted, day-clustered over 68 event
days and 556.5M contracts:

- **+7.20 ¢/ct** last-price basis, 95% CI [+6.28, +8.08]
- **+3.17 ¢/ct** first-print (conservative) basis, 95% CI [+2.78, +3.57]
- **~4.0% return on posted collateral per turn**, median hold 5.4 h, 4.4% losing days
- **No measurable decay across 11 weeks** in the target zone (weekly slope CI straddles zero),
  even though the aggregate combo edge decays at ~−0.5 ¢/ct per month in the many-leg sub-5¢ tail.

P-029 sells combos in that zone as a maker, prices them with a correlation-aware joint, and holds
to settlement.

## 2. Why the two 0-for-5 priors do not automatically apply

- **Maker/fade 0-for-5** — all five fought a fee and died on fills. Combos are `quadratic`:
  the maker pays **zero**. The fee half of that record does not carry over. The fill half does,
  which is exactly what Gate 1 measures.
- **Multi-leg 0-for-5** — those were *arbitrage* attempts where the fee tax multiplied per leg.
  P-029 never trades the legs; it sells one binary contract whose fee is assessed once, on the
  combo price, at zero for the maker.
- **The house tail rule does not bind here.** It is a fee law, and the fee is zero. The edge peaks
  at 20–35¢ (+7.9 to +8.9 ¢/ct) and the pod should sit there.

## 3. Universe and hard exclusions

| rule | value | basis |
|---|---|---|
| legs | 2–4 | +3.47/+3.10/+2.31 ¢/ct July; 5–6 legs +1.53, 10+ legs +0.40 |
| model fair value | 10–35¢ | peak edge bucket |
| **exclude > 60¢** | hard | **measured −2.46 ¢/ct in July** — the seller loses |
| **exclude < 5¢** | hard | decaying tail; taker rounding fee is 17.5% of premium there |
| collections | live rolling `-R` collections only | only two are ever live |

Re-resolve the live collection set every cycle — the rolling containers are re-opened daily.

## 4. Pricing

**Never the independence product.** Independence understates a correlated 3-leg joint by ~30–35%
relative, which is larger than the entire edge. Required:

1. Leg marks from the underlying Kalshi books (genuinely two-sided: 396/400 sampled, median leg
   spread 1.00¢). Use `orderbook_fp.{yes_dollars,no_dollars}`; best YES ask = 1 − best NO bid.
2. Gaussian copula over leg outcomes, correlation matrix estimated from the settled archive,
   with same-game correlation held **wider** than measured until an in-season sample exists (n=39
   today — not enough to trust).
3. Quote = joint × (1 + margin), margin floored so that expected capture ≥ +1.5 ¢/ct after the
   in-zone haircut measured by Gate 4.
4. Scalar legs multiply and **round down** — model it; it is a permanent small edge to the seller.
5. Combos quote on a **`deci_cent` (0.1¢) grid**. Do not round to whole cents.

## 5. Execution

**Primary — resting interception** (pending Gate 2):
subscribe to the `communications` WebSocket (`rfq_created` is broadcast to all subscribers and
carries `mve_selected_legs`), price the combo, and rest a limit offer in that combo market before
the execution timer expires. Rulebook 5.3.D.e: resting orders hold time priority over the RFQ
orders, and *"Quoter and Requester may in fact never transact a contract between them, if existing
book liquidity exists at the quoted price."* Latency budget: seconds, not milliseconds.

**Secondary — RFQ response:** `POST /communications/quotes` with `yes_bid`/`no_bid`. Combos are
High Volatility Markets — **2–3 s to confirm, 1–3 s execution timer** (docs and rulebook disagree;
assume the tighter). The confirm handler must be a **lookup, not a calculation**: pre-compute the
confirm decision at quote time.

Quote creation costs 2 tokens (not the default 10), so throughput is not a constraint.

## 6. Risk

- **Collateral: full `(1 − price)` per contract, unhedged, unnettable.** There is no documented
  cross-margin between a combo and its legs; combo and leg events sit in different netting groups,
  so hedging legs *adds* capital. Confirm via Gate 5 before sizing.
- **Size per turn, not per contract.** The book carries a large common factor: realised sd at
  5,000 contracts is 2.8× independence, and mean/sd saturates around 1.09. Kelly on a per-contract
  basis will oversize.
- Per-turn distribution at $25,000 collateral (~29,800 contracts): mean +$370, sd $344,
  P(loss) 15.5%, 1-in-1000 −$343.
- Day-level exposure cap in addition to the standard per-pod cap, because same-day combos share
  legs (the single most-used leg appears in 15.1% of a day's combos).
- Register exposure through `AggregateRiskGuard.reserve_trade` — a maker placing many quotes in
  one scan is precisely the pattern that broke the guard for P-017 (see CLAUDE.md §Aggregate risk).
  **Reserve quoted collateral, not just filled** — the same §7 defect found on P-022.
- **Counterparty profiling.** RFQ creator IDs are persistent and pseudonymous. Track realised P&L
  per creator ID; widen or withdraw for IDs that beat us. Reportedly table stakes among existing
  responders, and the main defence against the sharp minority.

## 7. Settlement — required at ship, not after

P-017 shipped without a settler and `on_settlement` was dead code. P-029 ships with
`KalshiComboSettler` from day one.

- Settle only on a **populated `result`**; `status="closed"` is not settled.
- `result="scalar"` is a **partial payout at `settlement_value_dollars`, never VOID.** 56,128
  settled combos resolve scalar with a populated value spread across the whole range. Booking
  them at $0 repeats the golf-settler error exactly.
- Outcome must remain one of `WIN`/`LOSS`/`VOID` — those are hard-coded in `trade_store`,
  `engine`, `aggregate_risk` and `capital_allocator`. Record `settlement_kind` and
  `settlement_value` alongside.
- Use `KalshiPublic.get_market(ticker)` for settlement state. *(Note: unlike the rest of the
  exchange, KXMVE LIST endpoints do **not** null out price/volume/result on settled markets —
  verified 40/40. But per-ticker remains the safe path.)*

## 8. Engine placement

Standalone fast loop via `scripts/run_combo_maker.py`, **not** in the 5-minute engine and **not**
in `pods.active` — same pattern as P-016 and P-017M. The WebSocket subscription and the
seconds-scale resting path cannot live in a 5-minute cycle.

Infra note: the current droplet is a 2 GB box already running six workloads. A persistent
WebSocket consumer plus a copula pricer is a new workload class. Budget for a second box.

## 9. Data obligation, starting now

Kalshi purges settled combo markets after ~3 months. Stand up a **daily settled-combo archiver**
before anything else in this spec — independently of the verdict. Every day it does not run is a
day of NFL/NBA-season combo data that will not exist later, and the season is the regime that
matters. Archive gzipped under `combo_research/archive/` and commit.

## 10. Definition of done for the go/no-go

P-029 is written only when **all** of the following hold:

1. Gate 1 returns fill fraction ≥ 10% **and** ≥ 200 fills **and** realised ≥ +1.0 ¢/ct with a
   day-clustered CI consistent with the measured +3.17 ¢/ct.
2. Gate 3 shows the copula beats independence out-of-sample by more than the edge.
3. Gate 4 confirms the in-zone first-print edge ≥ +1.5 ¢/ct with a CI excluding zero.
4. Gate 5 has produced a measured collateral number.
5. The settled-combo archiver has been running long enough to cover at least one in-season week.

Gate 2 does not gate the pod — it selects the execution path and the infra budget.
