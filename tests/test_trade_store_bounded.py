"""
tests/test_trade_store_bounded.py
─────────────────────────────────
Tests for TradeStore's bounded (memory-capped) load path.

The bounded loader streams the entire log but only retains the most recent
``max_entries`` full records PLUS every still-open position, while keeping the
complete fingerprint set for dedup.  This is the startup-OOM defence added
after the May–Jun 2026 crash loop (476 MB log exhausted a 1.9 GB VPS).
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.trade_store import TradeStore


def _now():
    return datetime(2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc)


def _placed(fp, ticker, ts, pod_id="P-006", size=100.0):
    return {
        "fingerprint": fp,
        "action": "PLACED",
        "pod_id": pod_id,
        "pod_name": "Test Pod",
        "venue": "polymarket",
        "market_id": ticker,
        "side": "YES",
        "venue_prob": 0.50,
        "position_size_usd": size,
        "timestamp_utc": ts,
        "mode": "paper",
    }


def _settlement(fp, ticker, ts, outcome="WIN", pnl=10.0, pod_id="P-006"):
    return {
        "fingerprint": fp,
        "outcome": outcome,
        "pnl_usd": pnl,
        "market_id": ticker,
        "pod_id": pod_id,
        "settled_at_utc": ts,
        "timestamp_utc": ts,
    }


def _ts(i):
    """Monotonic ISO timestamps so file order == chronological order."""
    return f"2026-06-{1 + (i // 1440):02d}T{(i // 60) % 24:02d}:{i % 60:02d}:00"


def _write_log(lines):
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for obj in lines:
        tmp.write(json.dumps(obj) + "\n")
    tmp.close()
    return Path(tmp.name)


class TestBoundedLoad(TestCase):
    def test_unbounded_loads_everything(self):
        """max_entries=None preserves original full-load behaviour."""
        lines = []
        for i in range(100):
            lines.append(_placed(f"f{i}", f"T{i}", _ts(i)))
        path = _write_log(lines)
        store = TradeStore.from_log(path)
        self.assertFalse(store.bounded)
        self.assertEqual(store.entry_count, 100)
        self.assertEqual(store.open_count, 100)

    def test_tail_cap_limits_retained_entries(self):
        """With no open positions, only the last max_entries are retained."""
        lines = []
        for i in range(100):
            # each market placed then immediately settled → nothing stays open
            lines.append(_placed(f"f{i}", f"T{i}", _ts(2 * i)))
            lines.append(_settlement(f"f{i}", f"T{i}", _ts(2 * i + 1)))
        path = _write_log(lines)
        store = TradeStore.from_log(path, max_entries=10)
        self.assertTrue(store.bounded)
        # 200 lines scanned, only 10 retained as full records
        self.assertEqual(store.total_scanned, 200)
        self.assertEqual(store.entry_count, 10)
        self.assertEqual(store.open_count, 0)

    def test_full_dedup_set_preserved_under_cap(self):
        """Every fingerprint is still seen for dedup, even beyond the tail."""
        lines = [_placed(f"f{i}", f"T{i}", _ts(i)) for i in range(100)]
        # settle them so they don't all stay open
        lines += [_settlement(f"f{i}", f"T{i}", _ts(100 + i)) for i in range(100)]
        path = _write_log(lines)
        store = TradeStore.from_log(path, max_entries=5)
        # Old fingerprint dropped from full-record index...
        self.assertIsNone(store.get_by_fingerprint("f0"))
        # ...but still recognised for dedup.
        self.assertTrue(store.is_fingerprint_seen("f0"))
        self.assertEqual(len(store.seen_fingerprints), 100)

    def test_old_open_position_preserved_beyond_tail(self):
        """A still-open PLACED older than the tail window must be retained."""
        lines = [_placed("OLD_OPEN", "OLD", _ts(0))]  # never settled, line 0
        # filler: 60 settled pairs pushing the open trade out of the tail
        for i in range(1, 61):
            lines.append(_placed(f"f{i}", f"T{i}", _ts(2 * i)))
            lines.append(_settlement(f"f{i}", f"T{i}", _ts(2 * i + 1)))
        path = _write_log(lines)
        store = TradeStore.from_log(path, max_entries=10)

        # The old open position survives despite being far outside the tail.
        self.assertTrue(store.is_position_open("OLD"))
        self.assertEqual(store.open_count, 1)
        open_trades = store.get_open_trades()
        self.assertEqual(len(open_trades), 1)
        self.assertEqual(open_trades[0]["fingerprint"], "OLD_OPEN")
        self.assertIn("OLD", store.get_open_market_ids())
        # Bootstrap view also sees it (risk guard relies on this).
        boot = store.get_open_positions_for_bootstrap()
        self.assertEqual(len(boot), 1)
        self.assertEqual(boot[0]["market_id"], "OLD")

    def test_settled_in_tail_not_open(self):
        """A position placed and settled within the tail is not open."""
        lines = []
        for i in range(20):
            lines.append(_placed(f"f{i}", f"T{i}", _ts(2 * i)))
            lines.append(_settlement(f"f{i}", f"T{i}", _ts(2 * i + 1)))
        path = _write_log(lines)
        store = TradeStore.from_log(path, max_entries=40)  # whole file fits
        self.assertEqual(store.open_count, 0)

    def test_dashboard_view_works_under_cap(self):
        """Recent-placed-with-status still resolves settlements in the tail."""
        lines = []
        for i in range(30):
            lines.append(_placed(f"f{i}", f"T{i}", _ts(2 * i)))
            lines.append(_settlement(f"f{i}", f"T{i}", _ts(2 * i + 1),
                                     outcome="WIN", pnl=5.0))
        path = _write_log(lines)
        store = TradeStore.from_log(path, max_entries=20)
        recent = store.get_recent_placed_with_status()
        self.assertTrue(all(r["status"] in ("WIN", "OPEN") for r in recent))
        # At least the tail's settled trades resolve to WIN.
        self.assertTrue(any(r["status"] == "WIN" for r in recent))

    def test_instance_level_cap_used_by_load(self):
        """max_entries passed to __init__ is honoured by load()."""
        lines = [_placed(f"f{i}", f"T{i}", _ts(i)) for i in range(50)]
        # settle all but keep memory bounded
        lines += [_settlement(f"f{i}", f"T{i}", _ts(50 + i)) for i in range(50)]
        path = _write_log(lines)
        store = TradeStore(log_path=path, _now_fn=_now, max_entries=8)
        store.load()
        self.assertTrue(store.bounded)
        self.assertEqual(store.entry_count, 8)

    def test_append_after_bounded_load_still_works(self):
        """New trades append and index correctly on top of a bounded load."""
        lines = []
        for i in range(40):
            lines.append(_placed(f"f{i}", f"T{i}", _ts(2 * i)))
            lines.append(_settlement(f"f{i}", f"T{i}", _ts(2 * i + 1)))
        path = _write_log(lines)
        store = TradeStore.from_log(path, max_entries=10)
        before = store.open_count
        store.append(_placed("NEW", "NEWMKT", _ts(1000)))
        self.assertEqual(store.open_count, before + 1)
        self.assertTrue(store.is_position_open("NEWMKT"))
        self.assertTrue(store.is_fingerprint_seen("NEW"))
