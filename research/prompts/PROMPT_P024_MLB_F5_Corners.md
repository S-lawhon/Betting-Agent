# Claude Code Task — P-024: MLB F5/RFI Thin-Corner Sharp-Reference Backtest

> Background: `Deep Research R3 - Settlement Quirks & Unwatched Corners 2026-07.md` §3 and the P-021 postmortem (`mlb_totals_research/REPORT_MLB_Totals_2026-07.md`). Phase 1 only, then STOP. This is the heaviest task in the night queue — run it last.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper-mode; **no real orders, ever**). P-021 proved Kalshi's LIQUID pregame MLB totals are as sharp as Pinnacle (Brier tied) — but found a real lead-lag: Pinnacle leads Kalshi's drift (t=+3.21, +1.8¢ CLV), too small to clear fees there. The hypothesis: the same lag is LARGER in the maker-free thin corners — `KXMLBF5`, `KXMLBF5TOTAL`, `KXMLBF5SPREAD` (spreads 3–4¢), `KXMLBRFI` (27–32¢ intraday spreads), `KXMLBTEAMTOTAL` — where attention concentrates on the headline market. Odds API carries the full innings family (`h2h_1st_5_innings`, `totals_1st_5_innings`, `totals_1st_1_innings` = RFI/NRFI, `team_totals`) with history since May 2023, per-event endpoint, Pinnacle in region `eu`.

## Non-negotiable protocol (thin books make mid-based backtests lie in our favor)
- Measure every edge against **executed Kalshi trade prints and the touch** (candlestick-derived bid/ask), NEVER bare mids; a bare one-sided ask on an empty book is not a price.
- Require the sharp-implied prob to be strictly **through** the touch (we'd cross, or rest and be filled through).
- Haircut simulated size to the entry-hour `volume_fp`; report median executable $ per market.
- **Lag-align conservatively:** Odds API Pinnacle-eu "may incur a delay" — pair t−10min sharp snapshots vs t Kalshi so the lead-lag isn't feed-latency artifact.
- Day-clustered SEs throughout (the P-021 harness discipline; reuse `mlb_totals_research/backtest_totals.py` structure and its devig path via `src/devig.py`).
- Mind Odds API historical pricing: featured markets are cheap; **per-event/period markets cost more** — sample ~60–100 recent game-days rather than exhaustive history, and log API spend as you go. Stop pulling if projected credits exceed a reasonable run (state your budget in the REPORT).

## Task
1. Start with `KXMLBF5` + `KXMLBF5TOTAL` (best liquidity of the thin set). Match settled Kalshi markets to Odds API events (reuse `cross_venue_matcher` idioms); devig Pinnacle-eu F5 lines → `p_sharp`.
2. At T−1h and T−15min before first pitch: Kalshi touch/prints vs `p_sharp`. Run the P-021 triple: (i) Brier `p_sharp` vs Kalshi price on realized outcomes; (ii) gap→outcome regression, day-clustered; (iii) simulated net-of-fee PnL under the print-based fill protocol; (iv) CLV vs Kalshi close.
3. `KXMLBRFI` separately and ONLY print-based near lock (the 27¢ daytime spread makes anything else garbage): prints vs contemporaneous Pinnacle NRFI prob; count usable prints/season.
4. Report the lead-lag amplification test explicitly: is the gap materially larger than P-021's +1.8¢ in these corners, and does executable size exist?

## Gate
- **ADVANCE** if: gap-regression b positive & significant (day-clustered) AND executable (print-based) net edge ≥2¢/ct AND median executable size ≥$100/market on F5; RFI additionally needs ≥300 usable prints/season.
- **KILL** if the corners are as sharp as the headline (the P-021 result repeating) or the edge exists only on unfillable quotes.

Deliver `mlb_f5_research/REPORT_MLB_F5_2026-07.md` + `p024_params.json` + cached pulls. **No pod. STOP at the REPORT.**
