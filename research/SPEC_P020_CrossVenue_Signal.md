# P-020 — Cross-Venue Signal: Polymarket Oracle → Kalshi (Politics/World)

*Build/test spec v1, 2026-07-22. For handoff to Claude Code. Backtest-first, kill-gated. The P-001 archetype (lag a sharper free reference, CLV-gated) applied to Kalshi politics/world markets using Polymarket as a free fair-value oracle. Ranked #2 this round on testability + durability; highest capacity of any candidate.*

---

## 1. One-paragraph thesis

Polymarket is materially deeper than Kalshi on **politics and world events**, so its mid is a sharper fair value there. When Kalshi's retail-driven price diverges from Polymarket beyond the fee corridor, the Poly-implied direction is a **CLV-gated taker signal on Kalshi** — a single-venue trade (we have Polymarket *data* access only, no execution), structurally identical to P-001 trading Kalshi vs sportsbook consensus. We already own the clients: `polymarket_client.py` (read paths), `kalshi_public.py`, `cross_venue_matcher.py`.

**The load-bearing caveat, stated up front:** our own prior study (ev-map H2) found the Kalshi↔Polymarket basis is **fee-bounded in the liquid shared head** — a ~±2¢ no-arb corridor keepers stay inside. So P-020's edge **cannot** live where both venues are liquid and agree. It must live where **Polymarket is genuinely deeper and Kalshi retail overreacts/lags**, and it must clear the corridor to settlement. Prior work never tested that — it compared quotes synchronically, never followed divergences to realized outcomes. This spec's backtest is exactly that missing test. If it doesn't clear, we kill it cheaply, same as P-019.

**Status target:** backtest-first on settled data. No pod until the gate (§4) passes with positive CLV, event-clustered.

---

## 2. Relationship to existing infra (reuse, do NOT rebuild)

| Existing | Reuse for P-020 |
|---|---|
| `src/polymarket_client.py` (`get_sport_events`, `get_book`, `get_midpoint`, `get_price`, `find_series_id`; Gamma + CLOB hosts wired) | **Read-only oracle.** Poly mid/depth. **Use READ paths ONLY** — `place_order`/`cancel_order` target the offshore CLOB and are close-only/unusable from the US; the collector must never call them. |
| `src/kalshi_public.py` | Kalshi price/book at decision time (orderbook mid; list endpoints null bid/ask). |
| `src/cross_venue_matcher.py` (`match_all`, `normalise_market_title`, city/entity resolution) | Match Kalshi ↔ Poly contracts. **Settlement-definition matching is the main trap** — only keep pairs whose YES resolves on the *same* event definition; log a match-confidence and drop low-confidence pairs. |
| `scripts/collect_inplay_basis.py` | **Template for the collector** (standalone, read-only, JSONL append, systemd unit `betting-inplay-basis.service`). P-020's collector is the politics analogue. |
| `src/kalshi_fees.py` | Fee corridor per contract (`fee_per_contract(price, maker/taker, series_ticker=...)`). Politics series are `quadratic` → maker 0. |
| `src/clv.py` | CLV logging → promote gate. |

---

## 3. The signal

For each matched Kalshi↔Poly pair, each observation:

```
corridor   = kalshi_taker_fee(kalshi_price) + poly_half_spread + match_slippage_buffer
gap        = poly_mid - kalshi_mid                      # Poly is the sharper reference
signal     = gap  if abs(gap) > corridor + MARGIN else 0
# taker signal: if signal>0 (Poly higher), BUY Kalshi YES; if signal<0, BUY Kalshi NO.
# CLV-gate + settle to realized outcome. Optional maker variant (§6) rests toward Poly mid.
```

Only act where (a) Poly depth ≫ Kalshi depth (Poly is genuinely the sharper side — filter on Poly liquidity), (b) match confidence is high, (c) the gap exceeds the corridor plus a margin. Politics/world only (where Poly is deep); do NOT apply to the liquid shared head (sports headline) where H2 already showed no edge.

---

## 4. Backtest FIRST — the gate

Create `crossvenue_research/backtest_crossvenue.py`.

**Cheapest falsification (settled data, one afternoon, no forward collection):**
1. Assemble ~100–200 **already-settled** Kalshi political/world markets with a confident Polymarket match (via `cross_venue_matcher`).
2. For each, reconstruct a time series of {Poly mid, Poly depth, Kalshi mid, Kalshi taker fee corridor} over the market's life (Kalshi candlesticks + Poly historical/Gamma; where only endpoints exist, use the cleanest available snapshots and **log data-quality per pair**).
3. **Strategy under test:** "when `abs(poly_mid − kalshi_mid) > corridor + MARGIN`, move Kalshi toward Poly." Measure: (i) does the Poly-implied direction predict the realized outcome better than Kalshi's own price (Brier/log-loss)? (ii) net-of-fee settlement PnL of the signal; (iii) **CLV vs Kalshi's own close** (the survival criterion); all **event-clustered** (contracts within an event correlate — the discipline that killed P-019).
4. **Deliverable:** `REPORT_CrossVenue_2026-07.md` — signal Brier vs Kalshi, net PnL by gap-size bucket, CLV, and an explicit **corridor-sensitivity table** (does the edge survive realistic corridors, or only at implausibly tight ones?). Persist `p020_params.json`.

**Gate:** kill if the signal does not beat Kalshi on Brier AND produce positive CLV net of a realistic corridor, event-clustered. Given the H2 prior, the burden of proof is on the thesis — a marginal result that only works at a sub-corridor threshold is a **kill**, not a maybe.

---

## 5. Data note / honesty flags

- **Settlement-definition mismatch** is the primary failure mode: Kalshi and Poly can word "the same" event differently (date windows, tie handling, resolution source). The matcher must be conservative; every backtest row logs match confidence, and low-confidence pairs are excluded, not fudged.
- **Poly historical depth** may be thin on some politics markets; a gap measured against a thin Poly book is not a sharper reference. Filter on Poly liquidity per observation (the same lesson from the in-play basis collector, where ~$700–900 Poly books made top-of-book basis meaningless — use mid + depth-weighted, log spread).
- **No Polymarket execution.** This is a one-venue Kalshi trade signalled by Poly data; it is not a two-legged arb and must never be represented as one.

---

## 6. Pod build (only if the gate passes)

```
scripts/collect_crossvenue_politics.py   # standalone read-only collector (mirror collect_inplay_basis.py)
                                          # + betting-crossvenue.service ; forward-collects live pairs
src/pods/crossvenue_signal.py            # BasePod, @register_pod("P-020"); taker on Kalshi toward Poly
config_multi_pod.yaml                    # P-020 block; OUT of pods.active until gate passes
tests/test_crossvenue_signal.py          # match confidence gating; corridor math; Poly-depth filter;
                                         # READ-ONLY guard (assert no Poly order paths called); net-edge
```

Optional **maker variant** (note, do not build first): on *slow* politics markets adverse selection is far lower than the in-play MLB books that killed P-016, so resting a Kalshi quote toward Poly mid at zero maker fee is defensible — but only propose it after the taker signal validates, and gate it on markouts like any maker.

---

## 7. Pre-registered kill / promote gates

- **Backtest gate (above):** signal beats Kalshi on Brier + positive CLV net of a realistic corridor, event-clustered — or kill.
- **Forward paper gate:** forward CLV tracks the backtest before any real-money discussion; sample unit = **event**, not contract.
- **Durability monitor:** cross-venue bots compress the largest gaps; retire any sub-family whose gaps stop clearing the corridor. Re-confirm the H2 corridor width periodically.

---

## 8. Build order

1. `crossvenue_research/backtest_crossvenue.py` → settled-data falsification → `REPORT` + corridor-sensitivity table → **decision point** (kill if it only works sub-corridor).
2. If pass: `scripts/collect_crossvenue_politics.py` + systemd unit; start forward collection.
3. `src/pods/crossvenue_signal.py` (taker) + tests green; add to `pods.active` in paper.
4. Only after taker validates: consider the maker variant, markout-gated.

**Definition of done (phase 1):** backtest REPORT committed with event-clustered signal-vs-Kalshi Brier, net-of-fee PnL by gap bucket, CLV, and a corridor-sensitivity table; explicit kill-or-advance verdict that honestly confronts the H2 tight-corridor prior; nothing placed live.
