# Codex Research Pilot - Interim Operational Review

**Reviewed:** 2026-08-07 after the fourth and final model-execution day.  
**Final status checkpoint:** scheduled for 2026-08-08 14:00 UTC.

## Verdict

The tightly capped provider pilot already exists and operated successfully. Do
not enable a second provider path or add network access to the default hourly
planner. The fixed August 4-7 window completed four of four invocations with
durable, schema-valid dispositions and no service failure. The Saturday job is
status-only; no further model run is authorized by this window.

## Measured execution

| Date | Status | Input tokens | Output tokens | Artifact | Decision |
|---|---:|---:|---:|---|---|
| 2026-08-04 | completed | 88,948 | 1,589 | scout rejection | reject |
| 2026-08-05 | completed | 202,807 | 2,188 | scout rejection | reject |
| 2026-08-06 | completed | 74,134 | 1,226 | scout rejection | reject |
| 2026-08-07 | completed | 36,868 | 2,188 | scout rejection | reject |

Total: **4 completed, 0 failed, 402,757 input tokens, 7,191 output
tokens, 0 advances**. The provider is ChatGPT-subscription-backed, so the run
records correctly report $0 incremental API cost; that does not mean zero plan
usage.

## Safety findings

- The provider surface is the separate `research-agent-codex-week` /
  `research-agent-codex-pilot` path, not `research-agent-worker.service`.
- The default hourly worker remains network-denied, provider-free, and dry-run.
- The Codex service admits one `strategy-scout` attempt per UTC day, uses an
  empty workspace and ephemeral session, rejects local command execution, and
  masks betting credentials and the legacy tree.
- Every completed run produced a tracked invocation, artifact, and durable
  disposition. A successful process exit alone was never counted as research.
- The fixed-date timer has no execution date after August 7. Its August 8 run
  writes a status checkpoint only.

## Decision

The infrastructure pilot passed. Research yield did not: all four assignments
were rejected and none advanced. Leave the recurring default service in dry-run
and do not extend provider execution automatically. A new window should require
an explicit allocation decision based on the Saturday checkpoint and queue
quality, not merely the absence of runtime errors.
