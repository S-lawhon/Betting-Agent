"""P-029 heartbeat health check — the pass/fail rules and the attestation.

The contract under test: a heartbeat row is appended ONLY for a passing
check, carries `timestamp_utc` (the key manager/collect.py's row_ts reads),
and the registry's staleness threshold therefore measures real evidence.
"""
import json

from scripts.p029_health_check import (
    ARCHIVE_MAX_AGE_S,
    SHADOW_MAX_DB_AGE_S,
    append_heartbeat,
    evaluate,
)

HEALTHY = {
    "shadow_unit": "active",
    "shadow_db_age_s": 300.0,
    "shadow_db_bytes": 10_000_000,
    "archive_unit": "inactive",
    "archive_newest": "settled_2026-07-30.jsonl.gz",
    "archive_newest_age_s": 4 * 3600.0,
    "disk_used_pct": 41.2,
}


def test_healthy_box_passes_both():
    v = evaluate(HEALTHY)
    assert v["p029_shadow"]["ok"] and v["p029_archive"]["ok"]


def test_stalled_shadow_db_fails_even_with_active_unit():
    # The known failure mode: SQLite-lock-guarded second copy silently stops
    # collecting while systemd still says active. mtime is the only signal.
    facts = dict(HEALTHY, shadow_db_age_s=SHADOW_MAX_DB_AGE_S + 1)
    assert not evaluate(facts)["p029_shadow"]["ok"]


def test_missing_shadow_db_fails():
    assert not evaluate(dict(HEALTHY, shadow_db_age_s=None))["p029_shadow"]["ok"]


def test_inactive_shadow_unit_fails():
    assert not evaluate(dict(HEALTHY, shadow_unit="inactive"))["p029_shadow"]["ok"]


def test_archive_activating_is_a_run_in_progress_not_a_failure():
    # `systemctl is-active` exits non-zero for `activating`, and a good run
    # takes ~3h. Only `failed` (TimeoutStartSec catches wedges) or a stale
    # newest file fails the check.
    assert evaluate(dict(HEALTHY, archive_unit="activating"))["p029_archive"]["ok"]


def test_failed_archive_unit_fails_despite_fresh_file():
    assert not evaluate(dict(HEALTHY, archive_unit="failed"))["p029_archive"]["ok"]


def test_stale_archive_fails():
    facts = dict(HEALTHY, archive_newest_age_s=ARCHIVE_MAX_AGE_S + 1)
    assert not evaluate(facts)["p029_archive"]["ok"]


def test_heartbeat_row_carries_timestamp_utc(tmp_path):
    v = evaluate(HEALTHY)
    path = append_heartbeat("p029_shadow", HEALTHY, v["p029_shadow"],
                            hb_dir=tmp_path)
    row = json.loads(path.read_text().strip().splitlines()[-1])
    assert row["timestamp_utc"]  # the key manager/collect.py row_ts() reads
    assert row["ok"] is True
    assert path.name == "p029_shadow.jsonl"

    # collect.py must parse it and derive a content age.
    from manager.collect import last_json_row, row_ts
    assert row_ts(last_json_row(path)) is not None
