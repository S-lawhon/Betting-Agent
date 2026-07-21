# Golf (P-017) — Handoff to Claude Code

Everything below is already on disk in this repo. Open Claude Code in the project
root (`claude`) and it auto-loads `CLAUDE.md`. Use the kickoff prompt, then work
the checklist.

## Kickoff prompt (paste into Claude Code)

> Read `CLAUDE.md`, `golf_research/P-017_Golf_Pod_Spec.md`, and
> `golf_research/backtest/REPORT_Golf_TopN_2026-07.md` to load context on the P-017
> golf pod. Then: (1) run `python3 -m pytest tests/test_golf_topn.py
> tests/test_datagolf.py -q` and confirm all pass; (2) run `git status` and show me
> the golf-related changes that are staged/unstaged; (3) do a dry-run scan of P-017
> against live Kalshi markets (instantiate `GolfTopNPod` with a real `KalshiPublic`,
> `datagolf_client=None`, and call `scan_once()`) and summarize how many bets it
> would place and why. Don't deploy or commit anything yet — just report.

## What exists (files to know)

Pod + engine
- `src/pods/golf_topn_pod.py` — P-017 taker pod (validated leg)
- `src/golf_fade_maker.py` — P-017M standalone fade maker (paper-experimental)
- `src/datagolf_client.py` — optional DataGolf model feed (off by default)
- `src/kalshi_fees.py` / `src/devig.py` — series-aware fees + power de-vig (promoted)
- `scripts/run_golf_maker.py` — runner for P-017M
- `tests/test_golf_topn.py`, `tests/test_datagolf.py` — 19 tests

Config
- `config_multi_pod.yaml` — P-017 in `pods.active`; P-017/P-017M blocks; `datagolf:`
  section (enabled: false)

Research / validation (context, not runtime)
- `golf_research/GOLF_KALSHI_RESEARCH.md` — literature + market-data research
- `golf_research/backtest/` — `backtest_golf.py`, `refine_golf.py`,
  `REPORT_Golf_TopN_2026-07.md`, `p017_params.json`, `backtest_results.json`
- `golf_research/P-017_Golf_Pod_Spec.md` — what shipped + rationale

## Open next steps (checklist)

- [ ] **Review & commit to git.** `git status`/`git diff` the golf files + `CLAUDE.md`
      + `config_multi_pod.yaml`; commit on a branch. (Nothing has been git-committed
      yet — files are only written to disk.)
- [ ] **Deploy to paper.** `bash scripts/deploy.sh 129.212.176.202 restart` — engine
      picks up P-017 from `pods.active`. Confirm it logs to `data/pods/P-017.jsonl`
      and shows on the :8080 dashboard.
- [ ] **Start P-017M collection.** `python3 scripts/run_golf_maker.py` (kill switch:
      `touch data/KILL_GOLF_MAKER`). Logs to `data/trade_logs/golf_maker_*.jsonl`.
- [ ] **Watch the first event (3M Open, Jul 23–26).** After it settles, sanity-check
      P-017 fills vs. results; expect ~30–40 placements/tournament in the 8–45¢ band.
- [ ] **DataGolf (optional).** Add `DATAGOLF_API_KEY` to `.env`, set
      `datagolf.enabled: true`. It replaces the structural `edge_bump` with a blended
      model prob; falls back automatically if the feed/match is missing.
- [ ] **Extend the sample.** After each event, re-pull data and re-run
      `golf_research/backtest/backtest_golf.py` to grow beyond the H1-2026 sample
      (10 tournaments for the taker leg, 4 for the maker). THOC26/COPC26 were
      excluded on the original pull (settlement lag) — fold them in now.
- [ ] **Go/no-go after ~8 tournaments.** Keep the leg if forward net edge / CLV holds
      above half the backtest baseline (taker +6.9¢ → threshold +3.45¢; maker
      **+3.3¢ → threshold +1.65¢**). Kill otherwise. Only then consider small real
      money per the v2 Phase-3 rules.
      ⚠️ The maker baseline was **+9.1¢ until 2026-07-21**, when a contract-weighting
      bug in `leg_fade_maker` was fixed (+3.3¢ weighted, CI [−4.25, +8.61] straddling
      zero on 6 events — and on the original 4 too). A +4.55¢ threshold derived from
      the old figure would have killed a leg that was performing *at* its true
      baseline. Leg B is now paper-only with an unproven edge; treat the maker
      go/no-go as "is there any edge at all", not "does it hold up".

## Gotchas the next agent must not relearn (also in CLAUDE.md)

- Kalshi top-N event timing is unreliable per-market → resolve ONE event-level
  close = MIN occurrence/close across the event's markets (`_resolve_event_closes`).
  Without this the pod sees everything ~20 days out and places nothing.
- Golf prop series charge ZERO maker fee (`quadratic`); winner series charge makers.
  Pass `series_ticker` to `fee_per_contract`.
- Orderbook is `orderbook_fp` in dollars, sub-penny; YES ask = 1 − best NO bid.
- Fade-maker window is 36h→6h before close; the 48–24h slice is NEGATIVE. Don't
  widen the start past 36h.
