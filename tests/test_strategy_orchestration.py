"""
tests/test_strategy_orchestration.py
────────────────────────────────────
Regression coverage for the recursive strategy registry and schemas.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategy_orchestration import (  # noqa: E402
    IntegrityReport,
    MonitoringReport,
    OpportunityCard,
    PromotionDecision,
    StrategyRegistry,
    StrategySpec,
    StrategyTransitionError,
    ValidationReport,
)


def _card() -> OpportunityCard:
    return OpportunityCard(
        id="op_20260801_001",
        created_at="2026-08-01T16:40:00Z",
        source_agent="scout",
        market_family="KXPGATOP10",
        thesis="Late weather shifts are not fully priced.",
        edge_source=["timing", "information_lag"],
        external_data_needed=["weather", "tee_times"],
        confidence=0.74,
        urgency="medium",
        known_failure_modes=["signal arrives too late"],
        rule_ambiguities=[],
        next_step="spec",
        extra={"priority": "high"},
    )


def _spec() -> StrategySpec:
    return StrategySpec(
        id="strat_20260801_001",
        opportunity_id="op_20260801_001",
        name="weather top-10 misprice",
        version="1.0",
        state="spec",
        entry_logic={"type": "threshold", "min_edge": 0.0125},
        exit_logic={"type": "time_or_price"},
        fair_value_model={"type": "logit"},
        fee_model={"venue": "kalshi", "slippage_bps": 15},
        risk_limits={"max_usd_at_risk": 250},
        null_hypothesis="No net edge after costs.",
        alternative_hypothesis="Net edge survives costs.",
    )


def _integrity() -> IntegrityReport:
    return IntegrityReport(
        strategy_id="op_20260801_001",
        checked_at="2026-08-01T16:45:00Z",
        status="pass",
        contract_terms={"passed": True},
        settlement_mapping={"passed": True},
        fee_mapping={"passed": True},
        api_field_reliability={"passed": True},
        external_schedule_dependency={"required": True, "available": True},
        blocking_issues=[],
    )


def _validation() -> ValidationReport:
    return ValidationReport(
        strategy_id="op_20260801_001",
        run_id="val_20260801_001",
        checked_at="2026-08-01T17:10:00Z",
        status="pass",
        sample_size={"events": 42, "trades": 318},
        performance={"net_edge": 0.009, "clv": 0.013},
        uncertainty={"ci_lower": 0.002, "ci_upper": 0.016},
        stress_tests={"low_liquidity": "pass"},
        capacity={"estimated_daily_usd": 1500},
        go_no_go="go",
    )


class TestStrategyRegistry(TestCase):
    def test_full_lifecycle_round_trip(self):
        reg = StrategyRegistry()
        reg.register_opportunity(_card())
        reg.attach_spec("op_20260801_001", _spec())
        reg.record_integrity("op_20260801_001", _integrity())
        reg.record_validation("op_20260801_001", _validation())
        reg.record_promotion(
            "op_20260801_001",
            PromotionDecision(
                strategy_id="op_20260801_001",
                decision_at="2026-08-01T17:30:00Z",
                from_state="validated",
                to_state="paper_live",
                decision="promote",
                rationale=["net edge positive"],
                required_conditions=["paper sample size >= threshold"],
                owner_agent="promotion",
                review_window_days=14,
            )
        )
        reg.record_monitoring(
            "op_20260801_001",
            MonitoringReport(
                strategy_id="op_20260801_001",
                observed_at="2026-08-01T18:00:00Z",
                state="paper_live",
                window="24h",
                status="ok",
                realized_edge=0.011,
                clv=0.014,
                pnl_usd=18.25,
                fill_quality={"median_slippage_bps": 12},
                drift_signals=[],
                guardrail_breaches=[],
                recommended_state="live_small",
                notes="paper confirmed",
            )
        )

        self.assertEqual(reg.get("op_20260801_001").state, "live_small")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            reg.dump(path)
            loaded = StrategyRegistry.load(path)

        record = loaded.get("op_20260801_001")
        self.assertEqual(record.state, "live_small")
        self.assertEqual(record.opportunity.extra["priority"], "high")
        self.assertEqual(len(record.events), len(reg.get("op_20260801_001").events))
        self.assertEqual(len(record.monitoring), 1)

    def test_invalid_transition_is_rejected(self):
        reg = StrategyRegistry()
        reg.register_opportunity(_card())
        with self.assertRaises(StrategyTransitionError):
            reg.transition("op_20260801_001", "live_small")

    def test_validation_requires_passed_integrity(self):
        reg = StrategyRegistry()
        reg.register_opportunity(_card())
        reg.attach_spec("op_20260801_001", _spec())
        with self.assertRaises(StrategyTransitionError):
            reg.record_validation("op_20260801_001", _validation())

    def test_hold_decision_must_keep_state(self):
        with self.assertRaises(ValueError):
            PromotionDecision(
                strategy_id="op_20260801_001",
                decision_at="2026-08-01T17:30:00Z",
                from_state="validated",
                to_state="paper_live",
                decision="hold",
                rationale=[],
                required_conditions=[],
                owner_agent="promotion",
                review_window_days=14,
            )
