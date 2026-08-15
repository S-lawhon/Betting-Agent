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


def test_stale_downstream_task_is_visible_even_with_fresh_recorder():
    findings = check_services(_snapshot({
        "failed": 0, "consecutive_failed_passes": 0,
        "oldest_queue_age_minutes": None,
        "oldest_task_age_minutes": 90,
    }))
    assert "service.betting-strategy-agents.task_stale" in {
        finding.key for finding in findings
    }


# --- not-installed vs down -------------------------------------------------
# systemctl reports ActiveState=inactive for a unit that was never installed
# AND for one that crashed. Paging CRITICAL for the first is crying wolf: the
# deploy simply ran ahead of the install step. LoadState tells them apart.

def _service(**kw):
    svc = {"id": "betting-strategy-agents", "restarts": 0}
    svc.update(kw)
    return {"services": [svc]}


def test_uninstalled_unit_warns_instead_of_paging_critical():
    findings = check_services(_service(load="not-found", active="inactive"))
    by_key = {f.key: f for f in findings}
    assert "service.betting-strategy-agents.not_installed" in by_key
    assert by_key["service.betting-strategy-agents.not_installed"].severity == "warn"
    assert "service.betting-strategy-agents.down" not in by_key


def test_installed_but_dead_unit_still_pages_critical():
    findings = check_services(_service(load="loaded", active="failed"))
    by_key = {f.key: f for f in findings}
    assert by_key["service.betting-strategy-agents.down"].severity == "critical"
    assert "service.betting-strategy-agents.not_installed" not in by_key


def test_missing_load_state_falls_back_to_the_down_check():
    # Snapshots written before LoadState was collected carry no "load" key.
    # They must keep paging on a dead service rather than going quiet.
    findings = check_services(_service(active="failed"))
    assert "service.betting-strategy-agents.down" in {f.key for f in findings}
