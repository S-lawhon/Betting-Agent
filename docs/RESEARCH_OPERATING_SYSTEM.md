# Research operating system

The research system is optimized for validated, executable, capacity-adjusted
edge—not idea volume. Social engagement, novelty, publication prestige, and a
market's existence are inputs, never evidence that an edge exists.

## Safety boundary

Research and execution are separate authorities:

1. `config/research_venues.yaml` determines whether a venue/product may be
   researched and whether a **new** proposal may call it executable.
2. Unknown, stale, pending, reference-only, and prohibited decisions are
   non-executable.
3. A new executable approval requires product scope, current evidence, an
   expiration date, and a named human approver.
4. This registry does not change authorization for existing production code.
5. Candidate assignments cannot enter `data/strategy_agents/queue` and cannot
   advance strategy state.

The Texas classifications are operational controls, not legal advice. Review
venue terms, account approval, product eligibility, state and federal changes,
and counsel guidance before granting an execution approval.

## Architecture

```text
Official venue APIs ─┐
CFTC filings ────────┤
Academic RSS ────────┼─> SourceItem ledger ─> ranked assignments
X recent search ─────┤          │                    │
Kalshi census ───────┘          │                    ├─ literature-scout
                                │                    ├─ social-scout
 eligibility registry ──────────┘                    └─ strategy-scout
                                                          │
                                                   research-critic
                                                          │
                                      reject / defer / OpportunityCard
```

Deterministic software owns fetching, hashing, deduplication, provenance,
eligibility, and metrics. Agents are used only where judgment is necessary.

## Sources and cadence

| Source | Normal cadence | Purpose |
|---|---:|---|
| Venue listings | 5–15 min where APIs permit | New products and payoff changes |
| Venue/CFTC rules and products | Daily | Terms, fees, incentives, new DCMs |
| Kalshi census | Daily; full on UTC day 1 | Deltas plus coverage rotation |
| Academic feeds | Daily collection, weekly review | Mechanisms and replication leads |
| X curated queries | Explicit opt-in; twice daily | Practitioner and emerging leads |
| Official event data | Event-specific | Timestamped falsification inputs |

The current scheduled intake runs daily at 04:05 UTC, after the 03:35 UTC
Kalshi census. The collector is intentionally not a low-latency trading feed.

### X cost and data controls

X is disabled in `config/research_sources.yaml`. To enable a bounded pilot:

1. Approve a monthly API budget outside the repository.
2. Set `X_BEARER_TOKEN` in the service secret environment.
3. Start with the three configured queries and inspect source yield.
4. Run `python3 -m scripts.run_research_intake --include-x`.

The collector stores post ID, URL, author, timestamp, metrics, and a short
excerpt. It does not use follower counts or engagement to score edge and does
not archive uncontrolled full timelines. Apparent MNPI, hacked/leaked data,
affiliate promotion, or unverifiable P&L requires rejection or human review.

## Artifact contracts

### `SourceItem`

Defined in `src/research_intake.py`. It records stable identity, content hash,
source type, publication/retrieval timestamps, rights posture, venue/product
scope, and compact metadata.

### `ResearchAssignment`

A ranked instruction to a specialist. It carries source provenance and current
eligibility decisions and always sets `may_enter_strategy_registry: false`.

### `ResearchDisposition`

Defined in `src/research_outcomes.py` and stored under
`research/dispositions/`. Every reviewed assignment becomes:

- `reject`: durable reason codes and evidence checked;
- `defer`: requires `recheck_after`;
- `advance`: requires the resulting `opportunity_id`.

Fast, well-supported rejection is a successful research outcome.

## Runbook

Offline/replay input:

```bash
python3 -m scripts.run_research_intake \
  --offline-items /path/to/source_items.json \
  --output-dir /tmp/research-intake \
  --now 2026-08-01T12:00:00Z
```

Live free-source intake:

```bash
python3 -m scripts.run_market_census --fetch-live --full-on-month-start
python3 -m scripts.run_research_intake
```

Build source/lane funnel metrics:

```bash
python3 -m scripts.build_research_metrics
```

Outputs under `data/research_intake/`:

- `ledger.json`: durable source/content deduplication;
- `source_batches/`: collected metadata batches;
- `assignments/`: lane-balanced research assignments;
- `manifests/`: coverage, errors, deferrals, and eligibility audit;
- `metrics.json`: disposition yield by source and lane.

Partial source failures are recorded in `collector_errors`. Prior state is
written atomically. The scheduled unit fails only when collectors report errors
and produce no usable items.

## Deployment

```bash
sudo install -d -o bettingbot -g bettingbot -m 0750 \
  /opt/betting-pod-shop/data/market_census \
  /opt/betting-pod-shop/data/research_intake
sudo cp scripts/systemd/market-census.{service,timer} /etc/systemd/system/
sudo cp scripts/systemd/research-intake.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now market-census.timer research-intake.timer
systemctl list-timers market-census.timer research-intake.timer
```

The manager registry monitors both manifests for freshness. A fresh intake
manifest proves collection ran; it does not prove any agent researched the
assignments. The existing strategy-agent daemon remains an artifact recorder,
not an LLM runner.

## Research allocation and measurement

Rank assignments by:

```text
P(real edge) × deployable capacity × expected half-life × executability
──────────────────────────────────────────────────────────────────────
                    research time and data cost
```

Apply penalties for prior rejection, unavailable timestamp-correct data,
settlement basis, rule ambiguity, unrealistic fills, fees, inaccessible
latency, crowding, and prohibited information.

Track assignment-to-disposition coverage, advance and rejection rates,
research minutes per advance, rejection reasons, and yield by source/lane.
Validated and paper/live outcomes should eventually be joined back to
`opportunity_id`; advance rate alone is not research quality.

Maintain a 15–20% exploration floor even after source metrics accumulate. A
small early sample must not permanently starve new sources or market families.

## Current limitations

- The CFTC collector parses official filing tables; a site markup change will
  produce zero items and must be treated as a collector anomaly.
- Polymarket US discovery is public and read-only; execution remains pending.
- ForecastEx discovery still depends on eligible IBKR product discovery and is
  not yet implemented by the legacy connector.
- ProphetX remains read-only and requires authenticated response-shape
  reconciliation.
- X is disabled until budget approval and credentials exist.
- No repository daemon invokes the model-driven agents automatically.
