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
X recent search ─────┤          │
Kalshi census ───────┘          ├─> ranked assignments ─> bounded triage
                                │                              │
 eligibility registry ──────────┘                              ├─ literature-scout
                                                               ├─ social-scout
                                                               └─ strategy-scout
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
| X curated queries | Once daily during $50 pilot | Practitioner and emerging leads |
| Official event data | Event-specific | Timestamped falsification inputs |

The current scheduled intake runs daily at 04:05 UTC, after the 03:35 UTC
Kalshi census. The collector is intentionally not a low-latency trading feed.

### X cost and data controls

X uses a two-key opt-in in `config/research_sources.yaml` and at runtime. The
approved pilot runs once daily:

1. Keep the X-side monthly hard limit at $50 with auto-recharge disabled.
2. Set `X_BEARER_TOKEN` in the root `.env` for local runs, or in the VPS-only
   `/opt/betting-pod-shop/.env.x`; never commit the token.
3. Keep `collectors.x.enabled: true` and pass `--include-x`.
4. Start with the three configured queries and inspect source yield.

The application allows one run per UTC day, warns at a conservative estimated
$40, and stops before its next worst-case run could exceed $45. X's billing
console remains authoritative. `data/research_intake/x_usage.json` is a local
guard ledger based on returned posts and authors; it can overestimate billing
because it deliberately does not assume X resource deduplication.

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

### `DispatchPacket`

Defined in `src/research_triage.py` and written under
`data/research_triage/dispatches/<agent>/`. It carries the assignment plus the
compact original source, attention-allocation scorecard, explicit unknowns,
research-minute budget, similarity warnings, and specialist handoff chain.
Triage preserves at least one candidate per available lane before score-based
filling, caps lane concentration, and limits all retries combined to 10 packets
and 300 allocated research minutes per UTC day. It never treats its priors as edge evidence.
It creates tasks only: no agent is invoked and no strategy state changes.

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

Live intake including the bounded X pilot:

```bash
python3 -m scripts.run_research_intake --include-x
```

Allocate the newest unreviewed assignments to specialist queues:

```bash
python3 -m scripts.run_research_triage
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
- `x_usage.json`: daily returned-resource counts and conservative cost estimate.

Outputs under `data/research_triage/`:

- `ledger.json`: assignments already dispatched and compact title history;
- `dispatches/<agent>/`: durable specialist task packets;
- `dispatch_archive/<agent>/`: packets with a disposition or registered opportunity;
- `latest_manifest.json`: selection, deferral, diversity, and safety telemetry.

Partial source failures are recorded in `collector_errors`. Prior state is
written atomically. The scheduled unit fails only when collectors report errors
and produce no usable items.

## Deployment

```bash
sudo install -d -o bettingbot -g bettingbot -m 0750 \
  /opt/betting-pod-shop/data/market_census \
  /opt/betting-pod-shop/data/research_intake \
  /opt/betting-pod-shop/data/research_triage
sudo cp scripts/systemd/market-census.{service,timer} /etc/systemd/system/
sudo cp scripts/systemd/research-intake.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now market-census.timer research-intake.timer
systemctl list-timers market-census.timer research-intake.timer
```

`research-intake.service` imports `/opt/betting-pod-shop/.env`, then the
optional X-only `/opt/betting-pod-shop/.env.x`, and supplies the explicit
`--include-x` flag. The X-only file is preferred for this pilot so deploying the
Bearer Token does not copy unrelated credentials. Lock it to mode `0600` and
owner `bettingbot:bettingbot`. The normal code deploy excludes secret files.

The manager registry monitors census, intake, and triage manifests for
freshness. A fresh triage manifest proves tasks were allocated; only a durable
`ResearchDisposition` proves review. The existing strategy-agent daemon remains
an artifact recorder, not an LLM runner.

## Research allocation and measurement

Rank assignments by:

```text
P(real edge) × deployable capacity × expected half-life × executability
──────────────────────────────────────────────────────────────────────
                    research time and data cost
```

The deterministic runner can measure provenance, eligibility state, title
similarity, source/lane priors, and attention decay. It cannot know real edge or
capacity, so those fields remain null until specialist evidence exists.

Apply penalties for prior rejection, unavailable timestamp-correct data,
settlement basis, rule ambiguity, unrealistic fills, fees, inaccessible
latency, crowding, and prohibited information.

Track assignment-to-disposition coverage, advance and rejection rates,
research minutes per advance, rejection reasons, and yield by source/lane.
Validated and paper/live outcomes should eventually be joined back to
`opportunity_id`; advance rate alone is not research quality.

`data/research_intake/metrics.json` also carries the Research Operations
contract used by both the dashboard and daily email: 24-hour dispatch/review
activity, per-agent pending and overdue queues, oldest task age, and explicit
execution semantics. A dispatch is task creation only. Agent invocation/start
is untracked until a dedicated claim event exists, while a newer durable
disposition is the completion signal.

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
- The bounded X pilot is enabled with application and X-side budget controls;
  the runtime credential remains VPS-only and uncommitted.
- No repository daemon invokes the model-driven agents automatically.
