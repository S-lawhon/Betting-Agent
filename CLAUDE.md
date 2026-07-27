# CLAUDE.md — Betting Pod Shop

Multi-pod sports betting engine (Kalshi-focused) in **paper/demo mode**. Runs on a
DigitalOcean VPS (129.212.176.202) as systemd service `betting-pod-shop` (NOT
`bettingbot` — that is the unix user the service runs as; `systemctl status
bettingbot` reports "unit could not be found"). The P-016 live maker is a second
unit, `betting-live-maker`. Also runnable locally. Python 3.12. This file orients a fresh Claude Code session — deeper detail
lives in the docs linked below.

## Run / test / deploy

```bash
# tests (pytest)
python3 -m pytest tests/ -q

# run the 5-minute engine locally (paper)
python3 -m src.main            # or src/cli.py — see main.py

# deploy to VPS (rsync + chown to bettingbot + restart + health check)
bash scripts/deploy.sh 129.212.176.202 restart
#   ALWAYS preserves live data: deploy excludes data/ ; chown after rsync is required
```

## Architecture (essentials)

- Pods implement `BasePod` (`src/base_pod.py`): `scan_once() -> [ScanResult]`,
  `venue_name()`, `from_config()`. Register with `@register_pod("P-0XX")`
  (`src/pod_registry.py`). The engine auto-discovers pods in `src/pods/`.
- Standalone maker engines (P-016, P-017M) run their own fast loop via a
  `scripts/run_*.py`, NOT inside the 5-minute engine — do not add them to
  `pods.active`.
- Shared infra: `edge_calculator`/Kelly, `capital_allocator`, `aggregate_risk`,
  `trade_store` (+ `trade_log_schema`), `clv.py` (CLV harness), `devig.py`,
  `kalshi_fees.py`, `kalshi_public.py` (read-only public market data client).
- Config: `config_multi_pod.yaml` merges over base `config.yaml`. Per-pod blocks
  under `pods:`; enabled set in `pods.active`.

## Pod IDs (do not reuse)

P-001 Kalshi moneyline · P-002 cross-venue arb · P-006 sportsbook-Polymarket
consensus · P-014 live game agent · **P-015 tennis qualifier** · **P-016 live MLB
maker** · **P-017 golf top-N (taker)** · **P-017M golf fade maker (standalone)**.
Disabled/legacy: P-004, P-009, P-010, P-012, P-013.

## Kalshi API gotchas (hard-won — a coding agent WILL hit these)

- **Event timing is unreliable per-market.** Top-N `close_time`,
  `expiration_time`, and often `occurrence_datetime` are set to a far-future
  conservative default; only a few markets per event carry the real end date.
  Resolve ONE event-level close = the MIN occurrence/close across the event's
  markets and apply to all (see `GolfTopNPod._resolve_event_closes`).
- **On an OPEN market there is NO field carrying the real close.** This is the
  generalisation of the point above and it has now cost P-022 two separate
  fixes. `close_time`, `occurrence_datetime`, `expiration_time`,
  `expected_expiration_time` and `latest_expiration_time` all collapse to one
  conservative fallback while the market is open; `close_time` is REWRITTEN to
  the true value only at the moment the market closes (these markets carry
  `can_close_early: true` / "will close and expire after a winner is
  declared"). Measured 2026-07-27: three round-leader events on three
  different tours, 346 markets, every field `2026-08-16T00:00:00Z`, listed
  `+19.99d` earlier. Exchange-wide, not a golf quirk — `KXMLBGAME` markets sit
  open with `close_time` three days past their own first pitch (the real game
  time is in the TICKER, not a field). **Corollary: never measure a time field
  on a SETTLED market and infer how it behaves on an open one** — settled is
  the only state in which `close_time` has been corrected, which is exactly how
  the 2026-07-26 P-022 reconciliation concluded the opposite of the truth. Any
  strategy whose window is defined relative to the determining event needs an
  EXTERNAL schedule; Kalshi does not publish it. See
  `research/REPORT_P022_First_Quote_2026-07.md` and
  `scripts/p022_window_check.py`. **That external schedule now exists**:
  `src/golf_schedule.py` resolves `(competition, round) → round-end UTC` from
  ESPN's free public golf API for all five tours, keyed on Kalshi's
  `product_metadata.competition` (the only handle Kalshi gives on WHICH
  tournament a listed event is). It is calibrated to err EARLY on purpose —
  validated one-sided on 72 of 72 settled events, min +0.16h — because
  predicting a close LATE puts orders into a live round, and the measured
  round span means the 12h window edge already sits at the first tee. It
  fails CLOSED: a wrong round time is worse than no quote. ESPN publishes no
  tee times ~3 days out, so the coarse per-tour day offset is the path that
  runs at listing time and books must be RE-RESOLVED, not timed once at
  discovery. `research/REPORT_P022_Close_Time_2026-07.md`.
- **Orderbook**: `/markets/{t}/orderbook` returns `orderbook_fp` in DOLLARS with
  sub-penny ticks; best YES ask = 1 − best NO bid (see `KalshiPublic.orderbook`).
- **Settled-market LIST endpoints null out** volume/price/last — use candlesticks
  for historical prices. Freshly-settled events leave `result` unpopulated ~1 day.
- **Fees are series-aware** (`src/kalshi_fees.py`): taker = 0.07·P·(1−P) always;
  maker = 0 for `quadratic` series and 0.0175·P·(1−P) for
  `quadratic_with_maker_fees`. The split is props-vs-outcomes, NOT per-sport:
  derivative series are maker-free (golf top-N, make-cut, H2H, 3-ball, round
  leaders; MLB hits, K's, totals, team totals, TB, HR, HRR, SB, RFI, F5,
  spreads), game-winner/league series charge (KXPGA, KXMLBGAME, KXMLB, KXMLBAL/NL,
  KXMLBASGAME, KXMLBHRDERBY). Pass `series_ticker` to `fee_per_contract`. No arg
  → general maker rate (backward-compatible for P-016).
  `_SERIES_MAKER_FEE` matches by LONGEST prefix — required because KXMLB (charges)
  ⊂ KXMLBHR (free) ⊂ KXMLBHRDERBY (charges) ⊂ KXMLBHRDERBYR1LEAD (free). Verify
  new entries against
  `GET /trade-api/v2/series/?category=Sports&limit=200` (`fee_type` field); this
  table has now drifted **four** times, and the 2026-07-26 run queue found three
  separate missing slices in one day. **The durable fix is a fixture generated
  from `/series` plus a CI check, not a fifth hand patch.**
- **Prefix nesting is the trap, and it is not intuitive.** `KXPGATOP` does NOT
  cover `KXPGAR1TOP5` — their shared prefix is only `KXPGA`, which charges — so
  every round-based top-N series was silently billed a maker fee on markets
  Kalshi bills at zero. Same shape for `KXLIVTOUR` vs `KXLIVTOP5` (disjoint, not
  nested). When adding an entry, check what its *actual* longest match resolves
  to; do not assume a similar-looking entry covers it.
- **Leader markets are maker-free — all of them.** Swept 2026-07-26 across all
  7,665 series in every category: 88 tickers contain `LEAD` and not one is
  `quadratic_with_maker_fees`. Round leaders on every tour
  (KXPGA/KXDPWORLDTOUR/KXLIV/KXLPGA/KXCHAMPTOUR `R{1,2,3}LEAD`) and the season
  stat-leader family (`KXLEADER*`, 39 series) are all `quadratic`. Add round
  leaders as FULL tickers, never a per-tour round prefix: `KXPGAR` would also
  swallow KXPGARYDER, the one golf series that genuinely charges.

## Aggregate risk — reservations (added 2026-07-20)

`AggregateRiskGuard` registers positions in `update_post_cycle`, so a pod
placing its whole book in ONE scan (P-017 golf: all candidates visible at
once, 4–10 days out) had every trade checked against the *previous* cycle's
exposure — the guard rejected nothing, and only the pod's own
`max_open_positions` prevented a breach ($1122 of a $1000 bankroll at cap 174).

- **Reserve, don't just check.** `reserve_trade(pod_id, venue, market_id, usd)`
  holds the exposure on approval so the rest of the scan sees it.
  `check_trade` stays read-only but now *counts* live reservations.
  `BasePod.check_aggregate_risk(venue, usd, market_id=...)` reserves when
  given a market_id; `MultiExecutor` always reserves.
- **Reservations cannot leak.** `update_post_cycle` converts them to
  positions using actual sizes and sweeps the rest (rejected, errored, or
  dropped post-approval); `check_pre_cycle` is a backstop for a cycle that
  died before its callback. Never hand-unwind one.
- Both are optional-by-`getattr` at every call site, so a test double
  implementing only `check_trade` still works. They are deliberately NOT on
  `AggregateRiskProtocol` (it is `runtime_checkable`).
- `_add_position` is idempotent by market_id and `close_position` credits
  the venue/pod buckets back — they used to only ever go up. Do not call
  `_add_position` from a pod (P-006 used to; it double-counted).
- **Bootstrap tracks paper positions for pods that have a settler**
  (`settled_pod_ids`, derived by `engine._settled_pod_ids`). The old blanket
  paper-skip predated the P-015/P-017 settlers and, since the whole engine
  runs `mode: paper`, meant the guard started every process at zero exposure.
  Pods with no settler are still skipped — they never drain.

## Conventions

- Paper-first, CLV-gated. New pods are validated in a `*_research/` folder
  (backtest → REPORT + params JSON) BEFORE writing the pod, then the pod bakes in
  the validated params. See `tennis_research/`, `golf_research/`, `mlb_props_research/`.
- Edge must clear NET of fee + half-spread (`kalshi_fees.net_edge`). Use CLV vs the
  de-vigged sharp close as the north-star metric, not raw P&L.
- Bootstrap CIs for backtests cluster by event/tournament (within-event outcomes
  correlate) — treat each event as one observation.
- **Commit research artifacts at each gate, not at the end of the run.** On
  2026-07-25 the `golf_quirks_research/` harness `.py` files were never committed
  and vanished; the reports survived only as session copies. Before ending a
  research session or handing one off, run:
  ```bash
  bash scripts/check_research_committed.sh
  ```
  It fails if any `*_research` `.py`, `REPORT*.md`, or `*_params.json` is
  untracked, modified, or **hidden by `.gitignore`** — the last check is the one
  that matters, since `data/`, `*.jsonl` and `*.log` are ignored repo-wide and the
  lost files never showed up in `git status` at all. When a research cache is worth
  keeping, commit it gzipped under `<pod>_research/archive/` (see
  `golf_quirks_research/archive/README.md`); Kalshi trade history rolls off after
  ~1 month and cannot be re-pulled.

## Recent work — P-017 Golf (July 2026)

Built, backtested, tested (19 golf tests pass), wired to `pods.active` in paper.
- Taker leg VALIDATED (+6.8¢/ct net, 9/10 tournaments); fade-maker promising but
  underpowered (paper-collect via `python3 scripts/run_golf_maker.py`).
- Optional DataGolf model feed (`src/datagolf_client.py`) — off until
  `DATAGOLF_API_KEY` in `.env` + `datagolf.enabled: true`. Falls back to a
  validated structural `edge_bump`.
- Full detail: `golf_research/GOLF_KALSHI_RESEARCH.md` (research),
  `golf_research/backtest/REPORT_Golf_TopN_2026-07.md` (validation),
  `golf_research/P-017_Golf_Pod_Spec.md` (what shipped + next steps).

Open next steps: deploy so the engine picks up P-017; start `run_golf_maker.py`;
wire DataGolf key when available; re-run `golf_research/backtest/backtest_golf.py`
after each event to extend the sample.

### Golf settlement (added 2026-07-20) — `src/kalshi_golf_settler.py`

P-017 shipped with NO settler, which made `on_settlement` dead code: the generic
Kalshi `Settler` filters to `pod_ids=("P-001","")` and only the tennis branch
calls `pod.on_settlement`. Symptom would have been silent — bets placed, nothing
resolved, `_open_count` pegged at the cap, pod mute after one tournament.
`KalshiGolfSettler` fixes it. Three golf-specific rules, each verified live:

- **`status="closed"` is NOT settled.** Top-N markets sit closed with an empty
  `result` for ~a day post-tournament (156/156 observed). The tennis settler's
  `result or "void"` idiom would VOID a whole tournament. Settle only on a
  populated `result`; the stale guard (10d after `close_utc`) backstops.
- **`result="scalar"` is a PARTIAL PAYOUT, never a void** (532/24,195 settled golf
  markets, 2.2%; 102/3,206 = 3.2% inside P-017's own KXPGATOP10/20 universe).
  Kalshi settles the market at `settlement_value_dollars`: YES receives it, NO
  receives `1 − it` (notional is $1.00). **Corrected 2026-07-26 — this section
  previously said scalar meant "competitor never teed off, market cancelled →
  VOID", and the settler booked $0 P&L on every one.** Two regimes produce
  scalar and both settle the same way:
  - **Dead-heat $1/n split** on round-leader series (`KX*LEAD`). Verified exactly
    on all 21 scalar LEAD events in the census — 2-way 0.50, 3-way 0.33, 4-way
    0.25, 5-way 0.20, 6-way 0.16. This is P-022's entire thesis; voiding it
    erases the event the pod exists to harvest.
  - **Withdrawal cancelled at FAIR VALUE** on top-N / make-cut. A golfer who
    never teed off is marked to the prevailing price, **not refunded at your
    fill**. Verified on COPC26: the same 8 names settle monotone TOP5 ≤ TOP10 ≤
    TOP20 ≤ MAKECUT (8/8), e.g. TMOO 0.11/0.16/0.36/0.51 — a probability
    surface, not $1/n and not a refund.

  Scalar now yields WIN or LOSS on the sign of realised P&L and **never** VOID;
  `settlement_kind` (`scalar_partial` / `void` / `binary`) and `settlement_value`
  keep the two apart in the log. A scalar with no `settlement_value_dollars` is
  left OPEN with a logged ERROR — never defaulted. Do not add a new `outcome`
  value: `WIN`/`LOSS`/`VOID` is hard-coded in `trade_store`, `engine`,
  `aggregate_risk`, and `capital_allocator`, and an unrecognised outcome would
  leak exposure.
- **P&L is booked NET of the taker fee**, since the go/no-go compares against a
  fee-net baseline (+6.8¢/ct). `pnl_gross_usd`/`fees_usd` recorded alongside.

Use `KalshiPublic.get_market(ticker)` for settlement state — LIST endpoints null
out fields on settled markets (but they DO carry `settlement_value_dollars`;
`settlement_value` in integer cents is usually null on these series).

Rows already booked by the buggy version carry
`resolution_source="kalshi_withdrawn"`. Re-derive them with
`scripts/backfill_golf_scalar_corrections.py`, which writes a corrected series to
a NEW file and never touches trade history.

## Canonical docs

`PROJECT_PLAN_Kalshi_Sports_v2.md` (current mission) · `PROJECT_STATUS.md` (state) ·
`golf_research/`, `tennis_research/`, `mlb_props_research/` (per-sport work).
