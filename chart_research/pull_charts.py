"""
chart_research/pull_charts.py
─────────────────────────────
Settled-data pull for P-025 (music-chart mid-week edge). Caches raw Kalshi
pulls to chart_research/data/ so backtest_charts.py never re-hits the shared
2 req/s Kalshi budget.

Two cached artifacts:
  data/events.json          one record per settled KXTOPSONG / KXTOPALBUM
                            event (chart-week): chart_sat (Billboard chart
                            date = Saturday encoded in the ticker), the
                            decision-time timestamps, the winner strike, and
                            every strike's (yes_sub_title, result,
                            settlement_value_dollars, volume_fp).
  data/candles/<ticker>.json  HOURLY candlesticks for the winner strike and
                            every strike that traded (volume_fp>0), over the
                            market's [open, close] window. Each candle carries
                            yes_ask.close_dollars (taker entry price),
                            yes_bid.close_dollars (spread proxy) and the hour's
                            volume_fp (the honest fill cap).

Settlement data model (verified live 2026-07-23, same schema as the golf
round-leader work):
  result='yes'    settlement_value_dollars = 1.0000   (was Billboard #1)
  result='no'     settlement_value_dollars = 0.0000   (was not #1)
  result='scalar' settlement_value_dollars = 1/n      (n-way #1 dead-heat;
                  the RANKLIST tie rule — never observed in this sample).

Timing model (Billboard modern calendar, cross-checked against live
close_time): for a chart dated Saturday chart_sat, the tracking week runs
Fri (chart_sat-15) .. Thu (chart_sat-9); the Kalshi book closes Sun
(chart_sat-6) 23:59 ET; Billboard announces Tue (chart_sat-4). Decision
times used by the backtest:
  T-mid   = Wed of tracking week (chart_sat-10) 20:00 ET  (HITS midweek out)
  T-close = Fri after tracking week (chart_sat-8) 12:00 ET (full-week data)
  T-late  = Sat (chart_sat-7) 18:00 ET  (book still open, ~deterministic)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
ET = ZoneInfo("America/New_York")
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "chart-research/P-025"})

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CANDLE_DIR = os.path.join(DATA, "candles")

SERIES = ["KXTOPSONG", "KXTOPALBUM"]

_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

_last = [0.0]


def throttle(min_int: float = 0.3) -> None:
    now = time.time()
    wait = _last[0] + min_int - now
    if wait > 0:
        time.sleep(wait)
    _last[0] = max(now, _last[0] + min_int)


def get(path: str, retries: int = 4):
    for i in range(retries):
        try:
            throttle()
            r = _SESSION.get(BASE + path, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 * (i + 1))
                continue
            print("  HTTP", r.status_code, path[:90], flush=True)
            return None
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                print("  ERR", path[:90], e, flush=True)
                return None
            time.sleep(2 * (i + 1))


def epoch(s: str):
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError, TypeError):
        return None


def parse_chart_date(event_ticker: str) -> datetime | None:
    """KXTOPSONG-26JUL25 -> date(2026,7,25) at 00:00 ET (Billboard chart Sat)."""
    tag = event_ticker.split("-")[-1]            # 26JUL25
    try:
        yy = int(tag[:2]); mon = _MONTHS[tag[2:5]]; dd = int(tag[5:])
    except (ValueError, KeyError):
        return None
    return datetime(2000 + yy, mon, dd, tzinfo=ET)


def decision_times(chart_sat: datetime) -> dict:
    """Return {name: (iso_et, epoch_utc)} for the three decision moments.

    chart_sat is midnight ET on the Billboard chart Saturday. The offsets are
    validated against the live close_time (Sun = chart_sat-6 23:59 ET)."""
    def mk(days_before: int, hour: int):
        dt = (chart_sat - timedelta(days=days_before)).replace(hour=hour)
        return {"et": dt.isoformat(), "epoch": int(dt.timestamp()),
                "date": dt.date().isoformat()}
    return {
        "T_mid":   mk(10, 20),   # Wed of tracking week, 8pm ET
        "T_close": mk(8, 12),    # Fri after tracking week, noon ET
        "T_late":  mk(7, 18),    # Sat, 6pm ET (book closes Sun 23:59)
    }


# ── Step 1: enumerate settled events ──────────────────────────────────────

def pull_events() -> list[dict]:
    os.makedirs(DATA, exist_ok=True)
    events: list[dict] = []
    for series in SERIES:
        markets, cursor = [], None
        while True:
            p = (f"/markets?series_ticker={series}&status=settled&limit=1000"
                 + (f"&cursor={cursor}" if cursor else ""))
            d = get(p)
            if not d:
                break
            b = d.get("markets", [])
            markets += b
            cursor = d.get("cursor")
            if not cursor or not b:
                break
        by_ev: dict[str, list] = {}
        for m in markets:
            by_ev.setdefault(m.get("event_ticker"), []).append(m)
        for ev, mk in sorted(by_ev.items()):
            chart_sat = parse_chart_date(ev)
            if chart_sat is None:
                print("  ! could not parse chart date for", ev)
                continue
            # weekday guard: Billboard chart dates are Saturdays
            wd = chart_sat.weekday()          # Mon=0 .. Sat=5
            strikes = []
            for m in mk:
                strikes.append({
                    "ticker": m.get("ticker"),
                    "sub": m.get("yes_sub_title"),
                    "result": m.get("result"),
                    "sv": float(m.get("settlement_value_dollars") or 0),
                    "volume": float(m.get("volume_fp") or 0),
                    "last_price": (float(m["last_price_dollars"])
                                   if m.get("last_price_dollars") else None),
                })
            winners = [s for s in strikes if s["result"] == "yes"]
            scalars = [s for s in strikes if s["result"] == "scalar"]
            events.append({
                "series": series,
                "event": ev,
                "chart_sat": chart_sat.date().isoformat(),
                "chart_sat_weekday": wd,          # 5 == Saturday (expected)
                "open_time": min(m.get("open_time") for m in mk),
                "close_time": mk[0].get("close_time"),
                "decisions": decision_times(chart_sat),
                "n_strikes": len(strikes),
                "total_volume": sum(s["volume"] for s in strikes),
                "winner": winners[0]["sub"] if winners else None,
                "winner_ticker": winners[0]["ticker"] if winners else None,
                "winner_sv": winners[0]["sv"] if winners else None,
                "has_tie": bool(scalars),
                "scalars": [{"sub": s["sub"], "sv": s["sv"]} for s in scalars],
                "strikes": strikes,
            })
        print(f"[events] {series}: {len(by_ev)} events, {len(markets)} markets",
              flush=True)
    with open(os.path.join(DATA, "events.json"), "w") as f:
        json.dump(events, f, indent=1)
    # weekday sanity
    bad = [e["event"] for e in events if e["chart_sat_weekday"] != 5]
    if bad:
        print("  ! non-Saturday chart dates (check timing model):", bad)
    return events


# ── Step 2: hourly candlesticks for traded strikes ────────────────────────

def pull_candles(min_volume: float = 1.0) -> None:
    os.makedirs(CANDLE_DIR, exist_ok=True)
    events = json.load(open(os.path.join(DATA, "events.json")))
    todo = []
    for e in events:
        st, et = epoch(e["open_time"]), epoch(e["close_time"])
        for s in e["strikes"]:
            # winner always; other strikes only if they traded
            if s["result"] != "yes" and s["volume"] < min_volume:
                continue
            tk = s["ticker"]
            if os.path.exists(os.path.join(CANDLE_DIR, tk + ".json")):
                continue
            todo.append((e["series"], tk, st, et, e["event"],
                         s["result"], s["sv"]))
    print(f"[candles] {len(todo)} strikes need hourly candles", flush=True)
    for i, (series, tk, st, et, ev, res, sv) in enumerate(todo):
        if st is None or et is None:
            continue
        c = get(f"/series/{series}/markets/{tk}/candlesticks"
                f"?start_ts={st}&end_ts={et}&period_interval=60")
        cndl = (c or {}).get("candlesticks", []) if c else []
        with open(os.path.join(CANDLE_DIR, tk + ".json"), "w") as f:
            json.dump({"ticker": tk, "series": series, "event": ev,
                       "result": res, "sv": sv, "candles": cndl}, f)
        if (i + 1) % 25 == 0:
            print(f"[candles] {i+1}/{len(todo)}", flush=True)
    print("[candles] done", flush=True)


if __name__ == "__main__":
    import sys
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("events", "all"):
        pull_events()
    if step in ("candles", "all"):
        pull_candles()
