# Markout + VPIN Instrumentation for the Maker Pods (P-016, P-017M)

*Outline v1, 2026-07-22. For handoff to Claude Code. This is the cheap, do-now "deepen existing pods" item from `Deep Research - Durable EV Opportunities 2026-07.md`. It de-risks the promote/kill decisions already in flight for P-016 and P-017M, and produces the shared instrumentation P-018 will also use.*

---

## 0. What already exists (do NOT rebuild)

Before writing anything, note the current state so this is purely additive:

- **P-016** (`src/pods/live_maker_pod.py`): markouts at **60/300/900s + settlement**, correct sign convention (`buy → +1·(ref−price)`, `sell → −1·(ref−price)`), and rich per-fill telemetry already logged — `taker_side`, `sens_at_quote`, `quote_dist_from_mid`, `secs_since_state_change`, `inning`, `is_top`, `inventory_after`, plus **shadow quotes** (counterfactuals for inning-gate/inventory-cap).
- **P-017M** (`src/golf_fade_maker.py`): markouts at **300/900/3600s** + settlement; logs `taker_side`, `trade_price`, `trade_count`, `inventory_after`.
- **`scripts/maker_diagnostics.py`**: already indexes FILL/MARKOUT/SETTLE and buckets net markout by distance-from-mid, **seconds-since-state-change (adverse-selection proxy)**, |dWP/drun| leverage, side, and price decile (FLB); emits fee-adjusted +5m markout P&L and parameter proposals.

So markout **capture and most markout analysis already exist for P-016.** The genuinely additive work is: (A) two markout enrichments, (B) a **new VPIN toxicity metric** (does not exist), (C) extending the diagnostics to P-017M and to the new metrics, (D) a single promote/kill scoreboard number. Keep the changes read-only/telemetry — do **not** alter P-016 quoting while its gate sample is running (fee model included; see the P-016 header warning).

---

## A. Markout enrichments

### A1. Event-clocked markout horizon (not just wall-clock)

Wall-clock 60/300/900s markouts miss *which game state* picked us off. Add one **event-relative** markout per fill:

- **P-016:** on the first observed `state_key` change at/after `fill.epoch`, record `markout_at_next_state_change` = signed (mid_now − fill_price). This is the "at-bat/possession resolution" markout — a fill that's fine at 5 min but deeply negative here tells you the fill clustered right before the next resolving event.
- **P-017M:** the analogue is a round-transition / large-mid-move marker; if that's noisy for golf, skip and rely on the existing 300/900/3600s + settlement (golf has no discrete high-frequency events).
- Implementation: extend the `MARKOUT` record with `horizon_type: "wallclock" | "next_event"` and, for `next_event`, `secs_elapsed` so decay can still be plotted.

### A2. Persist the wall-clock markout **curve**, not just points

The three horizons already give a curve; make `maker_diagnostics.py` emit it as a per-fill **markout curve** (t=60/300/900 [+3600 for P-017M] + settlement) and aggregate to a **mean curve with event-clustered CIs**. The shape is the diagnosis: a curve that's positive early and negative late = we're being run over on continuation; flat-positive = clean spread capture.

---

## B. VPIN / order-flow toxicity metric (NEW — the core add)

Rationale: the Stanford Kalshi study (41.6M trades) shows single-name in-play markets carry ~2× the informed price impact of broad markets, and **one-sided order flow predicts maker losses** there. We already fetch every public print with `taker_side` and `count` (`KalshiPublic.trades_since`), so VPIN is computable with no new data source.

### B1. Computation (`src/order_flow.py`, new)

Volume-Synchronized Probability of Informed Trading, thin-book variant:

```
# classify each print by aggressor
buy_vol  = sum(count for prints where taker_side == "yes")   # lifting the ask = buy pressure
sell_vol = sum(count for prints where taker_side == "no")    # hitting the bid = sell pressure

# equal-volume buckets of size V (e.g. V = 50 contracts; PAPER-TUNE per family)
# accumulate prints into buckets; when a bucket fills:
bucket_imbalance = abs(bucket_buy - bucket_sell) / (bucket_buy + bucket_sell)

# VPIN = mean bucket_imbalance over the last n buckets (e.g. n = 20)
vpin = mean(bucket_imbalance[-n:])          # 0 = balanced, 1 = fully one-sided/toxic
```

For very thin books where buckets fill slowly, also maintain a simpler **rolling one-sidedness** over the last `W` contracts: `abs(buy−sell)/(buy+sell)` — cheaper and reacts faster. Log both; decide later which predicts losses better.

### B2. Wiring (telemetry only, no behavior change yet)

- Maintain `vpin` + `one_sidedness` per book, updated each cycle from the prints already fetched in `_fetch_trades` / `_check_fills`.
- Stamp **every FILL record** with `vpin_at_fill` and `one_sidedness_at_fill`.
- Stamp every QUOTE record with the current values (so you can see the state you quoted into).
- No quoting change in this phase — this is measurement. (Optional Phase 2, only after the gate sample completes: use VPIN as a quote-pull/widen trigger, mirroring the existing `divergence_guard` pull.)

---

## C. Extend `scripts/maker_diagnostics.py`

1. **Generalize the loader** to read both P-016 and P-017M logs (`data/trade_logs/maker_fills.jsonl` and `.../golf_maker_fills.jsonl`), selectable by `--pod P-016|P-017M`.
2. **New sections:**
   - *Markout by VPIN bucket* — net markout for fills in low / mid / high `vpin_at_fill` terciles. **The headline adverse-selection test:** if high-VPIN fills have sharply negative net markouts, one-sidedness is toxic and should become a pull trigger.
   - *Event-clocked markout* — net markout at `next_event` vs wall-clock, by side and leverage.
   - *Markout curve* — mean curve + event-clustered CI (A2).
3. **Keep** the existing distance/staleness/leverage/side/price-decile sections.

---

## D. Promote/kill scoreboard (one number + guards)

Add a top-of-report block that directly feeds the pre-registered gates:

- **Markout-adjusted net P&L per fill**, net of series-aware maker fee (`fee_per_contract(price, maker=True, series_ticker=...)`), over all real (non-shadow) fills, with an **event-clustered CI** and the value **after dropping the single best day**.
- **VPIN interaction:** the same number restricted to below-median VPIN fills (what we'd earn if we pulled in toxic flow) vs above-median (what the toxicity costs). If the edge lives entirely in low-VPIN fills, that is the Phase-2 pull rule writing itself.
- Gate reminders inline: P-016/P-017M promote only on **≥500 fills, markout-adjusted P&L > 0 robust to dropping the best day**; sample unit is **game/tournament**, not fill.

---

## E. Note on P-017 (taker) — "the other 17"

P-017 is a pre-tournament **taker**, so bid/ask spread-capture markouts don't apply the same way. If you want entry-quality tracking there, add a **taker-markout variant**: for each P-017 buy, record mid at +1h/+24h/close vs entry price — this measures whether the pre-tournament entry timing (the "Wednesday" anchor) is well-chosen or whether we're systematically early/late. It reuses the same markout plumbing (`horizon_type: "taker_entry"`), reads P-017's placements from the trade log, and is a nice-to-have, not a gate. VPIN is not meaningful for a once-per-event taker and should be skipped for P-017.

---

## F. Build order + definition of done

1. `src/order_flow.py` (VPIN + one-sidedness) + unit test on a synthetic print stream.
2. Wire `vpin_at_fill` / `one_sidedness_at_fill` into P-016 and P-017M FILL/QUOTE records (telemetry only).
3. Add event-clocked (`next_event`) markout to P-016; markout-curve aggregation.
4. Extend `maker_diagnostics.py` (loader + new sections + scoreboard).
5. (Optional, post-gate) VPIN-triggered quote pull/widen — separate change, separate sample.

**Definition of done:** running `scripts/maker_diagnostics.py --pod P-016` and `--pod P-017M` prints the markout curve, the VPIN-bucket adverse-selection table, and the markout-adjusted net-P&L scoreboard with event-clustered CI; P-016/P-017M fills now carry VPIN + event-clocked markout tags; no change to live quoting behavior while the gate samples run.
