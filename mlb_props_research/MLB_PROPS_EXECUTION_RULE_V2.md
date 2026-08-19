# MLB Props Live Execution Gate V2

Locked 2026-08-18, before collecting or reading any outcome in the successor
sample. This is a new trial; no day or outcome from the terminal V1 sample is
eligible.

## Question

Would marketable YES orders in actively traded `KXMLBHIT` markets, entered at
the displayed ask in the final 30 minutes before first pitch, have produced a
positive fee-net return and at least $10 per completed game-day of displayed
size-capped capacity?

This public-book paper gate measures price and displayed quantity at capture
time. It does not prove that a real order would avoid latency, queue changes,
or partial fills. Passing authorizes only a written implementation and canary
proposal. It does not authorize credentials, orders, or capital.

## Power and frozen sample

Phase 3c measured a 9.13-cent standard deviation across equal-weighted daily
net returns. Detecting a +5-cent daily edge with a two-sided 5% test and 80%
power requires:

`ceil(((1.96 + 0.84) * 0.0913 / 0.05) ^ 2) = 27 game-days`.

- Clean start: 2026-08-19 America/New_York. Earlier snapshots are ineligible.
- Earliest outcome read: 2026-09-15 00:30 America/New_York. September 14 is
  only the 27th possible date and must finish before it can count.
- Use exactly the first 27 chronological admissible completed ET game-days.
  Later days cannot change the decision sample.
- Re-bucket records from timestamps and scheduled starts, never filenames.
  Files suffixed `_mac` are excluded.
- A day is admissible when at least 90% of distinct `KXMLBHIT` markets seen for
  that scheduled day have a final-30-minute observation and at least one market
  passes the entry rule. The current ET date is incomplete and never counts.
- Outcomes remain unread until both the earliest read time and 27 admissible
  days have been reached.

## Entry rule

For every ticker, retain its last market snapshot from 0 through 30 minutes
before scheduled first pitch. Enter once when all conditions hold:

- YES ask is 0.15 through 0.45, inclusive.
- YES ask size is positive.
- A positive YES bid exists below the ask.
- Cumulative market volume is at least 50 contracts.
- Bid/ask spread is no more than $0.06.

Paper size is displayed YES ask size capped at 100 contracts per market. The
taker fee is `0.07 * price * (1 - price)` per contract.

## Settlement rule

Every selected market counts; there is no outcome-dependent exclusion or day
substitution.

- Binary YES pays $1.00 per YES contract.
- Binary NO pays $0.00.
- `result="scalar"` pays the published `settlement_value_dollars` per YES
  contract. If only integer-cent `settlement_value` is present, divide it by
  100. Values must be finite and inside [0, 1].
- A selected market without a final result remains `RESULTS_PENDING`.
- A finalized scalar without a valid settlement value is `DATA_ERROR`. It is
  never guessed, treated as a void, dropped, or replaced.

For market `i`, fee-net return per contract is:

`payout_i - ask_i - 0.07 * ask_i * (1 - ask_i)`.

## Statistic and decision

The inference unit is the ET game-day. First average market returns within
each day, then give each of the 27 days equal weight. The 95% interval is the
mean daily return plus or minus `1.96 * sample_sd / sqrt(27)`.

Displayed capacity is realized paper P&L from the same entries at
`min(displayed_ask_size, 100)` contracts, averaged across the 27 days.

- `BUILD_CANDIDATE` only when the 95% lower bound is above zero and mean daily
  displayed-capacity P&L is at least $10.
- `STOP` otherwise. There is no sample extension, market substitution, or
  threshold renegotiation.
- `DATA_ERROR` and `RESULTS_PENDING` authorize no decision until the exact
  frozen sample becomes scoreable under the rules above.

