# P-020 — Cross-Venue Signal (Polymarket → Kalshi, Politics/World)

**Phase 1, settled-data falsification · 2026-07-26 · paper/research only, nothing placed, no pod built**
**Prompt:** `research/prompts/PROMPT_P020_CrossVenue.md` (Task 6)

## Verdict: **KILL**

On the matched politics/world universe, **Kalshi is the deeper AND the sharper venue.**
Polymarket's mid is a *worse* forecast than Kalshi's own price (Brier 0.1445 vs 0.1373;
the Brier-minimising weight on Polymarket is **0.0**), and trading the divergence as a
Kalshi taker earns **+0.66¢/contract, event-clustered CI [−6.94, +11.08]** at the loosest
threshold, turning negative at every stricter one. Net CLV is negative.

The thesis fails at its own premise before it reaches the signal test: **median
Polymarket/Kalshi dollar volume on matched pairs is 0.50** — Kalshi trades about twice
the notional — and Polymarket is the deeper book in only **32%** of observations. The
strategy was built on "Polymarket is materially deeper than Kalshi on politics/world."
For the markets both venues actually list, that is not true.

## 1. What was tested

> "When `|poly_mid − kalshi_mid| > corridor + MARGIN`, take on Kalshi toward Polymarket."

Single-venue trade: execution on Kalshi only, Polymarket used purely as a free
fair-value oracle. No Polymarket order path was called (read endpoints only: Gamma
`/markets`, `/markets/keyset`, `/tags`, CLOB `/prices-history`).

## 2. Data and how pairs were matched

| | |
|---|---|
| Study window | 2026-06-15 → 2026-07-27 UTC |
| Polymarket closed markets scanned (politics/world/econ tags) | **3,752** |
| Kalshi settled markets scanned | **86,825** across 1,942 series |
| Candidate pairs after matching | **390** |
| → HIGH confidence / MED / LOW | **152 / 108 / 130** |
| Pairs surviving the $1,000 Polymarket volume floor | 325 |
| Pairs with usable **dual-venue** price history | **34 HIGH · 81 HIGH+MED** |
| Observations (anchor × pair) | **209 HIGH / 5 clusters · 435 HIGH+MED / 15 clusters** |

**The window is set by Polymarket, not by choice.** The CLOB `/prices-history` endpoint
retains roughly the last 30 days: markets ending before ~2026-06-20 return an empty
history array (verified across six month-buckets on 2026-07-26). Everything older — the
2024 election, the whole Iran/Hormuz complex of June — is price-blind. This is the single
biggest limitation of the study and the reason 152 HIGH pairs collapse to 34 with usable
series.

### Matching pipeline

1. Polymarket events (≥$25k event volume) → Kalshi candidate series via Kalshi's
   `/v1/search/series` (488 searches → 1,942 candidate series).
2. Settled Kalshi markets pulled per series inside the window.
3. Market-level match, gated on **all** of:
   - token-sort fuzzy score ≥ 78 on the question text;
   - **strike-token equality** — numbers that are not calendar dates or years must match
     exactly ("above 175 transits" ≠ "fewer than 150 ships");
   - **polarity guard** — a hike/above question never matches a cut/below one;
   - **Kalshi `yes_sub_title` must be locatable in the Polymarket question** — the
     sub-title *is* the contract's strike (the candidate name, the phrase Trump must say,
     the party);
   - Kalshi `close_time` within 48 h of the Polymarket `endDate`;
   - both venues resolved determinately.

### False-match rate — measured, not asserted

- **Before** the strike/polarity/sub-title gates: a manual audit of 35 random pairs found
  **13 settlement-definition mismatches — 37%** (Hormuz ">175 transits" paired with
  "<150 ships"; Fed "hike >25bps" with "decrease 25bps"; Trump "say Nuclear" with "say
  Nuclear **15+ times**"). **That audit is why the gates exist.**
- **After** the gates, a fresh manual audit of **40 random HIGH pairs found 0 mismatches**
  → false-match rate **0%, 95% upper bound 7.5%** (rule of three).
- Independent corroboration: HIGH pairs agree on the realised outcome **99%** of the time
  (150/152), versus 81% for MED and 62% for LOW.
- Tier assignment deliberately does **not** use outcome agreement — conditioning on the
  realised result would select on the very thing the Brier test measures.

### Composition caveat (important)

The 5 HIGH clusters are 3 Trump-speech "what will he say" events plus the Arizona AG and
Colorado governor primaries. HIGH+MED adds Truth-Social post-count markets, the AZ-01
primary and the Knesset market (15 clusters). This is a narrow slice of "politics/world"
— it is what a 30-day Polymarket history window permits. The verdict is strong for what
could be measured and is stated with that scope.

## 3. Method guardrails

- Anchors are fixed horizons **before** the Kalshi close (T−168 h … T−6 h). The final 6 h
  and `last_price` are never used.
- Kalshi observations require a **two-sided quote** (bid > 0 and ask < 1) with spread
  ≤ 6¢. **197 anchor slots were discarded for a one-sided/empty book** — exactly the
  bare-ask placeholders that fabricated the EV-Map Build 1 edge. Median accepted spread:
  **1¢**.
- Kalshi candlesticks are emitted only for periods with activity, so a resting quote is
  reconstructed by forward-fill (≤72 h staleness), never interpolated.
- Trades execute at the **ask** for YES / **1−bid** for NO, never the mid, and pay the
  series-aware taker fee (`src/kalshi_fees.fee_per_contract`; politics series are all
  `quadratic`).
- Every statistic is bootstrapped (5,000 resamples) **clustering on the real-world
  event**, never per contract.

## 4. Is Polymarket the sharper reference? — No

| HIGH pairs, n=209, 5 clusters | Brier | LogLoss |
|---|---|---|
| Kalshi mid | **0.1373** | 0.4382 |
| Polymarket mid | 0.1445 | 0.4563 |
| 50/50 blend | 0.1401 | — |

Brier(Kalshi) − Brier(Poly) = **−0.0072, CI [−0.0448, +0.0063]** — statistically a tie,
with the point estimate pointing the *wrong way* for the thesis.

**Blend-weight sweep (the decisive test).** If Polymarket carried any information Kalshi
lacked, some positive weight would beat w=0:

| weight on Poly | 0.0 | 0.1 | 0.25 | 0.5 | 0.75 | 1.0 |
|---|---|---|---|---|---|---|
| Brier | **0.1373** | 0.1377 | 0.1385 | 0.1401 | 0.1421 | 0.1445 |

Monotone increasing. **The optimal weight on Polymarket is zero.** Every cent of
Polymarket information degrades Kalshi's own forecast.

(On the full 652-observation set including LOW-confidence pairs the gap is much wider —
Brier 0.158 vs 0.215 — but that number is contaminated by definitional mismatches and
should not be quoted.)

## 5. Corridor-sensitivity table

Taker on Kalshi toward Polymarket, executed at the ask/1−bid, net of taker fee and
half-spread. `corridor = fee(mid) + half-spread + MARGIN`.

**HIGH-confidence pairs (5 event clusters):**

| MARGIN | trades | clusters | net PnL ¢/ct (event-clustered CI) | net CLV ¢/ct | hit % |
|---|---|---|---|---|---|
| 0¢ | 118 | 5 | **+0.66 [−6.94, +11.08]** | −0.42 [−1.08, +4.62] | 44.9% |
| 1¢ | 81 | 4 | −1.06 [−7.92, +9.67] | −0.28 [−1.44, +5.50] | 39.5% |
| 2¢ | 46 | 3 | −1.18 [−20.52, +10.86] | −0.13 [−1.11, +2.18] | 43.5% |
| 3¢ | 31 | 3 | +5.39 [−33.33, +19.48] | +0.52 [−1.32, +5.01] | 51.6% |
| 5¢ | 12 | 3 | −17.04 [−56.11, +12.57] | +3.10 [−0.37, +5.01] | 33.3% |
| 8¢ | 9 | 3 | −18.34 [−56.11, +34.39] | +0.94 [−4.10, +5.01] | 33.3% |
| 12¢ | 5 | 1 | −56.11 (single cluster) | +5.01 | 0.0% |

**HIGH+MED (15 clusters — 3× the cluster count, at ~19% false-match cost):**

| MARGIN | trades | clusters | net PnL ¢/ct | net CLV ¢/ct |
|---|---|---|---|---|
| 0¢ | 282 | 15 | −0.73 [−5.33, +6.66] | **−1.23 [−2.57, −0.27]** |
| 1¢ | 226 | 14 | −1.06 [−5.87, +7.39] | −0.96 [−2.51, +0.50] |
| 2¢ | 164 | 13 | −3.43 [−10.36, +7.89] | −1.36 [−3.35, +0.16] |
| 3¢ | 138 | 11 | −3.13 [−13.37, +11.08] | **−2.04 [−4.46, −0.27]** |
| 5¢ | 99 | 10 | −8.93 [−20.24, +9.41] | **−2.15 [−4.49, −0.34]** |
| 8¢ | 82 | 10 | −9.76 [−22.32, +12.78] | −1.67 [−4.50, +0.83] |
| 12¢ | 68 | 9 | −11.28 [−26.61, +15.05] | −1.83 [−5.37, +1.02] |

There is no corridor at which the strategy is positive with a CI excluding zero. On the
higher-cluster universe, **net CLV is significantly negative** at 0¢, 3¢ and 5¢ — the
signal is worse than nothing.

### By gap bucket (margin 0, HIGH)

| \|gap\| | n | clusters | net PnL ¢/ct | net CLV ¢/ct | Brier(K)−Brier(P) |
|---|---|---|---|---|---|
| 0–3¢ | 38 | 5 | +3.25 [−3.41, +13.25] | −0.21 [−3.01, +0.29] | +0.0023 |
| 3–6¢ | 59 | 5 | +0.26 [−15.25, +37.72] | −1.49 [−2.71, +6.01] | +0.0009 |
| 6–10¢ | 12 | 3 | +8.69 [−15.55, +22.83] | +4.53 [+2.08, +11.87] | +0.0073 |
| 10–20¢ | 4 | 2 | +28.87 [+27.03, +34.39] | −1.77 [−4.10, +2.89] | +0.0635 |
| 20¢+ | 5 | 1 | −56.11 (single cluster) | +5.01 | −0.3804 |

The apparently attractive 10–20¢ bucket is **4 trades across 2 clusters**, and the
neighbouring 20¢+ bucket is −56¢. This is noise, not a tail edge.

### By anchor horizon (margin 2¢, HIGH)

T−48 h +7.57 · T−36 h −3.84 · T−24 h +2.28 · T−18 h −12.38 · T−12 h +30.87 · T−8 h
−23.19 · T−6 h +6.14 (¢/ct). No monotone structure; every cell is 1–11 trades.

### Robustness

- **Leave-one-cluster-out** at margin 2¢: range −8.24¢ to +3.52¢, **1 of 3 clusters
  positive**.
- **Lead-lag** (does the gap predict Kalshi's next 24 h move? β=1 would mean full
  convergence to Polymarket): HIGH β = **+0.300 [+0.105, +1.242]** on 5 clusters, but
  HIGH+MED β = **+0.045 [−0.039, +0.143]** on 15 clusters. **The convergence that looks
  real on five clusters disappears the moment there are enough clusters to test it** —
  the same pattern that killed P-021 and P-024.
- **Split on which venue is deeper** (HIGH+MED, margin 0): where Polymarket *is* deeper
  (n=87, 15 clusters) net PnL is **−3.61¢** and Brier still favours Kalshi
  (−0.0389 [−0.1034, +0.0053]). **Conditioning on the thesis's own precondition makes the
  result worse, not better.**

## 6. Confronting the H2 tight-corridor prior — the one genuinely new finding

The ev-map H2 study concluded the Kalshi↔Polymarket basis is **fee-bounded inside a ~±2¢
no-arb corridor**, from four synchronous quote comparisons in the liquid sports head.
**That does not generalise to politics.**

| | median \|gap\| | p75 | p90 | median corridor | share outside corridor |
|---|---|---|---|---|---|
| HIGH (n=209) | 2.10¢ | 4.00¢ | 6.00¢ | 1.98¢ | **56%** |
| HIGH+MED (n=435) | 3.00¢ | 6.50¢ | 24.50¢ | 2.01¢ | **65%** |

The basis breaches the fee corridor most of the time on politics markets, with a 90th
percentile of 6¢ (24.5¢ on the wider set). **Nobody is arbitraging these two books flat.
The corridor is not what stops this trade.**

What stops it is that the gap carries no information about the outcome. This is a cleaner
and more useful kill than "the corridor binds": it says the two venues simply disagree,
noisily, on markets where Kalshi has the deeper book and the better calibration — so the
disagreement is **Polymarket's error, not Kalshi's.**

**Action item:** re-scope H2 in `kalshi-ev-map/02_edge_hypotheses.md` to "fee-bounded *in
the liquid sports head*". A future strategy that assumes a tight corridor on politics
would be built on a wrong premise.

## 7. Does Polymarket execution access block monetisation?

**No — and that is not why this dies.** P-020 was designed as a single-venue Kalshi taker
precisely to dodge the blocker that shelved P-002 and P-006. If the signal had been real,
it would have been tradeable today with existing Kalshi access and no new venue
relationship.

The blocker here is upstream of execution: **there is no signal.** P-006 remains the most
trustworthy shelved edge in the lifetime analysis (CI [+5.4%, +21.2%]) and this result
says nothing about it — P-006 uses sportsbook consensus against Polymarket *sports*
prices, a different reference on a different venue-pair. **P-020 does not refute P-002 or
P-006 and should not be cited as doing so.**

## 8. What would change the verdict

Two conditions, both of which must hold before this is worth re-opening:

1. **A politics universe where Polymarket is demonstrably deeper.** Measure
   dollar-for-dollar *first*. The 2026 US midterms cycle is the obvious candidate; the
   current matched set is 2:1 Kalshi.
2. **Polymarket price history beyond ~30 days.** Either forward-capture (a read-only
   collector, cheap — but it would be building infrastructure for a killed hypothesis, so
   only do it if condition 1 is independently established) or a paid historical feed.
   Without it any repeat lands on 5 event clusters again.

Absent both, this is a dead hypothesis. **It has now been tested once, properly, and
should come off the queue.**

## 9. Scoreboard entry

Consistent with the house record: "we have better information than Kalshi" is now
**0 for 7** (P-016, P-019, P-021, P-024, P-025, EV-Map Build 1, and now P-020). Kalshi's
books are better than the literature assumes, including against Polymarket on political
markets.

## Files

| Path | What |
|---|---|
| `harvest.py` | throttled read-only harvest (Kalshi series/markets/candlesticks, Gamma, CLOB prices-history), everything cached |
| `shortlist_series.py` | local shortlist of Kalshi series worth querying |
| `build_pairs.py` | stage A/B: Poly event → Kalshi candidate series → settled markets |
| `match_pairs.py` | stage C: pair matching, strike/polarity/sub-title gates, tiering |
| `audit_matches.py` | reproducible sample for the manual false-match audit |
| `backtest_crossvenue.py` | the backtest (all tables above) |
| `results.json` / `p020_params.json` / `backtest_output.txt` | machine-readable results, pre-registered params, raw console output |
| `archive/crossvenue_cache_20260726.tar.gz` | the API cache, 271 MB → 11 MB. **The Polymarket half cannot be re-pulled** (~30-day retention) |

Reproduce with `python3 crossvenue_research/backtest_crossvenue.py` — fully cached, no
network calls (restore the cache first via `archive/README.md`).

---

*Verification note: the orchestrating session independently re-read `results.json` and
confirmed the blend sweep is monotone increasing from w=0 (Brier 0.13731) to w=1
(0.14446) — optimal weight on Polymarket exactly zero; Brier HIGH n=209/5 clusters,
diff −0.00715 CI [−0.04475, +0.00633]; basis outside corridor 56.5% (HIGH) and 64.8%
(HIGH+MED); Polymarket the deeper book in 87 of 282 observations (31%).*
