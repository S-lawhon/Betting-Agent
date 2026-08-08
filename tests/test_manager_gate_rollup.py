from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from manager import collect, gate_rollup


UTC = timezone.utc
REQUIRED = ("p015", "p017", "p001", "p022", "p014", "throughput")


def _registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(
        "meta:\n  project_root: {}\n  local_root: {}\n"
        "services: []\nsystemd_units: []\njobs: []\nworkstreams: []\n"
        .format(tmp_path, tmp_path),
        encoding="utf-8")
    return path


def _payload(generated_at: datetime, *, status: str = "healthy") -> dict:
    payload = {
        "schema_version": 1,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "status": status,
        "faults": [],
    }
    for key in REQUIRED:
        payload[key] = {"available": True, "marker": key}
    return payload


def _write_rollup(root: Path, payload: dict) -> None:
    path = root / "manager" / "state" / "gate_rollup.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_collector_accepts_only_fresh_healthy_complete_rollup(tmp_path, monkeypatch):
    fixed = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(collect, "now", lambda: fixed)
    collector = collect.Collector(_registry(tmp_path), root=tmp_path,
                                  local_root=tmp_path)

    _write_rollup(tmp_path, _payload(fixed - timedelta(minutes=90)))
    result = collector.gate_rollup()
    assert result["available"] is True
    assert result["age_hours"] == 1.5
    assert result["payload"]["p015"]["marker"] == "p015"

    _write_rollup(tmp_path, _payload(fixed - timedelta(hours=3)))
    assert collector.gate_rollup()["available"] is False

    incomplete = _payload(fixed)
    incomplete.pop("p022")
    _write_rollup(tmp_path, incomplete)
    assert "missing blocks: p022" in collector.gate_rollup()["error"]

    _write_rollup(tmp_path, _payload(fixed, status="degraded"))
    assert collector.gate_rollup()["available"] is False


def test_run_uses_fresh_rollup_without_invoking_expensive_readers(
        tmp_path, monkeypatch):
    fixed = datetime.now(UTC)
    payload = _payload(fixed)
    _write_rollup(tmp_path, payload)
    collector = collect.Collector(_registry(tmp_path), root=tmp_path,
                                  local_root=tmp_path)

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("expensive reader ran despite a fresh rollup")

    for name in ("p015_gate", "p017_gate", "p001_gate", "p022_gate",
                 "p014_gate", "throughput"):
        monkeypatch.setattr(collector, name, should_not_run)

    simple = {
        "trade_activity": {}, "services": [], "systemd_unit_drift": {},
        "jobs": [], "maker_gate": {}, "p029_gate": {}, "mlb_props_gate": {},
        "p022_window": {}, "evmap_jobs": {}, "invariants": {},
        "recent_errors": {}, "work_today": {}, "research_operations": {},
    }
    for name, value in simple.items():
        monkeypatch.setattr(collector, name, lambda value=value: value)
    monkeypatch.setattr(collector, "workstreams", lambda _trade: [])

    snapshot = collector.run()
    for key in REQUIRED:
        assert snapshot[key]["marker"] == key
    assert snapshot["gate_rollup"]["available"] is True
    assert "payload" not in snapshot["gate_rollup"]


def test_rollup_degrades_when_a_reader_is_unavailable(tmp_path, monkeypatch):
    class FakeCollector:
        def __init__(self, *_args, **_kwargs):
            self.faults = []

        def throughput(self, _blocks):
            return {"available": True}

    for key, method in gate_rollup.GATE_READERS:
        setattr(FakeCollector, method,
                lambda self, key=key: {"available": key != "p022"})
    monkeypatch.setattr(gate_rollup, "Collector", FakeCollector)

    payload = gate_rollup.build(tmp_path, _registry(tmp_path))
    assert payload["status"] == "degraded"
    assert payload["unavailable"] == ["p022"]


def test_rollup_unit_is_bounded_hourly_and_monitored():
    service = Path("scripts/systemd/manager-gate-rollup.service").read_text()
    timer = Path("scripts/systemd/manager-gate-rollup.timer").read_text()
    registry = yaml.safe_load(Path("manager/registry.yaml").read_text())

    assert "manager.gate_rollup" in service
    assert "MemoryMax=256M" in service
    assert "TimeoutStartSec=300" in service
    assert "RestrictAddressFamilies=AF_UNIX" in service
    assert "*:07:00 UTC" in timer
    assert "Persistent=true" in timer

    units = {row["id"] for row in registry["systemd_units"]}
    jobs = {row["id"]: row for row in registry["jobs"]}
    assert "manager-gate-rollup.timer" in units
    assert "manager-gate-rollup.service" in units
    assert jobs["manager_gate_rollup"]["output"] == {
        "file": "manager/state/gate_rollup.json", "max_stale_hours": 2,
    }
