# AGENTS.md — Betting Pod Shop

Multi-pod sports betting engine (Kalshi-focused) in **paper/demo mode**. Runs on a
DigitalOcean VPS (129.212.176.202) as systemd service `betting-pod-shop` (NOT
`bettingbot` — that is the unix user the service runs as; `systemctl status
bettingbot` reports "unit could not be found"). The P-016 live maker is a second
unit, `betting-live-maker`. Also runnable locally. Python 3.12. This file orients a fresh Codex session — deeper detail
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
  `quadratic_with_maker_fees`. The split is props-vs-outcomes, NOT per-sport.
  Pass `series_ticker` to `fee_per_contract`; no arg → general maker rate
  (backward-compatible for P-016, which makes on KXMLBGAME and does charge).
  **The table is now a GENERATED FIXTURE, not a hand-maintained dict** —
  `src/fixtures/kalshi_series_fees.json`, built from one `GET /series` call by
  `scripts/generate_fee_fixture.py` (12,199 series, 130 charging). Matching is
  **EXACT on the series ticker**, so the longest-prefix trap below is dead by
  construction. Do NOT hand-patch it; regenerate and commit. Drift against live
  Kalshi: `python3 -m scripts.check_fee_fixture` (exit 1), also available as an
  opt-in test via `KALSHI_FEE_CHECK=1`. It alarms on a classification change or
  a new CHARGING series, and stays quiet for new maker-free series — Kalshi
  lists those constantly. `research/REPORT_Fee_Audit_2026-07-27.md`.
  Two things exact matching still needs care with: **148 series tickers contain
  a hyphen** (`KXMLBWINS-MIL`), so resolution tries the full string before the
  leading segment; and `_PENDING_SERIES` holds the only hand-written fee data
  left — series that do not exist yet — with a test that fails once Kalshi
  lists one, forcing deletion.
- **Prefix nesting WAS the trap — this is now history, keep it that way.**
  Under the old longest-prefix rule `KXPGATOP` did NOT cover `KXPGAR1TOP5`
  (shared prefix only `KXPGA`, which charges), `KXLIVTOUR` and `KXLIVTOP5` were
  disjoint rather than nested, and `KXMLB` ⊂ `KXMLBHR` ⊂ `KXMLBHRDERBY` ⊂
  `KXMLBHRDERBYR1LEAD` alternated charge/free/charge/free. Four of the five
  drifts came from this. Exact matching killed it — **do not reintroduce
  prefix logic anywhere in the fee path.**
- **Golf H2H ties pay 0.50/0.50 — NOT $0.** `GOLFH2H.pdf` (governs `KXPGAH2H`,
  `KXLIVH2H`, `KXDPWTH2H`, `KXGOLFH2H`) resolves an identical-stroke tie by
  paying **both** YES and NO $0.50, so `fair(A) + fair(B) = $1.00 exactly` and
  the tie mass — 9.4% of matchups, 51 of 541 settled events — is symmetric and
  fully paid. There is no structural edge. Verified verbatim in the PDF and on
  102/102 settled scalar legs, all `settlement_value_dollars = 0.5000`
  (P-028, 2026-07-28). A 2026-07 task brief quoted this same clause as "$0" and
  built a whole hypothesis on it: **read the PDF yourself even when the rule is
  quoted to you.** `golf_quirks_research/REPORT_P028_Template_Sweep_2026-07.md`.
- **The `KXLEADER*` $1/n split is CONDITIONAL, and every prior write-up stated it
  unconditionally.** `LEAGUELEADER.pdf` (governs the whole `KXLEADER*` stat-leader
  family) says verbatim: *"In the event of a tie where multiple participants have exactly the same <statistic> total, **and <league> does not declare a single winner through official tiebreaker procedures**, the markets for all tied <participant>s will resolve so "Yes" holders receive $1/[the number of tied <participant>s] rounded down to the nearest cent and "No" holders receive $1 minus the Yes payout."*
  **The split only fires when the league does NOT resolve the tie itself** — and
  most leagues publish official tiebreakers, so the clause may almost never
  apply. Any model that prices the full `E[1/n]` haircut unconditionally
  overstates it. Distinct from the golf `KX*R{1,2,3}LEAD` round-leader split
  (P-022), which has no such carve-out.
  **The split has never been observed in the wild.** Two `KXLEADER*`-family
  series have settled — `KXWCGOALLEADER` (54 markets) and `KXLEADERUCLGOALS`
  (6) — and **both resolved outright at $1.0000**, no scalar. The claim that
  "~Oct 15 is the first-ever KXLEADER settlement" is wrong.
- **`GOLDENGLOBESNOM.pdf` is a RANKLIST template, NOT an award template**, and
  the two carry OPPOSITE dead-heat regimes: RANKLIST ties pay **pro-rata $1/n**,
  award ties pay the tied winner **ZERO**. Two opposite regimes sit one PDF apart
  under similar-looking tickers, so a census that groups by ticker name will
  conflate them. Read the `contract_terms_url` per series, never the name.
- **Leader markets are maker-free — all of them.** Swept 2026-07-26 across all
  series in every category: 88 tickers contain `LEAD` and not one is
  `quadratic_with_maker_fees`. Round leaders on every tour and the season
  stat-leader family (`KXLEADER*`) are all `quadratic`. Asserted against the
  fixture by `tests/test_kalshi_fees.py`, so a regression fails the suite
  rather than being caught by a later reader.
- **The fee table drifted FIVE times and every one was found by accident**,
  downstream of a verdict already written. The audit found no committed
  verdict changed sign — but only because every maker study distrusted the
  code and hard-coded a fee it verified live against `/series` itself. P-023c's
  +0.2¢ executable KILL sat well inside the 0.44¢ phantom-fee band and was
  safe only for that reason. `research/REPORT_Fee_Audit_2026-07-27.md`.

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
- **SHADOW MODE while in research (`aggregate_risk.enforce: false`, 2026-08-02).**
  The guard now MEASURES the portfolio constraint instead of imposing it.
  Every limit is still evaluated and every breach recorded; nothing is
  blocked. Rationale: vetoing paper trades **biases** the research, it does
  not merely shrink it — a trade is dropped when the book is already full,
  which correlates with high opportunity density, so the skips delete
  observations from busy regimes and pods are then compared on the
  survivors. The portfolio-wide daily-loss halt was worse still: it
  **couples the pods**, so one pod's bad hour blanks every other pod's data
  for 60 minutes, which is fatal when the objective is per-pod attribution.
  Measured on the shipped config: 40 × $25 tickets in one scan → **12 placed
  enforcing vs 40 in shadow**. ~70% of the sample was being deleted.
- **A veto is destructive; a log is replayable.** From an unconstrained log
  you can reconstruct what any cap setting would have done; from a
  constrained log you can never recover the blocked trades. `shadow_log`
  (`data/trade_logs/aggregate_risk_shadow.jsonl`) carries pod, venue, size
  and full exposure state per decision — the raw material for that replay.
  **Caveat: the recorded verdicts are evaluated against the UNCONSTRAINED
  book** (nothing was blocked, so exposure runs past the caps). They say
  which trades touched a limit, NOT what the constrained portfolio would
  have held. That requires replaying the log offline.
- **Shadow mode is PAPER-ONLY and fails closed.** `from_config(config,
  mode=...)` forces enforcement back on unless the caller states
  `mode="paper"`, and logs an ERROR when it does. `cli.py` resolves
  `args.mode or "paper"`. The live-capable paths are unaffected: P-016 and
  P-029 are separate units, and the P-022 standalone maker calls
  `from_config(config)` with no mode, so it keeps enforcing. A repo-wide
  research flag that silently disarmed a live guard is the failure this
  project already had once with the loss guard — do not add one.
- **`enforce: false` does NOT make the caps dead config** — they decide what
  gets recorded. Keep them meaningful. **Flip back to `true` before funding.**
- **The binding cap is the VENUE cap, not the total cap.** With P-002/P-006
  shelved every pod trades Kalshi, so `max_venue_exposure_pct: 0.30` ($300
  of the $1k paper bankroll) binds before `max_total_exposure_pct: 0.50`.
- **`exempt_pods` bypasses ONLY the total-exposure check** — venue, per-pod,
  position-count and halt checks still apply. It is not an off switch.
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
- **A clustered CI does not mean the POINT ESTIMATE is clustered too, and in the
  golf harness it is not.** `quirks_common.bootstrap_weighted` resamples
  tournaments correctly for the interval, but returns `sum(p*w)/total_w` as its
  estimate — a **pooled** contract-weighted mean in which a large-field
  tournament dominates. `P022_DECISION_RULE.md` §3 defines the gate statistic as
  `edge = mean(x_t)`, **equal weight per tournament**. Anyone reading
  `net_per_contract` off that harness is reading the pooled number, not the
  gate's. Measured 2026-08-02: on the published 364-market sample the two agree
  (+3.41¢ vs +3.80¢, both z>3, so the Phase-2 GREEN-LIGHT stands on either), but
  on the widened 404-market sample they split — +2.57¢ pooled vs **+1.45¢
  equal-weighted, z=0.65, not significant**. The divergence grows with
  field-size imbalance, so it is invisible exactly until it matters. Reproduce
  with `python3 golf_quirks_research/repro_estimator_check.py`. The forward
  reader `scripts/p022_checkpoint.py` is fine — it uses `statistics.mean(xs)`.
- **A backtest that ignores the pod's own caps is measuring a strategy you are
  not allowed to run.** The same harness replays at `quote_size=25`
  contracts/name; at the measured mean filled quote of $0.0763 that is
  25 × $0.9237 = **$23.09 of collateral against a $5.00 per-name cap — 4.62×**.
  The cap permits ~5.4 contracts. Any capacity or P&L figure taken from that run
  is quoted at ~4.6× authorised size. Caps are GATE CONDITIONS in the P-022
  rule, not tuning knobs, so this is not a rounding detail.
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

## Authenticated Kalshi access — `src/kalshi_private.py` (added 2026-07-29)

Until this landed there was **no authenticated Kalshi path in the repo at all** — which is why P-022
could be "ARMED" with 13 quotes and no way to place them. Use this module; do not write another.

- **Signing:** `timestamp_ms + METHOD + path`, path includes `/trade-api/v2` and **excludes the
  query string**; RSA-PSS/MGF1-SHA256, salt = digest length, base64, timestamp in **ms**. A signed
  path carrying `?params` is the classic silent 401; a test asserts it must not verify.
- **Closed by default:** `allow_orders=False`. Order/RFQ/quote methods raise `OrdersDisabled`
  otherwise. `dry_run=True` logs the body and never hits the network.
- **`LossGuard`** reads the **exchange**, never an in-process counter (a counter resets on restart —
  the P-014 failure mode), and **fails closed**. Pass **both** `subaccount=` and `since=`.
- **Credentials:** `KALSHI_PRIVATE_KEY_PATH`, never the inline `KALSHI_PRIVATE_KEY`.
  **Never `cat`/`sed`/`grep` `.env` or any file that may hold a PEM** — a multi-line PEM survives
  naive redaction, and one leaked this way on 2026-07-28. Ask Sam to confirm values instead.
  **After creating any file next to secrets, run `git check-ignore` before committing** — a backup
  file reached the public repo on 2026-07-29 because `.gitignore` had `.env`, which matches only that
  exact name (now `.env*`).

**Kalshi permits ONE ACCOUNT PER PERSON.** Never create a second. Isolate an algo from Sam's manual
GUI trading with a **subaccount** (0–63) — they are API-only, so the web and mobile apps cannot reach
them. P-029 uses subaccount 1.

## More Kalshi API gotchas (added 2026-07-29, from the combo work)

- **`/portfolio/settlements` returns NO P&L field.** Derive it:
  `revenue/100 − yes_total_cost_dollars − no_total_cost_dollars − fee_cost`. **`revenue` and `value`
  are integer CENTS; the cost fields are DOLLAR strings.** `value` is per-contract (max 100 = $1.00)
  — never sum it. Reading the non-existent `realized_pnl_dollars` silently blinded the loss guard on
  a live account. Diagnose with `scripts/inspect_settlements.py`.
- **`status=open` is the FILTER; rows read `active`.** The string `"open"` never appears as a value.
- **`/markets?ticker=X` (singular) is silently ignored** — returns the unfiltered list, HTTP 200.
  Use `?tickers=` (plural CSV), and **batch by character budget** (414s at ~9,600 URL chars).
- **Read the tick from `price_ranges`, not `tick_size`** (which is `None` even on sub-cent markets).
  Combos quote on a **0.1¢ `deci_cent`** grid.
- **`result="scalar"` is a partial payout at `settlement_value_dollars`, never VOID.**
- **Never append to one gzip file from a long-running job.** A kill mid-append truncates the stream
  and makes the whole file unreadable — systemd restarts do this routinely. Write immutable parts via
  temp-file + `os.replace`. See `combo_research/archive_settled_combos.py`.

**Cowork cannot SSH** — port 22 is blocked outbound from the sandbox. Package VPS work as
self-contained scripts for Sam to run (the DigitalOcean web console works when no key is installed).

## EV-Map settled archive (resumable since 2026-08-16)

`kalshi-ev-map/src/archive_settled.py` no longer rewrites one growing lifetime
Parquet file. The valid 8.68M-row `settled_archive.parquet` is an immutable base;
new rows are atomically published under `settled_archive_parts/`. Exact ticker
deduplication and API-cursor state live in `settled_archive_index.sqlite`.
Legacy-base indexing checkpoints by Parquet row group, and ingestion checkpoints
by page bundle. `archive_progress.json` is the monitored progress surface.

- Never merge the parts back into the base or delete the SQLite index to “start
  clean”; either action restores the unbounded weekly rewrite that timed out.
- A `*.parquet.tmp` or the old `settled_archive.parquet.raw` is not committed
  archive data. Final `*.parquet` parts are committed and corruption fails closed.
- The script budgets 35 minutes inside a 45-minute systemd limit and runs daily.
  A clean checkpointed exit is expected; the next run resumes.
- The wrapper streams child progress to journald. Do not restore
  `capture_output=True`, which made the 2026-08-16 timeout stage invisible.
- Startup reconciliation verifies size/mtime for completed sources without
  reopening every Parquet footer or fsyncing progress once per part. Only new
  or incomplete sources enter row-group indexing; preserve that fast path.
- `metadata.ticker_count` is updated in the same SQLite transaction as ticker
  inserts. Progress and the wrapper use this cached exact total; do not restore
  a routine `COUNT(*)` over the 11.95M-row ticker index or count only the base.

## Recursive strategy factory — the OTHER agent system (live 2026-08-01)

Separate from the pods. Six subagents in `.Codex/agents/` move an idea through
`idea → spec → validated → paper_live → live_small → live_scaled` (+ `degraded`,
`retired`): `strategy-scout` → `-spec` → `-integrity` → `-validator` →
`-promotion` → `-monitor`. Each returns ONE typed JSON artifact defined in
`src/strategy_orchestration.py`; none can do the next one's job. `fund-manager`
is unrelated to this chain — it reports on the fund from `manager/`.

- **The Python enforces it, not the prompts.** `ALLOWED_TRANSITIONS` rejects
  illegal moves; `registry.transition()` permits ONLY `degraded`/`retired`
  directly; promotion into `live_small`/`live_scaled` raises without a human
  `approval_ref`; validation raises unless integrity passed first. Every
  mutation appends a `StrategyEvent` and persists via flock + revision check +
  `os.replace` (stale writer → `StrategyConflictError`).
- **Runs as systemd `betting-strategy-agents`** on the droplet (queue worker,
  60s poll, no network). Submit with `python3 -m scripts.strategy_agent_submit
  --type <t> --payload-file <f>`; state lives in `data/strategy_agents/`
  (registry.json, heartbeat.jsonl, queue/<role>/, processed/).
- **Authority comes from WHICH INBOX the file lands in** — `actor = role`, never
  from caller-controlled JSON, and a `type` that doesn't match its inbox is
  rejected. Do not add a field that lets a payload name its own actor.
- **`betting-strategy-agents` remains a RECORDER, not an invoker.** It now emits
  an idempotent next-role task after each accepted artifact. The separate,
  bounded `betting-strategy-chain` oneshot may invoke one task every 15 minutes
  and can only queue typed JSON back to the recorder. It rejects unattended
  promotion into `live_small`/`live_scaled`; human approval remains mandatory.
  Check `registry_size`, `task_depth`, and `oldest_task_age_minutes` in the
  heartbeat — a running recorder alone still does not prove advancement.
- The registry is **empty** as of 2026-08-01. `OpportunityCard` is proven
  end-to-end; `IntegrityReport`/`ValidationReport` (mandatory mappings, dataset
  + gate hashes, provenance) have never been filled by an agent — that is the
  open question, and it decides whether automation glue is worth writing.

### Deploying a NEW systemd unit (learned the hard way, 2026-08-01)

- **Deploys are serialized before rsync.** Two 2026-08-16 deploys interleaved
  sync and one deleted the other's shared backup while `cp -a` was running.
  `deploy.sh` now holds `/run/lock/betting-pod-shop-deploy` through health or
  rollback and uses a tokenized backup path. Never move lock acquisition after
  rsync or restore a shared `.deploy-backup` directory.
- **`deploy.sh` rsyncs `scripts/systemd/` but installs nothing** and never runs
  `daemon-reload`, so the repo unit and the running unit drift silently. The
  deploy now PRINTS the drift after each sync; installing is still manual and
  deliberate.
- **`install -d -o X -g X` only chowns the LEAF directory** it creates, not the
  parents it makes along the way. The worker came up `active`, could not create
  its lockfile in a root-owned parent, and crash-looped 10 times while
  `systemctl is-active` still said `active` (Restart=always keeps flipping it
  back). Follow any `install -d` with an explicit `chown -R`.
- **A unit file with no `[Install]` section cannot be `systemctl enable`d** and
  will not survive a reboot. `ProtectSystem=strict` + `ReadWritePaths=` also
  fails the unit (226/NAMESPACE) if the path does not exist — and `deploy.sh`
  excludes `data/`, so it never will. Create the directories first.
- **Adding the service to `manager/registry.yaml` ARMS a check.** Ship the
  registry entry and the unit install together, or the collector sees an
  uninstalled unit. `collect._systemd` now reads `LoadState` so `not-found`
  warns ("in registry, not installed") instead of paging CRITICAL like a dead
  service — but the window still leaves it unmonitored.

## Canonical docs

`PROJECT_PLAN_Kalshi_Sports_v2.md` (current mission) · `PROJECT_STATUS.md` (state) ·
`golf_research/`, `tennis_research/`, `mlb_props_research/` (per-sport work) ·
**`combo_research/HANDOFF_P-029.md` (combo market-making — start here for P-029)**.

## Imported Claude Cowork project instructions

I'd like to continue building out this project and adding various levels to it.
