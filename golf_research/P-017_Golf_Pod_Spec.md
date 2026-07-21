# P-017 — Golf Top-N Pod (Kalshi)

*Spec v2, 2026-07-20. Supersedes the earlier P-015 draft (that ID belongs to the Tennis Qualifier pod; the Live Maker is P-016 — golf is **P-017**). This version reflects what is now BUILT and BACKTESTED, not just proposed. Full evidence: `GOLF_KALSHI_RESEARCH.md` (literature + market data) and `backtest/REPORT_Golf_TopN_2026-07.md` (decision-logic replay).*

## What shipped

Two components, both paper, both reusing existing infrastructure (BasePod, KalshiPublic, the P-016 maker idiom, CLV logging):

**P-017 — Golf Top-N Pre-Tournament Value (taker) — VALIDATED.**
`src/pods/golf_topn_pod.py`, a `BasePod` pod (direct analog of P-015 `qualifier_favorite_pod`). Buys YES on KXPGATOP10/20 contracts priced 8–45¢ on the pre-tournament "Wednesday" quote (4–10 days before close). Backtest: **+6.8¢/contract net of taker fee, CI [+3.1, +10.2], 9/10 tournaments positive** (n=926). Structural, model-free edge (tie-inflation + attention). `fair_prob = ask + edge_bump` with a conservative `edge_bump = 0.04`; net-edge gate at 0.02 after the quadratic taker fee; depth-capped; optional signature-event skip. Runs in the 5-minute engine once added to `pods.active`.

**P-017M — Golf Fade Maker (standalone, paper-experimental) — PROMISING.**
`src/golf_fade_maker.py`, a `GolfFadeMakerEngine` mirroring P-016's `LiveMakerEngine`. Rests offers (sells YES) at mid+3¢ on top-N names in the **36→6h-before-close** window, filling only on prints strictly *through* the quote (pessimistic, adverse-selection-inclusive), zero maker fee (prop series are `quadratic`). Backtest: **+9.1¢/contract net** — but on only 4 tournaments of tick data, so it exists to collect live paper fills + markouts and confirm before real money. Critical timing: the 48→24h slice is *negative* (Friday YES still climbing); the edge is Sat→Sun-AM. Run via `scripts/run_golf_maker.py`.

## Supporting pieces built

- `src/golf_fees.py` — series-aware fees: golf prop series (`quadratic`) charge **zero** maker fee; winner series (`quadratic_with_maker_fees`) charge makers. Plus power/Shin de-vig for longshot-heavy fields (the engine's `devig.py` only had multiplicative). Intended to promote `series_maker_charges_fee` into `src/kalshi_fees.py`.
- `tests/test_golf_topn.py` — 10 tests (fees, de-vig, all decision-gate paths). All pass.
- `backtest/` — the replay harness (`backtest_golf.py`), refinement grids (`refine_golf.py`), validated params (`p017_params.json`), and the report. Re-runnable after each new event.
- `p017_config_block.yaml` — config to paste; P-017 deliberately left OUT of `pods.active` (flip one line to enable in paper).

## Not built (deliberately)

Make-cut leg (only 12 two-sided pre-tournament quotes in-sample — insufficient); top-5 (backtest ~0, matches literature); top-40 (1 event); outright winner markets (efficient, maker-fee'd, institutional MM — benchmark only). Live round-leader in-play (large build; the R1-leader flow of 30M contracts/major is a future P-018 candidate).

## Validation & kill criteria (unchanged from methodology)

Paper-first. Go/no-go after ~8 tournaments treating **each tournament as one observation** (within-event outcomes correlate). P-017 validates if forward net CLV / realized net edge stays > half the backtest baseline; P-017M validates on fill quality (≥30% of quoted names fill) AND positive markout-implied net. Kill any leg whose measured edge drops below half baseline (inattention edges decay). Only then consider small real money per the v2 Phase-3 rules.

## Next steps (when you want them)

1. Add `- P-017` to `pods.active` and run the engine in paper through the next event (3M Open week).
2. Run `scripts/run_golf_maker.py` alongside to start P-017M paper collection.
3. Optional: wire the DataGolf API (Scratch Plus) as `fair_prob` to replace `edge_bump` and sharpen name selection — the edge doesn't require it, but it should improve hit rate on the taker leg.
4. Promote `series_maker_charges_fee` into `src/kalshi_fees.py` and `devig_power` into `src/devig.py` so the rest of the engine benefits.
