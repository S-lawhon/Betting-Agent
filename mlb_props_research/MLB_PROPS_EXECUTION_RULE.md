# MLB Props Live Execution Gate

Locked 2026-08-07, before reading the accumulated forward outcomes.

## Question

Would marketable YES orders in actively traded `KXMLBHIT` markets, entered at
the displayed ask in the final 30 minutes before first pitch, have produced a
positive fee-net return and at least $10 per completed game-day of displayed
size-capped capacity?

This is a public-book paper execution gate. It measures the price and quantity
displayed at capture time; it does not prove that a real order would avoid
latency, queue changes, or partial fills. Passing authorizes only a written
implementation and canary proposal. It does not authorize credentials, orders,
or capital.

## Frozen sample

The only sanctioned reader is
`mlb_props_research/execution_checkpoint.py`.

- Start date: 2026-07-22 America/New_York. Earlier manual and truncated runs are
  inadmissible.
- Read no earlier than 2026-08-18 00:30 America/New_York. August 17 is only the
  27th possible clean date after July 22 and must finish before it can count.
- Use exactly the first 27 chronological admissible completed ET game-days.
  Later days cannot change the decision sample.
- Re-bucket records from their timestamps and scheduled starts, never from the
  filename. Files suffixed `_mac` are excluded.
- A day is admissible when at least 90% of the distinct `KXMLBHIT` markets seen
  for that scheduled day have an observation in the final 30 minutes and at
  least one market passes the entry rule. The current ET date is incomplete and
  never counts.

## Entry rule

For every ticker, retain its last market snapshot from 0 through 30 minutes
before the scheduled first pitch. Enter once when all conditions hold:

- YES ask is 0.15 through 0.45, inclusive.
- YES ask size is positive.
- A positive YES bid exists below the ask.
- Cumulative market volume is at least 50 contracts.
- Bid/ask spread is no more than $0.06.

Paper size is the displayed YES ask size capped at 100 contracts per market.
The taker fee is `0.07 * price * (1 - price)` per contract, matching the Phase
3 report and the project's marginal fee model. A missing or scalar settlement
is not guessed and makes the result unavailable until every selected market has
a binary result.

## Statistic and decision

For market `i`, fee-net return per contract is:

`result_i - ask_i - 0.07 * ask_i * (1 - ask_i)`

The inference unit is the ET game-day. First average market returns within each
day, then give each of the 27 days equal weight. The 95% interval is the mean of
those daily means plus or minus `1.96 * sample_sd / sqrt(27)`.

Displayed capacity is the realized paper P&L from the same entries at
`min(displayed_ask_size, 100)` contracts, averaged across the 27 days.

- `BUILD_CANDIDATE` only when the 95% lower bound is above zero and mean daily
  displayed-capacity P&L is at least $10.
- `STOP` otherwise. There is no sample extension or threshold renegotiation.
- Before the read time or 27 admissible days, the verdict is `NO DECISION` and
  the reader must not load or fetch outcomes.

