"""Fail-closed readiness contract for ProphetX read-only market data.

This module evaluates evidence; it never enables a collector or creates an
execution path.  Reports intentionally contain counts and statuses only, so
they are safe to surface in operational dashboards.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional


UTC = timezone.utc
MAX_START_SKEW_SECONDS = 15 * 60
MIN_EXECUTABLE_QUOTE_COVERAGE = 0.90


def _check(check_id: str, passed: bool, detail: str,
           category: str = "technical") -> Dict[str, Any]:
    return {
        "id": check_id,
        "status": "passed" if passed else "blocked",
        "category": category,
        "detail": detail,
    }


def _timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def build_readiness_report(
        *, environment: str, probe: Mapping[str, Any], event_count: int,
        moneyline_count: int, pairs: Iterable[Mapping[str, Any]],
        unmatched_reasons: Mapping[str, Any],
        snapshot_rows: Iterable[Mapping[str, Any]],
        tax_gate0_resolved: bool = False,
        production_collection_approved: bool = False,
        generated_at: Optional[datetime] = None) -> Dict[str, Any]:
    """Evaluate one read-only validation run without exposing credentials."""
    if environment not in ("sandbox", "production"):
        raise ValueError("environment must be sandbox or production")
    pairs, rows = list(pairs), list(snapshot_rows)
    skews = [row.get("schedule_skew_seconds") for row in rows]
    valid_skews = [v for v in skews if isinstance(v, (int, float))]
    aligned = bool(pairs) and len(valid_skews) == len(rows) and all(
        0 <= value <= (row.get("schedule_tolerance_seconds")
                       if isinstance(row.get("schedule_tolerance_seconds"),
                                     (int, float))
                       else MAX_START_SKEW_SECONDS)
        for row, value in zip(rows, skews) if isinstance(value, (int, float)))
    timestamp = (generated_at or datetime.now(UTC)).astimezone(UTC)
    # A started market can legitimately have no remaining executable stake.
    # It is useful evidence, but not a valid denominator for pregame collector
    # readiness. Unknown starts remain in the denominator and therefore fail
    # closed if they are unpriced.
    post_start = [row for row in rows
                  if (_timestamp(row.get("kalshi_scheduled_start_at"))
                      and _timestamp(row.get("kalshi_scheduled_start_at")) <= timestamp)]
    eligible = [row for row in rows if row not in post_start]
    priced = [row for row in eligible
              if row.get("k_yes_ask") is not None
              and row.get("px_other_ask_prob") is not None]
    coverage = len(priced) / len(eligible) if eligible else 0.0
    quote_coverage_by_sport = {}
    for sport in sorted({str(row.get("sport") or "unknown") for row in rows}):
        sport_rows = [row for row in rows
                      if str(row.get("sport") or "unknown") == sport]
        sport_post = [row for row in sport_rows if row in post_start]
        sport_eligible = [row for row in sport_rows if row not in post_start]
        sport_priced = [row for row in sport_eligible
                        if row.get("k_yes_ask") is not None
                        and row.get("px_other_ask_prob") is not None]
        quote_coverage_by_sport[sport] = {
            "eligible_rows": len(sport_eligible),
            "executable_rows": len(sport_priced),
            "missing_kalshi_ask": sum(
                row.get("k_yes_ask") is None for row in sport_eligible),
            "missing_prophetx_ask": sum(
                row.get("px_other_ask_prob") is None for row in sport_eligible),
            "missing_both_asks": sum(
                row.get("k_yes_ask") is None
                and row.get("px_other_ask_prob") is None
                for row in sport_eligible),
            "post_start_rows_excluded": len(sport_post),
            "coverage": (round(len(sport_priced) / len(sport_eligible), 6)
                         if sport_eligible else None),
        }
    isolated = all(row.get("px_environment") == environment for row in rows)

    technical = [
        _check("credentials_present", bool(probe.get("has_credentials")),
               "credential pair is configured" if probe.get("has_credentials")
               else "credential pair is absent"),
        _check("authenticated", bool(probe.get("authenticated")),
               "read-only login succeeded" if probe.get("authenticated")
               else "read-only login did not succeed"),
        _check("events_available", event_count > 0,
               f"{event_count} sport events returned"),
        _check("main_moneyline_shape", moneyline_count > 0,
               f"{moneyline_count} exact main-game moneylines parsed"),
        _check("schedule_safe_matches", aligned,
               f"{len(pairs)} uniquely schedule-aligned matches; "
               f"{int(unmatched_reasons.get('ambiguous_px_market') or 0)} ambiguous"),
        _check("executable_quote_coverage",
               bool(eligible) and coverage >= MIN_EXECUTABLE_QUOTE_COVERAGE,
               f"{len(priced)}/{len(eligible)} eligible pre-start/unknown rows "
               f"({coverage:.1%}) have both asks; {len(post_start)} post-start excluded"),
        _check("environment_labels", bool(rows) and isolated,
               f"all {len(rows)} rows identify {environment}"),
    ]
    policy = [
        _check("production_environment", environment == "production",
               f"validated against {environment}", "policy"),
        _check("tax_gate0", tax_gate0_resolved,
               "tax Gate 0 resolved" if tax_gate0_resolved
               else "tax Gate 0 requires qualified human review", "policy"),
        _check("production_collection_approval", production_collection_approved,
               "production collection separately approved"
               if production_collection_approved
               else "production collection has not been approved", "policy"),
    ]
    technical_ready = all(row["status"] == "passed" for row in technical)
    rollout_ready = technical_ready and all(
        row["status"] == "passed" for row in policy)
    blockers = [row["id"] for row in technical + policy
                if row["status"] != "passed"]
    return {
        "schema_version": 1,
        "generated_at": timestamp.isoformat().replace("+00:00", "Z"),
        "venue": "prophetx", "environment": environment,
        "mode": "read_only_validation",
        "status": ("rollout_ready" if rollout_ready else
                   "technical_ready" if technical_ready else "blocked"),
        "technical_ready": technical_ready,
        "rollout_ready": rollout_ready,
        "checks": technical + policy,
        "blockers": blockers,
        "counts": {
            "events": event_count, "main_moneylines": moneyline_count,
            "matched_events": len(pairs), "snapshot_rows": len(rows),
            "eligible_quote_rows": len(eligible),
            "post_start_rows_excluded": len(post_start),
            "executable_rows": len(priced),
            "executable_quote_coverage": round(coverage, 6),
            "quote_coverage_by_sport": quote_coverage_by_sport,
            "unmatched_reasons": dict(unmatched_reasons),
            "matched_by_sport": dict(sorted(Counter(
                str(pair.get("sport") or "unknown") for pair in pairs).items())),
        },
        "safety": {
            "execution_enabled": False,
            "collector_enabled_by_validation": False,
            "contains_secrets": False,
        },
    }
