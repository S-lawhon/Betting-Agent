"""Regression tests for the 2026-08-04 00:15 UTC false page.

The paper maker's quoting window (07:13-19:43 Chicago) straddles UTC
midnight. `rows_today` was bucketed by UTC calendar date, so it reset to 0
mid-window every night, and an alerter that read the previous cycle's
snapshot while the current collect was still writing counted that transient
warn twice — WARN_CONFIRMATIONS satisfied in 13 minutes by stale data.

Two invariants pinned here:

  1. collect.evmap_jobs counts a trailing 24h, so a row written just before
     UTC midnight still counts just after it.
  2. alert.run refuses to evaluate a snapshot older than one collect cycle,
     and treats an hours-old snapshot as a dead collector (critical), not as
     current truth.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MANAGER = Path(__file__).resolve().parent.parent / "manager"
sys.path.insert(0, str(MANAGER))

collect = pytest.importorskip("collect")
alert = pytest.importorskip("alert")

UTC = timezone.utc


def write_registry(tmp_path: Path) -> Path:
    reg = tmp_path / "registry.yaml"
    reg.write_text(
        "meta:\n"
        "  version: test\n"
        "  project_root: {root}\n"
        "  local_root: {root}\n"
        "services: []\n"
        "workstreams: []\n"
        "jobs: []\n".format(root=tmp_path),
        encoding="utf-8",
    )
    return reg


def evmap_record(iso: str, rows_added: int) -> str:
    return json.dumps({
        "iso": iso, "job": "paper_maker", "phase": "END", "ok": True,
        "exit_code": 0, "rows_added": rows_added, "rows_after": 0,
        "consecutive_failures": 0,
    })


def test_rows_24h_survives_utc_midnight(tmp_path, monkeypatch):
    """A row written at 23:43 UTC must still count at 00:15 UTC.

    Under the old UTC-calendar-day bucketing this exact fixture produced
    rows_today == 0 and fired the empty-day warn mid-quoting-window.
    """
    data = tmp_path / "kalshi-ev-map" / "data"
    data.mkdir(parents=True)
    (data / "job_status.jsonl").write_text(
        evmap_record("2026-08-02T23:00:00+00:00", 50) + "\n"    # > 24h old
        + evmap_record("2026-08-03T23:43:20+00:00", 114) + "\n",  # yesterday's
        encoding="utf-8",                                         # UTC date
    )
    monkeypatch.setattr(
        collect, "now", lambda: datetime(2026, 8, 4, 0, 15, tzinfo=UTC))

    c = collect.Collector(write_registry(tmp_path), root=tmp_path,
                          local_root=tmp_path)
    out = c.evmap_jobs()

    assert out["available"] is True
    rec = out["jobs"]["paper_maker"]
    assert rec["rows_24h"] == 114          # 23:43 row counts, 25h-old row not
    assert "rows_today" not in rec         # the calendar-day field is gone


def make_snapshot(tmp_path: Path, age: timedelta) -> Path:
    p = tmp_path / "status.json"
    p.write_text(json.dumps({
        "collected_at": (datetime.now(UTC) - age).isoformat(),
        "services": [], "jobs": [], "faults": [],
    }), encoding="utf-8")
    return p


def test_alert_skips_snapshot_older_than_one_cycle(tmp_path, capsys):
    p = make_snapshot(tmp_path, age=timedelta(minutes=20))
    rc = alert.run(dry_run=True, status_path=p)
    assert rc == 0
    out = capsys.readouterr().out
    assert "skipping" in out
    assert "WOULD SEND" not in out


def test_alert_pages_on_hours_old_snapshot(tmp_path, capsys):
    p = make_snapshot(tmp_path, age=timedelta(hours=3))
    alert.run(dry_run=True, status_path=p)
    out = capsys.readouterr().out
    assert "WOULD SEND" in out
    assert "collector is not producing" in out


def test_alert_evaluates_fresh_snapshot(tmp_path, capsys):
    p = make_snapshot(tmp_path, age=timedelta(minutes=2))
    rc = alert.run(dry_run=True, status_path=p)
    assert rc == 0
    assert "skipping" not in capsys.readouterr().out
