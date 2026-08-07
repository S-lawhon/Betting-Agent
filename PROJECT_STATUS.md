# Betting Pod Shop — Project Status & Continuity Reference

**Last Updated:** July 22, 2026
**VPS:** 129.212.176.202 (DigitalOcean)
**VPS Path:** `/opt/betting-pod-shop/`
**Dashboard:** https://dashboard.htxtrades.org/ (TLS + basic auth via Caddy; public :8080 closed)
**Deploy:** `cd ~/Desktop/"Betting Fund Project" && bash scripts/deploy.sh 129.212.176.202 restart`

> **What this document is.** The two sections that follow (System Overview,
> Active Pods) plus Deployment Rules are the **current-state reference** — kept
> accurate. Everything under **Recent Changes** is a **reverse-chronological
> changelog / archive**; older entries reflect the state at their date and are
> not maintained. The single authoritative source of live state is
> `manager/registry.yaml` + the daily brief (`python3 manager/brief.py`); this
> file is orientation, the registry is truth.

---

## System Overview

Multi-pod sports betting engine on a DigitalOcean VPS, **paper/demo mode
throughout** — no real money has ever been deployed. Since the July 2026 pivot
the focus is **Kalshi-only, per-sport, CLV-gated** models (see
`PROJECT_PLAN_Kalshi_Sports_v2.md`). Polymarket-executing pods are shelved
because the project has no Polymarket execution access.

### Architecture

```
5-minute engine  (betting-pod-shop.service; src/main.py → src/engine.py)
  ├── AggregateRiskGuard — portfolio exposure/drawdown limits; intra-cycle
  │                        reservations (caps bind within a scan, not just across)
  ├── CapitalAllocator   — pod-level bankroll allocation + performance tracking
  ├── PodRunner          — scans active pods concurrently (execution.max_scan_workers: 2)
  │     ├── P-001  Kalshi Moneyline Value  → Legacy Scanner → Kalshi
  │     ├── P-014  Live Game Agent (in-play consensus)
  │     ├── P-015  Tennis Qualifier Favorite (paper validation)
  │     └── P-017  Golf Top-N pre-tournament value (paper validation)
  ├── Settlers (run at settlement.interval_cycles: 6, i.e. every 30 min):
  │     Kalshi (P-001), Polymarket, KalshiTennisSettler (P-015), KalshiGolfSettler (P-017)
  ├── TradeStore         — central in-memory indexed JSONL store
  └── WebDashboardServer — dashboard

Standalone units (own fast loops, NOT in the 5-min engine):
  ├── betting-live-maker      — P-016 Live Maker. RETIRED 2026-07-21 (failed
  │                             gate). DECOMMISSIONED 2026-07-22: KILL_MAKER hit
  │                             01:22:40 UTC, drained, unit stop+disabled ~06:05
  │                             UTC; removed from manager/registry.yaml monitoring.
  ├── betting-book-capture    — 1-min Kalshi order-book capture (src/book_capture.py)
  └── mlb-props-collector     — MLB order-book snapshots (timer; writes /opt/mlb-props/)

Disabled/retired: P-002 (shelved), P-004, P-006 (shelved), P-009, P-010,
P-012, P-013, P-016 (retired). P-017M golf fade maker shelved.
```

### VPS Details

- **Path:** `/opt/betting-pod-shop/` (service runs here; `data/` is the
  authoritative live trade history — never overwrite it, see Deployment Rules)
- **Services (3):** `betting-pod-shop` (5-min engine), `betting-book-capture`,
  `mlb-props-collector`. (`betting-live-maker` decommissioned 2026-07-22 —
  stopped + disabled, dropped from registry monitoring.)
- **User:** `bettingbot` (system user, no login shell) — rsync-as-root must
  `chown -R bettingbot:bettingbot` after; `deploy.sh` does this
- **Python:** `/opt/betting-pod-shop/venv/bin/python` (3.12)
- **Legacy sys.path:** injected in `src/engine.py` + PYTHONPATH in the service
  file (the Scanner/Settler/KalshiClient live under `Legacy/Kalshi Arb Project/src/`)
- **Logs:** `journalctl -u <service> -f`
- **Config:** `config.yaml` (base) + `config_multi_pod.yaml` (multi-pod overlay,
  merged over base). Key blocks added this era: `kalshi.rate_limit` (2 req/s —
  shared client, per-IP throttle), `execution.max_scan_workers`,
  `settlement.interval_cycles`, `book_capture`.
- **Kalshi API:** **production** `https://api.elections.kalshi.com/trade-api/v2`
  for full market inventory; `KALSHI_ENVIRONMENT=demo` + no
  `I_UNDERSTAND_LIVE_TRADING` keeps order placement gated. **Demo credentials
  only** — this is why the book-capture daemon uses REST, not the authenticated
  websocket. Kalshi throttles **per-IP**, so all services share one budget.
- **Timezone:** the droplet is UTC; Kalshi tickers encode ET. Use
  `src/et_time.py` for any ET conversion — do not hardcode UTC offsets (they
  break at DST). Known deferred bug: `aggregate_risk` daily-P&L resets at UTC
  midnight (mid-slate ET); scheduled fix `fix-aggregate-risk-pnl-reset-tz`.

### Key Environment Variables

- `ODDS_API_KEY` — The Odds API (scanning + P-001/settlement)
- `KALSHI_API_KEY_ID` / `KALSHI_PRIVATE_KEY` — Kalshi (RSA PEM); **demo** keys
- `KALSHI_ENVIRONMENT=demo` — paper-mode gate
- `POLYMARKET_API_KEY` — data only (no execution access)
- `FRED_API_KEY` — FRED (legacy P-012, disabled)

---

## Active Pods

Roster as of 2026-07-22. Full per-workstream state, gates, and history live in
`manager/registry.yaml` (authoritative) — this is the orientation summary.

| Pod | Status | Tier | Gate |
|---|---|---|---|
| **P-001** Kalshi Moneyline Value | active | validating | 200 CLV rows (~81) |
| **P-014** Live Game Agent | active | validating | 500 settled trades |
| **P-015** Tennis Qualifier Favorite | active | validating | 120 trades (0; US Open quals Aug) |
| **P-017** Golf Top-N (taker) | active | validating | 8 tournaments (1) |
| P-002 Cross-Venue Arb | ⛔ shelved | none | no Poly execution access |
| P-006 Poly Consensus | ⛔ shelved | none | no Poly execution access |
| P-016 Live Maker | ☠ retired | none | failed gate (−1.29¢ markout) |
| P-017M Golf Fade Maker | shelved | none | +9.1¢ was a weighting-bug artifact |

**No pod is `tier: production` — nothing has cleared a gate.** All trading is
paper. Kill switch for the maker unit: `touch data/KILL_MAKER`.

### P-001 — Kalshi Moneyline Value

- `src/pods/kalshi_moneyline.py` (wrapper) → `Legacy/Kalshi Arb Project/src/scanner.py`.
  Mispriced Kalshi moneylines vs Odds API multi-book (Pinnacle-weighted) consensus.
- Settlement: `Legacy/.../settler.py` via **Odds API `/scores`** (Kalshi demo
  never settles sports). Gate: forward CLV must follow the +1.4pp net-maker CLV
  measured in backtest (`clv_log.jsonl`). Do not scale until 200.
- **Gate progress is NOT 81/200 and NOT 650/200 — treat it as blocked (2026-07-26
  capture diagnostic).** `clv_log.jsonl` holds 650 rows, but 84% of them were
  placed on a Kalshi market for a *different day's game* than the Odds API event
  that produced the edge (matcher tie-break bug, fix written but not deployed).
  Only **105 rows are admissible** (ticker start within 3h of the priced game).
  The blended +1.39¢/ct would read as a PASS on a population that does not test
  the hypothesis. Re-scope proposed, awaiting Sam's decision.

### P-014 — Live Game Agent

- In-play consensus-edge agent. Verdict was INCONCLUSIVE-but-well-calibrated;
  gate is 500 settled trades to resolve significance. Hold, do not scale.

### P-015 — Tennis Qualifier Favorite (paper validation)

- `src/pods/qualifier_favorite_pod.py`, `src/kalshi_tennis_client.py` (read-only),
  `src/kalshi_tennis_settler.py`. Buy heavy favorites (ask 0.85–0.975) in ATP/WTA
  **qualifying** matches. Basis: `tennis_research/REPORT.md` — +4.1¢/ct net,
  n=238, CI [+1.4,+6.3]. Sized `fair = ask + 0.025`, depth×0.5, max 6 concurrent.
- Gate: 120 trades (currently 26 as of Aug 7; first major volume spike is US Open quals Aug 17–21,
  checkpoint ~Jan 2027). **P-015b** (extending to Challenger/ITF) was tested and
  **dropped** — edge did not replicate (Challenger −1.98¢, ITF −2.33¢).

### P-017 — Golf Top-N pre-tournament value (paper validation)

- `src/pods/golf_topn_pod.py`, settler `src/kalshi_golf_settler.py`. Taker on
  top-10/top-20 props. Backtest +6.92¢/ct net, 11/12 tournaments positive
  (`golf_research/`). Gate: 8 live tournaments (currently 1, the 3M Open).
- Cap derived from the TradeStore (not a process counter); `max_open_positions:
  30` sized to the 25% per-pod exposure policy. **P-017M** (fade maker) shelved —
  its +9.1¢ was a contract-weighting bug; corrected ~+3.3¢, below baseline.

### Shelved / retired (kept in code, not running)

- **P-016 Live Maker** — RETIRED 2026-07-21, failed its markout gate. Adverse
  selection ate the spread; a v2 was tested and rejected (loss is diffuse). See
  `research/POSTMORTEM_P016_2026-07-21.md`.
- **P-002 / P-006** — shelved 2026-07-22, no Polymarket execution access. P-006's
  edge was strong but unexecutable; do not read "shelved" as "refuted."
- **P-004, P-009, P-010, P-012, P-013** — legacy/disabled since the July pivot.

---

## Optimization Plan Status

The Phase I–IV optimization plan (`OPTIMIZATION_PLAN.md`) completed pre-pivot.
Test suite is now **~1376 passing** (the "35 pre-existing failures" noted in old
entries below was itself stale — the suite has been green). Historical detail:

| Phase | Focus | Status |
|-------|-------|--------|
| Phase I | TradeStore, concurrent scanning, atomic writes | ✅ Complete |
| Phase II | Exception audit, HTTP pooling, constants, VPS hardening, watchdog | ✅ Complete |
| Phase III | Split main.py, log schema, BasePod helpers, deploy hardening, integration tests | ✅ Complete |
| Phase IV | Pod auto-discovery, template extraction, type hints, P&L calculator, compat layer | ✅ Complete |

---

## CRITICAL: Deployment Rules

### Use `scripts/deploy.sh` — it protects live data by construction

```bash
bash scripts/deploy.sh 129.212.176.202          # sync code only, no restart
bash scripts/deploy.sh 129.212.176.202 restart  # sync + restart betting-pod-shop + health check
```

`deploy.sh` rsyncs the repo **directly** to `/opt/betting-pod-shop/` with
`--exclude 'data/'` and `--exclude '*.jsonl'`, then `chown -R
bettingbot:bettingbot`, then (with `restart`) restarts **only**
`betting-pod-shop` and runs a health check with auto-rollback on failure. There
is no longer a two-directory staging step — the old `/root/Betting-Fund-Project`
path still exists on the box but is unused.

### ⚠️ Why `data/` must never be synced

`/opt/betting-pod-shop/data/` holds the **authoritative live trade history**;
the Mac copy is a stale snapshot. Overwriting it destroys records — this
happened **March 21, 2026** (lost March 7–19 trades) when an rsync omitted
`--exclude 'data/'`. `deploy.sh` bakes the exclusion in; never hand-roll an
rsync without it.

### What `deploy.sh restart` does NOT touch — and when that matters

It restarts only `betting-pod-shop`. `betting-live-maker`,
`betting-book-capture`, and `mlb-props-collector` keep running. This is
deliberate: **P-016's gate sample (and any mid-validation pod's) must not be
interrupted by a code deploy.** When a change must reach a standalone unit
(book-capture, the maker, the collector), scp the file and
`systemctl restart <unit>` that unit specifically. The MLB props collector
writes to `/opt/mlb-props/` (outside the rsync target) — its data is safe but
its **code drifts silently** and needs a manual scp.

### Sync-only vs restart

Use sync-only (no `restart` arg) for registry/doc/config changes the running
process re-reads on its next cycle, or when you explicitly do not want to bounce
the engine. Use `restart` when a `.py` change must take effect now. `deploy.sh`
clears nothing by hand — systemd's restart reloads the code; there is no stale
`__pycache__` step needed with the current flow.

---

## Recent Changes (Session — July 21–22, 2026)

> **Source-of-truth note:** the living project state is `manager/registry.yaml`
> (per-workstream) and the daily brief (`python3 manager/brief.py`). This
> narrative is a continuity summary; where they disagree, the registry wins.
> 31 commits this session; `git log 5120383..HEAD` for the full list.

### P-016 decommissioned — killed, drained, unit disabled (2026-07-22)

P-016 was retired 2026-07-21 but its `betting-live-maker` unit was left running
idle. On 2026-07-22 the kill switch (`data/KILL_MAKER`) was hit at 01:22:40 UTC;
the maker pulled all quotes within one cycle (last fill 01:23:00) and drained
its open books via normal settlement. This surfaced a **monitor false alarm**:
the heartbeat check (`maker_quotes.jsonl`, 45-min stale limit, `only_during:
mlb_games_window`) cannot tell a *wedged* loop from a *deliberately killed* one,
so it paged CRITICAL ("active but silent for N min") every cycle for an
intentionally-silent unit. Fix: removed `betting-live-maker` from
`manager/registry.yaml` `services:` (commented, with rationale) — a retired unit
should fire neither the stale check nor `service.down` once inactive. Registry
synced to droplet (no restart). A `maker-decom` systemd timer stops + disables
the unit at ~06:05 UTC after the last games settle.

### Pod roster changed materially

| Pod | Before | Now | Why |
|---|---|---|---|
| P-016 Live Maker | validating (500-fill gate) | **RETIRED** | Failed its pre-registered gate — see below |
| P-002 Cross-Venue Arb | paper | **SHELVED** | No Polymarket execution access |
| P-006 Poly Consensus | paper | **SHELVED** | No Polymarket execution access (executes on Polymarket only) |
| P-015b lower-tour tennis | proposed | **DROPPED** | Edge did not replicate on Challenger/ITF |
| P-017M golf fade maker | promising | **SHELVED** | +9.1¢ was a weighting-bug artifact; true ~+3.3¢, below baseline |

Engine now runs **4 active pods**: P-001, P-014, P-015, P-017 (down from 6).

### P-016 Live Maker — RETIRED (failed gate), and its successor rejected too

- Hit its pre-registered gate: **814 fills, +5m markout −1.29¢/contract
  (−$107.59 net of fee)**, negative at every horizon. Gate required *positive*
  markout → **KILL**. Retired via `data/KILL_MAKER` on the droplet (unit
  `betting-live-maker` stays up but idle; fully reversible).
- The result in two numbers: **spread capture +4.08¢, markout −1.29¢** — earned
  the spread, lost more than all of it to adverse selection.
- A **v2** (suppress/widen quotes after game-state changes) was spec'd, then
  **tested and REJECTED the same day**. The offline grid found no arm turns
  markout non-negative, and the founding premise (loss concentrated in a ±15s
  window) **did not reproduce**: the loss is diffuse (62% of fills >60s from any
  state change, still −2.2¢). Conclusion: market-anchored MLB in-play making
  does not appear to have an edge here. No successor queued.
- Docs: `research/POSTMORTEM_P016_2026-07-21.md`,
  `research/SPEC_P016_v2_2026-07-21.md` (marked NO-GO),
  `research/P016_v2_offline_grid_2026-07-21.md`.
- **Process lesson banked:** a founding number (−8.78¢/−0.96¢) was relayed from
  an agent report into a committed doc *before* independent verification, and it
  didn't hold up. Reproduce a founding number before it justifies building on it.

### The 429 storm — fixed, after two misattributions

`betting-pod-shop` was taking ~829 HTTP 429s/24h (peaking ~137/hr), escalating
with the MLB slate. Root-caused in three steps; **only the third was the fix**:

1. **Scan concurrency** (`execution.max_scan_workers: 2`) — the engine opened one
   Kalshi connection per active pod. Real but ~12% of the problem (137→120/hr).
2. **Settlement cadence** (`settlement.interval_cycles: 6`, every 30 min) — all
   four settlers polled every 5-min cycle for markets that settle daily. ~3%.
3. **The actual fix: `kalshi.rate_limit.requests_per_second: 5 → 2`.** The shared
   legacy `KalshiClient` (P-001 scanner, settlers) defaulted to 5 req/s because
   `config_multi_pod.yaml` had no `kalshi:` block. 98 of 98 429s came from this
   one client; **137/hr → 0**, and cycles got *faster* (retries were costlier
   than the throttle).

Key fact: **Kalshi throttles per-IP**, so process separation does not separate
the budget. The correlation evidence that misled steps 1–2 was confounded — the
settler runs immediately before the scan, so "429s cluster on settler minutes"
was indistinguishable from "on scan minutes." Attribution (grep the emitter)
beat correlation. See memory `kalshi-api-rate-saturation`.

### New data-collection infrastructure (two daemons)

- **Book capture** (`src/book_capture.py`, `scripts/run_book_capture.py`, unit
  `betting-book-capture`): 1-minute Kalshi order-book snapshots, append-only
  JSONL gzipped per UTC day. **REST not websocket** — the websocket needs
  production credentials and this project holds demo-only keys. Rate **3.0 req/s**
  (cap 54 markets, sized for steady coverage under shared-budget contention; was
  briefly 4.0). Self-throttles on 429, logs every truncation as a `DISCOVERY`
  record (no silent caps). Unblocks the sub-5-min book data three tracks needed.
- **MLB props collector** was a manual script with no schedule; now a systemd
  timer (`mlb-props-collector`, ET-anchored, `--rate 2.0`, writes to
  `/opt/mlb-props/` outside the deploy rsync). Throttled + instrumented so its
  429s are visible. **3 of ~27 game-days** collected toward the execution test.

### Timezone audit — Kalshi encodes ET, hosts disagree by 5h

Triggered by an MLB-props collector bug: naive `datetime.now()` on the UTC
droplet truncated collection at 19:45 ET, silently dropping ~45% of first-pitch
windows. Full audit found and fixed:

- **`kalshi_live_discovery.py`** — a LIVE bug (P-014): built the Kalshi ticker
  date tag from UTC, wrong for the evening slate 20:00–24:00 ET nightly.
- **`clv_settlement.py`** and **`manager/checks.py`** — hardcoded UTC-4 (EDT),
  latent until DST ends 2026-11-01.
- New **`src/et_time.py`** centralises ET conversion (tzdata-based, documented
  fallback). 12 tests + both DST boundaries.
- **NOT fixed (deferred, records the tradeoff):** `aggregate_risk.py` resets
  daily P&L at UTC midnight = 8pm ET, mid-slate — a genuine risk-control
  weakening. Deferred because it's shared infra for 4 pods mid-validation;
  **scheduled task `fix-aggregate-risk-pnl-reset-tz` set for ~2026-09-15**, gated
  on P-017 clearing first. Strictly-more-conservative one-line fix.

### AggregateRiskGuard — intra-cycle caps now bind

`close_position` credited nothing back to venue/pod buckets (exposure ratcheted
up forever, `CODE_REVIEW_2026-03-31.md:20`), and positions were only registered
post-cycle — so a pod placing its whole book in one scan was checked against
*last* cycle's (zero) exposure. This is why P-017 briefly ran ~32% of bankroll
against a 25% cap. Added intra-cycle **reservations**; caps now bind within a
scan. 280 new tests.

### Golf P-017 — backtest extended, band drift found

- Folded in THOC26/COPC26 (now settled): **Leg A holds at +6.92¢/ct, 11/12
  tournaments positive** (was +6.81¢/10). Confirmation, not new signal; does NOT
  satisfy the 8-live-tournament gate (that's about live paper fills).
- **Leg B (fade maker) +9.1¢ was a contract-weighting bug** — one entry per fill
  event regardless of size. Corrected: **+3.3¢, CI straddles zero even at 4
  events**, below the +4.55¢ half-baseline → P-017M shelved.
- **Band drift:** shipped pod runs 8–45¢ (`ask_cap 0.45`) but the validating
  backtest ran 8–40¢. The pod was trading an unvalidated band. It tests slightly
  *better* (+7.14¢), so no change needed — but that's luck, not process.

### Fees, decisions, and manager tooling

- **MLB prop maker-fee fix:** `series_maker_charges_fee()` over-charged 0.438¢/ct
  on nine MLB prop series (its zero-fee list was golf-only). P-016 was
  unaffected (no `series_ticker` → correct fallback). Longest-prefix table now
  resolves `KXMLB`/`KXMLBHR`/`KXMLBHRDERBY` correctly.
- **P-016 trigger restated:** the markout-vs-settlement open question resolved
  (keep +5m markout); the trigger was mis-specified (counted 107 fills that were
  really 5 games) → restated to ">=40 distinct settled games."
- **Manager tooling:** fixed `collect.py` reporting `root: local` jobs as "does
  not exist" (they run on the Mac); added a **registry-reconciliation check**
  (catches pods trading-but-unregistered, or recorded-as-trading-but-not) and an
  explicit **`tier:`** field per pod (validating/production/none — every pod is
  currently `validating`, none `production`). Added a settler-construction
  tripwire test (pods.active drives settler build via `engine.py:204/221`).
- **Polymarket MLB blocker corrected:** the long-standing "pending regular-season
  markets" note is stale — they're live. Real blockers found: derivative events
  (First-5-Innings/Player-Props) priced as the moneyline, and identically-titled
  events on different dates matched title-only (32/58 MLB matches paired the
  wrong game day — the likely cause of P-002's 74% void rate).
- **NCAAW removed from the odds-api scan list:** `HTTP 404 Unknown sport` (~1,164
  errors/day), NOT a season issue — the key itself is wrong.
- **Research answered:** Polymarket US has a real trading API but shares nothing
  with the offshore CLOB (rewrite + KYC to use); MLB moneyline (`KXMLBGAME`) does
  charge maker fees, props don't.

### Live-agent recursive review

Tier 1 was built; **Tier 2** (offline challenger evaluator, `src/maker_challenger.py`,
33 tests) built this session — proposals-only, champion never touched. It's what
measured P-016's adverse-selection profile and ran the v2 grid. Tier 3 and three
design-doc gaps (no [min,max] bounds, no promotion margin, arm-budget arithmetic)
remain open.

### Current gate progress (all correctly time-blocked)

| Track | Progress | Next milestone |
|---|---|---|
| P-017 golf | 1/8 tournaments | ~mid-Sept |
| MLB props | 3/~27 game-days | ~Aug 17 (07-22 first clean full-slate day) |
| Book capture | started, ~2wk window | ~Aug 4 |
| P-001 CLV | ~81 rows / 200 | slow |
| P-015 tennis | 0/120 | US Open quals Aug 17–21 |

---

---

## Known Open Gaps (current)

Genuinely-open items as of 2026-07-22. Fixed/superseded items live in the
archive. Cross-cutting infra items (429 budget, timezone, aggregate_risk P&L
reset) are covered in System Overview and the July 21–22 entry.

1. **`aggregate_risk` daily-P&L resets at UTC midnight** (= 8pm ET, mid-slate) —
   a real risk-control weakening. Deferred while pods validate; scheduled fix
   `fix-aggregate-risk-pnl-reset-tz` (~2026-09-15, gated on P-017 clearing).
2. **P-001 `aggregate_risk` not wired** in `kalshi_moneyline.py`'s `from_config`
   — minor; the guard still binds at the main-loop level.
3. **NCAAB fuzzy matching broken** for 2–3-letter college codes (UK, ISU, KU…)
   vs Odds API full names — scores below `min_team_score=50`. Blocks NCAAB on
   P-001; fix is an abbreviation→full-name lookup in the matcher. (Seasonal.)
4. **P-015 tennis settlement source — RESOLVED.** The pod now reads settled
   match results from Kalshi's public production API rather than Odds API
   `/scores`; 26/26 gate rows are binary WIN/LOSS, not stale auto-voids. The
   Aug 7 readiness audit also corrected all 26 legacy settlements from gross to
   fee-net P&L (-$3.48 total adjustment) without changing the locked gate
   statistic or verdict.
5. **Kalshi demo API never settles markets** (`status=active` always) — all
   settlement runs off Odds API scores / public-API `result`, not the Kalshi
   demo endpoint.
6. **NHL has no moneyline/game-winner tickers** on Kalshi production (props
   only) — any NHL moneyline pod is blocked at the venue.
7. **Legacy vs multi-pod trade-log schema divergence** (`market_ticker` vs
   `market_id`+`pod_id`+`venue`) complicates cross-pod log analysis.
8. **Book capture is Kalshi-only, REST-sampled** (demo creds block the
   websocket). The Polymarket-mid leg that R-EV-MAP Build 3 needs is not built.
9. **Shelved-pod revival notes** (P-002/P-006 Polymarket matching: derivative-
   event contamination + same-title-different-date collision; likely cause of
   P-002's 74% void rate) are recorded in `manager/registry.yaml`. Fix those
   before ever re-enabling.
10. **Live-agent Tier 3** and three design-doc gaps (no [min,max] bounds, no
    promotion margin, arm-budget arithmetic) remain open.

---

## File Map

### Core Engine (src/)

| File | Lines | Purpose |
|------|-------|---------|
| `main.py` | ~150 | Backward-compat shim re-exporting symbols from config_loader, engine, cli |
| `config_loader.py` | ~140 | YAML loading, deep merge, pod filtering, tennis enrichment |
| `engine.py` | ~700 | Runtime engine: venue clients, shared deps, cycle callback, guarded loop |
| `cli.py` | ~180 | CLI entry point, argument parser, logging setup |
| `web_dashboard.py` | ~410 | Live web dashboard (API handlers + template loading) |
| `pod_runner.py` | ~315 | Orchestrates scan cycles across active pods |
| `capital_allocator.py` | ~494 | Pod-level bankroll allocation + performance tracking |
| `aggregate_risk.py` | ~400 | Portfolio-level exposure/drawdown/halt controls |
| `base_pod.py` | ~255 | Abstract BasePod interface + shared helpers |
| `trade_store.py` | ~450 | Centralized in-memory indexed JSONL store with atomic writes |
| `multi_executor.py` | ~280 | Venue-agnostic execution router |
| `pod_registry.py` | ~100 | Decorator-based pod auto-discovery |
| `pnl_calculator.py` | ~180 | Shared P&L calculator with venue-specific fees |
| `trade_log_schema.py` | ~180 | Trade log normalization, validation, migration |
| `constants.py` | ~30 | Centralized magic numbers |
| `protocols.py` | ~140 | Protocol-based structural type hints |
| `compat.py` | ~120 | Legacy adapter layer with deprecated aliases |
| `watchdog.py` | ~60 | Systemd notify socket integration |
| `settlement_bridge.py` | ~500 | Settlement orchestration for both venues |
| `polymarket_client.py` | ~700 | Polymarket API wrapper (Gamma + CLOB) |
| `polymarket_matcher.py` | ~260 | Odds API → Polymarket market matcher |
| `polymarket_settler.py` | ~287 | P-006 settlement engine |

### Pods (src/pods/)

| File | Pod | Strategy |
|------|-----|----------|
| `kalshi_moneyline.py` | P-001 | Kalshi Moneyline Value (delegates to legacy Scanner) — **active** |
| `live_game_pod.py` | P-014 | Live Game Agent (in-play consensus) — **active** |
| `qualifier_favorite_pod.py` | P-015 | Tennis Qualifier Favorite — **active** |
| `golf_topn_pod.py` | P-017 | Golf Top-N pre-tournament value — **active** |
| `live_maker_pod.py` | P-016 | Live In-Play Maker — **retired** (own unit, idle) |
| `cross_venue_arb.py` | P-002 | Cross-venue arb (Kalshi/Polymarket) — shelved |
| `polymarket_consensus.py` | P-006 | Sportsbook-Polymarket consensus — shelved |
| `forecastex_kalshi_arb.py` | P-004 | ForecastEx/Kalshi arb — disabled |
| `crypto_options_arb.py` | P-013 | Kalshi-Deribit crypto options — disabled |
| `signup_bonus_pod.py` / `boost_scanner_pod.py` / `macro_nowcast.py` | P-009/010/012 | disabled |

Standalone (not in `src/pods/`): `src/golf_fade_maker.py` (P-017M, shelved),
`src/book_capture.py` (book-capture daemon), `src/maker_challenger.py` (Tier-2
challenger evaluator).

### Templates (src/templates/)

| File | Purpose |
|------|---------|
| `dashboard.html` | 815-line HTML/CSS/JS dashboard (extracted from web_dashboard.py) |

### Legacy (Legacy/Kalshi Arb Project/src/)

| File | Lines | Purpose |
|------|-------|---------|
| `scanner.py` | ~850 | Kalshi scanner — sport-aware prefix filtering, synthetic pricing, game-started guard |
| `settler.py` | ~603 | Kalshi settlement (rewritten: Odds API scores primary) |
| `odds_client.py` | ~400 | The Odds API wrapper |
| `matcher.py` | ~320 | Odds API → Kalshi market matcher — ticker prefix blocklist + fast rejection |
| `edge_calculator.py` | ~200 | Pinnacle-weighted consensus edge computation |
| `risk_manager.py` | ~350 | Per-pod risk limits + Ledger |
| `kalshi_client.py` | ~400 | Kalshi API wrapper |
| `logger_setup.py` | ~50 | structlog configuration |

### Config

| File | Purpose |
|------|---------|
| `config.yaml` | Base config (Kalshi settings, odds_api sports, risk limits) |
| `config_multi_pod.yaml` | Multi-pod overlay (P-006 settings, aggregate risk, pod allocations) |

### Scripts

| File | Purpose |
|------|---------|
| `scripts/deploy.sh` | Pre-deploy tests, rsync, health check, auto-rollback |
| `scripts/betting-pod-shop.service` | Hardened systemd unit (Type=notify, WatchdogSec, ProtectSystem) |
| `scripts/server_setup.sh` | VPS provisioning (bettingbot user, venv, cron, journald) |
| `scripts/rotate_trade_logs.py` | Monthly trade log archival to .jsonl.gz |
| `scripts/migrate_trade_log.py` | One-time log schema migration script |

### Tests

| File | Tests | Purpose |
|------|-------|---------|
| `tests/test_trade_store.py` | ~30 | TradeStore unit tests |
| `tests/test_pod_runner_concurrent.py` | ~10 | Concurrent scanning verification |
| `tests/test_watchdog.py` | 7 | Systemd watchdog no-op and real socket |
| `tests/test_rotate_trade_logs.py` | 5 | Trade log rotation |
| `tests/test_trade_log_schema.py` | 15 | Schema normalize/validate/migrate |
| `tests/test_integration.py` | 10 | End-to-end pipeline integration |
| `tests/test_pnl_calculator.py` | 17 | P&L calculator with venue fees |

---

## Data Flow

### Scanning

```
Every 5 min cycle:
  1. Settler checks open positions → settles completed games
  2. PolymarketSettler checks open positions → settles resolved markets
  3. AggregateRiskGuard pre-cycle check (exposure/drawdown limits)
  4. PodRunner.run_once() → each pod scans for edges
     P-001: Kalshi markets × Odds API events → Matcher → EdgeCalculator → RiskManager → PLACED
     P-006: Polymarket markets × Odds API events → Matcher → EdgeCalculator → RiskManager → PLACED
  5. Callback: update dashboard, log metrics, persist trades via TradeStore
```

### Settlement

```
P-001 (Kalshi):
  settler.settle_cycle()
    → TradeStore.get_placed_entries() for unresolved PLACED
    → For each: _check_scores(sport) via Odds API /v4/sports/{sport}/scores/?daysFrom=3
    → Fuzzy match game by team names + time → determine winner from scores
    → Map winner to YES/NO via yes_side field → _settle_position() writes WIN/LOSS to log
    → Falls back to Kalshi API for non-sports markets

P-006 (Polymarket):
  polymarket_settler.settle_cycle()
    → TradeStore.get_placed_entries() for unresolved P-006 PLACED
    → For each: client.get_market_resolution(slug=market_slug)
    → If closed=True and prices indicate resolution → determine result
    → _calc_outcome_pnl() → write WIN/LOSS/VOID to trade log
```

### Dashboard Data

```
/api/status JSON payload:
  engine_status: "running" | "halted" | "starting"
  risk: { bankroll, total_exposure_usd/pct, daily_pnl, open_positions, halted, venue_exposure }
  cycle: { cycle_number, duration_seconds, pods_scanned, placed/skipped/error_count, success_rate }
  pods: { pod_id → { name, placed, wins, losses, win_pct, pnl, alloc_pct, max_position_usd, capital_usd } }
  settlement: { total_pnl, total_settled, wins, losses, voids, win_rate }
  trades: [ { status, timestamp_utc, event, market_ticker, side, position_size_usd, edge_pct, pnl_usd, settled_at_utc, ... } ]

Trade status resolution (in _read_recent_placed_trades):
  1. Match PLACED entry fingerprint → settlement entry fingerprint (exact match)
  2. Fallback: match PLACED entry ticker → settlement entry ticker (for legacy/orphan entries)
  3. If no match: status = "OPEN"
```

---

## Pending / Known Gaps (ARCHIVED — see "Known Open Gaps (current)" above)

*This March-2026 list mixed since-fixed items, now-shelved P-006/Polymarket
notes, and a stale "35 test failures" line (the suite is green). The still-open
items were curated into **Known Open Gaps (current)** near the top of this
document; the Polymarket-matching detail lives in `manager/registry.yaml`.*
---

## Useful Commands

```bash
# ── Deploy (use the script; it excludes data/, chowns, health-checks) ──
bash scripts/deploy.sh 129.212.176.202          # code only, no restart
bash scripts/deploy.sh 129.212.176.202 restart  # + restart betting-pod-shop

# ── Standalone units (deploy.sh restarts ONLY betting-pod-shop) ────────
ssh root@129.212.176.202 'systemctl restart betting-book-capture'   # or ...-live-maker
#   mlb-props-collector code lives in /opt/mlb-props (outside deploy.sh) — scp by hand

# ── Live logs / status (any of the 4 services) ────────────────────────
ssh root@129.212.176.202 'journalctl -u betting-pod-shop -f'
ssh root@129.212.176.202 'systemctl status betting-pod-shop --no-pager'

# ── 429 health (the shared per-IP budget) ─────────────────────────────
ssh root@129.212.176.202 'journalctl -u betting-pod-shop --since "1 hour ago" --no-pager | grep -c "status_code=429"'

# ── Daily brief / live state (authoritative) ──────────────────────────
python3 manager/refresh.py && python3 manager/brief.py

# ── Trade log stats ───────────────────────────────────────────────────
ssh root@129.212.176.202 'grep -c "\"action\": \"PLACED\"" /opt/betting-pod-shop/data/trade_logs/trade_log.jsonl'

# ── P-016 maker kill switch (already retired; leave in place) ──────────
ssh root@129.212.176.202 'touch /opt/betting-pod-shop/data/KILL_MAKER'
```

---

## Known Constraints

- **Paper mode only** — `environment: demo` in config, orders logged but never actually placed. Dual safety gate: `place_order()` blocked unless `environment="live"` AND `I_UNDERSTAND_LIVE_TRADING` env var is set.
- **Production API, paper mode** — reads production Kalshi
  (`https://api.elections.kalshi.com/trade-api/v2`) for full market inventory;
  the demo environment guard prevents real order placement. **Demo credentials
  only** — no authenticated websocket/order access.
- **Shared per-IP Kalshi rate budget** — all 4 services share one throttle
  (`kalshi.rate_limit` = 2 req/s); new pollers must be rate-aware.
- **Polymarket** — data only; no execution access.
- **Odds API rate limits** — each /scores call costs 2 API requests per sport; cached per cycle
- **Odds API plan limit** — `daysFrom` parameter capped at 3 (using 7 returns HTTP 422)
- **Kalshi market page limit** — scanner now fetches up to 25 pages × 200 = 5000 markets per cycle (increased from 2000 in Session 5)
- **P-006 SANITY_SKIP threshold** — set to 25% (0.25). Below 25% = legitimate edge; above 25% = likely mapping error. Lowering risks placing trades with inverted YES/NO mappings; raising risks missing real opportunities



# 📁 ARCHIVE — historical session log (not maintained)

Everything below reflects the state at its date. Much predates the July 2026
Kalshi-only pivot (P-006/Polymarket-centric pods, old deploy procedure, etc.)
and is kept for provenance only. For current state use the sections above,
`manager/registry.yaml`, and the July 21–22 entry.

---

## Recent Changes (Session 10 — March 24, 2026)

### Fix: Blocked First-Half Winner Tickers ✅

**File:** `Legacy/Kalshi Arb Project/src/matcher.py`

Added 7 sport-specific prefixes (`KXNCAAMB1H`, `KXNCAABB1H`, `KXNBAGAME1H`, `KXNBA1H`, `KXNHL1H`, `KXNFL1H`, `KXMLB1H`) to `_BLOCKED_TICKER_PREFIXES` plus a catch-all substring check for `1HWINNER`/`1HWIN`. The Odds API doesn't provide first-half lines, so these markets can never match correctly. Before: failing with TEAM_MISMATCH or low fuzzy scores (e.g. NCAAB titles diluted by ": First Half Winner?" suffix). After: 21 tickers silently rejected at prefix stage.

### Fix: Removed Challenger Tennis from Scanner ✅

**File:** `Legacy/Kalshi Arb Project/src/scanner.py`

Removed `KXATPCHALLENGERMATCH` and `KXWTACHALLENGERMATCH` from `_SPORT_TICKER_PREFIXES`. The Odds API only covers main-draw ATP/WTA events (Miami Open, Indian Wells, etc.) — challenger-tier tournaments have no matching data. Before: 98 challenger markets scanned per cycle, all failing with NO_FUZZY_MATCH. After: ATP markets scanned dropped from 146 → 48, WTA from 18 → 4. All main-draw matches continue matching at 76-100 scores.

### VPS User Migration: root → bettingbot ✅

Migrated the VPS from running as `root` via `nohup` to a dedicated `bettingbot` system user via systemd:

- Created `bettingbot` system user (no login shell, no home directory)
- Transferred ownership of `/opt/betting-pod-shop/` to `bettingbot:bettingbot`
- Fixed service file: `Type=notify` → `Type=simple` (sdnotify not installed), venv path `.venv` → `venv`
- Installed and enabled `betting-pod-shop.service` with security hardening: `ProtectSystem=strict`, `NoNewPrivileges=true`, `ProtectHome=true`, `PrivateTmp=true`, `ReadWritePaths` limited to `data/`
- Logs now go to journald (`journalctl -u betting-pod-shop -f`) instead of `output.log`
- Auto-restart on failure with 30s cooldown
- Dashboard confirmed responding on port 8080

**Deploy procedure updated:** After `rsync`, run `chown -R bettingbot:bettingbot /opt/betting-pod-shop` then `systemctl restart betting-pod-shop`.

### NHL TEAM_MISMATCH — Not a Bug

Investigated 10 NHL games failing with TEAM_MISMATCH (e.g. CBJMTL, CHIPHI, MINFLA). Root cause: Odds API only had 16 NHL events (mostly near-term games). March 26 games hadn't been posted yet. The matcher was finding the best available Odds API event (one team would partially match), and TEAM_MISMATCH correctly rejected these wrong matches. No code fix needed — events will match once Odds API posts lines closer to game time.

---

## Recent Changes (Session 9 — March 24, 2026)

### Investigation: P-006 Not Placing New Trades

P-006 was loaded and scanning across 4/8 sports (NBA, NHL, ATP tennis, WTA tennis) but ALL matches hit SKIP_EDGE. Raw edges ranged from 0.1%–0.7% while `min_edge_pct` was 2%. Root cause: Polymarket is a far more efficient market than Kalshi — sophisticated market makers keep prices tightly aligned with sportsbook consensus, leaving very small arbitrage windows.

**Key insight:** P-001 finds large edges (3–10%) because Kalshi has lower liquidity and less active market making for sports markets. These are real edges on live Kalshi API prices (not demo — the system reads from `trading-api.kalshi.co` production), though order placement is gated by `environment: demo`. Polymarket, by contrast, has deep liquidity and edges rarely exceed 1%.

### Fix 1: Lowered P-006 Edge Thresholds ✅

**File:** `config_multi_pod.yaml` (P-006 section)

- `min_edge_pct`: 0.02 → 0.008 (2% → 0.8%). With 0% Polymarket fees, edges above ~0.5% are genuinely +EV. The old 2% threshold was blocking every single match.
- `min_ev`: added at 0.005 (0.5%). The EdgeCalculator's `best_side()` applies TWO filters: `raw_edge >= min_edge_pct` AND `ev >= min_ev`. The default `min_ev` of 1% was a second gate blocking trades even if the edge threshold was met. At skewed prices, EV can be lower than raw edge, so both thresholds needed lowering.

**Result:** After deploy, edges are still mostly below 0.8% (largest observed: Lehecka vs Fritz tennis at 0.68%, Kraken vs Panthers NHL at 0.64%). This is expected — Polymarket efficiency means edges are thin. Trades should appear when lines move around game time, creating brief windows above 0.8%.

### Investigation: MLB Matching Failure (0 matches despite 12 markets × 15 events)

DIAG fuzzy scores of 62–70 (above the 55 threshold) suggested the fuzzy check was passing but `both_teams_present` was rejecting. Deeper investigation revealed the **real cause: Polymarket and Odds API have completely different games.**

Polymarket's MLB series (series_id=3) was returning stale spring training markets (created early March with `startDate` of March 3). The Odds API had upcoming regular season games starting March 26. Example: Polymarket had "Boston Red Sox vs. Minnesota Twins" while Odds API had Red Sox playing Cincinnati Reds and Twins playing Baltimore Orioles — different opponents entirely.

**No code fix needed.** MLB matching will self-resolve once Polymarket lists regular season game-day markets (MLB regular season started March 20, 2026). The team name formats are identical between venues so matching will work perfectly once the same games appear on both.

### SSH Access from Cowork ✅

Mounted `~/.ssh` directory, enabling direct VPS deployment and management from Cowork sessions without requiring the user to run commands manually on their Mac terminal.

---

## Recent Changes (Session 8 — March 23-24, 2026)

### Investigation: P-001 Stopped Placing Trades After Initial Burst

P-001 placed ~45 trades in the first 3 scan cycles after deploy (March 21, 3:40-3:50 PM ET) but zero trades since. This session diagnosed and fixed five interconnected issues.

### Fix 1: Legacy sys.path Not Set — Scanner, Settler, KalshiClient All Failing ✅

**Root cause:** The legacy modules (`scanner.py`, `settler.py`, `kalshi_client.py`, etc.) live in `Legacy/Kalshi Arb Project/src/` and need bare imports (`from scanner import Scanner`). No `.pth` files, no symlinks, no `PYTHONPATH` environment variable existed — so ALL legacy imports failed with `ModuleNotFoundError`.

This meant:
- `build_venue_clients()` failed to import `KalshiClient` → Kalshi client was `None`
- `build_shared_deps()` failed to import `Scanner` and `Settler`
- Since Settler is built conditionally (`if kalshi_client:`), it was silently skipped
- P-001 was NOT running AT ALL on the VPS; zero settlements were ever written for legacy trades

**Fix:** Added `sys.path.insert(0, _LEGACY_SRC)` at **module level** in `src/engine.py`, before any function definitions. First attempt placed it inside `build_shared_deps()`, but that was too late — `build_venue_clients()` runs first and also needs `from kalshi_client import KalshiClient`.

**Lesson learned:** The sys.path fix must be at module level, not inside a function, because multiple functions need legacy imports and they run in sequence.

**File changed:** `src/engine.py`

### Fix 2: max_positions=10 Blocking All P-001 Trades ✅

**Root cause:** `Legacy/Kalshi Arb Project/config.yaml` had `risk.max_positions: 10`. This is the legacy RiskManager's limit, completely separate from `config_multi_pod.yaml`'s `aggregate_risk.max_open_positions: 200`. After the initial 45-trade burst, the Scanner was blocked by `BLOCK_MAX_POSITIONS` on every subsequent scan. There were 3,829 `SKIPPED_RISK` entries with "MAX_POSITIONS: Open positions (X) at maximum (10)".

**Fix:** Changed `max_positions: 10` → `max_positions: 50` in `Legacy/Kalshi Arb Project/config.yaml`.

**Lesson learned:** Two completely separate risk systems exist — the legacy RiskManager (reads `config.yaml`) and the multi-pod AggregateRiskGuard (reads `config_multi_pod.yaml`). Both must be configured.

**File changed:** `Legacy/Kalshi Arb Project/config.yaml`

### Fix 3: capital_allocator.pre_cycle NoneType Error ✅

**Root cause:** `capital_allocator.py` line 159 used `hasattr(pod, "risk_manager")` which returns `True` even when `pod.risk_manager is None`. Then `rm = pod.risk_manager` → `rm = None` → `rm.max_position_usd = ...` crashed with `'NoneType' object has no attribute 'max_position_usd'`. This error fired every cycle for pods without a risk_manager, with the fallback message "pods will use previous allocations".

**Fix:** Changed to `rm = getattr(pod, "risk_manager", None)` with `if alloc and rm is not None:` check.

**Lesson learned:** `hasattr()` returns `True` for attributes set to `None`. Use `getattr(obj, attr, None) is not None` for null-safe checks.

**File changed:** `src/capital_allocator.py`

### Fix 4: Stale Positions Blocking Scanner — STALE_POSITION_HOURS 72→24 ✅

**Root cause:** With the Settler broken for 2+ weeks, the trade log accumulated 181 unsettled PLACED entries. The Settler's 72-hour auto-void couldn't clear March 21-22 entries (~50 hours old) because they hadn't reached the threshold. Meanwhile, the Odds API returned `completed=0` for tennis (scores endpoint doesn't reliably return completed tennis matches), and the Kalshi demo API returned `status=active` for everything. The Scanner's `_open_game_keys()` treated all unsettled entries as open, blocking trades on those games.

**Fix:** Changed `STALE_POSITION_HOURS` from 72 to 24 in `Legacy/Kalshi Arb Project/src/settler.py`. Sports games complete within hours — 24 hours is generous enough to avoid premature voids while preventing stale positions from permanently blocking the scanner.

**Lesson learned:** The Kalshi demo API never settles markets (`status=active` forever). The Odds API tennis scores are unreliable. Auto-void is the critical safety net — it must be aggressive enough to clear positions within a day.

**File changed:** `Legacy/Kalshi Arb Project/src/settler.py`

### Fix 5: Event-Level Dedup — Preventing Duplicate Trades Across Market Types ✅

**Root cause:** The Scanner's `_open_game_keys()` extracted game keys using `ticker.rsplit("-", 1)[0]`, which only strips the last segment. For tennis, this produced three different keys per match:
- `KXATPMATCH-26MAR24ATMTIA` (match winner)
- `KXATPSETWINNER-26MAR24ATMTIA-1` (set 1 winner)
- `KXATPSETWINNER-26MAR24ATMTIA-2` (set 2 winner)

The within-cycle `placed_event_ids` dedup prevented duplicates in one cycle, but across cycles (every 5 min), each new market type passed the game key check and got placed. One tennis match generated 3 trades; one NBA game generated 3 trades (game + 1H + 2H). This caused the trade count to balloon from 45 to 126.

**Fix:** Added `_extract_event_key(ticker)` helper that splits on the first dash and takes only the date+teams segment (e.g., `26MAR24ATMTIA`). This is shared across all market types for the same event. Updated both `_open_game_keys()` (uses a `Counter` for reference-counted tracking so voiding one market type doesn't prematurely free the event) and `_scan_sport()` to use event-level keys.

**Lesson learned:** Kalshi has multiple market types per event (match winner, set winners, half winners). Dedup must operate at the EVENT level, not the MARKET level, to prevent correlated position stacking.

**Files changed:** `Legacy/Kalshi Arb Project/src/scanner.py`

### Updated Deploy Procedure

The VPS currently runs via `nohup` (not systemd). The two-directory staging approach is no longer used — rsync goes directly to `/opt/betting-pod-shop/`.

**Safe deploy from Mac:**
```bash
# Step 1: rsync code (EXCLUDES data/ to protect live trade logs)
rsync -avz --exclude='data/' --exclude='*.pyc' --exclude='__pycache__/' -e ssh '/Users/samlawhon/Desktop/Betting Fund Project/' root@129.212.176.202:/opt/betting-pod-shop/

# Step 2: SSH in and restart
ssh root@129.212.176.202
fuser -k 8080/tcp 2>/dev/null; sleep 2 && cd /opt/betting-pod-shop && nohup /opt/betting-pod-shop/venv/bin/python -m src.main --loop --interval 300 --web --no-browser > /opt/betting-pod-shop/output.log 2>&1 & echo 'Started'

# Step 3: Verify (wait 30s for first cycle)
sleep 30 && grep 'settler_cycle_done\|cycle.*placed\|ERROR' /opt/betting-pod-shop/output.log
```

---

## Recent Changes (Session 7 — March 22, 2026)

### NCAAW Support Added ✅

Added women's college basketball (`basketball_ncaaw`) support across all relevant components:

- **`config_multi_pod.yaml`** — Added `basketball_ncaaw` to `odds_api.sports` list (P-001 and P-006 now pull NCAAW lines from The Odds API)
- **`Legacy/.../scanner.py`** — Added `"basketball_ncaaw": ("KXNCAAWBGAME", "KXNCAAWGAME", "KXNCAAWBTOTAL")` to `_SPORT_TICKER_PREFIXES`. Game-winner prefixes are speculative; `KXNCAAWBTOTAL` is confirmed live on Kalshi.
- **`Legacy/.../matcher.py`** — Added `basketball_ncaaw` to `_NCAAB_SPORTS` so `_expand_ncaab_abbrevs()` runs on NCAAW market titles. Women's teams share the same school codes (UK, ISU, KU, etc.) as NCAAB.
- **`src/pods/polymarket_consensus.py`** — Added `"basketball_ncaaw": "NCAAW"` to `_SPORT_LEAGUE_NAME` for P-006 Gamma API series_id discovery.

---

## Recent Changes (Session 6 — March 22, 2026)

### P-006 Double-Filter Fix (Zero Trades Since ~March 12) ✅

**Root cause:** The Phase II optimization introduced `MIN_KELLY_FRACTION = 0.02` as a global low-conviction filter in `base_pod.py::compute_position_size()`. This was calibrated for P-001 (Kalshi, 7% fee, 7%+ min_edge) and worked well there. But for P-006 (Polymarket, 0% fee, 3% min_edge), the Kelly fraction at quarter-Kelly for a 3% edge is only 0.015 at mid=0.50 — below the 0.02 floor. This created a double-filter where:
- 3% min_edge + 2% min_kelly at mid=0.50 requires **4% real edge** to place
- At mid=0.30 (underdogs), it requires **6% edge**
- Only at extreme favorites (mid=0.70+) did 3% edge actually pass

Since Polymarket markets rarely diverge 4-6% from sportsbook consensus, this effectively killed ALL P-006 trade placement starting ~March 12 when the optimization was deployed.

**Fix:**
1. Made `min_kelly_fraction` per-pod configurable via config YAML + `BasePod.__init__()` override parameter
2. P-006 config sets `min_kelly_fraction: 0.005` (0.5%) — still filters zero-conviction noise but doesn't compound with edge threshold
3. Lowered P-006 `min_edge_pct` from 3% to 2% — with 0% Polymarket fees, 2% edge is genuinely profitable
4. Added diagnostic logging: `SKIP_EDGE` (raw numbers), `SKIP_KELLY` (Kelly vs floor), and `PLACING` (confirmation) log lines so the VPS journal shows exactly where trades are filtered

**Files changed:** `src/constants.py`, `src/base_pod.py`, `src/pods/polymarket_consensus.py`, `config_multi_pod.yaml`

---

## Recent Changes (Session 5 — March 21, 2026)

### Duplicate Trade Fix (P-001 + P-006) ✅

**Root cause:** `trade_store` was created in `shared_deps` by `engine.py` and passed to `pod_class.from_config()` as overrides, but both P-001 and P-006 ignored it — neither `__init__` accepted a `trade_store` parameter, nor did `from_config()` extract it from overrides. This meant `BasePod._trade_store` was always `None`, forcing fallback to weaker in-memory dedup sets that reset on restart. P-006 had 42 markets with duplicate placements (up to 7x each).

**Fix:** Added `trade_store=None` parameter to `__init__()` and `trade_store=overrides.get("trade_store")` to `from_config()` in both `src/pods/polymarket_consensus.py` and `src/pods/kalshi_moneyline.py`. Now `has_open_position()` and `is_duplicate()` query the centralized `TradeStore`.

### P-006 SANITY_SKIP Threshold Fix ✅

**Root cause:** The `_validate_and_correct_mapping()` function in `polymarket_consensus.py` had a hard-coded 15% (0.15) absolute-difference threshold. Any match where Polymarket price and sportsbook consensus differed by >15pp was skipped as a "probable mapping error." In practice, legitimate edge opportunities routinely exceed 15pp — this was killing virtually ALL P-006 trade placements since ~March 13.

**Fix:** Raised threshold from `0.15` to `0.25` in `_validate_and_correct_mapping()`. Markets with >25% disagreement (likely genuine mapping errors or stale data) still get filtered. Markets with 15-25% disagreement (legitimate edge) now pass through.

### Settler daysFrom Fix ✅

**Root cause:** The Kalshi settler's `_fetch_scores()` in `Legacy/Kalshi Arb Project/src/settler.py` used `daysFrom=7` in the Odds API `/scores` URL. The API plan only supports `daysFrom=3`, causing HTTP 422 for ALL sports. This broke scores-based settlement entirely — no trades were being resolved through real game results. The Kalshi demo API fallback always returns `status=active`, so positions sat OPEN indefinitely.

**Fix:** Changed `daysFrom=7` to `daysFrom=3` in `settler.py` line 542. Settlement now resolves positions using real game scores within 3 days of game completion.

### Data Loss Incident — March 21, 2026

During deployment, `rsync -av /root/Betting-Fund-Project/ /opt/betting-pod-shop/` was run WITHOUT `--exclude 'data/'`. This overwrote the live trade log (containing trades through March 19) with the stale Mac copy (last synced ~March 7). Approximately 175 P-001 trades and ~107 P-006 trades from March 7-19 were lost. The journal does not contain raw trade JSON entries (trades are written to file, not stdout), so recovery was not possible.

**Prevention:** Always use `--exclude 'data/'` when rsyncing to `/opt/betting-pod-shop/`. See deployment rules above.

---

## Recent Changes (Sessions 3-4 — March 13-14, 2026)

### Phase II: Reliability & Safety Hardening ✅

**Magic Numbers Centralized** — Created `src/constants.py` with `DEFAULT_BANKROLL`, `DEFAULT_MAX_BET_PCT`, `MIN_VALID_PRICE`, `MAX_VALID_PRICE`, `HTTP_TIMEOUT_SECONDS`, `DEDUP_WINDOW_HOURS`. Replaced hardcoded `10_000.0` in 12+ locations and `0.03` max_bet_pct in 4 pod files.

**VPS Security Hardening** — Rewrote `scripts/betting-pod-shop.service` with `Type=notify`, `WatchdogSec=900`, `ProtectSystem=strict`, `PrivateTmp=true`, `ReadWritePaths`, and kernel protections. Created `scripts/server_setup.sh` for `bettingbot` system user provisioning.

**Watchdog + Log Rotation** — Created `src/watchdog.py` (zero-dependency systemd notify socket integration) and `scripts/rotate_trade_logs.py` (archives entries >30 days into compressed monthly `.jsonl.gz` files).

### Phase III: Architecture Cleanup ✅

**Split main.py** — Decomposed 1,191-line `main.py` into `config_loader.py`, `engine.py`, `cli.py`, plus a thin backward-compat shim so all existing test patches (`@patch("src.main.PodRunner")` etc.) continue to work unchanged.

**Trade Log Schema** — Created `src/trade_log_schema.py` with `TradeLogSchema.normalize()`, `validate()`, and `migrate_file()`. Handles field aliasing (market_ticker→market_id, kalshi_prob→venue_prob), defaults, numeric coercion, and atomic file rewrite.

**BasePod Helpers** — Added `get_bankroll()` and `compute_position_size()` to `BasePod`, simplifying all 4 active pods from ~12 lines of duplicated position-sizing logic to a single method call.

**Deploy Script Hardening** — Rewrote `scripts/deploy.sh` with pre-deploy `pytest` gate, post-deploy health check (60s timeout), and automatic rollback on failure. Added rsync excludes for `*.log`, `.mypy_cache`, `.pytest_cache`, `venv/`.

**Integration Tests** — Created `tests/test_integration.py` with 10 end-to-end tests covering the scan → match → edge → risk → place → settle pipeline.

### Phase IV: Developer Experience & Future-Proofing ✅

**Pod Auto-Discovery** — Created `src/pod_registry.py` with `@register_pod("P-xxx")` decorator applied to all 7 pods. `discover_pods()` auto-imports all `src/pods/` modules at startup. `pod_runner.py` updated to use registry with legacy fallback.

**Dashboard Template Extraction** — Moved 815 lines of inline HTML/CSS/JS from `web_dashboard.py` into `src/templates/dashboard.html`. Python file shrank from 1,201 → 411 lines. Loads from disk with graceful fallback.

**Type Hints + mypy** — Created `src/protocols.py` with Protocol-based structural types for all duck-typed dependencies (EdgeCalculator, RiskManager, venue clients, AggregateRisk, TradeStore). Fixed 8 mypy errors across pod files. All 18 hot-path source files pass mypy cleanly.

**Shared P&L Calculator** — Created `src/pnl_calculator.py` with venue-specific fee structures (Kalshi 7% profit fee, Polymarket 0%, ForecastEx 3.14% APY coupon). Supports batch computation, custom fee overrides, and collateral credit for holding periods. 17 tests.

**Legacy Adapter Layer** — Created `src/compat.py` providing single-import access to all public symbols. Deprecated aliases emit `DeprecationWarning` at import time.

---

## Prior Session Changes (Session 2 — March 12, 2026)

### Dashboard Audit & Duplicate Settlement Fix ✅

Rewrote `_read_recent_placed_trades()` to resolve by fingerprint first (falling back to ticker), eliminating duplicate counting when multiple PLACED entries share a ticker. Added VOID entry for orphan Winthrop NO-side trade. Dashboard numbers reconciled: 25 W / 16 L / 187 V, P&L $1,707.05, Win Rate 61.0%.

### P-001 Pod Rename ✅

Renamed from "Kalshi Moneyline Value" to "Kalshi vs Sharp's Strategy". Updated `capital_allocator.py` to always refresh `pod_name` from live pod object.

---

## Prior Session Changes (Session 1 — March 12, 2026)

### Exotic Ticker Prefix Blocklist ✅

Added `_BLOCKED_TICKER_PREFIXES` (~20 prefixes) and fast rejection in `match()` to skip exotic/prop tickers before fuzzy computation.

### Sport-Aware Ticker Prefix Filtering ✅

Added `_SPORT_TICKER_PREFIXES` mapping in `scanner.py` — only markets matching sport-specific prefixes are passed to the matcher. Reduced scan cycle from ~9s to ~5.5s, log volume dropped 94%.

### Paper-Mode Synthetic Price Generation ✅

When `mode=="paper"` and no bid/ask exists, generates synthetic price from consensus fair probability ± deterministic hash-based offset.

### Switched to Production Kalshi API ✅

Changed `config.yaml` to `trading-api.kalshi.co` (production) while keeping `environment: demo` for safety.

---

## Prior Session Changes (March 5, 2026)

### P-001 Settler: Odds API Scores-Based Settlement ✅

Rewrote settlement to use Odds API `/scores` endpoint. All 5 positions settled: +$1,089.56 (4W / 1L).

### Game-Started Guard ✅

Added commence_time check to skip games already in progress.

### Dashboard Pod Performance Wiring ✅

Wired settlers to call `allocator.record_settlement()` so pod performance table shows real W/L/P&L.

### Portfolio Analytics Dashboard ✅

Added hero metrics (Total Return, Sharpe, Max Drawdown, Profit Factor), returns analysis, risk metrics, and equity curve canvas chart.

---
