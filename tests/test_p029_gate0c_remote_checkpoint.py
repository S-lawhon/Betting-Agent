import json
from datetime import datetime, timezone
from pathlib import Path

from scripts import run_p029_gate0c_remote_checkpoint as remote


UTC = timezone.utc


def _payload(verdict="EXTEND"):
    return {
        "pod": "P-029",
        "model_sha256": (
            "01411d863de04075a38f02b40b7e0c4a7e21463a96b42d8194e8ccd8325956af"
        ),
        "verdict": verdict,
        "progress": 500,
        "threshold": 500,
    }


def test_qualification_always_discloses_known_tape_gap():
    at = datetime(2026, 8, 23, 12, 30, tzinfo=UTC)
    result = remote.qualify(_payload(), at, "root@p029")

    gaps = result["data_qualification"]["known_tape_gaps"]
    assert gaps == remote.KNOWN_TAPE_GAPS
    assert gaps[0]["backfillable"] is False
    assert result["data_qualification"]["collection_complete"] is False
    assert result["retrieved_at_utc"] == "2026-08-23T12:30:00Z"


def test_persist_keeps_immutable_result_and_atomic_latest(tmp_path):
    at = datetime(2026, 8, 23, 12, 30, tzinfo=UTC)
    first = remote._persist(tmp_path, _payload("EXTEND"), at)
    second = remote._persist(tmp_path, _payload("STOP"), at)

    assert first.name == "checkpoint_2026-08-23T123000Z.json"
    assert second.name == "checkpoint_2026-08-23T123000Z_1.json"
    assert json.loads(first.read_text())["verdict"] == "EXTEND"
    assert json.loads((tmp_path / "latest.json").read_text())["verdict"] == "STOP"


def test_extension_run_skips_when_initial_verdict_was_terminal(tmp_path):
    (tmp_path / "latest.json").write_text(json.dumps(_payload("CONTINUE")))

    rc = remote.main([
        "--output-dir", str(tmp_path),
        "--now", "2026-08-30T12:30:00Z",
        "--key", str(tmp_path / "missing-key"),
    ])

    assert rc == 0
    assert len(list(tmp_path.glob("checkpoint_*.json"))) == 0


def test_timer_and_service_keep_registered_dates_and_frozen_reader_remote():
    root = Path(__file__).resolve().parent.parent
    timer = (root / "scripts/systemd/p029-gate0c-checkpoint.timer").read_text()
    service = (root / "scripts/systemd/p029-gate0c-checkpoint.service").read_text()

    assert "OnCalendar=2026-08-23 12:30:00 UTC" in timer
    assert "OnCalendar=2026-08-30 12:30:00 UTC" in timer
    assert "Persistent=true" in timer
    assert "run_p029_gate0c_remote_checkpoint.py" in service
    assert "P029_GATE0C_SSH_KEY=/root/.ssh/p029_probe" in service
    assert "ReadWritePaths=/opt/betting-pod-shop/data/p029_gate0c" in service
