# REPORT — Golf Top-N Backtest (P-017 validation)

*2026-07-19. Backtest replay of the P-017 golf decision rules through a realistic execution model, on historical Kalshi data pulled today. Companion to `GOLF_KALSHI_RESEARCH.md` (the aggregate-calibration research) — this is the rigor step that runs the **actual per-bet decision logic** with correct series fees and pessimistic fills before any live wiring, in the same spirit as `tennis_research/REPORT.md` → P-015 and `live_agent_research/` → P-016.*

*Code: `backtest/backtest_golf.py` (canonical legs), `backtest/refine_golf.py` (window/band grids), `backtest/golf_fees.py` (series-aware fees + power de-vig). Data: `candles.jsonl` (12,796 prop markets across 10 tournaments), `trades.jsonl` (170k ticks, top-10/20 of 5 events). THOC26/COPC26 excluded — settlement not yet populated on pull date.*

---

## Verdict

| Leg | Rule | Net /contract | 95% CI (event-clustered) | Status |
|---|---|---|---|---|
| **A — pre-tournament cheap-YES** | Buy YES at ask, top-10/20, 8–45¢ band, 4–10 days before close | **+6.8¢** (taker fee paid) | **[+3.1¢, +10.2¢]** | ✅ **Validated** — 9/10 events positive |
| **B — fade maker** | Rest ask (sell YES), top-10/20, 36→6h before close, +3¢ over mid | **+9.1¢** (zero maker fee) | **[+4.5¢, +13.5¢]** | ⚠️ **Promising** — only 4 events of tick data |
| C — make-cut cheap-YES | Buy YES at ask, make-cut, cheap band | — | — | ⛔ Shelved — 12 quotes, insufficient |
| — top-5 | Buy YES at ask, top-5 | +0.5¢ | [−0.7¢, +1.8¢] | ⛔ Rejected — straddles zero |

The two structural legs are **positive without any model input** — pure market-price rules, so the edge (Leg A especially) needs no DataGolf subscription to capture. DataGolf would refine name selection, not create the edge.

## Leg A — the core, validated edge

Buying any top-10/20 contract priced 8–45¢ on the latest two-sided quote 4–10 days before close (the "Wednesday" anchor) returned **+6.8¢/contract net of the Kalshi taker fee**, over 926 bets in 10 tournaments. The bootstrap CI (resampling *tournaments*, since names within an event are correlated) is [+3.1¢, +10.2¢] — comfortably excludes zero. It is robust:

- **9 of 10 tournaments positive.** Range −4.9¢ (Travelers, TRAV26 — a signature event with a strong, heavily-watched field) to +15.6¢ (THCCBN26). The lone negative is a plausible "attention" exception: the softness comes from inattention, and signature events draw sharp attention.
- **Band-insensitive:** every band from 5–25¢ to 15–50¢ returns +6.6¢ to +7.4¢. Not a knife-edge fit.
- **Both series positive:** top-20 +8.3¢ [+3.6, +13.2], top-10 +4.9¢ [+2.3, +7.3]. Top-20 is the stronger book (more slots → more tie-inflation mass, more inattention).

Economic driver (from the research): the tie/dead-heat settlement quirk (top-20 markets averaged 22.7 yes-settlements vs the nominal 20, +13% mass) plus retail attention concentrating on stars leaves the mid-tier structurally cheap pre-tournament. Both are mechanical/behavioral and present on the exchange's own flow.

## Leg B — real but window-critical and underpowered

Resting an offer (selling YES) on top-10/20 names in-tournament, filling **only** on public prints that go strictly *through* the quote (the P-016 pessimistic-maker convention, which bakes in adverse selection), with zero maker fee (prop series are `quadratic`):

- **Window is everything.** 36→6h before close: **+9.1¢/ct [+4.5, +13.5]**. 48→6h: only +1.4¢ (dilutes with still-cheap 2–4d flow). **48→24h: −3.1¢** — fading Friday *loses* because YES is still being bid up. The tradeable window is Saturday through Sunday morning.
- Offset +3¢ over mid beats +1¢ (fills less, but avoids the worst adverse selection).
- **Caveat that caps confidence:** only 4 tournaments have tick data, so even the significant CI rests on 4 events. This is exactly what a P-016-style paper deployment resolves — collect fills live over 8–10 events before trusting it.

## What this changes vs the aggregate research

The aggregate analysis suggested makers could capture ~+8¢ in a "12–48h" window. The realistic replay sharpens this: the naive 48h version is break-even, and the *edge is entirely in the 36→6h window* — the aggregate number was right in magnitude but wrong on timing, and a pod that started fading too early would have bled. This is precisely the realized-vs-modeled shrinkage the literature warned about, caught before it cost anything.

## Recommended P-017 build (params in `p017_params.json`)

1. **Ship Leg A as the primary pod** — a BasePod taker pod, direct analog of `qualifier_favorite_pod` (P-015): discover top-10/20 markets, gate on 4–10-days-to-close + 8–45¢ band + spread ≤6¢, `fair_prob = ask + edge_bump` with a **conservative edge_bump = 0.04** (lower-CI convention) and `min_net_edge = 0.02`, depth-capped, paper mode. Initially skip signature-event weeks (the TRAV26 exception).
2. **Ship Leg B as a paper-only maker engine** mirroring P-016's `LiveMakerEngine`: quote asks at mid+3¢ on top-10/20 in the 36→6h window, pessimistic fills, zero maker fee, markout + settlement logging. Treat its 4-event edge as a hypothesis to confirm live, not a validated number.
3. **Shelve Leg C and top-5/40.**

## Honest limitations

10 tournaments (Leg A) / 4 (Leg B), H1-2026, correlated within-event outcomes — CIs are event-clustered but the event count is the real sample size. Structural edges (tie inflation) should persist; behavioral edges (inattention) can decay as Kalshi's maker ecosystem professionalizes — the paper-first gate and a kill criterion (net edge < half of baseline) guard against trading a dead edge. Leg B's fill model is pessimistic-but-simplified (quote anchored to the nearest prior daily candle mid, not a live book); the live P-016-style engine will be more faithful. Not financial advice; read each series rulebook for limits and eligibility before live trading.
