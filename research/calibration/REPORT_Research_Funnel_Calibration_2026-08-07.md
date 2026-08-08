# Research funnel calibration — 2026-08-07

## Verdict

**PASS: 3 of 3 cases.** The screened research path preserved a real structural
mechanism, cheaply rejected an already-falsified mechanism, and deferred an
unresolved lead with a dated evidence dependency. Screening never advanced a
strategy, and deep research ran only for the surviving case.

This result supports one new manual, screened queue pilot. It does not support
automatic scheduling, strategy-registry mutation, live execution, or any
trading-state change.

## Cases

| Case | Screen | Full result | Calls | Measured tokens |
|---|---|---|---:|---:|
| P-022 pro-rata round-leader settlement | `deep_research` | `advance` to an OpportunityCard-shaped calibration artifact | 2 | 42,072 input / 1,966 output |
| P-016 state-change suppression defense | `reject` | screening completion | 1 | 20,638 input / 410 output |
| New maker-incentive filing | `defer` | recheck `2026-08-14T15:00:00Z` | 1 | 20,633 input / 524 output |

Effective final calibration total: **83,343 input / 2,900 output tokens**.
The provider was ChatGPT-subscription-backed and reported $0 incremental API
cost; this is not zero subscription usage.

## What calibration changed

The initial 12,000-input-token screening cap was empirically impossible for
this provider. The first run also allowed search and default reasoning, causing
20,420–99,257 input tokens per screen. Disabling search and setting reasoning
to low made all three screens converge tightly at 20,589–20,638 input tokens.
The hard ceiling is therefore **25,000**, giving about 21% operational headroom
while remaining below the first full-research pilot's 36,868–202,807-token
calls.

The second run exposed a schema defect: the unresolved lead returned a prose
recheck condition instead of a date. `ScreeningDecision` and the JSON schema now
require an ISO-8601 timezone-aware `recheck_after`. The invalid screen was not
grandfathered; it was rerun and returned `2026-08-14T15:00:00Z`.

Across calibration and its two corrective iterations, the provider made eight
calls totaling **268,711 input / 7,457 output tokens**. The final pass reused
only previously measured screens that satisfied the tightened contract.

## Substantive review

- P-022 advanced for the correct reason: the documented pro-rata settlement
  mechanic is real and falsifiable. The artifact explicitly says the corrected
  retrospective estimate remains underpowered and does not authorize live
  readiness.
- P-016 rejected the named defense because loss was diffuse and no
  suppression/widening arm made markout non-negative. It did not manufacture a
  successor thesis.
- The maker-incentive lead deferred because eligible contracts, rebate terms,
  queue rules, dates, participant eligibility, fees, and order-level outcomes
  remain unknown. It did not infer edge from a filing.

## Safety and provenance

The executor used an ephemeral, read-only Codex workspace; local command
execution was rejected by the adapter. Only `HOME`/Codex authentication context
was passed to the provider subprocess. The research dispatch queue, strategy
registry, services, and trading state were not read or mutated by the runner.

Final local artifacts:

- `data/research_calibration/pilot_v1c_20260807/observations.json`
  (`sha256:e90a78bbc91319ce1e1e0d578064450f1f084c4f244918a462f88580950bd6fe`)
- `data/research_calibration/pilot_v1c_20260807/report.json`
  (`sha256:32445fe857c8e44687affdca8bf3d3d16698f4cda3cd88fe19eb02244fe9b590`)
- Calibration cases
  (`sha256:93802611253b6f10275a737f0e4d39dcb1e08bfb9afe5c2c77c002ab1f1a1cf3`)
- Screening schema
  (`sha256:64ae831ba76e9fdd31c9cf6a87ed6ea9f7f13052191c8d68d3672da5e4aca586`)
- Full-output schema
  (`sha256:163bae5d4ee89d0a85d6990b494e1c9d92485c17cbd8194b0ec5389152f58ffc`)

The ignored `data/` results are diagnostic records, not strategy artifacts.
This committed report is the durable gate record.
