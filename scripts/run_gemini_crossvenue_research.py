#!/usr/bin/env python3
"""Capture synchronized public Gemini/Kalshi MLB research snapshots.

The job is deliberately read-only.  It has no credential arguments and does
not import either venue's authenticated trading client.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gemini_kalshi_research import (  # noqa: E402
    evaluate_match, match_events, normalize_gemini_events,
    normalize_kalshi_events,
)
from src.dislocation_analytics import analyze_directory  # noqa: E402
from src.crossvenue_research import (  # noqa: E402
    evaluate_pair_signals, load_venue_pipeline,
)
from src.gemini_public import GeminiPublic  # noqa: E402
from src.kalshi_public import KalshiPublic  # noqa: E402
from src.settlement_registry import load_policy_state  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _write_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _append_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _timed_fetch(fetch: Callable[[], List[Dict[str, Any]]]) -> Dict[str, Any]:
    started = datetime.now(UTC)
    monotonic = time.monotonic()
    try:
        rows = fetch()
        error = None
    except Exception as exc:  # external failures are captured as degraded data
        rows = []
        error = f"{type(exc).__name__}: {exc}"
    finished = datetime.now(UTC)
    return {
        "started_at": _iso(started), "finished_at": _iso(finished),
        "midpoint_epoch": (started.timestamp() + finished.timestamp()) / 2.0,
        "latency_ms": round((time.monotonic() - monotonic) * 1000.0, 1),
        "rows": rows, "error": error,
    }


def _history_24h(directory: Path, now: datetime) -> Dict[str, Any]:
    cutoff = now - timedelta(hours=24)
    runs = set()
    matched = hypothetical = actionable = 0
    best: Optional[float] = None
    paths = [directory / f"{day.isoformat()}.jsonl"
             for day in (now.date(), (now - timedelta(days=1)).date())]
    for path in paths:
        try:
            lines = path.read_text().splitlines() if path.exists() else []
        except OSError:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            captured = _parse_iso(row.get("captured_at"))
            if not captured or captured < cutoff:
                continue
            runs.add(str(row.get("collection_id") or row.get("captured_at")))
            matched += 1
            hypothetical += int(row.get("hypothetical_positive_paths") or 0)
            actionable += int(row.get("actionable_paths") or 0)
            value = row.get("best_net_edge_usd")
            if isinstance(value, (int, float)) and (best is None or value > best):
                best = value
    return {
        "runs_with_matches": len(runs), "matched_events": matched,
        "hypothetical_positive_paths": hypothetical,
        "actionable_paths": actionable, "best_net_edge_usd": best,
    }


def _run_history_24h(directory: Path, now: datetime) -> Dict[str, int]:
    cutoff = now - timedelta(hours=24)
    snapshots = healthy = degraded = 0
    for day in (now.date(), (now - timedelta(days=1)).date()):
        path = directory / f"{day.isoformat()}.jsonl"
        try:
            lines = path.read_text().splitlines() if path.exists() else []
        except OSError:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            captured = _parse_iso(row.get("captured_at"))
            if not captured or captured < cutoff:
                continue
            snapshots += 1
            healthy += row.get("status") == "healthy"
            degraded += row.get("status") != "healthy"
    return {"snapshots": snapshots, "healthy_snapshots": healthy,
            "degraded_snapshots": degraded}


def collect(gemini: GeminiPublic, kalshi: KalshiPublic, output_dir: Path,
            *, now: Optional[datetime] = None,
            settlement_config: Path = ROOT / "config" / "settlement_equivalence.yaml",
            settlement_manifest: Path = ROOT / "data" / "settlement_registry"
            / "manifest.json",
            venue_config: Path = ROOT / "config" / "research_venues.yaml"
            ) -> Dict[str, Any]:
    captured_at = (now or datetime.now(UTC)).astimezone(UTC)
    collection_id = captured_at.strftime("%Y%m%dT%H%M%S.%fZ")
    with ThreadPoolExecutor(max_workers=2) as pool:
        gemini_future = pool.submit(
            _timed_fetch, gemini.active_prediction_events)
        kalshi_future = pool.submit(
            _timed_fetch, lambda: kalshi.open_events("KXMLBGAME"))
        captures = {"gemini": gemini_future.result(),
                    "kalshi": kalshi_future.result()}

    errors = {venue: result["error"] for venue, result in captures.items()
              if result["error"]}
    gemini_rows, gemini_rejections = normalize_gemini_events(
        captures["gemini"]["rows"])
    kalshi_rows, kalshi_rejections = normalize_kalshi_events(
        captures["kalshi"]["rows"])
    terms_policy = load_policy_state(
        settlement_config, settlement_manifest, "gemini_kalshi_mlb_moneyline")
    matches, matching = match_events(
        gemini_rows, kalshi_rows, terms_policy=terms_policy)
    skew = abs(captures["gemini"]["midpoint_epoch"]
               - captures["kalshi"]["midpoint_epoch"])
    evaluated = [evaluate_match(item, quote_skew_seconds=skew)
                 for item in matches]

    observations = []
    for item in evaluated:
        observations.append({
            "schema_version": 1, "collection_id": collection_id,
            "captured_at": _iso(captured_at),
            "venue_capture": {
                venue: {key: value for key, value in result.items()
                        if key not in ("rows", "midpoint_epoch")}
                for venue, result in captures.items()
            },
            **item,
        })
    history_path = (output_dir / "observations"
                    / f"{captured_at.date().isoformat()}.jsonl")
    if observations:
        _append_jsonl(history_path, observations)

    current = {
        "schema_version": 1, "mode": "read_only_research",
        "scope": "mlb_moneyline", "collection_id": collection_id,
        "captured_at": _iso(captured_at),
        "status": "degraded" if errors else "healthy", "errors": errors,
        "venue_capture": {
            venue: {"started_at": result["started_at"],
                    "finished_at": result["finished_at"],
                    "latency_ms": result["latency_ms"],
                    "raw_events": len(result["rows"]),
                    "error": result["error"]}
            for venue, result in captures.items()
        },
        "quote_skew_seconds": round(skew, 3),
        "normalization": {
            "gemini_events": len(gemini_rows), "kalshi_events": len(kalshi_rows),
            "gemini_rejections": gemini_rejections,
            "kalshi_rejections": kalshi_rejections,
        },
        "matching": matching,
        "terms_policy": terms_policy,
        "matches": evaluated,
    }
    _write_atomic(output_dir / "latest.json", current)

    latest_hypothetical = sum(item["hypothetical_positive_paths"]
                              for item in evaluated)
    latest_actionable = sum(item["actionable_paths"] for item in evaluated)
    best = max((item["best_net_edge_usd"] for item in evaluated
                if item["best_net_edge_usd"] is not None), default=None)
    run_history_path = (output_dir / "runs"
                        / f"{captured_at.date().isoformat()}.jsonl")
    _append_jsonl(run_history_path, [{
        "schema_version": 1, "collection_id": collection_id,
        "captured_at": _iso(captured_at), "status": current["status"],
        "matched_events": len(evaluated),
        "hypothetical_positive_paths": latest_hypothetical,
        "actionable_paths": latest_actionable,
    }])
    day_metrics = _history_24h(output_dir / "observations", captured_at)
    day_metrics.update(_run_history_24h(output_dir / "runs", captured_at))
    analytics = analyze_directory(
        output_dir / "observations", now=captured_at, window_days=14,
        threshold_usd=0.03)
    _write_atomic(output_dir / "analytics.json", {
        "generated_at": _iso(captured_at), **analytics})
    metrics = {
        "schema_version": 1, "mode": "read_only_research",
        "scope": "mlb_moneyline", "status": current["status"],
        "generated_at": _iso(captured_at), "last_error": errors or None,
        "terms_equivalence": terms_policy["status"],
        "terms_policy": terms_policy,
        "actionable_allowed": bool(terms_policy["actionable_allowed"]),
        "latest": {
            "gemini_events": len(gemini_rows), "kalshi_events": len(kalshi_rows),
            "matched_events": len(evaluated),
            "hypothetical_positive_paths": latest_hypothetical,
            "actionable_paths": latest_actionable,
            "best_net_edge_usd": best, "quote_skew_seconds": round(skew, 3),
            "venue_latency_ms": {venue: result["latency_ms"]
                                 for venue, result in captures.items()},
        },
        "last_24h": day_metrics,
        "analytics": analytics,
        "venue_pipeline": load_venue_pipeline(venue_config),
        "source": "data/gemini_crossvenue/latest.json",
    }
    metrics["research_signals"] = evaluate_pair_signals(
        "gemini_kalshi_mlb_moneyline", metrics)
    _write_atomic(output_dir / "metrics.json", metrics)
    return current


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data" / "gemini_crossvenue")
    parser.add_argument("--settlement-config", type=Path,
                        default=ROOT / "config" / "settlement_equivalence.yaml")
    parser.add_argument("--settlement-manifest", type=Path,
                        default=ROOT / "data" / "settlement_registry"
                        / "manifest.json")
    parser.add_argument("--venue-config", type=Path,
                        default=ROOT / "config" / "research_venues.yaml")
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = args.output_dir / ".collector.lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("gemini cross-venue collector already running", file=sys.stderr)
            return 75
        result = collect(
            GeminiPublic(), KalshiPublic(), args.output_dir,
            settlement_config=args.settlement_config,
            settlement_manifest=args.settlement_manifest,
            venue_config=args.venue_config)
    latest = result["matching"]["matched"]
    print(f"gemini cross-venue: status={result['status']} matched={latest} "
          f"skew={result['quote_skew_seconds']}s")
    return 0 if result["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
