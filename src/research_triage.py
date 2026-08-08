"""Deterministic research triage and specialist-dispatch contracts.

This module allocates scarce research attention.  It does not evaluate whether
an idea has edge, create dispositions, invoke an LLM, or mutate the strategy
registry.  Unknown evidence remains explicitly unknown in every scorecard.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src.research_intake import (
    quality_rejection_from_mapping,
    regulatory_filing_description_field,
    research_display_title,
)


SCHEMA_VERSION = 1

AGENT_BY_SOURCE = {
    "paper": "literature-scout",
    "social": "social-scout",
}

HANDOFF_BY_AGENT = {
    "literature-scout": ["literature-scout", "strategy-scout",
                         "research-critic", "strategy-spec"],
    "social-scout": ["social-scout", "strategy-scout",
                     "research-critic", "strategy-spec"],
    "strategy-scout": ["strategy-scout", "research-critic", "strategy-spec"],
}

BUDGET_MINUTES_BY_SOURCE = {
    "social": 20,
    "official_data": 25,
    "regulatory_filing": 30,
    "venue_market": 30,
    "practitioner": 35,
    "paper": 45,
}

STOPWORDS = {
    "a", "an", "and", "are", "at", "be", "by", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to",
    "what", "when", "will", "with",
}

COMPONENT_WEIGHTS = {
    "source_quality": 0.15,
    "legal_readiness": 0.15,
    "testability_prior": 0.15,
    "data_availability_prior": 0.15,
    "edge_half_life_prior": 0.15,
    "novelty": 0.15,
    "specificity_prior": 0.10,
    "historical_yield": 0.10,
}

MECHANISM_TERMS = {
    "api", "arbitrage", "calibration", "contract", "depth", "execution",
    "fee", "forecast", "incentive", "latency", "liquidity", "market maker",
    "market making", "mechanism",
    "mispricing", "model", "odds", "probability", "rebate", "research",
    "rule", "settlement", "slippage", "spread", "timestamp",
}

# These rejection reasons say the packet should have been filtered before a
# specialist spent time on it.  Other rejections can be valuable falsification
# and therefore are not treated as zero-yield source outcomes.
INPUT_QUALITY_REJECTION_REASONS = {
    "dormant_designated_venue",
    "no_executable_market",
    "no_live_market_data",
    "no_price_or_liquidity_data",
    "no_specific_market_mechanism",
    "no_testable_edge_mechanism",
    "not_active_venue",
    "organization_level_source_only",
    "pending_regulatory_application",
    "products_migrated_to_other_venue",
    "series_without_events",
    "venue_not_operational",
}

FEEDBACK_PRIOR_SCORE = 50.0
FEEDBACK_PRIOR_WEIGHT = 5


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def title_tokens(value: Any) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return sorted({token for token in tokens
                   if len(token) > 1 and token not in STOPWORDS})


def _similarity(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _source_text(assignment: Mapping[str, Any],
                 source_item: Mapping[str, Any]) -> str:
    metadata = source_item.get("metadata") or {}
    descriptive_metadata = " ".join(str(metadata.get(key) or "") for key in (
        "Filing Description", "Product Name", "Description", "market_family",
        "mechanism", "rule_change", "fee_structure",
    ))
    return "{} {} {}".format(
        source_item.get("title") or assignment.get("title") or "",
        source_item.get("summary") or "",
        descriptive_metadata,
    ).lower()


def normalize_legacy_regulatory_assignment(
    assignment: Mapping[str, Any], source_item: Mapping[str, Any],
) -> Dict[str, Any]:
    """Promote a filing description into a legacy assignment's display title.

    Older CFTC batches used the organization code as the title even when the
    filing table carried a mechanism-bearing ``Filing Description``. Preserve
    the source item byte-for-byte (and therefore its original content hash),
    but give newly generated dispatch packets the useful title emitted by the
    fixed collector.
    """
    normalized = dict(assignment)
    source_type = str(
        assignment.get("source_type") or source_item.get("source_type") or "")
    if source_type != "regulatory_filing":
        return normalized
    title = str(assignment.get("title") or source_item.get("title") or "").strip()
    normalized["title"] = research_display_title(
        source_type, title, source_item.get("metadata") or {})
    return normalized


def normalize_legacy_regulatory_source_item(
    source_item: Mapping[str, Any],
) -> Dict[str, Any]:
    """Give a dispatch packet's embedded source record the useful title.

    The packet is what a specialist (or a screening model) reads, so a legacy
    record whose title and summary are both the filer code ("COIN", "COIN:
    COIN") has to be corrected here too.  ``id`` and ``content_hash`` are left
    untouched, and the original title is retained as provenance, so the
    correction never masquerades as a different source item.
    """
    payload = dict(source_item)
    if str(payload.get("source_type") or "") != "regulatory_filing":
        return payload
    metadata = payload.get("metadata") or {}
    original = str(payload.get("title") or "").strip()
    normalized = research_display_title("regulatory_filing", original, metadata)
    if not normalized or normalized == original:
        return payload
    organization = str(metadata.get("Organization") or "").strip()
    payload["title"] = normalized
    payload["summary"] = (f"{organization}: {normalized}" if organization
                          else normalized)
    payload["original_title"] = original
    payload["title_source"] = "metadata.{}".format(
        regulatory_filing_description_field(metadata))
    return payload


def _mechanism_hits(text: str) -> List[str]:
    words = set(re.findall(r"[a-z0-9]+", text))
    return sorted(term for term in MECHANISM_TERMS
                  if ((" " in term and term in text)
                      or (" " not in term and term in words)))


def mechanism_readiness_rejection(
    assignment: Mapping[str, Any], source_item: Mapping[str, Any],
) -> Optional[str]:
    """Reject source records that are universe entries, not hypotheses.

    Intake intentionally keeps broad venue coverage.  Triage has a narrower
    job: spend specialist attention only when the compact packet names some
    market mechanism or comes from the mechanism-oriented census bridge.
    """
    source_type = str(assignment.get("source_type") or
                      source_item.get("source_type") or "")
    text = _source_text(assignment, source_item)
    hits = _mechanism_hits(text)
    metadata = source_item.get("metadata") or {}
    topics = {str(value).lower() for value in source_item.get("topics") or []}

    if source_type == "venue_market":
        # A census seed is already family-level and carries a deterministic
        # reason for being in the scout inbox.  A raw event listing such as a
        # single matchup is inventory, not a research mechanism.
        if metadata.get("market_family") or metadata.get("census_run_id"):
            return None
        if not hits:
            return "event_listing_without_mechanism"

    if source_type == "regulatory_filing":
        if "organizations" in topics and not hits:
            return "organization_listing_without_mechanism"
        quality_flags = set(
            (assignment.get("assignment") or {}).get("quality_flags") or [])
        if "official_listing_is_not_edge_evidence" in quality_flags and not hits:
            return "official_listing_without_mechanism"
    return None


def research_family_key(assignment: Mapping[str, Any],
                        source_item: Mapping[str, Any]) -> str:
    explicit = str(
        (assignment.get("assignment") or {}).get("market_family_key") or ""
    ).strip()
    if explicit:
        return explicit
    metadata = source_item.get("metadata") or {}
    census_family = str(metadata.get("market_family") or "").strip()
    source = re.sub(
        r"[^a-z0-9]+", "_",
        str(assignment.get("source_name") or source_item.get("source_name")
            or "unknown").lower(),
    ).strip("_")
    if census_family:
        return f"{source}:census:{census_family.lower()}"
    if str(assignment.get("source_type") or "") == "venue_market":
        text = str(assignment.get("title") or source_item.get("title") or "")
        if re.search(r"\bvs\.?\b", text.lower()):
            product = str(assignment.get("product_family") or "unknown").lower()
            return f"{source}:{product}:matchup"
    return ""


def _outcome_value(disposition: Mapping[str, Any]) -> Tuple[float, bool]:
    decision = str(disposition.get("decision") or "")
    reasons = {str(value) for value in disposition.get("reason_codes") or []}
    if decision == "advance":
        return 100.0, False
    if decision == "defer":
        return 45.0, False
    if decision == "reject" and reasons & INPUT_QUALITY_REJECTION_REASONS:
        return 10.0, True
    if decision == "reject":
        # A supported falsification is productive research, even though it did
        # not create an opportunity.
        return 65.0, False
    return FEEDBACK_PRIOR_SCORE, False


def _feedback_profiles(
    assignments: Mapping[str, Mapping[str, Any]],
    dispositions: Sequence[Mapping[str, Any]],
) -> Tuple[Dict[str, Dict[str, Dict[str, float]]], Dict[str, int]]:
    profiles: Dict[str, Dict[str, Dict[str, float]]] = {
        "attribution": defaultdict(lambda: {"count": 0.0, "total": 0.0}),
        "lane": defaultdict(lambda: {"count": 0.0, "total": 0.0}),
    }
    quality_rejections = 0
    used = 0
    for disposition in dispositions:
        assignment = assignments.get(str(disposition.get("assignment_id") or ""))
        if not assignment:
            continue
        value, input_quality_failure = _outcome_value(disposition)
        quality_rejections += int(input_quality_failure)
        used += 1
        keys = {
            "attribution": str(assignment.get("attribution_key") or
                               assignment.get("source_name") or "unknown"),
            "lane": str(assignment.get("lane") or "unknown"),
        }
        for dimension, key in keys.items():
            profiles[dimension][key]["count"] += 1
            profiles[dimension][key]["total"] += value
    return {
        dimension: {key: dict(values) for key, values in rows.items()}
        for dimension, rows in profiles.items()
    }, {"records": used, "input_quality_rejections": quality_rejections}


def _historical_yield_component(
    assignment: Mapping[str, Any],
    profiles: Mapping[str, Mapping[str, Mapping[str, float]]],
) -> Tuple[float, str]:
    observations: List[Tuple[float, float, str]] = []
    keys = {
        "attribution": str(assignment.get("attribution_key") or
                           assignment.get("source_name") or "unknown"),
        "lane": str(assignment.get("lane") or "unknown"),
    }
    for dimension, key in keys.items():
        row = (profiles.get(dimension) or {}).get(key) or {}
        count = float(row.get("count") or 0)
        if count <= 0:
            continue
        score = (
            float(row.get("total") or 0)
            + FEEDBACK_PRIOR_SCORE * FEEDBACK_PRIOR_WEIGHT
        ) / (count + FEEDBACK_PRIOR_WEIGHT)
        observations.append((score, min(count, 5.0), f"{dimension}:{key} n={int(count)}"))
    if not observations:
        return FEEDBACK_PRIOR_SCORE, "no prior dispositions; neutral shrunk prior"
    weight = sum(item[1] for item in observations)
    score = sum(item[0] * item[1] for item in observations) / weight
    return round(score, 2), "disposition feedback, shrunk: {}".format(
        "; ".join(item[2] for item in observations))


@dataclass(frozen=True)
class DispatchPacket:
    id: str
    assignment_id: str
    source_item_id: str
    created_at: str
    assigned_agent: str
    handoff_chain: List[str]
    priority: str
    triage_score: float
    research_budget_minutes: int
    score_components: Dict[str, Dict[str, Any]]
    evidence_unknowns: List[str]
    similar_assignment_ids: List[str]
    completion_contract: Dict[str, Any]
    assignment: Dict[str, Any]
    source_item: Dict[str, Any]
    status: str = "pending"
    safety: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _prior_score(name: str, assignment: Mapping[str, Any]) -> Tuple[int, str]:
    source = str(assignment.get("source_type") or "unknown")
    lane = str(assignment.get("lane") or "general_research")
    if name == "testability_prior":
        value = {
            "market_structure": 80, "new_products": 75, "cross_venue": 75,
            "information_latency": 70, "literature": 60,
            "practitioner_research": 55, "social_signal": 45,
        }.get(lane, 50)
        return value, "lane prior only; the specialist must identify a decisive test"
    if name == "data_availability_prior":
        value = {
            "official_data": 85, "regulatory_filing": 80,
            "venue_market": 75, "paper": 60, "practitioner": 50,
            "social": 35,
        }.get(source, 45)
        return value, "source-type prior only; no dataset has been verified"
    if name == "edge_half_life_prior":
        value = {
            "social_signal": 90, "information_latency": 85,
            "new_products": 75, "cross_venue": 70,
            "market_structure": 65, "practitioner_research": 55,
            "literature": 35,
        }.get(lane, 50)
        return value, "attention-decay prior, not evidence that an edge exists"
    raise KeyError(name)


def _legal_component(assignment: Mapping[str, Any]) -> Tuple[int, str, bool]:
    decisions = [row for row in assignment.get("legal_decisions") or []
                 if isinstance(row, Mapping)]
    if not decisions:
        return 60, "venue-neutral lead; execution eligibility remains unknown", False
    if any(row.get("research_allowed") is False for row in decisions):
        return 0, "one or more venue decisions prohibit research", True
    if all(row.get("execution_allowed") is True for row in decisions):
        return 100, "all recorded venue/product decisions allow execution", False
    if any(row.get("stale") is True for row in decisions):
        return 30, "research allowed, but at least one eligibility decision is stale", False
    statuses = sorted({str(row.get("status") or "unknown") for row in decisions})
    return 55, "research allowed; execution is not approved ({})".format(
        ", ".join(statuses)), False


def _score_assignment(
    assignment: Mapping[str, Any],
    *,
    source_item: Mapping[str, Any],
    historical_titles: Mapping[str, Sequence[str]],
    current_titles: Mapping[str, Sequence[str]],
    feedback_profiles: Mapping[str, Mapping[str, Mapping[str, float]]],
) -> Tuple[float, Dict[str, Dict[str, Any]], List[str], List[str], bool]:
    assignment_id = str(assignment.get("id") or "")
    tokens = current_titles.get(assignment_id) or []
    similarities: List[Tuple[float, str]] = []
    for other_id, other_tokens in list(historical_titles.items()) + list(current_titles.items()):
        if other_id == assignment_id:
            continue
        similarity = _similarity(tokens, other_tokens)
        if similarity >= 0.55:
            similarities.append((similarity, str(other_id)))
    similarities.sort(key=lambda pair: (-pair[0], pair[1]))
    max_similarity = similarities[0][0] if similarities else 0.0
    novelty = round(max(10.0, 100.0 * (1.0 - max_similarity)))

    source_score = max(0, min(100, int(assignment.get("score") or 0)))
    legal_score, legal_reason, blocked = _legal_component(assignment)
    components: Dict[str, Dict[str, Any]] = {
        "source_quality": {
            "score": source_score,
            "reason": "intake provenance/reliability score; not edge evidence",
        },
        "legal_readiness": {"score": legal_score, "reason": legal_reason},
        "novelty": {
            "score": novelty,
            "reason": "title-token comparison against tracked assignments; semantic duplication still requires review",
        },
        "capacity": {
            "score": None,
            "reason": "unknown until executable depth and scalable event cadence are measured",
        },
        "net_edge": {
            "score": None,
            "reason": "unknown until a falsifiable mechanism survives costs and validation",
        },
    }
    source_text = _source_text(assignment, source_item)
    term_hits = _mechanism_hits(source_text)
    specificity = min(90, 20 + 15 * len(term_hits))
    if str(assignment.get("source_type") or "") in {
            "paper", "regulatory_filing", "official_data"}:
        specificity = max(specificity, 55)
    components["specificity_prior"] = {
        "score": specificity,
        "reason": ("mechanism/test vocabulary present: {}".format(
            ", ".join(term_hits[:6])) if term_hits else
            "no mechanism/test vocabulary detected in the compact source text"),
    }
    yield_score, yield_reason = _historical_yield_component(
        assignment, feedback_profiles)
    components["historical_yield"] = {
        "score": yield_score,
        "reason": yield_reason,
    }
    for name in ("testability_prior", "data_availability_prior",
                 "edge_half_life_prior"):
        value, reason = _prior_score(name, assignment)
        components[name] = {"score": value, "reason": reason}

    numerator = sum(COMPONENT_WEIGHTS[name] * float(components[name]["score"])
                    for name in COMPONENT_WEIGHTS
                    if components[name]["score"] is not None)
    denominator = sum(COMPONENT_WEIGHTS[name] for name in COMPONENT_WEIGHTS
                      if components[name]["score"] is not None)
    total = round(numerator / denominator, 2) if denominator else 0.0
    unknowns = [name for name, row in components.items() if row["score"] is None]
    similar = [other_id for _, other_id in similarities[:5]]
    return total, components, unknowns, similar, blocked


def _priority(score: float) -> str:
    if score >= 75:
        return "high"
    if score >= 60:
        return "medium"
    return "exploratory"


def _agent(assignment: Mapping[str, Any]) -> str:
    declared = str((assignment.get("assignment") or {}).get("agent") or "")
    source = str(assignment.get("source_type") or "")
    return AGENT_BY_SOURCE.get(source, declared or "strategy-scout")


def _budget_minutes(assignment: Mapping[str, Any]) -> int:
    return BUDGET_MINUTES_BY_SOURCE.get(
        str(assignment.get("source_type") or ""), 30)


def _packet(assignment: Mapping[str, Any], *, source_item: Mapping[str, Any],
            now: datetime,
            historical_titles: Mapping[str, Sequence[str]],
            current_titles: Mapping[str, Sequence[str]],
            feedback_profiles: Mapping[str, Mapping[str, Mapping[str, float]]],
            ) -> Tuple[DispatchPacket, bool]:
    score, components, unknowns, similar, blocked = _score_assignment(
        assignment, source_item=source_item,
        historical_titles=historical_titles,
        current_titles=current_titles,
        feedback_profiles=feedback_profiles)
    assignment_id = str(assignment["id"])
    assigned_agent = _agent(assignment)
    packet = DispatchPacket(
        id=f"dispatch_{_hash([assignment_id, assigned_agent])[:20]}",
        assignment_id=assignment_id,
        source_item_id=str(assignment.get("source_item_id") or ""),
        created_at=_utc_iso(now),
        assigned_agent=assigned_agent,
        handoff_chain=HANDOFF_BY_AGENT.get(
            assigned_agent, [assigned_agent, "research-critic", "strategy-spec"]),
        priority=_priority(score),
        triage_score=score,
        research_budget_minutes=_budget_minutes(assignment),
        score_components=components,
        evidence_unknowns=unknowns,
        similar_assignment_ids=similar,
        completion_contract={
            "required_output": "ResearchDisposition or falsifiable hypothesis brief",
            "disposition_schema": "src.research_outcomes.ResearchDisposition",
            "advance_requires": [
                "named falsifiable mechanism", "public primary evidence",
                "cheapest decisive test", "timestamp-correct dataset",
                "current prices/depth or a documented availability plan",
                "fees, fills, and execution friction",
                "settlement rules and current eligibility decision",
                "opportunity_id after OpportunityCard registration",
            ],
            "output_directory": "research/dispositions",
        },
        assignment=dict(assignment),
        source_item=dict(source_item),
        safety={
            "creates_opportunity_card": False,
            "mutates_strategy_registry": False,
            "authorizes_execution": False,
            "engagement_is_edge_evidence": False,
        },
    )
    return packet, blocked


def triage_assignments(
    assignments: Sequence[Mapping[str, Any]],
    *,
    previous_ledger: Optional[Mapping[str, Any]] = None,
    source_items_by_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
    reviewed_assignment_ids: Optional[Set[str]] = None,
    opportunity_assignment_ids: Optional[Set[str]] = None,
    redispatch_assignment_ids: Optional[Set[str]] = None,
    disposition_history: Sequence[Mapping[str, Any]] = (),
    pending_packets: Sequence[Mapping[str, Any]] = (),
    memory_rules: Sequence[Mapping[str, Any]] = (),
    now: Optional[datetime] = None,
    max_dispatches: int = 10,
    max_research_minutes: int = 300,
    lane_concentration_cap: float = 0.40,
    max_pending_total: Optional[int] = None,
    max_pending_per_agent: Optional[int] = None,
    max_new_dispatches_per_run: Optional[int] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[DispatchPacket]]:
    """Rank unreviewed assignments and allocate a diverse bounded portfolio."""
    if max_dispatches < 1:
        raise ValueError("max_dispatches must be positive")
    if max_research_minutes < 1:
        raise ValueError("max_research_minutes must be positive")
    if not 0 < lane_concentration_cap <= 1:
        raise ValueError("lane_concentration_cap must be in (0, 1]")
    for name, value in (
        ("max_pending_total", max_pending_total),
        ("max_pending_per_agent", max_pending_per_agent),
        ("max_new_dispatches_per_run", max_new_dispatches_per_run),
    ):
        if value is not None and value < 0:
            raise ValueError(f"{name} cannot be negative")

    now = now or datetime.now(timezone.utc)
    previous = dict(previous_ledger or {})
    source_items = {
        str(key): normalize_legacy_regulatory_source_item(value)
        for key, value in dict(source_items_by_id or {}).items()
    }
    dispatched_before = set(previous.get("dispatched_assignment_ids") or [])
    reopened = set(redispatch_assignment_ids or set())
    dispatched_before -= reopened
    reviewed = set(reviewed_assignment_ids or set())
    opportunities = set(opportunity_assignment_ids or set())
    historical_titles = {
        str(key): [str(token) for token in value]
        for key, value in (previous.get("title_tokens_by_assignment") or {}).items()
        if isinstance(value, list)
    }
    day = now.date().isoformat()
    daily_allocations = {
        str(key): dict(value) for key, value in
        (previous.get("daily_allocations") or {}).items()
        if isinstance(value, Mapping)
    }
    today = daily_allocations.get(day) or {}
    dispatched_today_before = int(today.get("dispatches", 0))
    minutes_today_before = int(today.get("minutes", 0))
    remaining_dispatches = max(0, max_dispatches - dispatched_today_before)
    remaining_minutes = max(0, max_research_minutes - minutes_today_before)

    pending_by_agent = Counter(
        str(packet.get("assigned_agent") or "unknown")
        for packet in pending_packets
        if packet.get("assignment_id"))
    pending_total = sum(pending_by_agent.values())
    pending_by_agent_before = dict(pending_by_agent)
    if max_pending_total is not None:
        remaining_dispatches = min(
            remaining_dispatches, max(0, max_pending_total - pending_total))
    if max_new_dispatches_per_run is not None:
        remaining_dispatches = min(
            remaining_dispatches, max_new_dispatches_per_run)

    by_id: Dict[str, Mapping[str, Any]] = {}
    invalid = 0
    for assignment in assignments:
        assignment_id = str(assignment.get("id") or "")
        if not assignment_id or not assignment.get("source_item_id"):
            invalid += 1
            continue
        source_item = source_items.get(str(assignment.get("source_item_id"))) or {}
        by_id[assignment_id] = normalize_legacy_regulatory_assignment(
            assignment, source_item)
    current_titles = {assignment_id: title_tokens(row.get("title"))
                      for assignment_id, row in by_id.items()}
    feedback_profiles, feedback_summary = _feedback_profiles(
        by_id, disposition_history)

    packets: List[DispatchPacket] = []
    legally_blocked: List[str] = []
    quality_blocked: Dict[str, str] = {}
    readiness_blocked: Dict[str, str] = {}
    candidates = []
    for assignment_id, assignment in by_id.items():
        if (assignment_id in dispatched_before or assignment_id in reviewed
                or assignment_id in opportunities):
            continue
        source_item = source_items.get(str(assignment.get("source_item_id"))) or {}
        quality_reason = quality_rejection_from_mapping(
            source_item, now=now, memory_rules=memory_rules)
        if quality_reason:
            quality_blocked[assignment_id] = quality_reason
            continue
        readiness_reason = mechanism_readiness_rejection(
            assignment, source_item)
        if readiness_reason:
            readiness_blocked[assignment_id] = readiness_reason
            continue
        packet, blocked = _packet(
            assignment,
            source_item=source_item,
            now=now, historical_titles=historical_titles,
            current_titles=current_titles,
            feedback_profiles=feedback_profiles)
        if blocked:
            legally_blocked.append(assignment_id)
            continue
        candidates.append(packet)

    candidates.sort(key=lambda packet: (
        -packet.triage_score,
        -int(packet.assignment.get("score") or 0),
        packet.assignment_id,
    ))
    family_duplicates: Dict[str, str] = {}
    family_representatives: Dict[str, str] = {}
    deduplicated_candidates: List[DispatchPacket] = []
    for packet in candidates:
        family = research_family_key(packet.assignment, packet.source_item)
        if family and family in family_representatives:
            family_duplicates[packet.assignment_id] = family_representatives[family]
            continue
        if family:
            family_representatives[family] = packet.assignment_id
        deduplicated_candidates.append(packet)
    candidates = deduplicated_candidates
    lane_cap = max(1, math.ceil(max_dispatches * lane_concentration_cap))
    lane_counts: Dict[str, int] = {
        str(key): int(value) for key, value in (today.get("by_lane") or {}).items()
    }
    minutes = 0
    agent_capacity_blocked: Set[str] = set()

    def agent_has_capacity(packet: DispatchPacket) -> bool:
        if max_pending_per_agent is None:
            return True
        return pending_by_agent.get(packet.assigned_agent, 0) < max_pending_per_agent

    def select(packet: DispatchPacket) -> None:
        nonlocal minutes
        packets.append(packet)
        minutes += packet.research_budget_minutes
        pending_by_agent[packet.assigned_agent] += 1

    # Exploration floor: take the best affordable item from every available
    # lane before filling by score. This prevents a temporarily productive lane
    # from consuming the entire research portfolio on small-sample history.
    top_by_lane: Dict[str, DispatchPacket] = {}
    for packet in candidates:
        lane = str(packet.assignment.get("lane") or "general_research")
        top_by_lane.setdefault(lane, packet)
    for lane, packet in sorted(top_by_lane.items(),
                               key=lambda pair: (-pair[1].triage_score, pair[0])):
        if len(packets) >= remaining_dispatches:
            break
        if lane_counts.get(lane, 0) >= lane_cap:
            continue
        if not agent_has_capacity(packet):
            agent_capacity_blocked.add(packet.assignment_id)
            continue
        if minutes + packet.research_budget_minutes > remaining_minutes:
            continue
        select(packet)
        lane_counts[lane] = lane_counts.get(lane, 0) + 1

    selected_ids = {packet.assignment_id for packet in packets}
    for packet in candidates:
        if (packet.assignment_id in selected_ids
                or len(packets) >= remaining_dispatches):
            continue
        lane = str(packet.assignment.get("lane") or "general_research")
        if lane_counts.get(lane, 0) >= lane_cap:
            continue
        if not agent_has_capacity(packet):
            agent_capacity_blocked.add(packet.assignment_id)
            continue
        if minutes + packet.research_budget_minutes > remaining_minutes:
            continue
        select(packet)
        selected_ids.add(packet.assignment_id)
        lane_counts[lane] = lane_counts.get(lane, 0) + 1

    dispatched_after = dispatched_before | selected_ids
    title_history = dict(historical_titles)
    for packet in packets:
        title_history[packet.assignment_id] = current_titles[packet.assignment_id]
    daily_allocations[day] = {
        "dispatches": dispatched_today_before + len(packets),
        "minutes": minutes_today_before + minutes,
        "by_lane": dict(sorted(lane_counts.items())),
    }
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _utc_iso(now),
        "dispatched_assignment_ids": sorted(dispatched_after),
        "title_tokens_by_assignment": dict(sorted(title_history.items())),
        "daily_allocations": dict(sorted(daily_allocations.items())),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_iso(now),
        "assignments_seen": len(by_id),
        "invalid_assignments": invalid,
        "already_dispatched": len(set(by_id) & dispatched_before),
        "already_reviewed": len(set(by_id) & reviewed),
        "already_opportunity": len(set(by_id) & opportunities),
        "reopened_due_deferral": len(set(by_id) & reopened),
        "legally_blocked": len(legally_blocked),
        "legally_blocked_assignment_ids": sorted(legally_blocked),
        "quality_blocked": len(quality_blocked),
        "quality_blocked_assignment_ids": dict(sorted(quality_blocked.items())),
        "readiness_blocked": len(readiness_blocked),
        "readiness_blocked_assignment_ids": dict(
            sorted(readiness_blocked.items())),
        "family_duplicates": len(family_duplicates),
        "family_duplicate_representatives": dict(
            sorted(family_duplicates.items())),
        "eligible_candidates": len(candidates),
        "dispatched": len(packets),
        "deferred": len(candidates) - len(packets),
        "research_minutes_allocated": minutes,
        "utc_day": day,
        "daily_totals": dict(daily_allocations[day]),
        "daily_remaining": {
            "dispatches": max(0, max_dispatches - daily_allocations[day]["dispatches"]),
            "minutes": max(0, max_research_minutes - daily_allocations[day]["minutes"]),
        },
        "limits": {
            "max_dispatches": max_dispatches,
            "max_research_minutes": max_research_minutes,
            "lane_concentration_cap": lane_concentration_cap,
        },
        "backpressure": {
            "pending_before": pending_total,
            "pending_by_agent_before": dict(sorted(
                pending_by_agent_before.items())),
            "pending_after": pending_total + len(packets),
            "max_pending_total": max_pending_total,
            "max_pending_per_agent": max_pending_per_agent,
            "max_new_dispatches_per_run": max_new_dispatches_per_run,
            "slots_available_at_start": remaining_dispatches,
            "agent_capacity_blocked_assignment_ids": sorted(
                agent_capacity_blocked),
            "saturated": bool(
                (max_pending_total is not None
                 and pending_total >= max_pending_total)
                or (max_new_dispatches_per_run == 0)
                or (bool(candidates) and not packets
                    and len(agent_capacity_blocked) == len(candidates))),
        },
        "outcome_feedback": {
            **feedback_summary,
            "prior_score": FEEDBACK_PRIOR_SCORE,
            "prior_weight": FEEDBACK_PRIOR_WEIGHT,
            "input_quality_reason_codes": sorted(
                INPUT_QUALITY_REJECTION_REASONS),
        },
        "by_agent": {
            agent: sum(packet.assigned_agent == agent for packet in packets)
            for agent in sorted({packet.assigned_agent for packet in packets})
        },
        "by_lane": dict(sorted(lane_counts.items())),
        "by_priority": {
            priority: sum(packet.priority == priority for packet in packets)
            for priority in ("high", "medium", "exploratory")
        },
        "safety": {
            "invokes_agents": False,
            "creates_dispositions": False,
            "creates_opportunity_cards": False,
            "mutates_strategy_registry": False,
            "authorizes_execution": False,
        },
    }
    return ledger, manifest, packets
