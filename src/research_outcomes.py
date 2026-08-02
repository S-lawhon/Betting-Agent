"""Research disposition schema and source/lane funnel metrics."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


DECISIONS = {"reject", "defer", "advance"}


@dataclass(frozen=True)
class ResearchDisposition:
    assignment_id: str
    source_item_id: str
    decided_at: str
    decision: str
    reason_codes: List[str]
    evidence_checked: List[str]
    research_minutes: int
    opportunity_id: Optional[str] = None
    recheck_after: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(f"invalid research decision: {self.decision}")
        if self.research_minutes < 0:
            raise ValueError("research_minutes cannot be negative")
        if self.decision == "advance" and not self.opportunity_id:
            raise ValueError("advance disposition requires opportunity_id")
        if self.decision == "defer" and not self.recheck_after:
            raise ValueError("defer disposition requires recheck_after")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchDisposition":
        return cls(
            assignment_id=str(payload["assignment_id"]),
            source_item_id=str(payload["source_item_id"]),
            decided_at=str(payload["decided_at"]),
            decision=str(payload["decision"]),
            reason_codes=[str(value) for value in payload.get("reason_codes") or []],
            evidence_checked=[str(value) for value in
                              payload.get("evidence_checked") or []],
            research_minutes=int(payload.get("research_minutes", 0)),
            opportunity_id=payload.get("opportunity_id"),
            recheck_after=payload.get("recheck_after"),
            notes=str(payload.get("notes") or ""),
        )


def summarize_research(
    assignments: Sequence[Mapping[str, Any]],
    dispositions: Sequence[ResearchDisposition],
) -> Dict[str, Any]:
    by_id = {str(item.get("id")): item for item in assignments if item.get("id")}
    disposition_by_assignment = {item.assignment_id: item for item in dispositions}
    decisions = Counter(item.decision for item in dispositions)
    reasons = Counter(reason for item in dispositions for reason in item.reason_codes)
    source_stats: Dict[str, Counter] = defaultdict(Counter)
    source_name_stats: Dict[str, Counter] = defaultdict(Counter)
    attribution_stats: Dict[str, Counter] = defaultdict(Counter)
    lane_stats: Dict[str, Counter] = defaultdict(Counter)
    for assignment_id, assignment in by_id.items():
        source = str(assignment.get("source_type") or "unknown")
        source_name = str(assignment.get("source_name") or source)
        attribution = str(assignment.get("attribution_key") or source_name)
        lane = str(assignment.get("lane") or "unknown")
        source_stats[source]["assigned"] += 1
        source_name_stats[source_name]["assigned"] += 1
        attribution_stats[attribution]["assigned"] += 1
        lane_stats[lane]["assigned"] += 1
        disposition = disposition_by_assignment.get(assignment_id)
        if disposition:
            source_stats[source][disposition.decision] += 1
            source_stats[source]["research_minutes"] += disposition.research_minutes
            source_name_stats[source_name][disposition.decision] += 1
            source_name_stats[source_name]["research_minutes"] += disposition.research_minutes
            attribution_stats[attribution][disposition.decision] += 1
            attribution_stats[attribution]["research_minutes"] += disposition.research_minutes
            lane_stats[lane][disposition.decision] += 1
            lane_stats[lane]["research_minutes"] += disposition.research_minutes

    def finish(stats: Dict[str, Counter]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, values in sorted(stats.items()):
            assigned = values["assigned"]
            result[key] = dict(values) | {
                "advance_rate": (values["advance"] / assigned if assigned else 0.0),
                "review_rate": ((values["advance"] + values["reject"] + values["defer"])
                                / assigned if assigned else 0.0),
            }
        return result

    total_minutes = sum(item.research_minutes for item in dispositions)
    return {
        "assignments": len(by_id),
        "reviewed": len(set(by_id) & set(disposition_by_assignment)),
        "unreviewed": len(set(by_id) - set(disposition_by_assignment)),
        "decisions": dict(sorted(decisions.items())),
        "top_rejection_reasons": dict(reasons.most_common(20)),
        "research_minutes": total_minutes,
        "minutes_per_advance": (
            total_minutes / decisions["advance"] if decisions["advance"] else None
        ),
        "by_source_type": finish(source_stats),
        "by_source_name": finish(source_name_stats),
        "by_attribution": finish(attribution_stats),
        "by_lane": finish(lane_stats),
    }
