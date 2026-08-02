"""Tests for the strategy-agent queue submission command."""

from __future__ import annotations

import json
import stat
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from scripts.strategy_agent_submit import main


class TestStrategyAgentSubmit(TestCase):
    def test_request_is_readable_by_separate_service_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "opportunity.json"
            payload.write_text(json.dumps({
                "id": "op_test",
                "created_at": "2026-08-02T00:00:00Z",
                "source_agent": "strategy-scout",
                "market_family": "test",
                "thesis": "test",
                "edge_source": [],
                "external_data_needed": [],
                "confidence": 0.1,
                "urgency": "low",
            }))
            queue = root / "queue"
            argv = [
                "strategy_agent_submit", "--type", "opportunity",
                "--payload-file", str(payload), "--queue-dir", str(queue),
            ]
            with mock.patch("sys.argv", argv):
                self.assertEqual(main(), 0)

            [request] = list((queue / "scout").glob("*.json"))
            mode = stat.S_IMODE(request.stat().st_mode)
            self.assertEqual(mode, 0o644)
            self.assertEqual(json.loads(request.read_text())["strategy_id"], "op_test")
