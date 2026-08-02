# Research dispositions

Store reviewed research assignments here as one JSON file per disposition.
These artifacts make rejection durable, enable `recheck_after`, and provide the
source-attribution denominator needed to measure research yield.

The schema is `ResearchDisposition` in `src/research_outcomes.py`. Example:

```json
{
  "assignment_id": "assignment_...",
  "source_item_id": "src_...",
  "decided_at": "2026-08-01T18:00:00Z",
  "decision": "reject",
  "reason_codes": ["no_executable_mechanism"],
  "evidence_checked": ["contract terms", "fee schedule", "prior research"],
  "research_minutes": 25,
  "opportunity_id": null,
  "recheck_after": null,
  "notes": "The source claim ignored settlement-basis risk."
}
```

`advance` requires an `opportunity_id`; `defer` requires `recheck_after`.
