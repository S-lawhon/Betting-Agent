#!/usr/bin/env python3
"""Align KBO Kalshi spread touches to timestamp-correct bookmaker spreads.

This is a small feasibility sample, not a validation backtest.  It consumes one
historical Odds API snapshot per distinct KBO slate timestamp and never stores
the API key or request URL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, Optional

import requests

from src.kalshi_fees import fee_per_contract


ENDPOINT = "https://api.the-odds-api.com/v4/historical/sports/baseball_kbo/odds"


def normalize_team(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def devig_probability(prices: Iterable[float], target_index: int) -> Optional[float]:
    implied = [1.0 / float(price) for price in prices if float(price) > 1.0]
    if len(implied) != 2 or not 0 <= target_index < 2:
        return None
    return implied[target_index] / sum(implied)


def book_probability(
    event: Dict[str, Any], *, target_team: str, margin: float
) -> Dict[str, float]:
    target = normalize_team(target_team)
    result: Dict[str, float] = {}
    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            if market.get("key") != "spreads":
                continue
            outcomes = list(market.get("outcomes") or [])
            if len(outcomes) != 2:
                continue
            target_indexes = [index for index, outcome in enumerate(outcomes)
                              if normalize_team(outcome.get("name")) == target]
            if len(target_indexes) != 1:
                continue
            index = target_indexes[0]
            point = outcomes[index].get("point")
            other_point = outcomes[1 - index].get("point")
            if point is None or other_point is None:
                continue
            if abs(float(point) + margin) > 1e-9:
                continue
            if abs(float(other_point) - margin) > 1e-9:
                continue
            probability = devig_probability(
                [float(outcomes[0]["price"]), float(outcomes[1]["price"])], index
            )
            if probability is not None:
                result[str(book.get("key") or "unknown")] = probability
    return result


def historical_snapshot(snapshot: str, regions: str) -> tuple[Dict[str, Any], int]:
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
    if response.status_code != 200:
        raise RuntimeError(
            f"Odds API historical request failed HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )
    return response.json(), int(response.headers.get("x-requests-last") or 0)


def run(input_path: Path, *, regions: str) -> Dict[str, Any]:
    source = json.loads(input_path.read_text())
    rows = source.get("rows") or []
    by_snapshot: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row.get("scheduled_start") or not row.get("t15"):
            continue
        start = datetime.fromisoformat(row["scheduled_start"].replace("Z", "+00:00"))
        snapshot = (start - timedelta(minutes=15)).astimezone(timezone.utc)
        by_snapshot[snapshot.isoformat().replace("+00:00", "Z")].append(row)

    aligned = []
    credits = 0
    event_matches: set[str] = set()
    for snapshot, contract_rows in sorted(by_snapshot.items()):
        payload, cost = historical_snapshot(snapshot, regions)
        credits += cost
        events = payload.get("data") or []
        for row in contract_rows:
            target = normalize_team(row.get("target_team"))
            matches = [event for event in events if target in {
                normalize_team(event.get("home_team")),
                normalize_team(event.get("away_team")),
            }]
            if len(matches) != 1 or row.get("margin") is None:
                continue
            event = matches[0]
            event_matches.add(str(row.get("event_ticker")))
            probabilities = book_probability(
                event,
                target_team=str(row["target_team"]),
                margin=float(row["margin"]),
            )
            if not probabilities:
                continue
            consensus = mean(probabilities.values())
            fair = probabilities.get("pinnacle", consensus)
            bid = float(row["t15"]["bid"])
            ask = float(row["t15"]["ask"])
            yes_edge = fair - ask - fee_per_contract(
                ask, maker=False, series_ticker="KXKBOSPREAD")
            no_price = 1.0 - bid
            no_edge = (1.0 - fair) - no_price - fee_per_contract(
                no_price, maker=False, series_ticker="KXKBOSPREAD")
            side, predicted_edge = (
                ("yes", yes_edge) if yes_edge >= no_edge else ("no", no_edge)
            )
            won = ((row.get("result") == "yes") if side == "yes"
                   else (row.get("result") == "no"))
            entry = ask if side == "yes" else no_price
            realized = (1.0 if won else 0.0) - entry - fee_per_contract(
                entry, maker=False, series_ticker="KXKBOSPREAD")
            aligned.append({
                "event_ticker": row.get("event_ticker"),
                "ticker": row.get("ticker"),
                "target_team": row.get("target_team"),
                "margin": row.get("margin"),
                "snapshot": snapshot,
                "book_count": len(probabilities),
                "has_pinnacle": "pinnacle" in probabilities,
                "consensus_probability": round(consensus, 6),
                "fair_probability": round(fair, 6),
                "kalshi_bid": bid,
                "kalshi_ask": ask,
                "best_side": side,
                "predicted_net_edge": round(predicted_edge, 6),
                "realized_net": round(realized, 6),
            })

    predicted = [row["predicted_net_edge"] for row in aligned]
    realized = [row["realized_net"] for row in aligned]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": str(input_path),
        "regions": regions,
        "snapshot_calls": len(by_snapshot),
        "credits_used": credits,
        "sample_contracts": len(rows),
        "matched_events": len(event_matches),
        "exact_line_contracts": len(aligned),
        "pinnacle_contracts": sum(row["has_pinnacle"] for row in aligned),
        "positive_predicted_edges": sum(value > 0 for value in predicted),
        "predicted_edge_median": round(median(predicted), 6) if predicted else None,
        "predicted_edge_mean": round(mean(predicted), 6) if predicted else None,
        "realized_net_mean": round(mean(realized), 6) if realized else None,
        "limitations": [
            "Exploratory latest-event sample; no train/holdout split or clustered confidence interval.",
            "Pinnacle is preferred when present; otherwise the multi-book consensus is used.",
            "Historical Kalshi depth and fill priority are unavailable.",
            "A positive discrepancy is not a validated strategy or execution authorization.",
        ],
        "rows": aligned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path,
        default=Path("kbo_spread_research/feasibility_results.json"),
    )
    parser.add_argument("--regions", default="eu")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.input, regions=args.regions)
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
