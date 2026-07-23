# Deep Research Round 2 — External-Information & New-Product Opportunities

**Betting Pod Shop · Strategy research**
**Prepared July 22, 2026**

---

## Why this round looks different from the last one

The first research round proposed two microstructure pods (P-018 in-play fade maker, P-019 longshot maker). Initial testing has now resolved both, and the results sharpen where we should look next:

- **P-019 (longshot maker) — killed.** A clean NO-GO on 5,192 event-clustered settled contracts. Kalshi's politics + sports-futures books are *well-calibrated*; the favorite-longshot bias isn't there to harvest. The only real mispricing (the 0–3¢ dying-longshot tail) is a T-1-day effect a long-horizon maker structurally can't capture.
- **P-018 (in-play fade maker) — blocked on data, not killed.** The surprise-gate logic and tests are built, but `book_capture` hasn't accrued in-play ticks yet. It needs the VPS capture service running ~2–3 weeks before its kill gate can even run.
- **Earlier this cycle: P-016 (live maker) failed its gate** (−1.29¢/ct markout — adverse selection ate the spread) and was retired; P-017M was shelved (its +9.1¢ was a weighting-bug artifact).

Put together, a **house pattern** is now unmistakable and it dictates this round's targeting:

> **Every pod built on a behavioral bias or pure market-making has failed on contact with Kalshi's real prices.** The books are better-calibrated than the imported academic literature assumes. What *survives* — P-001 (Kalshi mispriced vs sharp-sportsbook consensus), P-015 (tennis-qualifier inattention pocket), P-017 (golf tie-inflation) — has either an **external information edge** or a **structural/mechanical quirk**. It is never "the crowd is irrational."

So this round hunts exactly those two things, per your direction: **external-information edges (the P-001 archetype)** and **new 2026 products**, ranked by a **balance of testability and durable edge**. Consistent with how P-019 just played out, every candidate below leads with a *cheap, settled-data falsification* — the backtest is the gate, and the pod is built only if it passes.

---

## Ranked opportunity map

| # | Opportunity | Archetype | External reference | Testable now? | Capacity | Durability | Verdict |
|---|---|---|---|---|---|---|---|
| **1** | **MLB totals/run-line vs sharp consensus** | External-info (P-001 extended) | Pinnacle-eu + no-vig multi-book via **Odds API (already paid)** | **Yes — existing infra** | Low–mid, maker-free | Moderate | **Build/test spec (P-021)** |
| **2** | **Cross-venue signal: Polymarket → Kalshi** | External-info (oracle) | Polymarket CLOB/Gamma (free data) | **Yes — settled backtest** | High (politics) | Moderate | **Build/test spec (P-020)** |
| 3 | **Weather tail brackets vs NBM percentiles** | External-info + structural | NWS NBM NBP percentiles (free) | Yes — archived forecasts | Low ($5–20k/day) | Moderate | Cheap fal&#8203;sification (§C) |
| 4 | **KXART "Above $X" ladder monotonicity** | Structural quirk (new product) | Auction-house pre-sale estimates | **Yes — on history via API** | Very low | Med | Free-option scan (§C) |
| 5 | **Macro "bad-tail" premium fade** | External-info (structural residual) | Cleveland Fed nowcast (weak access) | Partial — needs nowcast archive | Thin | High | Start collecting (§C) |
| — | Biotech / KXFDAAPPROVAL | Base-rate calibration | ClinicalTrials.gov + PDUFA base rates | Partial; **human onboarding gated** | Low→Med | High | Watchlist (§C) |
| — | Rotten Tomatoes score markets | Info-latency | RT live Tomatometer | Forward-collect only | Hobby | Low–Med | Satellite only |
| — | Box office brackets | External-info | Boxoffice Pro / Deadline | Yes | Moderate | Moderate | Optional backtest |
| — | Fed-decision stale legs vs ZQ/FedWatch | Latency capture | 30-day Fed Funds (ZQ) / pyfedwatch | Forward-collect | Small | **Low** (one-off) | Opportunistic monitor |
| — | Awards / Mentions / SpaceX-AI timing | — | Gold Derby / none / schedules | — | Low | Low | Skip / novelty |
| — | Crypto perps; Midterms hub; GDPNow-vs-Kalshi | — | — | — | — | — | **Rejected** (see below) |

**Rejected outright:** crypto **perps** (fee-uncompetitive at ~12bp taker vs Hyperliquid ~2–4.5bp); the **Midterms hub** (elections are Kalshi's most liquid, most-made, most-efficient family — no first-mover thinness left); **GDPNow-vs-Kalshi** (the Atlanta Fed states in its own words that GDPNow does not beat professional forecasters, and Kalshi prices off that same consensus — no external edge).

---

## The two to build/test first

Both are the **P-001 archetype** — the one thing that works — pointed at new surfaces, and both are testable *now* with data we already have. They rank 1 and 2 by a balance of testability and durability; full specs follow this report as `SPEC_P021_*` and `SPEC_P020_*`.

### #1 — MLB totals/run-line vs sharp-book consensus (P-021)

The most infra-ready idea we have, and the cleanest extension of P-001. P-001 trades Kalshi *game moneylines* vs a Pinnacle-weighted sportsbook consensus; P-021 does the identical thing on Kalshi's **totals and run-line** derivative series (`KXMLBTOTAL`, `KXMLBSPREAD`, `KXMLBF5`), which I confirmed live are **`quadratic` fee-type → zero maker fee**. That fee status is the structural advantage: a resting version pays nothing, and a taker pays ≤1.75¢/ct.

The honesty flag is the reference, not the plumbing. Totals and run-lines are the *efficient* part of the sports market, so a devigged Pinnacle/consensus line there is genuinely sharp — but Kalshi may also be near-efficient on them, so the edge could be thin. The prior negative result matters here: our own batter-hits *model* did not beat Kalshi props, because a public-stats model carries no information Kalshi lacks. P-021 is different in kind — it references a **sharp book's line** (which aggregates sharp money and bet flow), not our model. Player props (`KXMLBHIT`, `KXMLBKS`, …) are a *speculative second leg* only, because the sharp reference is weaker there (Pinnacle hangs low limits on props and reportedly outsources prop pricing; DFS "lines" are not sharp and must be excluded from any consensus).

**Cheapest falsification (do first, ~1 day, minimal API spend):** ~200 recent settled games, `KXMLBTOTAL` only, Pinnacle-eu closing total devigged vs Kalshi mid at T-1h. Does the sharp-implied probability beat Kalshi's price on realized outcomes (Brier + a day-clustered regression of outcome on the price gap) and produce positive CLV vs Kalshi's own close? If the sharpest, cheapest, most-liquid prop series shows nothing, player props won't save it — kill. If it passes, *then* spend the 10×-cost historical player-prop pulls.

### #2 — Cross-venue signal: Polymarket → Kalshi (P-020)

Highest-capacity candidate and a pure "lag a sharper free reference" play. Polymarket is materially deeper than Kalshi on **politics and world events**, so its mid is a sharper fair value there. When Kalshi's retail-driven price diverges from Polymarket beyond the fee corridor, the Poly-implied direction is a **CLV-gated taker signal on Kalshi** — not a two-legged arb (we have Polymarket *data* access, no execution), which is exactly the single-venue shape of P-001. We already have the clients: `polymarket_client.py` (read paths), `kalshi_public.py`, and `cross_venue_matcher.py`, built for precisely this pairing.

**The honest caveat, front and center:** our own earlier cross-venue study (ev-map H2) found the Kalshi↔Poly basis is *fee-bounded* in the liquid shared head — a ~±2¢ no-arb corridor that keepers stay inside. So P-020's edge cannot live in the liquid head where both venues agree; it must live where **Polymarket is genuinely deeper and Kalshi retail overreacts** (politics/world), and it must clear the corridor. That is an empirical question a settled-data backtest answers directly, and the prior study never ran it (it only compared quotes synchronically, never followed divergences to settlement). The settlement-definition mismatch between venues is the main trap and the matcher must be strict about it.

**Cheapest falsification:** match ~100–200 already-settled Kalshi/Poly political pairs; reconstruct {Poly mid, Kalshi price, fee corridor, realized outcome}; test whether "move Kalshi toward Poly when the gap exceeds the corridor" earns positive CLV and settlement P&L, event-clustered. One afternoon of settled-data work, no forward collection.

---

## §C — Cheap falsifications worth running in parallel (satellites & watchlist)

These rank below the top two on capacity or access, but several are testable for the cost of an afternoon and are worth queuing behind P-020/P-021.

**Weather tail brackets vs NBM (rank 3).** Kalshi daily high/low temp brackets (~15 cities, settle on NWS CLI) vs the **NWS National Blend of Models NBP product**, which publishes *calibrated 10/25/50/75/90th-percentile temperature guidance* per station — free via NOMADS/AWS Open Data/api.weather.gov, and genuinely sharper than the consumer apps most retail reads. This passes the house bar *in kind* (real external reference, cheaply falsifiable against archived NBM + GEFS reforecast). It likely fails on *magnitude/capacity*: an entire cottage industry of "NBM-vs-Kalshi" bots already farms it, so residual edge is confined to the **tail brackets** (lottery demand) and the **morning-of re-pricing** lag, and the book is shallow ($5–20k/day). Falsification: on ~6 months of settled *edge-bracket* contracts, test whether the NBM-percentile-implied tail probability beats Kalshi's ask net of fee, **clustered by city-day** (the discipline that killed broad tennis). If the tails — the most favorable locus — don't clear, kill. Good small satellite at best; do not staff a full pod on spec.

**KXART "Above $X" ladder monotonicity (rank 4).** Confirmed live: `KXART` events are **non-mutually-exclusive monotonic threshold ladders** (5–9 "Above $X" strikes per lot; 16 events, most already finalized). Arbitrage-free pricing requires P(Above $X) weakly decreasing in X, so a thin, unmade book can invert adjacent strikes → a riskless vertical-spread arb; separately, the ladder should straddle the auction house's **public pre-sale low/high estimate**, and there's a knowable **bought-in-lot tail** (an unsold lot resolves the whole ladder to No). This is a structural quirk (not the external-info direction you prioritized, but it rides under "new products"), and it is **testable on history today via the API** with zero onboarding. Capacity is genuinely tiny — treat it as a free-option scan, not a book. First check: pull all settled `KXART` ladders, flag strike-price inversions and estimate-vs-ladder centering, and **verify hammer-vs-hammer+premium and bought-in resolution in the contract rulebook before sizing anything**.

**Macro "bad-tail" premium fade (rank 5).** The one macro residual that survived the Fed's own calibration finding: Kalshi *overprices* high-inflation / high-unemployment tail buckets (retail hedging demand). Sell the scary tail bucket (as maker, on the maker-fee'd `KXCPI`/inflation/unemployment series) when the **Cleveland Fed inflation nowcast** — the one macro model with a documented headline-CPI edge over consensus — sits comfortably inside a benign bucket. Durable (structural demand) but thin (books $13–55 deep) and breadth-driven. The catch: the Cleveland Fed nowcast has **no confirmed public data endpoint**, so the actionable first step is to **start daily-snapshotting it now** into our store; the tail-bucket-vs-realized-frequency backtest can run this week without it (weaker, uses hindsight classification). The companion **stale-Fed-leg-vs-ZQ** idea is real but honestly one-off latency capture, not a book — run it as a cheap always-on divergence alert, not a pod.

**Biotech / KXFDAAPPROVAL (watchlist).** The most *durable* thesis (ClinicalTrials.gov endpoints + PDUFA/Phase-3 base rates are permanent references) but gated: the trial-endpoint pilot requires **human employment-verification onboarding** and MNPI compliance, and its specific tickers aren't on the anonymous API. The pre-existing `KXFDAAPPROVAL` binary series *is* partially backtestable now (calibration of past FDA-approval markets vs realized outcomes and base rates). Worth a calibration backtest to see if there's anything before committing to onboarding.

---

## Recommended sequence

1. **Run the two cheap falsifications now** — P-021 (`KXMLBTOTAL` vs Pinnacle-eu, ~200 games) and P-020 (Poly→Kalshi politics, ~100–200 settled pairs). Both use data we already have; both are one-day settled-data tests; both kill-or-advance before any pod is built. Specs attached.
2. **In parallel, start the collectors that unblock the rest:** get `betting-book-capture` actually running on the VPS (this also unblocks P-018), and begin daily Cleveland Fed nowcast snapshotting.
3. **Queue the free-option scans:** the `KXART` monotonicity/estimate scan and the `KXFDAAPPROVAL` calibration backtest — an afternoon each, no new infra.
4. Build a pod only for whichever of P-020/P-021 clears its backtest with positive CLV, event-clustered, exactly as P-019's gate was applied. The expectation, given the house pattern, is that at least one of these dies at the backtest — and that is the system working.

---

## Sources

- Whelan, "Makers or Takers: The Economics of the Kalshi Prediction Market," GWU 2026-001 — https://www.karlwhelan.com/Papers/Kalshi.pdf
- The Odds API (MLB markets, player props, Pinnacle-eu coverage) — https://the-odds-api.com/liveapi/guides/v4/
- Unabated, on the absence of a sharp player-prop book — https://unabated.com/articles/the-biggest-mistake-youre-making-when-betting-nfl-player-props
- Closing-line efficiency (totals/spreads vs props) — https://joesaumarez.co.uk/sports-betting-market-efficiency-and-the-closing-line
- Kalshi↔Polymarket spreads (raw gap observations) — https://news.dropstab.com/research/kalshi-vs-polymarket
- Polymarket CLOB/Gamma API (free reads) — https://docs.polymarket.com/
- NWS National Blend of Models NBP percentile product (NOAA/MDL VLab) — https://vlab.noaa.gov/web/mdl/nbm-textcard-v4.2
- NBM on AWS Open Data — https://registry.opendata.aws/noaa-nbm/
- Kalshi art markets launch (Sotheby's settlement) — https://news.kalshi.com/p/kalshi-art-markets-launch
- Kalshi biotech pilot (AppliedXL) — https://news.kalshi.com/p/kalshi-biotech-prediction-markets ; https://www.statnews.com/2026/07/16/kalshi-new-offering-prediction-market-clinical-trials-fda-approvals/
- Cleveland Fed Inflation Nowcasting — https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting
- Atlanta Fed GDPNow (accuracy caveat) — https://www.atlantafed.org/cqer/research/gdpnow
- pyfedwatch (ZQ-implied Fed probabilities) — https://github.com/ARahimiQuant/pyfedwatch
- Gold Derby awards consensus — https://www.goldderby.com/
- Boxoffice Pro Long Range Forecast — https://www.boxofficepro.com/category/forecasts-tracking/
- Live Kalshi public API (KXART ladder structure, KXMLBTOTAL fee_type), pulled 2026-07-22 — https://api.elections.kalshi.com/trade-api/v2

*Evidence-quality note: all per-contract edge figures for the candidates are [reasoned estimates] pending the backtests specified — nothing here is measured on our data yet, and the house expectation is that some of these die at the backtest gate. Load-bearing external claims come from single sources each (GWU maker/taker economics, the Fed calibration paper, NBM skill, the "no sharp prop book" practitioner claim) and should be re-read against their primary tables before sizing. Affiliate/SEO sources encountered in the search (weather-signal vendors, prediction-market arb blogs, Mentions-strategy hype) were excluded from every factual claim above.*
