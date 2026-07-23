# Deep Research — Durable Positive-EV Opportunities on Kalshi

**Betting Pod Shop · Strategy research**
**Prepared July 22, 2026**

---

## Purpose and scope

You asked for deep research into new markets and strategies with positive EV on Kalshi — not limited to sports — with two priorities set explicitly at the outset: **hunt novel angles and deepen the pods we already run**, and rank everything by **largest durable net-of-fee edge** rather than by how soon we can test it or how much capital it absorbs. This report does that. It builds directly on the `kalshi-ev-map` survey and the five strategy walkthroughs (P-015 through P-017M) rather than re-deriving them, and it deliberately skips the families we already scored and shelved on capacity grounds (weather-temp, crypto maker) unless a *new* mechanism resurfaces them.

Five parallel research threads fed this: post-news repricing latency, newly-listed Kalshi products, macro nowcast relative value, cross-family favorite-longshot bias, and market-making adverse selection. Each was run against the academic and practitioner literature and cross-checked against live Kalshi API pulls on 2026-07-22. Every edge number is tagged **[verified]** (a cited figure), **[internal]** (our own prior measurement), or **[estimate]** (reasoned, not yet backtested). The honest state of play: the two top-ranked opportunities are *mechanism-verified and literature-backed but not yet edge-measured on our own data* — the build specs below exist precisely to measure them before any capital moves, exactly as we gated P-015/016/017.

### The organizing finding

One structural fact dominated every thread and it is the spine of this report:

> **On Kalshi, the maker starts 2–3.5¢/contract ahead of the taker, and the fee curve is cheapest at the tails.** Maker fees default to **zero** on the vast majority of series — a live pull confirmed that politics (~2,100 series), entertainment (~2,500), and science/tech (~350) are almost entirely `quadratic` fee-type, i.e. free to rest into. Taker fees max at 1.75¢ at 50¢ and shrink to ~0.33¢ at the 5¢/95¢ tails. There is no settlement fee. [verified, live API + fee schedule]

Every durable edge we found is a variation on *provide, don't cross* — and the single best-documented Kalshi result in the literature says the same thing empirically: makers on the exchange returned **−9.6%** versus **−31.5%** for takers, and makers on ≥50¢ contracts earned a small **positive** after-fee return, because retail systematically overbets the YES side and cross-subsidizes the spread (Whelan, GWU 2026-001). The taker strategies we run survive only where the raw mispricing clears the spread; the durable, scalable edges are all on the resting side.

---

## The ranked opportunity map

Ranked by **durable net-of-fee edge** (the filter you chose). "Durability" is the load-bearing axis: an edge that decays the moment attention arrives is worth less than a smaller edge rooted in structure or fees that persists.

| # | Opportunity | Style | Edge (net of fee) | Durability | Novel / Deepen | Status |
|---|---|---|---|---|---|---|
| **1** | **Surprise-Gated In-Play Fade Maker** (P-018) | Maker | +2.8% @2min post-surprise, decaying [verified soccer]; our capture est. **+2–5¢/ct** | **High** — behavioral + zero-fee structural | Both | **Build spec below** |
| **2** | **Cross-Family Longshot Maker Harvest** (P-019) | Maker | **+2–4¢/ct** favorites; +1–2¢/ct longshots [estimate, GWU-anchored] | **High** in thin/long-horizon books; decays with volume | Both | **Build spec below** |
| 3 | Rotten Tomatoes score-drop taker | Taker | **+8–25¢/ct** on sharp films [estimate] | Medium — scraping bots arrive | Novel | Watchlist / satellite |
| 4 | Macro "bad-tail" risk-premium fade | Maker | **+1–3¢/ct** [estimate], thin | High (structural hedging demand) | Novel | Watchlist |
| 5 | Adverse-selection instrumentation for P-016/P-017M | — | Protects existing edge | — | Deepen | **Do now (cheap)** |
| — | Fed-decision stale tail legs vs FedWatch | Taker | Large per-event, episodic | **Low** (staleness, not structure) | Novel | Opportunistic only |
| — | Art-auction dutch-book | Taker | thin novelty | Low | Novel | **Rejected** (not a systematic family) |
| — | Kalshi perps basis/funding | — | fee-uncompetitive | — | Novel | **Rejected** |
| — | GDPNow / general econ model-arb | — | no documented edge | — | Novel | **Rejected** (Kalshi already calibrated) |

The rest of the report defends this ranking, then gives full build specs for #1 and #2.

---

## Why the top two, and why in this order

Both winners are the same trade seen twice: **rent our balance sheet to biased retail on the resting side, in a family where the maker fee is zero, and defend hard against being picked off.** #1 is the in-play, event-driven version; #2 is the pre-event, cross-sectional version. They rank above everything else because their edge is anchored in *structure and fees*, not in attention — the one thing our own history says survives. P-018 ranks first because it has both the highest capacity (liquid in-play books) and the strongest external number behind it; P-019 ranks just behind because its edge, while very durable in the right books, is thinner per contract and carries a genuine tail-risk management burden.

### The rejections, briefly

**Art-auction markets** looked promising in the abstract (new May 2026, thin, dead-heat-like settlement quirks) but the live pull showed they exist only as **sparse one-off novelty series** (`KXAUCTIONLIGHTSABER`, `KXAUCTIONPIKACHU`) — a handful of "how much will X sell for" markets, not a recurring family you can build a pod around. Revisit only if Kalshi lists systematic single-lot bracket sets before fall auction season. **Perps** (launched 2026, 13 crypto assets) are fee-uncompetitive at ~12bp taker versus Hyperliquid's ~2–4.5bp; any funding/basis edge is arbitraged in milliseconds by crypto-native desks with infra we don't have. **General macro model-arb is dead**: the Federal Reserve's own 2026 study ("Kalshi and the Rise of Macro Markets," FEDS 2026-010) finds Kalshi's headline-CPI markets *beat* Bloomberg consensus (MAE 7bp vs 8bp) and its FOMC mode hits zero error by meeting day — you cannot out-forecast a market that is already better than consensus. GDPNow, specifically, has *no documented edge over professional forecasters* by the Atlanta Fed's own admission, so a GDPNow-vs-Kalshi line is unsupported.

What survives from macro is narrow and is #4: not "our model beats the market" but "retail pays a **structural risk premium** for high-inflation / high-unemployment protection," which the Fed paper explicitly flags. That is a fade, on the maker side, durable because it is hedging demand — but the books are $13–55 deep, so it is a breadth-across-releases satellite, not a line of size.

---

## #1 — Surprise-Gated In-Play Fade Maker (P-018)

**Venue:** Kalshi in-play sports (moneyline/derivatives, zero maker fee) · **Style:** resting maker · **Status:** proposed, paper-first

### The mechanism, and why it's durable

This is the generalization of P-016 (live MLB maker) and P-017M (golf fade maker) into a single, cross-sport, event-gated maker — and it is grounded in the cleanest number in the whole research pass. In-play betting exchanges exhibit a **surprise-asymmetric reaction**: bettors *overreact* to surprising events and *underreact* to expected ones. Measured on Betfair in-play soccer, a contrarian strategy that fades the crowd after a **surprising** goal earns **+2.79% at 2 minutes post-event (p=0.018), +1.85% at 3 minutes, decaying to insignificance by ~6 minutes** — roughly 40%/minute decay — with real liquidity in the window (Choi & Hui, AUT). The mirror-image underreaction is what the June 2026 Kalshi NBA study already told us: in-play prices move only ~0.64-for-1 with the true win-probability change.

The durability comes from stacking three persistent sources: (1) the **behavioral overshoot** itself, which is strongest exactly where retail emotion is highest; (2) Kalshi's **zero maker fee** on sports props/derivatives, a permanent 1.75¢/ct structural head start over any taker trying the same thing; and (3) the documented **retail-YES-overbet cross-subsidy** (Whelan) that we can monetize as a standing inventory skew. None of these is an attention edge that evaporates when volume arrives — which is the failure mode that will most likely kill P-015.

### The innovation over P-016/P-017M: the surprise gate

P-016 and P-017M each quote in a fixed regime. The new idea, and the reason this is worth a fresh pod rather than a parameter tweak, is a **surprise score** computed per event: `surprise = |realized_event_winprob_jump| relative to pre-event model prob`. The pod fades **only high-surprise events** (the overreaction regime, where resting an offer into the panic pays) and **withdraws or widens on low-surprise/expected events** (the underreaction regime, where the informed side runs you over on continuation). This single gate operationalizes the entire over/underreaction asymmetry and is portable across sports. Concretely, per the Stanford adverse-selection study of 41.6M Kalshi trades, single-name in-play markets carry ~2× the informed price impact of broad markets and one-sided flow *predicts* maker losses there — so the gate is not optional polish, it is the survival mechanism.

### Edge, honestly

- **External anchor [verified]:** +2.79% @2min fading surprising in-play events (soccer); Kalshi in-play underreaction 0.64-for-1 (NBA study).
- **Our realistic capture [estimate]:** **+2–5¢/contract** per fill — half-spread capture plus a fraction of the overreaction residual, fee-free. This is unproven on our data and is exactly what the paper pilot must measure.
- **Why it beats P-017M's headline +9.1¢:** it doesn't, per contract — but P-017M's +9.1¢ rests on *four* golf events, while this pod fires on hundreds of in-play situations per week across sports, so its *capacity and statistical power* are far higher. Durable-edge-times-volume is the ranking metric, and this wins it.

### Fee and capacity

Sports props/derivatives are `quadratic` (zero maker fee) [verified live API — but note MLB moneyline `KXMLBGAME` and the NBA/NFL spread/total series *do* charge maker fees; the pod must series-check fee_type per market, reusing `kalshi_fees.series_maker_charges_fee`]. Capacity is the highest of any opportunity here — liquid in-play game books — bounded by fill rate, not by displayed depth.

### What the pilot must prove (pre-registered gates)

1. **Surprise gate works:** fading high-surprise events is positive and fading low-surprise events is negative (the gate earns its keep). If both regimes look the same, the gate is noise — kill it.
2. **Markout-adjusted P&L is positive** over ≥500 fills, robust to dropping the best day, measured at 1/5/15-min *and* event-clocked horizons (next pitch, at-bat resolution, next scoring play).
3. **Adverse selection is survivable:** a VPIN-style one-sidedness metric at time of fill does not predict losses that swamp the spread capture.

### Build spec (P-018)

```
Pod:            src/pods/inplay_fade_maker.py  (BasePod maker; or standalone like run_golf_maker.py)
Sports (phase): NBA and MLB first (discrete scoring, high-surprise events, existing live infra from P-014)
Core gate:      surprise_score(event) = |post_event_model_wp - pre_event_model_wp|
                fade  if surprise >= SURPRISE_HI (overreaction regime)
                widen/withdraw if surprise < SURPRISE_LO (underreaction regime)
Model:          reuse P-014 LiveOddsPoller + GameStateTracker for pre/post-event win-prob
Quoting:        Avellaneda-Stoikov adapted to binary:
                  reservation r = mid - q*gamma*sigma^2*(T-t),  sigma^2 ≈ p(1-p) (max at 50c)
                  spread   d = (2/gamma)*ln(1+gamma/kappa) + sigma^2*(T-t)*q^2
                  neutral inventory target q* skewed SLIGHTLY SHORT YES (monetize retail YES-overbet)
Cancel rules:   hard-pull on pitch-in-flight / 2-strike-3-ball / RISP<2out / any tying-or-leading-run AB (MLB)
                widen on lead-change / late-game run (NBA); converge-to-mid + flatten in final window
Fills (paper):  pessimistic — only fill when market trades THROUGH the quote (same as P-016/P-017M)
Fees:           kalshi_fees.fee_per_contract(price, maker=True, series_ticker=...) — series-aware; 0 on props
Instrument:     markout curve (1/5/15min + event-clocked) and VPIN one-sidedness per fill  [see #5]
Kill/promote:   no decision before 500 markout-adjusted fills; promote only if gate-positive AND
                markout-adjusted P&L > 0 dropping best day; kill if surprise gate is indistinguishable
Research first:  inplay_research/  → replay backtest on historical in-play candles → REPORT + params.json
```

---

## #2 — Cross-Family Longshot Maker Harvest (P-019)

**Venue:** Kalshi politics tails + sports-season futures/top-N (zero maker fee families) · **Style:** resting maker · **Status:** proposed, paper-first

### The mechanism, and where it's strongest

The favorite-longshot bias — longshots overpriced, heavy favorites underpriced relative to true frequency — is one of the most replicated regularities in all of wagering. On Kalshi specifically, contracts priced **under 10¢ lose >60% of stake pre-fee**, the loss rate shrinks monotonically as price rises, and **contracts above 50¢ earn small positive returns** (Whelan, GWU 2026-001). This is the same edge we already harvest in two places — tennis qualifier favorites (P-015, +4.1¢/ct [internal]) and golf top-N tie-inflation (P-017) — generalized into a systematic, cross-family *maker* book.

The critical refinement from the calibration literature (arXiv 2602.19520) is **where** it's durable, measured by calibration slope (>1 = favorites underpriced / longshots overpriced):

- **Politics: slope 0.93–1.83, worst at long horizons** — the strongest, most durable bias. A 70¢ political contract a week out is really ~83%. [verified]
- **Sports at long horizon: slope 1.74 beyond one month** (but well-calibrated 0–48h) — season futures and top-N, where our golf edge already lives. [verified]
- **Crypto: near-calibrated (1.01–1.21)** — low durable edge despite exciting tails; skip. [verified]
- **Weather short-horizon: *overconfident* (0.69–0.97)** — the *opposite* of the bias near resolution; our verified weather edge is a bracket-structure artifact, not general FLB, so do **not** generalize it. [verified — important caveat]

The two harvest legs, both as **posted (maker) orders** so we pay zero fee and sit at the fee-cheapest part of the curve:

1. **Buy underpriced favorites** — post YES bids at **85–95¢** in thin, long-horizon politics and sports-futures books. (This is literally what P-015 does; generalize it.)
2. **Sell overpriced longshots** — post NO (sell YES) on **3–10¢** narrative/lottery contracts.

### The tail-risk problem — quantified, because it is the whole design

Selling a 5¢ longshot pays 5¢ and risks 95¢ — a **19:1 downside**. With true probability ~3%, per-contract EV is ~+2¢ but each loss is ~47× each win. Full-Kelly on such a bet computes to ~40% of bankroll, which is a **trap**: with estimation error in the true probability and fat correlated left tails (one storm spikes many weather brackets; one debate spikes many political longshots), full Kelly invites ruin. The design is therefore **capital-cap-first, edge-second**:

- **Fractional Kelly ≤ 1/10**, and a hard **per-event collateral cap ≤ 0.5% of bankroll**.
- **Correlation-cluster caps**, not just per-event caps: model the worst case as "all longshots in a theme resolve YES simultaneously" and bound aggregate cluster exposure. This is the `AggregateRiskGuard` logic we already have, extended to tail clusters.
- **Ruin arithmetic:** at 1/10-Kelly (~0.5%/event, ~40 independent events), a simultaneous 8-event tail cluster costs ~4% of bankroll — survivable, recoverable in ~2 collection cycles. At full Kelly the same cluster is terminal. The cap, not the edge, is what keeps us alive.

### Edge, honestly

- **Favorites (buy 85–95¢, posted):** **+2–4¢/ct** net [estimate], anchored by our own P-015 +4.1¢ [internal] and the GWU positive-maker-return-on-favorites result [verified].
- **Longshots (sell 3–10¢, posted):** **+1–2¢/ct** net [estimate], smaller and tail-heavy — run at half size or favorites-only until the collector proves the tail is priced as modeled.
- **Durability:** high in thin/long-horizon/obscure books; **decays as a specific market gets watched** (our own 13-month tennis evaporation; horse-racing bias moderating as arbitrage arrived). Therefore **CLV-gate every book and retire it as volume rises** — treat decay as the base case, not the surprise.

### Build spec (P-019)

```
Pod:            src/pods/longshot_maker.py  (resting engine like run_golf_maker.py; standalone loop)
Universe:       politics tails (obscure/downballot/"will X happen") + sports SEASON futures / top-N
                filter to fee_type == 'quadratic' (zero maker fee) AND low volume/OI (unwatched)
                EXCLUDE crypto (calibrated) and short-horizon weather (overconfident)
Legs:           A) post YES bid 85-95c on underpriced favorites
                B) post NO (sell YES) 3-10c on overpriced longshots  [half size / gated on collector]
True-prob src:  where available, external model (DataGolf-style for sports; base rates for politics);
                where absent, structural bump like P-017 (conservative lower-CI convention)
Fills (paper):  pessimistic trade-through only
Sizing:         fractional Kelly <= 1/10; per-event collateral cap <= 0.5% bankroll
Risk:           AggregateRiskGuard extended to CORRELATION CLUSTERS (theme/region/event);
                worst-case = all cluster longshots resolve YES
CLV gate:       track closing-line value per book; auto-retire a book when volume/OI crosses threshold
Kill/promote:   pre-registered like P-015 — favorites leg first (lower tail risk); longshot leg only
                after collector confirms tails priced as modeled; kill any book at CLV-negative
Research first:  longshot_research/ → settled-data calibration backtest bucketed by posted-fill price,
                clustered by event; confirm <10c bucket wins < half its implied rate before shipping
```

---

## #3–#5 — Watchlist and the cheap thing to do now

**#3 Rotten Tomatoes score-drop taker (novel satellite).** The Tomatometer is a deterministic aggregation of individually-published reviews; the Kalshi bracket settles on the score shortly after opening. A reader who tallies the first 20–40 source reviews holds a tight posterior *hours before* the crowd-quoted book catches up — a scheduled shock (embargo lift) with an estimated **8–25¢/ct** taker edge on the sharpest films [estimate]. Capacity is low (hundreds to low-thousands of dollars per film) and durability is medium (scraping bots will arrive), so this is a high-Sharpe *satellite* to validate information-latency mechanics, not a line of size. RT series are `quadratic` (zero maker fee), so a resting variant is even cheaper. Cheap to falsify: snapshot the book at embargo-lift and +2/+6/+12h against a model score built from scraped source reviews, and measure the staleness half-life before writing an executor.

**#4 Macro bad-tail risk-premium fade (novel, thin).** Sell the overpriced high-inflation/high-unemployment tail (posted, maker) when the daily **Cleveland Fed inflation nowcast** — the one macro model with a documented edge over consensus on headline CPI — sits comfortably inside the benign bucket. Durable because it's structural hedging demand, but books are $13–55 deep, so it's a breadth-across-releases satellite. Falsify on the last 12–18 CPI/PCE/unemployment settlements before sizing.

**#5 Adverse-selection instrumentation for the pods we already run (deepen — do this now, it's cheap).** Independent of any new pod, our existing makers are missing the one measurement that predicts live survival: **markouts**. Add, to P-016 and P-017M today: (a) a markout curve at 1/5/15-min and event-clocked horizons per fill — a fill that's fine at 5min but deeply negative at "at-bat resolution" tells you exactly which state picks you off; (b) a **VPIN-style one-sidedness metric** at time of fill, which the Stanford study shows *predicts* maker losses in single-name markets; (c) an Avellaneda-Stoikov inventory skew with a slightly-short-YES neutral target to monetize the documented retail-YES-overbet subsidy. Our current "fill only on trade-through" pessimism models *whether* we fill, not *how toxic* the fill was — markouts close that gap and directly de-risk the promote/kill decisions already in flight.

---

## The through-line

Every durable edge in this pass is the same shape: **post, don't cross; harvest biased retail; defend against the informed.** P-018 does it in-play and event-gated; P-019 does it pre-event and cross-sectional; #4 does it on macro hedging demand; and #5 is the instrumentation that keeps all of them alive. The taker ideas that scored well on point-edge (Rotten Tomatoes) are satellites — real, but capacity-bounded and attention-decaying. The macro model-arb that a rates-literate desk would reach for first is genuinely dead on Kalshi, because the exchange is already better-calibrated than consensus; what's left there is a structural risk-premium fade, not a forecasting edge.

Recommended next actions, in order: (1) ship the **markout + VPIN instrumentation** to P-016/P-017M this week — cheapest, de-risks live work already underway; (2) stand up the **P-018 in-play replay backtest** in `inplay_research/` to measure whether the surprise gate is real on our own candles; (3) run the **P-019 favorites-leg calibration backtest** on settled politics/futures data, favorites-first because the tail risk is lower. Each is gated behind a paper-validation period with a pre-committed kill rule, same discipline as the existing pods — the point is to tell a real structural edge from a backtest artifact *before* any capital is at risk.

---

## Sources

- Whelan, "Makers or Takers: The Economics of the Kalshi Prediction Market," GWU 2026-001 — https://www.karlwhelan.com/Papers/Kalshi.pdf
- Federal Reserve Board, "Kalshi and the Rise of Macro Markets," FEDS 2026-010 — https://www.federalreserve.gov/econres/feds/files/2026010pap.pdf
- Bartlett & O'Hara, "Adverse Selection in Prediction Markets: Evidence from Kalshi" (41.6M trades) — https://law.stanford.edu/2026/04/21/adverse-selection-in-prediction-markets-evidence-from-kalshi/
- Choi & Hui, "Over/Underreaction to Unanticipated Events in In-Play Soccer Betting" (AUT) — https://acfr.aut.ac.nz/__data/assets/pdf_file/0013/30046/AFM_2012_120.pdf
- Angelini, De Angelis & Singleton, "Informational efficiency in in-play prediction markets" — https://www.carlsingletoneconomics.com/uploads/4/2/3/0/42306545/information_efficiency_angelini_de_angelis_singleton.pdf
- "Domain-Specific Calibration Dynamics in Prediction Markets," arXiv 2602.19520 — https://arxiv.org/pdf/2602.19520
- Snowberg & Wolfers, "Explaining the Favorite-Longshot Bias," NBER w15923 — https://www.nber.org/system/files/working_papers/w15923/w15923.pdf
- Cleveland Fed Inflation Nowcasting — https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting
- Atlanta Fed GDPNow (accuracy/methodology) — https://www.atlantafed.org/research-and-data/data/gdpnow
- Databento microstructure — markouts — https://databento.com/microstructure/markout
- Hummingbot, Avellaneda-Stoikov guide — https://hummingbot.org/blog/guide-to-the-avellaneda--stoikov-strategy/
- Kalshi art markets launch — https://news.kalshi.com/p/kalshi-art-markets-launch
- Kalshi perpetual futures launch — https://news.kalshi.com/p/kalshi-launches-perpetual-futures-america
- Delvecchio, "Informed Trading in Prediction Markets: Evidence from Kalshi Mentions" — https://scholarship.claremont.edu/cmc_theses/4166/
- Live Kalshi public API (series fee_type, market structure), pulled 2026-07-22 — https://api.elections.kalshi.com/trade-api/v2

*Evidence-quality note: the two top-ranked opportunities are mechanism-verified and literature-backed but **not yet edge-measured on our own data**; all per-contract capture figures for them are [estimate] pending the backtests specified. The load-bearing external numbers (GWU maker/taker returns, Fed CPI calibration, soccer overreaction, Kalshi calibration slopes) come from single papers each and should be re-read against their exact tables before sizing. Several practitioner figures encountered in the search came from affiliate/SEO blogs and were excluded from every claim above.*
