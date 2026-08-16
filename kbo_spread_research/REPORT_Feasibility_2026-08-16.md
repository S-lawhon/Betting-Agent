# KBO Spread historical-data feasibility — 2026-08-16

## Verdict

**PRICE-DATA FEASIBILITY PASSES; EDGE IS UNTESTED.** Do not wait for November.
The missing evidence is research work available now, so the assignment is
classified `needs_work`, not `defer`.

## Prespecified question

Before building a model, determine whether public Kalshi history can recover:

1. a reliable scheduled pregame cutoff;
2. timestamped two-sided prices near that cutoff;
3. trade-print volume as a limited capacity proxy; and
4. enough settled events to justify an external-odds alignment test.

This study does not estimate edge and does not authorize execution.

## Sample and method

`feasibility.py` pulled the latest 20 distinct settled `KXKBOSPREAD` events,
comprising 80 contract legs, from 600 settled markets returned across three
public API pages. Scheduled starts were parsed from each contract's primary
rules. For every leg it requested one-minute candlesticks over the six hours
before the scheduled start and measured the latest genuine two-sided touch
within 30 minutes of T-60 and T-15.

The run is reproduced by:

```bash
python3 -m kbo_spread_research.feasibility \
  --events 20 --max-pages 3 \
  --output kbo_spread_research/feasibility_results.json
```

## Results

| Check | Result |
|---|---:|
| Scheduled start parsed | 80 / 80 |
| Any pregame candlesticks | 80 / 80 |
| Two-sided touch at T-60 | 58 / 80 |
| Two-sided touch at T-15 | 80 / 80 |
| Any pregame prints in prior 6h | 61 / 80 |
| Median T-60 spread | 1 cent |
| T-60 spread p75 | 2 cents |
| Median T-15 spread | 1 cent |
| Median 6h pregame print volume | 251.12 contracts |
| Median lifetime volume | 2,419.05 contracts |

The original packet's zero-volume observation was not representative of the
settled sample. Historical touch and prints are available at useful cadence.

## What remains unknowable from Kalshi history

Candlesticks do not preserve orderbook depth, queue position, cancellations,
or fill priority. Trade volume is therefore only a capacity proxy. A future
paper strategy would still require live forward book capture before any fill
claim.

Kalshi prices and settlement outcomes also cannot establish fair value by
themselves. A separate timestamp-correct comparison signal is required. The
Odds API documents `baseball_kbo` coverage, including historical odds from
2024-03-28 and featured spread markets. That makes a historical sharp-consensus
alignment the next bounded test, subject to the account's historical-data
entitlement and actual bookmaker coverage.

That entitlement/coverage condition was checked after deployment with one
historical EU-region request at 2026-08-16 09:45 UTC. It succeeded and returned
five KBO events, 31 two-sided spread books, and nine bookmakers, including
Pinnacle on three events. The request cost 10 credits with 4,961,820 remaining.
The durable summary is `odds_coverage_results.json`; it contains no credential.

## Next gate

Match KBO games, teams, start times, and alternate spread strikes between
Kalshi and historical bookmaker snapshots. Compare de-vigged consensus against
the executable Kalshi bid/ask at fixed pregame horizons, net of the exact
Kalshi fee and conservative fill/capacity haircuts. Stop without modelling if:

- event or strike matching is materially incomplete;
- historical timestamps cannot prevent look-ahead;
- the provider lacks usable KBO spread bookmakers; or
- the observed discrepancy does not clear friction on an event-clustered
  holdout sample.

Execution eligibility remains `reference_only`.
