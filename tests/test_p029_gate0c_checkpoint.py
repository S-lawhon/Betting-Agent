import gzip
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts import p029_gate0c_checkpoint as gate


AFTER_READ = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)


def test_reader_runs_by_absolute_path_outside_checkout(tmp_path):
    script = Path(gate.__file__).resolve()
    result = subprocess.run(
        [sys.executable, str(script), "--now", "2026-08-22T23:59:59Z", "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "NO DECISION"
    assert payload["progress"] is None


def _db(tmp_path: Path, rows):
    path = tmp_path / "shadow.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE combo (market_ticker TEXT, event_ticker TEXT,"
        " seen_ts TEXT, legs_json TEXT, leg_marks_json TEXT,"
        " first_trade_px REAL, traded INTEGER)"
    )
    con.executemany("INSERT INTO combo VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return path


def _row(ticker="C1", first_px=0.30, seen="2026-08-10T12:00:00Z",
         traded=1, mid=0.50):
    legs = [
        ["KXMLBGAME-26AUG10AAABBB-AAA", "yes"],
        ["KXMLBGAME-26AUG10CCCDDD-CCC", "yes"],
    ]
    marks = [
        {"ticker": legs[0][0], "yes_mid": mid},
        {"ticker": legs[1][0], "yes_mid": mid},
    ]
    return (ticker, "EV1", seen, json.dumps(legs), json.dumps(marks),
            first_px, traded)


def _archive(tmp_path: Path, settlements):
    path = tmp_path / "archive"
    path.mkdir()
    with gzip.open(path / "part.jsonl.gz", "wt") as fh:
        for ticker, value in settlements.items():
            fh.write(json.dumps({
                "ticker": ticker,
                "settlement_value_dollars": value,
            }) + "\n")
    return path


def test_reader_stays_blind_before_the_registered_date(tmp_path):
    result = gate.evaluate(
        tmp_path / "missing.sqlite", tmp_path / "missing-archive",
        gate.DEFAULT_MODEL, datetime(2026, 8, 22, tzinfo=timezone.utc), 20,
    )

    assert result["progress"] is None
    assert result["verdict"] == "NO DECISION"
    assert "blind" in result["reason"]
    assert "margin_median_c" not in result
    assert result["data_qualification"]["known_tape_gaps"] == gate.KNOWN_TAPE_GAPS


def test_model_hash_is_load_bearing(tmp_path):
    changed = tmp_path / "model.json"
    changed.write_text(gate.DEFAULT_MODEL.read_text() + "\n")

    try:
        gate._model(changed)
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("changed frozen model was accepted")


def test_reader_uses_only_forward_window_and_settled_rows(tmp_path):
    rows = [
        _row("IN"),
        _row("UNSETTLED"),
        _row("BEFORE", seen="2026-08-05T23:59:59Z"),
        _row("HEALTH_ONLY", first_px=None, traded=0, mid=0.80),
    ]
    result = gate.evaluate(
        _db(tmp_path, rows), _archive(tmp_path, {
            "IN": 0.0, "BEFORE": 0.0, "HEALTH_ONLY": 1.0,
        }),
        gate.DEFAULT_MODEL, AFTER_READ, 20,
    )

    assert result["progress"] == 1
    assert result["traded_in_zone"] == 2
    assert result["unsettled_in_zone"] == 1
    assert result["model_health_n"] == 2


def test_extension_read_includes_registered_extra_week(tmp_path):
    rows = [
        _row("INITIAL", seen="2026-08-19T23:59:59Z"),
        _row("EXTENSION", seen="2026-08-25T12:00:00Z"),
        _row("TOO_LATE", seen="2026-08-27T00:00:00Z"),
    ]
    archive = _archive(tmp_path, {
        "INITIAL": 0.0, "EXTENSION": 0.0, "TOO_LATE": 0.0,
    })
    db = _db(tmp_path, rows)

    initial = gate.evaluate(db, archive, gate.DEFAULT_MODEL, AFTER_READ, 20)
    extended = gate.evaluate(
        db, archive, gate.DEFAULT_MODEL,
        datetime(2026, 8, 30, 12, tzinfo=timezone.utc), 20,
    )

    assert initial["progress"] == 1
    assert initial["window_end_utc"] == gate.WINDOW_END
    assert extended["progress"] == 2
    assert extended["window_end_utc"] == gate.EXTENSION_WINDOW_END
    assert extended["extension_final"] is True


def test_dual_condition_decision_rule():
    assert gate.decide(500, 2.1, 1.2, [0.1, 2.0])[0] == "CONTINUE"
    assert gate.decide(500, 0.9, 4.0, [1.0, 5.0])[0] == "STOP"
    assert gate.decide(500, 3.0, 0.0, [-1.0, 1.0])[0] == "STOP"
    assert gate.decide(500, 2.1, 1.2, [-0.1, 2.0])[0] == "EXTEND"
    assert gate.decide(499, 2.1, 1.2, [0.1, 2.0])[0] == "EXTEND"
    assert gate.decide(
        500, 2.1, 1.2, [-0.1, 2.0], extension_final=True,
    )[0] == "INCONCLUSIVE"
