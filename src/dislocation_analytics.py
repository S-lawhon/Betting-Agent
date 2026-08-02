"""Replayable analytics for synchronized cross-venue quote observations."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


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


def load_observations(directory: Path, *, now: Optional[datetime] = None,
                      window_days: int = 14) -> List[Dict[str, Any]]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = now - timedelta(days=window_days)
    rows = []
    for path in sorted(directory.glob("*.jsonl")):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            captured = _parse(row.get("captured_at"))
            if captured and cutoff <= captured <= now:
                rows.append(row)
    return rows


def analyze(rows: Iterable[Dict[str, Any]], *, threshold_usd: float = 0.03,
            max_episode_gap_seconds: float = 450.0,
            window_days: int = 14) -> Dict[str, Any]:
    observations = list(rows)
    paths = []
    skews = []
    for row in observations:
        captured = _parse(row.get("captured_at"))
        if not captured:
            continue
        if isinstance(row.get("quote_skew_seconds"), (int, float)):
            skews.append(float(row["quote_skew_seconds"]))
        for path in row.get("paths") or []:
            if not isinstance(path, dict):
                continue
            paths.append({
                **path, "captured": captured,
                "collection_id": row.get("collection_id"),
                "match_key": row.get("match_key"),
                "direction": "gemini:{}|kalshi:{}".format(
                    path.get("gemini_yes_team"), path.get("kalshi_yes_team")),
            })
    priced = [row for row in paths
              if isinstance(row.get("net_edge_usd"), (int, float))]
    net = [float(row["net_edge_usd"]) for row in priced]
    gross = [float(row["gross_edge_usd"]) for row in priced
             if isinstance(row.get("gross_edge_usd"), (int, float))]
    qualifying = [row for row in priced
                  if float(row["net_edge_usd"]) >= threshold_usd]

    series = defaultdict(list)
    for row in priced:
        series[(row["match_key"], row["direction"])].append(row)
    episodes = []
    for key, values in series.items():
        values.sort(key=lambda item: item["captured"])
        current = []
        previous = None
        for row in values:
            qualifies = float(row["net_edge_usd"]) >= threshold_usd
            gap = ((row["captured"] - previous).total_seconds()
                   if previous is not None else None)
            if not qualifies or (gap is not None and gap > max_episode_gap_seconds):
                if current:
                    episodes.append((key, current))
                current = []
            if qualifies:
                current.append(row)
            previous = row["captured"]
        if current:
            episodes.append((key, current))
    episode_rows = []
    for (match_key, direction), values in episodes:
        duration = (values[-1]["captured"] - values[0]["captured"]).total_seconds()
        episode_rows.append({
            "match_key": match_key, "direction": direction,
            "observations": len(values), "duration_seconds": duration,
            "first_seen": values[0]["captured"].isoformat().replace("+00:00", "Z"),
            "last_seen": values[-1]["captured"].isoformat().replace("+00:00", "Z"),
            "max_net_edge_usd": max(float(row["net_edge_usd"]) for row in values),
            "persistent": len(values) >= 2,
        })
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
        "observation_rows": len(observations), "path_observations": len(paths),
        "priced_path_observations": len(priced),
        "quote_completeness": (round(len(priced) / len(paths), 6) if paths else None),
        "qualifying_path_observations": len(qualifying),
        "qualifying_share": (round(len(qualifying) / len(priced), 6)
                             if priced else None),
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
