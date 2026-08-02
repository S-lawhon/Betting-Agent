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


def collect_live(config: Dict[str, Any], *, now: datetime,
                 include_x: bool = False) -> tuple:
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
    if include_x:
        token = os.getenv("X_BEARER_TOKEN", "")
        if not token:
            errors.append({"source": "x", "error": "X_BEARER_TOKEN is not set"})
        else:
            collector = XRecentSearchCollector(token)
            maximum = int(x_config.get("max_results_per_query", 25))
            for query in x_config.get("queries") or []:
                counts[f"x:{query}"] = 0
                try:
                    collected = collector.collect(str(query), max_results=maximum,
                                                  now=now)
                    items.extend(collected)
                    counts[f"x:{query}"] = len(collected)
                except Exception as exc:
                    errors.append({"source": "x",
                                   "error": f"{type(exc).__name__}: {exc}"})
    return items, errors, counts


def main(argv: Optional[List[str]] = None) -> int:
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
    else:
        items, errors, collector_counts = collect_live(
            config, now=now, include_x=args.include_x)

    output = args.output_dir
    previous = _read_json(output / "ledger.json", {})
    maximum = (args.max_assignments or
               int((config.get("intake") or {}).get("max_assignments", 50)))
    ledger, manifest, assignments = build_intake(
        items, eligibility=eligibility, previous_ledger=previous,
        now=now, max_assignments=maximum)
    manifest["collector_errors"] = errors
    manifest["collector_counts"] = collector_counts
    manifest["eligibility_audit"] = eligibility.audit(as_of=now.date())
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    _write_atomic(output / "ledger.json", ledger)
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
