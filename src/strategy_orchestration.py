"""
src/strategy_orchestration.py
─────────────────────────────
Typed schema + registry for recursive strategy development.

This module gives the strategy factory a production home:

    idea -> spec -> validated -> paper_live -> live_small -> live_scaled
         -> degraded -> retired

It is deliberately deterministic, JSON-friendly, and strict about state
transitions so agents can share the same contract without inventing their own
variants.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json
import tempfile

UTC = timezone.utc

STRATEGY_STATES = (
    "idea",
    "spec",
    "validated",
    "paper_live",
    "live_small",
    "live_scaled",
    "degraded",
    "retired",
)

ALLOWED_TRANSITIONS = {
    "idea": {"spec", "retired"},
    "spec": {"validated", "retired"},
    "validated": {"paper_live", "retired"},
    "paper_live": {"live_small", "degraded", "retired"},
    "live_small": {"live_scaled", "degraded", "retired"},
    "live_scaled": {"degraded", "retired"},
    "degraded": {"paper_live", "retired"},
    "retired": set(),
}

REPORT_STATUSES = {"pass", "warn", "fail"}
PROMOTION_DECISIONS = {"promote", "hold", "demote", "retire"}
MONITORING_STATUSES = {"ok", "warn", "degraded", "retired"}


class StrategyError(ValueError):
    """Base class for registry errors."""


class StrategyNotFoundError(StrategyError):
    """Raised when a strategy id is unknown."""


class StrategyTransitionError(StrategyError):
    """Raised when a state transition is not allowed."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _ensure_list(value: Optional[Iterable[Any]]) -> List[Any]:
    if value is None:
        return []
    return list(value)


def _ensure_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return dict(value or {})


def _require_state(state: str) -> str:
    if state not in STRATEGY_STATES:
        raise StrategyError("invalid strategy state: {}".format(state))
    return state


def _require_status(status: str, allowed: Iterable[str], label: str) -> str:
    if status not in allowed:
        raise StrategyError("invalid {}: {}".format(label, status))
    return status


def _require_fraction(name: str, value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise StrategyError("{} must be in [0, 1]".format(name))
    return value


@dataclass(frozen=True)
class OpportunityCard:
    id: str
    created_at: str
    source_agent: str
    market_family: str
    thesis: str
    edge_source: List[str]
    external_data_needed: List[str]
    confidence: float
    urgency: str
    status: str = "open"
    known_failure_modes: List[str] = field(default_factory=list)
    rule_ambiguities: List[str] = field(default_factory=list)
    next_step: str = "spec"
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_fraction("confidence", self.confidence)
        _ensure_list(self.edge_source)
        _ensure_list(self.external_data_needed)
        _ensure_list(self.known_failure_modes)
        _ensure_list(self.rule_ambiguities)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "OpportunityCard":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        extra = {k: v for k, v in payload.items() if k not in known}
        filtered = {
            k: v for k, v in payload.items()
            if k in known and k not in {
                "edge_source", "external_data_needed",
                "known_failure_modes", "rule_ambiguities", "extra",
            }
        }
        return cls(
            **filtered,
            edge_source=_ensure_list(payload.get("edge_source")),
            external_data_needed=_ensure_list(payload.get("external_data_needed")),
            known_failure_modes=_ensure_list(payload.get("known_failure_modes")),
            rule_ambiguities=_ensure_list(payload.get("rule_ambiguities")),
            extra=_ensure_dict(payload.get("extra")) | extra,
        )


@dataclass(frozen=True)
class StrategySpec:
    id: str
    opportunity_id: str
    name: str
    version: str
    state: str
    entry_logic: Dict[str, Any]
    exit_logic: Dict[str, Any]
    fair_value_model: Dict[str, Any]
    fee_model: Dict[str, Any]
    risk_limits: Dict[str, Any]
    null_hypothesis: str
    alternative_hypothesis: str
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_state(self.state)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "StrategySpec":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        extra = {k: v for k, v in payload.items() if k not in known}
        filtered = {
            k: v for k, v in payload.items()
            if k in known and k not in {"entry_logic", "exit_logic", "fair_value_model",
                                        "fee_model", "risk_limits", "extra"}
        }
        return cls(
            **filtered,
            entry_logic=_ensure_dict(payload.get("entry_logic")),
            exit_logic=_ensure_dict(payload.get("exit_logic")),
            fair_value_model=_ensure_dict(payload.get("fair_value_model")),
            fee_model=_ensure_dict(payload.get("fee_model")),
            risk_limits=_ensure_dict(payload.get("risk_limits")),
            extra=_ensure_dict(payload.get("extra")) | extra,
        )


@dataclass(frozen=True)
class IntegrityReport:
    strategy_id: str
    checked_at: str
    status: str
    contract_terms: Dict[str, Any]
    settlement_mapping: Dict[str, Any]
    fee_mapping: Dict[str, Any]
    api_field_reliability: Dict[str, Any]
    external_schedule_dependency: Dict[str, Any]
    blocking_issues: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_status(self.status, REPORT_STATUSES, "integrity status")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "IntegrityReport":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        extra = {k: v for k, v in payload.items() if k not in known}
        filtered = {
            k: v for k, v in payload.items()
            if k in known and k not in {
                "contract_terms", "settlement_mapping", "fee_mapping",
                "api_field_reliability", "external_schedule_dependency",
                "blocking_issues", "extra",
            }
        }
        return cls(
            **filtered,
            contract_terms=_ensure_dict(payload.get("contract_terms")),
            settlement_mapping=_ensure_dict(payload.get("settlement_mapping")),
            fee_mapping=_ensure_dict(payload.get("fee_mapping")),
            api_field_reliability=_ensure_dict(payload.get("api_field_reliability")),
            external_schedule_dependency=_ensure_dict(
                payload.get("external_schedule_dependency")
            ),
            blocking_issues=_ensure_list(payload.get("blocking_issues")),
            extra=_ensure_dict(payload.get("extra")) | extra,
        )


@dataclass(frozen=True)
class ValidationReport:
    strategy_id: str
    run_id: str
    checked_at: str
    status: str
    sample_size: Dict[str, Any]
    performance: Dict[str, Any]
    uncertainty: Dict[str, Any]
    stress_tests: Dict[str, Any]
    capacity: Dict[str, Any]
    go_no_go: str
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_status(self.status, REPORT_STATUSES, "validation status")

    def passes(self) -> bool:
        return self.status == "pass" and self.go_no_go == "go"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ValidationReport":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        extra = {k: v for k, v in payload.items() if k not in known}
        filtered = {
            k: v for k, v in payload.items()
            if k in known and k not in {
                "sample_size", "performance", "uncertainty",
                "stress_tests", "capacity", "extra",
            }
        }
        return cls(
            **filtered,
            sample_size=_ensure_dict(payload.get("sample_size")),
            performance=_ensure_dict(payload.get("performance")),
            uncertainty=_ensure_dict(payload.get("uncertainty")),
            stress_tests=_ensure_dict(payload.get("stress_tests")),
            capacity=_ensure_dict(payload.get("capacity")),
            extra=_ensure_dict(payload.get("extra")) | extra,
        )


@dataclass(frozen=True)
class PromotionDecision:
    strategy_id: str
    decision_at: str
    from_state: str
    to_state: str
    decision: str
    rationale: List[str]
    required_conditions: List[str]
    owner_agent: str
    review_window_days: int
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_state(self.from_state)
        _require_state(self.to_state)
        _require_status(self.decision, PROMOTION_DECISIONS, "promotion decision")
        if self.decision == "hold" and self.from_state != self.to_state:
            raise StrategyError("hold decisions must not change state")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "PromotionDecision":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        extra = {k: v for k, v in payload.items() if k not in known}
        filtered = {
            k: v for k, v in payload.items()
            if k in known and k not in {"rationale", "required_conditions", "extra"}
        }
        return cls(
            **filtered,
            rationale=_ensure_list(payload.get("rationale")),
            required_conditions=_ensure_list(payload.get("required_conditions")),
            extra=_ensure_dict(payload.get("extra")) | extra,
        )


@dataclass(frozen=True)
class MonitoringReport:
    strategy_id: str
    observed_at: str
    state: str
    window: str
    status: str
    realized_edge: float
    clv: float
    pnl_usd: float
    fill_quality: Dict[str, Any]
    drift_signals: List[str] = field(default_factory=list)
    guardrail_breaches: List[str] = field(default_factory=list)
    recommended_state: Optional[str] = None
    notes: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_state(self.state)
        _require_status(self.status, MONITORING_STATUSES, "monitoring status")
        if self.recommended_state is not None:
            _require_state(self.recommended_state)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "MonitoringReport":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        extra = {k: v for k, v in payload.items() if k not in known}
        filtered = {
            k: v for k, v in payload.items()
            if k in known and k not in {"fill_quality", "drift_signals",
                                        "guardrail_breaches", "extra"}
        }
        return cls(
            **filtered,
            fill_quality=_ensure_dict(payload.get("fill_quality")),
            drift_signals=_ensure_list(payload.get("drift_signals")),
            guardrail_breaches=_ensure_list(payload.get("guardrail_breaches")),
            extra=_ensure_dict(payload.get("extra")) | extra,
        )


@dataclass(frozen=True)
class StrategyEvent:
    timestamp_utc: str
    strategy_id: str
    kind: str
    from_state: str
    to_state: str
    actor: str
    note: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "StrategyEvent":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {
            k: v for k, v in payload.items()
            if k in known and k != "payload"
        }
        return cls(
            **filtered,
            payload=_ensure_dict(payload.get("payload")),
        )


@dataclass
class StrategyRecord:
    opportunity: OpportunityCard
    state: str = "idea"
    spec: Optional[StrategySpec] = None
    integrity: Optional[IntegrityReport] = None
    validation: Optional[ValidationReport] = None
    promotion: Optional[PromotionDecision] = None
    monitoring: List[MonitoringReport] = field(default_factory=list)
    events: List[StrategyEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_state(self.state)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "StrategyRecord":
        opportunity = OpportunityCard.from_dict(payload["opportunity"])
        spec = payload.get("spec")
        integrity = payload.get("integrity")
        validation = payload.get("validation")
        promotion = payload.get("promotion")
        monitoring = [
            MonitoringReport.from_dict(x) for x in (payload.get("monitoring") or [])
        ]
        events = [StrategyEvent.from_dict(x) for x in (payload.get("events") or [])]
        return cls(
            opportunity=opportunity,
            state=payload.get("state", "idea"),
            spec=StrategySpec.from_dict(spec) if spec else None,
            integrity=IntegrityReport.from_dict(integrity) if integrity else None,
            validation=ValidationReport.from_dict(validation) if validation else None,
            promotion=PromotionDecision.from_dict(promotion) if promotion else None,
            monitoring=monitoring,
            events=events,
        )


class StrategyRegistry:
    """In-memory registry for the recursive strategy factory."""

    def __init__(self) -> None:
        self._records: Dict[str, StrategyRecord] = {}

    def __contains__(self, strategy_id: str) -> bool:
        return strategy_id in self._records

    def get(self, strategy_id: str) -> StrategyRecord:
        try:
            return self._records[strategy_id]
        except KeyError as exc:
            raise StrategyNotFoundError(strategy_id) from exc

    def values(self) -> List[StrategyRecord]:
        return list(self._records.values())

    def by_state(self, state: str) -> List[StrategyRecord]:
        _require_state(state)
        return [r for r in self._records.values() if r.state == state]

    def register_opportunity(self, card: OpportunityCard) -> StrategyRecord:
        if card.id in self._records:
            raise StrategyError("strategy already exists: {}".format(card.id))
        record = StrategyRecord(opportunity=card)
        self._records[card.id] = record
        self._append_event(
            record, kind="opportunity_registered", from_state="idea",
            to_state="idea", actor=card.source_agent, payload=card.to_dict(),
        )
        return record

    def attach_spec(self, strategy_id: str, spec: StrategySpec,
                    actor: str = "spec") -> StrategyRecord:
        record = self.get(strategy_id)
        if spec.opportunity_id != strategy_id:
            raise StrategyError("spec/opportunity mismatch: {}".format(strategy_id))
        record.spec = spec
        self._transition(record, "spec", actor=actor, kind="spec_attached",
                         payload=spec.to_dict())
        return record

    def record_integrity(self, strategy_id: str, report: IntegrityReport,
                         actor: str = "integrity") -> StrategyRecord:
        record = self.get(strategy_id)
        if report.strategy_id != strategy_id:
            raise StrategyError("integrity report mismatch: {}".format(strategy_id))
        record.integrity = report
        self._append_event(
            record, kind="integrity_recorded", from_state=record.state,
            to_state=record.state, actor=actor, payload=report.to_dict(),
        )
        return record

    def record_validation(self, strategy_id: str, report: ValidationReport,
                          actor: str = "validation") -> StrategyRecord:
        record = self.get(strategy_id)
        if report.strategy_id != strategy_id:
            raise StrategyError("validation report mismatch: {}".format(strategy_id))
        if record.integrity is None or record.integrity.status != "pass":
            raise StrategyTransitionError(
                "strategy {} cannot validate before integrity passes".format(strategy_id)
            )
        record.validation = report
        if report.passes():
            self._transition(record, "validated", actor=actor,
                             kind="validation_passed", payload=report.to_dict())
        else:
            self._append_event(
                record, kind="validation_failed", from_state=record.state,
                to_state=record.state, actor=actor, payload=report.to_dict(),
            )
        return record

    def record_promotion(self, strategy_id: str, decision: PromotionDecision,
                         actor: str = "promotion") -> StrategyRecord:
        record = self.get(strategy_id)
        if decision.strategy_id != strategy_id:
            raise StrategyError("promotion decision mismatch: {}".format(strategy_id))
        if record.state != decision.from_state:
            raise StrategyTransitionError(
                "decision from_state {} does not match current state {}".format(
                    decision.from_state, record.state)
            )
        record.promotion = decision
        if decision.decision == "promote" or decision.decision in {"demote", "retire"}:
            self._transition(record, decision.to_state, actor=actor,
                             kind="promotion_{}".format(decision.decision),
                             payload=decision.to_dict())
        else:
            self._append_event(
                record, kind="promotion_hold", from_state=record.state,
                to_state=record.state, actor=actor, payload=decision.to_dict(),
            )
        return record

    def record_monitoring(self, strategy_id: str, report: MonitoringReport,
                          actor: str = "monitor") -> StrategyRecord:
        record = self.get(strategy_id)
        if report.strategy_id != strategy_id:
            raise StrategyError("monitoring report mismatch: {}".format(strategy_id))
        record.monitoring.append(report)
        self._append_event(
            record, kind="monitoring_recorded", from_state=record.state,
            to_state=record.state, actor=actor, payload=report.to_dict(),
        )
        if report.recommended_state and report.recommended_state != record.state:
            self._transition(
                record, report.recommended_state, actor=actor,
                kind="monitoring_transition", payload=report.to_dict(),
            )
        return record

    def transition(self, strategy_id: str, to_state: str,
                   actor: str = "system", kind: str = "state_transition",
                   payload: Optional[Dict[str, Any]] = None) -> StrategyRecord:
        record = self.get(strategy_id)
        self._transition(record, to_state, actor=actor, kind=kind,
                         payload=payload or {})
        return record

    def to_dict(self) -> Dict[str, Any]:
        return {"records": {sid: rec.to_dict() for sid, rec in self._records.items()}}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "StrategyRegistry":
        registry = cls()
        for sid, rec in (payload.get("records") or {}).items():
            registry._records[sid] = StrategyRecord.from_dict(rec)
        return registry

    def dump(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent),
                                         prefix=path.name + ".", suffix=".tmp") as fh:
            fh.write(payload)
            tmp_path = Path(fh.name)
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: Path) -> "StrategyRegistry":
        return cls.from_dict(json.loads(Path(path).read_text()))

    @classmethod
    def load_or_empty(cls, path: Path) -> "StrategyRegistry":
        path = Path(path)
        if not path.exists():
            return cls()
        return cls.load(path)

    def _transition(self, record: StrategyRecord, to_state: str, actor: str,
                    kind: str, payload: Dict[str, Any]) -> None:
        to_state = _require_state(to_state)
        if to_state == record.state:
            self._append_event(
                record, kind=kind, from_state=record.state, to_state=to_state,
                actor=actor, payload=payload,
            )
            return
        allowed = ALLOWED_TRANSITIONS.get(record.state, set())
        if to_state not in allowed:
            raise StrategyTransitionError(
                "illegal transition {} -> {}".format(record.state, to_state)
            )
        self._append_event(
            record, kind=kind, from_state=record.state, to_state=to_state,
            actor=actor, payload=payload,
        )
        record.state = to_state

    def _append_event(self, record: StrategyRecord, kind: str, from_state: str,
                      to_state: str, actor: str, payload: Dict[str, Any]) -> None:
        event = StrategyEvent(
            timestamp_utc=_utc_now(),
            strategy_id=record.opportunity.id,
            kind=kind,
            from_state=from_state,
            to_state=to_state,
            actor=actor,
            payload=_ensure_dict(payload),
        )
        record.events.append(event)
