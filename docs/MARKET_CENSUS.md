# Market census and strategy-scout handoff

The market census broadens idea discovery without turning breadth into false
confidence. It is a deterministic inventory/delta process; `strategy-scout`
remains the model-driven researcher that must falsify each promising seed.

```text
Kalshi /series
    -> normalized snapshot + coverage ledger
    -> deterministic novelty/risk ranking
    -> scout inbox (research assignments only)
    -> strategy-scout research
    -> OpportunityCard OR scout_rejection
```

No census output matches the strategy runtime's queue envelope. This is a
deliberate safety boundary: a seed cannot accidentally become a registered
strategy or advance through a gate.

## Cadence

- Daily at 03:35 UTC: scan the whole public series inventory and seed only new
  families, metadata/terms/fee changes, and due `recheck_after` items.
- First UTC day of each month: run a full-universe rotation and prioritize
  series that have never appeared in a scout inbox. The default full-run cap is
  100 seeds versus 25 for a delta run.
- Scout research: work the ranked inbox in focused batches. A practical initial
  operating cadence is Tuesday and Friday, with event-driven work whenever a
  terms, settlement-source, or fee change is detected.

The shortlist caps any one research lane at 40% when alternatives exist. This
prevents a large product category from crowding every other family out of the
research cadence. Lane labels are routing hints, not claims that cross-venue or
latency edge has been observed.

The repository timer automates the first two items. It does **not** invoke an
LLM: the existing strategy-agent daemon is an artifact recorder, not an agent
runner. A Claude session, scheduled-agent facility, or another explicitly
configured model runner must invoke `strategy-scout`; do not describe a running
timer as completed research.

## Run it

Offline/reproducible, using the committed series cache:

```bash
python3 -m scripts.run_market_census \
  --series-input satellites_research/data/all_series.json \
  --output-dir /tmp/market-census-test \
  --now 2026-08-02T12:00:00Z
```

Normal live run:

```bash
python3 -m scripts.run_market_census --fetch-live --full-on-month-start
```

Outputs under `data/market_census/`:

- `snapshot.json`: normalized current universe and reproducible fingerprint;
- `coverage_ledger.json`: first/last seen, seed count, lane, and recheck state;
- `latest_manifest.json` and `manifests/`: run counts and anomalies;
- `scout_inbox/`: immutable ranked seed batches for `strategy-scout`.

The runner writes atomically. An empty or failed live fetch raises before any
prior output is replaced. Source-count collapse, large removal waves, and new
fee regimes appear in `manifest.anomalies` and require review.

## Systemd installation

Copy `scripts/systemd/market-census.service` and `.timer` to the host, then:

```bash
sudo install -d -o bettingbot -g bettingbot -m 0750 \
  /opt/betting-pod-shop/data/market_census
sudo systemctl daemon-reload
sudo systemctl enable --now market-census.timer
systemctl list-timers market-census.timer
```

The unit has network access only because it reads the public Kalshi endpoint;
its only writable project path is `data/market_census`.

## Scout completion contract

For a surviving hypothesis, the scout returns an `OpportunityCard` with
`census_run_id`, `candidate_seed_id`, and `research_lane`. Failed hypotheses
return `type: scout_rejection`, reason codes, evidence checked, and an optional
`recheck_after`. Only a reviewed OpportunityCard may subsequently be wrapped in
the strategy runtime's `opportunity` request by an authorized operator.
