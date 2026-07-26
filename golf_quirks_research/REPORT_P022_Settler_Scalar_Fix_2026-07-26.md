# P-022 — Settler Scalar Fix & Pre-Registered Gate

**Date:** 2026-07-26 · **Prompt:** `research/prompts/PROMPT_P022_Settler_Scalar_Fix_And_Gate.md` (Task 3)
**Scope:** code fix + gate registration only. **No pod built** — that remains unapproved.

## VERDICT: **FIXED**

`result="scalar"` is **not** a withdrawal void — it is a **partial payout** at
`settlement_value_dollars`. The old settler zeroed the P&L on **532 of 24,195** settled golf
markets (2.2%), including **100% of the dead-heat splits P-022 exists to harvest** and
**102 markets (3.2%) inside P-017's own live universe**. No pod built, no config touched,
nothing deployed, nothing traded.

## Scalar semantics verified against real Kalshi data

Cached census (`golf_quirks_research/data/settled_meta.jsonl`, 24,195 markets):

| `result` | n | distinct payouts | zero-valued | range |
|---|---:|---:|---:|---|
| `no` | 21,275 | 1 | 21,275 | 0.0000 |
| `yes` | 2,388 | 1 | 0 | 1.0000 |
| **`scalar`** | **532** | **61** | **0** | **0.01 – 0.71** |

**Not one scalar market settled at zero.** Confirmed live on the per-ticker GET (2 API
calls): `KXPGAR1LEAD-COPC26-TCLE` → `0.5000`, `KXPGATOP10-COPC26-TMOO` → `0.1600`.

**Two regimes produce it, and both settle the same way:**

1. **Dead-heat $1/n split** (`KX*LEAD`). **21 of 21 scalar LEAD events match 1/n exactly** —
   2-way 0.50, 3-way 0.33, 4-way 0.25, 5-way 0.20, 6-way 0.16. P-022's contract term, now
   confirmed empirically rather than only from the PDF.
2. **Withdrawal cancelled at FAIR VALUE** (top-N / make-cut) — *not* a refund. COPC26's 8
   names settle **monotone TOP5 ≤ TOP10 ≤ TOP20 ≤ MAKECUT, 8/8** (e.g. TMOO
   0.11/0.16/0.36/0.51). That is a probability surface: not $1/n (1/8 = 0.125 fits no
   make-cut value), and not the fill price back.

So the old code comment was half-right about the cause and wholly wrong about the
consequence.

## The fix — `src/kalshi_golf_settler.py`

- `scalar_settlement_value()` reads `settlement_value_dollars`, falls back to
  `settlement_value` (cents), and returns `None` rather than defaulting.
- P&L = `contracts × (settlement_value − fill_price)` on YES,
  `contracts × (fill_price − settlement_value)` on NO; contracts derived off collateral at
  the entry price so **scalar@$1.00 ≡ WIN and scalar@$0.00 ≡ LOSS** (tested invariant).
- **Scalar never yields `VOID`** — WIN/LOSS on the sign of realised P&L. New fields
  `settlement_kind` (`scalar_partial` / `void` / `binary`) and `settlement_value` keep the
  two apart permanently.
- Scalar with no settlement value → left **OPEN** with `logger.error`; `_calc_outcome_pnl`
  raises `ScalarSettlementValueMissing`. Never defaulted.
- `kalshi_withdrawn` retired for `kalshi_scalar`; genuine voids are now reachable only via
  `stale_timeout`.

**Design constraint worth recording:** `WIN`/`LOSS`/`VOID` is a closed vocabulary
hard-coded in `trade_store`, `engine`, `aggregate_risk` and `capital_allocator`. A fourth
`outcome` value would leave positions un-closed and leak exposure — the same failure mode as
the July-25 halt. Hence the discriminator is a **new field, not a new outcome**.

**Tests:** `tests/test_golf_settler.py` 26 → 39. Covers scalar YES-side, scalar NO-side (the
−42¢ fade tail), 2-way and 5-way splits, withdrawal-at-fair-value, missing-value-must-not-void
(and must not decay across cycles), the bounds invariant on both sides, sign-never-void
parametrisation, void-vs-partial distinguishability, genuine void still refunding stake but
keeping the fee, and the unknown-result warning path still firing on novel vocabulary.

```
python3 -m pytest tests/test_golf_settler.py -q  →  39 passed
python3 -m pytest tests/ -q                      →  1420 passed, 1 warning in 26.4s
```

## Back-correction

`scripts/backfill_golf_scalar_corrections.py` finds rows carrying the buggy signature
(`resolution_source="kalshi_withdrawn"`, no `settlement_kind`), re-queries Kalshi
(disk-cached), recomputes, and writes to a **new** file; it refuses to write over a trade
log. Verified end-to-end on a synthetic log seeded with the two real scalar markets: 2/2
corrected, delta **+$26.48**, 2 VOID → 1 WIN / 1 LOSS.

**It has NOT been run against live history** — that needs droplet access and is Sam's call.
Magnitude was measured instead from P-017's market universe, reconstructing entry price from
the candle cache (mean `yes_ask` over its 4–10 d window, `[0.08, 0.45]` band):

| | |
|---|---:|
| settled KXPGATOP10 + KXPGATOP20 | 3,206 |
| `result="scalar"` | **102 (3.18%)** |
| priced in band | 20 |
| **mean booking error** | **−4.24 ¢/contract** |
| median / range | −3.00¢ / −36.0¢ to +2.2¢ |
| sign | **15 neg / 3 flat / 2 pos** |

**The error is one-sided and it flatters P-017.** A withdrawal is marked to fair value at
cancellation, usually well below the pre-tournament entry price; booking $0 credited a
refund that was never received. Worst case: `KXPGATOP10-THOC26-TWOO`, entry 0.390 → settled
0.030 = −36¢.

Two distortions, and **the second is worse**:

1. **P&L:** 3.18% × −4.24¢ ≈ **−0.13¢/ct** against a +6.8¢/ct baseline — ~2% of the edge.
   Small.
2. **Sample:** all 102 were booked `VOID`, and the gate excludes VOIDs — so **~3.2% of
   settled markets were silently dropped from the denominator, and the dropped rows are
   systematically the losers.** On a gate needing 8 tournaments with 1 in flight, that
   matters far more than the 0.13¢.

**Why this gated P-022 specifically:** in the LEAD universe, 61 scalars with a **$0.342 mean
split payout** were all booked at **$0.000**. A fade sold at 5¢ and hit by a 2-way tie owes
50¢ and would have been recorded as costing nothing — forward P&L would have been premium
collected with the entire loss tail deleted, **strictly positive regardless of whether the
edge was real. The gate would have passed a money-losing strategy.**

## Pre-registered gate — `golf_quirks_research/P022_DECISION_RULE.md`, LOCKED

- **Unit = TOURNAMENT** (contract-weighted within, equal-weighted across; rounds of one
  event pool into a single observation). The anti-P-017M rule.
- **T = 14, derived not guessed.** From CI [+1.7, +5.1] at T=19: SE(19) = 0.867¢,
  σ = 3.781¢. `T = ((2.0 + z_power)·σ/d)²` = **13.3** for 90% power against the measured
  +3.4¢ → 14. A T=8 look has 71% power vs the full effect and 23% vs half, and is explicitly
  forbidden. **T = 40** = 39.9 for 80% power against half the effect.
- **Marginal rule registered in advance:** positive but z < 2.0 at T=14 buys **one**
  extension to T=40 at byte-identical parameters; z < 2.0 at T=40 is a KILL. No second
  extension.
- **Hard kill at any T** if z ≤ −2.0.
- **Caps are gate conditions, not just risk limits:** per-name ≤0.5%, per-tournament ≤5%,
  aggregate ≤15%, quote only at H ≈ 12–24 h pre-round. A tournament with any cap breached is
  **excluded from T** — it cannot contribute a favourable observation. Raising a cap resets
  T to 0 under a new pod ID.
- Sample caveat recorded up front (~1 mo history, no temporal holdout; **if forward
  disagrees with the backtest, the backtest loses**).
- Sanctioned reader `scripts/p022_checkpoint.py` — runs today, prints
  `NO DECISION — no settled P-022 trades yet`.

## Open defect found while writing the gate

`python3 -m scripts.p022_checkpoint --check-fees` reports **DRIFTED**. `_SERIES_MAKER_FEE`
(`src/kalshi_fees.py:54-56`) lists only `KXPGAR1LEAD`, `KXPGAR2LEAD`, `KXPGAR3LEAD`;
`KXDPWORLDTOUR*LEAD`, `KXLIV*LEAD`, `KXLPGA*LEAD` and `KXCHAMPTOURR1LEAD` fall through to
the charging default. Verified against `GET /series/?category=Sports&limit=200`: **all 63
`*LEAD` series are `fee_type=quadratic`** (as is the whole `KXLEADER*` family — relevant to
P-026). That is a phantom 0.129¢/ct at P=0.08 on the non-PGA tours, which are the majority of
Phase 2's tournaments, biasing the forward estimate **downward** against a fee-free-calibrated
rule. **Fix before T starts counting.** Left unfixed here to avoid colliding with the
concurrent Task 2 agent. This is the third time this table has drifted.

## Needs Sam

1. **Deploy is owed but was NOT run.** The fix only takes effect on the VPS after a deploy;
   until then P-017 keeps booking scalars as voids.
2. **Run the back-correction against live history:**
   `python3 -m scripts.backfill_golf_scalar_corrections --pod P-017 --log 'data/trade_logs/*.jsonl' --log 'data/trade_logs/archive/*.jsonl'`
   It writes a new file and touches nothing. Its printed **P&L DELTA** is the real number;
   the −4.24¢/ct above is an estimate from market data, not from P-017's actual fills.
3. **Fix the maker-fee table** before P-022's counter starts.
4. **The P-022 pod build remains unapproved** and was not started.
