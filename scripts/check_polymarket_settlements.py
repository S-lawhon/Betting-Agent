#!/usr/bin/env python3
"""
scripts/check_polymarket_settlements.py
────────────────────────────────────────
Diagnostic script: reads open P-006 trades from trade_log.jsonl and
checks each condition_id against the Polymarket Gamma API to see if
the oracle has resolved the market.

Usage (from the VPS, use the venv python):
  /opt/betting-pod-shop/venv/bin/python scripts/check_polymarket_settlements.py

Or with system python (will use urllib with proper headers):
  python3 scripts/check_polymarket_settlements.py

Check a specific condition_id:
  /opt/betting-pod-shop/venv/bin/python scripts/check_polymarket_settlements.py --cid 0x1234abcd...

Dump raw JSON for first N entries:
  /opt/betting-pod-shop/venv/bin/python scripts/check_polymarket_settlements.py --raw --limit 3
"""

import argparse
import json
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    httpx = None

import urllib.request
import urllib.error

GAMMA_API = "https://gamma-api.polymarket.com/markets"
TRADE_LOG = Path("data/trade_logs/trade_log.jsonl")

# Headers to avoid Cloudflare 403
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; BettingPodShop/1.0)",
}


def fetch_gamma(condition_id: str) -> dict:
    """Query Gamma API for a single condition_id."""
    params = {"condition_id": condition_id, "limit": "1"}

    if httpx:
        resp = httpx.get(
            GAMMA_API,
            params=params,
            headers=_HEADERS,
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
    else:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{GAMMA_API}?{qs}"
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

    if not isinstance(data, list) or not data:
        return {"error": f"No results for {condition_id[:20]}..."}
    return data[0]


def get_open_p006_entries() -> dict:
    """Return {market_id: placed_record} for unresolved P-006 PLACED entries."""
    if not TRADE_LOG.exists():
        print(f"Trade log not found: {TRADE_LOG}")
        return {}

    placed = {}
    resolved = set()

    for raw in TRADE_LOG.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue

        action = (rec.get("action") or "").upper()
        outcome = (rec.get("outcome") or "").upper()
        pod_id = rec.get("pod_id", "")

        if action == "BANKROLL_RESET":
            placed.clear()
            resolved.clear()
            continue

        if pod_id != "P-006":
            continue

        market_id = rec.get("market_id", "")
        if not market_id:
            continue

        if action == "PLACED":
            size = float(rec.get("position_size_usd") or 0)
            if size > 0:
                placed[market_id] = rec

        if outcome in ("WIN", "LOSS", "VOID"):
            resolved.add(market_id)

    return {mid: r for mid, r in placed.items() if mid not in resolved}


def check_one(condition_id: str, entry: dict = None, raw_mode: bool = False):
    """Check and print status for one condition_id."""
    market = fetch_gamma(condition_id)

    if "error" in market:
        print(f"  ERROR: {market['error']}")
        return

    if raw_mode:
        print(json.dumps(market, indent=2))
        return

    question = market.get("question", "?")[:80]
    closed = market.get("closed")
    active = market.get("active")
    archived = market.get("archived")
    prices_raw = market.get("outcomePrices", "")

    try:
        if isinstance(prices_raw, str):
            prices = [float(p) for p in json.loads(prices_raw)]
        elif isinstance(prices_raw, list):
            prices = [float(p) for p in prices_raw]
        else:
            prices = []
    except (ValueError, TypeError):
        prices = []

    resolved = False
    result = ""
    if closed and len(prices) >= 2:
        if prices[0] >= 0.95 and prices[1] <= 0.05:
            resolved, result = True, "YES"
        elif prices[1] >= 0.95 and prices[0] <= 0.05:
            resolved, result = True, "NO"

    status_icon = "RESOLVED" if resolved else ("CLOSED" if closed else "OPEN")

    print(f"  Question: {question}")
    print(f"  CID:      {condition_id[:24]}...")
    print(f"  Status:   {status_icon}  closed={closed}  active={active}  archived={archived}")
    print(f"  Prices:   {prices}  ->  {'Result: ' + result if resolved else 'Not resolved yet'}")
    if entry:
        side = entry.get("side", "?")
        size = entry.get("position_size_usd", "?")
        ts = entry.get("timestamp_utc", "?")[:19]
        print(f"  Our bet:  side={side}  size=${size}  placed={ts}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Check Polymarket settlement status")
    parser.add_argument("--cid", help="Check a specific condition_id")
    parser.add_argument("--raw", action="store_true", help="Dump raw JSON response")
    parser.add_argument("--limit", type=int, default=0, help="Only check first N entries")
    args = parser.parse_args()

    print(f"\nUsing: {'httpx' if httpx else 'urllib'}")

    if args.cid:
        print(f"Checking condition_id: {args.cid[:24]}...\n")
        check_one(args.cid, raw_mode=args.raw)
        return

    entries = get_open_p006_entries()
    if not entries:
        print("No open P-006 trades found in trade log.")
        return

    total = len(entries)
    if args.limit:
        entries = dict(list(entries.items())[:args.limit])

    print(f"Found {total} open P-006 trades. Checking {len(entries)}...\n")
    print("=" * 80)

    resolved_count = 0
    for i, (mid, entry) in enumerate(entries.items(), 1):
        print(f"\n[{i}/{len(entries)}]")
        try:
            check_one(mid, entry, raw_mode=args.raw)
        except Exception as exc:
            print(f"  ERROR: {exc}\n")
        # Small delay to avoid rate limiting
        time.sleep(0.3)

    print("=" * 80)
    print(f"Total open: {total}")
    print("Done.\n")


if __name__ == "__main__":
    main()
