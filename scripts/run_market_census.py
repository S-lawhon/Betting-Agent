#!/usr/bin/env python3
"""Run the deterministic Kalshi universe census and write a scout inbox."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_public import KalshiPublic  # noqa: E402
from src.market_census import build_census, seeds_payload  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ROOT / "satellites_research" / "data" / "all_series.json"


def _read_json(path: Optional[Path], default: Any) -> Any:
    if path is None or not path.exists():
        return default
    return json.loads(path.read_text())


def _write_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _load_opportunities(directory: Path) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    if not directory.exists():
        return cards
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            cards.append(payload)
    return cards


def _fetch_live(client: Optional[KalshiPublic] = None) -> Dict[str, Any]:
    client = client or KalshiPublic(timeout=30.0)
    data = client.get("/series/", {"limit": 5})
    if not data or not data.get("series"):
        raise RuntimeError("live /series returned no series; outputs left unchanged")
    return data


def _fetch_active_series(client: KalshiPublic, max_pages: int = 100) -> set[str]:
    """Return every series with at least one currently open event."""
    active: set[str] = set()
    params: Dict[str, Any] = {"status": "open", "limit": 200}
    for _ in range(max_pages):
        data = client.get("/events", params)
        if not data:
            raise RuntimeError("live /events returned no payload; outputs left unchanged")
        for event in data.get("events") or []:
            ticker = str(event.get("series_ticker") or "").strip().upper()
            if ticker:
                active.add(ticker)
        cursor = data.get("cursor")
        if not cursor:
            return active
        params["cursor"] = cursor
    raise RuntimeError("live /events pagination exceeded safety cap")


def _parse_now(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--fetch-live", action="store_true",
                        help="read the public Kalshi /series endpoint")
    source.add_argument("--series-input", type=Path, default=DEFAULT_CACHE,
                        help="offline JSON input (default: cached all_series.json)")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data" / "market_census")
    parser.add_argument("--opportunities-dir", type=Path,
                        default=ROOT / "research" / "opportunities")
    parser.add_argument("--full", action="store_true",
                        help="seed unchanged families as well as deltas")
    parser.add_argument("--full-on-month-start", action="store_true",
                        help="automatically use full mode on UTC day 1")
    parser.add_argument("--max-seeds", type=int, default=25)
    parser.add_argument("--full-max-seeds", type=int, default=100,
                        help="seed cap for full/month-start runs")
    parser.add_argument("--now", help="ISO timestamp (test/replay only)")
    args = parser.parse_args(argv)

    if not 1 <= args.max_seeds <= 500 or not 1 <= args.full_max_seeds <= 500:
        parser.error("seed caps must be between 1 and 500")
    now = _parse_now(args.now)
    full = args.full or (args.full_on_month_start and now.day == 1)
    active_series = None
    if args.fetch_live:
        client = KalshiPublic(timeout=30.0)
        raw_series = _fetch_live(client)
        active_series = _fetch_active_series(client)
    else:
        raw_series = _read_json(args.series_input, None)
    if raw_series is None:
        raise SystemExit(f"series input does not exist: {args.series_input}")

    output = args.output_dir
    previous = _read_json(output / "snapshot.json", {})
    ledger = _read_json(output / "coverage_ledger.json", {})
    result = build_census(
        raw_series,
        previous_snapshot=previous,
        previous_ledger=ledger,
        opportunities=_load_opportunities(args.opportunities_dir),
        now=now,
        full=full,
        max_seeds=args.full_max_seeds if full else args.max_seeds,
        active_series=active_series,
    )

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    inbox = seeds_payload(result)
    _write_atomic(output / "snapshot.json", result.snapshot)
    _write_atomic(output / "coverage_ledger.json", result.ledger)
    _write_atomic(output / "latest_manifest.json", result.manifest)
    _write_atomic(output / "manifests" / f"{stamp}.json", result.manifest)
    _write_atomic(output / "scout_inbox" / f"{stamp}.json", inbox)

    changes = result.manifest["changes"]
    print(
        f"{result.manifest['run_id']}: {result.manifest['source_count']} series; "
        f"new={changes['new']} changed={changes['changed']} "
        f"missing={changes['missing']} inactive_suppressed="
        f"{changes['inactive_suppressed']}; {len(result.seeds)} scout seeds"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
