from manager.checks import check_services


def _snapshot(last_row):
    return {"services": [{
        "id": "betting-strategy-agents", "active": "active", "restarts": 0,
        "heartbeat": {"age_minutes": 1, "max_stale_minutes": 30,
                      "last_row": last_row},
    }]}


def test_agent_request_failures_are_visible_even_with_fresh_heartbeat():
    findings = check_services(_snapshot({
        "failed": 2, "consecutive_failed_passes": 3,
        "oldest_queue_age_minutes": None,
    }))
    assert "service.betting-strategy-agents.request_failures" in {
        finding.key for finding in findings
    }


def test_stale_queue_is_visible_even_with_fresh_heartbeat():
    findings = check_services(_snapshot({
        "failed": 0, "consecutive_failed_passes": 0,
        "oldest_queue_age_minutes": 45,
    }))
    assert "service.betting-strategy-agents.queue_stale" in {
        finding.key for finding in findings
    }
