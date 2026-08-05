"""
tests/test_rotate_active_log.py
───────────────────────────────
Regression tests for the silent-settlement failure found 2026-07-26.

The bug: the droplet's shell rotator gzipped ``trade_log.jsonl`` and then
truncated it (``: > "$LOG"``).  ``TradeStore.load()`` reads only the active
log, so at the next process restart every PLACED row that had not yet settled
vanished from the store.  ``store.get_open_trades("P-017")`` then returned
nothing, ``KalshiGolfSettler.settle_cycle()`` returned at its
``if not open_entries`` guard, and 16 live 3M Open positions ($146.96) became
permanently unsettleable — with no error anywhere.  The settler's own
stale-timeout backstop could not fire either, because it lives inside the loop
over ``open_entries``.

The tests below pin the two properties that stop it recurring:

  1. rotation carries still-open PLACED rows into the new active log, so a
     TradeStore built from that log still sees them as open;
  2. the rows are carried VERBATIM (same fingerprint), because
     ``TradeStore._index_entry`` pairs settlements to placements by
     fingerprint and settlement records are ``{**placed_entry, "action": ...}``.

The end-to-end test wires the real TradeStore and the real settler together,
because every unit in the chain was individually correct — the failure only
existed at the seam.
"""

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.rotate_active_log import (
    MANIFEST_NAME,
    _prune_archives,
    month_key,
    open_placed_rows,
    recent_archives,
    recover_open_from_archives,
    rotate,
)
from src.trade_store import TradeStore

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def _placed(fp, ticker, pod="P-017", size=10.0):
    return {
        "fingerprint": fp,
        "timestamp_utc": "2026-07-21T03:22:58+00:00",
        "pod_id": pod,
        "market_id": ticker,
        "side": "YES",
        "position_size_usd": size,
        "fill_price": 0.08,
        "action": "PLACED",
        "extra": {"contracts": 124, "taker_fee": 0.0052,
                  "close_utc": "2026-07-26T00:00:00+00:00"},
    }


def _settled(placed, outcome="LOSS"):
    return {**placed, "action": outcome, "outcome": outcome, "result": "no",
            "pnl_usd": -10.54, "settled_at_utc": "2026-07-25T00:42:08+00:00"}


def _skipped(i):
    # The 99% of the log that is noise; also the reason rotation exists.
    return {"fingerprint": f"skip{i}", "pod_id": "P-017", "action": "SKIPPED_RISK",
            "market_id": "KXPGATOP20-3MO26-XXX", "timestamp_utc": "2026-07-21T03:22:58+00:00"}


def _write(path: Path, entries) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


# ── open_placed_rows: the pairing rule ───────────────────────────────

def test_open_rows_exclude_settled_and_keep_unsettled():
    p1, p2 = _placed("fp1", "AAA"), _placed("fp2", "BBB")
    lines = [json.dumps(e) for e in (p1, p2, _settled(p1))]
    out = open_placed_rows([lines])
    assert set(out) == {"fp2"}


def test_open_rows_pair_settlement_by_fingerprint_not_ticker():
    """Settlements reuse the placement's fingerprint; two placements on the
    same ticker must not both be closed by one settlement."""
    p1 = _placed("fp1", "AAA")
    p2 = _placed("fp2", "AAA")
    lines = [json.dumps(e) for e in (p1, p2, _settled(p1))]
    out = open_placed_rows([lines])
    assert set(out) == {"fp2"}


def test_open_rows_ignore_skipped_noise():
    lines = [json.dumps(e) for e in ([_skipped(i) for i in range(50)] + [_placed("fp1", "AAA")])]
    assert set(open_placed_rows([lines])) == {"fp1"}


def test_open_rows_settlement_in_later_source_closes_earlier_placement():
    """Placement in the archive, settlement in the active log."""
    p1 = _placed("fp1", "AAA")
    archive = [json.dumps(p1)]
    active = [json.dumps(_settled(p1))]
    assert open_placed_rows([archive, active]) == {}


def test_bankroll_reset_clears_the_book():
    p1 = _placed("fp1", "AAA")
    lines = [json.dumps(p1), json.dumps({"fingerprint": "r", "action": "BANKROLL_RESET"})]
    assert open_placed_rows([lines]) == {}


def test_zero_size_placement_is_not_an_open_position():
    lines = [json.dumps(_placed("fp1", "AAA", size=0.0))]
    assert open_placed_rows([lines]) == {}


# ── rotate() ─────────────────────────────────────────────────────────

def test_rotation_under_cap_is_a_noop(tmp_path):
    log = _write(tmp_path / "trade_log.jsonl", [_placed("fp1", "AAA")])
    before = log.read_text()
    out = rotate(log, cap_bytes=10 ** 9, _now_fn=lambda: NOW)
    assert out["rotated"] is False
    assert log.read_text() == before


def test_rotation_carries_open_positions_and_drops_the_rest(tmp_path):
    p1, p2 = _placed("fp1", "AAA"), _placed("fp2", "BBB")
    log = _write(
        tmp_path / "trade_log.jsonl",
        [_skipped(i) for i in range(200)] + [p1, p2, _settled(p1)],
    )
    out = rotate(log, cap_bytes=10, _now_fn=lambda: NOW)

    assert out["rotated"] is True
    assert out["carried_open"] == 1

    kept = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    assert [k["fingerprint"] for k in kept] == ["fp2"]

    # Nothing is lost — the full history is in the archive.
    with gzip.open(out["archive"], "rt") as fh:
        archived = fh.read().splitlines()
    assert len(archived) == 203


def test_rotation_carries_rows_verbatim(tmp_path):
    """Byte-identical carry-forward: the fingerprint and every field a
    settlement record copies forward must survive untouched."""
    p2 = _placed("fp2", "BBB")
    log = _write(tmp_path / "trade_log.jsonl", [p2])
    rotate(log, cap_bytes=10, _now_fn=lambda: NOW)
    assert json.loads(log.read_text().strip()) == p2


def test_rotation_keeps_open_positions_across_multiple_pods(tmp_path):
    rows = [_placed("fp1", "AAA", pod="P-017"), _placed("fp2", "BBB", pod="P-015"),
            _placed("fp3", "CCC", pod="P-001")]
    log = _write(tmp_path / "trade_log.jsonl", rows)
    rotate(log, cap_bytes=10, _now_fn=lambda: NOW)
    kept = {json.loads(l)["pod_id"] for l in log.read_text().splitlines() if l.strip()}
    assert kept == {"P-017", "P-015", "P-001"}


def test_rotation_prunes_to_twelve_archives(tmp_path):
    log = tmp_path / "trade_log.jsonl"
    for i in range(14):
        _write(log, [_placed(f"fp{i}", "AAA")])
        rotate(log, cap_bytes=1,
               _now_fn=lambda i=i: datetime(2026, 7, 1, 0, 0, i, tzinfo=timezone.utc))
    assert len(recent_archives(log, 100)) == 12


# ── consolidation at prune time ──────────────────────────────────────
#
# Pruning must never delete history: the doomed archive's bytes move into
# archive/YYYY-MM.jsonl.gz and are recorded in the consolidation manifest
# BEFORE the rotated file is unlinked.


def _rotate_n(log: Path, n: int, month: int = 7):
    """n rotations with distinct rows and strictly increasing archive stamps."""
    import os as _os
    for i in range(n):
        _write(log, [_placed(f"fp{i}", "AAA")])
        out = rotate(log, cap_bytes=1,
                     _now_fn=lambda i=i: datetime(2026, month, 1 + i // 60, 0, i % 60,
                                                  tzinfo=timezone.utc))
        # mtime drives recent_archives' ordering; make it deterministic.
        if out.get("archive"):
            _os.utime(out["archive"], (1_000_000 + i, 1_000_000 + i))


def _manifest(log: Path) -> dict:
    return json.loads((log.parent / "archive" / MANIFEST_NAME).read_text())


def test_month_key_parses_the_archive_stamp():
    assert month_key("trade_log.archive_20260628_191829.jsonl.gz") == "2026-06"
    assert month_key("not_an_archive.jsonl.gz") is None


def test_prune_consolidates_into_monthly_archive(tmp_path):
    log = tmp_path / "trade_log.jsonl"
    _rotate_n(log, 14)  # 14 archives -> 2 pruned

    assert len(recent_archives(log, 100)) == 12
    monthly = log.parent / "archive" / "2026-07.jsonl.gz"
    assert monthly.exists()

    # The pruned rows are readable back out (multi-member gzip).
    with gzip.open(monthly, "rt") as fh:
        rows = [json.loads(l) for l in fh.read().splitlines() if l.strip()]
    assert {r["fingerprint"] for r in rows} == {"fp0", "fp1"}

    m = _manifest(log)["monthly"]["2026-07.jsonl.gz"]
    assert len(m["members"]) == 2
    assert m["members"][0]["offset"] == 0
    assert m["members"][1]["offset"] == m["members"][0]["length"]
    assert m["bytes"] == monthly.stat().st_size


def test_prune_members_carry_exact_byte_ranges(tmp_path):
    """The rollup reads members by (offset, length) — they must be exact."""
    log = tmp_path / "trade_log.jsonl"
    _rotate_n(log, 14)
    monthly = log.parent / "archive" / "2026-07.jsonl.gz"
    blob = monthly.read_bytes()
    for mem in _manifest(log)["monthly"]["2026-07.jsonl.gz"]["members"]:
        piece = blob[mem["offset"]:mem["offset"] + mem["length"]]
        rows = [json.loads(l) for l in gzip.decompress(piece).decode().splitlines() if l.strip()]
        assert len(rows) == 1 and rows[0]["action"] == "PLACED"


def test_prune_failure_keeps_the_rotated_archive(tmp_path, monkeypatch):
    """Fail closed: if the bytes cannot be consolidated, nothing is deleted."""
    import scripts.rotate_active_log as ral
    log = tmp_path / "trade_log.jsonl"
    _rotate_n(log, 12)

    monkeypatch.setattr(ral, "_atomic_append_file",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    _write(log, [_placed("fp_last", "AAA")])
    out = rotate(log, cap_bytes=1, _now_fn=lambda: NOW)

    assert out["rotated"] is True
    assert out["pruned"] == []
    assert len(recent_archives(log, 100)) == 13  # over the cap, but nothing lost
    assert not (log.parent / "archive" / "2026-07.jsonl.gz").exists()


def test_prune_adopts_bytes_from_a_run_that_died_before_the_manifest(tmp_path):
    """Crash between the byte-append and the manifest write must not
    duplicate the member on the next run."""
    log = tmp_path / "trade_log.jsonl"
    _rotate_n(log, 12)
    oldest = recent_archives(log, 100)[0]

    # Simulate the dead run: bytes are already in the monthly file, no manifest.
    mdir = log.parent / "archive"
    mdir.mkdir()
    monthly = mdir / "2026-07.jsonl.gz"
    monthly.write_bytes(oldest.read_bytes())

    pruned = _prune_archives(log, keep=11, now=NOW)
    assert [p.name for p in pruned] == [oldest.name]
    assert not oldest.exists()

    m = _manifest(log)["monthly"]["2026-07.jsonl.gz"]
    assert [x["archive"] for x in m["members"]] == [oldest.name]
    assert m["bytes"] == monthly.stat().st_size  # adopted, not re-appended


def test_prune_refuses_a_monthly_file_it_cannot_explain(tmp_path):
    """Unexplained bytes in the monthly file -> keep the rotated archive."""
    log = tmp_path / "trade_log.jsonl"
    _rotate_n(log, 12)
    oldest = recent_archives(log, 100)[0]

    mdir = log.parent / "archive"
    mdir.mkdir()
    (mdir / "2026-07.jsonl.gz").write_bytes(b"garbage that is no gzip member")

    pruned = _prune_archives(log, keep=11, now=NOW)
    assert pruned == []
    assert oldest.exists()
    manifest_file = mdir / MANIFEST_NAME
    if manifest_file.exists():
        assert _manifest(log)["monthly"].get("2026-07.jsonl.gz", {}).get("members", []) == []


def test_prune_unlinks_an_already_recorded_member_without_reappending(tmp_path):
    """Crash between the manifest write and the unlink: next run just unlinks."""
    log = tmp_path / "trade_log.jsonl"
    _rotate_n(log, 12)
    oldest = recent_archives(log, 100)[0]

    _prune_archives(log, keep=11, now=NOW)
    assert not oldest.exists()
    monthly = log.parent / "archive" / "2026-07.jsonl.gz"
    size_before = monthly.stat().st_size

    # Resurrect the rotated file as if unlink never happened.
    with gzip.open(monthly, "rb") as fh:
        pass  # (sanity: file is a readable gzip)
    member = _manifest(log)["monthly"]["2026-07.jsonl.gz"]["members"][0]
    monthly_blob = monthly.read_bytes()
    oldest.write_bytes(monthly_blob[member["offset"]:member["offset"] + member["length"]])
    import os as _os
    _os.utime(oldest, (1, 1))  # make it the oldest again

    pruned = _prune_archives(log, keep=11, now=NOW)
    assert [p.name for p in pruned] == [oldest.name]
    assert monthly.stat().st_size == size_before  # nothing re-appended
    assert len(_manifest(log)["monthly"]["2026-07.jsonl.gz"]["members"]) == 1


# ── recovery of an already-orphaned book ─────────────────────────────

def test_recover_reappends_orphans_missing_from_the_active_log(tmp_path):
    """The production repair path: placements live only in the archive."""
    log = tmp_path / "trade_log.jsonl"
    p1, p2 = _placed("fp1", "AAA"), _placed("fp2", "BBB")

    _write(log, [p1, p2, _settled(p1)])
    rotate(log, cap_bytes=10, _now_fn=lambda: NOW)   # produces the archive
    log.write_text("", encoding="utf-8")             # simulate the truncating rotator

    out = recover_open_from_archives(log, archive_count=3)
    assert out["recovered"] == 1
    assert out["by_pod"] == {"P-017": 1}
    assert json.loads(log.read_text().strip())["fingerprint"] == "fp2"


def test_recover_scopes_to_named_pods(tmp_path):
    """Unscoped recovery would resurrect settler-less pods' dead exposure —
    177 rows / $1458 on the droplet, of which only 16 were P-017's."""
    log = tmp_path / "trade_log.jsonl"
    _write(log, [_placed("fp1", "AAA", pod="P-017"),
                 _placed("fp2", "BBB", pod="P-002"),
                 _placed("fp3", "CCC", pod="P-006")])
    rotate(log, cap_bytes=10, _now_fn=lambda: NOW)
    log.write_text("", encoding="utf-8")

    out = recover_open_from_archives(log, archive_count=3, pods=["P-017"])
    assert out["recovered"] == 1
    assert out["by_pod"] == {"P-017": 1}
    assert json.loads(log.read_text().strip())["pod_id"] == "P-017"


def test_recover_is_idempotent(tmp_path):
    log = tmp_path / "trade_log.jsonl"
    _write(log, [_placed("fp1", "AAA")])
    rotate(log, cap_bytes=10, _now_fn=lambda: NOW)
    log.write_text("", encoding="utf-8")

    recover_open_from_archives(log, archive_count=3)
    second = recover_open_from_archives(log, archive_count=3)
    assert second["recovered"] == 0
    assert len(log.read_text().splitlines()) == 1


def test_recover_does_not_resurrect_a_position_settled_in_the_archive(tmp_path):
    log = tmp_path / "trade_log.jsonl"
    p1 = _placed("fp1", "AAA")
    _write(log, [p1, _settled(p1)])
    rotate(log, cap_bytes=10, _now_fn=lambda: NOW)
    log.write_text("", encoding="utf-8")
    assert recover_open_from_archives(log, archive_count=3)["recovered"] == 0


# ── end-to-end: the seam that actually broke ─────────────────────────

def _store_open_count(log: Path, pod="P-017") -> int:
    return len(TradeStore(log_path=log).load().get_open_trades(pod))


def test_truncating_rotation_orphans_positions_carrying_rotation_does_not(tmp_path):
    """Reproduces the 2026-07-25 production failure and its fix side by side."""
    rows = [_placed(f"fp{i}", f"KXPGATOP20-3MO26-{i:03d}") for i in range(16)]

    # (a) what the shell rotator did
    bad = _write(tmp_path / "bad" / "trade_log.jsonl", rows)
    bad.write_text("", encoding="utf-8")
    assert _store_open_count(bad) == 0, "precondition: truncation orphans the book"

    # (b) what this rotator does
    good = _write(tmp_path / "good" / "trade_log.jsonl", rows)
    rotate(good, cap_bytes=10, _now_fn=lambda: NOW)
    assert _store_open_count(good) == 16


def test_settler_sees_carried_positions_after_rotation(tmp_path):
    """The real settler, sourcing from the real store, over a rotated log."""
    from src.kalshi_golf_settler import KalshiGolfSettler

    rows = [_placed("fp1", "KXPGATOP20-3MO26-AAA"), _placed("fp2", "KXPGATOP20-3MO26-BBB")]
    log = _write(tmp_path / "trade_log.jsonl", rows)
    rotate(log, cap_bytes=10, _now_fn=lambda: NOW)

    store = TradeStore(log_path=log).load()

    class _Client:
        def get_market(self, ticker):
            return {"status": "finalized", "result": "no", "ticker": ticker}

    settler = KalshiGolfSettler(
        client=_Client(), log_path=tmp_path / "unused.jsonl",
        _now_fn=lambda: NOW, trade_store=store,
    )
    settled = settler.settle_cycle()
    assert len(settled) == 2
    assert {s["outcome"] for s in settled} == {"LOSS"}


def test_settlement_after_carry_forward_closes_the_position(tmp_path):
    """A settlement appended post-rotation must pair with the carried row."""
    log = _write(tmp_path / "trade_log.jsonl", [_placed("fp1", "AAA")])
    rotate(log, cap_bytes=10, _now_fn=lambda: NOW)

    store = TradeStore(log_path=log).load()
    assert store.open_count == 1
    store.append(_settled(_placed("fp1", "AAA")))

    assert _store_open_count(log) == 0
