#!/usr/bin/env python3
"""diagnose_mlb.py - MLB matching diagnostic."""
import os
import sys
import json
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from kalshi_client import KalshiClient

base_url = "https://api.elections.kalshi.com/trade-api/v2"
api_key_id = os.environ["KALSHI_API_KEY_ID"]
pk = os.environ["KALSHI_PRIVATE_KEY"]
kalshi = KalshiClient(base_url=base_url, api_key_id=api_key_id, private_key=pk)

# 1. Kalshi OPEN MLB markets
print("=== KALSHI OPEN KXMLBGAME MARKETS ===")
open_markets = []
cursor = None
for _ in range(5):
    resp = kalshi.list_markets(status="open", limit=200, cursor=cursor, series_ticker="KXMLBGAME")
    page = resp.get("markets", [])
    open_markets.extend(page)
    cursor = resp.get("cursor") or None
    if not cursor or len(page) < 200:
        break
print("Open KXMLBGAME markets: %d" % len(open_markets))

# Group by date
dates = {}
for m in open_markets:
    t = m.get("ticker", "")
    # Ticker format: KXMLBGAME-26MAR28XXXX-XX
    # Extract date part
    parts = t.split("-")
    if len(parts) >= 2:
        date_part = parts[1][:7]  # e.g. "26MAR28"
    else:
        date_part = "unknown"
    if date_part not in dates:
        dates[date_part] = []
    dates[date_part].append(t)

for d in sorted(dates.keys()):
    print("  %s: %d markets" % (d, len(dates[d])))
    for t in dates[d][:3]:
        mk = next((m for m in open_markets if m["ticker"] == t), None)
        if mk:
            print("    %s: %s" % (t, mk.get("title", "")[:60]))

# 2. Kalshi ACTIVE (closed but not settled — in-play) MLB markets
print("")
print("=== KALSHI CLOSED KXMLBGAME MARKETS (recent, likely in-play) ===")
closed_markets = []
cursor = None
for _ in range(3):
    resp = kalshi.list_markets(status="closed", limit=200, cursor=cursor, series_ticker="KXMLBGAME")
    page = resp.get("markets", [])
    closed_markets.extend(page)
    cursor = resp.get("cursor") or None
    if not cursor or len(page) < 200:
        break
print("Closed KXMLBGAME markets: %d" % len(closed_markets))

# Filter to recent dates
for m in closed_markets:
    t = m.get("ticker", "")
    title = m.get("title", "")
    # Show March 28 games
    if "26MAR28" in t or "26MAR29" in t:
        print("  %s: %s" % (t, title[:60]))

# 3. Check individual tickers from settler (today's games)
print("")
print("=== STATUS CHECK: Today's games from settler ===")
today_tickers = [
    "KXMLBGAME-26MAR282140CLESEA-SEA",
    "KXMLBGAME-26MAR281910LAAHOU-HOU",
    "KXMLBGAME-26MAR281610BOSCIN-BOS",
    "KXMLBGAME-26MAR281610PITNYM-NYM",
]
for t in today_tickers:
    try:
        mk = kalshi.get_market(t)
        status = mk.get("market", {}).get("status", mk.get("status", "?"))
        title = mk.get("market", {}).get("title", mk.get("title", "?"))
        print("  %s: status=%s  %s" % (t, status, title[:50]))
    except Exception as exc:
        print("  %s: ERROR %s" % (t, str(exc)[:60]))

# 4. Odds API MLB events
print("")
print("=== ODDS API MLB EVENTS ===")
api_key = os.environ.get("ODDS_API_KEY", "")
if api_key:
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds?apiKey=%s&regions=us&markets=h2h&oddsFormat=american" % api_key
    with urllib.request.urlopen(url, timeout=15) as resp:
        events = json.loads(resp.read().decode())
    print("MLB events: %d" % len(events))
    for e in events:
        home = e.get("home_team", "?")
        away = e.get("away_team", "?")
        commence = e.get("commence_time", "?")
        books = [b.get("key", "") for b in e.get("bookmakers", [])]
        print("  %-25s vs %-25s  %s  books=%d" % (away, home, commence, len(books)))

print("")
print("Done.")
