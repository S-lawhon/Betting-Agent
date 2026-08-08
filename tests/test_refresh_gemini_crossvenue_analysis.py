from pathlib import Path

import yaml


def test_analysis_unit_is_networkless_bounded_and_hourly():
    service = Path(
        "scripts/systemd/gemini-crossvenue-analysis.service").read_text()
    timer = Path(
        "scripts/systemd/gemini-crossvenue-analysis.timer").read_text()

    assert "scripts.refresh_gemini_crossvenue_analysis" in service
    assert "MemoryMax=256M" in service
    assert "TimeoutStartSec=300" in service
    assert "RestrictAddressFamilies=AF_UNIX" in service
    assert "*:02:00 UTC" in timer
    assert "Persistent=true" in timer


def test_analysis_unit_and_output_are_monitored():
    registry = yaml.safe_load(Path("manager/registry.yaml").read_text())
    units = {row["id"] for row in registry["systemd_units"]}
    jobs = {row["id"]: row for row in registry["jobs"]}

    assert "gemini-crossvenue-analysis.timer" in units
    assert "gemini-crossvenue-analysis.service" in units
    job = jobs["gemini_crossvenue_analysis"]
    assert job["output"]["file"] == "data/gemini_crossvenue/analytics.json"
    assert job["output"]["max_stale_hours"] == 2.0
