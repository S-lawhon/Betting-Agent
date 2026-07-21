# Live (In-Play) Betting Agent — Deep Research Report

**Date:** 2026-07-19
**Method:** Two-pass multi-agent deep research (207 agents total). Pass 1: global evidence on in-play market efficiency — 19 sources fetched, 94 claims extracted, top 25 adversarially verified by independent 3-vote panels (22 confirmed, 3 refuted). Pass 2: US venue landscape — search/fetch completed (~115 claims extracted), but 53 verification agents were killed by a session usage limit, so most pass-2 venue claims are **extracted-but-unverified** (flagged throughout). Six pass-2 claims were fully verified before the limit hit.

---

## Verdict up front

**The thesis is half right, and the tradable half is not the half you proposed.**

- ✅ **Confirmed:** in-play markets really do misprice around game events. Underreaction to expected events and overreaction to surprising ones is documented in peer-reviewed studies on Betfair soccer and cricket, and — critically — the same underreaction signature was just confirmed (3-0 verification) on **Kalshi NBA in-play markets in the 2025-26 season**. The inefficiency is real, current, and on a venue you can legally trade.
- ❌ **Refuted:** the "fast agent snipes momentary mispricings" version. On the one modern, US-legal, directly-on-point study, the drift is **not profitable after transaction costs** — executable (cross-the-spread) returns were negative at *every* gap threshold. And structurally, the speed race is unwinnable for retail: bet delays, event suspensions with order-book clearing, and courtsiders/official-feed operators who are seconds ahead of any screen.
- 🟡 **The surviving angle:** every thread of evidence — the new Kalshi NBA study, a 2026 Kalshi favorite-longshot-bias study, and three of our own internal research projects (MLB, tennis, kalshi-ev-map) — converges on the same conclusion: **the taker loses to the spread+fee; the maker side is where documented positive returns live.** The viable project is not a momentum sniper. It is a **live market-making pod**: quote two-sided (or one-sided, bias-aware) markets around your own live win-probability model, earn the spread and the behavioral biases of in-play takers, and manage adverse selection around scoring events.

**Recommendation: CONDITIONAL BUILD** — as a paper-first *live maker pod* on Kalshi MLB (extending existing infrastructure), not as a standalone momentum-sniping agent. Details and pilot design in §7.

---

## 1. Is in-play mispricing real? (Pass 1, verified)

The academic literature is genuinely contested, but the verified net of it:

**Yes — conditionally.** Two independent peer-reviewed studies on Betfair tick data (verified 3-0 against full texts):

- **Choi & Hui (JEBO 2014)** — 2,017 matches, second-by-second data: bettors *underreact* to expected/moderately surprising goals and *overreact* to highly surprising ones (e.g., underdog goals). A strategy conditioned on surprise earned **~2.79% net of commission at +2 minutes** after the goal, decaying to 1.85% at +3 min and insignificance by +6 min. The bias decays ~40%/minute — ~90% gone by minute 5.
- **Angelini, De Angelis & Singleton (IJF 2022)** — 1,004 EPL matches: mispricing significant up to 5 minutes post-goal, strongest ~20 seconds after, explained by reverse favorite-longshot bias, persisting out-of-sample into 2019/20.

**But the naive version is refuted.** Croxson & Reade (Economic Journal 2014), on real order-book quotes (spread included), found goal news incorporated essentially immediately — *unconditional* back-after-goal and lay-after-goal strategies were strictly unprofitable. Only surprise-conditioned or bias-conditioned strategies show documented edge. "Prices always drift after events" is false.

**Strongest pro-thesis result:** ODI cricket on Betfair (Norton, Gray & Faff, JBF 2015). Market overreacts to first-innings wickets; backing the batting team *restricted to 40-60% win-probability states* returned **20.8% net of 5% commission** (300 bets, 1% significance, out-of-sample). A ball-by-ball Monte Carlo model traded on model-market divergence returned 7-36% across variants. Cautions: the *unrestricted* version lost 12.3%; execution around wicket-fall suspensions was never modeled; data ends 2012; thin market.

**Methodological warning (verified 3-0):** in tennis, 40 backtested betting rules that individually looked profitable collapsed under data-snooping correction (Applied Economics 2018). Most published "edges" are selection artifacts. Any candidate strategy must survive multiple-comparison correction and live execution.

**Key modern caveat:** the three headline profitability numbers above all rest on 2006-2014 Betfair data. In-play markets are far more algorithmic now. The only 2026 academic attempt to beat Betfair in-play soccer (Bristol preprint, arXiv 2605.16066) matched but did not beat the exchange on accuracy (70.2% vs 70.6%), and its headline profit claim (4.5% ROI, Sharpe 5.94) was **refuted 0-3** in our verification — profits appeared only under Kelly staking at last-traded (not executable) prices on 140 matches; unit staking lost money.

## 2. The speed race is structurally unwinnable (Pass 1, verified 3-0)

Venues have engineered latency arbitrage away:

- **Betfair suspends in-play markets at every material event** (goal, penalty, red card, wicket) and **cancels the entire unmatched order book** — odds reopen already re-priced. Suspension lasts ~2 minutes around soccer goals.
- **Bet-confirmation delays ("last look"):** Betfair in-play delay ~5-10s (recently a dynamic ~9s average for aggressive orders; passive orders being tested with *no* delay — note the asymmetry favoring makers). US books measured at 1.4-2.0s median (FanDuel/DraftKings/BetMGM, 2025-26), industry-described 1-5s holds; the operator re-prices after seeing the event, before accepting your bet.
- **Broadcast latency:** anyone on TV/stream is 5-20s behind the venue (up to ~45-60s OTT). Official feeds (Sportradar/Genius) deliver at 0.5-1.5s; courtsiding is legal in the UK (UKGC's explicit position) and that fastest tier is professionally occupied. A first-hand Bet Angel forum account of tennis courtsiding: even 3-4 seconds ahead of the umpire's scoring, the operator failed to make it pay.

**Implication (and the one piece of good news):** the documented edges live at the **2-5 minute** timescale, not milliseconds — precisely the region the venues' defenses permit. You don't need co-location, paid feeds, or courtsiders. A free/delayed data feed is sufficient for the only strategies that ever worked. The arms race you'd lose is one you don't need to enter.

## 3. The decisive question: US venue landscape (Pass 2)

⚠️ *Verification status: the six claims marked ✅ were confirmed 3-0 (or 2-1) before the usage limit killed the verifier fleet; everything else in this section is extracted from fetched sources but unverified. Treat specifics (dates, fee numbers, state counts) as probably-right-but-check.*

### Offshore exchanges: no legal route. Full stop.
- Betfair is inaccessible to US persons; even the old NV/DE/NJ TVG-brand route no longer offers exchange/lay functionality (the NJ exchange was horse-racing-only and near-dead).
- Matchbook explicitly restricts US customers; Smarkets only ever had a Colorado foothold; the broker routes (BetInAsia, Asianconnect, etc.) all restrict US persons and lost exchange access anyway.
- **Everything in §1 was documented on venues you cannot legally trade.** The entire build case therefore rests on whether the same inefficiencies exist on US venues.

### Kalshi — the anchor venue, and the venue with the on-point study
- ✅ **Verified 3-0:** a June 2026 academic study (arXiv 2606.07811) analyzed live NBA winner contracts on Kalshi at 1-minute frequency: **1,438 games, 2,876 contracts, 409k contract-minutes (Apr 2025-May 2026)**. Kalshi in-play markets are real, actively traded, with observable books.
- ✅ **Verified 3-0:** Kalshi in-play NBA prices **systematically underreact**: a 1-minute benchmark win-probability move is matched only ~**0.64-for-1** by the Kalshi midpoint (0.62 in Q4, 0.51 in clutch time). The same behavioral signature as Betfair soccer 2006-2014, alive on a US venue in 2026.
- ✅ **Verified 3-0, and this is the kill-shot for the taker thesis:** trading that drift is **unprofitable after costs**. Midpoint returns were positive (0.39-0.87% over 5 minutes, rising with gap size) but **executable returns (buy ask / exit bid) were negative at every gap threshold** — including -0.36% even for high-quality gaps ≥ 200bp. The bid-ask spread absorbs the entire edge. (A companion claim that the drift was exploitably predictable was refuted 1-2.)
- Also noteworthy from the same study (unverified extraction): Kalshi's in-play Brier score equals the authors' benchmark model (0.164) — the market is roughly as *accurate* as a good model; the inefficiency is in *speed of updating*, not level.
- ✅ **Verified:** fee structure — taker `roundup(0.07·C·P·(1−P))` (~1.75¢/contract at P=0.50, i.e. ~3.5¢ round-trip if both legs taker — this alone eats a 2-3% edge); maker orders **free on most markets**, designated series pay 1/4 the taker rate (~0.44¢ max). No settlement fee — **settling instead of trading out avoids the second fee leg entirely.**
- A separate 2026 academic study of Kalshi (2021-Apr 2025 sample, not in-play-specific; unverified): strong favorite-longshot bias (sub-10¢ contracts lose >60%; >50¢ contracts earn small positive returns), **maker after-fee return +2.6% per contract on ≥50¢ contracts**, and thin books (example: $33.60 available at best ask in a CPI market) cited as why it hasn't been competed away.
- Practitioner observations (unverified, blog-quality): single scoring plays move Kalshi in-play prices 15-20¢ within a minute; stale quotes persist during dead time between plays; API supports automated in-play trading (WebSocket order-lifecycle channel added Feb 2026, per-market maker/taker fee rates queryable, token-based rate limits with burst allowance).

### Polymarket — newly US-legal, and structurally interesting
- Acquired QCEX (CFTC-licensed DCM + clearinghouse) for $112M, July 2025 (✅ verified 3-0). US access relaunched **December 2, 2025** via a CFTC-regulated app; available in 49 states (Nevada excluded); iOS app fully open since May 2026 (unverified).
- Supports **in-play trading** (enter/exit during live NBA/MLB/NFL/NHL games), though not micro-market granularity. Sports are now ~63% of Polymarket trades (unverified).
- **Fee structure is maker-favorable:** taker pays `t·C·p·(1−p)`; **makers receive a rebate equal to 25% of the taker fee** — resting-order flow is net-*positive* on fees (unverified but consistent across sources).
- Risks: several states (AZ, CT, IL, MA, MI, TN, WI) have issued cease-and-desist or enforcement actions despite federal approval; Minnesota banned prediction markets outright (May 2026). Whether the US app exposes a full trading API (vs. app-only access) went unresolved — a critical open item.

### The new CFTC cohort (all unverified, all June 2026)
- **ProphetX:** DCM + DCO approval June 16, 2026, relaunched 5 days later, 49 states. CLOB + RFQ for parlays, 2% of net winnings, liquidity from third-party institutional market makers (no house trading arm). In-play offering unconfirmed.
- **Novig:** Ludlow Exchange DCM approval June 2026; targeting all-50-state rollout summer 2026; self-reports $5B+ cumulative volume; 1-4% commission on maker-side activity. **No mention of live/in-play markets in any fetched source.**
- **Sporttrade:** the one US venue purpose-built for in-play *trading* **shut down its state-licensed operations May 25, 2026** and has a pending CFTC DCM/DCO application with no relaunch date. Dead as a venue today.
- Liquidity concentration (mid-June 2026 snapshot, unverified): Kalshi $3.38B weekend volume vs Polymarket $1.41B vs Rothera $131M — new entrants start with thin books.

### US sportsbooks: not a venue for this project
DraftKings/FanDuel/BetMGM live betting is a dead end for an automated winner: limiting sharp bettors is open policy (BetMGM limits ~1% of MA customers; Fanatics says nearly half its limited customers were *net losers* at the time — they limit on behavioral sharpness signals, so a bot gets limited before it's even profitable). High live hold, multi-second last-look delays, no regulator has barred limiting. Ignore this channel.

## 4. Reconciliation with our own internal research

This is where the external evidence gets its teeth — three independent internal projects already tested pieces of this thesis on Kalshi:

| Internal finding (Jul 2026) | External corroboration |
|---|---|
| kalshi-ev-map kill-test: MLB totals/spreads **calibrated in-play**; all naive bias strategies lose net of fees (start-anchored, 4.4k markets) | NBA study: market Brier = benchmark model Brier; taker strategies negative after costs |
| Live NBA underreaction ~0.64-for-1 doesn't survive bid-ask + fees as arbitrage (per pivot-plan research) | Same paper, now formally verified 3-0, with executable returns negative at every threshold |
| Tennis: prop mids unbiased; taker sim lost 11/12 cells; surviving edges = **fee-free prop market-making at fair ±2-3¢**, FLB on deep books, stale-quote scraps | Kalshi FLB study: maker +2.6%/contract on ≥50¢; blogs: stale in-play quotes between plays |
| MLB props: maker-side correlation angle is the edge | Same maker-side conclusion, different market |

Four independent lines of evidence (two external-academic, two internal-empirical), one conclusion: **on Kalshi, the spread+fee kills takers, and the documented positive returns sit with resting orders.**

## 5. Does "constant remodeling is a moat" hold?

Partially — with an important reframe. The moat is real but it is not *forecast accuracy*: the Kalshi NBA market is already as accurate as a good benchmark model, and the 2026 Bristol model couldn't beat Betfair either. Nobody small out-forecasts the market level. What the evidence supports:

1. **The market's weakness is update speed, not level** (0.64-for-1 underreaction; stale quotes between plays). A live model's job is to know *fair* continuously so your quotes are on the right side while the crowd catches up.
2. **The moat for a maker is quote management** — re-pricing your resting orders on every game event faster/better than other makers, and pulling quotes before predictable event risk (the at-bat resolving, the possession ending). That *is* a "constant remodeling" moat, just pointed at your own quotes instead of at sniping others.
3. **Per-sport modeling depth compounds** — the cricket result (edge only in 40-60% WP states) and our tennis mixture-model work show the payoff of granular state modeling is knowing *when* to quote tight vs. wide vs. not at all.

For a solo operator on a thin, retail-dominated, fee-advantaged-for-makers venue, that moat is plausible. On liquid global markets against professional syndicates, it is not.

## 6. Venue ranking for a small automated in-play strategy

1. **Kalshi** — only venue with proven in-play liquidity, a mature trading API, documented behavioral inefficiency, zero-to-tiny maker fees, no settlement fee, and (decisively) our existing engine, fee models, and paper infrastructure. Pilot here.
2. **Polymarket US** — structurally attractive (maker *rebate*, in-play supported, 49 states) but API access for US automated trading unconfirmed and state-level legal risk unresolved. Research target #2; also the natural second leg for cross-venue in-play basis monitoring (our earlier work found pre-game basis within the fee corridor; in-play basis is untested).
3. **ProphetX / Novig** — too new, in-play offering unconfirmed, books likely thin. Re-check in 3-6 months.
4. **Sporttrade** — currently shut down; revisit if/when CFTC relaunch happens.
5. **US sportsbooks** — no (limiting).
6. **Offshore exchanges** — no legal route.

## 7. Recommendation and pilot design

**CONDITIONAL BUILD.** Do not build the momentum-sniping taker agent — the direct evidence against it (verified, current, on-venue) is about as clean as negative evidence gets. Build the **live maker pod** pilot, paper-first, as a new pod in the existing engine:

**Pilot: "P-015 Live Maker" — Kalshi MLB moneyline (+ totals), in season now.**

- **Core loop:** live win-probability model (we have elo/moneyline models; extend with a game-state layer — base/out/inning/score for MLB) → continuous fair value → post two-sided resting quotes at fair ± k¢ (k calibrated per liquidity), skewed by known biases (FLB: shade quotes to be net seller of longshots / buyer of favorites) → **pull/re-price quotes on event risk** (pitch/at-bat resolution windows) → prefer settlement over trading out (saves the exit fee).
- **What paper trading must measure (the real unknowns):**
  1. **Fill rate** on resting quotes in-game (are there enough takers at our prices?)
  2. **Adverse selection / markouts** — mid-price 1/5/15 min after each fill. This is the pilot's central risk: our fills may be concentrated at exactly the moments someone faster knew more. Paper fills must be modeled pessimistically (assume fill only when the market trades *through* the quote, not at touch).
  3. Effective spread capture net of maker fee vs. markout losses.
- **Success gate (pre-registered, in the spirit of the CLV discipline):** positive net markout-adjusted P&L over ≥500 fills, robust to excluding the best day, before any real-money consideration. Kill criteria defined up front — the tennis data-snooping result says we must not rummage for the subset that worked.
- **Infrastructure (modest, mostly exists):** Kalshi WebSocket market + order channels; free MLB game-state feed (MLB StatsAPI; a few seconds' delay is acceptable because we quote, not race — but quote-pulling logic must assume we're 5-15s behind reality); quoting engine with global kill-switch; runs alongside existing pods on the droplet.
- **Explicitly out of scope for the pilot:** NBA/NFL (out of season), Polymarket execution (until API access confirmed), any taker-side "momentum" logic, any real money.

**Parallel cheap research tasks:** (a) confirm whether the July 7, 2026 Kalshi fee schedule puts MLB series on the maker-fee list (changes economics by 0.44¢/contract); (b) determine Polymarket US API availability; (c) log in-play Kalshi-vs-Polymarket basis for a few weeks — if the pre-game fee-corridor result breaks down in-play, that's a second strategy.

## 8. What would change the verdict

- **Toward full build/scale:** paper maker pilot shows positive markout-adjusted capture; Polymarket API confirmed (maker rebate makes marginal economics ~1.75-2.2¢/contract better than Kalshi at midprice).
- **Toward no-build:** fills are rare (no taker flow at fair±2¢ in-game), or markouts show systematic pick-off around scoring events that quote-pulling can't fix — that would mean Kalshi in-play flow is sharper than the retail-crowd picture suggests, and the pod dies cheaply, as designed.

## Appendix: verification ledger

**Pass 1 (fully verified):** 22 confirmed / 3 refuted / 0 unverified. Refuted: (i) "longshot late goals → underreaction" specific claim (1-2); (ii) generalization of tennis data-snooping result to all sports markets (1-2); (iii) Bristol 2026 "4.5% ROI, Sharpe 5.94 on Betfair in-play" (0-3 — Kelly-only, last-traded prices, 140 matches).

**Pass 2 (partially verified due to session-limit failures):** confirmed 3-0 — Kalshi NBA in-play dataset exists and is liquid; 0.64-for-1 underreaction; negative executable returns at all thresholds; Kalshi maker-fee structure; Polymarket-QCEX acquisition. Confirmed 2-1 — Kalshi taker fee formula/magnitude. Refuted 1-2 — "5-minute drift is an exploitable divergence signal." Everything else in §3: unverified extraction.

**Primary sources:** Choi & Hui JEBO 2014 (sciencedirect S0167268114000481); Croxson & Reade EJ 2014; Norton, Gray & Faff JBF 2015 (UQ eSpace); Angelini, De Angelis & Singleton IJF 2022; Applied Economics 2018 tennis reality-check; Meier/Flepp/Franck COVID ghost-games (JSE); UKGC in-play guidance; arXiv 2605.16066 (Bristol, refuted profit claim); **arXiv 2606.07811 (Kalshi NBA in-play, June 2026 — the pivotal study)**; Kalshi fee schedule PDF; PRNewswire QCEX release; CNBC Novig/ProphetX coverage; CBS Sports prediction-market legality tracker; Sporttrade shutdown coverage; MA Gaming Commission limiting roundtable coverage; Bet Angel courtsiding forum account.
