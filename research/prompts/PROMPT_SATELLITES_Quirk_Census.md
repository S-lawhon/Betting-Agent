# Claude Code Task — Quirk Satellites: Award Ties, RT Fallback/Drift, WINS Partition Scanner

> Background: `Deep Research R4 - Terms Census & Chart Markets 2026-07.md` §4–6. Three small independent settled-data censuses in one task. Research only, then STOP.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper-mode; **no real orders, ever**). Kalshi API gotchas apply throughout: list endpoints null bid/ask (use candlesticks/orderbook), settled `settlement_value_dollars` IS the realized payout, shared 2 req/s budget — cache everything under `satellites_research/data/`. Cluster stats by event. Fetch and quote the operative rule clause in each section of the REPORT before analyzing (PDFs under `https://kalshi-public-docs.s3.amazonaws.com/contract_terms/`).

## Study A — Award tie regimes (GLOBES / CRITICS / AMAS)
Verified rule: in these three templates a **tied winner's strike pays No** — the separate "tie" strike pays instead. (OSCARS/EMMYS/GRAMMY/ACMA are silent on ties → discretion → EXCLUDE, not tradeable.)
1. Enumerate settled markets across the three templates (~107 series). Census: how many events carry an explicit "tie" strike; what did tie strikes trade at (last pre-close candle) vs how often ties actually occurred historically in those award shows (research base rates per show/category type — cite sources).
2. Compute: is the tie strike systematically under- or over-priced vs its base rate? Is the favorite's YES haircut (P(outright win only)) visible in prices?
3. Verdict: tradeable edge ≥2¢/ct on either side (event-clustered), or dead.

## Study B — RT family fallback + read-time drift (RT.pdf / RTTV.pdf, 238 series)
Verified rules: settlement reads the Tomatometer at **Monday 10:00 AM ET after wide release** (RTTV: day 3 after premiere); if no data a week later, **ALL strikes resolve No** (including "below X" — the ladder is not a partition).
1. Census settled RT/RTTV events: count all-No settlements (the fallback firing) and, where it fired, what the "safe" low-bracket YES traded at (the uncompensated tail).
2. Drift study: for settled titles, compare the publicly archived opening-weekend score vs the Monday-10AM settlement value (Wayback Machine snapshots of RT pages where retrievable; log coverage honestly). Direction and magnitude of drift by review-count bucket; did closing prices anchor on the early score?
3. Verdict per mechanism: NO-side convexity worth buying on fringe titles? Drift tradeable ≥2¢ net?

## Study C — WINS/EXACTWINS partition coherence scanner (live, not settled)
Templates verbatim-identical across NFLWINS/NFLEXACTWINS/MLBWINS (95 series; NFL season series listing now = thin first-mover window).
1. Build `satellites_research/wins_scanner.py`: for each team, pull live orderbooks for the EXACTWINS partition and the WINS ladder; check coherence: sum of exact-N mids ≈ 1; ladder increments ≈ exact buckets; P(≥N) monotone decreasing.
2. Flag violations exceeding fees + spread (use executed-trade/tight-two-sided prices only — bare asks on empty books fabricate arbs, per the P-022 lesson).
3. Output a violations table; if any persist across two scans 1h apart, quantify the locked profit net of taker fees. This is a scanner deliverable, not a pod.

## Deliverable
`satellites_research/REPORT_Satellites_2026-07.md` with three sections, each ending in an explicit verdict (ADVANCE ≥2¢ event-clustered / KILL / MARGINAL), plus the scanner script + cached data. **No pods. STOP at the REPORT.**
