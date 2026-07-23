"""
mlb_totals_research/backtest_totals.py
──────────────────────────────────────
P-021 STEP-1 DECISION GATE — does a sharp book's closing MLB total (Pinnacle-eu
+ ex-DFS multi-book consensus) carry information that Kalshi's `KXMLBTOTAL`
pregame price lacks?  This is the cheapest falsification the spec puts BEFORE
any pod code (SPEC_P021_MLB_Totals_Sharp_Consensus.md §4), the same discipline
that killed P-019 at its gate.

Thesis (P-001 extended): Kalshi game moneylines lag a Pinnacle-weighted
consensus and trading the devigged gap earns positive CLV.  P-021 runs the
identical pipe on the maker-free `KXMLBTOTAL` series.  The honest risk is that
Kalshi is *also* efficient on totals, making the edge thin — the backtest
settles that before any capital.

Data pipe (verified live 2026-07-22, see memory p021-mlb-totals-feasibility):
  • Kalshi KXMLBTOTAL opens ~24h pregame, SETTLES AT GAME END (close_time = end
    of game, not first pitch).  The pregame decision anchor is therefore T-Xh
    before COMMENCE (first pitch), decoded from the event ticker
    (KXMLBTOTAL-26JUL221540ATHAZ → 15:40 ET).  Central strikes carry a tight 1¢
    pregame spread + real volume; extreme strikes only trade in-game (ignored).
  • Realized outcome is FREE: Kalshi's settled `result` (yes/no) per exact
    strike IS the realized 0/1 — no /scores settler needed.
  • Sharp reference: Odds API historical snapshot at commence-5m, regions=eu
    → Pinnacle-eu totals (line + american price) + ex-DFS consensus over the
    other eu books; devig via src/devig.py → p_sharp = P(total > line).
  • Comparison is at the MAIN LINE: Pinnacle's posted total == the Kalshi strike
    at the same number (both are X.5).  One clean strike per game = one obs;
    stats are CLUSTERED BY GAME-DAY (same-day games share weather/pitching
    shocks), the mandatory discipline from the spec.

Pipeline (each stage disk-cached + resumable):
  1. pull_settled()   Kalshi settled KXMLBTOTAL over lookback -> settled_totals.jsonl
  2. pull_kalshi()    per event's central strikes: 1-min candles -> kalshi_prices.jsonl
                      (p_kalshi at entry T-1h and at Kalshi close T-5m, pregame vol)
  3. pull_pinnacle()  one Odds API historical snapshot per game @ commence-5m
                      -> pinnacle_totals.jsonl  (Pinnacle-eu + consensus, per line)
  4. score()          match, align at main line, devig, then Brier / day-clustered
                      gap regression / net-of-fee taker PnL / CLV -> REPORT + params

Usage:
  python3 backtest_totals.py --lookback-days 20            # full run
  python3 backtest_totals.py --lookback-days 6 --max-games 40   # smoke
  python3 backtest_totals.py --score-only                  # re-score cache
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DATA.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(HERE.parent))

from src.env_loader import load as load_env          # noqa: E402
from src.kalshi_public import KalshiPublic, fnum, _parse_iso  # noqa: E402
from src import devig                                 # noqa: E402
from src import kalshi_fees                            # noqa: E402

load_env()
KP = KalshiPublic(min_interval=0.25)
EDT = timezone(timedelta(hours=-4))   # MLB season = US Eastern Daylight Time
ODDS_BASE = "https://api.the-odds-api.com/v4"

# ── MLB team canonicalisation ────────────────────────────────────────────
# Kalshi event titles use a mix of city + nickname ("A's vs Arizona Total
# Runs?"); Odds API uses full "City Nickname".  Map every alias -> a canonical
# 3-letter code so the two venues match order-insensitively.
_TEAM_ALIASES: Dict[str, str] = {}
def _alias(code: str, *names: str) -> None:
    for n in names:
        _TEAM_ALIASES[n.lower()] = code
_alias("ARI", "arizona", "diamondbacks", "d-backs", "dbacks", "arizona diamondbacks")
_alias("ATL", "atlanta", "braves", "atlanta braves")
_alias("BAL", "baltimore", "orioles", "baltimore orioles")
_alias("BOS", "boston", "red sox", "boston red sox")
_alias("CHC", "chicago cubs", "cubs", "chicago c")
_alias("CWS", "chicago white sox", "white sox", "chicago w")
_alias("CIN", "cincinnati", "reds", "cincinnati reds")
_alias("CLE", "cleveland", "guardians", "cleveland guardians")
_alias("COL", "colorado", "rockies", "colorado rockies")
_alias("DET", "detroit", "tigers", "detroit tigers")
_alias("HOU", "houston", "astros", "houston astros")
_alias("KC",  "kansas city", "royals", "kansas city royals")
_alias("LAA", "la angels", "los angeles angels", "angels", "anaheim", "los angeles a")
_alias("LAD", "la dodgers", "los angeles dodgers", "dodgers", "los angeles d")
_alias("MIA", "miami", "marlins", "miami marlins")
_alias("MIL", "milwaukee", "brewers", "milwaukee brewers")
_alias("MIN", "minnesota", "twins", "minnesota twins")
_alias("NYM", "new york mets", "mets", "ny mets", "new york m")
_alias("NYY", "new york yankees", "yankees", "ny yankees", "new york y")
_alias("OAK", "oakland", "athletics", "a's", "as", "oakland athletics", "the athletics")
_alias("PHI", "philadelphia", "phillies", "philadelphia phillies")
_alias("PIT", "pittsburgh", "pirates", "pittsburgh pirates")
_alias("SD",  "san diego", "padres", "san diego padres")
_alias("SF",  "san francisco", "giants", "san francisco giants")
_alias("SEA", "seattle", "mariners", "seattle mariners")
_alias("STL", "st louis", "st. louis", "cardinals", "st louis cardinals", "st. louis cardinals")
_alias("TB",  "tampa bay", "rays", "tampa bay rays")
_alias("TEX", "texas", "rangers", "texas rangers")
_alias("TOR", "toronto", "blue jays", "toronto blue jays")
_alias("WSH", "washington", "nationals", "washington nationals", "nats")


def _canon_team(name: str) -> Optional[str]:
    n = (name or "").strip().lower()
    if n in _TEAM_ALIASES:
        return _TEAM_ALIASES[n]
    # longest-alias substring fallback (handles "the athletics", "ny mets")
    best, blen = None, 0
    for alias, code in _TEAM_ALIASES.items():
        if alias in n and len(alias) > blen:
            best, blen = code, len(alias)
    return best


def _teams_from_kalshi_title(title: str) -> Optional[Tuple[str, str]]:
    """"A's vs Arizona Total Runs?" -> ('OAK','ARI') (order-insensitive set)."""
    t = re.sub(r"\s*total runs\??\s*$", "", title or "", flags=re.IGNORECASE)
    parts = re.split(r"\s+vs\.?\s+", t, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None
    a, b = _canon_team(parts[0]), _canon_team(parts[1])
    if a and b and a != b:
        return tuple(sorted((a, b)))  # type: ignore
    return None


def _decode_commence(event_ticker: str) -> Optional[datetime]:
    """KXMLBTOTAL-26JUL221540ATHAZ -> 2026-07-22 15:40 ET -> UTC datetime."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})(\d{4})", event_ticker or "")
    if not m:
        return None
    yy, mon, dd, hhmm = m.groups()
    months = dict(JAN=1, FEB=2, MAR=3, APR=4, MAY=5, JUN=6, JUL=7, AUG=8,
                  SEP=9, OCT=10, NOV=11, DEC=12)
    try:
        aware = datetime(2000 + int(yy), months[mon], int(dd),
                         int(hhmm[:2]), int(hhmm[2:]), tzinfo=EDT)
    except (KeyError, ValueError):
        return None
    return aware.astimezone(timezone.utc)


def _strikeval(sub_title: str) -> Optional[float]:
    m = re.search(r"Over ([\d.]+)", sub_title or "")
    return float(m.group(1)) if m else None


# ── Stage 1: Kalshi settled KXMLBTOTAL markets ───────────────────────────

def pull_settled(lookback_days: int) -> List[Dict[str, Any]]:
    out_path = DATA / "settled_totals.jsonl"
    if out_path.exists():
        rows = [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
        print(f"[settled] {len(rows)} cached", flush=True)
        return rows
    min_close = int(time.time()) - lookback_days * 86400
    rows: List[Dict[str, Any]] = []
    cursor = None
    pages = 0
    while True:
        params: Dict[str, Any] = {
            "series_ticker": "KXMLBTOTAL", "status": "settled",
            "min_close_ts": min_close, "limit": 1000,
        }
        if cursor:
            params["cursor"] = cursor
        data = KP.get("/markets", params)
        if not data:
            break
        ms = data.get("markets", [])
        for m in ms:
            res = (m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            sv = _strikeval(m.get("yes_sub_title") or "")
            if sv is None:
                continue
            rows.append({
                "ticker": m.get("ticker"),
                "event": m.get("event_ticker"),
                "title": m.get("title") or "",
                "sub": m.get("yes_sub_title") or "",
                "strike": sv,
                "result": 1 if res == "yes" else 0,
                "open_time": m.get("open_time"),
                "close_time": m.get("close_time"),
            })
        pages += 1
        cursor = data.get("cursor")
        if not cursor or not ms:
            break
    with open(out_path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    n_ev = len({r["event"] for r in rows})
    print(f"[settled] {len(rows)} settled strikes across {n_ev} events "
          f"({pages} pages, lookback {lookback_days}d)", flush=True)
    return rows


# ── Stage 2: Kalshi pregame prices via candlesticks ──────────────────────

def _mid_from_candle(c: Dict[str, Any]) -> Optional[float]:
    b = fnum((c.get("yes_bid") or {}).get("close_dollars"))
    a = fnum((c.get("yes_ask") or {}).get("close_dollars"))
    if b is not None and a is not None and 0 < b <= a < 1:
        return (a + b) / 2.0, b, a
    return None


def _quote_at(candles: List[Dict[str, Any]], ts: float
              ) -> Optional[Tuple[float, float, float]]:
    """Latest (mid, bid, ask) at or before ts. None if no valid quote."""
    prior = [c for c in candles if c.get("end_period_ts", 0) <= ts]
    for c in reversed(prior):
        q = _mid_from_candle(c)
        if q is not None:
            return q
    return None


def pull_kalshi(rows: List[Dict[str, Any]], entry_offset_h: float,
                strike_lo: float, strike_hi: float, workers: int = 8
                ) -> List[Dict[str, Any]]:
    """For strikes in [strike_lo, strike_hi], fetch 1-min candles and record
    the pregame quote at entry (commence - entry_offset_h) and at the Kalshi
    close (commence - 5min), plus pregame cumulative volume."""
    out_path = DATA / "kalshi_prices.jsonl"
    have = set()
    priced: List[Dict[str, Any]] = []
    if out_path.exists():
        for l in out_path.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                priced.append(r)
                have.add(r["ticker"])
    todo = [r for r in rows
            if r["ticker"] not in have
            and strike_lo <= r["strike"] <= strike_hi
            and _decode_commence(r["event"]) is not None]
    print(f"[kalshi] {len(todo)} strikes to price ({len(have)} cached)", flush=True)

    def fetch(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        commence = _decode_commence(r["event"])
        cl = _parse_iso(r.get("close_time"))
        op = _parse_iso(r.get("open_time"))
        if commence is None or cl is None or op is None:
            return {"ticker": r["ticker"], "priced": False, "why": "no_time"}
        commence_ts = commence.timestamp()
        entry_ts = commence_ts - entry_offset_h * 3600
        close_ts = commence_ts - 300.0
        try:
            data = KP.get(
                f"/series/KXMLBTOTAL/markets/{r['ticker']}/candlesticks",
                {"start_ts": int(op), "end_ts": int(cl), "period_interval": 1})
        except Exception as exc:  # noqa: BLE001
            return {"ticker": r["ticker"], "priced": False, "why": str(exc)[:40]}
        candles = (data or {}).get("candlesticks", [])
        if not candles:
            return {"ticker": r["ticker"], "priced": False, "why": "no_candles"}
        pre_vol = sum(fnum(c.get("volume_fp")) or 0.0 for c in candles
                      if c.get("end_period_ts", 0) <= commence_ts)
        q_entry = _quote_at(candles, entry_ts)
        q_close = _quote_at(candles, close_ts)
        rec: Dict[str, Any] = {
            "ticker": r["ticker"], "event": r["event"], "strike": r["strike"],
            "result": r["result"], "priced": True,
            "commence_ts": commence_ts, "pregame_vol": pre_vol,
        }
        rec["k_entry_mid"] = q_entry[0] if q_entry else None
        rec["k_entry_bid"] = q_entry[1] if q_entry else None
        rec["k_entry_ask"] = q_entry[2] if q_entry else None
        rec["k_close_mid"] = q_close[0] if q_close else None
        return rec

    with open(out_path, "a") as fh:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(fetch, r) for r in todo]
            n = 0
            for fut in as_completed(futs):
                rec = fut.result()
                n += 1
                if rec:
                    fh.write(json.dumps(rec) + "\n")
                    if rec.get("priced"):
                        priced.append(rec)
                if n % 200 == 0:
                    fh.flush()
                    print(f"[kalshi] {n}/{len(todo)}", flush=True)
    usable = [r for r in priced if r.get("priced") and r.get("k_entry_mid")]
    print(f"[kalshi] {len(priced)} priced, {len(usable)} with entry quote", flush=True)
    return priced


# ── Stage 3: Pinnacle / consensus closing totals via Odds API historical ──

def _odds_get(path: str, params: Dict[str, Any]) -> Tuple[Optional[Any], int]:
    key = os.environ.get("ODDS_API_KEY", "")
    params = dict(params); params["apiKey"] = key
    url = f"{ODDS_BASE}{path}?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as r:
                cost = int(r.headers.get("x-requests-last") or 0)
                return json.loads(r.read().decode()), cost
        except urllib.error.HTTPError as e:  # noqa
            if e.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1)); continue
            return {"_error": e.code, "_body": e.read().decode()[:200]}, 0
        except Exception:  # noqa: BLE001
            if attempt < 2:
                time.sleep(2 * (attempt + 1)); continue
            return None, 0
    return None, 0


def _devig_total_line(over_am: float, under_am: float) -> float:
    """Devig a two-way total to P(over) using multiplicative (35-65% band)."""
    po = devig.american_to_prob(over_am)
    pu = devig.american_to_prob(under_am)
    return devig.devig_two_way(po, pu)


def _extract_lines(game: Dict[str, Any]) -> Dict[str, Any]:
    """From one Odds API game, return per-total-line devigged P(over) for
    Pinnacle-eu and for an ex-DFS multi-book consensus (mean of devigged
    per-book P(over))."""
    per_line_books: Dict[float, List[float]] = defaultdict(list)
    pin_by_line: Dict[float, float] = {}
    for bk in game.get("bookmakers", []):
        key = bk.get("key", "")
        for mk in bk.get("markets", []):
            if mk.get("key") != "totals":
                continue
            outs = {o.get("name", "").lower(): o for o in mk.get("outcomes", [])}
            over, under = outs.get("over"), outs.get("under")
            if not over or not under:
                continue
            pt = over.get("point")
            if pt is None or under.get("point") != pt:
                continue
            try:
                p_over = _devig_total_line(float(over["price"]), float(under["price"]))
            except (KeyError, ValueError, ZeroDivisionError):
                continue
            per_line_books[float(pt)].append(p_over)
            if key == "pinnacle":
                pin_by_line[float(pt)] = p_over
    consensus = {ln: sum(ps) / len(ps) for ln, ps in per_line_books.items() if ps}
    return {"pinnacle": pin_by_line, "consensus": consensus,
            "books": len({bk.get("key") for bk in game.get("bookmakers", [])})}


def pull_pinnacle(events: List[Dict[str, Any]], max_games: Optional[int]
                  ) -> List[Dict[str, Any]]:
    """One historical snapshot per game at commence-5m; extract totals lines."""
    out_path = DATA / "pinnacle_totals.jsonl"
    have = set()
    got: List[Dict[str, Any]] = []
    if out_path.exists():
        for l in out_path.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                got.append(r)
                have.add(r["event"])
    todo = [e for e in events if e["event"] not in have]
    if max_games is not None:
        todo = todo[:max_games]
    print(f"[pinnacle] {len(todo)} games to snapshot ({len(have)} cached)", flush=True)

    total_cost = 0
    with open(out_path, "a") as fh:
        for i, e in enumerate(todo, 1):
            commence = _decode_commence(e["event"])
            teams = _teams_from_kalshi_title(e["title"])
            if commence is None or teams is None:
                rec = {"event": e["event"], "ok": False, "why": "decode"}
                fh.write(json.dumps(rec) + "\n"); got.append(rec); continue
            snap_dt = commence - timedelta(minutes=5)
            date_iso = snap_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            body, cost = _odds_get(
                "/historical/sports/baseball_mlb/odds",
                {"regions": "eu", "markets": "totals", "oddsFormat": "american",
                 "date": date_iso})
            total_cost += cost
            if not body or "_error" in (body or {}):
                rec = {"event": e["event"], "ok": False,
                       "why": (body or {}).get("_error", "no_body") if body else "none"}
                fh.write(json.dumps(rec) + "\n"); got.append(rec); continue
            # find the matching game in the snapshot board; for doubleheaders
            # (two same-team games on one day) pick the one whose commence is
            # closest to the Kalshi first-pitch time.
            commence_ts = commence.timestamp()
            best, best_dt = None, None
            for g in body.get("data", []):
                gt = tuple(sorted(filter(None, (_canon_team(g.get("away_team", "")),
                                                _canon_team(g.get("home_team", ""))))))
                if gt != teams:
                    continue
                gct = _parse_iso(g.get("commence_time"))
                dt_ = abs(gct - commence_ts) if gct else 1e12
                if best_dt is None or dt_ < best_dt:
                    best, best_dt = g, dt_
            if best is None:
                rec = {"event": e["event"], "ok": False, "why": "no_match_in_snap",
                       "teams": list(teams), "snap": date_iso,
                       "n_games": len(body.get("data", []))}
                fh.write(json.dumps(rec) + "\n"); got.append(rec); continue
            lines = _extract_lines(best)
            rec = {
                "event": e["event"], "ok": True, "teams": list(teams),
                "snap_ts": body.get("timestamp"),
                "commence_odds": best.get("commence_time"),
                "pinnacle": {f"{k:.1f}": v for k, v in lines["pinnacle"].items()},
                "consensus": {f"{k:.1f}": v for k, v in lines["consensus"].items()},
                "n_books": lines["books"],
            }
            fh.write(json.dumps(rec) + "\n"); got.append(rec)
            if i % 25 == 0:
                fh.flush()
                print(f"[pinnacle] {i}/{len(todo)}  (cost so far {total_cost} cr)",
                      flush=True)
    ok = [r for r in got if r.get("ok")]
    print(f"[pinnacle] {len(ok)} matched games, {len(got)-len(ok)} misses; "
          f"Odds API spend this run ~{total_cost} credits", flush=True)
    return got


# ── Stage 4: scoring ─────────────────────────────────────────────────────

def _brier(pairs: List[Tuple[float, int]]) -> float:
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else float("nan")


def _logloss(pairs: List[Tuple[float, int]]) -> float:
    eps = 1e-6
    if not pairs:
        return float("nan")
    return -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
                for p, y in pairs) / len(pairs)


def _cluster_ols(x: List[float], y: List[int], clusters: List[str]
                 ) -> Dict[str, float]:
    """OLS y = a + b*x with cluster-robust (CR0) SEs, cluster = game-day.
    Returns {a, b, se_b, t_b, n, n_clusters}."""
    n = len(x)
    if n < 3:
        return {"a": float("nan"), "b": float("nan"), "se_b": float("nan"),
                "t_b": float("nan"), "n": n, "n_clusters": 0}
    mx = sum(x) / n
    my = sum(y) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx <= 0:
        return {"a": float("nan"), "b": float("nan"), "se_b": float("nan"),
                "t_b": float("nan"), "n": n, "n_clusters": 0}
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    b = sxy / sxx
    a = my - b * mx
    resid = [yi - (a + b * xi) for xi, yi in zip(x, y)]
    # cluster-robust variance of b: (1/Sxx^2) * sum_g (sum_i (xi-mx)*e_i)^2
    by_c: Dict[str, float] = defaultdict(float)
    for xi, ei, c in zip(x, resid, clusters):
        by_c[c] += (xi - mx) * ei
    meat = sum(v * v for v in by_c.values())
    var_b = meat / (sxx ** 2)
    G = len(by_c)
    # small-sample cluster correction
    if G > 1 and n > 2:
        var_b *= (G / (G - 1)) * ((n - 1) / (n - 2))
    se_b = math.sqrt(var_b) if var_b > 0 else float("nan")
    t_b = b / se_b if se_b and se_b == se_b else float("nan")
    return {"a": a, "b": b, "se_b": se_b, "t_b": t_b, "n": n, "n_clusters": G}


def score(entry_offset_h: float, margin: float) -> Dict[str, Any]:
    kfile = DATA / "kalshi_prices.jsonl"
    pfile = DATA / "pinnacle_totals.jsonl"
    krows = [json.loads(l) for l in kfile.read_text().splitlines() if l.strip()] \
        if kfile.exists() else []
    prows = [json.loads(l) for l in pfile.read_text().splitlines() if l.strip()] \
        if pfile.exists() else []
    pin_by_event = {r["event"]: r for r in prows if r.get("ok")}

    # one record per (event, strike) where Pinnacle posted that exact line and
    # Kalshi has a pregame entry quote
    recs: List[Dict[str, Any]] = []
    for k in krows:
        if not k.get("priced") or k.get("k_entry_mid") is None:
            continue
        pin = pin_by_event.get(k["event"])
        if not pin:
            continue
        sk = f"{k['strike']:.1f}"
        p_pin = pin["pinnacle"].get(sk)
        p_con = pin["consensus"].get(sk)
        if p_pin is None and p_con is None:
            continue
        day = k["event"][11:18] if k.get("event") else "?"  # ...26JUL22...
        recs.append({
            "event": k["event"], "day": day, "strike": k["strike"],
            "result": k["result"],
            "p_pin": p_pin, "p_con": p_con,
            "k_entry_mid": k["k_entry_mid"], "k_entry_bid": k.get("k_entry_bid"),
            "k_entry_ask": k.get("k_entry_ask"), "k_close_mid": k.get("k_close_mid"),
            "pregame_vol": k.get("pregame_vol", 0.0),
        })

    # Prefer Pinnacle; fall back to consensus for the reference prob
    for r in recs:
        r["p_sharp"] = r["p_pin"] if r["p_pin"] is not None else r["p_con"]

    n_games = len({r["event"] for r in recs})
    n_days = len({r["day"] for r in recs})

    # (i) Brier / logloss: p_sharp vs p_kalshi(entry mid) on realized
    valid = [r for r in recs if r["p_sharp"] is not None]
    brier_sharp = _brier([(r["p_sharp"], r["result"]) for r in valid])
    brier_kal = _brier([(r["k_entry_mid"], r["result"]) for r in valid])
    ll_sharp = _logloss([(r["p_sharp"], r["result"]) for r in valid])
    ll_kal = _logloss([(r["k_entry_mid"], r["result"]) for r in valid])
    # midpoint 50/50 baseline for reference
    brier_base = _brier([(0.5, r["result"]) for r in valid])

    # (ii) gap regression: realized ~ a + b*(p_sharp - p_kalshi), day-clustered
    gap = [r["p_sharp"] - r["k_entry_mid"] for r in valid]
    reg = _cluster_ols(gap, [r["result"] for r in valid], [r["day"] for r in valid])

    # (iii) net-of-fee taker PnL, flat 1 contract, both directions
    trades = []
    for r in valid:
        ask = r["k_entry_ask"]; bid = r["k_entry_bid"]
        if ask is None or bid is None:
            continue
        ps = r["p_sharp"]; y = r["result"]
        # buy YES at ask
        fee_y = kalshi_fees.fee_per_contract(ask, maker=False,
                                             series_ticker="KXMLBTOTAL")
        if ps - ask - fee_y > margin:
            trades.append({"side": "YES", "px": ask, "fee": fee_y,
                           "pnl": (y - ask) - fee_y, "r": r})
            continue
        # buy NO at (1 - bid): NO fair = 1-ps, NO cost = 1-bid, pays if y==0
        no_cost = 1.0 - bid
        fee_n = kalshi_fees.fee_per_contract(no_cost, maker=False,
                                             series_ticker="KXMLBTOTAL")
        if (1 - ps) - no_cost - fee_n > margin:
            trades.append({"side": "NO", "px": no_cost, "fee": fee_n,
                           "pnl": ((1 - y) - no_cost) - fee_n, "r": r})

    pnl_list = [t["pnl"] for t in trades]
    n_tr = len(trades)
    tot_pnl = sum(pnl_list)
    avg_pnl = tot_pnl / n_tr if n_tr else float("nan")
    # day-clustered mean PnL CI (bootstrap over game-days)
    pnl_ci = _bootstrap_day_ci([(t["r"]["day"], t["pnl"]) for t in trades])

    # (iv) CLV: for each taken trade, Kalshi close mid vs entry price, in the
    # direction of the trade. Positive = Kalshi moved toward our side post-entry.
    clv_vals = []
    for t in trades:
        kc_ = t["r"]["k_close_mid"]
        if kc_ is None:
            continue
        if t["side"] == "YES":
            clv_vals.append(kc_ - t["px"])            # bought YES at ask
        else:
            clv_vals.append((1 - kc_) - t["px"])      # bought NO at (1-bid)
    clv_mean = sum(clv_vals) / len(clv_vals) if clv_vals else float("nan")
    clv_ci = _bootstrap_day_ci(
        [(t["r"]["day"], (t["r"]["k_close_mid"] - t["px"]) if t["side"] == "YES"
          else ((1 - t["r"]["k_close_mid"]) - t["px"]))
         for t in trades if t["r"]["k_close_mid"] is not None])

    # also: does the sharp gap predict Kalshi's OWN drift to close? (leading
    # indicator check, independent of realized noise)
    drift_pairs = [(r["p_sharp"] - r["k_entry_mid"], r["k_close_mid"] - r["k_entry_mid"])
                   for r in valid if r["k_close_mid"] is not None]
    drift_reg = _cluster_ols([g for g, _ in drift_pairs],
                             [d for _, d in drift_pairs],  # type: ignore
                             [r["day"] for r in valid if r["k_close_mid"] is not None]) \
        if drift_pairs else {}

    return {
        "n_records": len(valid), "n_games": n_games, "n_days": n_days,
        "entry_offset_h": entry_offset_h, "margin": margin,
        "brier": {"sharp": brier_sharp, "kalshi": brier_kal, "base_50": brier_base},
        "logloss": {"sharp": ll_sharp, "kalshi": ll_kal},
        "gap_regression": reg,
        "drift_regression": drift_reg,
        "taker": {"n_trades": n_tr, "total_pnl": tot_pnl, "avg_pnl_ct": avg_pnl,
                  "avg_pnl_ci": pnl_ci},
        "clv": {"n": len(clv_vals), "mean_ct": clv_mean, "ci": clv_ci},
        "mean_pregame_vol": (sum(r["pregame_vol"] for r in valid) / len(valid)
                             if valid else 0.0),
    }


def _bootstrap_day_ci(items: List[Tuple[str, float]], n_boot: int = 2000,
                      seed: int = 7) -> Tuple[float, float]:
    """95% CI for the mean, resampling whole game-days (clustered)."""
    if not items:
        return (float("nan"), float("nan"))
    by_day: Dict[str, List[float]] = defaultdict(list)
    for d, v in items:
        by_day[d].append(v)
    days = list(by_day.keys())
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        pool: List[float] = []
        for _ in range(len(days)):
            pool.extend(by_day[days[rng.randrange(len(days))]])
        if pool:
            means.append(sum(pool) / len(pool))
    means.sort()
    return (means[int(0.025 * len(means))], means[int(0.975 * len(means))])


# ── Report ───────────────────────────────────────────────────────────────

def _verdict(s: Dict[str, Any]) -> Tuple[str, List[str]]:
    notes = []
    reg = s["gap_regression"]
    # A Brier improvement only counts if it is MEANINGFUL — on ~coinflip
    # main-line totals a sub-0.0015 gap on a few hundred obs is noise, not
    # information. (The un-thresholded test scored a 0.0007 gap as "beats".)
    brier_gain = s["brier"]["kalshi"] - s["brier"]["sharp"]
    brier_better = brier_gain > 0.0015
    b, t = reg.get("b", float("nan")), reg.get("t_b", float("nan"))
    gap_sig = (b == b) and (t == t) and b > 0 and abs(t) > 1.96
    # taker edge must clear zero with a usable sample (>=15 trades), else the
    # "positive" tail is 2-3 lucky fills (see margin sweep in REPORT)
    tk = s["taker"]
    tk_lo = tk["avg_pnl_ci"][0]
    tk_real = tk["n_trades"] >= 15 and tk_lo == tk_lo and tk_lo > 0
    clv_lo, clv_hi = s["clv"]["ci"]
    clv_pos = (clv_lo == clv_lo) and clv_lo > 0
    notes.append(f"Brier sharp {s['brier']['sharp']:.4f} vs Kalshi "
                 f"{s['brier']['kalshi']:.4f} (gain {brier_gain:+.4f}) -> "
                 f"{'sharp meaningfully better' if brier_better else 'tied (no info)'}")
    notes.append(f"gap->outcome coef b={b:+.3f} (t={t:+.2f}, {reg.get('n_clusters',0)} day-clusters) "
                 f"-> {'SIGNIFICANT +' if gap_sig else 'not significant'}")
    notes.append(f"net-of-fee taker: {tk['n_trades']} trades, "
                 f"avg {100*tk['avg_pnl_ct']:+.2f}c/ct "
                 f"CI[{100*tk['avg_pnl_ci'][0]:+.2f},{100*tk['avg_pnl_ci'][1]:+.2f}] "
                 f"-> {'tradeable' if tk_real else 'indistinguishable from 0'}")
    notes.append(f"CLV vs Kalshi close: {100*s['clv']['mean_ct']:+.2f}c/ct "
                 f"CI[{100*clv_lo:+.2f},{100*clv_hi:+.2f}] (n={s['clv']['n']}) "
                 f"-> {'POSITIVE' if clv_pos else 'not positive'}")
    # THE test is the outcome-gap regression: does the sharp line carry info the
    # Kalshi pregame price lacks? CLV without a significant outcome-gap is pure
    # line-chasing (Kalshi drifts toward Pinnacle whether or not Pinnacle is
    # right) and is not taker-monetizable.
    if gap_sig and brier_better and tk_real:
        return "ADVANCE", notes
    if (not gap_sig) and (not brier_better) and (not tk_real):
        return "KILL", notes
    return "MARGINAL", notes


def write_report(s: Dict[str, Any], args) -> None:
    verdict, notes = _verdict(s)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    params = {
        "generated": stamp, "verdict": verdict,
        "lookback_days": args.lookback_days,
        "entry_offset_h": args.entry_offset_h, "margin": args.margin,
        "strike_window": [args.strike_lo, args.strike_hi],
        "summary": s,
    }
    (HERE / "p021_params.json").write_text(json.dumps(params, indent=2))
    reg = s["gap_regression"]; dreg = s.get("drift_regression", {})
    md = f"""# P-021 — MLB Totals vs Sharp-Book Consensus — Phase-1 Gate REPORT
*Generated {stamp}. Cheapest falsification from `backtest_totals.py` on live
Kalshi settled `KXMLBTOTAL` data + Odds API historical Pinnacle-eu / consensus
closing totals. Backtest-first, kill-gated (SPEC_P021 §4).*

## Verdict: **{verdict}**

{chr(10).join('- ' + n for n in notes)}

## Method
For each settled `KXMLBTOTAL` game in the last **{args.lookback_days} days**, the
sharp reference is the **Pinnacle-eu** closing total (Odds API historical
snapshot at first-pitch − 5 min; regions=eu), devigged to P(total > line) with a
multiplicative de-vig; an **ex-DFS multi-book consensus** (mean of per-book
devigged P(over) across the eu board) is kept as a fallback reference. The Kalshi
price is the **pregame** bid/ask **mid at T−{args.entry_offset_h:g}h before first
pitch** (`k_entry_mid`), with the bid/ask used for the taker sim. Comparison is at
the **main line only** — the Pinnacle-posted total matched to the Kalshi strike at
the same number. Realized 0/1 = Kalshi's settled `result`. **All statistics are
clustered by game-day** (same-day games share weather/pitching shocks); the gap
regression uses cluster-robust SEs and PnL/CLV CIs bootstrap whole game-days.

Sample: **{s['n_records']} matched strikes** across **{s['n_games']} games** on
**{s['n_days']} game-days**. Mean Kalshi pregame volume/strike: {s['mean_pregame_vol']:.0f} (fp).

## (i) Calibration — Brier / log-loss vs realized
| reference | Brier | log-loss |
|---|---|---|
| Pinnacle/consensus sharp (`p_sharp`) | {s['brier']['sharp']:.4f} | {s['logloss']['sharp']:.4f} |
| Kalshi pregame mid (`p_kalshi`) | {s['brier']['kalshi']:.4f} | {s['logloss']['kalshi']:.4f} |
| 50/50 baseline | {s['brier']['base_50']:.4f} | — |

Lower is better. If the sharp line carries information Kalshi lacks, `p_sharp`
should have the lower Brier.

## (ii) Gap regression — realized ~ a + b·(p_sharp − p_kalshi)
- **b = {reg.get('b', float('nan')):+.4f}**, cluster-robust SE {reg.get('se_b', float('nan')):.4f},
  **t = {reg.get('t_b', float('nan')):+.2f}**, n={reg.get('n',0)} over {reg.get('n_clusters',0)} day-clusters.
- A positive, significant `b` means: when the sharp fair prob exceeds Kalshi's, the
  contract hits YES more often — the gap has predictive content.

## (ii-b) Does the sharp gap predict Kalshi's OWN drift to close?
- b = {dreg.get('b', float('nan')):+.4f} (t={dreg.get('t_b', float('nan')):+.2f}, {dreg.get('n_clusters',0)} clusters):
  regressing Kalshi's entry→close move on the sharp gap. Positive ⇒ Kalshi
  converges toward the sharp line by first pitch (the CLV mechanism).

## (iii) Net-of-fee taker PnL (flat 1 contract, margin {args.margin:g})
- Take YES at ask when `p_sharp − ask − fee > margin`; else take NO symmetrically.
- **{s['taker']['n_trades']} trades**, total {s['taker']['total_pnl']:+.3f}, avg
  **{100*s['taker']['avg_pnl_ct']:+.2f}¢/ct**, day-clustered 95% CI
  [{100*s['taker']['avg_pnl_ci'][0]:+.2f}, {100*s['taker']['avg_pnl_ci'][1]:+.2f}]¢.
- Fee = taker 0.07·P·(1−P) (KXMLBTOTAL is `quadratic`; maker fee 0, but this is a
  taker sim). Half-spread is implicit (entry at the ask/1−bid).

## (iv) CLV vs Kalshi's own close — the survival criterion (P-001 north star)
- **{100*s['clv']['mean_ct']:+.2f}¢/ct**, day-clustered 95% CI
  [{100*s['clv']['ci'][0]:+.2f}, {100*s['clv']['ci'][1]:+.2f}]¢ (n={s['clv']['n']}).
- Positive ⇒ Kalshi's price moved toward our entry side by first pitch: we bought
  below the closing line, exactly the P-001 edge signature.

## Gate
- **ADVANCE** iff sharp beats Kalshi on Brier **and** the gap coef is positive &
  significant (|t|>1.96, day-clustered) **and** day-clustered CLV lower bound > 0.
- **KILL** if sharp does not beat Kalshi on Brier **and** the gap coef is
  insignificant (the softer player props won't rescue it — do not proceed).
- **MARGINAL** otherwise → widen the sample (more lookback days) before deciding.

## Caveats
- Main-line-only: one strike/game keeps observations independent within the
  clustering but caps sample size; alternate-total strikes (correlated) are a
  Phase-1b extension, not this gate.
- Kalshi `KXMLBTOTAL` settles at game end; the pregame anchor is reconstructed
  from candlesticks at T−{args.entry_offset_h:g}h before the ticker-decoded first
  pitch. Games with no pregame quote at that horizon are dropped (not liquid
  enough to trade), which is the correct conservative treatment.
- Pinnacle "closing" = snapshot at first pitch − 5 min; a handful of games may
  have had the line pulled early or the snapshot mis-aligned (logged as misses).
"""
    (HERE / "REPORT_MLB_Totals_2026-07.md").write_text(md)
    print("\n" + "=" * 72)
    print(f"VERDICT: {verdict}")
    for n in notes:
        print("  " + n)
    print("=" * 72)
    print(f"wrote {HERE/'REPORT_MLB_Totals_2026-07.md'}")
    print(f"wrote {HERE/'p021_params.json'}")


# ── Driver ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback-days", type=int, default=20)
    ap.add_argument("--max-games", type=int, default=None,
                    help="cap games for the Pinnacle pull (smoke test)")
    ap.add_argument("--entry-offset-h", type=float, default=1.0,
                    help="hours before first pitch for the Kalshi entry quote")
    ap.add_argument("--strike-lo", type=float, default=6.5)
    ap.add_argument("--strike-hi", type=float, default=11.5)
    ap.add_argument("--margin", type=float, default=0.0,
                    help="extra net-edge required to take a taker trade")
    ap.add_argument("--rate", type=float, default=0.25)
    ap.add_argument("--score-only", action="store_true")
    args = ap.parse_args()
    KP._min_interval = args.rate

    if args.score_only:
        s = score(args.entry_offset_h, args.margin)
        write_report(s, args)
        return

    rows = pull_settled(args.lookback_days)
    # events (one row per event, carrying title for team parse)
    ev_seen: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        ev_seen.setdefault(r["event"], {"event": r["event"], "title": r["title"]})
    events = list(ev_seen.values())
    print(f"[universe] {len(events)} distinct settled games", flush=True)

    # Pinnacle FIRST so we know each game's posted line(s); then price ONLY the
    # Kalshi strike(s) at those lines — the main-line comparison — instead of a
    # whole strike window (≈1 Kalshi candle call/game instead of ~6).
    pin_rows = pull_pinnacle(events, args.max_games)
    posted: Dict[str, set] = {}
    for p in pin_rows:
        if p.get("ok"):
            posted[p["event"]] = set(p.get("pinnacle", {})) | set(p.get("consensus", {}))
    target = [r for r in rows
              if r["event"] in posted and f"{r['strike']:.1f}" in posted[r["event"]]]
    print(f"[universe] {len(target)} Kalshi strikes at a posted sharp line to price",
          flush=True)
    pull_kalshi(target, args.entry_offset_h, 0.0, 99.0)
    s = score(args.entry_offset_h, args.margin)
    write_report(s, args)


if __name__ == "__main__":
    main()
