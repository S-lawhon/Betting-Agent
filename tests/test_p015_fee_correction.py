import json
from pathlib import Path

import pytest

from scripts import apply_p015_fee_correction as correction


def _legacy(outcome="WIN", result="yes"):
    return {
        "pod_id": "P-015",
        "action": outcome,
        "outcome": outcome,
        "resolution_source": "kalshi_api",
        "result": result,
        "side": "YES",
        "position_size_usd": 90.0,
        "fill_price": 0.90,
        "pnl_usd": 10.0 if outcome == "WIN" else -90.0,
        "fingerprint": "fp-tennis",
        "extra": {"contracts": 100, "taker_fee": 0.0063},
    }


def test_correct_record_books_fee_net_and_preserves_outcome():
    fixed, audit = correction.correct_record(_legacy())

    assert fixed["outcome"] == fixed["action"] == "WIN"
    assert fixed["pnl_gross_usd"] == 10.0
    assert fixed["fees_usd"] == pytest.approx(0.63)
    assert fixed["pnl_usd"] == pytest.approx(9.37)
    assert fixed["fee_accounting_version"] == correction.VERSION
    assert audit["fees_usd"] == pytest.approx(0.63)


def test_correct_record_handles_loss_fee():
    fixed, _ = correction.correct_record(_legacy("LOSS", "no"))
    assert fixed["pnl_gross_usd"] == -90.0
    assert fixed["pnl_usd"] == -90.63


def test_missing_legacy_resolution_source_requires_same_reproducible_proof():
    row = _legacy()
    row.pop("resolution_source")
    fixed, audit = correction.correct_record(row)
    assert fixed["pnl_usd"] == pytest.approx(9.37)
    assert audit["fees_usd"] == pytest.approx(0.63)


def test_already_corrected_and_other_pods_are_inert():
    already = _legacy()
    already["fees_usd"] = 0.63
    other = {**_legacy(), "pod_id": "P-017"}
    for row in (already, other):
        same, audit = correction.correct_record(row)
        assert same is row
        assert audit is None


def test_unrelated_resolution_source_is_inert():
    row = {**_legacy(), "resolution_source": "manual_review"}
    same, audit = correction.correct_record(row)
    assert same is row
    assert audit is None


def test_refuses_when_booked_pnl_is_not_reproducible():
    row = _legacy()
    row["pnl_usd"] = 999
    with pytest.raises(correction.CorrectionError, match="gross P&L mismatch"):
        correction.correct_record(row)


def test_plan_preserves_non_target_lines_and_is_idempotent(tmp_path):
    path = tmp_path / "trade_log.jsonl"
    unrelated = '{"pod_id":"P-001","action":"PLACED"}\n'
    path.write_text(unrelated + json.dumps(_legacy()) + "\n", encoding="utf-8")

    plan = correction.plan_file(path)
    assert plan.corrected == 1
    assert plan.lines[0] == unrelated
    backup = correction._write_plan(plan, "TEST")
    assert backup.exists()
    assert correction.plan_file(path).corrected == 0


def test_summary_reports_exact_delta(tmp_path):
    path = tmp_path / "trade_log.jsonl"
    path.write_text(
        json.dumps(_legacy()) + "\n" +
        json.dumps(_legacy("LOSS", "no") | {"fingerprint": "fp-loss"}) + "\n",
        encoding="utf-8",
    )
    result = correction.summary([correction.plan_file(path)])
    assert result["rows_corrected"] == 2
    assert result["gross_pnl_usd"] == -80.0
    assert result["fees_usd"] == pytest.approx(1.26)
    assert result["net_pnl_usd"] == -81.26
    assert result["pnl_delta_usd"] == pytest.approx(-1.26)


def test_inventory_deduplicates_and_classifies_without_trade_details(tmp_path):
    path = tmp_path / "trade_log.jsonl"
    legacy = _legacy()
    corrected = {
        **_legacy(),
        "fingerprint": "fp-corrected",
        "fees_usd": 0.63,
        "pnl_gross_usd": 10.0,
        "fee_accounting_version": correction.VERSION,
    }
    path.write_text(
        json.dumps(legacy) + "\n" + json.dumps(legacy) + "\n" +
        json.dumps(corrected) + "\n",
        encoding="utf-8",
    )

    result = correction.settlement_inventory([path])

    assert result["unique_terminal_rows"] == 2
    assert sum(group["rows"] for group in result["groups"]) == 2
    assert all("fingerprint" not in group for group in result["groups"])
