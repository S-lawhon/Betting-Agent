"""
tests/test_base_pod.py
──────────────────────
Tests for ScanResult dataclass and BasePod abstract class.

  ✓ ScanResult: frozen dataclass with all fields accessible
  ✓ ScanResult: to_dict() produces correct dict
  ✓ ScanResult: default optional fields (order_id, fill_price, skip_reason, extra)
  ✓ ScanResult: extra dict preserved in to_dict
  ✓ BasePod: make_fingerprint returns 64-char hex SHA-256
  ✓ BasePod: same market/side/hour → same fingerprint
  ✓ BasePod: different side → different fingerprint
  ✓ BasePod: different hour → different fingerprint
  ✓ BasePod: is_duplicate returns False for new fingerprint
  ✓ BasePod: is_duplicate returns True after mark_seen
  ✓ BasePod: write_log creates JSONL entry
  ✓ BasePod: write_log appends (does not overwrite)
  ✓ BasePod: write_log error does not crash
  ✓ BasePod: _load_seen_from_log restores PLACED fingerprints
  ✓ BasePod: _load_seen_from_log ignores non-PLACED entries
  ✓ BasePod: _load_seen_from_log handles missing log gracefully
  ✓ BasePod: _load_seen_from_log handles corrupt JSON gracefully
  ✓ BasePod: cannot instantiate directly (abstract)
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.base_pod import BasePod, ScanResult


# ── Concrete test pod ───────────────────────────────────────────────

class _TestPod(BasePod):
    """Minimal concrete pod for testing BasePod infrastructure."""

    def __init__(self, results=None, **kwargs):
        super().__init__(**kwargs)
        self._results = results or []

    def scan_once(self) -> List[ScanResult]:
        return list(self._results)

    def venue_name(self) -> str:
        return "test"

    @classmethod
    def from_config(cls, config, **overrides):
        return cls(**overrides)


# ── Fixtures ────────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)


class _MockEdge:
    pass


class _MockRisk:
    pass


def _pod(log_path=None, now_fn=None) -> _TestPod:
    return _TestPod(
        pod_id="P-TEST",
        pod_name="Test Pod",
        edge_calculator=_MockEdge(),
        risk_manager=_MockRisk(),
        trade_log_path=log_path or Path(tempfile.mktemp(suffix=".jsonl")),
        mode="paper",
        _now_fn=now_fn or (lambda: _NOW),
    )


def _scan_result(**overrides) -> ScanResult:
    defaults = dict(
        fingerprint="abc123",
        timestamp_utc=_NOW.isoformat(),
        pod_id="P-TEST",
        pod_name="Test Pod",
        mode="paper",
        venue="test",
        market_type="sports_nfl",
        event="Chiefs vs Raiders",
        market_id="NFL-KC-LV",
        side="YES",
        fair_prob=0.62,
        venue_prob=0.57,
        edge_pct=0.05,
        ev=0.03,
        kelly_fraction=0.005,
        position_size_usd=50.0,
        action="PLACED",
    )
    defaults.update(overrides)
    return ScanResult(**defaults)


# ── ScanResult ──────────────────────────────────────────────────────

class TestScanResult(unittest.TestCase):

    def test_frozen_dataclass(self):
        sr = _scan_result()
        with self.assertRaises((AttributeError, TypeError)):
            sr.pod_id = "changed"

    def test_all_fields_accessible(self):
        sr = _scan_result()
        self.assertEqual(sr.pod_id, "P-TEST")
        self.assertEqual(sr.venue, "test")
        self.assertEqual(sr.action, "PLACED")
        self.assertAlmostEqual(sr.fair_prob, 0.62)

    def test_default_optional_fields(self):
        sr = _scan_result()
        self.assertIsNone(sr.order_id)
        self.assertIsNone(sr.fill_price)
        self.assertIsNone(sr.skip_reason)
        self.assertEqual(sr.extra, {})

    def test_to_dict(self):
        sr = _scan_result(order_id="ORD-1", fill_price=0.57)
        d = sr.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["pod_id"], "P-TEST")
        self.assertEqual(d["order_id"], "ORD-1")
        self.assertAlmostEqual(d["fill_price"], 0.57)

    def test_extra_dict_preserved(self):
        sr = _scan_result(extra={"hedge_leg": "OTHER-TICKER"})
        d = sr.to_dict()
        self.assertEqual(d["extra"]["hedge_leg"], "OTHER-TICKER")


# ── BasePod: fingerprint ────────────────────────────────────────────

class TestMakeFingerprint(unittest.TestCase):

    def test_returns_64_char_hex(self):
        pod = _pod()
        fp = pod.make_fingerprint("NFL-KC-LV", "YES")
        self.assertEqual(len(fp), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))

    def test_same_inputs_same_output(self):
        pod = _pod()
        fp1 = pod.make_fingerprint("NFL-KC-LV", "YES")
        fp2 = pod.make_fingerprint("NFL-KC-LV", "YES")
        self.assertEqual(fp1, fp2)

    def test_different_side_different_fp(self):
        pod = _pod()
        fp_yes = pod.make_fingerprint("NFL-KC-LV", "YES")
        fp_no = pod.make_fingerprint("NFL-KC-LV", "NO")
        self.assertNotEqual(fp_yes, fp_no)

    def test_different_market_different_fp(self):
        pod = _pod()
        fp1 = pod.make_fingerprint("NFL-KC-LV", "YES")
        fp2 = pod.make_fingerprint("NBA-LAL-BOS", "YES")
        self.assertNotEqual(fp1, fp2)

    def test_same_hour_same_fp(self):
        t1 = _NOW
        t2 = _NOW + timedelta(minutes=30)
        pod1 = _pod(now_fn=lambda: t1)
        pod2 = _pod(now_fn=lambda: t2)
        fp1 = pod1.make_fingerprint("T", "YES")
        fp2 = pod2.make_fingerprint("T", "YES")
        self.assertEqual(fp1, fp2)

    def test_different_hour_different_fp(self):
        t1 = _NOW
        t2 = _NOW + timedelta(hours=1)
        pod1 = _pod(now_fn=lambda: t1)
        pod2 = _pod(now_fn=lambda: t2)
        fp1 = pod1.make_fingerprint("T", "YES")
        fp2 = pod2.make_fingerprint("T", "YES")
        self.assertNotEqual(fp1, fp2)


# ── BasePod: dedup ──────────────────────────────────────────────────

class TestDedup(unittest.TestCase):

    def test_new_fingerprint_not_duplicate(self):
        pod = _pod()
        self.assertFalse(pod.is_duplicate("new-fp"))

    def test_after_mark_seen_is_duplicate(self):
        pod = _pod()
        pod.mark_seen("fp-1")
        self.assertTrue(pod.is_duplicate("fp-1"))

    def test_mark_seen_does_not_affect_other_fps(self):
        pod = _pod()
        pod.mark_seen("fp-1")
        self.assertFalse(pod.is_duplicate("fp-2"))


# ── BasePod: write_log ──────────────────────────────────────────────

class TestWriteLog(unittest.TestCase):

    def test_creates_jsonl_entry(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        pod = _pod(log_path=log_path)
        sr = _scan_result()
        pod.write_log(sr)
        self.assertTrue(log_path.exists())
        rec = json.loads(log_path.read_text().strip())
        self.assertEqual(rec["pod_id"], "P-TEST")
        self.assertEqual(rec["action"], "PLACED")

    def test_appends_not_overwrites(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        pod = _pod(log_path=log_path)
        pod.write_log(_scan_result(market_id="T1"))
        pod.write_log(_scan_result(market_id="T2"))
        lines = log_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)

    def test_write_error_does_not_crash(self):
        bad_path = Path("/dev/null/not_a_dir/log.jsonl")
        pod = _pod(log_path=bad_path)
        # Should not raise
        pod.write_log(_scan_result())


# ── BasePod: _load_seen_from_log ────────────────────────────────────

class TestLoadSeenFromLog(unittest.TestCase):

    def test_restores_placed_fingerprints(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        # Write a PLACED entry
        entry = {"action": "PLACED", "fingerprint": "fp-placed"}
        log_path.write_text(json.dumps(entry) + "\n")
        pod = _pod(log_path=log_path)
        self.assertTrue(pod.is_duplicate("fp-placed"))

    def test_ignores_non_placed_entries(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        entry = {"action": "SKIPPED_EDGE", "fingerprint": "fp-skipped"}
        log_path.write_text(json.dumps(entry) + "\n")
        pod = _pod(log_path=log_path)
        self.assertFalse(pod.is_duplicate("fp-skipped"))

    def test_missing_log_is_silent(self):
        pod = _pod(log_path=Path("/tmp/does_not_exist_xyz_123.jsonl"))
        self.assertEqual(len(pod._seen_fingerprints), 0)

    def test_corrupt_json_handled_gracefully(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        log_path.write_text('{"action": "PLACED", "fingerprint": "good"}\nnot-json\n')
        pod = _pod(log_path=log_path)
        # Good line should still be loaded
        self.assertTrue(pod.is_duplicate("good"))

    def test_empty_lines_handled(self):
        log_path = Path(tempfile.mktemp(suffix=".jsonl"))
        log_path.write_text('\n\n{"action": "PLACED", "fingerprint": "fp1"}\n\n')
        pod = _pod(log_path=log_path)
        self.assertTrue(pod.is_duplicate("fp1"))


# ── BasePod: abstract enforcement ───────────────────────────────────

class TestAbstract(unittest.TestCase):

    def test_cannot_instantiate_directly(self):
        with self.assertRaises(TypeError):
            BasePod(
                pod_id="X", pod_name="X",
                edge_calculator=None, risk_manager=None,
                trade_log_path=Path("/tmp/x.jsonl"),
            )


if __name__ == "__main__":
    unittest.main()
