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
    isolated = bool(rows) and all(
        row.get("px_environment") == environment for row in rows)
    matched_by_sport = dict(sorted(Counter(
        str(pair.get("sport") or "unknown") for pair in pairs).items()))

    sport_readiness = {}
    for sport, matched_count in matched_by_sport.items():
        sport_rows = [row for row in rows
                      if str(row.get("sport") or "unknown") == sport]
        sport_skews = [row.get("schedule_skew_seconds") for row in sport_rows]
        sport_aligned = bool(sport_rows) and all(
            isinstance(value, (int, float)) and 0 <= value <= (
                row.get("schedule_tolerance_seconds")
                if isinstance(row.get("schedule_tolerance_seconds"), (int, float))
                else MAX_START_SKEW_SECONDS)
            for row, value in zip(sport_rows, sport_skews))
        sport_coverage = quote_coverage_by_sport.get(sport) or {}
        coverage_value = sport_coverage.get("coverage")
        coverage_ready = (isinstance(coverage_value, (int, float))
                          and coverage_value >= MIN_EXECUTABLE_QUOTE_COVERAGE)
        labels_ready = bool(sport_rows) and all(
            row.get("px_environment") == environment for row in sport_rows)
        sport_blockers = []
        if not sport_aligned:
            sport_blockers.append("schedule_alignment")
        if not coverage_ready:
            sport_blockers.append("executable_quote_coverage")
        if not labels_ready:
            sport_blockers.append("environment_labels")
        sport_readiness[sport] = {
            "status": "technical_ready" if not sport_blockers else "blocked",
            "technical_ready": not sport_blockers,
            "blockers": sport_blockers,
            "matched_events": matched_count,
            "quote_coverage": sport_coverage,
        }

    adapter_checks = [
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
        _check("environment_labels", isolated,
               f"all {len(rows)} rows identify {environment}"),
    ]
    summary_checks = [
        _check("schedule_safe_matches", aligned,
               f"{len(pairs)} uniquely schedule-aligned matches; "
               f"{int(unmatched_reasons.get('ambiguous_px_market') or 0)} ambiguous"),
        _check("executable_quote_coverage",
               bool(eligible) and coverage >= MIN_EXECUTABLE_QUOTE_COVERAGE,
               f"{len(priced)}/{len(eligible)} eligible pre-start/unknown rows "
               f"({coverage:.1%}) have both asks; {len(post_start)} post-start excluded"),
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
    adapter_ready = all(row["status"] == "passed" for row in adapter_checks)
    ready_sports = sorted(sport for sport, row in sport_readiness.items()
                          if row["technical_ready"])
    blocked_sports = sorted(set(sport_readiness) - set(ready_sports))
    technical_ready = (adapter_ready and bool(sport_readiness)
                       and not blocked_sports)
    partially_technical_ready = (adapter_ready and bool(ready_sports)
                                 and bool(blocked_sports))
    rollout_ready = technical_ready and all(
        row["status"] == "passed" for row in policy)
    blockers = [row["id"] for row in adapter_checks
                if row["status"] != "passed"]
    blockers.extend(
        f"sport:{sport}:{blocker}"
        for sport in blocked_sports
        for blocker in sport_readiness[sport]["blockers"])
    blockers.extend(row["id"] for row in policy
                    if row["status"] != "passed")
    return {
        "schema_version": 2,
        "generated_at": timestamp.isoformat().replace("+00:00", "Z"),
        "venue": "prophetx", "environment": environment,
        "mode": "read_only_validation",
        "status": ("rollout_ready" if rollout_ready else
                   "technical_ready" if technical_ready else
                   "partially_ready" if partially_technical_ready else "blocked"),
        "technical_ready": technical_ready,
        "partially_technical_ready": partially_technical_ready,
        "adapter_ready": adapter_ready,
        "ready_sports": ready_sports,
        "blocked_sports": blocked_sports,
        "sport_readiness": sport_readiness,
        "rollout_ready": rollout_ready,
        "checks": adapter_checks + summary_checks + policy,
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
            "matched_by_sport": matched_by_sport,
        },
        "safety": {
            "execution_enabled": False,
            "collector_enabled_by_validation": False,
            "contains_secrets": False,
        },
    }
