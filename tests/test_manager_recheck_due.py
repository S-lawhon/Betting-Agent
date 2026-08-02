"""`recheck_after` must actually fire.

Until 2026-08-01 this field was documentation: three workstreams in
registry.yaml carried one and NO code read it, so a date that came due passed
in silence. P-001's was set to 2026-08-01 and would have gone unnoticed on the
day. Same failure shape as an opportunity card whose next_step is "monitor"
with no monitor scheduled — the note is written, nobody opens it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from manager.checks import check_workstreams


def today():
    """UTC, matching check_workstreams — never the local date.

    The first version of this file used the LOCAL date. It passed all day and
    failed the moment UTC rolled past midnight while the Mac was still on the
    previous date, reporting "4d overdue" for a date built as today-3. The
    whole manager is UTC; a test that is not will be green until it is not.
    """
    return datetime.now(timezone.utc).date()


def _snap(recheck_after, **kw):
    ws = {"id": "R-TEST", "name": "Test workstream",
          "blocked_on": "measurement", "recheck_after": recheck_after}
    ws.update(kw)
    return {"workstreams": [ws]}


def _keys(findings):
    return {f.key for f in findings}


def test_recheck_due_today_fires():
    findings = check_workstreams(_snap(today().isoformat()))
    assert "ws.R-TEST.recheck_due" in _keys(findings)


def test_recheck_overdue_fires_and_counts_days():
    past = (today() - timedelta(days=3)).isoformat()
    finding = next(f for f in check_workstreams(_snap(past))
                   if f.key == "ws.R-TEST.recheck_due")
    assert finding.severity == "action"
    assert "3d overdue" in finding.title


def test_future_recheck_stays_silent():
    future = (today() + timedelta(days=40)).isoformat()
    assert "ws.R-TEST.recheck_due" not in _keys(check_workstreams(_snap(future)))


def test_absent_recheck_stays_silent():
    assert "ws.R-TEST.recheck_due" not in _keys(check_workstreams(_snap(None)))


def test_unparseable_recheck_does_not_crash_the_check():
    # A typo in registry.yaml must not take down the whole findings run —
    # the collector wraps probes, but checks.py is not wrapped per-workstream.
    assert "ws.R-TEST.recheck_due" not in _keys(check_workstreams(_snap("soon")))


def test_action_required_is_used_as_the_detail_when_present():
    finding = next(f for f in check_workstreams(
        _snap(today().isoformat(), action_required="Re-query the series."))
        if f.key == "ws.R-TEST.recheck_due")
    assert finding.detail == "Re-query the series."
