# Kalshi MLB Props & Sub-Market Efficiency — Deep Research Report

**Date:** 2026-07-18
**Question:** Are MLB sub-markets (player/game props and other non-moneyline markets) on Kalshi less efficiently priced than moneyline markets, and do they therefore offer higher +EV potential?
**Method:** Multi-agent deep research (101 agents): 5 search angles → 19 sources fetched → 90 claims extracted → top 25 adversarially verified with 3-vote panels (23 confirmed, 2 refuted) → synthesis. Supplemented with two independent same-day pulls of Kalshi's live order books via the public trade API.

---

## Verdict

**Partially viable — build, but small and specific.**

The hypothesis splits into two versions with opposite outcomes:

- **The broad version ("everything besides moneyline is soft") is refuted.** Team-level derivatives — game totals and run-line spreads — trade nearly as tight as the moneyline (1¢ median spreads, seven-figure per-game dollar volume). There is no structural softness to harvest there.
- **The narrow version ("player props and micro-markets are soft") is confirmed** — but capturability at size is the binding constraint. Props show 3–37¢ spreads, only ~50% of books two-sided, and top-of-book depth of ~25–225 contracts. The inefficiency is real; the dollars available per mispricing are small today.
- **The most differentiated and defensible angle is the correlation/joint-distribution thesis** (your second idea). It is structurally exploitable on Kalshi in a way it is not at any sportsbook, because Kalshi prices every same-game contract independently, runs no same-game-parlay repricing engine, and charges **zero maker fees on all prop series**.

The existing CLV-validated moneyline model should remain the core: the only MLB-specific peer-reviewed inefficiency result found (Simon, *Management Science* 2024) is about **moneyline** line-movement overreaction, not props.

---

## 1. What Kalshi actually offers (verified against live API)

~204 baseball-related series (~171 MLB-specific), including per-game:

- **Team/game derivatives:** totals (KXMLBTOTAL), run-line spreads (KXMLBSPREAD), team totals, F3/F5/F7 winners, F5 spreads and totals, run-in-first-inning (KXMLBRFI), extra innings, series winner/exact result/total games.
- **Player props:** strikeouts (KXMLBKS), home runs (KXMLBHR), hits (KXMLBHIT), total bases (KXMLBTB), hits+runs+RBIs (KXMLBHRR), stolen bases (KXMLBSB), RBIs, outs recorded, next HR.
- **Futures/season:** divisions, pennants, World Series, per-team win totals (all 30 teams), awards, stat leaders, streaks, player debut/next team/manager markets.

Surface area is not the constraint. Liquidity is.

## 2. Live microstructure snapshot (2026-07-18, markets expiring ≤36h)

| Series | Class | Volume | Avg spread | Quoted (2-sided) | Median top depth |
|---|---|---|---|---|---|
| KXMLBGAME | Moneyline | $15.3M | 1.0¢ | 100% | ~138,000 |
| KXMLBTOTAL | Game total | $5.6M | 2.7¢ | 89% | ~3,200 |
| KXMLBSPREAD | Run spread | $2.5M | 4.7¢ | 96% | ~3,100 |
| KXMLBRFI | 1st-inning run | $211K | 1.4¢ | 100% | ~1,200 |
| KXMLBHR | Player HR | $759K | 3.5¢ | 57% | ~930 |
| KXMLBKS | Pitcher Ks | $512K | 6.4¢ | 55% | ~480 |
| KXMLBTEAMTOTAL | Team total | $270K | 11.6¢ | 90% | ~99 |
| KXMLBHIT | Player hits | $110K | 12.0¢ | 75% | 225 |
| KXMLBTB | Total bases | $93K | 27.4¢ | 78% | 225 |
| KXMLBF5 | First 5 winner | $81K | 32.8¢ | 100% | 91 |
| KXMLBHRR | Hits+Runs+RBIs | $77K | 36.8¢ | 71% | 225 |
| KXMLBSB | Stolen bases | $20K | 29.4¢ | 56% | 6 |

Corroborating detail from the verified research pass: a single game's totals event carried $1.35M across 11 strikes while the same game's RBI prop event had **$592.74 across 42 markets** — a 2,000x liquidity gap inside one game. Note: Kalshi "volume" counts $1-notional contracts including in-game trading, so per-game dollar figures overstate pregame resting liquidity.

**Reading:** the moneyline is ~2.7x the volume of all sub-markets combined. Totals/spreads/RFI are institutionally tight. Player props exhibit exactly the microstructure signature of inattention — wide, thin, half-unquoted — which proves friction and absence of sharp flow, but not (by itself) measured mispricing vs. true probabilities.

## 3. Fees — the single most strategy-relevant structural fact

All verified against the July 2026 fee schedule, Kalshi's help pages, the API's per-series fee fields, and a CFTC filing:

- **Taker fee is uniform everywhere:** ⌈0.07 × C × P × (1−P)⌉, fee_multiplier = 1 on every MLB series. No prop-specific fee tier. Fee drag peaks at 50¢ (coin-flip moneylines ≈ 1.75¢/contract round-number) and shrinks toward the extremes — longshot props pay proportionally less.
- **Maker-fee asymmetry favors props:** the moneyline/championship series (KXMLBGAME, KXMLB, KXMLBAL, KXMLBNL) are `quadratic_with_maker_fees` — resting orders pay 0.0175 × C × P × (1−P). **Every MLB prop/total/spread series is plain `quadratic` — resting orders pay nothing.**
- **Designated market-maker program covers MLB mainline (98%-of-hour quoting obligations) but no MLB prop series.** Double-edged: prop mispricings persist longer (nobody obligated to arb them away — consistent with ~48% of strikeout books being unquoted), but a prop strategy is effectively volunteering to be the market maker, earning spread while bearing adverse-selection risk against lineup/scratch/weather news. (Confidence: medium — 2-1 verification vote; program list can change.)

## 4. What the academic literature actually says

- **No direct academic evidence that props are softer than moneylines.** The two most relevant papers both study moneylines only.
- **arXiv 1910.08858** (the oft-cited "sports betting inefficiency" paper): its edge comes from **cross-book price dispersion** — line-shopping 16 sportsbooks — not from intrinsic prop softness. Transferred to this project, it supports a **Kalshi-vs-sportsbook-consensus divergence** strategy, not a props-are-soft strategy.
- **Simon, *Management Science* 70(12), 2024** (3,681 MLB games, 4 books): MLB **moneyline** markets fail weak-form efficiency — line movements are significantly negatively autocorrelated (systematic overreaction), and simple movement-based strategies backtested profitably. This is peer-reviewed, MLB-specific support for the *existing* mainline lane (and Kalshi doesn't limit winners, improving transferability — though the transfer is untested).
- **Winkelmann et al., *Journal of Sports Economics* 2024 — methodological warning:** under full market efficiency, >75% chance of at least one spuriously "significant" profitable season; a 14-season panel found inefficiencies only in isolated seasons, never persistent. **One good paper-traded prop season is not evidence.** Validation must be CLV-style leading-indicator based or multi-season.

## 5. The correlated-props angle (strongest differentiated edge)

Confidence: medium (direction mathematically certain; magnitudes are illustrative NFL examples, not measured MLB constants).

- Same-game legs are jointly mispriced under independence by material margins: a worked copula example showed the independence product at 16.0% vs. ~21.2% true joint probability (+33%); a 500-game illustrative sample showed 20.4% observed vs. 15.7% independence-implied (+30%).
- Sportsbooks know this and **over-extract** it: same-game-parlay holds run 15–25% vs. 4–5% on singles (Illinois regulator data: 18.2% parlay hold). The correlation edge is *not* capturable through sportsbook SGP products — the books reprice it away and then some.
- **Kalshi has no SGP engine.** Pitcher-K props, game totals, F5 winner, team totals, and the moneyline all trade as independent order books, yet all are functions of the same latent game state (run environment, pitcher performance, pace). A joint-distribution model can detect books that are individually near-fair but **collectively inconsistent**, and lean on whichever leg is mispriced conditional on the others — with zero maker fees and no account limiting.
- This cross-contract inconsistency detection is theoretically sound but **empirically undemonstrated** — nobody has measured how often Kalshi's MLB books imply an inconsistent joint state. That measurement is the first research deliverable of any build (see open questions).

## 6. Other +EV classes surfaced

Ranked by the synthesis across all verified claims:

1. **Pitcher strikeout props (KXMLBKS), maker-side** — the most liquid prop series (~$512K/day), fee-free resting orders, and K-modeling shares infrastructure with the win/loss model's pitcher inputs.
2. **Cross-contract correlation/consistency checks** within a single game (props × totals × F5 × moneyline).
3. **Kalshi vs. sharp sportsbook consensus divergence** — the mechanism the only relevant academic profitability result actually validated; also directly testable with the fund's existing CLV pipeline pointed at props.
4. **Newly-opened micro-markets as a quoting opportunity** — F5 books showed ~50¢ median spreads at open; quote, don't take.
5. **Not worth building:** totals/run-line models expecting soft pricing — those books are 1¢-tight.

## 7. Refuted claims (killed in verification)

- "Kalshi's fee schedule only enumerates 5 MLB series, consistent with a thin sub-market offering" — 0-3 vote; the offering is deep (~171 series).
- "A data vendor's coverage of Kalshi props implies they trade in volume worth productizing" — 1-2 vote; vendor marketing, contradicted by observed prop volumes.

## 8. Open questions (the empirical work a build would start with)

1. **Measure MLB same-game correlations on Kalshi:** how often do independently-traded books (Ks × total × F5 × ML) imply a jointly inconsistent, +EV game state? Requires building the joint model and logging per-game cross-contract implied distributions.
2. **Point the CLV pipeline at props:** do Kalshi prop prices systematically diverge from sharp sportsbook consensus (Pinnacle-style closers), and in which direction? This tests capturable softness directly instead of inferring it from spreads.
3. **Maker fill quality:** fill rates and adverse selection on resting prop orders around lineup/scratch/weather news. Zero maker fees only matter if fills come from uninformed flow rather than news pick-offs.
4. **Liquidity trajectory:** do position limits plus the pregame liquidity ramp allow a prop book to reach meaningful per-game size — and is prop depth growing (post-Robinhood-integration) fast enough that a model built now has a market within a season?

## 9. Recommended build posture

- **Keep the moneyline model as the core.** It has independent peer-reviewed support (line-overreaction) and the only deep liquidity on the exchange.
- **Props = capital-light satellite book.** Maker-only (never cross wide spreads), correlation-aware, starting with pitcher Ks. At ~25–930 contracts of top-of-book depth, a genuinely mispriced prop is worth tens of dollars, not fund-scale P&L — size expectations accordingly until depth grows 1–2 orders of magnitude.
- **Phase 0 before any model:** run the two measurement studies (open questions 1 and 2) as paper-only data collection. They reuse existing infrastructure and would convert the two medium-confidence theses (correlation edge, capturable softness) into measured quantities.

## Caveats

All liquidity figures are same-day snapshots (2026-07-18, mid-season); prop depth builds toward first pitch and the fee schedule/MM program are explicitly subject to change. Kalshi sports liquidity is growing quickly — the depth constraint could ease, or the softness could get quoted away, within months. The "props are softer" conclusion rests on microstructure evidence (spreads/depth/quoting rates), which proves inattention but not measured mispricing. Correlation-edge magnitudes (+30–33%) are illustrative NFL copula examples from a credible but non-peer-reviewed source; no source measured MLB-specific prop correlations. Simon's overreaction profits are backtested at sportsbooks; transfer to Kalshi order books is untested.

## Key sources

- Kalshi live trade API (api.elections.kalshi.com/trade-api/v2) — order books, fee types, series catalog (primary; independently replicated twice)
- [Kalshi fee schedule (7.7.26)](https://kalshi.com/docs/kalshi-fee-schedule.pdf); [Kalshi fees help page](https://help.kalshi.com/en/articles/13823805-fees); [Kalshi MM program](https://help.kalshi.com/en/articles/13823819-how-to-become-a-market-maker-on-kalshi)
- Simon (2024), "Inefficient Forecasts at the Sportsbook," *Management Science* 70(12):8583–8611
- [arXiv 1910.08858](https://arxiv.org/pdf/1910.08858) — inefficiency via cross-book dispersion, moneylines only
- Winkelmann et al. (2024), *Journal of Sports Economics* — single-season significance is expected under efficiency
- [Wizard of Odds — SGP correlation mathematics](https://wizardofodds.com/article/same-game-parlays-the-mathematics-of-correlation/); Illinois regulator parlay-hold data (18.2%)
