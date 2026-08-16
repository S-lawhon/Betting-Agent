#!/usr/bin/env python3
"""One-call entitlement/coverage check for historical KBO spread odds.

The API key is read from ``ODDS_API_KEY`` and is never printed or persisted.
The output contains only coverage counts, bookmaker names, quota headers, and
the provider snapshot timestamps.  This is a bounded feasibility check, not a
bulk historical download.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict

import requests


ENDPOINT = "https://api.the-odds-api.com/v4/historical/sports/baseball_kbo/odds"


def check(snapshot: str, regions: str) -> Dict[str, Any]:
    key = os.environ.get("ODDS_API_KEY", "")
    if not key:
        raise RuntimeError("ODDS_API_KEY is not configured")
    response = requests.get(
        ENDPOINT,
        params={
            "apiKey": key,
            "regions": regions,
            "markets": "spreads",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
            "date": snapshot,
        },
        timeout=30,
    )
    quota = {
        "requests_last": response.headers.get("x-requests-last"),
        "requests_used": response.headers.get("x-requests-used"),
        "requests_remaining": response.headers.get("x-requests-remaining"),
    }
    if response.status_code != 200:
        # Provider error messages are safe to retain; never retain the request
        # URL because it contains the API key in its query string.
        return {
            "schema_version": 1,
            "status": "unavailable",
            "http_status": response.status_code,
            "error": response.text[:300],
            "quota": quota,
        }
    payload = response.json()
    events = payload.get("data") or []
    bookmakers: Counter[str] = Counter()
    spread_markets = 0
    two_sided_lines = 0
    for event in events:
        for book in event.get("bookmakers") or []:
            bookmakers[str(book.get("key") or "unknown")] += 1
            for market in book.get("markets") or []:
                if market.get("key") != "spreads":
                    continue
                spread_markets += 1
                points: Counter[float] = Counter()
                for outcome in market.get("outcomes") or []:
                    if outcome.get("point") is not None:
                        points[abs(float(outcome["point"]))] += 1
                two_sided_lines += sum(count >= 2 for count in points.values())
    return {
        "schema_version": 1,
        "status": "available",
        "http_status": response.status_code,
        "requested_snapshot": snapshot,
        "provider_timestamp": payload.get("timestamp"),
        "previous_timestamp": payload.get("previous_timestamp"),
        "next_timestamp": payload.get("next_timestamp"),
        "events": len(events),
        "bookmakers": dict(sorted(bookmakers.items())),
        "spread_market_books": spread_markets,
        "two_sided_spread_lines": two_sided_lines,
        "quota": quota,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", default="2026-08-16T09:45:00Z")
    parser.add_argument("--regions", default="eu")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = check(args.snapshot, args.regions)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
        temp.write_text(text)
        os.replace(temp, args.output)
    print(text, end="")
    return 0 if result["status"] == "available" else 2


if __name__ == "__main__":
    raise SystemExit(main())
