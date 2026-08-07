"""
tests/test_gate_instrumentation.py
──────────────────────────────────
The enforcer for `docs/GATE_INSTRUMENTATION_STANDARD.md`.

A screen that cannot re-detect the bugs that motivated it is not a screen —
the same standard the R5 friction screener was held to. So every test here
reconstructs an actual historical failure of this repository and asserts the
check fires on it, and the control asserts a healthy gate stays quiet.

Reconstructed:
  * P-014 2026-07-28 — `source: trade_log`, no reader at all;
  * P-015 2026-07-28 — reader globbing `data/pods/P-015.jsonl`, a file that
    has never existed;
  * P-017's `progress: 1` — a hand-typed number with no observation behind it;
  * the JDAY re-book — `outcome` set while the reader keys off `action`;
  * P-001's "late Aug – early Sept 2026" — a forward date nothing derives.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "check_gate_instrumentation", ROOT / "scripts" / "check_gate_instrumentation.py")
gi = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gi)

# The number of failing checks against the real registry on the day the
# standard was adopted. This is a RATCHET: it may fall, never rise. Raising it
# means a gate lost instrumentation, which is the whole thing being prevented.
BASELINE_FAILING = 11   # measured on the droplet, where the logs are


def _tree(tmp_path, gate, reader_src=None, blocked_on="time", logs=None):
    """A minimal checkout: registry + optional reader + optional log rows."""
    (tmp_path / "manager").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    reg = {"workstreams": [{"id": "P-999", "name": "Test", "tier": "validating",
                            "blocked_on": blocked_on, "gate": gate}]}
    (tmp_path / "manager" / "registry.yaml").write_text(yaml.safe_dump(reg))
    # check 7 reads the source map out of manager/checks.py rather than
    # duplicating it, so a synthetic tree needs one.
    (tmp_path / "manager" / "checks.py").write_text(
        'key = {"p999_checkpoint": "p999", "p017_checkpoint": "p017"}\n')
    if reader_src is not None:
        (tmp_path / "scripts" / "p999_checkpoint.py").write_text(reader_src)
        (tmp_path / "scripts" / "__init__.py").write_text("")
    for rel, body in (logos := (logs or {})).items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    assert logos is not None
    return tmp_path


def _by_check(results):
    return {r.check: r for r in results}


# NOTE the `__main__` guard. The enforcer IMPORTS a reader to read its
# declared log constants, so a reader that runs its CLI at import time would
# execute inside the checker (and inside pytest). Real readers are guarded;
# these fixtures must be too, or the test exercises a shape that cannot exist.
GOOD_READER = '''
import argparse, glob, json, sys
DEFAULT_LOGS = ["data/trade_logs/trade_log.jsonl"]
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--json", action="store_true")
    ap.parse_args()
    if not glob.glob(DEFAULT_LOGS[0]):
        print("ERROR: no trade log", file=sys.stderr); return 1
    rows = 0
    for p in glob.glob(DEFAULT_LOGS[0]):
        rows += sum(1 for l in open(p) if "P-999" in l)
    print(json.dumps({"progress": rows, "threshold": 10,
                      "reason": "" if rows else "no settled rows yet"}))
    return 0
if __name__ == "__main__":
    sys.exit(main())
'''

GOOD_GATE = {"question": "q", "metric": "settled", "threshold": 10,
             "source": "p999_checkpoint", "reader": "scripts/p999_checkpoint.py",
             "rule_document": "docs/RULE.md"}


def _good(tmp_path, **over):
    gate = dict(GOOD_GATE); gate.update(over.pop("gate", {}))
    t = _tree(tmp_path, gate, GOOD_READER,
              blocked_on=over.pop("blocked_on", "time"),
              logs={"data/trade_logs/trade_log.jsonl":
                    '{"pod_id": "P-999", "action": "WIN"}\n' * 3})
    (t / "docs").mkdir(exist_ok=True); (t / "docs" / "RULE.md").write_text("rule")
    return t


# ── the control: a fully instrumented gate is silent ──────────────────

def test_a_fully_instrumented_gate_passes_every_check(tmp_path):
    res = gi.run(_good(tmp_path))
    failing = [r for r in res if r.ok is False]
    assert failing == [], [(r.check, r.detail) for r in failing]


# ── P-014, 2026-07-28: no reader at all ───────────────────────────────

def test_detects_a_gate_with_no_reader(tmp_path):
    t = _tree(tmp_path, {"threshold": 500, "source": "trade_log",
                         "metric": "settled_trades"}, reader_src=None)
    c = _by_check(gi.run(t))
    assert c["1_sanctioned_reader"].ok is False
    assert c["7_source_resolvable"].ok is False      # `trade_log` resolves to nothing
    # ...and the label is contradicted by the same evidence.
    assert c["9_blocked_on_vocabulary"].ok is False
    assert "DEFECT" in c["9_blocked_on_vocabulary"].detail
    # Downstream checks are UNKNOWN, never silently passing.
    assert c["2_reads_the_real_file"].ok is None


# ── P-015, 2026-07-28: reader points at a file that never existed ─────

def test_detects_a_reader_pointing_at_a_nonexistent_file(tmp_path):
    reader = GOOD_READER.replace('data/trade_logs/trade_log.jsonl',
                                 'data/pods/P-999.jsonl')
    t = _tree(tmp_path, GOOD_GATE, reader,
              logs={"data/trade_logs/trade_log.jsonl": '{"pod_id": "P-999"}\n'})
    c = _by_check(gi.run(t))
    assert c["2_reads_the_real_file"].ok is False
    assert "NONE of the reader's declared paths exist" in c["2_reads_the_real_file"].detail


def test_paths_exist_but_hold_no_rows_for_this_pod_is_a_failure(tmp_path):
    t = _tree(tmp_path, GOOD_GATE, GOOD_READER,
              logs={"data/trade_logs/trade_log.jsonl":
                    '{"pod_id": "P-001"}\n' * 5})
    c = _by_check(gi.run(t))
    assert c["2_reads_the_real_file"].ok is False


def test_a_checkout_with_no_data_is_UNKNOWN_not_a_failure(tmp_path):
    """A check that fails on every developer machine gets ignored, and an
    ignored check is the thing being prevented."""
    t = _tree(tmp_path, GOOD_GATE, GOOD_READER,
              logs={"data/trade_logs/trade_log.jsonl": ""})
    c = _by_check(gi.run(t))
    assert c["2_reads_the_real_file"].ok is None
    assert c["2_reads_the_real_file"].partial is True


# ── P-017's hand-typed progress: a number with no observation ─────────

def test_detects_a_reader_that_invents_progress_with_no_data(tmp_path):
    reader = '''
import argparse, json, sys
DEFAULT_LOGS = ["data/trade_logs/trade_log.jsonl"]
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--json", action="store_true")
    ap.parse_args()
    print(json.dumps({"progress": 1, "threshold": 8}))   # hand-typed, always
if __name__ == "__main__":
    main()
'''
    t = _tree(tmp_path, GOOD_GATE, reader,
              logs={"data/trade_logs/trade_log.jsonl": '{"pod_id": "P-999"}\n'})
    c = _by_check(gi.run(t))
    assert c["3_fails_to_none"].ok is False
    assert "came from somewhere other than observations" in c["3_fails_to_none"].detail


def test_refusing_loudly_with_no_data_is_CORRECT(tmp_path):
    """The check forbids inventing a number, not declining to produce one."""
    c = _by_check(gi.run(_good(tmp_path)))
    assert c["3_fails_to_none"].ok is True


# ── the JDAY signature: rows exist, reader counts zero, no reason ─────

def test_detects_the_gate_keying_off_the_wrong_field(tmp_path):
    reader = GOOD_READER.replace('"P-999" in l', '"IMPOSSIBLE-KEY" in l') \
                        .replace('"no settled rows yet"', '""')
    t = _tree(tmp_path, GOOD_GATE, reader,
              logs={"data/trade_logs/trade_log.jsonl":
                    '{"pod_id": "P-999", "outcome": "WIN"}\n' * 4})
    c = _by_check(gi.run(t))
    assert c["6_gate_key_matches"].ok is False
    assert "JDAY" in c["6_gate_key_matches"].detail


# ── P-001's undeducible forward date ─────────────────────────────────

def test_detects_a_hand_written_forward_date(tmp_path):
    t = _good(tmp_path, gate={"expected_completion": "late Aug - early Sept 2026"})
    c = _by_check(gi.run(t))
    assert c["8_projection_not_invented"].ok is False


def test_a_historical_date_is_not_a_projection(tmp_path):
    """`locked_date` is a fact about the past. Flagging it would train people
    to ignore the check."""
    t = _good(tmp_path, gate={"locked_date": "2026-07-26"})
    c = _by_check(gi.run(t))
    assert c["8_projection_not_invented"].ok is True


# ── decision rule ────────────────────────────────────────────────────

def test_detects_a_missing_decision_rule(tmp_path):
    gate = {k: v for k, v in GOOD_GATE.items() if k != "rule_document"}
    t = _tree(tmp_path, gate, GOOD_READER,
              logs={"data/trade_logs/trade_log.jsonl": '{"pod_id": "P-999"}\n'})
    c = _by_check(gi.run(t))
    assert c["4_decision_rule"].ok is False


def test_a_rule_that_exists_but_is_unnamed_reports_differently(tmp_path):
    gate = {k: v for k, v in GOOD_GATE.items() if k != "rule_document"}
    t = _tree(tmp_path, gate, GOOD_READER,
              logs={"data/trade_logs/trade_log.jsonl": '{"pod_id": "P-999"}\n'})
    (t / "somewhere").mkdir()
    (t / "somewhere" / "P999_DECISION_RULE.md").write_text("locked")
    c = _by_check(gi.run(t))
    assert c["4_decision_rule"].ok is False
    assert "does not NAME it" in c["4_decision_rule"].detail


def test_blocked_on_must_use_the_vocabulary(tmp_path):
    t = _good(tmp_path, blocked_on="vibes")
    c = _by_check(gi.run(t))
    assert c["9_blocked_on_vocabulary"].ok is False


# ── the enforcer itself must fail loudly, never skip ─────────────────

def test_an_unreadable_registry_exits_1_rather_than_0(tmp_path):
    (tmp_path / "manager").mkdir()
    (tmp_path / "manager" / "registry.yaml").write_text("{[not: yaml")
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.check_gate_instrumentation",
         "--root", str(tmp_path), "--json"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1


# ── the ratchet against the real registry ────────────────────────────

@pytest.mark.skipif(not os.environ.get("GATE_INSTRUMENTATION_CHECK"),
                    reason="set GATE_INSTRUMENTATION_CHECK=1 (needs the real logs)")
def test_real_registry_does_not_regress():
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.check_gate_instrumentation", "--json"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=900)
    payload = json.loads(proc.stdout)
    assert payload["n_failed"] <= BASELINE_FAILING, (
        "gate instrumentation REGRESSED: {} failing checks against a baseline "
        "of {}".format(payload["n_failed"], BASELINE_FAILING))


def test_the_standard_document_exists_and_names_every_check():
    """A check with no written justification is how a checklist becomes
    decoration."""
    doc = (ROOT / "docs" / "GATE_INSTRUMENTATION_STANDARD.md").read_text()
    for n in range(1, 10):
        assert f"### {n}." in doc, n


# ── a CLOSED gate is not a pending gate — and the exemption must be expensive ──
#
# Added 2026-07-28. The standard's first run flagged P-016 for having no reader
# and an unresolvable `source: maker_fills`. Both true — but P-016's gate had
# already RESOLVED (814 fills against a threshold of 500, verdict KILL) and a
# resolved gate needs no live reader. The risk of the fix is obvious: a
# `status: CLOSED` field is a way to silence any inconvenient gate. These tests
# pin the price of claiming it.

import scripts.check_gate_instrumentation as CGI


def _ws(**over):
    ws = {"id": "P-999", "stage": "killed", "blocked_on": "retired",
          "gate": {"status": "CLOSED", "resolved_on": "2026-07-21",
                   "verdict": "KILL", "threshold": 500,
                   "source": "maker_fills"}}
    ws["gate"].update(over.pop("gate", {}))
    ws.update(over)
    return ws


def test_closed_gate_with_all_three_is_exempt(tmp_path):
    res = CGI.check_pod(tmp_path, _ws())
    assert [r.check for r in res] == ["0_closed_gate_is_substantiated"]
    assert res[0].ok is True


def test_closed_gate_without_a_verdict_fails(tmp_path):
    ws = _ws()
    del ws["gate"]["verdict"]
    res = CGI.check_pod(tmp_path, ws)
    assert res[0].ok is False and "verdict" in res[0].detail


def test_closed_gate_without_a_date_fails(tmp_path):
    ws = _ws()
    del ws["gate"]["resolved_on"]
    res = CGI.check_pod(tmp_path, ws)
    assert res[0].ok is False and "resolved_on" in res[0].detail


def test_a_live_pod_cannot_close_its_own_gate(tmp_path):
    """THE abuse case: a running pod declaring CLOSED to dodge the checks."""
    res = CGI.check_pod(tmp_path, _ws(stage="paper", blocked_on="time"))
    assert res[0].ok is False
    assert "not terminal" in res[0].detail


def test_an_open_gate_is_still_fully_checked(tmp_path):
    ws = _ws()
    del ws["gate"]["status"]
    ws["stage"] = "paper"
    res = CGI.check_pod(tmp_path, ws)
    checks = {r.check for r in res}
    assert "0_closed_gate_is_substantiated" not in checks
    assert "1_sanctioned_reader" in checks


def test_retired_is_in_the_blocked_on_vocabulary():
    assert "retired" in CGI.BLOCKED_ON_VOCAB


def test_p018_published_kill_is_closed_and_instrumentation_exempt():
    registry = CGI._load_registry(ROOT)
    workstream = next(row for row in registry["workstreams"]
                      if row.get("id") == "P-018")

    results = CGI.check_pod(ROOT, workstream)

    assert [row.check for row in results] == ["0_closed_gate_is_substantiated"]
    assert results[0].ok is True
    assert "verdict=KILL" in results[0].detail
