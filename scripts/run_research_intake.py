#!/usr/bin/env python3
"""Collect broad research sources and produce a ranked, safe research inbox."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.env_loader import load as load_env  # noqa: E402
from src.research_eligibility import EligibilityRegistry  # noqa: E402
from src.research_intake import (  # noqa: E402
    CFTCCollector,
    FeedCollector,
    PolymarketUSCollector,
    SourceItem,
    XRecentSearchCollector,
    build_intake,
    market_census_items,
)


ROOT = Path(__file__).resolve().parent.parent
X_USAGE_SCHEMA_VERSION = 1


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _write_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _parse_now(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_offline_items(path: Path) -> List[SourceItem]:
    payload = _read_json(path, [])
    rows = payload.get("items", []) if isinstance(payload, dict) else payload
    return [SourceItem(**row) for row in rows]


def _x_usage_telemetry(config: Dict[str, Any], usage: Dict[str, Any],
                       now: datetime) -> Dict[str, Any]:
    """Return current conservative X cost estimates without exposing secrets."""
    budget = config.get("budget") or {}
    month = now.strftime("%Y-%m")
    day = now.date().isoformat()
    days = usage.get("days") or {}
    month_rows = [row for key, row in days.items() if key.startswith(month)]
    month_cost = round(sum(float(row.get("estimated_cost_usd", 0.0))
                           for row in month_rows), 4)
    month_posts = sum(int(row.get("post_reads", 0)) for row in month_rows)
    month_users = sum(int(row.get("user_reads", 0)) for row in month_rows)
    today = days.get(day) or {}
    return {
        "status": "ready",
        "utc_day": day,
        "month": month,
        "runs_today": int(today.get("runs", 0)),
        "post_reads_month": month_posts,
        "user_reads_month": month_users,
        "estimated_cost_month_usd": month_cost,
        "warning_usd": float(budget.get("warning_usd", 40.0)),
        "halt_usd": float(budget.get("halt_usd", 45.0)),
        "x_hard_limit_usd": float(budget.get("x_hard_limit_usd", 50.0)),
        "estimate_is_conservative": True,
    }


def _collect_x(config: Dict[str, Any], *, now: datetime,
               usage: Dict[str, Any]) -> tuple:
    """Collect X once within configured daily and monthly safety limits."""
    counts: Dict[str, int] = {}
    errors: List[Dict[str, str]] = []
    items: List[SourceItem] = []
    telemetry = _x_usage_telemetry(config, usage, now)

    if not config.get("enabled"):
        telemetry["status"] = "disabled_by_config"
        errors.append({
            "source": "x",
            "error": "X collection was requested but collectors.x.enabled is false",
        })
        return items, errors, counts, telemetry, False

    queries = [str(query) for query in config.get("queries") or []]
    maximum = int(config.get("max_results_per_query", 25))
    maximum_queries = int(config.get("max_queries_per_run", 3))
    queries = queries[:maximum_queries]
    if not queries:
        telemetry["status"] = "no_queries"
        errors.append({"source": "x", "error": "no X queries are configured"})
        return items, errors, counts, telemetry, False
    if not 10 <= maximum <= 100:
        telemetry["status"] = "invalid_config"
        errors.append({
            "source": "x",
            "error": "max_results_per_query must be between 10 and 100",
        })
        return items, errors, counts, telemetry, False

    budget = config.get("budget") or {}
    max_runs = int(budget.get("max_runs_per_utc_day", 1))
    if telemetry["runs_today"] >= max_runs:
        telemetry["status"] = "skipped_daily_limit"
        return items, errors, counts, telemetry, False

    post_price = float(budget.get("post_read_usd", 0.005))
    user_price = float(budget.get("user_read_usd", 0.010))
    maximum_run_cost = len(queries) * maximum * (post_price + user_price)
    projected = telemetry["estimated_cost_month_usd"] + maximum_run_cost
    telemetry["maximum_next_run_cost_usd"] = round(maximum_run_cost, 4)
    telemetry["projected_month_cost_usd"] = round(projected, 4)
    if projected > telemetry["halt_usd"]:
        telemetry["status"] = "halted_monthly_budget"
        return items, errors, counts, telemetry, False

    token = os.getenv("X_BEARER_TOKEN", "")
    if not token:
        telemetry["status"] = "missing_token"
        errors.append({"source": "x", "error": "X_BEARER_TOKEN is not set"})
        return items, errors, counts, telemetry, False

    collector = XRecentSearchCollector(token)
    post_reads = 0
    user_reads = 0
    requests_attempted = 0
    for query in queries:
        counts[f"x:{query}"] = 0
        requests_attempted += 1
        try:
            collected = collector.collect(query, max_results=maximum, now=now)
            items.extend(collected)
            counts[f"x:{query}"] = len(collected)
            post_reads += len(collected)
            user_reads += len({str(item.metadata.get("author_id"))
                               for item in collected
                               if item.metadata.get("author_id")})
        except Exception as exc:
            errors.append({"source": "x",
                           "error": f"{type(exc).__name__}: {exc}"})

    cost = round(post_reads * post_price + user_reads * user_price, 4)
    day = now.date().isoformat()
    days = usage.setdefault("days", {})
    row = days.setdefault(day, {
        "runs": 0, "requests": 0, "post_reads": 0, "user_reads": 0,
        "estimated_cost_usd": 0.0,
    })
    row["runs"] = int(row.get("runs", 0)) + 1
    row["requests"] = int(row.get("requests", 0)) + requests_attempted
    row["post_reads"] = int(row.get("post_reads", 0)) + post_reads
    row["user_reads"] = int(row.get("user_reads", 0)) + user_reads
    row["estimated_cost_usd"] = round(
        float(row.get("estimated_cost_usd", 0.0)) + cost, 4)
    usage["schema_version"] = X_USAGE_SCHEMA_VERSION

    telemetry = _x_usage_telemetry(config, usage, now)
    telemetry.update({
        "status": "completed" if not errors else "completed_with_errors",
        "requests_attempted": requests_attempted,
        "post_reads_this_run": post_reads,
        "user_reads_this_run": user_reads,
        "estimated_cost_this_run_usd": cost,
    })
    if telemetry["estimated_cost_month_usd"] >= telemetry["warning_usd"]:
        telemetry["warning"] = "estimated X usage has reached the warning threshold"
    return items, errors, counts, telemetry, True


def collect_live(config: Dict[str, Any], *, now: datetime,
                 include_x: bool = False,
                 x_usage: Optional[Dict[str, Any]] = None) -> tuple:
    collectors = config.get("collectors") or {}
    retrieved_at = now.isoformat().replace("+00:00", "Z")
    items: List[SourceItem] = []
    errors: List[Dict[str, str]] = []
    counts: Dict[str, int] = {}

    census = collectors.get("kalshi_census") or {}
    if census.get("enabled"):
        counts["kalshi_census"] = 0
        paths = sorted(ROOT.glob(str(census.get("inbox_glob") or "")))
        if paths:
            try:
                items.extend(market_census_items(
                    json.loads(paths[-1].read_text()), retrieved_at=retrieved_at))
                counts["kalshi_census"] = len(items)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                errors.append({"source": "kalshi_census", "error": str(exc)})

    cftc = collectors.get("cftc") or {}
    if cftc.get("enabled"):
        collector = CFTCCollector()
        for kind in cftc.get("kinds") or []:
            counts[f"cftc:{kind}"] = 0
            try:
                collected = collector.fetch(str(kind), now=now)
                items.extend(collected)
                counts[f"cftc:{kind}"] = len(collected)
            except Exception as exc:
                errors.append({"source": f"cftc:{kind}",
                               "error": f"{type(exc).__name__}: {exc}"})

    polymarket = collectors.get("polymarket_us") or {}
    if polymarket.get("enabled"):
        counts["polymarket_us"] = 0
        try:
            polymarket_collector = PolymarketUSCollector()
            collected = polymarket_collector.collect(
                limit=int(polymarket.get("page_size", 100)),
                max_pages=int(polymarket.get("max_pages", 10)), now=now)
            items.extend(collected)
            counts["polymarket_us"] = len(collected)
            if polymarket_collector.truncated:
                errors.append({
                    "source": "polymarket_us",
                    "error": "market inventory reached max_pages and may be truncated",
                })
        except Exception as exc:
            errors.append({"source": "polymarket_us",
                           "error": f"{type(exc).__name__}: {exc}"})

    for feed in collectors.get("feeds") or []:
        if not feed.get("enabled"):
            continue
        counts[f"feed:{feed.get('id')}"] = 0
        try:
            feed_items = FeedCollector(
                str(feed.get("name") or feed.get("id")),
                str(feed.get("type") or "paper"),
            ).fetch(str(feed["url"]), now=now)
            keywords = [str(value).lower() for value in
                        feed.get("include_keywords") or []]
            if keywords:
                feed_items = [item for item in feed_items if any(
                    keyword in f"{item.title} {item.summary}".lower()
                    for keyword in keywords)]
            items.extend(feed_items)
            counts[f"feed:{feed.get('id')}"] = len(feed_items)
        except Exception as exc:
            errors.append({"source": f"feed:{feed.get('id')}",
                           "error": f"{type(exc).__name__}: {exc}"})

    x_config = collectors.get("x") or {}
    usage = x_usage if x_usage is not None else {
        "schema_version": X_USAGE_SCHEMA_VERSION, "days": {}}
    x_telemetry = _x_usage_telemetry(x_config, usage, now)
    x_usage_changed = False
    if include_x:
        (x_items, x_errors, x_counts, x_telemetry,
         x_usage_changed) = _collect_x(x_config, now=now, usage=usage)
        items.extend(x_items)
        errors.extend(x_errors)
        counts.update(x_counts)
    else:
        x_telemetry["status"] = "not_requested"
    return items, errors, counts, x_telemetry, x_usage_changed


def main(argv: Optional[List[str]] = None) -> int:
    load_env(str(ROOT / ".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "config" / "research_sources.yaml")
    parser.add_argument("--eligibility", type=Path,
                        default=ROOT / "config" / "research_venues.yaml")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data" / "research_intake")
    parser.add_argument("--offline-items", type=Path,
                        help="read SourceItem JSON and skip all network collectors")
    parser.add_argument("--include-x", action="store_true",
                        help="use paid X recent search; requires X_BEARER_TOKEN")
    parser.add_argument("--max-assignments", type=int)
    parser.add_argument("--now", help="ISO timestamp (test/replay only)")
    args = parser.parse_args(argv)
    if args.max_assignments is not None and not 1 <= args.max_assignments <= 500:
        parser.error("--max-assignments must be between 1 and 500")

    config = yaml.safe_load(args.config.read_text()) or {}
    eligibility = EligibilityRegistry.load(args.eligibility)
    now = _parse_now(args.now)
    if args.offline_items:
        items = _load_offline_items(args.offline_items)
        errors: List[Dict[str, str]] = []
        collector_counts = {"offline_items": len(items)}
        x_telemetry = {"status": "offline"}
        x_usage_changed = False
    else:
        x_usage = _read_json(args.output_dir / "x_usage.json", {
            "schema_version": X_USAGE_SCHEMA_VERSION, "days": {}})
        (items, errors, collector_counts, x_telemetry,
         x_usage_changed) = collect_live(
            config, now=now, include_x=args.include_x, x_usage=x_usage)

    output = args.output_dir
    previous = _read_json(output / "ledger.json", {})
    maximum = (args.max_assignments or
               int((config.get("intake") or {}).get("max_assignments", 50)))
    ledger, manifest, assignments = build_intake(
        items, eligibility=eligibility, previous_ledger=previous,
        now=now, max_assignments=maximum)
    manifest["collector_errors"] = errors
    manifest["collector_counts"] = collector_counts
    manifest["x_usage"] = x_telemetry
    manifest["eligibility_audit"] = eligibility.audit(as_of=now.date())
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    _write_atomic(output / "ledger.json", ledger)
    if not args.offline_items and x_usage_changed:
        _write_atomic(output / "x_usage.json", x_usage)
    _write_atomic(output / "latest_manifest.json", manifest)
    _write_atomic(output / "manifests" / f"{stamp}.json", manifest)
    _write_atomic(output / "source_batches" / f"{stamp}.json", {
        "schema_version": 1, "retrieved_at": manifest["generated_at"],
        "items": [item.to_dict() for item in items],
    })
    _write_atomic(output / "assignments" / f"{stamp}.json", {
        "schema_version": 1, "generated_at": manifest["generated_at"],
        "kind": "research_assignment_inbox",
        "assignments": [assignment.to_dict() for assignment in assignments],
    })
    print(
        f"research intake: received={manifest['received']} "
        f"unique={manifest['new_unique']} assignments={manifest['assignments']} "
        f"deferred={manifest['backlog_deferred']} errors={len(errors)}"
    )
    # Partial source outages are recorded but do not discard healthy intake.
    # Fail the scheduled unit only when every collector failed to yield data.
    return 2 if errors and not items else 0


if __name__ == "__main__":
    raise SystemExit(main())
