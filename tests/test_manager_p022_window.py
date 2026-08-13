"""
tests/test_manager_p022_window.py
─────────────────────────────────
The P-022 window detector only matters if it reaches Sam.

Before this wiring the detector ran */30 on the droplet, appended a row to
`data/p022_window_check/status.jsonl`, and stopped there — its only consumer
was a human deciding to open the file. So the state that means "the fund's
only validated edge is structurally unable to trade" was recorded and never
delivered, which is the same outcome as not recording it.

These tests pin the two halves of that path:

  collector — the last status row is surfaced, and a MISSING or STALE file is
              reported as unmeasured rather than defaulted to healthy;
  checks    — WINDOW_OPEN_CANDIDATE_NO_QUOTE is `critical` (alert.py pages
              criticals immediately; the two-consecutive-runs confirmation
              lives upstream in the detector, which emits ..._ONCE for a
              single sighting), a checker failure is a `warn`, and the
              healthy states stay `info`.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manager"))

import checks  # noqa: E402
import collect  # noqa: E402

UTC = timezone.utc


def _row(state, **kw):
    row = {
        "iso": datetime.now(UTC).isoformat(),
        "state": state,
        "alarm": state in ("WINDOW_OPEN_CANDIDATE_NO_QUOTE",
                           "CLOSE_REF_PLACEHOLDER", "SCHEDULE_UNRESOLVED",
                           "CHECK_FAILED"),
        "detail": "detail for " + state,
        "funnel": {"markets_listed": 957, "close_resolved": 957,
                   "inside_window": 144, "priced": 144, "in_band": 13,
                   "passes_every_screen": 13, "quoted_since_window_open": 0,
                   "candidates_without_quote": 13},
        "n_in_window_events": 1,
        "n_candidates": 13,
        "candidates_without_quote": ["KXLPGAR1LEAD-AIGWO26-CHLWIL"],
        "events": [{"event": "KXLPGAR1LEAD-AIGWO26",
                    "close_ref_iso": "2026-07-30T15:30:00+00:00",
                    "close_source": "tour_day_offset",
                    "hours_to_close_ref": 23.98,
                    "window_open_iso": "2026-07-29T15:30:00+00:00"}],
    }
    row.update(kw)
    return row


def _collector(tmp_path, rows=None):
    root = tmp_path / "root"
    (root / "data/p022_window_check").mkdir(parents=True)
    if rows is not None:
        (root / "data/p022_window_check/status.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n")
    c = collect.Collector.__new__(collect.Collector)
    c.root = root
    c.faults = []
    return c


# ── collector ────────────────────────────────────────────────────────

def test_collector_surfaces_the_last_status_row(tmp_path):
    c = _collector(tmp_path, [_row("NO_WINDOW"),
                              _row("WINDOW_OPEN_CANDIDATE_NO_QUOTE")])
    out = c.p022_window()
    assert out["available"] is True
    assert out["state"] == "WINDOW_OPEN_CANDIDATE_NO_QUOTE"
    assert out["alarm"] is True
    assert out["funnel"]["in_band"] == 13
    assert out["age_min"] < 5


def test_missing_status_file_is_unavailable_not_healthy(tmp_path):
    """The checker never having run must not read as 'nothing wrong'."""
    c = _collector(tmp_path, rows=None)
    out = c.p022_window()
    assert out["available"] is False
    assert "never run" in out["error"]


def test_collector_reports_age_so_a_dead_cron_is_visible(tmp_path):
    old = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
    c = _collector(tmp_path, [_row("NO_WINDOW", iso=old)])
    out = c.p022_window()
    assert out["age_min"] > 300


# ── checks ───────────────────────────────────────────────────────────

def _findings(win):
    return checks.check_p022_window({"p022_window": win})


def test_candidate_without_quote_is_critical():
    """alert.py pages criticals immediately. Since 2026-08-12 the detector
    only emits this state after the same name is missing in two consecutive
    runs (a single sighting is ..._ONCE, tested below), so by the time this
    row exists the confirmation has already happened — it pages."""
    f = _findings({"available": True, "state": "WINDOW_OPEN_CANDIDATE_NO_QUOTE",
                   "detail": "13 names clear every screen",
                   "funnel": _row("x")["funnel"],
                   "candidates_without_quote": ["KXLPGAR1LEAD-AIGWO26-CHLWIL"]})
    assert len(f) == 1
    assert f[0].severity == "critical"
    assert f[0].workstream == "P-022"
    assert "KXLPGAR1LEAD-AIGWO26-CHLWIL" in f[0].detail
    assert "passes every screen 13" in f[0].detail


def test_single_sighting_is_a_warn_not_a_page():
    """The 2026-08-12 false page: a name drifted onto the band edge between
    pod cycles, one detector sample caught it, and a CRITICAL email went out
    about a quote that existed 80 s later. One sighting stays visible as a
    warn; the page needs the detector's two-run confirmation."""
    f = _findings({"available": True,
                   "state": "WINDOW_OPEN_CANDIDATE_NO_QUOTE_ONCE",
                   "detail": "FIRST run to see this",
                   "funnel": _row("x")["funnel"],
                   "candidates_without_quote": ["KXPGAR1LEAD-FESJC26-CAME"]})
    assert len(f) == 1
    assert f[0].severity == "warn"
    assert f[0].key == "p022.window.no_quote_once"
    assert "KXPGAR1LEAD-FESJC26-CAME" in f[0].detail


def test_placeholder_and_unresolved_are_also_critical():
    for state in ("CLOSE_REF_PLACEHOLDER", "SCHEDULE_UNRESOLVED"):
        f = _findings({"available": True, "state": state, "detail": "d"})
        assert [x.severity for x in f] == ["critical"], state


def test_checker_failure_is_a_warn_not_silence():
    f = _findings({"available": True, "state": "CHECK_FAILED",
                   "detail": "kalshi 503"})
    assert f[0].severity == "warn"
    assert "UNKNOWN" in f[0].detail


def test_healthy_states_are_info_and_do_not_page():
    for state in ("NO_MARKETS", "NO_WINDOW", "WINDOW_OPEN_NO_CANDIDATE",
                  "WINDOW_OPEN_GRACE", "QUOTED"):
        f = _findings({"available": True, "state": state, "detail": "d",
                       "funnel": _row("x")["funnel"]})
        assert [x.severity for x in f] == ["info"], state


def test_unavailable_detector_warns():
    f = _findings({"available": False, "error": "no status.jsonl"})
    assert f[0].severity == "warn"
    assert f[0].key == "p022.window.unavailable"


def test_stale_row_warns_on_top_of_the_state():
    f = _findings({"available": True, "state": "NO_WINDOW", "age_min": 240.0,
                   "detail": "d"})
    keys = {x.key for x in f}
    assert "p022.window.stale" in keys
    assert "p022.window.state" in keys


def test_absent_section_produces_nothing():
    """An older snapshot from before this probe existed must not fabricate a
    finding in either direction."""
    assert checks.check_p022_window({}) == []


def test_wired_into_run_checks():
    snap = {"collected_at": datetime.now(UTC).isoformat(),
            "p022_window": {"available": True,
                            "state": "WINDOW_OPEN_CANDIDATE_NO_QUOTE",
                            "detail": "d"}}
    assert any(f.key.startswith("p022.window.")
               for f in checks.run_checks(snap))
