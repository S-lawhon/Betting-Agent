#!/usr/bin/env python3
"""diagnose_tennis.py - Tennis matching diagnostic."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from kalshi_client import KalshiClient

base_url = "https://api.elections.kalshi.com/trade-api/v2"
api_key_id = os.environ["KALSHI_API_KEY_ID"]
pk = os.environ["KALSHI_PRIVATE_KEY"]
kalshi = KalshiClient(base_url=base_url, api_key_id=api_key_id, private_key=pk)

# 1. Query all open tennis markets
all_markets = {}
for series in ["KXATPMATCH", "KXATPSETWINNER", "KXWTAMATCH"]:
    cursor = None
    count = 0
    for _ in range(10):
        resp = kalshi.list_markets(status="open", limit=200, cursor=cursor, series_ticker=series)
        page = resp.get("markets", [])
        for m in page:
            t = m.get("ticker", "")
            all_markets[t] = {
                "series": series,
                "title": m.get("title", ""),
                "event_ticker": m.get("event_ticker", ""),
                "volume": m.get("volume", 0),
            }
            count += 1
        cursor = resp.get("cursor") or None
        if not cursor or len(page) < 200:
            break
    print("%s: %d open markets" % (series, count))

print("Total: %d unique markets" % len(all_markets))

# Group by event
events = {}
for t, info in all_markets.items():
    evt = info["event_ticker"]
    if evt not in events:
        events[evt] = []
    events[evt].append((t, info))
print("Events: %d" % len(events))

# 2. Search for Miami Open players
print("")
print("=== PLAYER SEARCH (open) ===")
for term in ["sinner", "lehecka", "sabalenka", "gauff", "miami"]:
    found = [(t,i) for t,i in all_markets.items() if term.lower() in i["title"].lower()]
    print("  %s: %d" % (term, len(found)))
    for t,i in found[:3]:
        print("    %s: %s" % (t, i["title"][:70]))

# 3. Check closed markets
print("")
print("=== CLOSED markets with Miami Open players ===")
for series in ["KXATPMATCH", "KXWTAMATCH"]:
    cursor = None
    for _ in range(5):
        resp = kalshi.list_markets(status="closed", limit=200, cursor=cursor, series_ticker=series)
        page = resp.get("markets", [])
        for m in page:
            title = m.get("title", "").lower()
            for term in ["sinner", "lehecka", "sabalenka", "gauff"]:
                if term in title:
                    print("  [closed] %s: %s" % (m["ticker"], m["title"][:70]))
        cursor = resp.get("cursor") or None
        if not cursor or len(page) < 200:
            break

# 4. Check settled markets
print("")
print("=== SETTLED markets with Miami Open players ===")
for series in ["KXATPMATCH", "KXWTAMATCH"]:
    cursor = None
    for _ in range(3):
        resp = kalshi.list_markets(status="settled", limit=200, cursor=cursor, series_ticker=series)
        page = resp.get("markets", [])
        for m in page:
            title = m.get("title", "").lower()
            for term in ["sinner", "lehecka", "sabalenka", "gauff"]:
                if term in title:
                    print("  [settled] %s: %s" % (m["ticker"], m["title"][:70]))
        cursor = resp.get("cursor") or None
        if not cursor or len(page) < 200:
            break

# 5. Event tickers
print("")
print("=== Event tickers (first 30) ===")
for evt in sorted(events.keys())[:30]:
    mkts = events[evt]
    vol = sum(m[1]["volume"] for m in mkts)
    title = mkts[0][1]["title"][:75]
    print("  %-50s vol=%8s mkts=%d  %s" % (evt, "{:,}".format(vol), len(mkts), title))

if len(events) > 30:
    print("  ... and %d more" % (len(events) - 30))

print("")
print("Done.")
