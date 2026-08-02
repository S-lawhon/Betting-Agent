"""Durable, deterministic research cases for cross-venue dislocations."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


UTC = timezone.utc
DEFAULT_THRESHOLDS = (0.01, 0.02, 0.03, 0.04, 0.05)
DEFAULT_SLIPPAGE_PER_LEG = (0.0, 0.005, 0.01, 0.02)


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _quantile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    value = (ordered[lower] if lower == upper else
             ordered[lower] * (upper - position)
             + ordered[upper] * (position - lower))
    return round(value, 6)


def _paths(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    paths = []
    for observation in rows:
        captured = _parse(observation.get("captured_at"))
        if not captured:
            continue
        gemini_start = _parse((observation.get("gemini") or {}).get(
            "scheduled_start_at"))
        kalshi_start = _parse((observation.get("kalshi") or {}).get(
            "scheduled_start_at"))
        scheduled_start = (min(gemini_start, kalshi_start)
                           if gemini_start and kalshi_start
                           else gemini_start or kalshi_start)
        for path in observation.get("paths") or []:
            if not isinstance(path, Mapping):
                continue
            direction = str(path.get("direction") or
                            "gemini:{}|kalshi:{}".format(
                                path.get("gemini_yes_team"),
                                path.get("kalshi_yes_team")))
            paths.append({
                **dict(path), "captured": captured,
                "collection_id": observation.get("collection_id"),
                "match_key": observation.get("match_key"),
                "direction": direction,
                "quote_skew_seconds": observation.get("quote_skew_seconds"),
                "scheduled_start": scheduled_start,
                "venue_start_difference_seconds": (
                    abs((gemini_start - kalshi_start).total_seconds())
                    if gemini_start and kalshi_start else None),
            })
    return paths


def settlement_basis(policy: Mapping[str, Any]) -> Dict[str, Any]:
    dimensions = policy.get("dimensions") or {}
    risk = sorted(key for key, value in dimensions.items()
                  if value not in ("equivalent", "compatible"))
    aligned = sorted(key for key, value in dimensions.items()
                     if value in ("equivalent", "compatible"))
    status = str(policy.get("status") or "unverified")
    return {
        "policy_id": policy.get("id"), "status": status,
        "classification": (
            "candidate_equivalent" if status == "verified"
            else "contract_non_equivalence" if status == "not_equivalent"
            else "unverified_contract_basis"),
        "risk_dimensions": risk, "aligned_dimensions": aligned,
        "historical_incidence_status": "unmeasured",
        "historical_incidence_note": (
            "Quote observations do not establish whether a postponement, void, "
            "shortened game, or discretionary review actually occurred."),
        "trade_allowed": False,
    }


def _case_id(pair_id: str, match_key: str, direction: str,
             first_seen: datetime) -> str:
    raw = "|".join((pair_id, match_key, direction, _iso(first_seen)))
    return "CV-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _finish_case(pair_id: str, key: tuple, values: List[Dict[str, Any]], *,
                 policy_basis: Mapping[str, Any], status: str,
                 closure_reason: Optional[str], closed_at: Optional[datetime],
                 post_case_edge_usd: Optional[float]) -> Dict[str, Any]:
    match_key, direction = key
    first, last = values[0]["captured"], values[-1]["captured"]
    edges = [float(row["net_edge_usd"]) for row in values]
    gross = [float(row["gross_edge_usd"]) for row in values
             if isinstance(row.get("gross_edge_usd"), (int, float))]
    skews = [float(row["quote_skew_seconds"]) for row in values
             if isinstance(row.get("quote_skew_seconds"), (int, float))]
    slippage = {}
    for per_leg in DEFAULT_SLIPPAGE_PER_LEG:
        adjusted = [edge - 2 * per_leg for edge in edges]
        slippage[f"{per_leg:.3f}"] = {
            "per_leg_usd": per_leg,
            "positive_observations": sum(value > 0 for value in adjusted),
            "max_adjusted_edge_usd": round(max(adjusted), 6),
            "survives": any(value > 0 for value in adjusted),
        }
    duration = (last - first).total_seconds()
    scheduled_starts = [row["scheduled_start"] for row in values
                        if row.get("scheduled_start")]
    scheduled_start = scheduled_starts[0] if scheduled_starts else None
    first_vs_start = ((first - scheduled_start).total_seconds()
                      if scheduled_start else None)
    phase = ("pre_scheduled_start" if first_vs_start is not None
             and first_vs_start < 0 else "after_scheduled_start"
             if first_vs_start is not None else "unknown")
    start_differences = [row["venue_start_difference_seconds"] for row in values
                         if isinstance(row.get("venue_start_difference_seconds"),
                         (int, float))]
    max_start_difference = max(start_differences) if start_differences else None
    alignment = ("mismatched" if max_start_difference is not None
                 and max_start_difference > 900 else "aligned"
                 if max_start_difference is not None else "unavailable")
    return {
        "schema_version": 1,
        "case_id": _case_id(pair_id, str(match_key), direction, first),
        "pair_id": pair_id, "match_key": match_key, "direction": direction,
        "status": status, "closure_reason": closure_reason,
        "first_seen": _iso(first), "last_seen": _iso(last),
        "closed_at": _iso(closed_at) if closed_at else None,
        "observations": len(values), "persistent": len(values) >= 2,
        "duration_seconds_lower_bound": duration,
        "scheduled_start_at": _iso(scheduled_start) if scheduled_start else None,
        "first_seen_vs_scheduled_start_seconds": first_vs_start,
        "market_phase": phase,
        "venue_start_difference_seconds": (
            max_start_difference),
        "schedule_alignment_status": alignment,
        "min_net_edge_usd": round(min(edges), 6),
        "median_net_edge_usd": _quantile(edges, .5),
        "max_net_edge_usd": round(max(edges), 6),
        "max_gross_edge_usd": round(max(gross), 6) if gross else None,
        "post_case_edge_usd": post_case_edge_usd,
        "median_quote_skew_seconds": _quantile(skews, .5),
        "max_per_leg_slippage_usd": round(max(edges) / 2.0, 6),
        "slippage_survival": slippage,
        "settlement_basis": dict(policy_basis),
        "trade_allowed": False,
    }


def build_cases(rows: Iterable[Mapping[str, Any]], *, pair_id: str,
                policy: Mapping[str, Any], now: Optional[datetime] = None,
                threshold_usd: float = 0.03,
                max_episode_gap_seconds: float = 450.0) -> List[Dict[str, Any]]:
    """Segment qualifying paths into stable cases with observed closure facts."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    basis = settlement_basis(policy)
    series = defaultdict(list)
    for row in _paths(rows):
        if isinstance(row.get("net_edge_usd"), (int, float)):
            series[(row.get("match_key"), row["direction"])].append(row)
    cases = []
    for key, values in series.items():
        values.sort(key=lambda row: row["captured"])
        current: List[Dict[str, Any]] = []
        previous: Optional[datetime] = None
        for row in values:
            edge = float(row["net_edge_usd"])
            gap = ((row["captured"] - previous).total_seconds()
                   if previous is not None else None)
            if current and gap is not None and gap > max_episode_gap_seconds:
                cases.append(_finish_case(
                    pair_id, key, current, policy_basis=basis, status="closed",
                    closure_reason="observation_gap", closed_at=current[-1]["captured"],
                    post_case_edge_usd=None))
                current = []
            if edge >= threshold_usd:
                current.append(row)
            elif current:
                cases.append(_finish_case(
                    pair_id, key, current, policy_basis=basis, status="closed",
                    closure_reason="edge_below_threshold", closed_at=row["captured"],
                    post_case_edge_usd=edge))
                current = []
            previous = row["captured"]
        if current:
            age = (now - current[-1]["captured"]).total_seconds()
            active = 0 <= age <= max_episode_gap_seconds
            cases.append(_finish_case(
                pair_id, key, current, policy_basis=basis,
                status="active" if active else "closed",
                closure_reason=None if active else "observation_gap",
                closed_at=None if active else current[-1]["captured"],
                post_case_edge_usd=None))
    return sorted(cases, key=lambda row: (row["first_seen"], row["case_id"]))


def calibrate_thresholds(rows: Iterable[Mapping[str, Any]], *, pair_id: str,
                         policy: Mapping[str, Any], now: Optional[datetime] = None,
                         thresholds: Sequence[float] = DEFAULT_THRESHOLDS
                         ) -> List[Dict[str, Any]]:
    observations = list(rows)
    output = []
    for threshold in thresholds:
        cases = build_cases(observations, pair_id=pair_id, policy=policy,
                            now=now, threshold_usd=threshold)
        durations = [row["duration_seconds_lower_bound"] for row in cases]
        output.append({
            "threshold_usd": threshold, "case_count": len(cases),
            "persistent_count": sum(row["persistent"] for row in cases),
            "active_count": sum(row["status"] == "active" for row in cases),
            "single_snapshot_count": sum(row["observations"] == 1 for row in cases),
            "pre_scheduled_start_count": sum(
                row["market_phase"] == "pre_scheduled_start" for row in cases),
            "after_scheduled_start_count": sum(
                row["market_phase"] == "after_scheduled_start" for row in cases),
            "schedule_mismatch_count": sum(
                row["schedule_alignment_status"] == "mismatched" for row in cases),
            "median_duration_seconds": _quantile(durations, .5),
            "p90_duration_seconds": _quantile(durations, .9),
        })
    return output


class CaseLedger:
    """SQLite-backed lifetime case index; raw JSONL remains the source tape."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS research_cases (
                case_id TEXT PRIMARY KEY,
                pair_id TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_cases_pair_last "
            "ON research_cases(pair_id, last_seen)")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def upsert(self, cases: Iterable[Mapping[str, Any]], *,
               updated_at: datetime) -> int:
        rows = list(cases)
        with self.connection:
            self.connection.executemany("""
                INSERT INTO research_cases
                    (case_id, pair_id, last_seen, status, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
            """, [(
                row["case_id"], row["pair_id"], row["last_seen"], row["status"],
                json.dumps(dict(row), sort_keys=True, separators=(",", ":")),
                _iso(updated_at),
            ) for row in rows])
        return len(rows)

    def cases(self, pair_id: str) -> List[Dict[str, Any]]:
        cursor = self.connection.execute(
            "SELECT payload_json FROM research_cases WHERE pair_id=?",
            (pair_id,))
        return [json.loads(row[0]) for row in cursor.fetchall()]

    def summary(self, pair_id: str, *, now: Optional[datetime] = None,
                limit: int = 20) -> Dict[str, Any]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        cases = self.cases(pair_id)

        def within(row: Mapping[str, Any], delta: timedelta) -> bool:
            last = _parse(row.get("last_seen"))
            return bool(last and timedelta(0) <= now - last <= delta)

        def rank(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
            ordered = sorted(rows, key=lambda row: (
                row.get("schedule_alignment_status") == "aligned",
                row.get("market_phase") == "pre_scheduled_start",
                bool(row.get("persistent")),
                bool((row.get("slippage_survival") or {}).get("0.010", {})
                     .get("survives")),
                float(row.get("max_net_edge_usd") or 0),
                float(row.get("duration_seconds_lower_bound") or 0),
            ), reverse=True)
            return ordered[:limit]

        day = [row for row in cases if within(row, timedelta(days=1))]
        week = [row for row in cases if within(row, timedelta(days=7))]
        singletons = [row for row in cases if row.get("observations") == 1]
        return {
            "schema_version": 1, "pair_id": pair_id,
            "ranking_basis": [
                "schedule_aligned", "pre_scheduled_start", "persistent",
                "survives_1c_per_leg_slippage",
                "max_net_edge_usd", "duration_seconds_lower_bound",
            ],
            "lifetime_cases": len(cases),
            "active_cases": sum(row.get("status") == "active" for row in cases),
            "cases_last_24h": len(day), "cases_last_7d": len(week),
            "persistent_lifetime": sum(bool(row.get("persistent")) for row in cases),
            "single_snapshot_share": (
                round(len(singletons) / len(cases), 6) if cases else None),
            "market_phase": {
                "pre_scheduled_start": sum(
                    row.get("market_phase") == "pre_scheduled_start" for row in cases),
                "after_scheduled_start": sum(
                    row.get("market_phase") == "after_scheduled_start" for row in cases),
                "unknown": sum(row.get("market_phase") == "unknown" for row in cases),
            },
            "schedule_alignment": {
                "aligned": sum(row.get("schedule_alignment_status") == "aligned"
                               for row in cases),
                "mismatched": sum(
                    row.get("schedule_alignment_status") == "mismatched"
                    for row in cases),
                "unavailable": sum(
                    row.get("schedule_alignment_status") == "unavailable"
                    for row in cases),
            },
            "top_last_24h": rank(day), "top_last_7d": rank(week),
        }
