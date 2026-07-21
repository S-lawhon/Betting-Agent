#!/usr/bin/env python3
"""
scripts/dump_gamma_events.py
────────────────────────────
Diagnostic: dump raw Gamma /events API response for one sport,
then VERIFY each conditionId by looking it up via /markets?condition_id=X.

This reveals whether the conditionIds in /events are correct or stale.

Usage:
  /opt/betting-pod-shop/venv/bin/python scripts/dump_gamma_events.py
"""

import json
import sys

import httpx

GAMMA = "https://gamma-api.polymarket.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BettingPodShop/1.0)"}

# Tag ID for game-day bets
GAME_BET_TAG_ID = 100639


def get_sports():
    """Fetch /sports metadata."""
    resp = httpx.get(f"{GAMMA}/sports", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def find_series_id(sports_data, league="NBA"):
    """Find series_id for a league."""
    target = league.upper()
    for obj in sports_data:
        sport_abbrev = (obj.get("sport") or "").upper()
        series_val = obj.get("series", "")
        if sport_abbrev == target:
            try:
                return int(series_val)
            except (ValueError, TypeError):
                continue
    fallback = {"NBA": 10345}
    return fallback.get(target)


def get_events(series_id=None, tag_id=None, limit=5):
    """Fetch /events."""
    params = {"active": "true", "closed": "false", "limit": limit}
    if series_id:
        params["series_id"] = series_id
    if tag_id:
        params["tag_id"] = tag_id

    resp = httpx.get(f"{GAMMA}/events", params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    raw = resp.json()
    if not isinstance(raw, list):
        raw = raw.get("data", raw.get("events", []))
    return raw


def verify_condition_id(cid):
    """Look up a conditionId via /markets and return what it actually points to."""
    if not cid:
        return {"error": "no conditionId"}
    try:
        resp = httpx.get(
            f"{GAMMA}/markets",
            params={"condition_id": cid, "limit": 1},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return {"error": "no results"}
        mkt = data[0] if isinstance(data, list) else data
        return {
            "question": (mkt.get("question") or "")[:100],
            "conditionId": mkt.get("conditionId", "?"),
            "slug": mkt.get("slug", "?"),
            "closed": mkt.get("closed"),
            "active": mkt.get("active"),
            "outcomes": mkt.get("outcomes"),
            "outcomePrices": mkt.get("outcomePrices"),
        }
    except Exception as e:
        return {"error": str(e)}


def lookup_market_by_slug(slug):
    """Try to find a market by its slug via /markets?slug=X."""
    if not slug:
        return None
    try:
        resp = httpx.get(
            f"{GAMMA}/markets",
            params={"slug": slug, "limit": 1},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list) and data[0]:
            mkt = data[0]
            return {
                "question": (mkt.get("question") or "")[:100],
                "conditionId": mkt.get("conditionId", "?"),
                "slug": mkt.get("slug", "?"),
            }
    except Exception:
        pass
    return None


def lookup_market_by_clob_token(token_id):
    """Try to find a market by clobTokenId via /markets?clob_token_ids=X."""
    if not token_id:
        return None
    try:
        resp = httpx.get(
            f"{GAMMA}/markets",
            params={"clob_token_ids": token_id, "limit": 1},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list) and data[0]:
            mkt = data[0]
            return {
                "question": (mkt.get("question") or "")[:100],
                "conditionId": mkt.get("conditionId", "?"),
                "clobTokenIds": (str(mkt.get("clobTokenIds", "?"))[:80]),
                "slug": mkt.get("slug", "?"),
            }
    except Exception:
        pass
    return None


def dump_event(event, idx):
    """Pretty-print one event's key fields + verify conditionIds."""
    print(f"\n{'='*80}")
    print(f"EVENT [{idx}]")
    print(f"  title:     {event.get('title', '?')}")
    print(f"  slug:      {event.get('slug', '?')}")
    print(f"  startDate: {event.get('startDate', '?')}")
    print(f"  endDate:   {event.get('endDate', '?')}")
    print(f"  active:    {event.get('active')}")
    print(f"  closed:    {event.get('closed')}")
    print(f"  top-keys:  {sorted(event.keys())}")

    markets = event.get("markets", [])
    print(f"  markets:   {len(markets)}")

    for j, mkt in enumerate(markets[:3]):  # First 3 markets
        print(f"\n  MARKET [{j}]")
        print(f"    ALL KEYS:       {sorted(mkt.keys())}")
        print(f"    question:       {(mkt.get('question') or '(none)')[:100]}")
        cid = mkt.get("conditionId") or mkt.get("condition_id", "")
        print(f"    conditionId:    {cid}")
        print(f"    id:             {mkt.get('id', '(none)')}")
        print(f"    slug:           {(mkt.get('slug') or '(none)')[:80]}")
        print(f"    outcomes:       {mkt.get('outcomes', '(none)')}")
        print(f"    outcomePrices:  {mkt.get('outcomePrices', '(none)')}")
        print(f"    groupItemTitle: {mkt.get('groupItemTitle', '(none)')}")

        clob_raw = mkt.get("clobTokenIds", "(none)")
        print(f"    clobTokenIds:   {str(clob_raw)[:120]}")

        tokens = mkt.get("tokens", [])
        print(f"    tokens:         {len(tokens)} items")
        for k, tok in enumerate(tokens[:4]):
            print(f"      token[{k}]: outcome={tok.get('outcome')!r}  "
                  f"tokenId={str(tok.get('tokenId', '?'))[:30]}...")

        # ── VERIFY the conditionId ──
        print(f"\n    >>> VERIFY conditionId via /markets?condition_id={cid[:20]}...")
        verify = verify_condition_id(cid)
        for k, v in verify.items():
            print(f"        {k}: {v}")

        # ── Try alternative lookups ──
        # 1) Look up by market slug
        mkt_slug = mkt.get("slug", "")
        if mkt_slug:
            print(f"\n    >>> LOOKUP by slug={mkt_slug[:40]}...")
            slug_result = lookup_market_by_slug(mkt_slug)
            if slug_result:
                for k, v in slug_result.items():
                    print(f"        {k}: {v}")
            else:
                print(f"        (no result)")

        # 2) Look up by first clobTokenId
        if clob_raw and clob_raw != "(none)":
            try:
                if isinstance(clob_raw, str):
                    clob_ids = json.loads(clob_raw)
                else:
                    clob_ids = clob_raw
                if clob_ids and isinstance(clob_ids, list):
                    first_tok = str(clob_ids[0])
                    print(f"\n    >>> LOOKUP by clobTokenId={first_tok[:30]}...")
                    tok_result = lookup_market_by_clob_token(first_tok)
                    if tok_result:
                        for k, v in tok_result.items():
                            print(f"        {k}: {v}")
                    else:
                        print(f"        (no result)")
            except (json.JSONDecodeError, TypeError, IndexError):
                pass

    # Full raw JSON of first market
    if markets:
        print(f"\n  RAW MARKET[0] (full JSON):")
        print(f"  {json.dumps(markets[0], indent=2, default=str)[:3000]}")


def main():
    print("Fetching /sports metadata...")
    sports = get_sports()
    print(f"Got {len(sports)} sport entries")

    for s in sports:
        print(f"  sport={s.get('sport')!r}  series={s.get('series')!r}  id={s.get('id')}")

    # Find NBA series_id
    nba_sid = find_series_id(sports, "NBA")
    print(f"\nNBA series_id: {nba_sid}")

    if nba_sid:
        print(f"\nFetching /events with series_id={nba_sid}, tag_id={GAME_BET_TAG_ID}...")
        events = get_events(series_id=nba_sid, tag_id=GAME_BET_TAG_ID, limit=2)
    else:
        print("\nNo NBA series_id found. Fetching unfiltered /events...")
        events = get_events(limit=2)

    print(f"Got {len(events)} events")

    for i, ev in enumerate(events):
        dump_event(ev, i)


if __name__ == "__main__":
    main()
