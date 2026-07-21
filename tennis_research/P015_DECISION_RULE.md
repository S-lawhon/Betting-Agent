# P-015 — Pre-Registered Decision Rule

**Locked 2026-07-20, before any paper trade settled. Do not renegotiate.**

This document exists because P-013 lost $2,094 while its criteria were
still being decided after the fact. The rule below is fixed in advance;
`scripts/p015_checkpoint.py` is the only sanctioned way to read results.

---

## The claim being tested

From `REPORT.md` §8b (13-month Kalshi history, 2026 qualifier data):
buying tennis **qualifying-round** favorites at ask ∈ [0.85, 0.985]
returned **+4.1¢/contract** net of taker fees — 95.8% realized hit rate
against a 91.7% breakeven (mean ask 0.907 + 1¢ fee), n=238,
bootstrap CI [+1.4, +6.3].

**Prior:** this was 1 of ~10 slices examined, on 2026-only data, with no
temporal holdout. Regression toward zero is the expected outcome. We
are testing a plausible-but-unconfirmed effect, not deploying a known one.

## Test statistic

Per settled trade (VOIDs excluded — no risk taken):
- `won` ∈ {0,1}
- `breakeven = fill_price + kalshi_taker_fee(fill_price)`

Aggregate: `edge = mean(won) − mean(breakeven)`,
`se = sqrt(hit·(1−hit)/n)`, `z = edge / se`.

## Decision schedule

| Trades (n) | Rule |
|---|---|
| any | **HARD KILL** if z ≤ −2.0 (significantly negative) |
| n < 120 | **NO DECISION** — underpowered, do not act |
| n ≥ 120 | **KILL** if edge ≤ 0; else CONTINUE |
| n ≥ 240 | **PROMOTE** to live only if edge > 0 AND z ≥ 2.0 |

At ~20 trades/month: the 120-trade checkpoint lands ~6 months out
(Jan 2027), the 240-trade checkpoint ~12 months out (Jul 2027).
**US Open qualifying (Aug 17–21, 2026) is the first volume spike.**

## Why these thresholds — power analysis

Computed against the actual 238-trade cohort (breakeven 0.9174,
observed hit 0.9580, edge +4.06pp):

| n | months | z if edge fully real | power at z≥2 |
|---|---|---|---|
| 60 | 3 | 1.57 | — |
| 120 | 6 | 2.22 | 60% |
| 240 | 12 | 3.13 | 79% |
| 480 | 24 | 4.43 | 98% |

- **A 3-month look cannot reach significance even if the edge is entirely
  real.** Any early read is noise. This is why n<120 is NO DECISION.
- If the true edge is **half** the measured value (the realistic
  regression case), power is only **26% at 12 months**. Consequence: a
  half-size edge is effectively unverifiable at this volume, and must
  not be traded live on the strength of a suggestive-but-insignificant
  result. This is why promotion requires z ≥ 2.0, not merely edge > 0.
- With no true edge, a 60-trade window shows a spurious 2σ "success"
  **18.5% of the time** — the precise trap the n≥120 gate closes.

## Anti-rationalization clauses

1. **No mid-flight parameter changes.** Adjusting `ask_floor`,
   `edge_bump`, or tour scope resets n to 0. Tuning on live results is
   the same error as fitting the original slice.
2. **No cherry-picking sub-slices.** The verdict is computed on all
   settled P-015 trades. "It works if you exclude WTA" is not a result;
   it is a new hypothesis requiring its own pre-registered test.
3. **VOIDs excluded, never counted as wins.**
4. **Silence is not confirmation.** If volume never reaches 120 trades,
   the verdict stays NO DECISION and the pod stays in paper. An
   unfalsifiable strategy does not get promoted by default.
5. **No CLV substitute.** Qualifier matches have no sharp closing line,
   so realized calibration is the only validator. Do not import a
   CLV-based argument to promote early.

## If the extended (Challenger/ITF) test changes scope

Adding lower-tour series to P-015 is a **new strategy version**
(P-015b) with its own n counter, not a continuation of this one. Log it
separately and re-register before enabling.
