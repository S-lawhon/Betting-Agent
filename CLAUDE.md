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
- **Orderbook**: `/markets/{t}/orderbook` returns `orderbook_fp` in DOLLARS with
  sub-penny ticks; best YES ask = 1 − best NO bid (see `KalshiPublic.orderbook`).
- **Settled-market LIST endpoints null out** volume/price/last — use candlesticks
  for historical prices. Freshly-settled events leave `result` unpopulated ~1 day.
- **Fees are series-aware** (`src/kalshi_fees.py`): taker = 0.07·P·(1−P) always;
  maker = 0 for `quadratic` series (all golf props: top-N, make-cut, H2H, 3-ball,
  round leaders) and 0.0175·P·(1−P) for `quadratic_with_maker_fees` (winner
  series). Pass `series_ticker` to `fee_per_contract`. No arg → general maker rate
  (backward-compatible for P-016).

## Conventions

- Paper-first, CLV-gated. New pods are validated in a `*_research/` folder
  (backtest → REPORT + params JSON) BEFORE writing the pod, then the pod bakes in
  the validated params. See `tennis_research/`, `golf_research/`, `mlb_props_research/`.
- Edge must clear NET of fee + half-spread (`kalshi_fees.net_edge`). Use CLV vs the
  de-vigged sharp close as the north-star metric, not raw P&L.
- Bootstrap CIs for backtests cluster by event/tournament (within-event outcomes
  correlate) — treat each event as one observation.

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
- **`result="scalar"` is a real third value** (9/200 settled) — competitor never
  teed off, market cancelled not resolved. VOID, labelled `kalshi_withdrawn`.
- **P&L is booked NET of the taker fee**, since the go/no-go compares against a
  fee-net baseline (+6.8¢/ct). `pnl_gross_usd`/`fees_usd` recorded alongside.

Use `KalshiPublic.get_market(ticker)` for settlement state — LIST endpoints null
out fields on settled markets.

## Canonical docs

`PROJECT_PLAN_Kalshi_Sports_v2.md` (current mission) · `PROJECT_STATUS.md` (state) ·
`golf_research/`, `tennis_research/`, `mlb_props_research/` (per-sport work).
