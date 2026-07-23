# Claude Code Task — P-020: Cross-Venue Signal (Polymarket → Kalshi, Politics/World)

> Paste this whole file into Claude Code as the task. Full spec lives at
> `research/SPEC_P020_CrossVenue_Signal.md` — read it first, it is the source of truth.

## Role & context
You are working in the **Betting Pod Shop** repo (`~/Desktop/Betting Fund Project`), a paper-mode Kalshi trading engine. Paper/demo only — **no real orders, ever**. Read `CLAUDE.md` and `PROJECT_STATUS.md` for conventions before writing code.

Thesis: Polymarket is deeper than Kalshi on **politics/world** events, so its mid is a sharper fair value there. When Kalshi's retail price diverges from Polymarket beyond the fee corridor, the Poly-implied direction is a **CLV-gated taker signal on Kalshi** — a single-venue trade (we have Polymarket **data only, no execution**), structurally identical to P-001.

## Non-negotiable guardrails
- **Backtest FIRST. Do not build a pod or collector until the backtest gate passes.** (P-019 was killed at exactly this gate — expect the same discipline. The burden of proof is on the thesis; see the caveat below.)
- **CRITICAL PRIOR:** our own ev-map study (H2) found the Kalshi↔Polymarket basis is **fee-bounded** in the liquid shared head — a ~±2¢ no-arb corridor. So the edge **cannot** live where both venues are liquid and agree; it must live where **Polymarket is genuinely deeper and Kalshi retail overreacts/lags**, and it must clear the corridor to settlement. A result that only "works" at a sub-corridor threshold is a **KILL**, not a maybe.
- **READ-ONLY Polymarket.** Use only read paths in `src/polymarket_client.py` (`get_sport_events`, `get_book`, `get_midpoint`, `get_price`, `find_series_id`). **Never call `place_order`/`cancel_order`** — they target the offshore CLOB, are close-only/unusable from the US, and must not be invoked. Add a test asserting no order paths are called.
- Reuse: `src/cross_venue_matcher.py` (matching), `src/kalshi_public.py`, `src/kalshi_fees.py`, `src/clv.py`, and `scripts/collect_inplay_basis.py` as the collector template.
- **Settlement-definition mismatch is the main failure mode.** Only keep pairs whose YES resolves on the *same* event definition; log a match-confidence per pair and drop low-confidence pairs — do not fudge.
- Cluster all statistics by **event**, not by contract.
- **Kalshi API gotcha:** list endpoints null bid/ask; use `orderbook`/candlesticks per market.

## Phase 1 — Cheapest falsification (settled data; DO THIS ALONE FIRST, then STOP and report)
Create `crossvenue_research/backtest_crossvenue.py`.

1. Assemble ~100–200 **already-settled** Kalshi political/world markets with a confident Polymarket match via `cross_venue_matcher`.
2. For each, reconstruct a time series of {Poly mid, Poly depth, Kalshi mid, Kalshi taker-fee corridor} over the market's life (Kalshi candlesticks + Poly historical/Gamma). Log data-quality/match-confidence per pair; **filter out observations where the Poly book is thin** (a gap vs a thin Poly book is not a sharper reference — same lesson as the in-play basis collector).
3. Strategy under test: "when `abs(poly_mid − kalshi_mid) > corridor + MARGIN`, move Kalshi toward Poly." Measure: (i) does the Poly-implied direction beat Kalshi's own price on Brier/log-loss vs realized outcomes; (ii) net-of-fee settlement PnL by gap-size bucket; (iii) **CLV vs Kalshi's own close**; all **event-clustered**.
4. Write `crossvenue_research/REPORT_CrossVenue_2026-07.md` including a **corridor-sensitivity table** (does the edge survive realistic corridors, or only implausibly tight ones?). Persist `crossvenue_research/p020_params.json`.

**GATE — stop and report the verdict:**
- **KILL** if the signal does not beat Kalshi on Brier AND produce positive CLV net of a realistic corridor, event-clustered — or if it only works below the corridor.
- **ADVANCE** only if it clears a realistic corridor with positive event-clustered CLV.

Do not start Phase 2 until I approve based on the REPORT.

## Phase 2 — Collector + pod (only after I approve the Phase-1 REPORT)
- `scripts/collect_crossvenue_politics.py` — standalone **read-only** collector (mirror `scripts/collect_inplay_basis.py`) + `scripts/betting-crossvenue.service`; forward-collect live matched pairs to JSONL.
- `src/pods/crossvenue_signal.py` — `BasePod`, `@register_pod("P-020")`, taker on Kalshi toward Poly when the gap clears the corridor; CLV-gated; depth-capped.
- `tests/test_crossvenue_signal.py`: match-confidence gating; corridor math; Poly-depth filter; **READ-ONLY guard (assert no Poly order paths called)**; net-edge gate.
- Config: add a `P-020` block to `config_multi_pod.yaml`, left OUT of `pods.active` until forward paper validates.
- (Optional, do NOT build first) a maker variant on slow politics markets — only after the taker validates, markout-gated.

## Definition of done (Phase 1)
`REPORT_CrossVenue_2026-07.md` committed with event-clustered signal-vs-Kalshi Brier, net-of-fee PnL by gap bucket, CLV, and a corridor-sensitivity table; an explicit KILL-or-ADVANCE verdict that directly confronts the H2 tight-corridor prior; `p020_params.json` written; no collector/pod/service built; nothing placed live.
