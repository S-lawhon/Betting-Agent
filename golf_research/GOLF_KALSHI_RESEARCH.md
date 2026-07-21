# Golf on Kalshi — Edge Research & Pod Proposal

*Research date: July 19, 2026 (final round of The Open Championship). Two evidence legs: (1) a verified deep-research sweep of the academic/industry literature (103 research agents, 12 findings surviving 3-vote adversarial verification, refuted claims documented); (2) original empirical analysis of Kalshi's own golf markets — 15,517 settled prop markets, 3,244 tournament-winner markets, ~18,800 daily candlesticks, and 170,581 tick-level trades pulled from the Kalshi public API today.*

---

## 0. Executive summary

**Verdict: golf deserves a pod — but not the pod you'd naively build.** The literature says golf outright-winner markets are efficient and even DataGolf's own model loses money on outrights, while derivative markets (make-cut, top-10/20, matchups, 3-balls) are demonstrably softer. Our Kalshi-specific data goes further: it shows *where in the week* and *on which side* the softness lives on Kalshi specifically, with executed-trade evidence rather than quote-based inference:

1. **A repeating weekly cycle in top-N markets.** Mid-tier players (5–40¢ to finish top-10/20) are systematically **underpriced pre-tournament** (YES takers 4–10 days out earned +5.3¢/contract *even paying the spread*, on 1.8M contracts across 4 events) and systematically **overpriced late in the tournament** (YES takers 12–48h before settlement lost −8.4¢/contract on 1.6M contracts; the resting maker side captured ~+8¢ gross at zero maker fee).
2. **Structural favorite-longshot bias at the quote midpoint**, not just in the spread: penultimate-day mids at 10–20¢ win 8.2% (n=732, edge +6.5¢ ± 1.0 se); 80–95¢ favorites *underpriced* by ~5¢. This partially survives DataGolf's "FLB is mechanical" critique because it's measured at the mid, not the ask.
3. **A structural settlement quirk:** Kalshi top-N contracts pay **in full on ties**. Real events averaged ~22.7 "yes" settlements per top-20 market vs the nominal 20 (max 30). Anyone pricing from sportsbook odds with dead-heat conventions systematically underprices the whole complex — a concrete, mechanical reason the pre-tournament YES side has been cheap.
4. **Liquidity is real and concentrated:** ~1.32 **billion** contracts traded across 13 KXPGATOUR winner events in 2026 (268M on The Open alone), ~98M on props. Round-1-leader markets do 91–99% of their volume in-play on round day. Winner markets have 0.2¢ median spreads (institutional MM present); prop markets 2–7¢ spreads (retail-facing, where the edge is).
5. **Fees make the strategy choice for you:** prop series charge quadratic taker fees (peak ~1.75¢ at 50¢) but **zero maker fees**; winner series (KXPGATOUR etc.) charge maker fees too. So: be a maker in props, and only cross spreads pre-tournament where measured edge (~5¢) exceeds taker fee (~1.1¢ at 20¢) plus half-spread.

The strongest warning from the literature applies with full force: **realized edge runs 15–30% of modeled edge** (DataGolf's own records), and published edges decay. The pod design below therefore ships with the same CLV-style validation gate as your v2 plan, adapted to golf.

---

## 1. What the verified literature says

The deep-research sweep produced 12 findings that survived adversarial verification (2/3+ refute votes killed a claim; six claims died, listed in §1.8). Confidence labels are the verifier panel's.

### 1.1 Outright winner markets are efficient — don't attack them with a model
Shmanske's peer-reviewed study of the full 2002 PGA Tour season found no systematically exploitable biases in golf odds, and explicitly tested a "Tiger Woods effect" by splitting on Tiger's participation — efficiency held (high confidence, 3-0). Critically, the companion inference "most variance is unexplained, so a better model can add value" was **refuted 0-3** — unexplained outcome variance is not room for edge. And DataGolf's own 2021 record lost −19.6% ROI on outrights/futures (1,011 bets) and −48.6% on top-5 (501 bets) while its derivative bets profited (§1.4). [Shmanske 2005, J. Econ & Finance 29(3)]

### 1.2 Market structure predicts bias direction
Multi-outcome markets with favorite probability < 0.5 (golf winner markets) tend toward classic favorite-longshot bias; binary markets with favorite > 0.5 (H2H, most Kalshi yes/no contracts) often show *reverse* bias — favorites overbet (high confidence, 3-0). This gives a structural prior per Kalshi market type. [Newall & Cortis 2021, Risks 9(1):22]

### 1.3 The FLB is not free money — but Kalshi's version is partly real
DataGolf's price-formation argument (verified 3-0, math independently confirmed): an unbiased bid-ask *midpoint* mechanically implies worse realized returns as odds lengthen when you buy at the ask (EV of buying at ask p+s is −s/(p+s)), so "fade all longshots" mostly harvests the spread you yourself pay. **However** the verifiers flagged the important counterpoint: Whelan's ~300k-contract Kalshi study finds Kalshi's FLB is *not fully* explained by fees/spreads — a residual belief bias exists. Our own mid-based calibration (§2.3) confirms exactly that on golf: the bias survives at the midpoint. Implication: harvest it **as a maker**, never by crossing the spread. [datagolf.com/fav-longshot-not-a-bias; Whelan, UCD]

### 1.4 Derivatives are the soft markets; the hurdle rate decides everything
DataGolf 2021 record by type: Props/make-miss-cut **+14.6%**, Top-10/20 **+11.9%**, Tournament matchups **+12.5%**, 3-balls **+8.1%**, 2-balls **+5.6%** — vs outrights −19.6% and top-5 −48.6% (high confidence; figures verified against page-embedded JSON). In their 106k-bet matchup dataset, blind betting lost only −3.3% at Pinnacle vs ~−7% at soft books (Bet365 −7.08%, DK −7.01%) — the take-rate spread that decides whether an edge survives. At an 8% EV threshold, model ROI was 0.1% on 72-hole matchups, 1.7% on single-round matchups, **9.7% on 3-balls** (medium confidence, 2-1). [datagolf.com/analyzing-betting-odds; /how-sharp-are-bookmakers]

### 1.5 Model edges shrink ~70–85% in the real world
The most robust cross-cutting finding (3-0, self-critical vendor disclosure): betting every positive DataGolf model edge **lost** money (−0.9% over 45,866 bets); a 5% EV threshold realized +1.45% vs +10.65% expected; 8%+ threshold realized +4.43% vs +14.12% expected. Their 2021 live record: +5.0% realized vs +16.6% model-expected. Practical rule: demand ~8%+ modeled edge, expect to keep a quarter to a third of it, shade toward market prices (the specific 45/55 blend figure was refuted 1-2 — direction verified, weights not).

### 1.6 Pinnacle close is the calibration reference
Pinnacle's de-vigged closing matchup/3-ball prices are essentially fully calibrated (flagged +4% EV realized ~4%, slope ~1.0 across 106,204 bets; corroborated by an independent 87,960-pair study) — medium confidence (2-1). Use de-vigged Pinnacle close as the golf CLV reference, exactly as your v2 plan does for MLB. Note: Pinnacle's supremacy is a *closing*-line phenomenon (Betcris opened sharper in DataGolf's follow-up).

### 1.7 For the model itself
Strokes-gained categories have unequal predictive weight — roughly OTT 1.2, approach 1.0, around-green 0.9, **putting 0.6**. Putting-driven hot streaks are the least persistent signal and thus the best candidate for market overreaction. Equally verified: model specs with near-identical cross-validated accuracy disagree substantially on individual predictions — "assume your model's odds are truth" is an unrealistic best case; ~half of model-vs-market discrepancies will be your model's error. (Both 3-0.) [datagolf.com/predictive-model-methodology]

### 1.8 What got refuted (do not build on these)
Six claims died in verification, most notably: "unexplained variance implies room for a better model" (0-3); "FLB is supply-side so exchanges should be clean" (0-3); the specific 45/55 model/market blend (1-2); "course-fit adds nothing" as a general claim (0-3); "all closing-odds predictive weight goes to Pinnacle alone" (1-2). Also verified as a caution (3-0): the sole surviving strategy from a peer-reviewed sentiment-mispricing study produced **zero further profit** when re-tested out-of-sample 2020–2023 — published edges decay.

---

## 2. Kalshi golf: what the exchange's own data shows

Dataset pulled July 19, 2026 via the Kalshi public API: all settled 2026 markets in 11 golf prop series (15,517 markets) plus 4 winner series (3,244 markets); daily candlesticks (bid/ask/trade OHLC, volume, OI) for every market; full tick-by-tick trade history (170,581 trades) for the top-10/top-20 complexes of the five biggest recent events. **The Open and Corales Puntacana settled today and their "yes" results were not yet populated in the API; both events are excluded from every outcome-based number below.** Analysis code and raw data are in `golf_research/`.

### 2.1 The catalog and the fee split

Kalshi lists a far deeper golf catalog than expected — per tournament: winner (KXPGATOUR, ~130–160 golfer markets), top-5/10/20 (and top-40 at majors), make-cut, round-1/2/3 leader, 72-hole head-to-heads (KXPGAH2H), single-round 3-balls (KXPGA3BALL), plus exotics (hole-in-one, bogey-free round, low round score, winning score, cut line value, nationality/region of winner, playoff, win margin). LPGA, LIV, DP World and Champions Tour get winner markets (KXLPGATOUR, KXLIVTOUR, KXCHAMPTOUR) and some H2H.

The fee structure splits cleanly (verified from series metadata):

| Series | Fee type | Meaning |
|---|---|---|
| KXPGATOUR, KXTHEOPEN, KXPGA (winner markets) | `quadratic_with_maker_fees` | Takers pay ~0.07·P·(1−P)/contract; **makers also pay** (~¼ rate) |
| All prop series (top-N, make-cut, H2H, 3-ball, leaders) | `quadratic` | Takers pay; **makers pay zero** |

The taker fee peaks at ~1.75¢/contract at 50¢, is ~1.1¢ at 20¢, ~0.65¢ at 10¢. Zero maker fees in props is the single most important microstructure fact for strategy design.

### 2.2 Liquidity: bigger than "niche," concentrated where you'd not expect

2026 season contract volume observed in the dataset (contracts, not dollars):

| Where | Volume |
|---|---|
| KXPGATOUR winner markets, 13 events | **1.32B** contracts (The Open 268M, PGA Champ 261M, US Open 251M; regular events 60–110M) |
| All prop series (11 series, season, ex-Open/Corales) | ~98M contracts |
| Round-1 leader (single prop series!) | 52.6M — of which 91–99% trades **in-play on round day** (US Open R1: 30.2M) |
| Top-20 complex | 16.5M; Top-10: 11.0M; Top-5: 8.6M; Make-cut: 2.5M |
| 72-hole H2H + 3-balls | ~1.8M combined — thin, 4–6¢ spreads |

Winner markets: median quoted spread **0.2¢** (sub-penny pricing, institutional market-maker present). Top-5/10/20: 2–3¢ median spreads. Make-cut/3-ball/top-40: 6–7¢. The 24–48h fade window alone (the overpricing window, §2.4) carried ~470k YES-taker contracts per event across just top-10+top-20 — a small automated maker capturing even 2–5% of that flow trades meaningful size.

### 2.3 Calibration: the favorite-longshot bias survives at the midpoint

Quote-midpoint calibration on the penultimate day before settlement, all prop series pooled, two-sided books with ≤10¢ spread (n = 5,749 markets):

| Mid price | n | Win rate | NO-side edge at mid |
|---|---|---|---|
| 0–5¢ | 1,769 | 1.0% | +1.1¢ (se 0.2) |
| 5–10¢ | 735 | 2.7% | **+4.8¢** (se 0.6) |
| 10–20¢ | 732 | 8.2% | **+6.5¢** (se 1.0) |
| 20–35¢ | 771 | 23.9% | **+3.7¢** (se 1.5) |
| 35–80¢ | 1,648 | — | ~0 (efficient) |
| 80–95¢ | 82 | 91.5% | **−5.3¢** (se 3.1) — favorites *underpriced* |

This is measured at the mid, so it is not the mechanical bid-ask artifact DataGolf warns about — it's Whelan's residual behavioral FLB, confirmed golf-specifically. The mirror image at 80–95¢ (buy-the-favorite edge) is the classic FLB signature. Caution: outcomes within an event are correlated (top-N slots crowd out), so true standard errors are wider than shown; event-level consistency is checked in §2.4.

### 2.4 The weekly cycle — the headline Kalshi-specific finding

Sell-at-bid NO edge in top-10/20 markets (bid 5–40¢, spread ≤6¢) by days before settlement:

| Days out | n | Avg bid | Win rate | NO edge at bid |
|---|---|---|---|---|
| 5 (Tue) | 763 | 17.9¢ | 26.3% | **−8.4¢** (YES side cheap) |
| 4 (Wed) | 977 | 17.3¢ | 26.8% | **−9.5¢** (YES side cheap) |
| 3 (Thu) | 1,358 | 16.5¢ | 19.4% | −3.0¢ |
| 2 (Fri) | 1,197 | 16.9¢ | 14.4% | +2.5¢ |
| 1 (Sat) | 1,255 | 16.3¢ | 9.7% | **+6.5¢** (YES side rich) |
| 0 (Sun) | 574 | 17.7¢ | 11.7% | **+6.1¢** (YES side rich) |

The crossover is confirmed by **executed trades** (no stale-quote artifact possible — these are fills), 4 events (US Open, PGA Champ, Travelers, Scottish Open), YES-taker flow at 5–40¢:

| Window before close | Contracts | VW price paid | VW outcome | Taker P&L |
|---|---|---|---|---|
| 4–10 days (pre-tourn) | 1.82M | 21.2¢ | 26.5% | **+5.3¢** — takers *won* |
| 2–4 days | 3.08M | 23.0¢ | 25.5% | +2.5¢ |
| 24–48h | 1.60M | 20.5¢ | 12.1% | **−8.4¢** — takers lost; makers won |
| 12–24h | 284k | 22.9¢ | 16.9% | −6.0¢ |
| final 12h | 411k | 24.6¢ | 27.9% | +3.3¢ (post-determination flow) |

Interpretation: pre-tournament, retail attention concentrates on stars while the "boring middle" (world-rank 30–80 players at 10–25¢ for top-20) goes unbought — and the tie-inflation quirk (§2.5) makes the whole complex structurally cheap. Once the tournament starts and players fall down the leaderboard, holders don't sell and new hope-buyers keep lifting offers — prices mark down too slowly ("hope premium"). Both sides of the cycle were profitable to trade *against* in H1 2026. Event-level consistency: midweek YES edge positive in 14/19 event-series (mean ≈ +7¢, t ≈ 4.3 treating events as units); the 24–48h fade negative for takers in 7/8 event-legs (range −6.6 to −13.7¢, one +3.9 exception).

Make-cut markets run the **opposite** direction: 5–40¢ make-cut YES was *underpriced* (−4.1¢ NO edge, se 2.6, n=318) — the crowd is too eager to write off marginal players. Consistent with reverse-favorite bias in binary markets (§1.2). Softer signal, but it means the make-cut pod leg buys cheap YES rather than fading.

### 2.5 The tie/dead-heat structural edge

Kalshi top-N rules: "finishes in the top N **including ties** → yes." Full payout, no dead-heat reduction. Counting actual settlements across the ten fully-settled 2026 events with complete fields: top-20 markets averaged **22.7 yes-settlements vs the nominal 20** (max 30 at THCCBN26), i.e., roughly +13% probability mass the complex must carry above a naive "exactly 20 slots" pricing. Sportsbook top-N odds conventionally assume dead-heat reduction; any maker or bettor transplanting book odds onto Kalshi underprices YES across the board. This alone plausibly explains much of the pre-tournament cheapness in §2.4 — and it is a *permanent, mechanical* edge source as long as pricing flows from sportsbook-style references, unlike behavioral edges that decay. DataGolf's API predicts P(top-N incl. ties) directly, which handles this correctly.

### 2.6 Winner markets: efficient-ish, institutional, and maker-fee'd

Winner-market calibration (mid two days out, n=1,670): 2–5¢ names won 1.0% vs 3.2% priced (−2.2¢, the FLB again), 5–10¢ fair, 10–20¢ slightly cheap (+5.5¢, n=16 only). With 0.2¢ spreads, an entrenched MM, maker fees charged, and the literature's unanimous "outrights are the hard market" — winner markets are for *benchmarking*, not for trading. Exception worth monitoring: the 2–5¢ longshot zone, where selling YES as a maker still cleared ~2¢ gross vs true probability in H1 2026.

### 2.7 In-play round-leader markets: where Kalshi golf liquidity actually lives

R1-leader markets trade 30M contracts on a single major's round day with 0.8¢ median spreads. This is Kalshi's live-golf product working. A live golf pod (P-014 analog: live win-probability model vs in-play price) would have enormous flow to trade against — but requires a shot-by-shot state model (live strokes-gained feed, hole-by-hole scoring) that is a much bigger build than the weekly-cycle pod. Log it as Phase-3 optionality; the literature's live-mispricing evidence (NBA underreaction) did not survive verification for executable profit after costs in any case.

---

## 3. Sam's five questions, answered

**Q1 — Is there edge from known behavioral factors?** Yes, and it's measurable on Kalshi specifically: the mid-survives FLB (§2.3), the weekly hope-premium cycle (§2.4), and reverse bias in make-cut (§2.4). But per §1.3, the *only* way to harvest most of it is as a maker at zero fee in props — crossing spreads to fade longshots hands the edge back.

**Q2 — Can you out-model the market / become the sharp?** Not by building from scratch — §1.5's shrinkage numbers and §1.7's "your model is half the error" finding are decisive against a solo ground-up model competing with DataGolf/Pinnacle. The realistic version: **buy the model rather than build it** (DataGolf's scratch API tier prices every PGA player's win/top-5/10/20/make-cut probabilities weekly, ties handled), de-vig Pinnacle/consensus via The Odds API golf outrights as a second anchor, and spend your engineering budget on *execution* (maker infrastructure) where you have an actual comparative advantage over both retail and the sportsbook-syncing MMs. You already own most of this pipeline.

**Q3 — Do prop markets have value?** They're the whole opportunity. The literature's derivative-softness finding (§1.4) replicates on Kalshi with exchange-native flow (§2.4) — answering the deep-research sweep's open question #3 in the affirmative. Priority by measured Kalshi edge density and capacity: top-20 > top-10 > top-40 (biggest edge, thin) > top-5 > make-cut (reverse side) > 3-balls/H2H (thin, wide spreads — skip until maker infra proves out).

**Q4 — Maker vs taker?** Maker, decisively, with one exception. Makers pay zero fees in every prop series, capture the 2–7¢ spread, and the measured fade edge (+8¢ gross in the 12–48h window) accrues to resting orders. The exception: the pre-tournament cheap-YES window, where measured edge (+5¢ even at the ask) justifies *taking* when a resting bid won't fill — the taker fee at 20¢ is only ~1.1¢. Adverse-selection risk as a maker is real but bounded: quote only where your fair value (DataGolf + de-vig consensus) gives you a margin, never quote through news windows (WDs, weather delays), and keep per-name size small.

**Q5 — Optimal overall approach on Kalshi?** The pod spec in §5. One structural note: golf does **not** violate your v2 "only trade where a sharp close exists" rule — Pinnacle/DataGolf provide exactly the reliable reference §1.6 requires, so golf CLV is measurable, unlike true niche markets. Golf's weakness vs MLB is cadence (≈1 tournament/week → slow sample accumulation); its strength is that the measured edges are 5–10× larger than anything you'll find on ~50¢ MLB moneylines, and the tie quirk is mechanical rather than behavioral.

---

## 4. Honest limitations

The H1-2026 sample is ~13 tournaments with full data and outcomes correlate within events — treat the effect sizes as directionally strong, magnitudes ±half. The trades analysis covers 4 events. THOC26/COPC26 exclusion was forced by settlement lag (re-run `analyze_candles.py` in a week to add both, plus each new event). Whether the fade window's resting-order queue is capturable at scale is unproven — measured maker capture is what *incumbent* makers earned; your fills compete with theirs. Published-edge decay (§1.8) applies to this document too: the weekly cycle is exactly the kind of pattern that Kalshi's professionalizing MM ecosystem could compress within seasons, which is why the pod ships paper-first with a kill criterion. Nothing here measured LPGA/LIV/Champions markets (thinner but plausibly softer — same scripts run on them directly). And the standard caveat: this is research, not financial advice; position limits and eligibility are governed by each series' rulebook, which should be read before live trading.

## 5. → Pod proposal: see `P-015_Golf_Pod_Spec.md`

---

## Appendix A — Key sources

Academic/industry (verified by adversarial panel): Shmanske 2005, *J. Econ & Finance* 29(3) (springer.com/article/10.1007/BF02761583); Newall & Cortis 2021, *Risks* 9(1):22 (mdpi.com/2227-9091/9/1/22); Metsola 2010, *J. Prediction Markets* (exchange vs bookmaker longshot gap); Clegg & Cartlidge, *Int. J. Forecasting* correction (arxiv.org/html/2306.01740v3, edge decay); DataGolf: fav-longshot-not-a-bias, analyzing-betting-odds, how-sharp-are-bookmakers (+ part-2), predictive-model-methodology, betting-results-2021; Whelan (UCD) Kalshi FLB working paper (~300k contracts); Kalshi fee schedule (help.kalshi.com, confirmed via API series metadata).

Original data: Kalshi Trade API v2, pulled 2026-07-19 — `settled_markets.json`, `winner_markets.json`, `candles.jsonl` (15,517 markets), `winner_candles.jsonl` (3,244), `trades.jsonl` (170,581 trades), analysis scripts `collect_*.py`, `analyze_candles.py`, `series_summary.json`.

## Appendix B — Fee math reference

Taker fee = ceil(0.07 × C × P × (1−P)) per Kalshi's schedule; maker fee = 0 on `quadratic` series (all golf props), ~¼ taker rate on `quadratic_with_maker_fees` series (winner markets). At P=10¢: taker 0.63¢; P=20¢: 1.12¢; P=30¢: 1.47¢; P=50¢: 1.75¢. Break-even edge for a taker at 20¢ ≈ fee + half-spread ≈ 1.1 + 1.0–1.5 ≈ **2.1–2.6¢** — comfortably below the +5.3¢ measured pre-tournament edge; zero for makers, against +8¢ measured fade capture.
