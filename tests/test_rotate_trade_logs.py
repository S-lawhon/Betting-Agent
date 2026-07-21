"""Tests for scripts/rotate_trade_logs.py."""
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.rotate_trade_logs import rotate


class TestRotateTradeLog(unittest.TestCase):

    def _make_entry(self, days_ago: int, fp: str = "fp") -> dict:
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        return {"timestamp_utc": ts, "fingerprint": fp, "action": "PLACED"}

    def test_no_log_file(self):
        with tempfile.TemporaryDirectory() as d:
            result = rotate(Path(d) / "missing.jsonl", Path(d) / "archive")
            self.assertEqual(result["total"], 0)

    def test_all_recent(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "trade_log.jsonl"
            entries = [self._make_entry(i, f"fp-{i}") for i in range(5)]
            with open(log, "w") as f:
                for e in entries:
                    f.write(json.dumps(e) + "\n")

            result = rotate(log, Path(d) / "archive", cutoff_days=30)
            self.assertEqual(result["archived"], 0)
            self.assertEqual(result["kept"], 5)

    def test_splits_old_and_new(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "trade_log.jsonl"
            archive_dir = Path(d) / "archive"

            entries = []
            # 3 entries from 60 days ago
            for i in range(3):
                entries.append(self._make_entry(60, f"old-{i}"))
            # 2 recent entries
            for i in range(2):
                entries.append(self._make_entry(5, f"new-{i}"))

            with open(log, "w") as f:
                for e in entries:
                    f.write(json.dumps(e) + "\n")

            result = rotate(log, archive_dir, cutoff_days=30)
            self.assertEqual(result["archived"], 3)
            self.assertEqual(result["kept"], 2)

            # Check main log only has recent entries
            with open(log) as f:
                remaining = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(remaining), 2)

            # Check archive exists and is gzipped
            archives = list(archive_dir.glob("*.jsonl.gz"))
            self.assertEqual(len(archives), 1)
            with gzip.open(archives[0], "rt") as f:
                archived = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(archived), 3)

    def test_dry_run_no_changes(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "trade_log.jsonl"
            entries = [self._make_entry(60, f"old-{i}") for i in range(3)]
            with open(log, "w") as f:
                for e in entries:
                    f.write(json.dumps(e) + "\n")

            result = rotate(log, Path(d) / "archive", cutoff_days=30, dry_run=True)
            self.assertEqual(result["archived"], 3)

            # Log should be unchanged
            with open(log) as f:
                remaining = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(remaining), 3)

    def test_appends_to_existing_archive(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "trade_log.jsonl"
            archive_dir = Path(d) / "archive"
            archive_dir.mkdir()

            # Create existing archive
            ts_old = (datetime.now(timezone.utc) - timedelta(days=60))
            month_key = ts_old.strftime("%Y-%m")
            archive_path = archive_dir / f"{month_key}.jsonl.gz"
            existing = {"timestamp_utc": ts_old.isoformat(), "fingerprint": "existing", "action": "PLACED"}
            with gzip.open(archive_path, "wt") as f:
                f.write(json.dumps(existing) + "\n")

            # Write new entries for same month
            entries = [self._make_entry(60, f"new-old-{i}") for i in range(2)]
            entries.append(self._make_entry(1, "recent"))
            with open(log, "w") as f:
                for e in entries:
                    f.write(json.dumps(e) + "\n")

            result = rotate(log, archive_dir, cutoff_days=30)
            self.assertEqual(result["kept"], 1)

            # Archive should have existing + new old entries
            with gzip.open(archive_path, "rt") as f:
                archived = [json.loads(line) for line in f if line.strip()]
            self.assertGreaterEqual(len(archived), 3)  # 1 existing + 2 new


if __name__ == "__main__":
    unittest.main()
