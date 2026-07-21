"""Manager collector: local-vs-droplet job routing.

The bug these cover: manager/refresh.py always runs collect.py ON THE DROPLET,
where a Mac-hosted job's output path (/Users/samlawhon/...) cannot exist. The
collector stat()'d it anyway and reported `exists: False` — a confident "does
not exist" for a job that may be running fine. R-EV-MAP Build 2 has a 30-day
evidence gate, so the brief was structurally unable to see evidence accrue.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

MANAGER = Path(__file__).resolve().parent.parent / "manager"
sys.path.insert(0, str(MANAGER))

collect = pytest.importorskip("collect")
checks = pytest.importorskip("checks")


def write_registry(tmp_path: Path, local_root: str, project_root: str) -> Path:
    reg = tmp_path / "registry.yaml"
    reg.write_text(
        "meta:\n"
        "  version: test\n"
        "  project_root: {}\n"
        "  local_root: {}\n"
        "services: []\n"
        "workstreams: []\n"
        "jobs:\n"
        "  - id: droplet_job\n"
        "    host: droplet\n"
        "    schedule: '0 6 * * *'\n"
        "    description: droplet-side\n"
        "    output:\n"
        "      file: data/droplet_out.jsonl\n"
        "      max_stale_hours: 48\n"
        "  - id: weather_paper_maker\n"
        "    host: mac\n"
        "    schedule: '13,43 7-19 * * *'\n"
        "    description: Weather paper maker quotes (Build 2 evidence)\n"
        "    output:\n"
        "      file: kalshi-ev-map/data/paper_quotes.parquet\n"
        "      max_stale_hours: 4\n"
        "      root: local\n"
        .format(project_root, local_root),
        encoding="utf-8",
    )
    return reg


def test_is_local_job_reads_both_markers():
    assert collect.is_local_job({"host": "mac"})
    assert collect.is_local_job({"output": {"root": "local"}})
    assert not collect.is_local_job({"host": "droplet"})
    assert not collect.is_local_job({"host": "droplet", "output": {"root": "project"}})


def test_local_job_on_droplet_is_uncheckable_not_missing(tmp_path):
    """The core regression: absent local_root must NOT yield exists=False."""
    project = tmp_path / "opt" / "betting-pod-shop"
    (project / "data").mkdir(parents=True)
    (project / "data" / "droplet_out.jsonl").write_text("{}\n", encoding="utf-8")
    missing_local = tmp_path / "Users" / "samlawhon" / "nope"

    reg = write_registry(tmp_path, str(missing_local), str(project))
    col = collect.Collector(reg, root=project, local_root=missing_local)
    jobs = {j["id"]: j for j in col.jobs()}

    mac = jobs["weather_paper_maker"]
    assert mac["state"] == "uncheckable"
    assert mac["exists"] is None, "must not claim the output is missing"
    assert mac["stale"] is None, "must not claim freshness either"
    assert mac["measurable"] is False
    assert "uncheckable_reason" in mac

    # The droplet job is unaffected — it is measured normally.
    assert jobs["droplet_job"]["state"] == "measured"
    assert jobs["droplet_job"]["exists"] is True


def test_local_job_is_measured_when_local_root_exists(tmp_path):
    project = tmp_path / "opt" / "betting-pod-shop"
    (project / "data").mkdir(parents=True)
    local = tmp_path / "mac" / "checkout"
    out = local / "kalshi-ev-map" / "data"
    out.mkdir(parents=True)
    (out / "paper_quotes.parquet").write_bytes(b"PAR1")

    reg = write_registry(tmp_path, str(local), str(project))
    col = collect.Collector(reg, root=project, local_root=local)
    mac = {j["id"]: j for j in col.jobs()}["weather_paper_maker"]

    assert mac["state"] == "measured"
    assert mac["exists"] is True
    assert mac["measurable"] is True
    assert mac["stale"] is False, "freshly written file is not stale"


def test_stale_local_job_is_reported_stale(tmp_path):
    """An admitted gap must not swallow a genuinely late job."""
    import os
    import time

    project = tmp_path / "opt" / "betting-pod-shop"
    (project / "data").mkdir(parents=True)
    local = tmp_path / "mac" / "checkout"
    out = local / "kalshi-ev-map" / "data"
    out.mkdir(parents=True)
    stale_file = out / "paper_quotes.parquet"
    stale_file.write_bytes(b"PAR1")
    old = time.time() - 40 * 3600  # threshold is 4h
    os.utime(stale_file, (old, old))

    reg = write_registry(tmp_path, str(local), str(project))
    col = collect.Collector(reg, root=project, local_root=local)
    mac = {j["id"]: j for j in col.jobs()}["weather_paper_maker"]

    assert mac["state"] == "measured"
    assert mac["stale"] is True
    assert mac["age_hours"] > 4


def test_merge_local_jobs_overlays_droplet_snapshot(tmp_path):
    """refresh.py's fix: droplet snapshot + Mac-measured local jobs."""
    project = tmp_path / "opt" / "betting-pod-shop"
    (project / "data").mkdir(parents=True)
    local = tmp_path / "mac" / "checkout"
    out = local / "kalshi-ev-map" / "data"
    out.mkdir(parents=True)
    (out / "paper_quotes.parquet").write_bytes(b"PAR1")

    reg = write_registry(tmp_path, str(local), str(project))

    # What the droplet would have produced: local job uncheckable there.
    droplet = collect.Collector(reg, root=project,
                                local_root=tmp_path / "does_not_exist")
    snapshot = {"jobs": droplet.jobs()}
    assert {j["id"]: j for j in snapshot["jobs"]}[
        "weather_paper_maker"]["state"] == "uncheckable"

    merged = collect.merge_local_jobs(snapshot, registry_path=reg, local_root=local)
    jobs = {j["id"]: j for j in merged["jobs"]}

    assert jobs["weather_paper_maker"]["state"] == "measured"
    assert jobs["weather_paper_maker"]["exists"] is True
    assert jobs["droplet_job"]["state"] == "measured", "droplet job preserved"
    assert len(merged["jobs"]) == 2, "merge must not duplicate rows"
    assert "local_jobs_merged_at" in merged


def test_merge_never_downgrades_a_real_measurement(tmp_path):
    """If the Mac can't see it either, the remote record stands unchanged."""
    project = tmp_path / "opt" / "betting-pod-shop"
    (project / "data").mkdir(parents=True)
    reg = write_registry(tmp_path, str(tmp_path / "gone"), str(project))

    droplet = collect.Collector(reg, root=project, local_root=tmp_path / "gone")
    snapshot = {"jobs": droplet.jobs()}
    merged = collect.merge_local_jobs(snapshot, registry_path=reg,
                                      local_root=tmp_path / "gone")
    jobs = {j["id"]: j for j in merged["jobs"]}
    assert jobs["weather_paper_maker"]["state"] == "uncheckable"
    assert jobs["weather_paper_maker"]["exists"] is None


def test_checks_surfaces_uncheckable_as_explicit_finding():
    """A false negative is worse than an admitted gap — so admit it."""
    snap = {"jobs": [{
        "id": "weather_paper_maker",
        "host": "mac",
        "schedule": "13,43 7-19 * * *",
        "description": "Weather paper maker quotes",
        "severity": "warn",
        "state": "uncheckable",
        "exists": None,
        "stale": None,
        "measurable": False,
        "uncheckable_reason": "collector is on the droplet",
    }]}
    found = checks.check_jobs(snap)
    keys = [f.key for f in found]
    assert "job.weather_paper_maker.uncheckable" in keys
    finding = [f for f in found if f.key.endswith(".uncheckable")][0]
    assert finding.severity == "info", "an unknown is not an incident"
    assert "unknown" in finding.title.lower()


def test_snapshot_is_json_serialisable(tmp_path):
    """write_state uses json.dumps; None-valued fields must round-trip."""
    project = tmp_path / "opt" / "betting-pod-shop"
    (project / "data").mkdir(parents=True)
    reg = write_registry(tmp_path, str(tmp_path / "gone"), str(project))
    col = collect.Collector(reg, root=project, local_root=tmp_path / "gone")
    text = json.dumps({"jobs": col.jobs()}, default=collect.jsonable)
    assert '"state": "uncheckable"' in text
    assert '"exists": null' in text
