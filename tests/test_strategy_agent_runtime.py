"""
tests/test_strategy_agent_runtime.py
────────────────────────────────────
Tests for the live queue-based strategy agent worker.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategy_agent_runtime import StrategyAgentRuntime  # noqa: E402


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


class TestStrategyAgentRuntime(TestCase):
    def test_run_once_processes_all_roles_and_persists_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "registry.json"
            queue = root / "queue"
            processed = root / "processed"
            heartbeat = root / "heartbeat.jsonl"

            _write(
                queue / "scout" / "001-opportunity.json",
                {
                    "type": "opportunity",
                    "strategy_id": "op_001",
                    "actor": "scout",
                    "payload": {
                        "id": "op_001",
                        "created_at": "2026-08-01T16:40:00Z",
                        "source_agent": "scout",
                        "market_family": "KXPGATOP10",
                        "thesis": "timing edge",
                        "edge_source": ["timing"],
                        "external_data_needed": ["weather"],
                        "confidence": 0.6,
                        "urgency": "medium",
                    },
                },
            )
            _write(
                queue / "integrity" / "002-spec.json",
                {
                    "type": "spec",
                    "strategy_id": "op_001",
                    "actor": "integrity",
                    "payload": {
                        "id": "spec_001",
                        "opportunity_id": "op_001",
                        "name": "timing edge",
                        "version": "1.0",
                        "state": "spec",
                        "entry_logic": {"type": "threshold"},
                        "exit_logic": {"type": "time_or_price"},
                        "fair_value_model": {"type": "logit"},
                        "fee_model": {"venue": "kalshi"},
                        "risk_limits": {"max_usd_at_risk": 100},
                        "null_hypothesis": "no edge",
                        "alternative_hypothesis": "edge survives costs",
                    },
                },
            )
            _write(
                queue / "validation" / "003-integrity.json",
                {
                    "type": "integrity",
                    "strategy_id": "op_001",
                    "actor": "integrity",
                    "payload": {
                        "strategy_id": "op_001",
                        "checked_at": "2026-08-01T16:45:00Z",
                        "status": "pass",
                        "contract_terms": {"passed": True},
                        "settlement_mapping": {"passed": True},
                        "fee_mapping": {"passed": True},
                        "api_field_reliability": {"passed": True},
                        "external_schedule_dependency": {"required": True, "available": True},
                        "blocking_issues": [],
                    },
                },
            )
            _write(
                queue / "validation" / "004-validation.json",
                {
                    "type": "validation",
                    "strategy_id": "op_001",
                    "actor": "validation",
                    "payload": {
                        "strategy_id": "op_001",
                        "run_id": "val_001",
                        "checked_at": "2026-08-01T17:10:00Z",
                        "status": "pass",
                        "sample_size": {"events": 42, "trades": 318},
                        "performance": {"net_edge": 0.009, "clv": 0.013},
                        "uncertainty": {"ci_lower": 0.002, "ci_upper": 0.016},
                        "stress_tests": {"low_liquidity": "pass"},
                        "capacity": {"estimated_daily_usd": 1500},
                        "go_no_go": "go",
                    },
                },
            )
            _write(
                queue / "promotion" / "005-promotion.json",
                {
                    "type": "promotion",
                    "strategy_id": "op_001",
                    "actor": "promotion",
                    "payload": {
                        "strategy_id": "op_001",
                        "decision_at": "2026-08-01T17:30:00Z",
                        "from_state": "validated",
                        "to_state": "paper_live",
                        "decision": "promote",
                        "rationale": ["net edge positive"],
                        "required_conditions": ["paper sample size >= threshold"],
                        "owner_agent": "promotion",
                        "review_window_days": 14,
                    },
                },
            )
            _write(
                queue / "monitoring" / "006-monitoring.json",
                {
                    "type": "monitoring",
                    "strategy_id": "op_001",
                    "actor": "monitor",
                    "payload": {
                        "strategy_id": "op_001",
                        "observed_at": "2026-08-01T18:00:00Z",
                        "state": "paper_live",
                        "window": "24h",
                        "status": "ok",
                        "realized_edge": 0.011,
                        "clv": 0.014,
                        "pnl_usd": 18.25,
                        "fill_quality": {"median_slippage_bps": 12},
                        "drift_signals": [],
                        "guardrail_breaches": [],
                        "recommended_state": "live_small",
                    },
                },
            )

            runtime = StrategyAgentRuntime(
                registry_path=registry,
                queue_dir=queue,
                processed_dir=processed,
                heartbeat_path=heartbeat,
            )
            summary = runtime.run_once()

            self.assertEqual(summary.processed, 6)
            self.assertEqual(summary.failed, 0)
            self.assertTrue(registry.exists())
            self.assertTrue(heartbeat.exists())
            self.assertFalse(any(queue.rglob("*.json")))
            self.assertTrue(any(processed.rglob("*.json")))

            loaded = json.loads(registry.read_text())
            record = loaded["records"]["op_001"]
            self.assertEqual(record["state"], "live_small")
            self.assertEqual(len(record["events"]), 7)
