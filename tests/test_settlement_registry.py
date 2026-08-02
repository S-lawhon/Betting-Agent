import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from src.settlement_registry import (
    SettlementRegistry, SettlementRegistryError, load_policy_state,
)


UTC = timezone.utc


def _payload(content=b"authoritative rules", decision="not_equivalent"):
    return {
        "documents": [{
            "id": "left_rules", "url": "https://example.test/rules.pdf",
            "expected_sha256": hashlib.sha256(content).hexdigest(),
        }],
        "policies": [{
            "id": "left_right", "document_ids": ["left_rules"],
            "decision": decision, "rationale": "different void windows",
            "dimensions": {"settlement": (
                "equivalent" if decision == "verified" else "different")},
            "approval": ({"approved_by": "reviewer",
                          "approved_at": "2026-08-02"}
                         if decision == "verified" else None),
        }],
    }


class _Response:
    def __init__(self, content):
        self.content = content
        self.headers = {"Content-Type": "application/pdf"}

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, content):
        self.content = content
        self.headers = {}

    def get(self, url, timeout):
        assert url == "https://example.test/rules.pdf"
        assert timeout > 0
        return _Response(self.content)


def test_project_registry_loads():
    registry = SettlementRegistry.load(Path("config/settlement_equivalence.yaml"))
    assert "gemini_kalshi_mlb_moneyline" in registry.policies
    assert registry.policies["gemini_kalshi_mlb_moneyline"]["decision"] == "not_equivalent"


def test_verified_policy_requires_explicit_approval():
    payload = _payload(decision="verified")
    payload["policies"][0]["approval"] = None

    with pytest.raises(SettlementRegistryError, match="explicit approval"):
        SettlementRegistry(payload)


def test_changed_hash_invalidates_policy():
    registry = SettlementRegistry(_payload())
    state = {"documents": {"left_rules": {
        "available": True, "hash_matches": False,
    }}}

    policy = registry.evaluate_policy("left_right", state)

    assert policy["status"] == "invalidated_document_change"
    assert policy["actionable_allowed"] is False
    assert policy["changed_documents"] == ["left_rules"]


def test_unavailable_document_fails_closed():
    policy = SettlementRegistry(_payload()).evaluate_policy(
        "left_right", {"documents": {}})

    assert policy["status"] == "unverified"
    assert policy["actionable_allowed"] is False


def test_refresh_archives_document_and_writes_healthy_manifest(tmp_path):
    content = b"authoritative rules"
    registry = SettlementRegistry(_payload(content))

    state = registry.refresh(
        tmp_path, session=_Session(content),
        now=datetime(2026, 8, 2, 12, tzinfo=UTC))

    assert state["status"] == "healthy"
    assert state["policies"]["left_right"]["status"] == "not_equivalent"
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    archive = Path(manifest["documents"]["left_rules"]["archive_path"])
    assert archive.read_bytes() == content


def test_load_policy_includes_registry_health(tmp_path):
    config = tmp_path / "registry.yaml"
    config.write_text(yaml.safe_dump(_payload()))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "status": "healthy", "generated_at": "2026-08-02T12:00:00Z",
        "documents": {"left_rules": {
            "available": True, "hash_matches": True,
        }},
    }))

    policy = load_policy_state(config, manifest, "left_right")

    assert policy["status"] == "not_equivalent"
    assert policy["registry_status"] == "healthy"
    assert policy["registry_generated_at"] == "2026-08-02T12:00:00Z"
