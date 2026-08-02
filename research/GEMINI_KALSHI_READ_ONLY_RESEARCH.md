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

Accordingly, the reviewed policy is `terms_equivalence=not_equivalent` and
`actionable_allowed=false`. The collector may report a fee-adjusted
`hypothetical_positive` path, but it must report zero actionable paths until a
reviewed, versioned contract-equivalence policy explicitly approves the pair.

`config/settlement_equivalence.yaml` pins each authoritative document by URL
and SHA-256 and records the comparison dimension by dimension. The daily
`settlement-registry.timer` downloads and archives the documents. A missing
document changes the policy to `unverified`; a hash mismatch changes it to
`invalidated_document_change`. Both states fail closed. A `verified` policy is
invalid unless every dimension is equivalent and an approver and approval date
are recorded.

## Outputs

- `data/gemini_crossvenue/latest.json`: latest normalized snapshot and paths.
- `data/gemini_crossvenue/observations/YYYY-MM-DD.jsonl`: durable matched-event
  history for replay and analysis.
- `data/gemini_crossvenue/runs/YYYY-MM-DD.jsonl`: one health/cadence row for
  every collection, including runs with no matched events.
- `data/gemini_crossvenue/metrics.json`: latest and rolling 24-hour health and
  opportunity counts consumed by the dashboard and daily brief.
- `data/gemini_crossvenue/analytics.json`: replayed 14-day quote completeness,
  edge distributions, persistence episodes, quote skew, and per-leg slippage
  scenarios.
- `data/settlement_registry/manifest.json`: latest rule-document hashes,
  archive locations, registry health, and evaluated policy state.

The systemd timer runs every five minutes. Venue requests start concurrently;
each snapshot records request latency and midpoint skew. Actual one-contract
taker fees are rounded up to the cent independently at each venue.

The analytics deliberately report depth as unavailable. Public snapshots do
not expose comparable full order-book depth, so slippage is scenario-tested
rather than represented as an estimate. A qualifying dislocation is at least
three cents net after known fees; it is persistent only when it survives at
least two observations no more than 450 seconds apart.

## Additional venue decisions

- ProphetX remains the next adapter candidate. It is a CFTC-designated contract
  market and the repository contains a read-only client, but collection is
  blocked until API credentials and live response shapes are verified. Its MLB
  settlement terms are tracked as non-equivalent to Kalshi.
- Novig is research-only. Texas eligibility is documented, but its sweepstakes
  rules prohibit automated or systematic participation; no automated adapter
  should be built without written API/institutional authorization and renewed
  legal review.
- Underdog Predict is research-only. Its contracts can be available in Texas,
  but the account may be powered by an underlying exchange such as Kalshi or
  Crypto.com/Nadex, so it is not reliably an independent price venue. Its terms
  also prohibit unapproved automated access.

## Promotion requirements

Before any future execution work, require legal/account eligibility, a reviewed
settlement-rule matrix with versioned source documents, authenticated-client
security review, liquidity/partial-fill modeling, capital/risk limits, and a
separate explicit implementation approval. None of those capabilities belongs
in this collector.
