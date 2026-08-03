"""Venue-neutral readiness and research-signal contracts.

Signals produced here are prompts for human research, never trade approval.
Execution authority remains governed by settlement and eligibility policies.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import yaml


DEFAULT_EXPANSION_VENUES = ("prophetx", "novig", "underdog_predict")
UTC = timezone.utc


def _read_validation_reports(config_path: Path, venue_id: str) -> Dict[str, Any]:
    """Load safe runtime readiness summaries; malformed reports fail closed."""
    report_dir = config_path.resolve().parent.parent / "data" / f"{venue_id}_readiness"
    reports: Dict[str, Any] = {}
    for environment in ("sandbox", "production"):
        path = report_dir / f"{environment}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping) or payload.get("venue") != venue_id:
            continue
        reports[environment] = {
            "generated_at": payload.get("generated_at"),
            "status": payload.get("status") or "unknown",
            "technical_ready": bool(payload.get("technical_ready")),
            "rollout_ready": bool(payload.get("rollout_ready")),
            "blockers": list(payload.get("blockers") or []),
            "counts": dict(payload.get("counts") or {}),
        }
    return reports


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def load_venue_pipeline(
        config_path: Path,
        venue_ids: Iterable[str] = DEFAULT_EXPANSION_VENUES) -> Dict[str, Any]:
    """Return public, non-secret onboarding state for research venues."""
    try:
        payload = yaml.safe_load(config_path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {
            "status": "unavailable", "venues": [],
            "reason": "research venue registry unavailable",
        }
    wanted = set(venue_ids)
    rows = []
    for venue in payload.get("venues") or []:
        if not isinstance(venue, Mapping) or venue.get("id") not in wanted:
            continue
        automated = bool(venue.get("automated_collection_allowed"))
        state = str(venue.get("collection_state") or "unclassified")
        rows.append({
            "id": str(venue["id"]), "name": str(venue.get("name") or venue["id"]),
            "research_allowed": bool(venue.get("research_allowed")),
            "execution_allowed": bool(venue.get("execution_allowed")),
            "automated_collection_allowed": automated,
            "collection_state": state,
            "adapter": venue.get("research_adapter"),
            "next_action": venue.get("next_action"),
            "validation": _read_validation_reports(config_path, str(venue["id"])),
        })
    rows.sort(key=lambda row: row["id"])
    found = {row["id"] for row in rows}
    missing = sorted(wanted - found)
    return {
        "status": "healthy" if not missing else "incomplete",
        "venues": rows, "missing_venues": missing,
        "summary": {
            "tracked": len(rows),
            "automatable": sum(row["automated_collection_allowed"] for row in rows),
            "collecting": sum(row["collection_state"] == "collecting" for row in rows),
            "blocked": sum(row["collection_state"].startswith("blocked_")
                           for row in rows),
            "excluded": sum(row["collection_state"].startswith("excluded_")
                            for row in rows),
        },
    }


def evaluate_pair_signals(pair_id: str, metrics: Mapping[str, Any], *,
                          min_quote_completeness: float = 0.90,
                          max_opportunity_age_minutes: float = 15.0
                          ) -> List[Dict[str, Any]]:
    """Derive stable operational/research signals from any pair's metrics."""
    signals: List[Dict[str, Any]] = []
    if metrics.get("status") != "healthy":
        signals.append({
            "id": f"{pair_id}.collection_degraded", "severity": "warn",
            "kind": "data_health", "trade_allowed": False,
            "title": f"{pair_id} collection is degraded",
            "detail": "Treat opportunity measurements as incomplete.",
        })
    policy = metrics.get("terms_policy") or {}
    registry_status = policy.get("registry_status")
    if registry_status not in (None, "healthy"):
        signals.append({
            "id": f"{pair_id}.settlement_registry_{registry_status}",
            "severity": "warn", "kind": "settlement_health",
            "trade_allowed": False,
            "title": f"{pair_id} settlement registry is {registry_status}",
            "detail": "Equivalence remains fail-closed until documents are current.",
        })
    analytics = metrics.get("analytics") or {}
    priced = analytics.get("priced_path_observations")
    completeness = analytics.get("quote_completeness")
    if (isinstance(priced, (int, float)) and priced > 0
            and isinstance(completeness, (int, float))
            and completeness < min_quote_completeness):
        signals.append({
            "id": f"{pair_id}.quote_completeness_low", "severity": "warn",
            "kind": "data_quality", "trade_allowed": False,
            "title": f"{pair_id} quote completeness is low",
            "detail": f"Only {100 * completeness:.1f}% of paths are priced.",
            "value": completeness,
        })
    episodes = analytics.get("episodes") or {}
    generated = _parse_timestamp(metrics.get("generated_at"))
    recent_persistent = []
    for episode in episodes.get("recent") or []:
        last_seen = _parse_timestamp(episode.get("last_seen"))
        if (episode.get("persistent") and generated and last_seen
                and 0 <= (generated - last_seen).total_seconds()
                <= max_opportunity_age_minutes * 60):
            recent_persistent.append(episode)
    persistent = len(recent_persistent)
    qualifying = analytics.get("qualifying_path_observations")
    if (isinstance(persistent, (int, float)) and persistent > 0
            and isinstance(qualifying, (int, float)) and qualifying > 0):
        terms = str(metrics.get("terms_equivalence") or "unverified")
        signals.append({
            "id": f"{pair_id}.persistent_dislocation", "severity": "warn",
            "kind": "research_opportunity", "trade_allowed": False,
            "title": f"{pair_id} has a persistent research dislocation",
            "detail": (
                f"{persistent} currently persistent episode(s), "
                f"{int(qualifying)} qualifying observations in-window; "
                f"settlement status is {terms}. "
                "Investigate the basis, but do not treat it as an arbitrage."),
            "value": int(persistent),
        })
    return signals
