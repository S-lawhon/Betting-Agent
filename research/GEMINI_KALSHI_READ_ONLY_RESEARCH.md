# Gemini/Kalshi read-only research collector

## Scope

The collector records synchronized public quotes for MLB moneyline events on
Gemini and Kalshi. It is a research instrument only: it accepts no credentials,
imports no authenticated client, and cannot place or cancel orders.

Events are matched only when all of the following are true:

- Gemini identifies the event as a baseball moneyline.
- Kalshi identifies it as a `KXMLBGAME` event.
- Both venues resolve to exactly two recognized MLB teams.
- The normalized team set and Eastern calendar date are identical.
- The event key is unique on both venues.

Anything ambiguous is rejected rather than fuzzy matched.

## Settlement-equivalence gate

Matching the same game does not prove that two contracts settle identically.
Gemini keeps a postponed/rescheduled baseball market open until the game is
played or the season ends, subject to its Market Outcome Review process.
Kalshi's current rules use a shorter rescheduling window and may resolve at a
fair price. That difference creates basis risk.

Accordingly, the policy is `terms_equivalence=unverified` and
`actionable_allowed=false`. The collector may report a fee-adjusted
`hypothetical_positive` path, but it must report zero actionable paths until a
reviewed, versioned contract-equivalence policy explicitly approves the pair.

## Outputs

- `data/gemini_crossvenue/latest.json`: latest normalized snapshot and paths.
- `data/gemini_crossvenue/observations/YYYY-MM-DD.jsonl`: durable matched-event
  history for replay and analysis.
- `data/gemini_crossvenue/runs/YYYY-MM-DD.jsonl`: one health/cadence row for
  every collection, including runs with no matched events.
- `data/gemini_crossvenue/metrics.json`: latest and rolling 24-hour health and
  opportunity counts consumed by the dashboard and daily brief.

The systemd timer runs every five minutes. Venue requests start concurrently;
each snapshot records request latency and midpoint skew. Actual one-contract
taker fees are rounded up to the cent independently at each venue.

## Promotion requirements

Before any future execution work, require legal/account eligibility, a reviewed
settlement-rule matrix with versioned source documents, authenticated-client
security review, liquidity/partial-fill modeling, capital/risk limits, and a
separate explicit implementation approval. None of those capabilities belongs
in this collector.
