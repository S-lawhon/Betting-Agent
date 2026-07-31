# Vendor evaluation: BettingIsCool "Pinnacle Data API"

**Date:** 2026-07-30
**URL:** https://api.bettingiscool.com/ · spec at `/openapi.json` · docs at `/docs`
**Verdict:** conditional buy, €49 Starter for one month as a data-integrity probe, then €249 Enterprise only if the probe reconciles. Do not wire it into any live pod path.

---

## 1. What it actually is

A single-vendor, unofficial Pinnacle mirror. Not a multi-book aggregator. One sportsbook, 5 years deep.

| | |
|---|---|
| Books | Pinnacle only |
| Prematch history | Jan 2021 to present |
| Live / in-play history | Mar 2026 to present |
| Volume | 2.4B odds rows, 2.7M fixtures, 463 GB |
| Auth | `X-API-Key` header |
| Quota | 1 token + 1 token per row returned; daily cap, resets midnight UTC |
| Devig | Precomputed `todds1/todds0/todds2` by the log method from football-data's "Wisdom of the Crowd" p9 |

Endpoints: `/api/sports`, `/leagues`, `/periods`, `/fixtures`, `/odds`, `/opening`, `/closing`, `/results`, `/clv`, and a `/specials/*` family (fixtures, odds, opening, closing, results, outrights, clv).

Tiers, and this is the part that decides the purchase:

| Tier | € / mo | Tokens / day | Delay | Gets `/clv` | Gets `/specials/*` |
|---|---|---|---|---|---|
| Starter | 49 | 500k | 15 min | no | no |
| Pro | 149 | 5M | real-time | yes | no |
| Enterprise | 249 | 50M | real-time | yes | **yes** |
| Enterprise+ | 599 | 500M | real-time | yes | yes |

Crypto pays 10% less. Annual pays 25% less. There is no free trial and no published SLA. Cancellation runs through a Stripe portal.

---

## 2. Coverage, verified against the live `/api/coverage` endpoint

Eleven sports are actively updating as of 2026-07-29: Baseball, Tennis, Soccer, Basketball, Football, Hockey, Handball, Volleyball, E Sports, MMA, Boxing.

Standard-market row counts for the ones we care about:

| Sport | Standard rows | Specials rows | Specials markets |
|---|---|---|---|
| Baseball | 93,605,076 | **6,619,750** | ES, GP, **PP** |
| Tennis | 167,321,756 | 1,019 | PP |
| Basketball | 532,494,312 | 3,556,794 | GP, PP |
| Football | 31,278,178 | 9,125 | GP, PP |
| **Golf** | 771,827 | **none** | — |

### Golf is dead on this feed

Golf's published coverage window is **Jan 2021 to Jun 2025**, and Golf does not appear in `last_seen` at all. There are zero golf specials, so no outrights, no top-N, no round-leader markets.

Our only two live or green-lit pods are golf. P-017 and P-022 get nothing from this vendor. Anyone who buys it expecting to feed the golf book will be disappointed thirteen months into a stale table.

---

## 3. What it does not fix

**P-018 stays blocked.** The missing input there is Kalshi's in-play book, which `book_capture` has zero ticks of. Pinnacle in-play history since Mar 2026 does not substitute for the venue we would be quoting into. The +9.09¢ headline remains unadjudicated for the same reason it was yesterday.

**P-021 stays dead.** The R3 kill found KXMLBTOTAL is as sharp as Pinnacle, Brier 0.2477 against 0.2484, gap-to-outcome t=0.23. More Pinnacle history does not move a tie. The lead-lag at b=+0.91 was real and too small for taker fees, and that arithmetic is unchanged by a better feed.

**It cannot replace the P-001 signal reference.** `live_odds_poller.get_consensus_probs()` builds a multi-book no-vig consensus with Pinnacle at 2x weight. This vendor supplies exactly one of those books. It is a leg, not a consensus.

---

## 4. What it does fix, ranked

### 4.1 The P-001 CLV benchmark, and the bug class that ruined 86% of it

`src/clv_close.py` hits The Odds API's historical snapshot endpoint at a guessed timestamp, `regions=eu`, `markets=h2h`, then fuzzy-matches team names inside a ±3h window. Eight distinguishable failure reasons are enumerated in that module, and three of them exist only because the snapshot might not contain Pinnacle at all: `NO_PINNACLE`, `PINN_NO_H2H`, `PINN_NAME_MISMATCH`.

On a Pinnacle-only feed those three cannot fire. Better, the flow becomes `/api/fixtures` over a narrow `starts_from`/`starts_to` window to get an `event_id`, then `/api/closing?event_id=` for the real closing line with `todds` and scores attached. Cost is about two tokens.

The July finding was that 86% of P-001's CLV rows were priced off a different day's game, because MLB series repeat identical team names and the matcher broke fuzzy ties by list order. Correct rows ran +7.65¢, mismatched rows +0.19¢. Matching on an integer `event_id` from an explicit start-time window removes that failure mode instead of patching it.

One warning: `/api/clv` does its own fuzzy name matching on `runner_home`/`runner_away`. That is the same trap we just climbed out of. Use `/api/fixtures` plus `/api/closing` and do the matching ourselves.

### 4.2 A sharp external reference for MLB player props, which we had written off as nonexistent

The R2 record says, in writing, "sharp prop books barely exist; exclude DFS." That premise is false. Pinnacle carries 6.6M baseball specials rows with player props, game props, and event specials, with opening, closing, devigged, and result endpoints on all of them.

This lands directly on the strongest unshelved number in the fund. From `mlb_props_research/PHASE3_REPORT.md`, addendum 2:

> Mean +5.51¢/contract, clustered SE 1.19¢, 95% CI +3.18¢ to +7.84¢, 59 qualifying game-days, buy-at-ask, ≤30m window, price 0.15–0.45.

The informed-flow objection was tested and refuted. The mechanism is inattention, strongest in the most neglected books. Two things remain open, and this feed speaks to both.

First, the report's own caveat: "Two windows within one 2026 season; no cross-season validation." Kalshi trade history only reaches back to about 2026-05-20, so the Kalshi-side edge cannot be cross-validated at all right now. Pinnacle's devigged prop closing prices plus results go back five seasons. That lets us test the *underlying* claim, that cheap batter-hits props in the 15–45% band are systematically underpriced against realized rates, on five years of data instead of one summer.

Second, the current rule is "lift any ask in 0.15–0.45 in an actively-trading market." That is a price-band filter with no view. A Pinnacle devigged fair per prop turns it into a disagreement filter: buy only where the sharp book's fair sits meaningfully above Kalshi's ask. Daily net SD is 9.13¢ and 27% of days are negative. A filter that removes the trades where Pinnacle agrees with Kalshi should cut that variance whether or not it raises the mean, and it converts an inattention story into the P-001 archetype, which is the one archetype with a survival record.

Note the tier consequence. Specials are Enterprise only. A €49 Starter subscription cannot answer this question.

### 4.3 Tick-level anchors, which kills a measurement artifact we have hit twice

`full_history=1` returns every Pinnacle movement with its own timestamp and `line_id`. Two of our kills were contaminated by anchor staleness. Make-cut's "48h anchor" was a median 68h-old price and produced a pure +9.5¢ artifact. P-023c's staleness bias ran the other direction, which is why stale is simply wrong rather than conservatively wrong.

With a full tick stream you price the anchor at the exact Kalshi decision timestamp. That is a methodology upgrade, not just more rows.

It also bears on P-024's mandatory protocol, which requires lag-aligning the sharp reference at t−10min to compensate for The Odds API's Pinnacle-eu delay. On Pro and above this feed is real-time, so the alignment can be measured rather than assumed, and history runs to 2021 instead of The Odds API's May 2023 for `totals_1st_5_innings`.

### 4.4 Cheaper historical pulls than what we pay now

The Odds API charges 10 credits per market per region on historical endpoints, on a 5M-credit plan at $119/mo. Here a closing-line lookup is roughly 2 tokens and Enterprise carries 50M tokens/day. Full-history pulls are the expensive case, since cost tracks rows returned. Poll with an advancing `since` and each follow-up call costs near 1 token.

---

## 5. Risks, stated plainly

**Devig method mismatch.** Their `todds` uses the log method. Our `src/devig.py` offers multiplicative and Shin-style power, and picks between them deliberately: multiplicative in the 35–65% band where the MLB edge lives, power for longshot-heavy golf fields. Mixing their devig into a study benchmarked on ours would shift measured edge silently. **Use raw `odds1`/`odds2` and devig in-house.** Treat `todds` as a cross-check only.

**Key-man and ToS risk.** This is one operator scraping Pinnacle. No SLA, no company behind it that we can see, no typed response schema in the OpenAPI spec, so field discovery is empirical. It could vanish. That is survivable for research and for CLV benchmarking. It is not survivable inside a live quoting path, which is the reason for the "do not wire it in" line at the top.

**No trial.** €49 is the cheapest possible look, and it excludes the props endpoints that carry most of the value.

**Golf.** Covered above. Worth repeating because it is the gap most likely to be missed by someone skimming the sport list, which shows 44 sports and a golf checkmark.

---

## 6. Recommendation and sequencing

**Step 0, before any money moves.** Write the pre-registration. Our own standing rule from the 07-26 run is that a hypothesis gets a hard pre-declaration before anyone sees numbers, and this vendor makes it cheap to look at numbers first. Declare the props test's screens, windows, clustering unit, and kill triggers now.

**Step 1, €49 Starter, one month, and it buys exactly one thing: a data-integrity verdict.** We already hold Pinnacle closing lines for 650 P-001 bets, captured independently through The Odds API. Pull the same events from `/api/fixtures` plus `/api/closing` and reconcile. Three outcomes matter:

- Do the closing lines agree with what we captured, on the rows the matcher got right?
- On the 86% that were priced off the wrong day, does `event_id` matching recover the correct game?
- Does re-deriving P-001's CLV on correctly matched rows still land near +7.65¢?

That last question is worth the €49 on its own. P-001 scenario D currently counts 0 of 200 admissible post-fix rows and the gate's clock is a placement rate falling 66.2 → 53.5 → 36.0/wk. A retroactive rebuild of the CLV benchmark does not have to wait for that clock.

**Step 2, upgrade to Enterprise for one month only if step 1 reconciles.** Then run the props test: five seasons of Pinnacle devigged prop closing against realized outcomes, and the disagreement filter applied to the existing Kalshi sample. Cancel at month end either way. The question is answered in a month or it is not answered by this vendor.

**Cheapest kill for step 2.** If Pinnacle's devigged prop closing shows no systematic gap against realized hit rates in the 15–45% band across five seasons, then Kalshi's cheap-YES bias is not something a sharp reference can locate, the disagreement filter has nothing to filter on, and the €249 stops after one month.

**What not to do.** Do not cancel The Odds API. It supplies the multi-book consensus that P-001's signal is built on, and this vendor cannot replace it. Do not point any golf pod at this feed. Do not use `/api/clv`.
