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
- Scheduled starts agree within 15 minutes. A wider difference is rejected as
  a schedule mismatch rather than risking a wrong game or doubleheader match.
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
- `data/gemini_crossvenue/research_cases.sqlite3`: lifetime, idempotent research
  case index. Stable case IDs are derived from the venue pair, event,
  direction, and first observation. Raw JSONL remains the source tape.
- `data/gemini_crossvenue/research_cases.json`: dashboard/email summary with
  24-hour and 7-day rankings, active/closed counts, single-snapshot share,
  threshold calibration, and settlement-basis classification.
- `data/gemini_crossvenue/settlement_basis_evidence.json`: daily join of cases
  to public MLB schedule outcomes, including schedule coverage and the observed
  settlement-exception incidence lower bound.
- `data/gemini_crossvenue/settlement_evidence/YYYY-MM-DD.json`: cached schedule
  evidence for reproducible incidence updates. Terminal dates are not refetched.
- `data/settlement_registry/manifest.json`: latest rule-document hashes,
  archive locations, registry health, and evaluated policy state.

The systemd timer runs every five minutes. Venue requests start concurrently;
each snapshot records request latency and midpoint skew. Actual one-contract
taker fees are rounded up to the cent independently at each venue.

The five-minute path captures current quotes, updates the compact 24-hour run
summary, and reuses the last historical analytics/case artifacts. It
deliberately does not replay the full observation tape: by 2026-08-08 the 68 MB
tape expanded beyond the service's 256 MiB memory limit and caused every run to
time out under reclaim pressure. The separate
`gemini-crossvenue-analysis.timer` streams the 14-day tape hourly under its own
256 MiB cgroup; it never calls either venue. Its timestamps are exposed under
`metrics.json.analysis_refresh`, so cached analysis is never presented as
newly recomputed. A manual refresh uses
`python3 -m scripts.refresh_gemini_crossvenue_analysis`.

Measured before deployment on the live 67 MB / 31,833-observation tape, the
streaming replay completed in 1.81 seconds at 52.4 MB peak RSS with zero swap.
`analytics.json` is written last and is the monitored completion heartbeat, so
a partial replay cannot report healthy merely because an early stage ran.

The analytics deliberately report depth as unavailable. Public snapshots do
not expose comparable full order-book depth, so slippage is scenario-tested
rather than represented as an estimate. A qualifying dislocation is at least
three cents net after known fees; it is persistent only when it survives at
least two observations no more than 450 seconds apart.

Each run also emits a venue-neutral `research_signals` list. Collection or
settlement-document degradation and quote completeness below 90% are data
health warnings. A three-cent dislocation becomes a research-opportunity
warning only when a persistent episode was observed within the last 15 minutes;
old episodes cannot keep paging. Every signal carries
`trade_allowed=false`—the alert asks for investigation and never overrides the
settlement or eligibility gates.

Qualifying observations are segmented into research cases. A case closes only
when the tape observes an edge below the threshold or the observation gap
exceeds 450 seconds; duration is always labeled as a lower bound. Replaying the
same tape is idempotent. Rankings are lexicographic and transparent:
schedule alignment, pregame phase, persistence, survival after one cent of
slippage per leg, maximum net edge, and observed duration. Calibration replays
thresholds from one through five cents without changing the live three-cent
definition.

Settlement basis is attached to every case using the versioned policy
dimensions. The ledger does not infer how often a void/postponement rule would
have changed an outcome from quote data alone; historical incidence remains
explicitly `unmeasured` until event-outcome evidence is joined.

`crossvenue-settlement-evidence.timer` performs that outcome join daily. It
recognizes schedule-level postponement, suspension, and cancellation evidence.
Its exception rate is deliberately labeled a lower bound: an ordinary MLB
`Final` status does not prove that a game was not shortened and cannot reveal a
venue's discretionary review. Same-team/date doubleheaders are left ambiguous
instead of guessing which game belongs to a case. API failures retain prior
evidence and mark the refresh degraded.

Each case is also classified as `pre_scheduled_start`,
`after_scheduled_start`, or `unknown`, using the earlier of the two venue start
times. Venue start times more than 15 minutes apart are marked mismatched. The
ranking prefers schedule-aligned pregame cases; after-start divergence remains
valuable research evidence but is more likely to reflect live-state or stale
quote differences than a pregame basis opportunity.

The `venue_pipeline` block reports onboarding state for additional venues
without reading or exposing credentials. It distinguishes collecting, blocked,
and policy-excluded venues and carries the next evidence required to advance
each one. The same contract can be reused when a new venue pair begins
collection.

## Additional venue decisions

- ProphetX remains the next adapter candidate. It is a CFTC-designated contract
  market and the repository contains a read-only client. Sandbox credentials,
  response shapes, quote parsing, and schedule-aligned matching are validated;
  production credentials, production-shape validation, and tax Gate 0 still
  block a production evidence run. Its MLB settlement terms are tracked as
  non-equivalent to Kalshi.
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
