# 03 — Historical Validation: Calibration & Fee-Aware Backtests

**Data:** 1,005,774 settled markets (90 days to 2026-07-18) across 77 target series; pre-close candlesticks pulled for 18,152 of them (top-≤400 by volume per series). Prices used are **executable candle bid/ask closes** at fixed horizons before market close — not last-trade prints. Filters throughout the headline results: `volume ≥ 500`, `spread ≤ 5¢`, non-degenerate quotes. Reproduce: `src/pull_settled.py`, `src/pull_candles.py`, `src/calibration.py`, `src/headline_vs_props.py`.

**Two known biases, disclosed:**
1. *Volume-stratified sampling* — candles cover each series' most-traded markets; conclusions apply to the liquid head (which is where you'd trade anyway).
2. *Horizon semantics* — `close_time` on settled markets is actual trading close. For sports, T-1h quotes are typically **in-play**; T-24h is pre-game. For threshold markets that can close early on breach (MLB totals), conditioning on close time can induce outcome-dependent selection — flagged on the affected finding below.

## Pooled calibration (T-1h, executable, filtered; n=6,727)

| Bucket | n | Priced | Realized | Miscal | Signif (Wilson 95) |
|---|---|---|---|---|---|
| 0.01–0.05 | 909 | 2.8 | 3.3 | +0.5 | no |
| 0.05–0.10 | 736 | 7.2 | 7.5 | +0.3 | no |
| 0.10–0.20 | 978 | 14.5 | 19.4 | **+4.9** | yes |
| 0.20–0.30 | 800 | 24.8 | 27.8 | **+3.0** | yes |
| 0.30–0.40 | 665 | 34.7 | 38.8 | **+4.1** | yes |
| 0.40–0.60 | 1004 | ~49.6 | ~50.4 | +0.8 | no |
| 0.60–0.70 | 415 | 64.7 | 61.0 | −3.8 | no |
| 0.70–0.80 | 341 | 75.0 | 72.7 | −2.3 | no |
| 0.80–0.90 | 370 | 84.6 | 77.6 | **−7.1** | yes |
| 0.90–0.99 | 509 | ~95.4 | ~95.7 | +0.3 | no |

Shape: mid-low YES prices (10–40¢) systematically **under**priced; 80–90¢ favorites **over**priced. The extreme tails are *fine* at T-1h. Multiple-testing: 8 of 24 family×bucket cells significant at 95% vs ~1.2 expected by chance; the headline cells (props 10–20¢ +4.8, props 80–90¢ −18.5, headline 30–40¢ +6.1) survive Bonferroni at α=0.05/24 (binomial p ≈ 8×10⁻⁴, 3×10⁻⁵, 2×10⁻³).

## Family attribution (significant cells only)

- **sports_props (T-1h, in-play):** cheap side +1.6 to +4.8pts across *every* prop series (WC score/goals/assists, UFC method-of-victory, MLB totals/spreads — all realized > priced at 5–40¢); favorites −12 to −18.5pts.
- **sports_headline (T-1h):** same shape, roughly half the size (+4.0 to +6.1 cheap, −6.7 at 80–90¢).
- **weather_temp (T-24h):** the **opposite** sign — classic longshot bias. 1–5¢ brackets realize 1.3% vs 2.5% priced; 5–10¢ realize 4.1% vs 7.0%. Retail buys temperature lottery tickets; they're overpriced.
- **forex_metals (T-24h):** favorites −16 to −19pts, longshots +7pts (n≈60–85; treat as suggestive).
- **crypto_hourly (KXBTCD et al., T-1h):** no significant cells — efficiently calibrated at the 1-hour horizon. (15-min series aren't testable at these horizons; needs T-10min candles — roadmap item.)

## Strategy backtests (taker fills at executable quotes, net of fees, hold to settlement)

| Strategy | n | Avg collateral | Net ROI/trade | t | Hold |
|---|---|---|---|---|---|
| Props: buy NO vs 70–90¢ favorites (T-1h) | 135 | 22¢ | **+59.8%** | **3.2** | ~hours |
| — of which MLB totals only | 51 | ~20¢ | realized 41% vs 80¢ priced | — | ~hours |
| — of which MLB spreads only | 59 | ~20¢ | ≈0 (calibrated) | — | — |
| All sports: buy NO vs 70–90¢ favorites | 551 | 22¢ | +20.6% | 2.4 | ~hours |
| Headline only: same trade | 416 | 22¢ | +7.8% | 0.8 | — |
| Props: buy YES 5–40¢ (T-1h) | 1,808 | 20¢ | +4.7% | 1.0 | ~hours |
| Headline: buy YES 5–40¢ | 989 | 25¢ | +8.6% | 1.5 | ~hours |
| Weather: sell 1–10¢ longshots (buy NO, T-24h) | 1,322 | 97¢ | +0.8% | 1.9 | ~1 day |
| Weather sanity: buy those longshots | 1,322 | 5¢ | −55.4% | −6.8 | — |

Annualization on collateral: the weather short-tail trade at +0.8%/day ≈ triple-digit annualized **if** capacity existed — it doesn't ($30–100k/day family volume, ask-only books; realistic deployment low tens of $k). The props-favorites fade at ~+60% per multi-hour hold is the standout — but see verdict.

## The named hypothesis — verdict

**"Headline efficient, props soft" is directionally confirmed, with a sharper statement:** headline markets show the same bias signature but at roughly half the magnitude and statistically weak net-of-fees (t=0.8 on the favorites fade). Props carry the payable edge, and it is **concentrated in MLB totals** (in-play) and **broad-based cheap-longshot underpricing across every prop series tested**. Prop depth (~$600 median bid, up to $110k on top events) supports meaningful but not unlimited size — call it low-tens-of-$k/week at current volumes [estimated].

**Required kill-tests before deploying the MLB-totals fade** (it is too good — +60%/trade — to accept without attack):
1. Early-close selection audit: totals markets settle/close on breach; re-run with horizons anchored to *scheduled game end*, not market close.
2. Fill realism: T-1h in-play quotes assume you can cross 5¢-wide books mid-game for the sampled size; replay against the trades tape.
3. Out-of-window persistence: 90 days, one half-season, World-Cup-adjacent flow. Re-measure on Aug–Sep data before scaling.

---

## 2026-07-19 addendum — kill-test results for the MLB totals fade

**Kill-test #1 (selection-free anchoring): FAILED — the fade is an artifact.**
Re-pulled minute candles for 4,400+ settled MLB markets anchored to *scheduled game start* (parsed from tickers) instead of market close:

| Anchor | Series | n | Priced | Realized | Fade net ROI | t |
|---|---|---|---|---|---|---|
| pre-game | KXMLBTOTAL 70–90¢ | 624 | 0.811 | 0.803 | −4.1% | −0.5 |
| start+2h (in-play) | KXMLBTOTAL 70–90¢ | 169 | 0.789 | 0.805 | −15.2% | −1.2 |
| start+2h (in-play) | KXMLBSPREAD 70–90¢ | 116 | 0.804 | 0.853 | −32.1% | −2.1 |
| all anchors | cheap-YES 5–40¢ (both series) | 890–903/anchor | ≈ | ≈ | −5% to −36% | ≤0 |

MLB totals are **well calibrated pre-game and in-play**. The +60%/trade "fade" in the original close-anchored table was outcome-dependent sampling: anchoring at T-1h-before-close conditions on when the market stopped trading, which correlates with how the total resolved. Textbook selection bias — this is why the kill-test existed.

**Contamination warning:** every close-anchored T-1h sports result in this file (UFC MOV, WC props cheap-side, the pooled props tables) carries the same methodology risk and should be treated as unverified until re-run with event-start anchoring. The **weather findings are NOT affected** — weather markets close at a fixed daily time regardless of outcome, so close-anchoring is selection-free there. The T-24h pre-game sports numbers are directly superseded by the pre-game row above: efficient.

**Kill-test #2 (fill realism): passed** (median ~14.8k contracts printed within ±15 min of sampled quotes; 99% of candidates had ≥100 near the bid) — moot for the fade, but it confirms MLB in-play books have real institutional-grade liquidity for a *model-driven* strategy.

**Revised Build 1 premise:** no free bias to harvest in MLB props. The path is the one the roadmap already specified as the model track: run-environment simulation + sharp-line anchor → CLV-validated pre-game/in-play quoting. Expected edge must come from forecasting, not calibration error.
