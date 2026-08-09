"""Provider-neutral, budgeted worker for research dispatch packets.

The default path is a read-only dry run: select and validate the next packet,
build the invocation contract, and report what would happen. Real invocation
requires both an execution-enabled worker and an explicit ``execute=True``
call. This module has no trading imports or order authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence

from src.research_execution import (
    ResearchClaim,
    ResearchExecutionError,
    claim_next,
    complete_claim,
    mark_claim_invoked,
    mark_claim_phase_invoked,
    preview_next,
    release_claim,
)
from src.research_outcomes import ResearchDisposition
from src.strategy_orchestration import OpportunityCard


UTC = timezone.utc
SCHEMA_VERSION = 1
ALLOWED_ARTIFACTS = {
    "literature-scout": {"research_disposition", "hypothesis_brief"},
    "social-scout": {"research_disposition", "hypothesis_brief"},
    "strategy-scout": {
        "opportunity_card", "scout_rejection", "research_disposition"},
}
HYPOTHESIS_KEYS = {
    "title", "mechanism", "null_hypothesis", "decisive_test",
    "evidence", "execution_eligibility",
}


def _trusted_provider_diagnostic(stderr: str) -> Optional[str]:
    """Extract the already-sanitized Codex adapter error, if present."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines or not lines[-1].startswith("codex research provider error:"):
        return None
    diagnostic = lines[-1]
    for pattern in (
        r"(?i)(bearer\s+)(\S+)",
        r"(?i)(authorization\s*[:=]\s*)(\S+)",
        r"(?i)((?:access|refresh|id)[_-]?token\s*[:=]\s*)(\S+)",
        r"(sk-[A-Za-z0-9_-]+)",
    ):
        diagnostic = re.sub(pattern, "[REDACTED]", diagnostic)
    return diagnostic[:600]


class ResearchWorkerError(RuntimeError):
    """A worker action was unsafe, invalid, or outside its budget."""


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class WorkerLimits:
    max_minutes_per_task: int = 45
    timeout_seconds: int = 900
    max_input_tokens_per_task: int = 50_000
    max_output_tokens_per_task: int = 8_000
    max_cost_usd_per_task: float = 2.00
    max_cost_usd_per_day: float = 10.00
    max_attempts_per_day: int = 24
    max_output_bytes: int = 250_000

    def __post_init__(self) -> None:
        numeric = asdict(self)
        if any(value <= 0 for value in numeric.values()):
            raise ValueError("all worker limits must be positive")
        if self.max_cost_usd_per_task > self.max_cost_usd_per_day:
            raise ValueError("per-task cost cannot exceed daily cost")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScreeningLimits:
    timeout_seconds: int = 180
    max_input_tokens: int = 25_000
    max_output_tokens: int = 1_000
    max_cost_usd: float = 0.25
    max_output_bytes: int = 32_000

    def __post_init__(self) -> None:
        if any(value <= 0 for value in asdict(self).values()):
            raise ValueError("all screening limits must be positive")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScreeningDecision:
    decision: str
    reason_codes: list[str]
    evidence_checked: list[str]
    research_minutes: int
    mechanism: str = ""
    cheapest_decisive_test: str = ""
    confidence: float = 0.0
    recheck_after: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.decision not in {"reject", "defer", "deep_research"}:
            raise ValueError(f"invalid screening decision: {self.decision}")
        if not self.reason_codes or not self.evidence_checked:
            raise ValueError("screening requires reasons and evidence checked")
        if self.research_minutes < 0:
            raise ValueError("screening research_minutes cannot be negative")
        if not 0 <= self.confidence <= 1:
            raise ValueError("screening confidence must be between 0 and 1")
        if self.decision == "defer" and not self.recheck_after:
            raise ValueError("defer screening requires recheck_after")
        if self.decision == "defer":
            try:
                parsed = datetime.fromisoformat(
                    str(self.recheck_after).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(
                    "defer screening recheck_after must be ISO-8601") from exc
            if parsed.tzinfo is None:
                raise ValueError(
                    "defer screening recheck_after must include a timezone")
        if self.decision == "deep_research" and (
                not self.mechanism or not self.cheapest_decisive_test):
            raise ValueError(
                "deep_research screening requires mechanism and decisive test")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScreeningDecision":
        return cls(
            decision=str(payload["decision"]),
            reason_codes=[str(value) for value in
                          payload.get("reason_codes") or []],
            evidence_checked=[str(value) for value in
                              payload.get("evidence_checked") or []],
            research_minutes=int(payload.get("research_minutes") or 0),
            mechanism=str(payload.get("mechanism") or ""),
            cheapest_decisive_test=str(
                payload.get("cheapest_decisive_test") or ""),
            confidence=float(payload.get("confidence") or 0),
            recheck_after=payload.get("recheck_after"),
            notes=str(payload.get("notes") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int
    output_tokens: int
    cost_usd: float

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0 or self.cost_usd < 0:
            raise ValueError("provider usage cannot be negative")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model: str
    output: Mapping[str, Any]
    usage: ProviderUsage


class ResearchProvider(Protocol):
    name: str
    model: str

    def invoke(self, request: Mapping[str, Any], *, timeout_seconds: int
               ) -> ProviderResult: ...


class CommandProvider:
    """JSON-over-stdin provider adapter. Never invokes a shell.

    The command must emit one JSON object containing ``output`` and measured
    ``usage``. Only explicitly allow-listed environment variables are passed,
    preventing betting credentials from leaking into a model subprocess.
    """

    def __init__(self, *, argv: Sequence[str], name: str, model: str,
                 cwd: Path, pass_env: Sequence[str] = (),
                 billing_mode: str = "metered_api") -> None:
        if not argv or not all(isinstance(value, str) and value for value in argv):
            raise ValueError("provider argv must contain non-empty strings")
        if not name.strip() or not model.strip():
            raise ValueError("provider name and model are required")
        self.argv = list(argv)
        self.name = name.strip()
        self.model = model.strip()
        self.cwd = Path(cwd)
        self.pass_env = tuple(pass_env)
        self.billing_mode = billing_mode.strip() or "metered_api"

    def invoke(self, request: Mapping[str, Any], *, timeout_seconds: int
               ) -> ProviderResult:
        env = {key: value for key in ("PATH", "LANG", "LC_ALL", "TMPDIR")
               if (value := os.getenv(key)) is not None}
        for key in self.pass_env:
            value = os.getenv(key)
            if value is not None:
                env[key] = value
        try:
            completed = subprocess.run(
                self.argv, input=json.dumps(request), text=True,
                capture_output=True, timeout=timeout_seconds,
                cwd=self.cwd, env=env, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ResearchWorkerError(
                f"provider timed out after {timeout_seconds}s") from exc
        if completed.returncode != 0:
            stderr_hash = hashlib.sha256(completed.stderr.encode()).hexdigest()
            diagnostic = _trusted_provider_diagnostic(completed.stderr)
            raise ResearchWorkerError(
                "provider exited {}; {}stderr_sha256={}".format(
                    completed.returncode,
                    "diagnostic={!r}; ".format(diagnostic) if diagnostic else "",
                    stderr_hash))
        try:
            envelope = json.loads(completed.stdout)
            usage = envelope["usage"]
            output = envelope["output"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ResearchWorkerError(
                "provider must return JSON with output and measured usage") from exc
        if not isinstance(output, Mapping) or not isinstance(usage, Mapping):
            raise ResearchWorkerError("provider output and usage must be objects")
        return ProviderResult(
            provider=self.name, model=self.model, output=output,
            usage=ProviderUsage(
                input_tokens=int(usage["input_tokens"]),
                output_tokens=int(usage["output_tokens"]),
                cost_usd=float(usage["cost_usd"])),
        )


class ResearchAgentWorker:
    def __init__(
        self, *, root: Path, dispatches_dir: Path, state_dir: Path,
        dispositions_dir: Path, worker_id: str, limits: WorkerLimits,
        provider: Optional[ResearchProvider] = None,
        screening_provider: Optional[ResearchProvider] = None,
        screening_limits: Optional[ScreeningLimits] = None,
        screening_enabled: bool = False,
        execution_enabled: bool = False,
        allowed_agents: Sequence[str] = tuple(ALLOWED_ARTIFACTS),
        clock=None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        self.root = Path(root).resolve()
        self.dispatches_dir = Path(dispatches_dir)
        self.state_dir = Path(state_dir)
        self.dispositions_dir = Path(dispositions_dir)
        self.worker_id = worker_id.strip()
        self.limits = limits
        self.provider = provider
        self.screening_provider = screening_provider
        self.screening_limits = screening_limits or ScreeningLimits()
        self.screening_enabled = bool(screening_enabled)
        self.execution_enabled = bool(execution_enabled)
        self.allowed_agents = tuple(allowed_agents)
        self._clock = clock or (lambda: datetime.now(UTC))

    def _uses_screening(self, packet: Mapping[str, Any]) -> bool:
        return (self.screening_enabled
                and str(packet.get("assigned_agent") or "") == "strategy-scout")

    def run_once(self, *, execute: bool = False,
                 assignment_id: Optional[str] = None,
                 persist_status: bool = True) -> Dict[str, Any]:
        now = self._clock()
        target_assignment_id = str(assignment_id or "").strip() or None
        candidate = preview_next(
            dispatches_dir=self.dispatches_dir, state_dir=self.state_dir,
            dispositions_dir=self.dispositions_dir,
            assignment_id=target_assignment_id,
            agents=set(self.allowed_agents), now=now)
        if not candidate:
            result = self._result(
                "blocked" if target_assignment_id else "idle", now, plan=None,
                error=(f"target assignment is not available: "
                       f"{target_assignment_id}" if target_assignment_id else None))
            return self._finish(result, persist_status)
        packet_path, packet = candidate
        try:
            plan = self._plan(packet_path, packet, now)
        except (ResearchWorkerError, ValueError) as exc:
            result = self._result("blocked", now, plan=None, error=str(exc),
                                  packet=packet)
            return self._finish(result, persist_status)
        if not execute:
            return self._finish(self._result("dry_run", now, plan=plan),
                                persist_status)
        if not self.execution_enabled:
            raise ResearchWorkerError(
                "execution requires runtime execution_enabled=true")
        if self.provider is None:
            raise ResearchWorkerError("execution requires a configured provider")
        uses_screening = self._uses_screening(packet)
        if uses_screening and self.screening_provider is None:
            raise ResearchWorkerError(
                "screening execution requires a configured screening provider")

        claim = claim_next(
            dispatches_dir=self.dispatches_dir, state_dir=self.state_dir,
            dispositions_dir=self.dispositions_dir, worker_id=self.worker_id,
            assignment_id=target_assignment_id,
            agents=set(self.allowed_agents),
            now=now, lease_minutes=max(5, min(480,
                (self.limits.timeout_seconds
                 + (self.screening_limits.timeout_seconds
                    if uses_screening else 0) + 299) // 60)))
        if not claim:
            return self._finish(self._result(
                "blocked" if target_assignment_id else "idle", now, plan=None,
                error=(f"target assignment became unavailable: "
                       f"{target_assignment_id}" if target_assignment_id else None)),
                persist_status)
        claim_path = (self.state_dir / "claims" / claim.assigned_agent
                      / f"{claim.assignment_id}.json")
        budget = self._invocation_budget(packet)
        plan["request"]["claim_context"] = {
            "claimed_at": claim.claimed_at,
            "decided_at_requirement": (
                "disposition.decided_at must be an ISO-8601 UTC timestamp "
                "greater than or equal to claimed_at"
            ),
        }
        if uses_screening:
            plan["screening_request"]["claim_context"] = {
                "claimed_at": claim.claimed_at,
            }
        started = time.monotonic()
        provider_result: Optional[ProviderResult] = None
        invocation_recorded = False
        invocations: list[Dict[str, Any]] = []
        try:
            if uses_screening:
                screen_budget = self._screening_budget()
                assert self.screening_provider is not None
                mark_claim_phase_invoked(
                    claim_path=claim_path, state_dir=self.state_dir,
                    provider=self.screening_provider.name,
                    model=self.screening_provider.model,
                    budget=screen_budget, phase="screening", now=now)
                invocation_recorded = True
                invocations.append(self._invocation_attempt(
                    "screening", self.screening_provider))
                screening_result = self.screening_provider.invoke(
                    plan["screening_request"],
                    timeout_seconds=self.screening_limits.timeout_seconds)
                invocations[-1] = self._invocation_record(
                    "screening", screening_result)
                screening = self._validate_screening_result(
                    screening_result, claim, packet)
                self._write_screening(
                    claim, screening, screening_result, now, packet)
                if screening.decision != "deep_research":
                    disposition, artifact_type, artifact = (
                        self._screening_completion(screening, claim))
                    artifact_record = self._write_artifact(
                        claim, artifact_type, artifact, screening_result, now)
                    complete_claim(
                        claim_path=claim_path,
                        disposition_payload=disposition.to_dict(),
                        state_dir=self.state_dir,
                        dispositions_dir=self.dispositions_dir,
                        now=self._clock())
                    elapsed = time.monotonic() - started
                    run = self._run_record(
                        "completed", claim, screening_result, now, elapsed,
                        artifact_record=artifact_record,
                        model_invocation_tracked=True,
                        invocations=invocations,
                        terminal_stage="screening")
                    self._write_run(run, now)
                    return self._finish(self._result(
                        "completed", self._clock(), plan=plan,
                        claim=claim.to_dict(), run=run), persist_status)
                mark_claim_phase_invoked(
                    claim_path=claim_path, state_dir=self.state_dir,
                    provider=self.provider.name, model=self.provider.model,
                    budget=budget, phase="deep_research", now=self._clock())
                plan["request"]["screening_context"] = screening.to_dict()
                invocations.append(self._invocation_attempt(
                    "deep_research", self.provider))
            else:
                mark_claim_invoked(
                    claim_path=claim_path, state_dir=self.state_dir,
                    provider=self.provider.name, model=self.provider.model,
                    budget=budget, now=now)
                invocation_recorded = True
                invocations.append(self._invocation_attempt(
                    "research", self.provider))
            provider_result = self.provider.invoke(
                plan["request"], timeout_seconds=self.limits.timeout_seconds)
            invocations[-1] = self._invocation_record(
                "deep_research" if uses_screening else "research",
                provider_result)
            elapsed = time.monotonic() - started
            disposition, artifact_type, artifact = self._validate_result(
                provider_result, claim, packet)
            artifact_record = self._write_artifact(
                claim, artifact_type, artifact, provider_result, now)
            complete_claim(
                claim_path=claim_path,
                disposition_payload=disposition.to_dict(),
                state_dir=self.state_dir,
                dispositions_dir=self.dispositions_dir, now=self._clock())
            run = self._run_record(
                "completed", claim, provider_result, now, elapsed,
                artifact_record=artifact_record,
                model_invocation_tracked=invocation_recorded,
                invocations=invocations,
                terminal_stage=("deep_research" if uses_screening
                                else "research"))
            self._write_run(run, now)
            return self._finish(self._result(
                "completed", self._clock(), plan=plan,
                claim=claim.to_dict(), run=run), persist_status)
        except Exception as exc:
            elapsed = time.monotonic() - started
            if claim_path.exists():
                try:
                    release_claim(
                        claim_path=claim_path, state_dir=self.state_dir,
                        reason=f"worker failure: {type(exc).__name__}: {exc}"[:500],
                        now=self._clock())
                except (OSError, ResearchExecutionError):
                    pass
            run = self._run_record(
                "failed", claim, provider_result, now, elapsed, error=str(exc),
                model_invocation_tracked=invocation_recorded,
                invocations=invocations)
            self._write_run(run, now)
            return self._finish(self._result(
                "failed", self._clock(), plan=plan,
                claim=claim.to_dict(), run=run, error=str(exc)), persist_status)

    def _plan(self, packet_path: Path, packet: Mapping[str, Any],
              now: datetime) -> Dict[str, Any]:
        agent = str(packet.get("assigned_agent") or "")
        if agent not in self.allowed_agents or agent not in ALLOWED_ARTIFACTS:
            raise ResearchWorkerError(f"agent is not allow-listed: {agent}")
        minutes = int(packet.get("research_budget_minutes") or 0)
        if minutes <= 0 or minutes > self.limits.max_minutes_per_task:
            raise ResearchWorkerError(
                f"packet budget {minutes}m exceeds worker limit "
                f"{self.limits.max_minutes_per_task}m")
        daily_usage = self._daily_usage(now)
        uses_screening = self._uses_screening(packet)
        reserved_attempts = 2 if uses_screening else 1
        if (daily_usage["attempts"] + reserved_attempts
                > self.limits.max_attempts_per_day):
            raise ResearchWorkerError(
                "daily model-attempt limit: reservation would exceed it")
        spent = daily_usage["cost_usd"]
        reserved_cost = self.limits.max_cost_usd_per_task + (
            self.screening_limits.max_cost_usd if uses_screening else 0)
        if spent + reserved_cost > self.limits.max_cost_usd_per_day:
            raise ResearchWorkerError(
                "daily cost reservation would exceed the hard limit")
        prompt_path = (self.root / ".claude" / "agents" / f"{agent}.md").resolve()
        prompt_root = (self.root / ".claude" / "agents").resolve()
        if prompt_path.parent != prompt_root or not prompt_path.is_file():
            raise ResearchWorkerError(f"agent prompt is unavailable: {agent}")
        request = {
            "schema_version": SCHEMA_VERSION,
            "mode": "research_only",
            "assignment_id": str(packet.get("assignment_id") or ""),
            "source_item_id": str(packet.get("source_item_id") or ""),
            "assigned_agent": agent,
            "agent_instructions": prompt_path.read_text(encoding="utf-8"),
            "dispatch_packet": dict(packet),
            "output_contract": {
                "required": ["disposition", "artifact_type", "artifact"],
                "allowed_artifact_types": sorted(ALLOWED_ARTIFACTS[agent]),
                "disposition_schema": "src.research_outcomes.ResearchDisposition",
            },
            "budget": self._invocation_budget(packet),
            "safety": {
                "trading_execution_allowed": False,
                "queue_mutation_allowed": False,
                "service_mutation_allowed": False,
                "secrets_allowed_in_output": False,
            },
        }
        plan = {
            "packet_path": str(packet_path),
            "assignment_id": request["assignment_id"],
            "assigned_agent": agent,
            "provider_configured": self.provider is not None,
            "screening_enabled": uses_screening,
            "screening_provider_configured": (
                self.screening_provider is not None if uses_screening else None),
            "stages": (["screening", "deep_research"] if uses_screening
                       else ["research"]),
            "reserved_attempts": reserved_attempts,
            "reserved_cost_usd": reserved_cost,
            "billing_mode": (
                getattr(self.provider, "billing_mode", "metered_api")
                if self.provider else "none"),
            "execution_enabled": self.execution_enabled,
            "daily_usage": self._daily_usage(now),
            "request": request,
        }
        if uses_screening:
            plan["screening_request"] = {
                "schema_version": SCHEMA_VERSION,
                "mode": "research_screening_only",
                "assignment_id": request["assignment_id"],
                "source_item_id": request["source_item_id"],
                "assigned_agent": agent,
                "dispatch_packet": dict(packet),
                "screening_rules": [
                    "Reject only when the mechanism is absent, duplicated, "
                    "illegal, or falsified by the supplied/public evidence.",
                    "Defer ONLY when the deciding fact DOES NOT EXIST YET and "
                    "needs time to come into being -- a market not yet listed, "
                    "an event not yet settled, a filing whose text is not yet "
                    "public. recheck_after must be an ISO-8601 UTC timestamp "
                    "for when that fact will exist.",
                    "If the missing input is public and FETCHABLE NOW -- a "
                    "linked filing document, contract terms, current prices, "
                    "depth, fees, a published dataset -- that is deep_research, "
                    "NOT defer. You cannot browse in this phase; the deep "
                    "research phase can. Deferring a fetchable fact spends the "
                    "assignment and resolves nothing.",
                    "Use only supplied evidence in screening; do not browse. "
                    "Read dispatch_packet.market_evidence before claiming any "
                    "evidence class is unavailable -- measured fees, quotes, "
                    "contract terms and filing documents are often already "
                    "there, with the timestamp they were measured at.",
                    "Choose deep_research only with a concrete market "
                    "mechanism and a cheapest decisive test.",
                    "This phase cannot advance or create an opportunity card.",
                ],
                "output_contract": {
                    "schema": "config/research_screen_output.schema.json",
                    "decisions": ["reject", "defer", "deep_research"],
                    "advance_allowed": False,
                },
                "budget": self._screening_budget(),
                "safety": dict(request["safety"]),
            }
        return plan

    def _invocation_budget(self, packet: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "research_minutes": min(
                int(packet.get("research_budget_minutes") or 0),
                self.limits.max_minutes_per_task),
            "timeout_seconds": self.limits.timeout_seconds,
            "max_input_tokens": self.limits.max_input_tokens_per_task,
            "max_output_tokens": self.limits.max_output_tokens_per_task,
            "max_cost_usd": self.limits.max_cost_usd_per_task,
        }

    def _screening_budget(self) -> Dict[str, Any]:
        return self.screening_limits.to_dict()

    @staticmethod
    def _invocation_record(phase: str, result: ProviderResult) -> Dict[str, Any]:
        return {
            "phase": phase, "provider": result.provider,
            "model": result.model, "usage": result.usage.to_dict(),
            "usage_measured": True,
        }

    @staticmethod
    def _invocation_attempt(phase: str, provider: ResearchProvider) -> Dict[str, Any]:
        return {
            "phase": phase, "provider": provider.name,
            "model": provider.model, "usage": {},
            "usage_measured": False,
        }

    def _validate_screening_result(
        self, result: ProviderResult, claim: ResearchClaim,
        packet: Mapping[str, Any],
    ) -> ScreeningDecision:
        assert self.screening_provider is not None
        if (result.provider != self.screening_provider.name
                or result.model != self.screening_provider.model):
            raise ResearchWorkerError(
                "screening provider identity changed during invocation")
        usage = result.usage
        if usage.input_tokens > self.screening_limits.max_input_tokens:
            raise ResearchWorkerError("screening exceeded input-token limit")
        if usage.output_tokens > self.screening_limits.max_output_tokens:
            raise ResearchWorkerError("screening exceeded output-token limit")
        if usage.cost_usd > self.screening_limits.max_cost_usd:
            raise ResearchWorkerError("screening exceeded cost limit")
        serialized = json.dumps(result.output, sort_keys=True)
        if len(serialized.encode()) > self.screening_limits.max_output_bytes:
            raise ResearchWorkerError("screening output exceeded byte limit")
        try:
            screening = ScreeningDecision.from_dict(result.output)
        except (KeyError, TypeError, ValueError) as exc:
            raise ResearchWorkerError(f"invalid screening decision: {exc}") from exc
        if screening.research_minutes > min(
                int(packet.get("research_budget_minutes") or 0),
                self.limits.max_minutes_per_task):
            raise ResearchWorkerError(
                "screening research minutes exceed assignment budget")
        return screening

    def _write_screening(
        self, claim: ResearchClaim, screening: ScreeningDecision,
        result: ProviderResult, now: datetime,
        packet: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = screening.to_dict()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        path = (self.state_dir / "screenings" / claim.assigned_agent
                / f"{claim.assignment_id}.json")
        record = {
            "schema_version": SCHEMA_VERSION, "created_at": _iso(now),
            "claim_id": claim.claim_id,
            "assignment_id": claim.assignment_id,
            "source_item_id": claim.source_item_id,
            "assigned_agent": claim.assigned_agent,
            "provider": result.provider, "model": result.model,
            "screening_sha256": digest, "screening": payload,
            # What the screen was actually holding when it decided. A rule
            # telling it not to defer a fetchable fact is unenforceable, so
            # record the context instead: a defer taken while measured
            # evidence was in hand is then visible rather than inferred.
            "evidence_context": self._evidence_context(packet),
            "safety": {
                "authorizes_execution": False,
                "authorizes_advancement": False,
            },
        }
        _write_atomic(path, record)
        return {"path": str(path), "sha256": digest}

    @staticmethod
    def _evidence_context(packet: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        evidence = (packet or {}).get("market_evidence") or {}
        contract = (packet or {}).get("completion_contract") or {}
        supplied = [str(value) for value in
                    (contract.get("supplied_evidence") or [])]
        return {
            "evidence_status": str(evidence.get("status") or "none"),
            "evidence_source": str(evidence.get("source") or ""),
            "measured_at": evidence.get("measured_at"),
            "supplied_evidence": supplied,
            "decided_with_measured_evidence": bool(
                supplied and str(evidence.get("status") or "") in
                {"measured", "partial"}),
        }

    def _screening_completion(
        self, screening: ScreeningDecision, claim: ResearchClaim,
    ) -> tuple[ResearchDisposition, str, Dict[str, Any]]:
        if screening.decision == "deep_research":
            raise ResearchWorkerError(
                "deep_research screening cannot directly complete a claim")
        disposition = ResearchDisposition(
            assignment_id=claim.assignment_id,
            source_item_id=claim.source_item_id,
            decided_at=_iso(self._clock()),
            decision=screening.decision,
            reason_codes=list(screening.reason_codes),
            evidence_checked=list(screening.evidence_checked),
            research_minutes=screening.research_minutes,
            recheck_after=screening.recheck_after,
            notes=("screening_only: " + screening.notes).strip(),
        )
        if disposition.decision == "reject":
            return disposition, "scout_rejection", {
                "assignment_id": claim.assignment_id,
                "reason_codes": list(disposition.reason_codes),
                "evidence_checked": list(disposition.evidence_checked),
            }
        return disposition, "research_disposition", disposition.to_dict()

    def _validate_result(self, result: ProviderResult, claim: ResearchClaim,
                         packet: Mapping[str, Any]):
        if result.provider != self.provider.name or result.model != self.provider.model:
            raise ResearchWorkerError("provider identity changed during invocation")
        usage = result.usage
        if usage.input_tokens > self.limits.max_input_tokens_per_task:
            raise ResearchWorkerError("provider exceeded input-token limit")
        if usage.output_tokens > self.limits.max_output_tokens_per_task:
            raise ResearchWorkerError("provider exceeded output-token limit")
        if usage.cost_usd > self.limits.max_cost_usd_per_task:
            raise ResearchWorkerError("provider exceeded per-task cost limit")
        serialized = json.dumps(result.output, sort_keys=True)
        if len(serialized.encode()) > self.limits.max_output_bytes:
            raise ResearchWorkerError("provider output exceeded byte limit")
        try:
            disposition = ResearchDisposition.from_dict(result.output["disposition"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ResearchWorkerError(f"invalid disposition: {exc}") from exc
        if disposition.assignment_id != claim.assignment_id:
            raise ResearchWorkerError("output assignment_id does not match claim")
        if disposition.source_item_id != claim.source_item_id:
            raise ResearchWorkerError("output source_item_id does not match claim")
        try:
            decided_at = datetime.fromisoformat(
                disposition.decided_at.replace("Z", "+00:00"))
            claimed_at = datetime.fromisoformat(
                claim.claimed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ResearchWorkerError(
                "output decided_at is not valid ISO-8601") from exc
        if (decided_at.tzinfo is None or claimed_at.tzinfo is None
                or decided_at < claimed_at):
            raise ResearchWorkerError("output disposition predates the claim")
        if not disposition.reason_codes or not disposition.evidence_checked:
            raise ResearchWorkerError(
                "disposition requires reason_codes and evidence_checked")
        minutes = int(packet.get("research_budget_minutes") or 0)
        if disposition.research_minutes > min(
                minutes, self.limits.max_minutes_per_task):
            raise ResearchWorkerError("reported research minutes exceed budget")
        artifact_type = str(result.output.get("artifact_type") or "")
        artifact = result.output.get("artifact")
        allowed = ALLOWED_ARTIFACTS.get(claim.assigned_agent, set())
        if artifact_type not in allowed or not isinstance(artifact, Mapping):
            raise ResearchWorkerError(
                f"artifact type {artifact_type!r} is invalid for {claim.assigned_agent}")
        self._validate_artifact(artifact_type, artifact, claim, disposition)
        return disposition, artifact_type, dict(artifact)

    @staticmethod
    def _validate_artifact(artifact_type: str, artifact: Mapping[str, Any],
                           claim: ResearchClaim,
                           disposition: ResearchDisposition) -> None:
        if artifact_type == "research_disposition":
            parsed = ResearchDisposition.from_dict(artifact)
            if parsed.to_dict() != disposition.to_dict():
                raise ResearchWorkerError("artifact disposition differs from completion")
        elif artifact_type == "hypothesis_brief":
            missing = sorted(HYPOTHESIS_KEYS - set(artifact))
            if missing:
                raise ResearchWorkerError(
                    "hypothesis brief missing: {}".format(", ".join(missing)))
            if disposition.decision != "advance":
                raise ResearchWorkerError("hypothesis brief requires advance disposition")
        elif artifact_type == "opportunity_card":
            card = OpportunityCard.from_dict(dict(artifact))
            if card.source_agent != "scout":
                raise ResearchWorkerError("opportunity source_agent must be scout")
            if disposition.decision != "advance":
                raise ResearchWorkerError("opportunity card requires advance disposition")
            if disposition.opportunity_id != card.id:
                raise ResearchWorkerError(
                    "disposition opportunity_id must match opportunity card")
        elif artifact_type == "scout_rejection":
            if disposition.decision != "reject":
                raise ResearchWorkerError("scout rejection requires reject disposition")
            if not artifact.get("reason_codes") or not artifact.get("evidence_checked"):
                raise ResearchWorkerError(
                    "scout rejection requires reasons and evidence")
        if artifact.get("assignment_id") not in (None, claim.assignment_id):
            raise ResearchWorkerError("artifact assignment_id does not match claim")

    def _write_artifact(self, claim: ResearchClaim, artifact_type: str,
                        artifact: Mapping[str, Any], result: ProviderResult,
                        now: datetime) -> Dict[str, Any]:
        canonical = json.dumps(dict(artifact), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        path = (self.state_dir / "artifacts" / claim.assigned_agent
                / f"{claim.assignment_id}.json")
        record = {
            "schema_version": SCHEMA_VERSION,
            "created_at": _iso(now), "claim_id": claim.claim_id,
            "assignment_id": claim.assignment_id,
            "source_item_id": claim.source_item_id,
            "assigned_agent": claim.assigned_agent,
            "artifact_type": artifact_type, "artifact_sha256": digest,
            "provider": result.provider, "model": result.model,
            "artifact": dict(artifact),
            "safety": {"authorizes_execution": False},
        }
        _write_atomic(path, record)
        return {"path": str(path), "sha256": digest,
                "artifact_type": artifact_type}

    def _daily_usage(self, now: datetime) -> Dict[str, Any]:
        directory = self.state_dir / "runs" / now.astimezone(UTC).date().isoformat()
        costs, inputs, outputs, attempts = 0.0, 0, 0, 0
        for path in directory.glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                usage = row.get("usage") or {}
                costs += float(usage.get("cost_usd") or 0)
                inputs += int(usage.get("input_tokens") or 0)
                outputs += int(usage.get("output_tokens") or 0)
                invocations = row.get("invocations")
                attempts += (len(invocations) if isinstance(invocations, list)
                             else 1)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return {"date": now.astimezone(UTC).date().isoformat(),
                "attempts": attempts, "cost_usd": round(costs, 6),
                "input_tokens": inputs, "output_tokens": outputs,
                "hard_attempt_limit": self.limits.max_attempts_per_day,
                "hard_cost_limit_usd": self.limits.max_cost_usd_per_day}

    def _run_record(self, status: str, claim: ResearchClaim,
                    result: Optional[ProviderResult], started_at: datetime,
                    elapsed_seconds: float, artifact_record=None,
                    error: Optional[str] = None,
                    model_invocation_tracked: bool = False,
                    invocations: Optional[Sequence[Mapping[str, Any]]] = None,
                    terminal_stage: Optional[str] = None) -> Dict[str, Any]:
        invocation_rows = [dict(item) for item in (invocations or [])]
        if invocation_rows:
            aggregate_usage = {
                "input_tokens": sum(int((item.get("usage") or {}).get(
                    "input_tokens") or 0) for item in invocation_rows),
                "output_tokens": sum(int((item.get("usage") or {}).get(
                    "output_tokens") or 0) for item in invocation_rows),
                "cost_usd": round(sum(float((item.get("usage") or {}).get(
                    "cost_usd") or 0) for item in invocation_rows), 6),
            }
        else:
            aggregate_usage = result.usage.to_dict() if result else {}
        return {
            "schema_version": SCHEMA_VERSION, "status": status,
            "started_at": _iso(started_at), "finished_at": _iso(self._clock()),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "claim_id": claim.claim_id, "assignment_id": claim.assignment_id,
            "assigned_agent": claim.assigned_agent,
            "provider": result.provider if result else (
                self.provider.name if self.provider else None),
            "model": result.model if result else (
                self.provider.model if self.provider else None),
            "billing_mode": (
                getattr(self.provider, "billing_mode", "metered_api")
                if self.provider else "none"),
            "usage": aggregate_usage,
            "invocations": invocation_rows,
            "terminal_stage": terminal_stage,
            "model_invocation_tracked": model_invocation_tracked,
            "artifact": artifact_record, "error": error,
            "safety": {"authorizes_execution": False},
        }

    def _write_run(self, run: Mapping[str, Any], now: datetime) -> None:
        path = (self.state_dir / "runs" / now.astimezone(UTC).date().isoformat()
                / f"{run['claim_id']}.json")
        _write_atomic(path, run)

    def _result(self, status: str, now: datetime, *, plan,
                packet: Optional[Mapping[str, Any]] = None,
                claim=None, run=None, error=None) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "generated_at": _iso(now),
            "worker_id": self.worker_id, "status": status,
            "mode": "execute" if self.execution_enabled else "dry_run",
            "provider_configured": self.provider is not None,
            "screening_enabled": self.screening_enabled,
            "screening_provider_configured": self.screening_provider is not None,
            "billing_mode": (
                getattr(self.provider, "billing_mode", "metered_api")
                if self.provider else "none"),
            "invocation_tracking_available": True,
            "plan": self._safe_plan(plan),
            "assignment_id": ((plan or {}).get("assignment_id")
                              or str((packet or {}).get("assignment_id") or "")
                              or None),
            "claim": claim, "run": run, "error": error,
            "daily_usage": self._daily_usage(now),
            "limits": self.limits.to_dict(),
            "safety": {
                "trading_execution_allowed": False,
                "model_invoked": bool(
                    (run or {}).get("model_invocation_tracked")),
            },
        }

    def _finish(self, result: Dict[str, Any], persist: bool) -> Dict[str, Any]:
        if persist:
            _write_atomic(self.state_dir / "worker_status.json", result)
        return result

    @staticmethod
    def _safe_plan(plan: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
        """Persist plan metadata without copying prompts or source text."""
        if not plan:
            return None
        request = plan.get("request") or {}
        screening_request = plan.get("screening_request") or {}
        canonical = json.dumps(request, sort_keys=True, separators=(",", ":"))
        screening_canonical = json.dumps(
            screening_request, sort_keys=True, separators=(",", ":"))
        return {
            key: value for key, value in plan.items()
            if key not in {"request", "screening_request"}
        } | {
            "request_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "request_bytes": len(canonical.encode()),
            "budget": dict(request.get("budget") or {}),
            "safety": dict(request.get("safety") or {}),
            "screening_request_sha256": (
                hashlib.sha256(screening_canonical.encode()).hexdigest()
                if screening_request else None),
            "screening_request_bytes": (
                len(screening_canonical.encode()) if screening_request else 0),
            "screening_budget": dict(
                screening_request.get("budget") or {}),
        }
