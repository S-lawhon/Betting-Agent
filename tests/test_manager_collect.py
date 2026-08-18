"""Manager collector: local-vs-droplet job routing.

The bug these cover: manager/refresh.py always runs collect.py ON THE DROPLET,
where a Mac-hosted job's output path (/Users/samlawhon/...) cannot exist. The
collector stat()'d it anyway and reported `exists: False` — a confident "does
not exist" for a job that may be running fine. R-EV-MAP Build 2 has a 30-day
evidence gate, so the brief was structurally unable to see evidence accrue.
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
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


def test_trade_activity_continues_into_rotated_archive_and_dedupes_carry(
        tmp_path, monkeypatch):
    """A size rotation must not zero the rolling 24h settlement totals."""
    project = tmp_path / "opt" / "betting-pod-shop"
    logs = project / "data" / "trade_logs"
    logs.mkdir(parents=True)
    local = tmp_path / "local"
    local.mkdir()
    reg = write_registry(tmp_path, str(local), str(project))

    fixed = datetime(2026, 8, 18, 22, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(collect, "now", lambda: fixed)

    def row(hours_ago, action, fingerprint, pnl=None):
        result = {
            "timestamp_utc": (fixed - timedelta(hours=hours_ago)).isoformat(),
            "action": action,
            "pod_id": "P-017",
            "fingerprint": fingerprint,
        }
        if pnl is not None:
            result["pnl_usd"] = pnl
        return json.dumps(result, sort_keys=True)

    old = row(25, "SKIPPED_EDGE", "old")
    placed_settled = row(3, "PLACED", "settled-trade")
    won = row(2, "WIN", "settled-trade", 7.25)
    carried = row(1, "PLACED", "open-trade")
    archive = logs / "trade_log.archive_20260818_214837.jsonl.gz"
    with gzip.open(archive, "wt", encoding="utf-8") as fh:
        fh.write("\n".join((old, placed_settled, won, carried)) + "\n")

    # rotate_active_log carries open PLACED rows forward with their original
    # timestamps, so the new active file is not necessarily chronological.
    ancient_carry = row(48, "PLACED", "ancient-open")
    (logs / "trade_log.jsonl").write_text(
        ancient_carry + "\n" + carried + "\n", encoding="utf-8")

    result = collect.Collector(reg, root=project, local_root=local).trade_activity()

    assert result["complete"] is True
    assert result["actions"] == {"PLACED": 2, "WIN": 1}
    assert result["per_pod"]["P-017"] == {
        "placed": 2, "settled": 1, "won": 1, "lost": 0, "void": 0,
    }
    assert result["realized_pnl_24h"] == 7.25
    assert result["last_row_ts"] == "2026-08-18T21:00:00Z"


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


def test_job_record_can_expose_latest_heartbeat_telemetry(tmp_path):
    project = tmp_path / "opt" / "betting-pod-shop"
    heartbeat = project / "data" / "p029_heartbeat" / "p029_shadow.jsonl"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text(
        json.dumps({
            "timestamp_utc": "2099-01-01T00:00:00Z",
            "shadow_memory_utilization_pct": 95.8,
        }) + "\n",
        encoding="utf-8",
    )
    local = tmp_path / "local"
    local.mkdir()
    reg = write_registry(tmp_path, str(local), str(project))
    col = collect.Collector(reg, root=project, local_root=local)
    rec = col.job_record({
        "id": "p029_shadow",
        "host": "droplet",
        "output": {
            "file": "data/p029_heartbeat/p029_shadow.jsonl",
            "max_stale_hours": 3,
            "inspect_last_row": True,
            "inspect_recent_rows": 48,
        },
    })
    assert rec["last_row"]["shadow_memory_utilization_pct"] == 95.8
    assert rec["recent_rows"] == [rec["last_row"]]


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
    # Mac-hosted: refresh.py from the Mac IS the fix, so the advice stands.
    assert "refresh.py" in finding.detail


def test_uncheckable_third_host_does_not_advise_refresh():
    """refresh.py runs the collectors on the Mac and the droplet. A job on a
    third host (P-029's own VPS) is uncheckable from BOTH, so telling Sam to
    run refresh.py is advice that cannot work — its real measurement is the
    out-of-band check the uncheckable_reason already names."""
    snap = {"jobs": [{
        "id": "p029_shadow",
        "host": "p029",
        "schedule": "continuous (p029-shadow.service, Restart=always)",
        "description": "P-029 Phase 0 public shadow logger",
        "severity": "warn",
        "state": "uncheckable",
        "exists": None,
        "stale": None,
        "measurable": False,
        "uncheckable_reason": ("job runs on 'p029', a host neither collector "
                               "can stat; this collector is on 'droplet'. "
                               "Measured by the p029-daily-health-check "
                               "scheduled task instead."),
    }]}
    found = checks.check_jobs(snap)
    finding = [f for f in found if f.key == "job.p029_shadow.uncheckable"][0]
    assert finding.severity == "info"
    assert "refresh.py" not in finding.detail
    # The admitted-gap framing survives, and the detail still points at the
    # declared out-of-band measurement instead.
    assert "admitted gap" in finding.detail
    assert "out-of-band" in finding.detail
    assert "p029-daily-health-check" in finding.detail


def test_p029_memory_pressure_is_warn_but_process_loss_is_critical():
    job = {
        "id": "p029_shadow",
        "measurable": True,
        "stale": False,
        "last_row": {
            "shadow_memory_utilization_pct": 95.8,
            "shadow_memory_current_bytes": 771_575_808,
            "shadow_memory_anon_bytes": 400_000_000,
            "shadow_memory_file_bytes": 371_575_808,
            "shadow_memory_anon_utilization_pct": 49.7,
            "shadow_memory_peak_bytes": 805_810_176,
            "shadow_memory_swap_bytes": 21_106_688,
            "shadow_memory_max_events": 42,
            "shadow_main_pid": 224158,
            "shadow_restart_delta": 1,
            "shadow_oom_kill_events": 0,
        },
    }
    found = checks.check_jobs({"jobs": [job]})
    by_key = {finding.key: finding for finding in found}
    assert by_key["job.p029_shadow.memory_pressure"].severity == "warn"
    assert by_key["job.p029_shadow.process_loss"].severity == "critical"


def test_p029_memory_warning_includes_bounded_pressure_trend():
    def row(ts, utilization, events, swap, backlog):
        return {
            "timestamp_utc": ts,
            "shadow_main_pid": 224158,
            "shadow_memory_utilization_pct": utilization,
            "shadow_memory_swap_bytes": swap,
            "shadow_memory_anon_utilization_pct": 49.7,
            "shadow_memory_max_events": events,
            "shadow_due_in_zone": backlog,
            "shadow_restart_delta": 0,
            "shadow_oom_kill_events": 0,
        }

    recent = [
        row("2026-08-07T12:00:00Z", 94.0, 1_000, 10_000_000, 189),
        row("2026-08-07T13:00:00Z", 95.0, 1_200, 20_000_000, 80),
        row("2026-08-07T14:00:00Z", 95.8, 1_500, 21_000_000, 50),
    ]
    job = {
        "id": "p029_shadow", "measurable": True, "stale": False,
        "last_row": recent[-1] | {
            "shadow_memory_current_bytes": 771_575_808,
            "shadow_memory_anon_bytes": 400_000_000,
            "shadow_memory_file_bytes": 371_575_808,
            "shadow_memory_anon_utilization_pct": 49.7,
            "shadow_memory_peak_bytes": 805_810_176,
        },
        "recent_rows": recent,
    }

    finding = {f.key: f for f in checks.check_jobs({"jobs": [job]})}[
        "job.p029_shadow.memory_pressure"]

    assert "3 samples / 2.0h" in finding.detail
    assert "memory.max +500 (250.0/h)" in finding.detail
    assert "backlog peak/latest 189/50" in finding.detail
    assert finding.value["trend"]["pid_stable"] is True


def test_p029_pressure_trend_ignores_legacy_rows_without_pid():
    recent = [
        {
            "timestamp_utc": "2026-08-07T12:00:00Z",
            "shadow_memory_max_bytes": 805_306_368,
        },
        {
            "timestamp_utc": "2026-08-07T13:00:00Z",
            "shadow_main_pid": 224158,
            "shadow_memory_utilization_pct": 95.8,
        },
        {
            "timestamp_utc": "2026-08-07T14:00:00Z",
            "shadow_main_pid": 224158,
            "shadow_memory_utilization_pct": 95.9,
        },
    ]

    trend = checks._p029_pressure_trend(recent)

    assert trend["samples"] == 2
    assert trend["pid_stable"] is True


def test_p029_healthy_memory_emits_no_pressure_findings():
    job = {
        "id": "p029_shadow",
        "measurable": True,
        "stale": False,
        "last_row": {
            "shadow_memory_utilization_pct": 70.0,
            "shadow_restart_delta": 0,
            "shadow_oom_kill_events": 0,
        },
    }
    assert not checks.check_jobs({"jobs": [job]})


def test_p029_reclaimable_file_cache_at_cap_emits_no_pressure_finding():
    job = {
        "id": "p029_shadow", "measurable": True, "stale": False,
        "last_row": {
            "shadow_memory_utilization_pct": 99.9,
            "shadow_memory_current_bytes": 804_000_000,
            "shadow_memory_anon_bytes": 50_000_000,
            "shadow_memory_file_bytes": 754_000_000,
            "shadow_memory_anon_utilization_pct": 6.2,
            "shadow_memory_swap_bytes": 0,
            "shadow_restart_delta": 0,
            "shadow_oom_kill_events": 0,
        },
    }
    assert not checks.check_jobs({"jobs": [job]})


def test_p029_remote_checkpoint_is_preferred_and_hash_checked(tmp_path):
    project = tmp_path / "opt" / "betting-pod-shop"
    output = project / "data" / "p029_gate0c" / "latest.json"
    output.parent.mkdir(parents=True)
    payload = {
        "pod": "P-029",
        "model_sha256": (
            "01411d863de04075a38f02b40b7e0c4a7e21463a96b42d8194e8ccd8325956af"
        ),
        "verdict": "EXTEND",
        "progress": 500,
    }
    output.write_text(json.dumps(payload), encoding="utf-8")
    local = tmp_path / "local"
    local.mkdir()
    reg = write_registry(tmp_path, str(local), str(project))
    col = collect.Collector(reg, root=project, local_root=local)

    result = col.p029_gate()

    assert result["available"] is True
    assert result["checkpoint"] == payload
    payload["model_sha256"] = "wrong"
    output.write_text(json.dumps(payload), encoding="utf-8")
    assert col.p029_gate()["available"] is False


def test_p029_gate_findings_surface_terminal_verdicts_and_gap():
    base = {
        "progress": 500,
        "data_qualification": {"known_tape_gaps": [{"backfillable": False}]},
    }
    for verdict, key, severity in (
        ("CONTINUE", "gate.P-029.continue", "action"),
        ("STOP", "gate.P-029.stop", "warn"),
        ("INCONCLUSIVE", "gate.P-029.inconclusive", "warn"),
    ):
        snap = {"p029": {"checkpoint": base | {"verdict": verdict}}}
        found = {f.key: f for f in checks.check_gates(snap, {})}
        assert found[key].severity == severity
        assert "1 known non-backfillable tape gap" in found[key].detail


def test_measurable_hosts_mirror_stays_in_sync():
    """checks.py mirrors collect.py's MEASURABLE_HOSTS instead of importing it
    (collect.py hard-requires PyYAML; checks.py deliberately does not). This
    test is the coupling."""
    assert checks.MEASURABLE_HOSTS == collect.MEASURABLE_HOSTS


def _init_repo(path: Path) -> None:
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "Tester"],
        ["config", "commit.gpgsign", "false"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True,
                       capture_output=True, text=True)


def _commit(path: Path, message: str) -> None:
    import subprocess
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message],
                   check=True, capture_output=True, text=True)


def test_work_today_reads_commits_and_research_areas(tmp_path, monkeypatch):
    """The daily brief's work summary: commits + touched research dirs from git."""
    import shutil
    if not shutil.which("git"):
        pytest.skip("git not available")
    monkeypatch.setattr(collect, "MIRROR_PATH", tmp_path / "no-mirror")

    project = tmp_path / "repo"
    _init_repo(project)
    (project / "foo_research").mkdir()
    (project / "foo_research" / "REPORT.md").write_text("v", encoding="utf-8")
    (project / "src").mkdir()
    (project / "src" / "pod.py").write_text("x = 1\n", encoding="utf-8")
    _commit(project, "P-999 research: KILL at Phase-1")
    # An uncommitted research file — visible only on a live working tree.
    (project / "foo_research" / "scratch.md").write_text("wip", encoding="utf-8")

    reg = write_registry(tmp_path, str(project), str(project))
    col = collect.Collector(reg, root=project, local_root=project)
    w = col.work_today()

    assert w["available"] is True
    assert w["is_mirror"] is False, "a live working tree must not be treated as a mirror"
    assert w["commit_count"] == 1
    assert w["commits"][0]["subject"] == "P-999 research: KILL at Phase-1"
    assert "foo_research" in w["research_areas"]
    assert w["uncommitted_research_files"] == 1
    assert col.faults == [], "a clean git read must record no fault"


def test_work_today_window_excludes_old_commits(tmp_path, monkeypatch):
    import shutil
    import subprocess
    if not shutil.which("git"):
        pytest.skip("git not available")
    monkeypatch.setattr(collect, "MIRROR_PATH", tmp_path / "no-mirror")

    project = tmp_path / "repo"
    _init_repo(project)
    (project / "a.txt").write_text("1", encoding="utf-8")
    # Backdate the commit well outside a 24h window.
    env = {"GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
           "GIT_COMMITTER_DATE": "2020-01-01T00:00:00"}
    subprocess.run(["git", "-C", str(project), "add", "-A"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(project), "commit", "-q", "-m", "ancient"],
                   check=True, capture_output=True, text=True,
                   env={**__import__("os").environ, **env})

    reg = write_registry(tmp_path, str(project), str(project))
    col = collect.Collector(reg, root=project, local_root=project)
    w = col.work_today(window_hours=24)
    assert w["available"] is True
    assert w["commit_count"] == 0, "a 2020 commit is not today's work"


def test_work_today_git_log_failure_is_unavailable_not_empty(tmp_path, monkeypatch):
    import subprocess
    project = tmp_path / "repo"
    _init_repo(project)
    (project / "a.txt").write_text("1", encoding="utf-8")
    _commit(project, "visible only if git works")
    reg = write_registry(tmp_path, str(project), str(project))
    real_run = subprocess.run

    def fail_log(cmd, *args, **kwargs):
        if cmd[:3] == ["git", "-C", str(project)] and "log" in cmd:
            return subprocess.CompletedProcess(
                cmd, 128, stdout="", stderr="fatal: dubious ownership")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(collect.subprocess, "run", fail_log)
    result = collect.Collector(
        reg, root=project, local_root=project).work_today()

    assert result["available"] is False
    assert result["note"] == "git history is unavailable"
    assert "dubious ownership" in result["log_error"]
    assert "commit_count" not in result


def test_work_today_fetch_failure_reports_stale_but_readable_mirror(
        tmp_path, monkeypatch):
    import subprocess
    project = tmp_path / "repo"
    _init_repo(project)
    (project / "a.txt").write_text("1", encoding="utf-8")
    _commit(project, "locally readable mirror commit")
    reg = write_registry(tmp_path, str(project), str(project))
    monkeypatch.setenv("MANAGER_GIT_REPO", str(project))
    real_run = subprocess.run

    def fail_fetch(cmd, *args, **kwargs):
        if cmd[:3] == ["git", "-C", str(project)] and "fetch" in cmd:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="network unavailable")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(collect.subprocess, "run", fail_fetch)
    result = collect.Collector(
        reg, root=tmp_path / "plain", local_root=tmp_path / "plain").work_today()

    assert result["available"] is True
    assert result["is_mirror"] is True
    assert result["visibility"] == "pushed_refs_only"
    assert result["fetched"] is False
    assert result["fetch_error"] == "network unavailable"
    assert result["commit_count"] == 1


def test_work_today_without_git_is_unavailable_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "MIRROR_PATH", tmp_path / "no-mirror")
    monkeypatch.delenv("MANAGER_GIT_REPO", raising=False)
    project = tmp_path / "plain"
    project.mkdir()
    reg = write_registry(tmp_path, str(project), str(project))
    col = collect.Collector(reg, root=project, local_root=project)
    w = col.work_today()
    assert w["available"] is False
    assert "note" in w
    assert col.faults == [], "a missing repo is a reported gap, not a crash"


def test_collector_loads_shared_research_operations_contract(tmp_path):
    project = tmp_path / "opt" / "betting-pod-shop"
    metrics_dir = project / "data" / "research_intake"
    metrics_dir.mkdir(parents=True)
    (metrics_dir / "metrics.json").write_text(json.dumps({
        "generated_at": "2026-08-03T04:10:00Z",
        "funnel": {"assignments": 200, "dispatched": 10,
                   "dispatched_reviewed": 2, "dispatched_advanced": 1},
        "dispatch": {"pending_review": 8},
        "operations": {
            "semantics": {"agent_invocation_tracked": False},
            "activity_24h": {"dispatched": 10, "reviewed": 2},
            "queue": {"pending": 8, "overdue": 1},
            "agents": {"strategy-scout": {"pending": 8}},
        },
        "x_pilot": {"month": "2026-08", "estimated_cost_usd": 1.065},
        "collector_health": {
            "status": "degraded", "academic_feed_items_raw": 0,
            "zero_academic_feeds": ["feed:arxiv_qfin_trading"],
            "collector_error_count": 1,
        },
        "quality_control": {
            "intake_rejected": 4, "triage_blocked": 2,
            "legacy_dispatches_quarantined": 1,
        },
    }), encoding="utf-8")
    reg = write_registry(tmp_path, str(project), str(project))
    record = collect.Collector(reg, root=project).research_operations()

    assert record["available"] is True
    assert record["funnel"]["assignments"] == 200
    assert record["operations"]["queue"]["pending"] == 8
    assert record["operations"]["semantics"]["agent_invocation_tracked"] is False
    assert record["collector_health"]["status"] == "degraded"
    assert record["quality_control"]["intake_rejected"] == 4


def test_collector_overlays_live_crossvenue_metrics_for_daily_brief(tmp_path):
    project = tmp_path / "opt" / "betting-pod-shop"
    metrics_dir = project / "data" / "research_intake"
    metrics_dir.mkdir(parents=True)
    (metrics_dir / "metrics.json").write_text(json.dumps({
        "generated_at": "2026-08-02T04:00:00Z",
        "operations": {},
        "crossvenue_pilot": {
            "generated_at": "2026-08-02T04:00:00Z",
            "terms_equivalence": "unverified",
        },
    }), encoding="utf-8")
    live_dir = project / "data" / "gemini_crossvenue"
    live_dir.mkdir(parents=True)
    (live_dir / "metrics.json").write_text(json.dumps({
        "generated_at": "2026-08-02T19:45:00Z",
        "terms_equivalence": "not_equivalent",
        "analytics": {"quote_completeness": 1.0},
    }), encoding="utf-8")
    reg = write_registry(tmp_path, str(project), str(project))

    record = collect.Collector(reg, root=project).research_operations()

    assert record["crossvenue_pilot"]["terms_equivalence"] == "not_equivalent"
    assert record["crossvenue_pilot"]["analytics"]["quote_completeness"] == 1.0


def test_collector_admits_missing_research_operations(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reg = write_registry(tmp_path, str(project), str(project))
    record = collect.Collector(reg, root=project).research_operations()
    assert record["available"] is False
    assert "does not exist" in record["reason"]


def test_snapshot_is_json_serialisable(tmp_path):
    """write_state uses json.dumps; None-valued fields must round-trip."""
    project = tmp_path / "opt" / "betting-pod-shop"
    (project / "data").mkdir(parents=True)
    reg = write_registry(tmp_path, str(tmp_path / "gone"), str(project))
    col = collect.Collector(reg, root=project, local_root=tmp_path / "gone")
    text = json.dumps({"jobs": col.jobs()}, default=collect.jsonable)
    assert '"state": "uncheckable"' in text
    assert '"exists": null' in text


def _p029_rows(swaps, *, anon_pct=5.7, pid=243750):
    """Trailing heartbeat rows, one per hour, with a given swap series."""
    return [
        {
            "timestamp_utc": "2026-08-09T{:02d}:00:00Z".format(hour),
            "shadow_main_pid": pid,
            "shadow_memory_utilization_pct": 100.0,
            "shadow_memory_anon_utilization_pct": anon_pct,
            "shadow_memory_swap_bytes": swap,
            "shadow_memory_max_events": 1_000_000 + hour * 130_000,
            "shadow_due_in_zone": 300,
            "shadow_restart_delta": 0,
        }
        for hour, swap in enumerate(swaps)
    ]


def _p029_job(rows):
    return {
        "id": "p029_shadow", "measurable": True, "stale": False,
        "last_row": dict(rows[-1], **{
            "shadow_memory_current_bytes": 805_273_600,
            "shadow_memory_anon_bytes": 46_264_320,
            "shadow_memory_file_bytes": 748_830_720,
            "shadow_memory_peak_bytes": 805_306_368,
            "shadow_oom_kill_events": 0,
        }),
        "recent_rows": rows,
    }


def test_p029_plateaued_swap_at_the_cap_does_not_warn():
    """The exact live state on 2026-08-09, which warned every day.

    A cgroup pinned at memory.max makes the kernel page out cold anonymous
    memory, so `swap > 0` is a permanent consequence of the 768 MiB cap. Heap
    flat at 5.7%, zero OOM kills, swap risen once and plateaued: nothing to
    wake anyone for.
    """
    rows = _p029_rows([30_244_864, 31_281_152, 31_338_496,
                       31_330_304, 31_334_400])
    found = checks.check_jobs({"jobs": [_p029_job(rows)]})
    assert [f.key for f in found] == []


def test_p029_growing_swap_against_a_flat_heap_still_warns():
    """Swap that keeps climbing is a leak the page-cache story would hide."""
    rows = _p029_rows([10_000_000, 30_000_000, 60_000_000,
                       90_000_000, 120_000_000])
    found = {f.key: f for f in checks.check_jobs({"jobs": [_p029_job(rows)]})}
    finding = found["job.p029_shadow.memory_pressure"]
    assert finding.severity == "warn"
    assert "growth 110000000" in finding.detail


def test_p029_high_anon_still_warns_even_with_flat_swap():
    """Heap pressure is judged on its own; it never needed the swap clause."""
    rows = _p029_rows([31_000_000] * 5, anon_pct=52.0)
    found = {f.key: f for f in checks.check_jobs({"jobs": [_p029_job(rows)]})}
    assert found["job.p029_shadow.memory_pressure"].severity == "warn"


def test_p029_legacy_rows_without_anon_telemetry_still_warn():
    """Telemetry that cannot split heap from cache must not read as healthy."""
    rows = _p029_rows([31_000_000] * 5)
    for row in rows:
        row.pop("shadow_memory_anon_utilization_pct")
    job = _p029_job(rows)
    job["last_row"].pop("shadow_memory_anon_utilization_pct", None)
    found = {f.key: f for f in checks.check_jobs({"jobs": [job]})}
    assert found["job.p029_shadow.memory_pressure"].severity == "warn"


def test_p029_swap_growth_is_measured_over_the_window():
    trend = checks._p029_pressure_trend(
        _p029_rows([19_271_680, 25_000_000, 31_334_400]))
    assert trend["swap_first_bytes"] == 19_271_680
    assert trend["swap_latest_bytes"] == 31_334_400
    assert trend["swap_growth_bytes"] == 12_062_720
    # A single sample cannot establish a trend and must not fabricate one.
    single = checks._p029_pressure_trend(_p029_rows([31_334_400]))
    assert single["swap_growth_bytes"] is None


def test_p029_process_start_fill_is_not_read_as_a_leak():
    """A window containing the process start begins at swap=0.

    End-to-end growth would report the whole one-time fill to steady state as
    a leak. Measured live on 2026-08-09: 0 -> 31 MiB across 22h, a single step
    that then went flat.
    """
    rows = _p029_rows([0, 19_271_680, 19_400_000, 30_244_864,
                       31_281_152, 31_330_304, 31_334_400])
    trend = checks._p029_pressure_trend(rows)
    assert trend["swap_growth_bytes"] == 31_334_400        # end to end
    assert trend["swap_recent_growth_bytes"] < 2 * 1024 * 1024
    assert not checks.check_jobs({"jobs": [_p029_job(rows)]})
