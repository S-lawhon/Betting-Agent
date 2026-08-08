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
first pilot is paused after producing 17 assignments with zero reviews and zero
advances. A second pilot requires an explicit allocation decision and review
capacity for the existing social-scout queue:

1. Keep the X-side monthly hard limit at $50 with auto-recharge disabled.
2. Set `X_BEARER_TOKEN` in the root `.env` for local runs, or in the VPS-only
   `/opt/betting-pod-shop/.env.x`; never commit the token.
3. Set `collectors.x.enabled: true` and pass `--include-x` only for an approved window.
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

The operational configuration adds queue backpressure on top of those library
ceilings. New dispatch creation stops at 10 pending packets, at four pending
packets for any one specialist, and at one new packet per scheduled run. The
daily production allocation is one packet / 45 research minutes, matching the
most recently authorized execution capacity instead of manufacturing a
ten-to-one backlog. Raising these limits is an allocation decision, not a
harmless cadence change.

Before scoring, triage rejects raw event inventory that names no mechanism and
organization/product listings that contain no mechanism-bearing description.
It also keeps only the highest-ranked representative of a research family.
Prior dispositions contribute one deliberately small, five-observation-shrunk
score component: advances score positively, supported falsifications remain
productive, and rejections caused by missing mechanism/data/executability count
against the source lane. This feedback cannot turn an unknown edge or capacity
field into evidence.

### `ResearchClaim`

Defined in `src/research_execution.py`. A human session or explicitly
configured model runner must atomically claim a dispatch before starting work.
Claims carry the worker identity, specialist role, packet and prompt paths, and
a bounded lease. Expired claims are recoverable; released and completed attempts
remain archived. A claim proves work was reserved and started, but does not
prove that a model was invoked or that research was completed.

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

Claim one bounded task for a human or configured runner:

```bash
python3 -m scripts.run_research_execution claim \
  --worker-id manual-session-20260803 \
  --agent strategy-scout
```

The returned claim names both the dispatch packet and the specialist prompt.
Finish by writing a valid `ResearchDisposition` JSON and completing the claim:

```bash
python3 -m scripts.run_research_execution complete \
  --claim data/research_execution/claims/strategy-scout/ASSIGNMENT.json \
  --disposition /path/to/disposition.json
```

If work cannot continue, use `release --claim ... --reason ...`; do not delete
claim files. `status` reports active, completed, released, and expired claims.

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

Outputs under `data/research_execution/`:

- `claims/<agent>/`: active leased work;
- `claim_archive/<agent>/`: completed claims;
- `claim_released/<agent>/`: explicitly returned work;
- `claim_expired/<agent>/`: stale attempts recovered for retry;
- `events.jsonl`: append-only start, completion, release, and expiry events.
- `screenings/<agent>/`: durable, non-promoting first-stage decisions;
- `runs/<UTC date>/`: aggregate usage plus one row per named invocation phase.

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
optional X-only `/opt/betting-pod-shop/.env.x`, but does not supply
`--include-x` while the source is paused. A later X window requires an explicit
unit override or manual invocation in addition to `collectors.x.enabled: true`.
The X-only file is preferred so deploying the Bearer Token does not copy
unrelated credentials. Lock it to mode `0600` and owner
`bettingbot:bettingbot`. The normal code deploy excludes secret files.

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
activity, per-agent pending, in-progress, and overdue queues, oldest task age,
and explicit execution semantics. A dispatch is task creation only. A claim is
the dedicated start event. Each provider call is recorded on the claim as a
named invocation phase, while a newer durable disposition is the completion
signal.

Maintain a 15–20% exploration floor even after source metrics accumulate. A
small early sample must not permanently starve new sources or market families.

## Autonomous research worker

Phase 1 turns the deterministic dispatch queue into a provider-neutral worker
contract without enabling model spend. `scripts.run_research_agent_worker`
selects the highest-priority available packet, validates its agent prompt and
allocated research minutes, reserves against hard token/cost/time ceilings, and
emits a secret-free plan summary. Dry-run mode does not create a claim, invoke a
model, or write a disposition.

The execution path exists for integration testing but is triply gated: runtime
configuration must be enabled, configuration mode must be `execute`, and the
caller must pass `--execute`. A provider receives JSON over stdin without a
shell and inherits only explicitly allow-listed environment variables. Output
must include measured usage, a valid `ResearchDisposition`, and an artifact
allowed for the assigned role. Budget violations and invalid artifacts release
the claim and are recorded as failed attempts; they never count as completed
research.

`research-agent-worker.service` remains network-denied in Phase 1. Do not add a
provider key or network egress to that unit. Phase 2 requires a separate review
of provider selection, model budget, filesystem permissions, and tool access.

### Codex Pro one-assignment pilot

Phase 2 adds a separate, manual-only `research-agent-codex-pilot.service`; it
does not change or replace the hourly dry-run timer. The pilot uses saved
ChatGPT authentication in `/var/lib/research-codex`, an empty Codex workspace,
an ephemeral `codex exec` session, live public web search, a read-only Codex
sandbox, and a strict JSON output schema. It permits only `strategy-scout` and
one model attempt per UTC day. Subscription usage is measured in tokens and
reported as zero incremental API dollars; zero dollars must not be interpreted
as zero ChatGPT-plan usage.

The unit cannot start unless both `auth.json` and an operator-created
`pilot-enabled` marker exist. It has no timer. The service masks the repository,
environment backups, Kalshi private key, and legacy tree from its mount
namespace. The adapter rejects any run in which Codex attempts a local command;
only public web-search activity is admitted. Keep the Phase 1 service installed
as the default until a human reviews the first durable artifact and disposition.

Install and inspect the pilot from the DigitalOcean console:

```bash
cd /opt/betting-pod-shop
sudo bash scripts/setup_codex_research_pilot.sh install
```

Provision `/var/lib/research-codex/auth.json` with a trusted device login; never
copy it into the repository or print it in a terminal transcript:

```bash
sudo -u bettingbot env HOME=/var/lib/research-codex \
  CODEX_HOME=/var/lib/research-codex \
  /usr/local/bin/codex login --device-auth
sudo bash scripts/setup_codex_research_pilot.sh check
```

The check reports only the auth file's owner and mode. Once authentication
exists, run exactly one assignment:

```bash
sudo bash scripts/setup_codex_research_pilot.sh run-once
```

`run-once` creates the enable marker immediately before starting the unit and
removes it after either success or failure. A successful unit exit is not the
acceptance gate: inspect the archived claim, the new `research/dispositions/`
artifact, and the journal's measured token usage. Keep the pilot manual-only if
the output is missing, malformed, unsupported by public evidence, attempts a
local command, exposes a secret, or advances strategy state.

After any provider window, pull the schema-valid disposition JSON files from
the VPS and commit them. `scripts/check_research_committed.sh` treats
`research/dispositions/*.json` as protected work product; a summary report is
not a substitute for the per-assignment completion records.

### Staged screening and calibration

The next pilot uses `config/research_agent_runtime_screened_pilot.yaml`. It is
still manual-only and strategy-scout-only. A schema-bound screening call is
limited to 25,000 input tokens, 1,000 output tokens, and three decisions:
`reject`, `defer`, or `deep_research`. Screening has no `advance` value and its
durable record explicitly carries `authorizes_advancement: false`.

A reject or defer completes the assignment after one invocation. Only
`deep_research` may make the full research call. The claim ledger records the
phases independently, run usage is their measured sum, and the worker reserves
two attempts and both worst-case cost ceilings before claiming a screened
assignment. The full stage remains responsible for any OpportunityCard and
cannot bypass the existing disposition/artifact validation.

Before another queue pilot, emit the blinded three-case suite:

```bash
python3 scripts/run_research_calibration.py > /tmp/research-calibration.json
```

The emitted cases omit expected labels. Completed results use an
`observations` array and are graded with:

```bash
python3 scripts/run_research_calibration.py \
  --results /path/to/calibration-observations.json
```

The cases are: a real settlement mechanism that should survive screening
(P-022), a directly falsified defense that should be rejected cheaply (P-016),
and a new unresolved maker-incentive filing that must either name a decisive
test or a dated evidence dependency. Acceptance requires all three cases,
25,000 or fewer screening input tokens per case, no deep call after reject or
defer, the known-answer labels, schema-valid durable dispositions, and zero
screening-stage advancement. The unresolved case is judged on evidence
discipline, not on whether it predicts an edge.

The original 12,000-token screen ceiling was rejected by calibration rather
than silently loosened: with web search disabled and reasoning set to low, all
three real provider calls converged at 20,589–20,638 input tokens. The 25,000
ceiling gives roughly 21% operational headroom while remaining materially below
the first pilot's 36,868–202,807-token full-research calls. Deferred screens
must supply an ISO-8601 UTC `recheck_after`; a prose condition is not a dated
feedback loop.

The queue controls were deployed on 2026-08-08. Their first live cycle saw 580
assignments, dispatched zero while the queue was saturated, and quarantined 19
weak legacy packets, leaving 39 pending. No model was invoked. The screened
worker remains manual-only; a new execution window requires the passing
calibration report, a freshly generated mechanism-bearing candidate, and an
explicit human allocation decision.

## Current limitations

- The CFTC collector parses official filing tables; a site markup change will
  produce zero items and must be treated as a collector anomaly.
- Polymarket US discovery is public and read-only; execution remains pending.
- ForecastEx discovery still depends on eligible IBKR product discovery and is
  not yet implemented by the legacy connector.
- ProphetX remains read-only. Sandbox authentication and response shapes are
  reconciled; production credentials, production-shape validation, and tax
  Gate 0 remain blockers to a production evidence run.
- The bounded X pilot is paused after its first measured window; application
  and X-side budget controls remain in place and the runtime credential remains
  VPS-only and uncommitted.
- No repository daemon invokes the model-driven agents automatically. The
  claim/completion lifecycle is ready, but live model execution remains
  fail-closed until a headless runtime and explicit API budget are provisioned.

### ProphetX production-readiness gate

Run `python3 scripts/validate_prophetx_readiness.py --env sandbox` for a
read-only sandbox check. The command writes a secret-free report to
`data/prophetx_readiness/sandbox.json`; it never writes basis observations,
installs a service, or enables execution. The report requires authentication,
non-empty events, an exact main-game moneyline shape, uniquely schedule-aligned
matches, at least 90% executable cross-venue quote coverage among pre-start or
unknown-phase rows, and correct environment labels. Post-start rows are
reported but excluded from that denominator because closed stake is expected.
The report also breaks executable coverage out by sport so an exploratory,
illiquid market cannot be mistaken for a parser or authentication failure.
Team sports use a 15-minute start tolerance. Tennis uses exact participant
pairs plus a six-hour tolerance because Kalshi publishes a coarse occurrence
time while ProphetX publishes a match estimate; multi-day discrepancies remain
rejected. The applied tolerance and observed skew are retained on every row.

Production validation requires an explicit acknowledgement:

```bash
python3 scripts/validate_prophetx_readiness.py \
  --env production --ack-production-read-only
```

Technical readiness is deliberately separate from rollout readiness. A
production validation can pass technically while rollout stays blocked by tax
Gate 0 or missing collection approval. The validator cannot enable the systemd
collector and its report always declares execution disabled. Readiness status
is included in the venue pipeline consumed by the dashboard and daily brief.
