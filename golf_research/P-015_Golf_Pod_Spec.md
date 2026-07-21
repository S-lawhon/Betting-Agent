# P-015 — Golf Weekly-Cycle Pod (Kalshi top-N props)

*Spec v1, 2026-07-19. Companion to `GOLF_KALSHI_RESEARCH.md`. Fits the v2 plan (Kalshi-only, CLV-gated, paper-first) as a low-cadence complement to the MLB pod — it reuses the engine, the net-edge gate, and the CLV harness; the only new pieces are a golf fair-value source and a maker-order execution mode.*

## Strategy (three legs, one pod)

**Leg A — Pre-tournament cheap-YES (Mon–Wed).** Buy YES on top-10/20 (and top-40 at majors) contracts where fair value exceeds ask by the net-edge gate. Edge sources: retail concentration on stars leaves mid-tier underpriced; tie-inflation (+13% mass on top-20) is mechanical. Measured H1-2026: +5.3¢/contract even as taker. Execution: rest bids 1–2¢ below ask first; take only when `fair − ask − fee(P) ≥ 3¢`.

**Leg B — In-tournament fade (Fri after cut – Sat).** Rest NO-side quotes (equivalently, offer YES) on 5–40¢ top-N contracts whose fair value sits well below bid/mid — the hope-premium markdown lag. Measured maker capture: ~+8¢ gross, zero maker fee. Maker-only: never cross to fade (per the DataGolf mechanical-FLB critique). Pull quotes on WDs, weather delays, and while any player in the name's pairing is mid-hole on a volatile hole (adverse-selection guard: simple “round in progress + leaderboard delta since quote” staleness check).

**Leg C — Make-cut cheap-YES (Mon–Wed, smaller).** Buy underpriced make-cut YES at 5–40¢ (measured −4.1¢ reverse bias). Half the sizing of Leg A until it independently validates.

Out of scope v1: winner markets (efficient, maker fees, institutional MM), 3-balls/H2H (spreads eat the edge at current liquidity), live round-leader (Phase-3 option; big build). LPGA/LIV: enable in paper only, same logic, once PGA legs validate.

## Fair value

`fair = w_dg × DataGolf + w_mkt × devig(consensus)` with `w_dg = 0.5` initial (shade-to-market per research §1.5; make weights config-tunable, recalibrate quarterly from CLV decomposition).

- **DataGolf API** (requires Scratch Plus membership; confirmed endpoints: *Pre-Tournament Predictions* with make-cut/top-20/top-5/win probabilities, *Live Model Predictions* during rounds, plus a historical predictions archive for backtesting): ties handled correctly — this is the primary anchor; the subscription is the only new recurring cost.
- **De-vigged consensus** via existing The Odds API integration (golf outright + top-N where offered; use **power/Shin de-vig, not proportional** — proportional de-vig systematically misprices longshot-heavy golf fields).
- **Dead-heat correction:** when a book-derived top-N probability is used, multiply by the tie-inflation factor (empirical: ~1.13 for top-20, ~1.04 for top-10; recompute monthly from settled data — `analyze_candles.py`).
- Degradation: if DataGolf feed is stale > 30 min in-tournament, quote only Leg A (pre-tournament) logic; if both sources stale, flat.

## Engine integration

- `SportModel` implementation: `src/models/golf_topn.py` → feeds existing edge/Kelly/risk pipeline; new `maker` execution flag on the order path (rest → reprice on interval → cancel on staleness), reusable later by every pod.
- Net-edge gate (already in v2 plan): `net = fair − price − fee(P) − half_spread` for takes; `net = quote_price − fair` (fee 0) for makes; thresholds: take ≥ 3¢, make ≥ 4¢.
- Sizing: quarter-Kelly on `net`, per-name cap 1% of pod bankroll, per-tournament cap 15%, and a correlation haircut: treat all YES longs in one event's top-N complex as one position at 50% weight (slots crowd out).
- CLV logging per v2 schema: log fair-at-execution, DataGolf-at-close, de-vigged-close, realized outcome. Golf CLV reference = DataGolf close (primary) + de-vigged Pinnacle outright close (secondary).
- Market discovery: series tickers `KXPGATOP5/10/20/40`, `KXPGAMAKECUT` by event; events via `/events?series_ticker=` (collection scripts in `golf_research/` are the template; note API's `orderbook_fp` dollar format and sub-penny ticks).

## Validation & kill criteria (paper first, per v2 discipline)

- **Paper from next week's event (3M Open, markets typically list Monday)** — every leg logs CLV + realized net edge vs the measured H1 baselines.
- Go/no-go after ~8 tournaments (≈ mid-Sept, FedEx playoffs): Leg A validates if realized net CLV > 0 with t ≥ 2 *treating each tournament as one observation*; Leg B validates on maker fill quality (fills ≥ 30% of quoted names) AND positive net capture; kill any leg at t ≤ −1 or if measured edge < half of H1 baseline (decay signal, per research §1.8).
- Graduate to small real money only per v2 Phase-3 rules. Expected realistic economics if H1 edges hold at the literature's ~30% realization rate: low four figures per season at $5–10k bankroll — this is a *complement* to MLB, not a replacement; its research value (maker infrastructure, second CLV-validated sport) exceeds its direct P&L.

## Build estimate

Reuses: pod framework, Kelly/risk, trade store, CLV harness (v2 Phase 0), Odds API client. New: DataGolf client (~half day), golf discovery + settlement mapping (~1 day, scripts exist), maker order lifecycle (~2–3 days, shared infra), tie-factor + de-vig utilities (~half day). ≈ one focused week, after v2 Phase 0 lands.
