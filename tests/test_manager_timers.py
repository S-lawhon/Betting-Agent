import json
from pathlib import Path

from manager import brief, checks


def test_systemd_unit_drift_becomes_a_warning():
    findings = checks.check_systemd_units({
        "systemd_units": {
            "available": True,
            "units": [{
                "unit": "manager-collect.timer",
                "source": "/repo/manager-collect.timer",
                "installed": "/etc/systemd/system/manager-collect.timer",
                "state": "differs",
                "severity": "warn",
            }],
        },
    })

    assert len(findings) == 1
    assert findings[0].key == "systemd.manager-collect.timer.differs"
    assert findings[0].severity == "warn"


def test_brief_run_writes_timer_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(brief, "HERE", tmp_path)
    brief.record_brief_run("ok", "all clear", emailed=True)

    row = json.loads(
        (tmp_path / "state" / "brief_runs.jsonl").read_text().strip())
    assert row["status"] == "ok"
    assert row["headline"] == "all clear"
    assert row["emailed"] is True
    assert row["timestamp_utc"].endswith("Z")


def test_brief_unit_can_write_its_heartbeat():
    service = Path("scripts/systemd/manager-brief.service").read_text()
    assert "ReadWritePaths=/opt/betting-pod-shop/manager/state" in service


def test_manager_timers_are_installable_but_not_part_of_code_deploy():
    for name in ("collect", "alert", "brief"):
        timer = Path("scripts/systemd/manager-{}.timer".format(name)).read_text()
        assert "[Install]" in timer
        assert "WantedBy=timers.target" in timer
    deploy = Path("scripts/deploy.sh").read_text()
    assert "systemctl enable --now manager-collect.timer" not in deploy
