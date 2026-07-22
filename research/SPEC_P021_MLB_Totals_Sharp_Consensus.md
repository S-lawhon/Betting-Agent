# P-021 — MLB Totals / Run-Line vs Sharp-Book Consensus (Kalshi)

*Build/test spec v1, 2026-07-22. For handoff to Claude Code. Backtest-first, kill-gated. Direct extension of P-001 (our one live winner) into the maker-free MLB derivative series. Ranked #1 this round on testability + durability.*

---

## 1. One-paragraph thesis

P-001 works: Kalshi game moneylines (`KXMLBGAME`) lag a Pinnacle-weighted sportsbook consensus, and trading the devigged gap earns positive CLV. P-021 runs the **identical pipe** on Kalshi's **totals and run-line** series — `KXMLBTOTAL`, `KXMLBSPREAD`, `KXMLBF5` — which are the *efficient* part of the sports market (so the sharp reference is genuinely sharp) and, confirmed live 2026-07-22, are **`quadratic` fee-type → ZERO maker fee** (taker ≤1.75¢/ct). The one honest risk is that Kalshi may also be near-efficient on totals, making the edge thin; the backtest settles that before any capital. This is explicitly **not** the failed "our model beats props" idea (a public box-score model carries no info Kalshi lacks) — the reference is a **sharp book's line**, which aggregates sharp money.

**Status target:** backtest-first. No pod ships until the settled-data gate (§4) passes with positive CLV, day-clustered.

---

## 2. Relationship to existing pods / infra (reuse, do NOT rebuild)

| Existing | Reuse for P-021 |
|---|---|
| **P-001** (`src/pods/kalshi_moneyline.py` → `Legacy/Kalshi Arb Project/src/scanner.py`) + its Odds API client & settler | **Structural template + the Odds API access path.** P-021 is P-001 with different Kalshi series and a totals/spread devig. |
| The Odds API (paid key `ODDS_API_KEY`) | Sharp reference. `KXMLBTOTAL`/`KXMLBSPREAD`/`KXMLBF5` map to Odds API `totals`, `spreads` (run line), and F5 alt markets; **Pinnacle is in the `eu` region**. |
| `src/devig.py` (`devig_power` Shin + multiplicative) | Devig the two-way sharp line to a fair prob. Use proportional/Shin; log both. |
| `src/kalshi_public.py` (`open_markets`/`orderbook`/`trades_since`, `fnum`) | Kalshi price at decision time. Remember: list endpoints null bid/ask → use `orderbook`/candlesticks. |
| `src/cross_venue_matcher.py` | Match Odds API game → Kalshi event (team codes, ET datecode/HHMM), same idiom P-016 discovery uses. |
| `src/kalshi_fees.py` (`fee_per_contract(price, maker, series_ticker=...)`) | **Pass `series_ticker`** — these MLB derivative series return maker fee 0; do not hardcode. |
| `src/clv.py` | CLV logging → the promote gate (same as P-001). |
| `Legacy/.../settler.py` via Odds API `/scores` | Settlement of totals/run-line outcomes (Kalshi demo never settles sports). |

P-021 can run as a `BasePod` in the 5-minute engine (like P-001/P-017) once it clears the backtest — it's a slow, pregame taker, not a fast loop.

---

## 3. Series mapping + the sharp reference

| Kalshi series | Fee | Odds API market | Devig target |
|---|---|---|---|
| `KXMLBTOTAL` (game total runs, `Over N` ladder) | maker 0 | `totals` (Pinnacle-eu + no-vig multi-book consensus) | P(total > line) per strike |
| `KXMLBSPREAD` (run line) | maker 0 | `spreads` | P(cover) |
| `KXMLBF5` (first-5-innings) | maker 0 | F5/alternate (if covered; else skip) | P(F5 outcome) |
| *player props (`KXMLBHIT`, `KXMLBKS`, `KXMLBTB`, `KXMLBHR`)* | maker 0 | per-event props endpoint (10× hist cost) | **speculative leg — gate behind totals** |

**Reference construction:** build **two** fair-value series and keep both through analysis — (a) **Pinnacle-eu only** (single sharp book, devigged), (b) **no-vig multi-book consensus with all DFS/soft books EXCLUDED** (PrizePicks/Underdog `us_dfs` are not sharp — exclude explicitly). Player-prop keys from Pinnacle-eu are **unconfirmed**; verify on one live event before spending historical prop pulls.

---

## 4. Backtest FIRST — the gate (this is the deliverable that decides everything)

Create `mlb_totals_research/backtest_totals.py` (mirror the golf/longshot backtest harness pattern).

**Cheapest falsification (run this alone first, ~1 day, minimal API spend):**
1. Universe: `KXMLBTOTAL` only, ~200 recent **settled** games.
2. For each, pull the **Pinnacle-eu closing total** (Odds API historical; note 10× cost for props but totals are cheap featured markets) and devig → `p_sharp` per strike.
3. Pull Kalshi price near close (`orderbook` mid / `trades_since` at T-1h) → `p_kalshi`.
4. **Tests:** (i) Brier/log-loss of `p_sharp` vs `p_kalshi` against realized outcomes; (ii) regress realized 0/1 on the gap `(p_sharp − p_kalshi)`, **day-clustered SEs** (same-day games share pitcher/weather shocks); a positive, significant gap coefficient = the sharp line has info Kalshi lacks; (iii) simulated net-of-fee PnL taking when `p_sharp − p_ask > fee + margin`, flat-sized; (iv) **CLV vs Kalshi's own close** — the true survival criterion, identical to P-001's gate.
5. **Deliverable:** `REPORT_MLB_Totals_2026-07.md` with the day-clustered gap coefficient, Brier comparison, net-of-fee PnL, and CLV. Persist `p021_params.json` (which series, horizon, min-gap threshold, devig method).

**Gate:** if devigged Pinnacle totals do **not** beat Kalshi on Brier *and* the gap coefficient is insignificant on this most-efficient, cheapest series — **kill P-021** (the softer player props won't rescue it). Only if totals pass do you spend the 10×-cost historical prop pulls to test `KXMLBKS`/`KXMLBHIT` as a second leg.

---

## 5. Pod build (only if the gate passes)

```
src/pods/mlb_totals_value.py     # BasePod, @register_pod("P-021"); mirrors kalshi_moneyline/P-001
  - discover: Odds API MLB events → devig totals/spreads → match to Kalshi via cross_venue_matcher
  - scan_once: for each matched strike, fair = p_sharp (conservative shade toward Kalshi, lower-CI
    convention like P-017); ScanResult when net_edge(fair, ask, maker/taker, series_ticker=...) > min
  - depth-capped; max_open_positions sized to aggregate_risk.max_pod_exposure_pct (0.25)
config_multi_pod.yaml  # P-021 block; LEFT OUT of pods.active until gate passes, then flip one line
tests/test_mlb_totals.py  # devig correctness; DFS-book exclusion; fee=0 via series_ticker;
                          # match logic; net-edge gate; taker-vs-maker path
```

Settlement: reuse the Odds API `/scores` settler (totals/run-line are scoreable). CLV logged via `src/clv.py`.

---

## 6. Pre-registered kill / promote gates (P-001 methodology)

- **Backtest gate (above):** positive, day-clustered gap coefficient + positive CLV on `KXMLBTOTAL`, or kill.
- **Forward paper gate:** promote toward real money only after forward CLV tracks the backtest (P-001 uses ~200 CLV rows). Sample unit = **game-day**, not contract.
- **Player-prop leg:** stays off until the totals leg passes AND a separate prop backtest shows the sharp/consensus prop line beats Kalshi (do not assume it does — the reference is weaker there).
- **Durability monitors:** re-check `series_maker_charges_fee` on these series periodically (Kalshi could add prop maker fees, which would erase the fee tailwind); retire if forward CLV goes negative.

---

## 7. Build order

1. `mlb_totals_research/backtest_totals.py` → cheapest `KXMLBTOTAL`-only falsification → `REPORT` → **decision point**.
2. If pass: `src/pods/mlb_totals_value.py` + tests green.
3. Wire settler + CLV; add to `pods.active` in paper.
4. Only after totals validate in paper: prop-leg backtest, then optional prop pod.

**Definition of done (phase 1):** backtest REPORT committed with the day-clustered gap coefficient, Brier comparison, net-of-fee PnL, and CLV vs Kalshi close; clear kill-or-advance verdict; nothing placed live.
