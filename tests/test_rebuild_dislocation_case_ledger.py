import hashlib
import json
from datetime import datetime, timezone

import yaml

from scripts.rebuild_dislocation_case_ledger import rebuild


UTC = timezone.utc


def test_rebuild_is_deterministic_and_idempotent(tmp_path):
    observations = tmp_path / "observations"
    observations.mkdir()
    row = {
        "captured_at": "2026-08-02T12:00:00Z", "collection_id": "one",
        "match_key": "game", "paths": [{
            "direction": "home", "net_edge_usd": .04,
            "gross_edge_usd": .05,
        }],
    }
    (observations / "2026-08-02.jsonl").write_text(json.dumps(row) + "\n")
    content = b"rules"
    config = tmp_path / "settlement.yaml"
    config.write_text(yaml.safe_dump({
        "documents": [{
            "id": "rules", "url": "https://example.test/rules.pdf",
            "expected_sha256": hashlib.sha256(content).hexdigest(),
        }],
        "policies": [{
            "id": "policy", "decision": "not_equivalent",
            "document_ids": ["rules"], "dimensions": {
                "underlying": "equivalent", "postponement": "different",
            },
        }],
    }))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "status": "healthy", "documents": {"rules": {
            "available": True, "hash_matches": True,
        }},
    }))
    kwargs = {
        "observations_dir": observations,
        "database": tmp_path / "cases.sqlite3",
        "output": tmp_path / "cases.json",
        "settlement_config": config, "settlement_manifest": manifest,
        "pair_id": "pair", "policy_id": "policy",
        "now": datetime(2026, 8, 2, 12, 1, tzinfo=UTC),
        "window_days": 30, "threshold_usd": .03,
    }

    first = rebuild(**kwargs)
    second = rebuild(**kwargs)

    assert first == second
    assert first["observations_replayed"] == 1
    assert first["cases_replayed"] == 1
    assert first["lifetime_cases"] == 1
    assert first["settlement_basis"]["risk_dimensions"] == ["postponement"]
    assert json.loads((tmp_path / "cases.json").read_text()) == first
