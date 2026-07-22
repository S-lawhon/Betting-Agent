# P-018 — Surprise-Gated In-Play Fade Maker (Kalshi)

*Build spec v1, 2026-07-22. For handoff to Claude Code. Paper-first, kill-gated, mirrors the P-016 `LiveMakerEngine` idiom exactly. This is the highest-ranked durable-edge opportunity from `Deep Research - Durable EV Opportunities 2026-07.md`.*

---

## 1. One-paragraph thesis

In-play prediction markets **underreact to expected events and overreact to surprising ones**. Fading the crowd after a *surprising* in-play event earned +2.79% at 2 min post-event (p=0.018), decaying ~40%/min to insignificance by ~6 min (Choi & Hui, Betfair in-play soccer); Kalshi's own in-play markets move only ~0.64-for-1 with the true win-probability change (NBA study, arXiv 2606.07811). P-016 already quotes a two-sided maker around a live win-prob model. **P-018 adds the one thing P-016 lacks: a surprise gate** that (a) *leans into fading* the overreaction after high-surprise events and (b) *widens or withdraws* around expected events and unresolved event risk, where the informed side runs a maker over. Sports props/derivatives are `quadratic` fee-type → **zero maker fee**, so we start 1.75¢/contract ahead of any taker doing the same trade.

**Status target:** paper-only, MLB first (reuse P-016 infra), NBA second. Never places real orders (uses `KalshiPublic` read paths + pessimistic simulated fills, identical to P-016/P-017M).

---

## 2. Relationship to existing pods (do NOT rebuild these)

| Existing | Reuse for P-018 |
|---|---|
| `src/pods/live_maker_pod.py` `LiveMakerEngine` | **Structural template.** Copy the quote/fill/markout/settle loop, `Quote`/`Fill`/`GameBook` dataclasses, pessimistic through-fill rule, anchor cache, divergence guard, shadow quotes. |
| `src/mlb_win_prob.py` `home_win_probability(snap, pregame_prob) -> fv` (has `.home_wp`, `.sensitivity`), `implied_pregame_prob(snap, mid)` | Pre/post-event win-prob and leverage. **This is the surprise signal source for MLB.** |
| `src/mlb_statsapi.py` `MlbStatsApi.linescore()`, `MlbLiveState.state_key()` | Game-state polling + the state-change trigger. |
| `src/kalshi_public.py` `KalshiPublic.open_markets/orderbook/trades_since` (+ `fnum` for `_fp` dollars) | Market discovery, mids, prints. |
| `src/kalshi_fees.py` `fee_per_contract(price, maker=True, series_ticker=...)` | Series-aware fee. **Pass `series_ticker`** — MLB moneyline `KXMLBGAME` DOES charge maker fee; MLB props/totals do not. Do not hardcode 0. |
| `scripts/maker_diagnostics.py` | The markout report harness — extend, don't duplicate (see the instrumentation spec). |
| `src/pod_registry.py` `@register_pod` | Thin wrapper registration. |

P-018 is a **new engine**, not a parameter tweak of P-016, because the surprise gate changes the *sign of intent* (P-016 is symmetric spread-capture; P-018 is asymmetric fade-into-overshoot) and because it must generalize across sports via a pluggable adapter.

---

## 3. The surprise gate (the core new logic)

Per book, on every cycle, after computing the current model fair `fv.home_wp`:

```
# 3a. Detect a discrete event: an OBSERVED state_key change (P-016 already tracks this).
if state_key != book.last_state_key:
    wp_jump      = fv.home_wp - book.fair_before_event      # signed model move at the event
    mkt_jump     = mid - book.mid_before_event              # signed market move at the event
    # SURPRISE = how far the event moved true value vs how "expected" it was.
    # Proxy for expectedness: leverage/sensitivity of the pre-event state — a 2-out
    # bases-empty single is low-leverage (expected noise); a go-ahead HR is high-surprise.
    surprise     = abs(wp_jump)
    underreaction = wp_jump - mkt_jump   # >0 => market hasn't caught up to model (the fade signal)
    book.event_ts = now
    book.last_event = {surprise, wp_jump, mkt_jump, underreaction}
    book.fair_before_event = fv.home_wp   # reset baseline
    book.mid_before_event   = mid
```

Regime selection each cycle:

```
secs_since_event = now - book.event_ts
in_fade_window   = SURPRISE_HI <= surprise  and  secs_since_event <= FADE_WINDOW_S   # e.g. 120..360s
expected_regime  = surprise < SURPRISE_LO

if killed or unresolved_event_risk(state):   # e.g. pitch in flight, 2-strike/3-ball, RISP<2out
    pull_all_quotes(reason="event_risk")
elif in_fade_window and abs(underreaction) >= MIN_UNDERREACTION:
    # FADE: rest a ONE-SIDED offer on the side the crowd is stampeding away from.
    # If the market overshot DOWN (underreaction>0, model higher than market), rest a BID
    # below model fair to buy the panic; if it overshot UP, rest an ASK above fair.
    quote = fade_quote(fv.home_wp, mid, underreaction, sensitivity)
elif expected_regime:
    # UNDERREACTION regime: informed continuation risk. Quote WIDE two-sided or step aside.
    quote = wide_two_sided(fv.home_wp, mid, extra_half_width=EXPECTED_WIDEN)
else:
    quote = normal_two_sided(...)   # P-016 behavior between events
```

Key decay rule: the fade edge decays ~40%/min, so `FADE_WINDOW_S` must be short (paper-tune 120–360s) and fade-quote size should **taper** with `secs_since_event`. Fading outside the window is the documented way to *lose* (the P-017M Friday-vs-Saturday lesson, restated for in-play).

`unresolved_event_risk(state)` = the P-016 cancel triggers, promoted to first-class: MLB → pitch in flight / 2-strike or 3-ball count / runner in scoring position with <2 outs / any at-bat that can plate the tying or lead run; NBA → possession in progress in final 3 min / free throws pending / after-timeout inbound. Pull, don't quote, through these.

---

## 4. Files to create

```
src/pods/inplay_fade_maker.py     # InplayFadeMakerEngine + @register_pod("P-018") wrapper
src/inplay_sport_adapter.py       # SportAdapter protocol: state poll, win_prob, sensitivity,
                                  #   event_risk(state), surprise inputs, kalshi discovery.
                                  #   MlbAdapter (wraps mlb_statsapi + mlb_win_prob) first;
                                  #   NbaAdapter stub second (needs an NBA WP model — see §7).
scripts/run_inplay_fade_maker.py  # standalone runner, mirrors scripts/run_golf_maker.py
scripts/betting-inplay-fade.service  # systemd unit, mirrors betting-live-maker.service
tests/test_inplay_fade_maker.py   # unit tests (see §6)
inplay_research/                  # backtest harness + REPORT + params.json (see §5)
  backtest_inplay_fade.py
  REPORT_InPlay_Fade_2026-07.md
  p018_params.json
```

Data/log outputs (JSONL, mirror P-016): `data/trade_logs/inplay_fade_quotes.jsonl`, `data/trade_logs/inplay_fade_fills.jsonl`. Kill file: `data/KILL_INPLAY_FADE`.

### Dataclasses (extend P-016's `Fill` with surprise telemetry)

Add to each `Fill` record so the diagnostics can bucket by surprise:
`surprise`, `wp_jump`, `underreaction`, `secs_since_event`, `regime` ("fade"|"expected"|"normal"), plus everything P-016 already logs (`sens_at_quote`, `quote_dist_from_mid`, `taker_side`, `inning`, `is_top`, `inventory_after`). These are what make the pilot's kill-gate #1 (below) measurable.

---

## 5. Backtest first (before any live paper)

Build `inplay_research/backtest_inplay_fade.py` as a **replay harness** over historical in-play data (mirror the golf `backtest/` pattern that produced `REPORT_Golf_TopN_2026-07.md`):

1. Source: replay historical Kalshi in-play prints + reconstructed game states (use `book_capture.py` / any captured tick logs if present; otherwise run a 2–3 week `book_capture` collection first — flag this as a prerequisite, don't fake it).
2. Reconstruct model WP path via `mlb_win_prob`, detect events, compute `surprise`/`underreaction`.
3. Simulate the pessimistic through-fill against real prints in the fade window; apply series-aware maker fee; mark to settlement.
4. **Deliverable:** `REPORT_InPlay_Fade_2026-07.md` with net ¢/contract **event-clustered** CI (cluster = game, not fill — same discipline as golf/tennis), a **surprise-bucket table** (does edge rise with surprise?), and a **window-decay table** (does edge die by ~6 min?). Persist tuned params to `p018_params.json`.

If the backtest surprise-bucket table is flat (high-surprise ≈ low-surprise), **the gate is noise — stop here and do not ship the pod.** That is the cheapest possible kill.

---

## 6. Tests (`tests/test_inplay_fade_maker.py`)

Mirror `tests/test_golf_topn.py` coverage style. At minimum:

- Surprise computation: a synthetic go-ahead HR produces `surprise` above `SURPRISE_HI`; a routine 2-out groundout stays below `SURPRISE_LO`.
- Regime routing: high-surprise+in-window → one-sided fade quote on the correct side; expected regime → widened two-sided; `event_risk` state → all quotes pulled.
- Pessimistic fill: a print AT the quote does **not** fill; a print THROUGH it does; fill size = min(print, quote).
- Fee: `fee_per_contract` called WITH `series_ticker`; `KXMLBGAME` charges maker, `KXMLBTOTAL` does not.
- Decay taper: fade size at `secs_since_event=300` < size at `secs_since_event=60`.
- Settlement P&L sign: a faded buy that settles YES is positive; markout sign convention matches P-016.
- Kill file present → no quotes.

---

## 7. Config block (paste into `config_multi_pod.yaml`; leave OUT of `pods.active` — runs standalone)

```yaml
pods:
  P-018:
    enabled: false          # standalone via scripts/run_inplay_fade_maker.py
    sports: ["mlb"]         # add "nba" after NbaAdapter + NBA WP model land
    quoting:
      surprise_hi: 0.06          # |wp_jump| >= this => fade regime  [PAPER-TUNE from backtest]
      surprise_lo: 0.02          # |wp_jump| <  this => expected/widen regime
      min_underreaction: 0.015   # require market lag before fading
      fade_window_s: 240         # seconds post-event to fade (decay ~40%/min)
      fade_size: 20              # contracts, tapered by secs_since_event
      expected_widen: 0.03       # extra half-width in the expected regime
      base_half_width: 0.025     # P-016 defaults reused for the normal regime
      max_half_width: 0.06
      flb_skew_coef: 0.04        # keep P-016's favorite-longshot lean
      inv_skew_coef: 0.5
      max_quote_px: 0.95
      min_quote_px: 0.05
    risk:
      max_net_contracts: 100
      max_pod_exposure_pct: 0.25   # honored via AggregateRiskGuard.reserve_trade
```

---

## 8. Pre-registered kill / promote gates (do not tune mid-flight)

1. **Gate the gate.** Over the backtest and ≥500 live paper fills: fade-regime fills must be net-positive AND expected-regime/anti-window fills net-negative-or-flat. If the surprise gate does not separate the two, **kill** — the pod has no reason to exist over P-016.
2. **Markout-adjusted P&L > 0** over ≥500 fills, robust to dropping the single best day, measured at 1/5/15-min **and** event-clocked horizons (see instrumentation spec).
3. **Adverse selection survivable.** VPIN/one-sidedness at time of fill must not predict losses that swamp spread capture (instrumentation spec).
4. Sample unit is the **game**, not the fill (within-game correlation). No promote before ~8–10 games clear per sport with net edge > half the backtest baseline. Kill any sport whose measured edge drops below half baseline (behavioral edges decay).

---

## 9. Build order (suggested for Claude Code)

1. `book_capture` prerequisite check — confirm in-play tick logs exist; if not, stand up 2–3 weeks of collection **before** the backtest (flag to Sam; don't fabricate).
2. `inplay_research/backtest_inplay_fade.py` + REPORT + params → **decision point** (kill if gate is flat).
3. `inplay_sport_adapter.py` (MlbAdapter) + `inplay_fade_maker.py` engine, copying P-016 loop.
4. Tests green (`pytest tests/test_inplay_fade_maker.py`).
5. `scripts/run_inplay_fade_maker.py` + systemd unit; start MLB paper collection.
6. Wire instrumentation (markout + VPIN) — shared with P-016/P-017M per the instrumentation spec.
7. NBA adapter + NBA WP model as a fast-follow once MLB clears gate #1.

**Definition of done (phase 1):** backtest REPORT committed with surprise-bucket + decay tables; engine + tests green; paper collection running and logging surprise-tagged fills + markouts; nothing placed live.
