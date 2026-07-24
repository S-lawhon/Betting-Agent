"""
chart_research/forward_log.py
─────────────────────────────
P-025 Phase-1b $0 forward log — READ-ONLY, cron-able. Runs every Tue and Fri
for ~6 weeks. Each run snapshots, for the currently-open KXTOPSONG /
KXTOPALBUM week:
  - the free-data leader + margin (HITS album midweek; kworb US-Spotify daily)
  - the LIVE Kalshi yes-ask + top-of-book size on the strike matching that
    leader (and on the current market favourite)
and appends one JSON line to data/forward_log.jsonl.

Purpose: the decay / pick-over detector. The settled-data backtest KILLED the
tradeable thesis (see REPORT_Charts_2026-07.md); this log runs anyway to (a)
confirm the KILL prospectively and (b) catch the low-probability world where a
future week shows a wide free-data margin AND a lagging Kalshi ask with real
size. If Friday asks sit >=0.97 every week, the corner is already watched —
which reinforces the KILL. It places NOTHING; it only records quotes.

Cron (Mac local, per scripts/ convention — DO NOT add to pods.active):
  # Tue & Fri 18:00 ET
  0 18 * * 2,5  cd <repo>/chart_research && /usr/bin/python3 forward_log.py >> forward_log.out 2>&1
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
sys.path.insert(0, os.path.join(HERE, os.pardir, "src"))

import pull_reference as ref                       # noqa: E402
from kalshi_public import KalshiPublic             # noqa: E402


def open_event(kp: KalshiPublic, series: str):
    """Return (event_ticker, [markets]) for the single currently-open week,
    or (None, []). Uses the public /markets open filter."""
    mk = kp.open_markets(series, max_pages=3)
    if not mk:
        return None, []
    by_ev = {}
    for m in mk:
        by_ev.setdefault(m.get("event_ticker"), []).append(m)
    # the open week with the soonest close_time
    ev = min(by_ev, key=lambda e: min(m.get("close_time", "9999")
                                      for m in by_ev[e]))
    return ev, by_ev[ev]


def live_ask(kp: KalshiPublic, markets, leader_title):
    """Live yes-ask + ask size for the strike matching leader_title."""
    for m in markets:
        if ref.title_match(m.get("yes_sub_title", ""), leader_title):
            ob = kp.orderbook(m["ticker"])
            return {"ticker": m["ticker"], "sub": m.get("yes_sub_title"),
                    "yes_ask": (ob or {}).get("yes_ask"),
                    "ask_qty": (ob or {}).get("ask_qty")}
    return {"ticker": None, "sub": None, "yes_ask": None, "ask_qty": None,
            "note": "leader not a listed strike"}


def market_favourite(kp: KalshiPublic, markets):
    """Strike with the highest yes-bid (the market's current #1 pick)."""
    best = None
    for m in markets:
        ob = kp.orderbook(m["ticker"])
        yb = (ob or {}).get("yes_bid")
        if yb is not None and (best is None or yb > best["yes_bid"]):
            best = {"ticker": m["ticker"], "sub": m.get("yes_sub_title"),
                    "yes_bid": yb, "yes_ask": (ob or {}).get("yes_ask")}
    return best


def snapshot():
    kp = KalshiPublic(min_interval=0.4)
    now = datetime.now(timezone.utc)
    rows = []
    # ALBUM leg — HITS album midweek (live, latest)
    for series, sigfn in (("KXTOPALBUM", _hits_live), ("KXTOPSONG", _kworb_live)):
        ev, markets = open_event(kp, series)
        rec = {"ts_utc": now.isoformat(), "series": series, "event": ev}
        if not ev:
            rec["note"] = "no open event"; rows.append(rec); continue
        sig = sigfn()
        rec["signal"] = {k: sig.get(k) for k in
                         ("source", "leader", "runner_up", "margin")} if sig.get("ok") \
            else {"error": sig.get("reason")}
        if sig.get("ok"):
            rec["leader_ask"] = live_ask(kp, markets, sig["leader"])
        rec["market_favourite"] = market_favourite(kp, markets)
        rows.append(rec)
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "forward_log.jsonl"), "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    for r in rows:
        s = r.get("signal", {})
        la = r.get("leader_ask", {}) or {}
        print(f"[fwd] {r['series']:11} {r.get('event')} "
              f"leader={s.get('leader')} margin={s.get('margin')} "
              f"ask={la.get('yes_ask')} qty={la.get('ask_qty')}", flush=True)


def _hits_live():
    """HITS album midweek — the current (undated) page."""
    from datetime import datetime as _dt
    # hits_signal wants a chart_sat; the live page is fetched via the base URL.
    html = ref.fetch("https://www.hitsdailydouble.com/charts/midweek-20")
    if not html:
        return {"ok": False, "reason": "HITS live fetch failed"}
    rows = ref.parse_hits(html)
    if len(rows) < 2:
        return {"ok": False, "reason": "HITS live parse empty"}
    a1, a2 = rows[0].get("activity"), rows[1].get("activity")
    return {"ok": True, "source": "HITS-midweek-20-live",
            "leader": rows[0]["title"], "runner_up": rows[1]["title"],
            "margin": ((a1 - a2) / a1) if (a1 and a2) else None}


def _kworb_live():
    """kworb US-Spotify daily — the current live page (no wayback needed)."""
    html = ref.fetch("https://kworb.net/spotify/country/us_daily.html")
    if not html:
        return {"ok": False, "reason": "kworb live fetch failed"}
    rows = [r for r in ref.parse_kworb(html) if r["seven_day"]]
    rows.sort(key=lambda r: -r["seven_day"])
    if len(rows) < 2:
        return {"ok": False, "reason": "kworb live parse empty"}
    s1, s2 = rows[0]["seven_day"], rows[1]["seven_day"]
    return {"ok": True, "source": "kworb-us-daily-7day-live",
            "leader": rows[0]["title"], "runner_up": rows[1]["title"],
            "margin": ((s1 - s2) / s1) if s1 else None}


if __name__ == "__main__":
    snapshot()
