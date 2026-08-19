# La Liga Spread Information-Latency Gate

**Strategy family:** `KXLALIGASPREAD`  
**Source assignment:** `assignment_2be00982cfa2eb349b89`  
**Mode:** public-data research only; this rule authorizes no orders  
**Preregistered:** 2026-08-18 America/Chicago, before the forward collector
was started

## 1. Hypothesis

After a public La Liga goal or red card, passive Kalshi spread quotes may remain
stale because attention and liquidity are fragmented. The predicted direction
is fixed before reading prices:

- a goal raises contracts on the scoring team's winning margin and lowers
  contracts on its opponent's winning margin;
- a red card lowers contracts on the carded team's winning margin and raises
  contracts on its opponent's winning margin.

Lineup publication, scheduled kickoff, and final result are captured for audit
and future hypotheses, but they do **not** enter this directional gate. No
lineup model was preregistered, so assigning a direction after seeing prices
would be outcome-driven research.

## 2. Timestamp contract

- Match schedule, rosters and events come from ESPN's public La Liga feed
  (`esp.1`). Goals, cards and kickoff must carry ESPN's UTC `wallclock`.
- Every HTTP request records local UTC request-start and response-receive time.
- Kalshi books are timestamped at response receipt. An event is usable only
  when a book snapshot exists from at most 30 seconds before its ESPN
  `wallclock`, and post snapshots exist between 45–75 seconds after it.
- Events first discovered after their 75-second post window are retained for
  audit but excluded. Historical ESPN events can never be back-joined to later
  books.
- An ESPN event whose `wallclock` precedes the collector's first snapshot for
  that match is excluded. This is forward-only.

## 3. Market and execution measurements

For every open market in the matched Kalshi event, retain the raw public
`orderbook_fp` levels to depth 20 and public trades since the prior cycle.

For a predicted YES increase:

- gross move = post midpoint − pre midpoint;
- taker markout = post midpoint − pre YES ask − the series-aware taker fee;
- stale at 60 seconds = gross move < one tick;
- displayed capacity = contracts at the pre YES ask.

For a predicted YES decrease:

- gross move = pre midpoint − post midpoint;
- taker markout = pre YES bid − post midpoint − the series-aware taker fee;
- stale at 60 seconds = gross move < one tick;
- displayed capacity = contracts at the pre YES bid.

One-sided books have no midpoint and are excluded from the edge statistic, but
remain in coverage and liquidity reporting. Maker fills are reported
separately and conservatively: a resting quote is counted only when a public
trade prints **strictly through** it inside 60 seconds. A touch is not a fill.
Displayed size is not assumed fillable and is never called capacity by itself;
the report must show displayed size, strict-through fills and realized trade
size separately.

## 4. Fixed friction and thresholds

- Tick: read from each market's `price_ranges`; expected current grid is 1¢.
- Taker fee: `src.kalshi_fees.fee_per_contract`, exact series ticker.
- The screening falsification boundary is a **2.25¢ gross predicted move** at
  the representative 49/50 book: 0.5¢ half-spread plus approximately 1.75¢
  taker fee. This is a screening boundary, not an assumed cost for every row;
  row-level executable markout uses the observed book and exact fee.
- `stale_60s` must occur in at least 20% of qualifying shocks, the threshold
  fixed by the originating deep-research disposition.

## 5. Unit of analysis

The primary observation is one information shock within one match. Multiple
markets and multiple shocks inside a match are dependent. Point estimates are
first averaged within match, then equally across matches. Bootstrap confidence
intervals resample matches, never market rows.

## 6. Gate

Do not decide before **20 completed matches and 20 qualifying goal/red-card
shocks across at least 10 matches**.

- **KILL — prompt incorporation:** median gross predicted move is ≤2.25¢, or
  fewer than 20% of qualifying shocks remain stale at 60 seconds.
- **KILL — not executable:** the direction is statistically positive but
  strict-through maker fill rate is below 2%, or median displayed executable
  size on otherwise qualifying rows is below 5 contracts.
- **PASS TO CAPACITY STUDY ONLY:** equal-match mean taker markout is positive
  with a match-clustered 95% CI wholly above zero, `stale_60s` is at least 20%,
  strict-through fill rate is at least 10%, and median displayed executable
  size is at least 25 contracts.
- Otherwise: **NO DECISION**. Continue the fixed forward sample; do not tune
  windows, event types or strikes.

Passing does not authorize a pod, paper orders, or live execution. It permits
only a separately preregistered capacity/fill study.

## 7. Data retention and failure rules

- Append plain JSONL and flush each cycle; never append to a gzip stream.
- Archive completed raw parts under `soccer_research/archive/` before a gate
  decision because public market history rolls off.
- Fail closed on unmatched fixtures, ambiguous team mappings, missing ESPN
  wallclock, malformed books, clock reversal, or fee-series ambiguity.
- Collector downtime, HTTP errors and coverage gaps are explicit rows/status,
  never silently treated as quiet markets.
