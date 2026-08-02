from __future__ import annotations

import json
from pathlib import Path

from scripts.run_research_execution import main


def test_cli_claim_complete_and_status(tmp_path, capsys):
    dispatches = tmp_path / "dispatches" / "strategy-scout"
    dispatches.mkdir(parents=True)
    (dispatches / "a1.json").write_text(json.dumps({
        "id": "d1", "assignment_id": "a1", "source_item_id": "s1",
        "assigned_agent": "strategy-scout", "priority": "high",
        "triage_score": 75, "created_at": "2026-08-03T11:00:00Z",
    }))
    state = tmp_path / "execution"
    dispositions = tmp_path / "dispositions"
    dispositions.mkdir()
    common = [
        "--dispatches-dir", str(dispatches.parent),
        "--state-dir", str(state),
        "--dispositions-dir", str(dispositions),
        "--now", "2026-08-03T12:00:00Z",
    ]
    assert main(common + ["claim", "--worker-id", "manual-pilot"]) == 0
    claimed = json.loads(capsys.readouterr().out)
    assert claimed["claimed"] is True
    disposition = tmp_path / "disposition.json"
    disposition.write_text(json.dumps({
        "assignment_id": "a1", "source_item_id": "s1",
        "decided_at": "2026-08-03T12:10:00Z", "decision": "reject",
        "reason_codes": ["no_mechanism"], "evidence_checked": ["source"],
        "research_minutes": 10,
    }))
    complete_args = common[:-2] + [
        "--now", "2026-08-03T12:10:00Z", "complete",
        "--claim", str(state / "claims/strategy-scout/a1.json"),
        "--disposition", str(disposition),
    ]
    assert main(complete_args) == 0
    capsys.readouterr()
    assert main(common + ["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["active"] == 0
    assert status["completed"] == 1
