# P-019 — Cross-Family Longshot Maker Harvest (Kalshi)

> ## ⛔ KILLED 2026-07-22 — NO-GO at step-1 calibration gate. DO NOT BUILD.
> The favorite-longshot bias this spec harvests **does not replicate in our
> universe.** On 5,192 event-clustered settled Kalshi contracts, both proposed
> bands are calibrated within CI (sell-target 0.03–0.10: realized 6.6% vs implied
> 5.8%; buy-target 0.80–0.90: realized 80.9% vs implied 84.8%, favorites if
> anything *over*priced). Only the 0–3¢ tail is mispriced — outside these bands
> and unharvestable (~1.2¢ vs ~65:1 downside). The long-horizon slice disconfirms
> hardest, exactly where the thesis claims the edge is strongest.
> **Evidence:** [`../longshot_research/REPORT_Longshot_2026-07.md`](../longshot_research/REPORT_Longshot_2026-07.md).
> Spec retained below for provenance only. The one reusable idea is the
> `AggregateRiskGuard` cluster-correlation dimension (§4.2), if a *different* pod
> ever needs theme-correlation caps.

*Build spec v1, 2026-07-22. For handoff to Claude Code. Paper-first, kill-gated, mirrors the P-017M `GolfFadeMakerEngine` standalone idiom. Second-ranked durable-edge opportunity from `Deep Research - Durable EV Opportunities 2026-07.md`.*

---

## 1. One-paragraph thesis

The favorite-longshot bias is one of the most replicated regularities in wagering, and it is present and measurable on Kalshi: contracts priced **under 10¢ lose >60% of stake pre-fee**, the loss rate shrinks monotonically as price rises, and **contracts above 50¢ earn small positive returns**; makers on the exchange returned −9.6% vs −31.5% for takers, with makers on ≥50¢ contracts *positive* after fee (Whelan, GWU 2026-001). This is the same edge P-015 (tennis qualifier favorites, +4.1¢/ct) and P-017 (golf top-N) already harvest, generalized into one systematic **maker** book. We **post** (never cross), because maker fee is zero on the target families and the taker fee curve is cheapest at the tails: buy underpriced favorites at 85–95¢ and sell overpriced longshots at 3–10¢, concentrated in **thin, long-horizon politics and sports-season-futures markets** where calibration slope is worst (most biased) — politics up to 1.83, long-horizon sports 1.74 (arXiv 2602.19520). The edge decays where attention arrives, so every book is CLV-gated and retired as volume rises.

**Status target:** paper-only, standalone engine. Never places real orders (`KalshiPublic` reads + pessimistic simulated fills, exactly like P-017M).

---

## 2. Relationship to existing pods (reuse, do NOT rebuild)

| Existing | Reuse for P-019 |
|---|---|
| `src/golf_fade_maker.py` `GolfFadeMakerEngine` | **Structural template.** Copy the standalone quote/fill/markout/settle loop, `MakerQuote`/`MakerFill`/`MarketBook` dataclasses, pessimistic through-fill, `_maybe_settle`, kill-file, JSONL logging. |
| `src/pods/qualifier_favorite_pod.py` (P-015) | Reference for the **favorites leg** sizing/edge idiom (`fair = ask + bump`, conservative lower-CI convention, depth cap). P-019's favorites leg is the *maker* version of this. |
| `src/kalshi_public.py` `open_markets/orderbook/trades_since` | Discovery, mids, prints. |
| `src/kalshi_fees.py` `series_maker_charges_fee(series_ticker)`, `fee_per_contract(price, maker=True, series_ticker=...)`, `fee_fraction_of_stake` | **Gate the universe to `series_maker_charges_fee == False` (zero maker fee).** Confirm per series; do not assume. |
| `src/devig.py` `devig_power` (Shin) | De-vig multi-outcome fields to a true-prob estimate for the longshot leg where a market partition exists. |
| `src/aggregate_risk.py` `AggregateRiskGuard` | Portfolio caps — **needs a new correlation-cluster dimension** (see §4). |
| `src/clv.py` | CLV logging → the decay gate (see §5). |
| `scripts/maker_diagnostics.py` | Markout/edge report harness — extend to P-019's log (see instrumentation spec). |

Framed as a **standalone maker engine** (like P-017M), not a `BasePod` in the 5-minute loop, because it rests resting orders and settles on its own fast cycle.

---

## 3. Universe + the two legs

### 3a. Universe selection (`_discover`)

Filter live open markets to the harvestable set:

```
keep a market iff:
  series_maker_charges_fee(series) is False           # zero maker fee (politics/entertainment/etc are 'quadratic')
  AND category in {Politics, sports SEASON futures/top-N}   # strongest, most-durable FLB families
  AND horizon_days >= MIN_HORIZON_DAYS (e.g. 14)       # long-horizon = worst calibration = most edge
  AND volume/open_interest below LOW_ATTENTION_MAX     # unwatched books only (edge lives where attention isn't)
  AND a two-sided book exists (bid and ask both present)
EXCLUDE:
  crypto ranges (near-calibrated, slope ~1.0-1.2 — no durable edge)
  short-horizon weather (binary calibration is OVERCONFIRMED near resolution — opposite sign; our
    weather edge is a bracket-structure artifact, do NOT put weather in P-019)
```

Emit the kept set to a discovery log so the universe is auditable.

### 3b. Leg A — buy underpriced favorites (lower tail risk; ship FIRST)

- Post a **YES bid** just under model fair on contracts whose ask is **85–95¢**.
- True-prob source: where a model exists (sports futures via DataGolf-style feed / base rates), use it; else the conservative structural bump (P-015/P-017 convention, lower-CI). Require `net_edge(fair, bid, maker=True, series_ticker=...) > MIN_NET_EDGE`.
- Fill = pessimistic through-print (a print strictly **below** our bid). Settle → `pnl = (result - fill_price - maker_fee) * qty`, `maker_fee ≈ 0`.

### 3c. Leg B — sell overpriced longshots (tail-heavy; gate behind Leg A)

- Post a **NO (sell YES)** on contracts priced **3–10¢** whose model true-prob is below price.
- Fill = pessimistic through-print (a print strictly **above** our ask). Settle → `pnl = (fill_price - result - maker_fee) * qty`.
- **Run at half size, and only after the collector confirms the tail is priced as modeled** (see §5). This is the picking-up-pennies-in-front-of-a-steamroller leg; the risk controls in §4 are mandatory before it goes live in paper.

---

## 4. Tail-risk controls (this IS the strategy — implement before Leg B)

Selling a 5¢ longshot pays 5¢ and risks 95¢ (19:1 downside); with true q≈3% the per-contract EV is ~+2¢ but each loss is ~47× each win. Controls:

1. **Fractional Kelly ≤ 1/10** on both legs, computed off the model edge; hard **per-event collateral cap ≤ 0.5% of bankroll** (`max_position_usd`). Reuse `BasePod.compute_position_size` semantics / `CapitalAllocator`.
2. **Correlation-cluster caps — NEW extension to `AggregateRiskGuard`.** The guard today has venue and per-pod caps but **no theme/event correlation dimension** (its docstring claims "correlation-aware" but the code only buckets by venue/pod). Add:
   - a `cluster_id` on each reservation (e.g. `politics:2026-midterms`, `pga:3M-OPEN`, region/day for any weather that ever enters),
   - `max_cluster_exposure_pct` (default 0.05), enforced in `check_trade`/`reserve_trade`/`_add_position`/`close_position` alongside the existing venue/pod buckets,
   - worst-case accounting: model a cluster's tail as **all longshots in the cluster resolve YES simultaneously**, and cap that stressed loss, not just nominal exposure.
3. **Ruin check (documented, not hand-waved):** at 1/10-Kelly (~0.5%/event, ~40 independent events) an 8-event simultaneous tail cluster costs ~4% of bankroll — survivable, recoverable in ~2 cycles. At full Kelly the same cluster is terminal. Encode the 0.5%/event + 5%/cluster caps so this holds by construction.

---

## 5. CLV decay gate (retire watched books automatically)

The edge is durable only while a book is unwatched; it decays as volume arrives (our own 13-month tennis-favorite evaporation; horse-racing bias moderating with arbitrage). So:

- Log **closing-line value** per fill via `src/clv.py` (compare fill price to the market's settle-eve mid).
- Maintain a rolling net-CLV per book/series; **auto-retire** (stop quoting) any book whose rolling net CLV goes ≤ 0 or whose volume/OI crosses `LOW_ATTENTION_MAX`.
- This is a first-class runtime control, not just a report — a decaying inattention edge that isn't retired becomes a losing book.

---

## 6. Files to create

```
src/pods/longshot_maker.py        # LongshotMakerEngine + @register_pod("P-019") wrapper
scripts/run_longshot_maker.py     # standalone runner, mirrors scripts/run_golf_maker.py
scripts/betting-longshot-maker.service   # systemd unit, mirrors betting-live-maker.service
tests/test_longshot_maker.py      # unit tests (see §7)
longshot_research/                # calibration backtest + REPORT + params
  backtest_longshot.py
  REPORT_Longshot_2026-07.md
  p019_params.json
# EXTENSION (not new file): src/aggregate_risk.py  → add cluster dimension (§4.2)
```

Logs: `data/trade_logs/longshot_maker_quotes.jsonl`, `.../longshot_maker_fills.jsonl`. Kill file: `data/KILL_LONGSHOT_MAKER`.

---

## 7. Backtest first + tests

**`longshot_research/backtest_longshot.py`** — settled-data calibration replay (mirror golf `backtest/`):
- Pull settled politics + sports-futures markets; bucket by **posted-fill price** decile; compute realized win-rate vs implied.
- Confirm the bias exists in *our* target universe before shipping: `<10¢` bucket must win **< half** its implied rate, and `85–95¢` favorites must win **> implied**. Cluster CIs by **event**, not contract.
- Persist `p019_params.json` (bands, MIN_NET_EDGE, MIN_HORIZON_DAYS, caps). Deliverable: `REPORT_Longshot_2026-07.md`.

**`tests/test_longshot_maker.py`** (mirror `tests/test_golf_topn.py`):
- Universe filter keeps a zero-maker-fee long-horizon politics market, drops a crypto range and a short-horizon weather market.
- Favorites leg posts a bid under fair only when `net_edge > MIN_NET_EDGE`; longshot leg gated off until `enable_longshot_leg` true.
- Pessimistic through-fill both directions; fee via `fee_per_contract(..., series_ticker=...)` returns 0 for `quadratic` series.
- Cluster cap: two reservations in the same `cluster_id` that jointly exceed `max_cluster_exposure_pct` → second rejected.
- Sizing: per-event collateral ≤ 0.5% bankroll; Kelly capped at 1/10.
- CLV retire: a book with rolling net CLV ≤ 0 stops quoting.

---

## 8. Config block (paste into `config_multi_pod.yaml`; leave OUT of `pods.active`)

```yaml
pods:
  P-019:
    enabled: false            # standalone via scripts/run_longshot_maker.py
    enable_longshot_leg: false  # Leg B stays off until collector confirms tails (see §5)
    universe:
      categories: ["politics", "sports_futures"]
      min_horizon_days: 14
      low_attention_max_oi: 5000     # PAPER-TUNE
      require_two_sided: true
    favorites_leg:
      price_band: [0.85, 0.95]
      min_net_edge: 0.02
      edge_bump: 0.03               # conservative lower-CI, used when no model
    longshot_leg:
      price_band: [0.03, 0.10]
      min_net_edge: 0.015
      size_fraction: 0.5            # half size vs favorites
    risk:
      kelly_fraction_cap: 0.10
      max_position_pct: 0.005       # <=0.5% bankroll per event
      max_cluster_exposure_pct: 0.05
aggregate_risk:
  max_cluster_exposure_pct: 0.05    # NEW field consumed by AggregateRiskGuard.from_config
```

---

## 9. Pre-registered kill / promote gates

- **Favorites leg first.** Promote the favorites leg to (small) real money only after ~8+ event-clustered paper wins with net edge > half the backtest baseline (P-015 methodology).
- **Longshot leg** stays paper until the collector shows the `<10¢` bucket realizing below its implied rate on ≥ a few hundred settled longshots AND the tail-cluster caps have been exercised without breach.
- **CLV kill:** retire any book at rolling net CLV ≤ 0. Kill any family whose measured edge drops below half baseline.
- Sample unit = **event**, not contract. Do not tune bands mid-flight (resets the counter, per P-015 discipline).

---

## 10. Build order (suggested for Claude Code)

1. `longshot_research/backtest_longshot.py` + REPORT → **decision point** (confirm the bias exists in our universe; kill if flat).
2. `src/aggregate_risk.py` cluster-dimension extension + tests (needed before any longshot-leg sizing).
3. `src/pods/longshot_maker.py` engine (favorites leg only), copying `GolfFadeMakerEngine`.
4. Tests green; `scripts/run_longshot_maker.py` + systemd unit; start **favorites-leg** paper collection.
5. CLV retire wiring via `src/clv.py`.
6. Enable Leg B (longshots) in paper only after §9 longshot condition + cluster caps verified.

**Definition of done (phase 1):** calibration REPORT committed with price-decile win-rate table; favorites-leg engine + cluster-cap extension + tests green; favorites-leg paper collection running and CLV-logging; Leg B implemented but config-disabled; nothing placed live.
