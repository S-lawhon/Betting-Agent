#!/usr/bin/env python3
"""Bounded, read-only feasibility check for Kalshi KBO spread history.

This is deliberately not a backtest.  It asks the cheaper prerequisite
questions first: do settled markets exist at useful cadence, do public
candlesticks preserve pregame bid/ask and prints, and what cannot be recovered
without a forward collector?
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from src.kalshi_public import KalshiPublic, fnum


SERIES = "KXKBOSPREAD"
SCHEDULE_RE = re.compile(
    r"originally scheduled for (?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4}) "
    r"at (?P<time>\d{1,2}:\d{2} [AP]M) E(?:D|S)T"
)
TARGET_RE = re.compile(
    r"Will the (?P<team>.+?) win by over (?P<margin>\d+(?:\.\d+)?) runs\?"
)


def scheduled_start(market: Dict[str, Any]) -> Optional[datetime]:
    match = SCHEDULE_RE.search(str(market.get("rules_primary") or ""))
    if not match:
        return None
    local = datetime.strptime(
        f"{match.group('date')} {match.group('time')}", "%b %d, %Y %I:%M %p"
    ).replace(tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(timezone.utc)


def contract_target(market: Dict[str, Any]) -> tuple[Optional[str], Optional[float]]:
    match = TARGET_RE.fullmatch(str(market.get("title") or "").strip())
    if not match:
        return None, None
    return match.group("team"), float(match.group("margin"))


def settled_markets(client: KalshiPublic, *, max_pages: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    params: Dict[str, Any] = {
        "series_ticker": SERIES,
        "status": "settled",
        "limit": 200,
    }
    for _ in range(max_pages):
        payload = client.get("/markets", params)
        if payload is None:
            raise RuntimeError("Kalshi settled-market request failed")
        rows.extend(item for item in payload.get("markets", [])
                    if isinstance(item, dict))
        cursor = payload.get("cursor")
        if not cursor:
            break
        params["cursor"] = cursor
    return rows


def latest_distinct_events(
    markets: Iterable[Dict[str, Any]], *, event_limit: int
) -> List[Dict[str, Any]]:
    chosen: List[Dict[str, Any]] = []
    events: set[str] = set()
    for market in markets:
        event = str(market.get("event_ticker") or "")
        if not event:
            continue
        if event not in events and len(events) >= event_limit:
            continue
        events.add(event)
        chosen.append(market)
    return chosen


def quote(candle: Dict[str, Any]) -> Optional[Dict[str, float]]:
    bid = fnum((candle.get("yes_bid") or {}).get("close_dollars"))
    ask = fnum((candle.get("yes_ask") or {}).get("close_dollars"))
    if bid is None or ask is None or not 0 < bid <= ask < 1:
        return None
    return {"bid": bid, "ask": ask, "spread": round(ask - bid, 4)}


def latest_quote_before(
    candles: List[Dict[str, Any]], cutoff: float, *, max_age_minutes: int = 30
) -> Optional[Dict[str, float]]:
    floor = cutoff - max_age_minutes * 60
    for candle in reversed(candles):
        ts = float(candle.get("end_period_ts") or 0)
        if floor <= ts <= cutoff:
            found = quote(candle)
            if found:
                return found | {"age_minutes": round((cutoff - ts) / 60, 2)}
    return None


def percentile(values: List[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * len(ordered)))
    return ordered[index]


def run(event_limit: int, max_pages: int, min_interval: float) -> Dict[str, Any]:
    client = KalshiPublic(min_interval=min_interval, timeout=20)
    all_markets = settled_markets(client, max_pages=max_pages)
    sample = latest_distinct_events(all_markets, event_limit=event_limit)
    rows: List[Dict[str, Any]] = []
    for market in sample:
        start = scheduled_start(market)
        target_team, margin = contract_target(market)
        row: Dict[str, Any] = {
            "ticker": market.get("ticker"),
            "event_ticker": market.get("event_ticker"),
            "result": market.get("result"),
            "title": market.get("title"),
            "target_team": target_team,
            "margin": margin,
            "lifetime_volume": fnum(market.get("volume_fp")) or 0.0,
            "scheduled_start": start.isoformat().replace("+00:00", "Z")
            if start else None,
            "n_candles": 0,
            "pregame_print_volume_6h": 0.0,
            "t60": None,
            "t15": None,
        }
        if start is None:
            rows.append(row)
            continue
        start_ts = start.timestamp()
        payload = client.get(
            f"/series/{SERIES}/markets/{market['ticker']}/candlesticks",
            {
                "start_ts": int((start - timedelta(hours=6)).timestamp()),
                "end_ts": int(start_ts),
                "period_interval": 1,
            },
        )
        candles = (payload or {}).get("candlesticks") or []
        row["n_candles"] = len(candles)
        row["pregame_print_volume_6h"] = round(sum(
            fnum(candle.get("volume_fp")) or 0.0 for candle in candles
        ), 2)
        row["t60"] = latest_quote_before(candles, start_ts - 3600)
        row["t15"] = latest_quote_before(candles, start_ts - 900)
        rows.append(row)

    spreads_60 = [row["t60"]["spread"] for row in rows if row["t60"]]
    spreads_15 = [row["t15"]["spread"] for row in rows if row["t15"]]
    print_volumes = [row["pregame_print_volume_6h"] for row in rows]
    lifetime_volumes = [row["lifetime_volume"] for row in rows]
    event_count = len({row["event_ticker"] for row in rows})
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "series_ticker": SERIES,
        "scope": {
            "settled_markets_seen": len(all_markets),
            "sample_events": event_count,
            "sample_markets": len(rows),
            "latest_distinct_events": event_limit,
            "pregame_window_hours": 6,
            "quote_max_age_minutes": 30,
        },
        "availability": {
            "scheduled_start_parsed": sum(bool(row["scheduled_start"]) for row in rows),
            "markets_with_candles": sum(row["n_candles"] > 0 for row in rows),
            "markets_with_t60_two_sided": len(spreads_60),
            "markets_with_t15_two_sided": len(spreads_15),
            "markets_with_pregame_prints": sum(value > 0 for value in print_volumes),
        },
        "liquidity": {
            "t60_spread_median": round(median(spreads_60), 4) if spreads_60 else None,
            "t60_spread_p75": round(percentile(spreads_60, 0.75), 4)
            if spreads_60 else None,
            "t15_spread_median": round(median(spreads_15), 4) if spreads_15 else None,
            "pregame_print_volume_median": round(median(print_volumes), 2)
            if print_volumes else None,
            "pregame_print_volume_p75": round(percentile(print_volumes, 0.75), 2)
            if print_volumes else None,
            "lifetime_volume_median": round(median(lifetime_volumes), 2)
            if lifetime_volumes else None,
        },
        "limitations": [
            "Candlesticks preserve historical touch and prints, not orderbook depth or fill priority.",
            "Kalshi settlement validates contract outcomes but does not supply an independent fair-value signal.",
            "No edge is estimated by this feasibility check.",
        ],
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--min-interval", type=float, default=0.3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.events <= 0 or args.max_pages <= 0 or args.min_interval < 0:
        parser.error("events/max-pages must be positive and min-interval non-negative")
    result = run(args.events, args.max_pages, args.min_interval)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
        temporary.write_text(text)
        os.replace(temporary, args.output)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
