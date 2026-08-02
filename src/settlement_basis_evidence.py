"""Event-outcome evidence for estimating settlement-basis incidence."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping

from src.gemini_kalshi_research import normalize_mlb_team


def classify_game(game: Any, date_et: str) -> Dict[str, Any]:
    detailed = str(getattr(game, "detailed_state", "") or "")
    abstract = str(getattr(game, "status", "") or "")
    text = (detailed + " " + abstract).lower()
    dimensions: List[str] = []
    if "postpon" in text:
        category, dimensions = "observed_exception", ["postponement"]
    elif "suspend" in text:
        category, dimensions = "observed_exception", ["suspended_game"]
    elif "cancel" in text:
        category, dimensions = "observed_exception", ["cancellation"]
    elif abstract.lower() == "final" or "completed" in text or "game over" in text:
        category = "ordinary_final_unverified"
    elif abstract.lower() in ("preview", "live"):
        category = "pending"
    else:
        category = "unknown"
    teams = sorted(filter(None, (
        normalize_mlb_team(getattr(game, "away_abbr", "")),
        normalize_mlb_team(getattr(game, "home_abbr", "")),
    )))
    match_key = f"{date_et}|{'|'.join(teams)}" if len(teams) == 2 else None
    return {
        "match_key": match_key, "date_et": date_et,
        "game_pk": getattr(game, "game_pk", None),
        "scheduled_start_at": getattr(game, "start_utc", None),
        "abstract_status": abstract, "detailed_status": detailed,
        "category": category, "observed_risk_dimensions": dimensions,
        "terminal": category in ("observed_exception", "ordinary_final_unverified"),
    }


def summarize_evidence(cases: Iterable[Mapping[str, Any]],
                       games: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    case_rows = list(cases)
    grouped_games: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in games:
        if row.get("match_key"):
            grouped_games[str(row["match_key"])].append(dict(row))
    ambiguous_keys = sorted(key for key, rows in grouped_games.items()
                            if len(rows) != 1)
    by_match = {key: rows[0] for key, rows in grouped_games.items()
                if len(rows) == 1}
    by_case = []
    resolved = exceptions = matched = 0
    dimensions: Dict[str, int] = {}
    for case in case_rows:
        evidence = by_match.get(case.get("match_key"))
        if evidence:
            matched += 1
            category = evidence.get("category")
            if category in ("observed_exception", "ordinary_final_unverified"):
                resolved += 1
            if category == "observed_exception":
                exceptions += 1
                for dimension in evidence.get("observed_risk_dimensions") or []:
                    dimensions[dimension] = dimensions.get(dimension, 0) + 1
        by_case.append({
            "case_id": case.get("case_id"), "match_key": case.get("match_key"),
            "evidence": evidence,
        })
    return {
        "schema_version": 1, "case_count": len(case_rows),
        "cases_matched_to_schedule": matched,
        "schedule_coverage": (round(matched / len(case_rows), 6)
                              if case_rows else None),
        "resolved_schedule_cases": resolved,
        "observed_exception_cases": exceptions,
        "observed_exception_rate_lower_bound": (
            round(exceptions / resolved, 6) if resolved else None),
        "observed_risk_dimensions": dimensions,
        "ambiguous_schedule_keys": ambiguous_keys,
        "interpretation": (
            "Lower bound only: ordinary Final status does not rule out a "
            "shortened game or discretionary venue review."),
        "by_case": by_case,
    }
