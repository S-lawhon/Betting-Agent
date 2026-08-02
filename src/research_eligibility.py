"""Fail-closed venue/product eligibility policy for research proposals.

This registry does not alter existing execution authorization.  It controls
whether *new research output* may describe a venue/product as executable.
Unknown, stale, pending, and reference-only decisions are never executable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

import yaml


VALID_STATUSES = {"approved", "reference_only", "pending_review", "prohibited"}


class EligibilityError(ValueError):
    """Invalid registry or attempt to use a non-executable product."""


@dataclass(frozen=True)
class EligibilityDecision:
    venue_id: str
    product_family: str
    status: str
    research_allowed: bool
    execution_allowed: bool
    rationale: str
    evidence: List[str]
    verified_at: Optional[str]
    expires_at: Optional[str]
    stale: bool = False
    source_scope: str = "venue"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _as_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


class EligibilityRegistry:
    def __init__(self, payload: Mapping[str, Any]):
        self.payload = dict(payload)
        self.defaults = dict(self.payload.get("defaults") or {})
        venues = self.payload.get("venues")
        if not isinstance(venues, list):
            raise EligibilityError("registry venues must be a list")
        self.venues: Dict[str, Dict[str, Any]] = {}
        for venue in venues:
            self._validate_entry(venue, scope="venue")
            venue_id = str(venue["id"])
            if venue_id in self.venues:
                raise EligibilityError(f"duplicate venue id: {venue_id}")
            self.venues[venue_id] = dict(venue)

    @classmethod
    def load(cls, path: Union[Path, str]) -> "EligibilityRegistry":
        payload = yaml.safe_load(Path(path).read_text()) or {}
        return cls(payload)

    @staticmethod
    def _validate_entry(entry: Any, *, scope: str) -> None:
        if not isinstance(entry, Mapping):
            raise EligibilityError(f"{scope} entry must be a mapping")
        if scope == "venue" and not entry.get("id"):
            raise EligibilityError("venue entry missing id")
        status = entry.get("status")
        if status not in VALID_STATUSES:
            raise EligibilityError(f"invalid {scope} status: {status!r}")
        if bool(entry.get("execution_allowed")) and status != "approved":
            raise EligibilityError(
                f"{scope} execution_allowed requires status=approved"
            )
        if bool(entry.get("execution_allowed")):
            required = ("verified_at", "expires_at", "approved_by", "evidence")
            missing = [key for key in required if not entry.get(key)]
            if missing:
                raise EligibilityError(
                    f"approved executable {scope} missing: {', '.join(missing)}"
                )
        products = entry.get("products", [])
        if products is not None and not isinstance(products, list):
            raise EligibilityError(f"{scope} products must be a list")
        for product in products or []:
            EligibilityRegistry._validate_entry(product, scope="product")
            if not product.get("family"):
                raise EligibilityError("product entry missing family")

    def decision(
        self,
        venue_id: str,
        product_family: str = "*",
        *,
        as_of: Optional[date] = None,
    ) -> EligibilityDecision:
        as_of = as_of or datetime.now(timezone.utc).date()
        venue = self.venues.get(venue_id)
        if venue is None:
            return EligibilityDecision(
                venue_id=venue_id,
                product_family=product_family,
                status=str(self.defaults.get("unknown_status", "pending_review")),
                research_allowed=bool(self.defaults.get("research_allowed", True)),
                execution_allowed=False,
                rationale="venue/product absent from eligibility registry",
                evidence=[], verified_at=None, expires_at=None,
                stale=True, source_scope="default",
            )

        selected: Mapping[str, Any] = venue
        source_scope = "venue"
        for product in venue.get("products") or []:
            if product.get("family") in {product_family, "*"}:
                selected = dict(venue) | dict(product)
                source_scope = "product"
                if product.get("family") == product_family:
                    break

        expiry = _as_date(selected.get("expires_at"))
        stale = expiry is None or expiry < as_of
        status = str(selected.get("status"))
        execution = bool(selected.get("execution_allowed"))
        if stale and status != "prohibited":
            status = "pending_review"
            execution = False
        if status != "approved":
            execution = False
        return EligibilityDecision(
            venue_id=venue_id,
            product_family=product_family,
            status=status,
            research_allowed=bool(selected.get("research_allowed", True)),
            execution_allowed=execution,
            rationale=str(selected.get("rationale") or ""),
            evidence=[str(item) for item in selected.get("evidence") or []],
            verified_at=(str(selected.get("verified_at"))
                         if selected.get("verified_at") else None),
            expires_at=(str(selected.get("expires_at"))
                        if selected.get("expires_at") else None),
            stale=stale,
            source_scope=source_scope,
        )

    def require_execution(self, venue_id: str, product_family: str = "*") -> None:
        decision = self.decision(venue_id, product_family)
        if not decision.execution_allowed:
            raise EligibilityError(
                f"execution not approved for {venue_id}/{product_family}: "
                f"status={decision.status}, stale={decision.stale}"
            )

    def audit(self, *, as_of: Optional[date] = None) -> Dict[str, Any]:
        decisions = [self.decision(venue_id, as_of=as_of)
                     for venue_id in sorted(self.venues)]
        return {
            "venue_count": len(decisions),
            "execution_approved": sum(d.execution_allowed for d in decisions),
            "stale": [d.venue_id for d in decisions if d.stale],
            "prohibited": [d.venue_id for d in decisions
                           if d.status == "prohibited"],
            "decisions": [d.to_dict() for d in decisions],
        }
