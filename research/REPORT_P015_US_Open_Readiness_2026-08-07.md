# P-015 US Open Readiness Audit - 2026-08-07

## Scope

Audit the deployed P-015 paper path before US Open qualifying begins on August
17: market discovery, qualifier filtering, restart-safe caps, settlement,
sanctioned gate reading, and P&L accounting. The locked decision rule in
`tennis_research/P015_DECISION_RULE.md` was not changed.

## Deployed State Before The Fix

- `betting-pod-shop` was active with zero service restarts.
- P-015 continuously scanned 32-48 open ATP/WTA match markets. Current events
  were not qualifiers, so zero candidates was the expected result.
- The slam-only cap window was already configured for August 17-22, with base
  concurrency 6 and qualifying-window concurrency 10.
- Open-position count restores from `TradeStore` after restart, and the
  Kalshi public production API settler is attached whenever P-015 is active.
- The sanctioned reader found 26 priced settlements: 22 wins, 4 losses, 84.6%
  hit rate, 89.7% breakeven, edge -5.12 percentage points, z=-0.72. Verdict:
  **NO DECISION**, because n=26 is below the locked n=120 checkpoint.

## Finding: Gross P&L Was Booked As Net

`KalshiTennisSettler` computed binary payout P&L but did not subtract the taker
fee stored on each placement. This did not affect the gate statistic: the
sanctioned reader independently computes `won - (fill_price + fee)` and uses
P&L only as a descriptive field. It did overstate fund and pod P&L and fed the
gross number into allocator and aggregate-risk settlement callbacks.

The dry-run correction reproduced every legacy row from its stored side,
result, stake, fill price, integer contract count, and taker fee:

| Rows | Files | Gross booked | Fees omitted | Correct net |
|---:|---:|---:|---:|---:|
| 26 | 2 compressed archives | -$18.80 | $3.48 | -$22.28 |

Five rows predated `resolution_source`; 21 carried `kalshi_api`. All 26 matched
the independently recomputed gross P&L exactly. The correction refuses the
entire run if that proof or any fee input is missing.

## Correction

- Future settlement rows record `pnl_usd` net of fees plus
  `pnl_gross_usd`, `fees_usd`, and `fee_accounting_version=p015_net_v2`.
- True VOID rows retain the locked rule's $0/no-risk convention and remain
  excluded from the gate.
- `scripts/apply_p015_fee_correction.py` is dry-run by default, exact-signature
  scoped, idempotent, backup-first, and atomic per file. Non-target lines are
  preserved byte-for-byte.
- The lifetime dashboard does not reread completed archives by design. The
  correction utility therefore writes per-day dollar deltas to the append-only
  `data/corrections/dashboard_pnl_corrections.jsonl` ledger. The rollup applies
  that ledger after restoring its immutable archived counters, changing P&L
  without changing settlement counts and without accumulating on reruns.
- The historical correction changes only P&L fields. Outcome, gate n,
  breakeven, z-score, and verdict remain unchanged.

## Readiness Verdict

P-015 is operationally ready for the August 17-22 qualifying window after the
fee correction is deployed and applied. The live evidence remains negative but
underpowered; the pre-registered rule requires NO DECISION and no parameter or
scope changes before n=120.
