"""Versioned, fail-closed settlement-document and equivalence registry."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import requests
import yaml


UTC = timezone.utc
VALID_DECISIONS = {"verified", "not_equivalent", "unverified"}


class SettlementRegistryError(ValueError):
    pass


def _iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace(
        "+00:00", "Z")


def _text_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


class SettlementRegistry:
    def __init__(self, payload: Mapping[str, Any]):
        self.payload = dict(payload)
        documents = payload.get("documents")
        policies = payload.get("policies")
        if not isinstance(documents, list) or not isinstance(policies, list):
            raise SettlementRegistryError("documents and policies must be lists")
        self.documents: Dict[str, Dict[str, Any]] = {}
        for document in documents:
            if not isinstance(document, Mapping) or not document.get("id"):
                raise SettlementRegistryError("document missing id")
            doc_id = str(document["id"])
            expected = str(document.get("expected_sha256") or "")
            if len(expected) != 64:
                raise SettlementRegistryError(f"invalid expected hash: {doc_id}")
            if doc_id in self.documents:
                raise SettlementRegistryError(f"duplicate document: {doc_id}")
            self.documents[doc_id] = dict(document)
        self.policies: Dict[str, Dict[str, Any]] = {}
        for policy in policies:
            if not isinstance(policy, Mapping) or not policy.get("id"):
                raise SettlementRegistryError("policy missing id")
            policy_id = str(policy["id"])
            decision = policy.get("decision")
            if decision not in VALID_DECISIONS:
                raise SettlementRegistryError(
                    f"invalid policy decision {policy_id}: {decision}")
            missing = set(policy.get("document_ids") or []) - set(self.documents)
            if missing:
                raise SettlementRegistryError(
                    f"policy {policy_id} references missing documents: {sorted(missing)}")
            dimensions = policy.get("dimensions") or {}
            if not isinstance(dimensions, Mapping) or not dimensions:
                raise SettlementRegistryError(f"policy {policy_id} has no dimensions")
            if decision == "verified":
                approval = policy.get("approval") or {}
                if not approval.get("approved_by") or not approval.get("approved_at"):
                    raise SettlementRegistryError(
                        f"verified policy {policy_id} requires explicit approval")
                if any(value != "equivalent" for value in dimensions.values()):
                    raise SettlementRegistryError(
                        f"verified policy {policy_id} has non-equivalent dimensions")
            self.policies[policy_id] = dict(policy)

    @classmethod
    def load(cls, path: Path) -> "SettlementRegistry":
        return cls(yaml.safe_load(path.read_text()) or {})

    def evaluate_policy(self, policy_id: str,
                        document_state: Mapping[str, Any]) -> Dict[str, Any]:
        policy = self.policies.get(policy_id)
        if policy is None:
            return {"id": policy_id, "status": "unverified",
                    "actionable_allowed": False,
                    "reason": "policy absent from settlement registry"}
        docs = document_state.get("documents") or {}
        changed = []
        unavailable = []
        for doc_id in policy.get("document_ids") or []:
            row = docs.get(doc_id) or {}
            if not row.get("available"):
                unavailable.append(doc_id)
            elif not row.get("hash_matches"):
                changed.append(doc_id)
        if changed:
            status = "invalidated_document_change"
            reason = "authoritative document hash changed: " + ", ".join(changed)
        elif unavailable:
            status = "unverified"
            reason = "authoritative documents unavailable: " + ", ".join(unavailable)
        else:
            status = str(policy["decision"])
            reason = str(policy.get("rationale") or "")
        return {
            "id": policy_id, "status": status,
            "actionable_allowed": status == "verified",
            "reason": reason,
            "reviewed_at": _text_date(policy.get("reviewed_at")),
            "dimensions": dict(policy.get("dimensions") or {}),
            "document_ids": list(policy.get("document_ids") or []),
            "changed_documents": changed,
            "unavailable_documents": unavailable,
        }

    def refresh(self, output_dir: Path, *, session: Optional[requests.Session] = None,
                now: Optional[datetime] = None, timeout: float = 30.0) -> Dict[str, Any]:
        session = session or requests.Session()
        session.headers.update({"User-Agent": "betting-pod-shop/settlement-registry"})
        rows: Dict[str, Dict[str, Any]] = {}
        changes = []
        for doc_id, document in self.documents.items():
            row: Dict[str, Any] = {
                "url": document["url"],
                "expected_sha256": document["expected_sha256"],
                "available": False, "hash_matches": False,
            }
            try:
                response = session.get(document["url"], timeout=timeout)
                response.raise_for_status()
                content = response.content
                observed = hashlib.sha256(content).hexdigest()
                row.update({
                    "available": True, "observed_sha256": observed,
                    "hash_matches": observed == document["expected_sha256"],
                    "bytes": len(content),
                    "content_type": response.headers.get("Content-Type"),
                })
                archive = output_dir / "documents" / doc_id / f"{observed}.pdf"
                archive.parent.mkdir(parents=True, exist_ok=True)
                if not archive.exists():
                    temporary = archive.with_name(f".{archive.name}.{os.getpid()}.tmp")
                    temporary.write_bytes(content)
                    os.replace(temporary, archive)
                row["archive_path"] = str(archive)
                if not row["hash_matches"]:
                    changes.append(doc_id)
            except requests.RequestException as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows[doc_id] = row
        state: Dict[str, Any] = {
            "schema_version": 1, "generated_at": _iso(now),
            "status": "changed" if changes else (
                "degraded" if any(not row["available"] for row in rows.values())
                else "healthy"),
            "documents": rows, "changed_documents": changes,
        }
        state["policies"] = {
            policy_id: self.evaluate_policy(policy_id, state)
            for policy_id in sorted(self.policies)
        }
        _atomic_json(output_dir / "manifest.json", state)
        return state


def load_policy_state(config_path: Path, manifest_path: Path,
                      policy_id: str) -> Dict[str, Any]:
    registry = SettlementRegistry.load(config_path)
    try:
        state = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        state = {"documents": {}}
    policy = registry.evaluate_policy(policy_id, state)
    policy["registry_status"] = state.get("status", "missing")
    policy["registry_generated_at"] = state.get("generated_at")
    return policy
