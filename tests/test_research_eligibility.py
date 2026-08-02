from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest import TestCase

from src.research_eligibility import EligibilityError, EligibilityRegistry


class TestEligibilityRegistry(TestCase):
    def test_project_registry_loads_and_is_fail_closed(self):
        registry = EligibilityRegistry.load(Path("config/research_venues.yaml"))
        audit = registry.audit(as_of=date(2026, 8, 1))
        self.assertGreaterEqual(audit["venue_count"], 7)
        self.assertEqual(audit["execution_approved"], 0)
        for venue in ("prophetx", "novig", "underdog_predict"):
            decision = registry.decision(venue, as_of=date(2026, 8, 2))
            self.assertTrue(decision.research_allowed)
            self.assertFalse(decision.execution_allowed)

    def test_unknown_venue_is_never_executable(self):
        registry = EligibilityRegistry({"defaults": {}, "venues": []})
        decision = registry.decision("mystery", "sports", as_of=date(2026, 8, 1))
        self.assertEqual(decision.status, "pending_review")
        self.assertFalse(decision.execution_allowed)
        self.assertTrue(decision.stale)

    def test_stale_approval_fails_closed(self):
        registry = EligibilityRegistry({"venues": [{
            "id": "venue", "status": "approved", "research_allowed": True,
            "execution_allowed": True, "verified_at": "2026-01-01",
            "expires_at": "2026-02-01", "approved_by": "Sam",
            "evidence": ["https://example.test"], "products": [],
        }]})
        decision = registry.decision("venue", as_of=date(2026, 8, 1))
        self.assertEqual(decision.status, "pending_review")
        self.assertFalse(decision.execution_allowed)
        self.assertTrue(decision.stale)

    def test_product_override_must_be_explicitly_approved(self):
        registry = EligibilityRegistry({"venues": [{
            "id": "venue", "status": "reference_only",
            "research_allowed": True, "execution_allowed": False,
            "verified_at": "2026-08-01", "expires_at": "2026-09-01",
            "evidence": [], "products": [{
                "family": "economics", "status": "approved",
                "execution_allowed": True, "verified_at": "2026-08-01",
                "expires_at": "2026-09-01", "approved_by": "Sam",
                "evidence": ["https://example.test/approval"],
            }],
        }]})
        approved = registry.decision("venue", "economics", as_of=date(2026, 8, 2))
        other = registry.decision("venue", "sports", as_of=date(2026, 8, 2))
        self.assertTrue(approved.execution_allowed)
        self.assertFalse(other.execution_allowed)

    def test_invalid_implicit_approval_is_rejected(self):
        with self.assertRaises(EligibilityError):
            EligibilityRegistry({"venues": [{
                "id": "bad", "status": "reference_only",
                "execution_allowed": True, "products": [],
            }]})

    def test_require_execution_raises_with_reason(self):
        registry = EligibilityRegistry({"venues": [{
            "id": "venue", "status": "pending_review",
            "execution_allowed": False, "products": [],
        }]})
        with self.assertRaisesRegex(EligibilityError, "status=pending_review"):
            registry.require_execution("venue", "sports")

    def test_expired_prohibition_never_softens(self):
        registry = EligibilityRegistry({"venues": [{
            "id": "blocked", "status": "prohibited",
            "research_allowed": False, "execution_allowed": False,
            "expires_at": "2026-01-01", "products": [],
        }]})
        decision = registry.decision("blocked", as_of=date(2026, 8, 1))
        self.assertEqual(decision.status, "prohibited")
        self.assertTrue(decision.stale)
        self.assertFalse(decision.execution_allowed)
