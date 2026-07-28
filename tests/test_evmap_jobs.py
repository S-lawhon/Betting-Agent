"""
tests/test_evmap_jobs.py
────────────────────────
EV-Map collector observability.

These jobs ran **139 times on the Mac and failed 139 times** — macOS TCC
denying `cron` read access under `~/Desktop`, so they failed awake as well as
asleep — and nothing noticed, because a failing collector and an idle
collector produced the same observable: nothing. Six days of point-in-time
weather paper quotes (~1,200/day) are permanently gone; the public horizon is
90 days and they cannot be re-pulled.

So the property under test is not "does the wrapper run a script". It is
**does every way this can go wrong become visible**:

  * nonzero exit -> failure;
  * exit 0 having written nothing it was contracted to write -> ALSO failure
    (an exit code never saw the real problem);
  * per-day outputs measured as "what did THIS run write", not as a
    delta against yesterday's file, which is ~0 on a healthy run;
  * consecutive failures counted, so the 2nd one pages;
  * a job that stops running at all -> alarm;
  * a day that collects nothing -> alarm, whatever the exit codes said.
"""
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "manager"))

import checks  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "evmap_job", ROOT / "scripts" / "evmap_job.py")
ej = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ej)

UTC = timezone.utc


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the wrapper at a scratch data dir; nothing touches real state."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(ej, "DATA", data)
    monkeypatch.setattr(ej, "STATUS", data / "job_status.jsonl")
    monkeypatch.setattr(ej, "EVMAP", tmp_path)
    (tmp_path / "src").mkdir()
    return data


def _script(tmp_path, name, body):
    p = tmp_path / "src" / name
    p.write_text(body)
    return p


def _status(data):
    return [json.loads(l) for l in (data / "job_status.jsonl").read_text().splitlines()]


# ── the wrapper ──────────────────────────────────────────────────────

def test_nonzero_exit_is_a_failure(sandbox, tmp_path, monkeypatch):
    _script(tmp_path, "boom.py", "import sys\nsys.stderr.write('kaboom\\n')\nsys.exit(4)\n")
    monkeypatch.setitem(ej.JOBS, "boom", {"script": "boom.py", "args": [],
                                          "output": "out.csv", "expect_rows": 0,
                                          "every": "test"})
    assert ej.run("boom") == 4
    rec = _status(sandbox)[-1]
    assert rec["ok"] is False
    assert rec["exit_code"] == 4
    assert "kaboom" in " ".join(rec["stderr_tail"])


def test_exit_zero_with_no_rows_is_ALSO_a_failure(sandbox, tmp_path, monkeypatch):
    """The half of the failure an exit code cannot see."""
    _script(tmp_path, "quiet.py", "print('collected nothing, cheerfully')\n")
    monkeypatch.setitem(ej.JOBS, "quiet", {"script": "quiet.py", "args": [],
                                           "output": "out.csv", "expect_rows": 1,
                                           "every": "test"})
    assert ej.run("quiet") != 0
    rec = _status(sandbox)[-1]
    assert rec["ok"] is False
    assert rec["exit_code"] == 0            # the script was "fine"
    assert "rows added = 0" in rec["empty_reason"]


def test_rows_are_measured_from_the_output_not_from_stdout(sandbox, tmp_path,
                                                           monkeypatch):
    _script(tmp_path, "writer.py",
            "from pathlib import Path\n"
            "p = Path('data/out.csv')\n"
            "p.write_text('h\\n' + '\\n'.join(str(i) for i in range(5)) + '\\n')\n"
            "print('I wrote 999 rows')\n")
    monkeypatch.setitem(ej.JOBS, "writer", {"script": "writer.py", "args": [],
                                            "output": "out.csv", "expect_rows": 1,
                                            "every": "test"})
    assert ej.run("writer") == 0
    rec = _status(sandbox)[-1]
    assert rec["rows_after"] == 5            # not 999
    assert rec["rows_added"] == 5
    assert rec["ok"] is True


def test_per_day_output_counts_THIS_runs_file_not_a_delta(sandbox, tmp_path,
                                                          monkeypatch):
    """The false alarm the first live run produced.

    `weather_pipeline` writes one CSV per target day. Yesterday's file and
    today's have near-identical row counts, so an after-minus-before delta is
    ~0 on a perfectly healthy run — which is exactly what it reported.
    """
    (sandbox / "fair_2026-07-20.csv").write_text(
        "h\n" + "\n".join(str(i) for i in range(114)) + "\n")
    _script(tmp_path, "perday.py",
            "from pathlib import Path\n"
            "Path('data/fair_2026-07-28.csv').write_text("
            "'h\\n' + '\\n'.join(str(i) for i in range(114)) + '\\n')\n")
    monkeypatch.setitem(ej.JOBS, "perday",
                        {"script": "perday.py", "args": [],
                         "output_glob": "fair_*.csv", "expect_rows": 1,
                         "every": "test"})
    assert ej.run("perday") == 0
    rec = _status(sandbox)[-1]
    assert rec["rows_before"] == 0           # per-day jobs start from zero
    assert rec["rows_added"] == 114          # not 0
    assert rec["ok"] is True


def test_a_stale_per_day_file_does_not_count_as_this_runs_work(sandbox, tmp_path,
                                                               monkeypatch):
    """If the job writes nothing, yesterday's file must not rescue it."""
    (sandbox / "fair_2026-07-20.csv").write_text("h\n1\n2\n3\n")
    _script(tmp_path, "noop.py", "pass\n")
    monkeypatch.setitem(ej.JOBS, "noop",
                        {"script": "noop.py", "args": [],
                         "output_glob": "fair_*.csv", "expect_rows": 1,
                         "every": "test"})
    assert ej.run("noop") != 0
    assert _status(sandbox)[-1]["ok"] is False


def test_consecutive_failures_accumulate_then_reset(sandbox, tmp_path, monkeypatch):
    _script(tmp_path, "boom.py", "import sys; sys.exit(1)\n")
    _script(tmp_path, "fine.py",
            "from pathlib import Path; Path('data/out.csv').write_text('h\\n1\\n')\n")
    monkeypatch.setitem(ej.JOBS, "j", {"script": "boom.py", "args": [],
                                       "output": "out.csv", "expect_rows": 1,
                                       "every": "test"})
    ej.run("j")
    ej.run("j")
    assert [r["consecutive_failures"] for r in _status(sandbox)] == [1, 2]
    ej.JOBS["j"]["script"] = "fine.py"
    ej.run("j")
    assert _status(sandbox)[-1]["consecutive_failures"] == 0


def test_every_configured_job_points_at_a_script_that_exists():
    """A typo'd script name would fail identically to a dead collector."""
    for name, spec in ej.JOBS.items():
        assert (ROOT / "kalshi-ev-map" / "src" / spec["script"]).exists(), name


# ── the alert path ───────────────────────────────────────────────────

def _f(jobs):
    return checks.check_evmap({"evmap": {"available": True, "jobs": jobs}})


def _healthy(**kw):
    base = {"ok": True, "age_min": 30.0, "rows_added": 100, "rows_today": 100,
            "consecutive_failures": 0, "exit_code": 0, "stderr_tail": []}
    base.update(kw)
    return base


def test_one_failure_warns_two_in_a_row_pages():
    assert _f({"paper_maker": _healthy(ok=False, consecutive_failures=1)}
              )[0].severity == "warn"
    assert _f({"paper_maker": _healthy(ok=False, consecutive_failures=2)}
              )[0].severity == "critical"


def test_a_job_that_stops_running_is_critical():
    f = _f({"paper_maker": _healthy(age_min=60 * 40)})
    assert f[0].severity == "critical"
    assert "has not run" in f[0].title


def test_a_day_that_collects_nothing_alarms_even_when_every_run_exited_zero():
    f = _f({"paper_maker": _healthy(rows_today=0)})
    assert [x.key for x in f] == ["evmap.paper_maker.empty_day"]
    assert f[0].severity == "warn"


def test_healthy_jobs_are_silent():
    assert _f({"paper_maker": _healthy(), "weather_sheet": _healthy()}) == []


def test_missing_status_file_is_unmeasured_not_healthy():
    f = checks.check_evmap({"evmap": {"available": False, "error": "no file"}})
    assert f[0].severity == "warn"
    assert f[0].key == "evmap.unavailable"


def test_absent_section_produces_nothing():
    assert checks.check_evmap({}) == []


def test_wired_into_run_checks():
    snap = {"collected_at": datetime.now(UTC).isoformat(),
            "evmap": {"available": True,
                      "jobs": {"paper_maker": _healthy(ok=False,
                                                       consecutive_failures=3)}}}
    assert any(f.key.startswith("evmap.") for f in checks.run_checks(snap))
