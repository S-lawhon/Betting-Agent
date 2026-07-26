# P-022 — Round-Leader Dead-Heat Fade Maker: what shipped

**Betting Pod Shop · Pod spec (paper)**
**Built 2026-07-23**

Paper standalone maker that sells YES / makes NO on cheap Kalshi golf
round-leader names, harvesting the dead-heat split ($1/n) plus longshot
overpricing. Validated in `golf_quirks_research/` Phase 1 (settled-data gate)
and Phase 2 (tick-print maker-fill replay). **Paper/demo only — cannot place
real orders.**

## Mechanism

A tie for the round lead SPLITS the payout: YES pays $1/n rounded down
(PGAROUNDLEADER terms, quoted in [REPORT_Golf_Quirks_2026-07.md](REPORT_Golf_Quirks_2026-07.md)).
A name that leads realizes only E[payout|led]=$0.63 (37% haircut). Retail
buys cheap leader names as lottery tickets. Selling YES on the 5–10¢ band is
the mirror image of P-017's top-N tie-inflation. All 13 round-leader series
are `quadratic` → **zero maker fee**.

## What was validated

- **Phase 1:** 5–10¢ pre-round band, sell YES, +4–6¢/ct on settled data,
  tournament-clustered CI excluding zero.
- **Phase 2** ([REPORT_Golf_Quirks_Phase2_P022_2026-07.md](REPORT_Golf_Quirks_Phase2_P022_2026-07.md)):
  pessimistic tick-print fills. Adverse selection is real (E[settle|filled]
  3.2¢ vs posted 1.8¢) but does not flip the sign: **+2.1¢/ct (offset 0) to
  +4.7¢ (offset +4¢)** posting EARLY (12–24h pre-round), 16/19 tournaments
  positive, robust to leave-one-out. Posting late (6h) collapses to marginal.

## What shipped

- `src/round_leader_fade_maker.py` — `RoundLeaderFadeMakerEngine` +
  `@register_pod("P-022")` wrapper. Mirrors `src/golf_fade_maker.py` (P-017M).
- `scripts/run_round_leader_fade.py` — standalone runner (NOT the 5-min
  engine). Kill switch: `data/KILL_ROUND_LEADER_FADE`.
- `config_multi_pod.yaml` → `pods.P-022` block. **Deliberately not in
  `pods.active`** — it runs its own fast loop.
- `tests/test_round_leader_fade.py` — 10 tests (fills, scalar settle, caps).

### Behaviour that differs from P-017M (and why)

1. **Post EARLY, rest through the round.** New quotes placed only in the
   `[fade_start_h=24, no_new_quote_h=12]` window; once placed they rest to
   close (that is where Phase-2 fills, net-positive, occurred). P-017M fades
   late (36h→6h) on multi-day top-N; here the determining event is one round
   and the tradeable window is before it.
2. **Scalar settles at the split payout, not a void.** `result="scalar"` on a
   round-leader market IS the $1/n dead-heat payout (read from
   `settlement_value_dollars`, verified present on the per-ticker GET even
   though `settlement_value` is null). The top-N settler correctly voids
   scalar (there it is a withdrawal); doing that here would zero the exact
   outcome the pod trades. This is the flagged Phase-2 fix, covered by
   `test_scalar_settles_at_split_payout_not_void`.

### Collateral caps (mandatory — the tail is a faded name leading outright, −94¢)

Sized on collateral-at-risk, not contract count (Phase-2 report §5). Config
defaults (conservative paper-pilot):

| cap | default | purpose |
|-----|--------:|---------|
| `max_contracts_per_name` | 25 | per-strike; bounds a single-name blowup |
| `max_collateral_per_tournament` | $50 | within-tournament correlation (a hot wave / multi-way tie) |
| `max_total_collateral` | $150 | aggregate across live tournaments |

The engine sizes each quote to the tightest remaining cap and pulls the quote
when any cap is exhausted.

## Open items / next steps

- **Not deployed.** `scripts/deploy.sh` + starting the runner on the VPS is a
  separate step, pending review. Start with `python3
  scripts/run_round_leader_fade.py`.
- **Capacity is small** (~$140 P&L / $3.8k collateral / month at 25-ct caps).
  Treat as a modest paper-CLV contributor; do not raise caps until a live-fill
  sample supports it.
- **Trade history was ~1 month** (19 tournaments). Re-run
  `backtest_fade_fills.py` as more events settle to tighten the CI.
- **`_close_epoch` uses `occurrence_datetime`** (round-granularity); refine
  against the tee-time schedule before any real money — the pre-round window
  gate depends on it.
- **No aggregate-risk-guard / trade-store integration yet.** Like P-017M, the
  engine keeps its own JSONL books; collateral caps are enforced in-process
  and reset on restart. Wire into `AggregateRiskGuard` before scaling.
