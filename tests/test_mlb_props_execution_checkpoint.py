from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from mlb_props_research import execution_checkpoint as gate


def ticker(day: date, suffix: str = "A") -> str:
    return "KXMLBHIT-{}{}{}1900-{}".format(
        day.strftime("%y"), day.strftime("%b").upper(),
        day.strftime("%d"), suffix)


def market_row(day: date, suffix: str = "A", minutes_before: int = 10,
               bid: float = 0.24, ask: float = 0.28, size: int = 80,
               volume: int = 100) -> dict:
    start = datetime.combine(day, time(19, 0), tzinfo=gate.ET)
    observed = start - timedelta(minutes=minutes_before)
    return {
        "t": "mkt", "tk": ticker(day, suffix),
        "ts": observed.astimezone(timezone.utc).isoformat(),
        "yb": bid, "ya": ask, "yas": size, "vol": volume,
    }


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8")


def test_final_window_observation_controls_entry(tmp_path):
    day = date(2026, 7, 22)
    rows = [
        market_row(day, minutes_before=20, volume=100),
        market_row(day, minutes_before=5, volume=0),
    ]
    write_rows(tmp_path / "snapshots_20260722.jsonl", rows)

    scan = gate.scan_snapshots(gate.snapshot_files(tmp_path))
    qualified = gate.qualify_days(
        scan, datetime(2026, 7, 23, 12, tzinfo=timezone.utc))

    assert qualified["days"][0]["window_markets"] == 1
    assert qualified["days"][0]["qualifying_entries"] == 0
    assert qualified["days"][0]["admissible"] is False


def test_day_requires_90_percent_window_coverage(tmp_path):
    day = date(2026, 7, 22)
    rows = [market_row(day, suffix=str(index)) for index in range(9)]
    rows.append(market_row(day, suffix="MISS", minutes_before=60))
    write_rows(tmp_path / "snapshots_20260722.jsonl", rows)

    qualified = gate.qualify_days(
        gate.scan_snapshots(gate.snapshot_files(tmp_path)),
        datetime(2026, 7, 23, 12, tzinfo=timezone.utc))

    assert qualified["days"][0]["coverage"] == pytest.approx(0.9)
    assert qualified["days"][0]["admissible"] is True


def test_mac_files_and_pre_clean_dates_never_count(tmp_path):
    old = date(2026, 7, 21)
    clean = date(2026, 7, 22)
    write_rows(tmp_path / "snapshots_20260721.jsonl", [market_row(old)])
    write_rows(tmp_path / "snapshots_20260722_mac.jsonl", [market_row(clean)])

    scan = gate.scan_snapshots(gate.snapshot_files(tmp_path))

    assert scan["files_scanned"] == 1
    assert scan["markets"] == {}


def test_locked_rule_hash_matches_document():
    import hashlib

    rule = Path(gate.__file__).with_name("MLB_PROPS_EXECUTION_RULE.md")
    assert hashlib.sha256(rule.read_bytes()).hexdigest() == gate.RULE_SHA256


def test_v2_locked_rule_hash_matches_document():
    import hashlib

    rule = Path(gate.__file__).with_name("MLB_PROPS_EXECUTION_RULE_V2.md")
    assert hashlib.sha256(rule.read_bytes()).hexdigest() == gate.RULE_SHA256_V2


def test_reader_does_not_touch_outcomes_before_read_time(tmp_path, monkeypatch):
    rows = []
    start = date(2026, 7, 22)
    for offset in range(27):
        rows.append(market_row(start + timedelta(days=offset)))
    write_rows(tmp_path / "snapshots_all.jsonl", rows)

    def forbidden(*args, **kwargs):
        raise AssertionError("outcomes were touched while the reader was blind")

    monkeypatch.setattr(gate, "load_settlements", forbidden)
    monkeypatch.setattr(gate, "fetch_result", forbidden)
    result = gate.run(
        tmp_path, datetime(2026, 8, 17, 23, tzinfo=timezone.utc),
        tmp_path / "settlements.json", fetch=True)

    assert result["progress"] == 26  # August 17 is still incomplete in ET.
    assert result["verdict"] == "NO DECISION"
    assert result["outcomes_read"] is False


def test_first_27_days_freeze_and_pass_only_after_settlements(tmp_path):
    rows = []
    start = date(2026, 7, 22)
    for offset in range(29):
        rows.append(market_row(start + timedelta(days=offset), size=100))
    write_rows(tmp_path / "snapshots_all.jsonl", rows)
    settlements = {row["tk"]: "yes" for row in rows}
    cache = tmp_path / "settlements.json"
    cache.write_text(json.dumps(settlements), encoding="utf-8")

    result = gate.run(
        tmp_path, datetime(2026, 8, 21, 12, tzinfo=timezone.utc), cache)

    assert result["progress"] == 29
    assert len(result["selected_days"]) == 27
    assert result["selected_days"][-1] == "2026-08-17"
    assert result["verdict"] == "BUILD_CANDIDATE"
    assert result["days"] == 27
    assert result["mean_daily_displayed_pnl_usd"] > 10


def test_finalized_scalar_makes_locked_sample_unscorable(tmp_path):
    rows = []
    start = date(2026, 7, 22)
    for offset in range(27):
        rows.append(market_row(start + timedelta(days=offset), size=100))
    write_rows(tmp_path / "snapshots_all.jsonl", rows)
    settlements = {row["tk"]: "yes" for row in rows}
    settlements[rows[0]["tk"]] = "scalar"
    cache = tmp_path / "settlements.json"
    cache.write_text(json.dumps(settlements), encoding="utf-8")

    result = gate.run(
        tmp_path, datetime(2026, 8, 21, 12, tzinfo=timezone.utc), cache)

    assert result["verdict"] == "UNSCORABLE"
    assert result["scalar_settlements"] == [rows[0]["tk"]]
    assert "no substitution or sample extension" in result["reason"]
    assert result["authorization"] == (
        "none; successor study requires a new locked rule")


def test_v2_is_disjoint_and_outcome_blind_until_locked_read_time(
        tmp_path, monkeypatch):
    start = date(2026, 8, 18)
    rows = [market_row(start + timedelta(days=offset)) for offset in range(28)]
    write_rows(tmp_path / "snapshots_all.jsonl", rows)

    def forbidden(*args, **kwargs):
        raise AssertionError("V2 outcomes were touched while reader was blind")

    monkeypatch.setattr(gate, "load_settlements_v2", forbidden)
    monkeypatch.setattr(gate, "fetch_settlement_v2", forbidden)
    result = gate.run_v2(
        tmp_path, datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc),
        tmp_path / "settlements_v2.json", fetch=True)

    assert result["clean_start_et"] == "2026-08-19"
    assert result["selected_days"][0] == "2026-08-19"
    assert result["selected_days"][-1] == "2026-09-14"
    assert result["progress"] == 27
    assert result["outcomes_read"] is False


def test_v2_scalar_counts_at_realized_payout(tmp_path):
    start = date(2026, 8, 19)
    rows = [market_row(start + timedelta(days=offset), size=100)
            for offset in range(27)]
    write_rows(tmp_path / "snapshots_all.jsonl", rows)
    settlements = {
        row["tk"]: {"result": "yes", "settlement_value_dollars": None}
        for row in rows
    }
    settlements[rows[0]["tk"]] = {
        "result": "scalar", "settlement_value_dollars": "0.34"}
    cache = tmp_path / "settlements_v2.json"
    cache.write_text(json.dumps(settlements), encoding="utf-8")

    result = gate.run_v2(
        tmp_path, datetime(2026, 9, 16, 12, tzinfo=timezone.utc), cache)

    fee = 0.07 * 0.28 * 0.72
    assert result["verdict"] == "BUILD_CANDIDATE"
    assert result["scalar_settlements_count"] == 1
    assert result["daily_results"][0]["net_per_contract"] == pytest.approx(
        0.34 - 0.28 - fee)


def test_v2_scalar_cents_fallback_and_invalid_values():
    assert gate.settlement_payout_v2(
        {"result": "scalar", "settlement_value": 34}) == pytest.approx(0.34)
    assert gate.settlement_payout_v2(
        {"result": "scalar", "settlement_value_dollars": 1.01}) is None
    assert gate.settlement_payout_v2({"result": "scalar"}) is None


def test_v2_invalid_finalized_scalar_is_data_error(tmp_path):
    start = date(2026, 8, 19)
    rows = [market_row(start + timedelta(days=offset)) for offset in range(27)]
    write_rows(tmp_path / "snapshots_all.jsonl", rows)
    settlements = {row["tk"]: {"result": "yes"} for row in rows}
    settlements[rows[0]["tk"]] = {"result": "scalar"}
    cache = tmp_path / "settlements_v2.json"
    cache.write_text(json.dumps(settlements), encoding="utf-8")

    result = gate.run_v2(
        tmp_path, datetime(2026, 9, 16, 12, tzinfo=timezone.utc), cache)

    assert result["verdict"] == "DATA_ERROR"
    assert result["authorization"] == "none"
    assert result["invalid_scalar_settlements"] == [rows[0]["tk"]]


def test_gate_estimator_equal_weights_days_not_markets():
    days = [(date(2026, 7, 22) + timedelta(days=i)).isoformat()
            for i in range(27)]
    entries = {}
    results = {}
    for index, day in enumerate(days):
        count = 100 if index == 0 else 1
        entries[day] = []
        for market in range(count):
            name = f"T-{index}-{market}"
            entries[day].append({"ticker": name, "yes_ask": 0.20,
                                 "ask_size": 100})
            results[name] = "no" if index == 0 else "yes"

    evaluated = gate.evaluate(entries, days, results, {"progress": 27})

    losing = -0.20 - 0.07 * 0.20 * 0.80
    winning = 1.0 + losing
    expected = (losing + 26 * winning) / 27
    assert evaluated["daily_equal_net_per_contract"] == pytest.approx(expected)
    assert evaluated["market_weighted_net_per_contract"] < 0
    assert evaluated["daily_equal_net_per_contract"] > 0


@pytest.mark.parametrize("field,value", [
    ("yb", 0.0), ("ya", 0.46), ("yas", 0), ("vol", 49),
])
def test_entry_filters_are_locked(field, value):
    row = market_row(date(2026, 7, 22))
    translated = {"yb": "yes_bid", "ya": "yes_ask",
                  "yas": "ask_size", "vol": "volume"}
    candidate = {
        "yes_bid": row["yb"], "yes_ask": row["ya"],
        "ask_size": row["yas"], "volume": row["vol"],
    }
    candidate[translated[field]] = value
    assert gate.is_entry(candidate) is False
