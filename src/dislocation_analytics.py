"""Replayable analytics for synchronized cross-venue quote observations."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional


UTC = timezone.utc


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    value = (ordered[lower] if lower == upper else
             ordered[lower] * (upper - position)
             + ordered[upper] * (position - lower))
    return round(value, 6)


def iter_observations(directory: Path, *, now: Optional[datetime] = None,
                      window_days: int = 14) -> Iterator[Dict[str, Any]]:
    """Yield retained observations one at a time in chronological file order."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = now - timedelta(days=window_days)
    for path in sorted(directory.glob("*.jsonl")):
        try:
            handle = path.open(encoding="utf-8")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                captured = _parse(row.get("captured_at"))
                if captured and cutoff <= captured <= now:
                    yield row


def load_observations(directory: Path, *, now: Optional[datetime] = None,
                      window_days: int = 14) -> List[Dict[str, Any]]:
    """Compatibility reader for callers that explicitly need a materialized list."""
    return list(iter_observations(directory, now=now, window_days=window_days))


def analyze(rows: Iterable[Dict[str, Any]], *, threshold_usd: float = 0.03,
            max_episode_gap_seconds: float = 450.0,
            window_days: int = 14) -> Dict[str, Any]:
    observation_count = path_count = priced_count = qualifying_count = 0
    net: List[float] = []
    gross: List[float] = []
    skews: List[float] = []
    states: Dict[tuple, Dict[str, Any]] = {}
    episode_rows: List[Dict[str, Any]] = []

    def finish_episode(key: tuple, current: Optional[Dict[str, Any]]) -> None:
        if not current:
            return
        match_key, direction = key
        duration = (current["last"] - current["first"]).total_seconds()
        episode_rows.append({
            "match_key": match_key, "direction": direction,
            "observations": current["count"],
            "duration_seconds": duration,
            "first_seen": current["first"].isoformat().replace("+00:00", "Z"),
            "last_seen": current["last"].isoformat().replace("+00:00", "Z"),
            "max_net_edge_usd": current["max_edge"],
            "persistent": current["count"] >= 2,
        })

    for row in rows:
        observation_count += 1
        captured = _parse(row.get("captured_at"))
        if not captured:
            continue
        if isinstance(row.get("quote_skew_seconds"), (int, float)):
            skews.append(float(row["quote_skew_seconds"]))
        for path in row.get("paths") or []:
            if not isinstance(path, dict):
                continue
            path_count += 1
            value = path.get("net_edge_usd")
            if not isinstance(value, (int, float)):
                continue
            priced_count += 1
            edge = float(value)
            net.append(edge)
            gross_value = path.get("gross_edge_usd")
            if isinstance(gross_value, (int, float)):
                gross.append(float(gross_value))
            qualifies = edge >= threshold_usd
            qualifying_count += qualifies
            direction = "gemini:{}|kalshi:{}".format(
                path.get("gemini_yes_team"), path.get("kalshi_yes_team"))
            key = (row.get("match_key"), direction)
            state = states.setdefault(key, {"previous": None, "current": None})
            previous = state["previous"]
            gap = ((captured - previous).total_seconds()
                   if previous is not None else None)
            if (not qualifies
                    or (gap is not None and gap > max_episode_gap_seconds)):
                finish_episode(key, state["current"])
                state["current"] = None
            if qualifies:
                current = state["current"]
                if current is None:
                    state["current"] = {
                        "first": captured, "last": captured,
                        "count": 1, "max_edge": edge,
                    }
                else:
                    current["last"] = captured
                    current["count"] += 1
                    current["max_edge"] = max(current["max_edge"], edge)
            state["previous"] = captured

    for key, state in states.items():
        finish_episode(key, state["current"])
    durations = [row["duration_seconds"] for row in episode_rows]
    persistent = [row for row in episode_rows if row["persistent"]]
    scenarios = []
    for per_leg in (0.0, 0.005, 0.01, 0.02):
        adjusted = [value - 2.0 * per_leg for value in net]
        scenarios.append({
            "per_leg_slippage_usd": per_leg,
            "positive_paths": sum(value > 0 for value in adjusted),
            "positive_share": (round(sum(value > 0 for value in adjusted)
                                     / len(adjusted), 6)
                               if adjusted else None),
            "p95_net_edge_usd": _quantile(adjusted, .95),
        })
    return {
        "schema_version": 1, "window_days": window_days,
        "threshold_usd": threshold_usd,
        "observation_rows": observation_count, "path_observations": path_count,
        "priced_path_observations": priced_count,
        "quote_completeness": (round(priced_count / path_count, 6)
                               if path_count else None),
        "qualifying_path_observations": qualifying_count,
        "qualifying_share": (round(qualifying_count / priced_count, 6)
                             if priced_count else None),
        "net_edge_usd": {
            "min": min(net) if net else None, "median": _quantile(net, .5),
            "p90": _quantile(net, .9), "p95": _quantile(net, .95),
            "max": max(net) if net else None,
        },
        "gross_edge_usd": {"median": _quantile(gross, .5),
                           "p95": _quantile(gross, .95)},
        "quote_skew_seconds": {"median": _quantile(skews, .5),
                               "p95": _quantile(skews, .95),
                               "max": max(skews) if skews else None},
        "episodes": {
            "count": len(episode_rows), "persistent_count": len(persistent),
            "median_duration_seconds": _quantile(durations, .5),
            "p90_duration_seconds": _quantile(durations, .9),
            "longest_duration_seconds": max(durations) if durations else None,
            "recent": sorted(episode_rows, key=lambda row: row["last_seen"],
                             reverse=True)[:20],
        },
        "slippage_scenarios": scenarios,
        "depth_status": "unavailable",
        "depth_note": "Public snapshots do not expose comparable full depth; slippage is scenario-tested, not estimated.",
    }


def analyze_directory(directory: Path, *, now: Optional[datetime] = None,
                      window_days: int = 14,
                      threshold_usd: float = 0.03) -> Dict[str, Any]:
    return analyze(load_observations(directory, now=now, window_days=window_days),
                   threshold_usd=threshold_usd, window_days=window_days)
