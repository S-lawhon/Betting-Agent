# 02 — Edge Hypotheses: Mechanism, Counterparty, Falsification

Every hypothesis below is tagged (a) relative-value, (b) internal-consistency, (c) model, or (d) structural/behavioral, and carries: the mechanism, who is on the other side, and what evidence kills it. Live-tested items are marked with their result. All prices ET evening, 2026-07-18.

## Tested live this session

### H1 [(b) internal consistency] Bracket sums / monotonicity violations — **TESTED: effectively extinct at snapshot resolution**
Scanned 5,185 multi-leg open events and 2,401 threshold ladders against executable quotes.
- 175 apparent candidates → 19 after requiring `mutually_exclusive=true` (the API flag; most apparent "arbs" were non-exclusive events like Mentions where bracket sums legitimately exceed 100%).
- All 19 survivors were **empty-book placeholder quotes** (market objects report bid 0.00/ask 0.01 when no orders rest — verified against live orderbooks). The two most promising ladder violations (Starship flight count 7–11¢, CA earthquake) dissolved on rules reads: "exactly N" brackets masquerading as ladders in the `strike_type` metadata, and year-thresholds parsed as strikes.
- **Conclusion:** the static structure book is clean. Whoever is doing structural arb on Kalshi (and the book shapes say someone is — dime-wide two-sided quotes across whole bracket sets) has it covered at rest. What is NOT tested here: **transient** violations in the seconds after news. That requires the websocket feed, and is folded into H8.
- Counterparty if you find one anyway: nobody — that's the point. It's a machine's queue you'd be joining.

### H2 [(a) RV] Kalshi vs Polymarket cross-venue basis — **TESTED: fee-bounded IN THE LIQUID SPORTS HEAD; false outside it**

> **Re-scoped 2026-07-28 after P-020.** This hypothesis was stated
> unconditionally ("fee-bounded"). It holds **only in the liquid sports head**,
> which is where the four rows below were measured. Outside that head, P-020
> found the basis is not a fee-bounded corridor at all — and, more damaging to
> the "free fair-value oracle" implication below, the **Brier-minimising weight
> on Polymarket was 0.0 on a monotone sweep**: Kalshi is both the *deeper* and
> the *sharper* venue there, so quoting around a Polymarket mid imports noise
> rather than information. See
> [`crossvenue_research/REPORT_CrossVenue_2026-07.md`](../crossvenue_research/REPORT_CrossVenue_2026-07.md).
> **The oracle implication in the third bullet is therefore scoped to the
> liquid head and unsupported outside it.**
Synchronized live comparison (WC final winner legs, Trump-attends, pre-game MLB):

| Market | Kalshi | Polymarket | Basis (mid) | Kalshi taker fee | Verdict |
|---|---|---|---|---|---|
| Argentina lifts WC | 42.30/42.40 | 40.90/41.00 | +1.4¢ K rich | 1.71¢ | inside corridor |
| Spain lifts WC | 58.60/58.70 | 59.00/59.10 | −0.4¢ | 1.70¢ | inside |
| Trump attends final | 96.00/96.10 | 96.20/96.80 | −0.5¢ | 0.27¢ | ≈flat after poly costs |
| WSH@ATH (pre-game) | 50/51 | 51/52 | −1¢ | 1.75¢ | inside |

- **Mechanism of the residual basis:** Kalshi's taker fee (up to 1.75¢) + maker fee on the big-sports series (0.44¢) defines a no-arb corridor ~±2¢ wide. Cross-venue keepers stay inside it. Both venues' retail can push price around *within* the corridor.
- **Implication:** cross-venue arb as a *business* is dead in liquid markets, but Polymarket (fee-free CLOB, deeper on politics/global sports) is a legitimate **free fair-value oracle** for a Kalshi maker: quote around Poly mid, capture Kalshi's wider retail-driven swings passively at zero maker fee (most series) or 0.44¢ (big sports). That inverts the fee asymmetry into an edge.
- Falsification: measure maker fill rates + adverse selection on Kalshi vs Poly-mid reversion (needs ~2wk of live quote capture; listed in roadmap).

### H3 [(a) RV] Fed/rates vs fed funds futures — **TESTED (partially): large gap, verify before believing**
- KXFEDDECISION-26JUL hike-25 = **5–6¢** vs CME-futures-implied **13.3%** `[estimated — investing.com Fed Rate Monitor, 19h stale; verify on Bloomberg WIRP/`FDTR` before trading]`. Book depth is real: $65k+ available on the yes side at 5–6¢, $263k top-5 no-side.
- Kalshi-internal chain check: composite P(rate >3.75% after Sept) via KXFEDDECISION legs ≈ **37%** vs direct KXFED-26SEP-T3.75 ≈ **27.5%** — a 9-point internal gap `[verified live]`, but KXFED-26SEP depth is ~$900/side: an inconsistency, not a trade. It does tell you which leg is the "real" price (the deep one) and that the thin cumulative series is where a maker can quote off the deep conditional complex.
- **Mechanism:** Kalshi rates books are retail + a few small quoters; no rates desk bothers to keep a $900-deep strike aligned. The July hike underpricing (if it survives verification) is retail anchoring on "the Fed is done" narrative vs. futures pricing genuine reacceleration risk.
- **Counterparty:** retail sellers of tail outcomes (see H6 longshot test — note this is the *reverse*: the tail here may be too cheap).
- **Falsification:** pull SOFR/FF futures off Bloomberg same-minute; if implied July-hike < 8%, the gap is inside combined model+staleness error — kill. Also check whether the 5¢ ask is stale-quote residue by watching it around Monday's open.

## To test in Phase 3 (settled data — running now)

### H4 [(c)/(d)] Headline efficient, props soft (the user's named hypothesis)
Test: calibration + fee-aware naive backtest of KXMLBGAME (headline) vs KXMLBTOTAL/KXMLBSPREAD and WC/UFC prop series vs their headline series, at T-24h and T-1h executable quotes. Also cross-sectional: is |mid − settled outcome| systematically larger in props at matched price levels and horizons?
- Mechanism if true: sportsbook-sharp pricing propagates to headline via cross-venue watchers; props have more legs than watchers, and Kalshi's prop books are quoted by fewer/lazier makers with wider models.
- Depth check must accompany any "yes": prop mispricing at $600 median bid depth is a curiosity (survey table says exactly that).

### H5 [(d)] Longshot bias in the tails
Realized settlement freq of 1–10¢ and 90–99¢ buckets vs price, by family, with Wilson CIs and multiple-testing correction (Bonferroni across family×bucket×horizon cells). Kalshi's own fee curve punishes mid-prices least per dollar of collateral at the tails — if 95–99¢ contracts settle YES more often than priced, buy-the-favorite is the classic harvest; net-of-fee test decides.

### H6 [(d)] Time-decay anchoring in long-dated markets
Drift analysis on daily candles for elections/futures families (T-7d vs T-24h vs settlement). Sample assembled; full test needs candle pulls beyond the current budget — roadmap item.

### H7 [(c)] Weather vs raw ensembles (GEFS/ECMWF)
Not testable from this desk tonight (no NOMADS pull built). Structure facts already in hand: daily NWS-settled temp brackets across ~15 cities, ~$30–100k/day family volume, 1–2¢ spreads, **ask-heavy books in the evening** (bid side literally $0 at sample time — you cannot passively exit). Retail buys "hot day" lottery tickets; the other side is a small number of forecast-literate quoters. The build is cheap (ensembles are free, the target is a single station's CLI report) and the depth question (morning books) decides capacity. Falsification: if morning two-sided depth < $2k/city, cap family capacity at hobby scale and kill.

### H8 [(a)/(b), infrastructure-gated] Post-news repricing latency
How long do Kalshi books stay stale after a shock (data release, goal scored, candidate drops out)? Requires websocket capture around known event times. The MVE printout (~4k parlay markets/min created, each priced off marginals by formula) plus H1's clean static books imply Kalshi's own systems are fast; the question is only whether *human-quoted* families (weather, RT, mentions, long-tail sports) lag. Roadmap item with a concrete capture plan.

## Hypotheses considered and rejected without full test

- **Index intraday RV vs options-implied digitals (the natural Bloomberg trade):** the family is dead on Kalshi — 3,230 open markets, $2.6k total 24h volume, 28¢ spreads. There is nothing to trade against. Revisit only if Kalshi revives the product (they now push perps for this exposure instead). `[verified from universe pull]`
- **CPI/inflation vs swaps:** $13 median bid depth. The signal exists; the market doesn't.
- **MVE parlay pricing errors:** parlays are priced off marginals by the exchange with no public book to make into; you can only take the exchange's price, which embeds its margin. No entry point for an arb.
