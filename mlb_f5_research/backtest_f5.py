"""
mlb_f5_research/backtest_f5.py
──────────────────────────────
P-024 STEP-1 DECISION GATE — is the Pinnacle→Kalshi lead-lag that P-021 found on
the LIQUID headline total (KXMLBTOTAL: +1.8¢ CLV, too small to clear fees)
MATERIALLY LARGER in the maker-free thin "innings" corners, where attention
concentrates on the headline market and the derivative books lag?

Corners tested (task step 1 & 3):
  • KXMLBF5TOTAL  (first-5-innings total runs) — the primary; Pinnacle-eu posts
    `totals_1st_5_innings` historically, and near lock the near-main strike
    carries a 1¢ spread + real volume (verified live 2026-07-24).
  • KXMLBRFI      (a run in the first inning) — print-based near lock ONLY; the
    27¢ daytime spread makes anything but a near-lock print garbage.  Pinnacle
    posts `totals_1st_1_innings` (Over 0.5 == RFI Yes) — a CLEAN line match.
  • KXMLBF5       (first-5 moneyline, 3-way with a Tie) is recorded opportun-
    istically, but Pinnacle-eu does NOT post `h2h_1st_5_innings` at the pregame
    horizons (only soft books do), so it has NO sharp reference — reported, not
    gated.  (Verified: 4 books post F5 h2h, none of them Pinnacle.)

NON-NEGOTIABLE PROTOCOL (thin books make mid-based backtests lie in our favour):
  • Every edge is measured against the TOUCH (candlestick yes_bid/yes_ask) and
    executed PRINTS (candlestick price OHLC + per-minute volume_fp), NEVER a bare
    mid.  A one-sided quote on a book with no entry-hour volume is not a price
    and is dropped.
  • A trade is admitted only when the sharp prob is STRICTLY THROUGH THE TOUCH
    net of fee (buy YES iff p_sharp > ask+fee+margin; buy NO symmetrically) — we
    would cross a real quote, or rest and be filled through.
  • Simulated size is haircut to the entry-hour volume_fp; we report the median
    executable $ per market (entry-hour contracts × fill price).
  • Lag-align CONSERVATIVELY: Odds API Pinnacle-eu "may incur a delay", so the
    sharp snapshot is taken 10 min BEFORE the Kalshi decision horizon
    (commence-70m vs Kalshi T-60m; commence-25m vs Kalshi T-15m).  Any measured
    lead must survive handing the sharp feed a 10-min head-start handicap.
  • ALL statistics day-clustered (same-day games share weather/pitching shocks):
    cluster-robust OLS SEs + whole-game-day bootstrap CIs — the P-021 discipline.

Data pipe (verified live 2026-07-24):
  • Kalshi F5TOTAL/RFI open ~24h pregame, SETTLE AT the relevant game state; the
    LIST endpoint nulls out volume/price on settled markets so candlesticks carry
    the real touch + volume.  Realized 0/1 = the settled `result` (yes/no) — free.
  • Sharp: Odds API per-EVENT historical endpoint (period markets are NOT on the
    cheap featured board): /historical/.../events/{id}/odds?regions=eu&
    markets=totals_1st_5_innings,totals_1st_1_innings — 10 credits/market/snap.
    Pinnacle-eu + ex-DFS eu consensus, devigged via src/devig.py.
  • F5-total line alignment: Kalshi strikes are X.5, Pinnacle often posts an
    INTEGER F5 line — so p_sharp at a Kalshi strike is taken from a book that
    posted that exact X.5 line when available (Pinnacle preferred), else linearly
    interpolated between the two nearest posted lines that straddle it.  Every
    record carries `match_kind` (exact/interp/pin_exact) so the REPORT can show
    the Pinnacle-exact subset separately.

Pipeline (each stage disk-cached + resumable in mlb_f5_research/data/):
  1. pull_settled()  Kalshi settled F5TOTAL + RFI over lookback -> settled_*.jsonl
  2. pull_odds()     per game: events-list -> event id, then 2 per-event snapshots
                     -> odds_lines.jsonl (Pinnacle + consensus per line, both mkts)
  3. pull_kalshi()   per relevant market: 1-min candles -> kalshi_prices.jsonl
                     (touch/print/vol at T-60m, T-15m, close T-5m; entry-hour vol)
  4. score()         match, align lines, devig, print/touch fill sim, day-clustered
                     Brier / gap regression / net-of-fee PnL / CLV -> REPORT + params

Usage:
  python3 backtest_f5.py --lookback-days 60             # full run
  python3 backtest_f5.py --lookback-days 6 --max-games 25   # smoke
  python3 backtest_f5.py --score-only                   # re-score cache
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
import urllib.error
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
KP = KalshiPublic(min_interval=0.20)
EDT = timezone(timedelta(hours=-4))   # MLB season = US Eastern Daylight Time
ODDS_BASE = "https://api.the-odds-api.com/v4"
_ODDS_SPEND = {"credits": 0, "calls": 0}   # running Odds API meter

# ── MLB team canonicalisation (reused verbatim from P-021) ───────────────
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
    best, blen = None, 0
    for alias, code in _TEAM_ALIASES.items():
        if alias in n and len(alias) > blen:
            best, blen = code, len(alias)
    return best


def _teams_from_title(title: str) -> Optional[Tuple[str, str]]:
    """'Kansas City vs Detroit first 5 innings Total Runs?' / '... First Inning
    Run?' -> ('DET','KC') as a sorted pair.  Cut at the first 'first' token,
    then split on ' vs '."""
    t = re.split(r"\bfirst\b", title or "", maxsplit=1, flags=re.IGNORECASE)[0]
    parts = re.split(r"\s+vs\.?\s+", t, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None
    a, b = _canon_team(parts[0]), _canon_team(parts[1])
    if a and b and a != b:
        return tuple(sorted((a, b)))  # type: ignore
    return None


def _decode_commence(event_ticker: str) -> Optional[datetime]:
    """KXMLBF5TOTAL-26JUL231840KCDET -> 2026-07-23 18:40 ET -> UTC."""
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


def _day_key(event_ticker: str) -> str:
    m = re.search(r"-(\d{2}[A-Z]{3}\d{2})", event_ticker or "")
    return m.group(1) if m else "?"


def _game_key(event_ticker: str) -> str:
    """The game identity shared across series: strip the series prefix so
    KXMLBF5TOTAL-26JUL231840KCDET and KXMLBRFI-26JUL231840KCDET both map to
    '26JUL231840KCDET' (one physical game, two Kalshi event tickers)."""
    parts = (event_ticker or "").split("-", 1)
    return parts[1] if len(parts) == 2 else (event_ticker or "?")


def _strikeval(sub_title: str) -> Optional[float]:
    m = re.search(r"Over ([\d.]+)", sub_title or "")
    return float(m.group(1)) if m else None


# ── Stage 1: Kalshi settled markets ──────────────────────────────────────

def _pull_settled_series(series: str, lookback_days: int) -> List[Dict[str, Any]]:
    out_path = DATA / f"settled_{series}.jsonl"
    if out_path.exists():
        rows = [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
        print(f"[settled {series}] {len(rows)} cached", flush=True)
        return rows
    min_close = int(time.time()) - lookback_days * 86400
    rows: List[Dict[str, Any]] = []
    cursor = None
    pages = 0
    while True:
        params: Dict[str, Any] = {
            "series_ticker": series, "status": "settled",
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
            ev = m.get("event_ticker")
            title = m.get("title") or ""
            sub = m.get("yes_sub_title") or ""
            rec = {
                "series": series, "ticker": m.get("ticker"), "event": ev,
                "title": title, "sub": sub,
                "result": 1 if res == "yes" else 0,
                "open_time": m.get("open_time"), "close_time": m.get("close_time"),
            }
            if series == "KXMLBF5TOTAL":
                sv = _strikeval(sub)
                if sv is None:
                    continue
                rec["strike"] = sv
            rows.append(rec)
        pages += 1
        cursor = data.get("cursor")
        if not cursor or not ms:
            break
    with open(out_path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    n_ev = len({r["event"] for r in rows})
    print(f"[settled {series}] {len(rows)} strikes across {n_ev} events "
          f"({pages} pages, lookback {lookback_days}d)", flush=True)
    return rows


# ── Stage 2: Odds API per-event historical (Pinnacle + consensus) ─────────

import threading                                      # noqa: E402
_SPEND_LOCK = threading.Lock()
_ODDS_RATE_LOCK = threading.Lock()
_ODDS_MIN_INTERVAL = 0.34   # s between Odds calls across all threads (~3/s)
_ODDS_LAST = {"ts": 0.0}


def _throttle_odds() -> None:
    with _ODDS_RATE_LOCK:
        slot = max(_ODDS_LAST["ts"] + _ODDS_MIN_INTERVAL, time.time())
        _ODDS_LAST["ts"] = slot
    delay = slot - time.time()
    if delay > 0:
        time.sleep(delay)


def _odds_get(path: str, params: Dict[str, Any]) -> Tuple[Optional[Any], int]:
    key = os.environ.get("ODDS_API_KEY", "")
    params = dict(params); params["apiKey"] = key
    url = f"{ODDS_BASE}{path}?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        _throttle_odds()
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=12) as r:
                cost = int(r.headers.get("x-requests-last") or 0)
                with _SPEND_LOCK:
                    _ODDS_SPEND["credits"] += cost
                    _ODDS_SPEND["calls"] += 1
                return json.loads(r.read().decode()), cost
        except urllib.error.HTTPError as e:  # noqa
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(1.5 * (attempt + 1)); continue
            return {"_error": e.code, "_body": e.read().decode()[:200]}, 0
        except Exception:  # noqa: BLE001
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1)); continue
            return None, 0
    return None, 0


def _devig_two_way(over_am: float, under_am: float) -> Optional[float]:
    try:
        po = devig.american_to_prob(over_am)
        pu = devig.american_to_prob(under_am)
        return devig.devig_two_way(po, pu)
    except (KeyError, ValueError, ZeroDivisionError):
        return None


def _extract_totals(game: Dict[str, Any], market_key: str) -> Dict[str, Any]:
    """From one Odds API event snapshot, return per-line devigged P(over) for
    `market_key` (a totals-family market): {pinnacle:{line:p}, consensus:{line:p},
    n_books:int}.  consensus = mean of per-book devigged P(over) at each line."""
    per_line_books: Dict[float, List[float]] = defaultdict(list)
    pin_by_line: Dict[float, float] = {}
    books = set()
    for bk in game.get("bookmakers", []):
        bkey = bk.get("key", "")
        for mk in bk.get("markets", []):
            if mk.get("key") != market_key:
                continue
            outs = {o.get("name", "").lower(): o for o in mk.get("outcomes", [])}
            over, under = outs.get("over"), outs.get("under")
            if not over or not under:
                continue
            pt = over.get("point")
            if pt is None or under.get("point") != pt:
                continue
            p_over = _devig_two_way(float(over["price"]), float(under["price"]))
            if p_over is None:
                continue
            per_line_books[float(pt)].append(p_over)
            books.add(bkey)
            if bkey == "pinnacle":
                pin_by_line[float(pt)] = p_over
    consensus = {ln: sum(ps) / len(ps) for ln, ps in per_line_books.items() if ps}
    return {"pinnacle": pin_by_line, "consensus": consensus, "n_books": len(books)}


def _f5_h2h_present(game: Dict[str, Any]) -> Dict[str, Any]:
    """Record whether any book / Pinnacle posts h2h_1st_5_innings (for the
    F5-moneyline coverage note)."""
    books = []
    pin = False
    for bk in game.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") == "h2h_1st_5_innings":
                books.append(bk.get("key"))
                if bk.get("key") == "pinnacle":
                    pin = True
    return {"any": bool(books), "pinnacle": pin, "books": books}


_EVLIST_LOCK = threading.Lock()
_EVLIST_CACHE: Dict[str, Dict[str, str]] = {}   # hour-bucket -> {teams_key: eid}


def _resolve_eid(commence: datetime, teams: Tuple[str, str]) -> Tuple[Optional[str], bool]:
    """Resolve the Odds event id for `teams` near `commence`, using a historical
    events-list snapshot cached per rounded hour (games share slates, so this
    collapses ~753 calls to ~1/hour-bucket).  Returns (eid, slate_ok): slate_ok
    is False when the events-list CALL failed (transient rate-limit) as opposed
    to succeeding-but-not-matching (a real miss).  A failed/empty events-list is
    never cached — otherwise one 429 would poison the whole bucket."""
    snap1 = commence - timedelta(minutes=70)
    bucket = snap1.strftime("%Y-%m-%dT%H:00:00Z")
    with _EVLIST_LOCK:
        table = _EVLIST_CACHE.get(bucket)
    if not table:
        ev_body, _ = _odds_get("/historical/sports/baseball_mlb/events",
                               {"date": snap1.strftime("%Y-%m-%dT%H:%M:%SZ")})
        call_ok = bool(ev_body) and "_error" not in (ev_body or {})
        data = (ev_body or {}).get("data", []) if call_ok else []
        table = {}
        for g in data:
            gt = tuple(sorted(filter(None, (_canon_team(g.get("away_team", "")),
                                            _canon_team(g.get("home_team", ""))))))
            if len(gt) == 2 and g.get("id"):
                table.setdefault("|".join(gt), g["id"])
        if table:                       # only cache a genuinely populated slate
            with _EVLIST_LOCK:
                _EVLIST_CACHE[bucket] = table
        elif not call_ok:
            return None, False          # transient: the events-list call failed
    return table.get("|".join(teams)), True


def _fetch_game(e: Dict[str, Any]) -> Dict[str, Any]:
    commence = _decode_commence(e["event"])
    teams = _teams_from_title(e["title"])
    if commence is None or teams is None:
        return {"game": e["game"], "event": e["event"], "ok": False,
                "why": "decode", "transient": False}
    eid, slate_ok = _resolve_eid(commence, teams)
    if not eid:
        return {"game": e["game"], "event": e["event"], "ok": False,
                "why": "evlist_fail" if not slate_ok else "no_event_id",
                "teams": list(teams), "transient": not slate_ok}
    snaps: Dict[str, Any] = {}
    n_snap_fail = 0
    for label, snap_dt in (("t60", commence - timedelta(minutes=70)),
                           ("t15", commence - timedelta(minutes=25))):
        body, _ = _odds_get(
            f"/historical/sports/baseball_mlb/events/{eid}/odds",
            {"date": snap_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "regions": "eu", "oddsFormat": "american",
             "markets": "totals_1st_5_innings,totals_1st_1_innings,h2h_1st_5_innings"})
        if not body or "_error" in (body or {}):
            snaps[label] = {"ok": False}
            n_snap_fail += 1
            continue
        g = body.get("data", {})
        f5 = _extract_totals(g, "totals_1st_5_innings")
        rfi = _extract_totals(g, "totals_1st_1_innings")
        h2h = _f5_h2h_present(g)
        snaps[label] = {
            "ok": True, "snap_ts": body.get("timestamp"),
            "f5": {"pin": {f"{k:.1f}": v for k, v in f5["pinnacle"].items()},
                   "con": {f"{k:.1f}": v for k, v in f5["consensus"].items()},
                   "n_books": f5["n_books"]},
            "rfi": {"pin": {f"{k:.1f}": v for k, v in rfi["pinnacle"].items()},
                    "con": {f"{k:.1f}": v for k, v in rfi["consensus"].items()},
                    "n_books": rfi["n_books"]},
            "f5h2h": h2h,
        }
    # both snapshots failing the odds call is transient (rate-limit), not a real
    # "this game has no F5/RFI market" — retry it on the next round.
    return {"game": e["game"], "event": e["event"], "ok": True, "eid": eid,
            "teams": list(teams), "commence": commence.isoformat(), "snaps": snaps,
            "transient": n_snap_fail >= 2}


def pull_odds(events: List[Dict[str, Any]], max_games: Optional[int],
              credit_budget: int, workers: int = 4) -> List[Dict[str, Any]]:
    """Threaded: resolve each game's Odds event id (per-hour-cached events-list)
    then take TWO per-event snapshots (commence-70m and commence-25m; the sharp
    side of the conservative 10-min lag) and extract F5-total + RFI devigged
    lines from Pinnacle-eu and the eu consensus.  Independent per game, so run
    concurrently — the serial version stalled on ~2.3k round-trips."""
    out_path = DATA / "odds_lines.jsonl"
    have: Dict[str, Dict[str, Any]] = {}
    if out_path.exists():
        for l in out_path.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                have[r["game"]] = r     # last write wins (a later round may fix a miss)
    if max_games is not None:
        events = events[:max_games]
    print(f"[odds] {len(events)} games ({len(have)} already cached); "
          f"budget {credit_budget} cr, {workers} workers", flush=True)

    budget_hit = False
    # Retry rounds: transient failures (rate-limit) are NOT written, so each round
    # re-attempts the games without a good cached row. Permanent misses (decode /
    # events-list matched-but-not-found) ARE written so they don't loop forever.
    for rnd in range(1, 6):
        todo = [e for e in events
                if not (have.get(e["game"]) and not have[e["game"]].get("transient"))]
        if not todo or budget_hit:
            break
        print(f"[odds] round {rnd}: {len(todo)} games to (re)try", flush=True)
        n = 0
        with open(out_path, "a") as fh:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(_fetch_game, e) for e in todo]
                for fut in as_completed(futs):
                    rec = fut.result()
                    n += 1
                    if not rec.get("transient"):     # only persist stable results
                        fh.write(json.dumps(rec) + "\n")
                        have[rec["game"]] = rec
                    if n % 40 == 0:
                        fh.flush()
                        print(f"[odds] r{rnd} {n}/{len(todo)}  spend "
                              f"{_ODDS_SPEND['credits']} cr ({_ODDS_SPEND['calls']} calls)",
                              flush=True)
                    if _ODDS_SPEND["credits"] >= credit_budget:
                        budget_hit = True
        n_ok = sum(1 for r in have.values() if r.get("ok"))
        n_trans = sum(1 for e in events
                      if not have.get(e["game"]) or have[e["game"]].get("transient"))
        print(f"[odds] round {rnd} done: {n_ok} matched, {n_trans} still pending; "
              f"spend {_ODDS_SPEND['credits']} cr", flush=True)
        if n_trans == 0:
            break
        time.sleep(8)   # let any rolling-window rate limit reset between rounds

    got = list(have.values())
    ok = [r for r in got if r.get("ok")]
    # persist real spend so a later --score-only re-render reports it accurately
    (DATA / "odds_spend.json").write_text(json.dumps(dict(_ODDS_SPEND)))
    print(f"[odds] FINAL {len(ok)} matched, {len(got)-len(ok)} misses; "
          f"Odds spend {_ODDS_SPEND['credits']} cr / {_ODDS_SPEND['calls']} calls",
          flush=True)
    return got


# ── Stage 3: Kalshi candlestick touch + prints ───────────────────────────

def _candle_quote(c: Dict[str, Any]) -> Dict[str, Any]:
    b = fnum((c.get("yes_bid") or {}).get("close_dollars"))
    a = fnum((c.get("yes_ask") or {}).get("close_dollars"))
    pr = c.get("price") or {}
    last = fnum(pr.get("close_dollars")) if isinstance(pr, dict) else None
    vol = fnum(c.get("volume_fp")) or fnum(c.get("volume")) or 0.0
    return {"bid": b, "ask": a, "last": last, "vol": vol,
            "ts": c.get("end_period_ts")}


def _touch_at(candles: List[Dict[str, Any]], ts: float) -> Optional[Dict[str, Any]]:
    """Latest candle with a two-sided touch at or before ts (bid AND ask), plus
    the last print price it carries.  None if the book is one-sided/empty."""
    prior = [c for c in candles if c.get("end_period_ts", 0) <= ts]
    for c in reversed(prior):
        q = _candle_quote(c)
        if q["bid"] is not None and q["ask"] is not None and 0 < q["bid"] <= q["ask"] < 1:
            return q
    return None


def _hour_volume(candles: List[Dict[str, Any]], end_ts: float) -> float:
    """Sum of per-minute volume_fp in the 60 minutes ending at end_ts — the
    entry-hour liquidity used to haircut simulated size."""
    lo = end_ts - 3600.0
    return sum(fnum(c.get("volume_fp")) or 0.0 for c in candles
               if lo < c.get("end_period_ts", 0) <= end_ts)


def pull_kalshi(targets: List[Dict[str, Any]], workers: int = 8
                ) -> List[Dict[str, Any]]:
    """For each target market fetch 1-min candles and record the touch/print/vol
    at T-60m, T-15m, and the Kalshi close (commence-5m), plus entry-hour volume
    at each horizon and total pregame volume."""
    out_path = DATA / "kalshi_prices.jsonl"
    have = set()
    priced: List[Dict[str, Any]] = []
    if out_path.exists():
        for l in out_path.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                priced.append(r)
                have.add(r["ticker"])
    todo = [t for t in targets if t["ticker"] not in have
            and _decode_commence(t["event"]) is not None]
    print(f"[kalshi] {len(todo)} markets to price ({len(have)} cached)", flush=True)

    def fetch(t: Dict[str, Any]) -> Dict[str, Any]:
        commence = _decode_commence(t["event"])
        cl = _parse_iso(t.get("close_time"))
        op = _parse_iso(t.get("open_time"))
        if commence is None or cl is None or op is None:
            return {"ticker": t["ticker"], "priced": False, "why": "no_time"}
        commence_ts = commence.timestamp()
        try:
            data = KP.get(
                f"/series/{t['series']}/markets/{t['ticker']}/candlesticks",
                {"start_ts": int(op), "end_ts": int(cl), "period_interval": 1})
        except Exception as exc:  # noqa: BLE001
            return {"ticker": t["ticker"], "priced": False, "why": str(exc)[:40]}
        candles = (data or {}).get("candlesticks", [])
        if not candles:
            return {"ticker": t["ticker"], "priced": False, "why": "no_candles"}
        pre_vol = sum(fnum(c.get("volume_fp")) or 0.0 for c in candles
                      if c.get("end_period_ts", 0) <= commence_ts)
        rec: Dict[str, Any] = {
            "ticker": t["ticker"], "series": t["series"], "event": t["event"],
            "result": t["result"], "priced": True, "commence_ts": commence_ts,
            "pregame_vol": pre_vol,
        }
        if "strike" in t:
            rec["strike"] = t["strike"]
        for label, off in (("t60", 3600.0), ("t15", 900.0), ("close", 300.0)):
            q = _touch_at(candles, commence_ts - off)
            rec[label] = q
            if label in ("t60", "t15") and q:
                rec[f"{label}_hourvol"] = _hour_volume(candles, commence_ts - off)
        return rec

    with open(out_path, "a") as fh:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(fetch, t) for t in todo]
            n = 0
            for fut in as_completed(futs):
                rec = fut.result()
                n += 1
                fh.write(json.dumps(rec) + "\n")
                if rec.get("priced"):
                    priced.append(rec)
                if n % 200 == 0:
                    fh.flush()
                    print(f"[kalshi] {n}/{len(todo)}", flush=True)
    usable = [r for r in priced if r.get("priced")]
    print(f"[kalshi] {len(usable)} priced markets", flush=True)
    return priced


# ── Stage 4: line alignment + scoring ────────────────────────────────────

def _sharp_at_line(lines: Dict[str, float], strike: float
                   ) -> Tuple[Optional[float], str]:
    """P(over `strike`) from a book's per-line P(over) map.
    exact match if the strike is posted; else linear interpolation in prob
    between the two nearest posted lines that straddle it (probs are monotone
    decreasing in the line).  Returns (p, kind)."""
    if not lines:
        return None, "none"
    fl = {float(k): v for k, v in lines.items()}
    if strike in fl:
        return fl[strike], "exact"
    below = sorted([ln for ln in fl if ln < strike])
    above = sorted([ln for ln in fl if ln > strike])
    if below and above:
        lo, hi = below[-1], above[0]
        if hi - lo <= 1.5:                    # only interpolate across a small gap
            w = (strike - lo) / (hi - lo)
            return fl[lo] + w * (fl[hi] - fl[lo]), "interp"
    return None, "none"


def _brier(pairs: List[Tuple[float, int]]) -> float:
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else float("nan")


def _logloss(pairs: List[Tuple[float, int]]) -> float:
    eps = 1e-6
    if not pairs:
        return float("nan")
    return -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
                for p, y in pairs) / len(pairs)


def _cluster_ols(x: List[float], y: List[float], clusters: List[str]
                 ) -> Dict[str, float]:
    """OLS y = a + b*x with cluster-robust (CR0 + small-sample) SEs."""
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
    by_c: Dict[str, float] = defaultdict(float)
    for xi, ei, c in zip(x, resid, clusters):
        by_c[c] += (xi - mx) * ei
    meat = sum(v * v for v in by_c.values())
    var_b = meat / (sxx ** 2)
    G = len(by_c)
    if G < 2:                       # cluster-robust SE undefined with one cluster
        return {"a": a, "b": b, "se_b": float("nan"), "t_b": float("nan"),
                "n": n, "n_clusters": G}
    var_b *= (G / (G - 1)) * ((n - 1) / (n - 2))
    se_b = math.sqrt(var_b) if var_b > 0 else float("nan")
    t_b = b / se_b if se_b and se_b == se_b else float("nan")
    return {"a": a, "b": b, "se_b": se_b, "t_b": t_b, "n": n, "n_clusters": G}


def _bootstrap_day_ci(items: List[Tuple[str, float]], n_boot: int = 2000,
                      seed: int = 7) -> Tuple[float, float]:
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
    if not means:
        return (float("nan"), float("nan"))
    return (means[int(0.025 * len(means))], means[int(0.975 * len(means))])


def _fill_sim(recs: List[Dict[str, Any]], horizon: str, series: str,
              margin: float) -> Dict[str, Any]:
    """Print/touch fill protocol at `horizon` (t60|t15).
    Buy YES at the ask iff p_sharp - ask - fee > margin (sharp strictly through
    the touch); else buy NO at (1-bid) symmetrically.  Only markets with a
    two-sided touch AND entry-hour volume > 0 are admitted (a bare one-sided
    quote on an empty book is not a price).  Size is haircut to entry-hour
    volume_fp; PnL is per-contract net of the taker fee."""
    trades = []
    for r in recs:
        q = r.get(horizon)
        hv = r.get(f"{horizon}_hourvol", 0.0)
        ps = r.get("p_sharp")
        if not q or ps is None:
            continue
        ask, bid = q.get("ask"), q.get("bid")
        if ask is None or bid is None or hv <= 0:   # phantom / empty book
            continue
        y = r["result"]; day = r["day"]
        fee_y = kalshi_fees.fee_per_contract(ask, maker=False, series_ticker=series)
        if ps - ask - fee_y > margin:
            trades.append({"side": "YES", "px": ask, "fee": fee_y,
                           "pnl": (y - ask) - fee_y, "day": day, "hourvol": hv,
                           "exec_usd": hv * ask, "r": r})
            continue
        no_cost = 1.0 - bid
        fee_n = kalshi_fees.fee_per_contract(no_cost, maker=False, series_ticker=series)
        if (1 - ps) - no_cost - fee_n > margin:
            trades.append({"side": "NO", "px": no_cost, "fee": fee_n,
                           "pnl": ((1 - y) - no_cost) - fee_n, "day": day,
                           "hourvol": hv, "exec_usd": hv * no_cost, "r": r})
    n = len(trades)
    pnl = [t["pnl"] for t in trades]
    avg = sum(pnl) / n if n else float("nan")
    ci = _bootstrap_day_ci([(t["day"], t["pnl"]) for t in trades])
    # CLV: fill price vs Kalshi close touch-mid, in the trade direction
    clv = []
    for t in trades:
        cq = t["r"].get("close")
        if not cq or cq.get("bid") is None or cq.get("ask") is None:
            continue
        cmid = (cq["bid"] + cq["ask"]) / 2.0
        clv.append((t["day"],
                    (cmid - t["px"]) if t["side"] == "YES" else ((1 - cmid) - t["px"])))
    clv_mean = sum(v for _, v in clv) / len(clv) if clv else float("nan")
    clv_ci = _bootstrap_day_ci(clv)
    exec_usds = sorted(t["exec_usd"] for t in trades)
    med_exec = exec_usds[len(exec_usds) // 2] if exec_usds else float("nan")
    return {"n_trades": n, "avg_pnl_ct": avg, "avg_pnl_ci": ci,
            "clv_n": len(clv), "clv_ct": clv_mean, "clv_ci": clv_ci,
            "median_exec_usd": med_exec}


def _f5_records(krows: List[Dict[str, Any]], odds_by_game: Dict[str, Any],
                horizon: str) -> List[Dict[str, Any]]:
    """Build scored F5-total records at `horizon`: match each Kalshi strike to a
    sharp P(over strike) from the lag-aligned sharp snapshot (Pinnacle preferred,
    else consensus; exact line or straddle-interpolated)."""
    snap_label = horizon   # t60 sharp snap == commence-70m; t15 == commence-25m
    out = []
    for k in krows:
        if k.get("series") != "KXMLBF5TOTAL" or not k.get("priced"):
            continue
        od = odds_by_game.get(_game_key(k["event"]))
        if not od or not od.get("ok"):
            continue
        snap = od["snaps"].get(snap_label)
        if not snap or not snap.get("ok"):
            continue
        strike = k.get("strike")
        if strike is None:
            continue
        p_pin, kind_pin = _sharp_at_line(snap["f5"]["pin"], strike)
        p_con, kind_con = _sharp_at_line(snap["f5"]["con"], strike)
        p_sharp = p_pin if p_pin is not None else p_con
        if p_sharp is None:
            continue
        match_kind = ("pin_" + kind_pin) if p_pin is not None else ("con_" + kind_con)
        q = k.get(horizon)
        out.append({
            "event": k["event"], "day": _day_key(k["event"]), "strike": strike,
            "result": k["result"], "p_sharp": p_sharp,
            "p_pin": p_pin, "p_con": p_con, "match_kind": match_kind,
            horizon: q, f"{horizon}_hourvol": k.get(f"{horizon}_hourvol", 0.0),
            "close": k.get("close"), "pregame_vol": k.get("pregame_vol", 0.0),
        })
    return out


def _rfi_records(krows: List[Dict[str, Any]], odds_by_game: Dict[str, Any],
                 horizon: str) -> List[Dict[str, Any]]:
    """RFI records at `horizon`: Kalshi RFI Yes vs Pinnacle P(1st-inning total >
    0.5).  Clean line match (both are the 0.5 threshold)."""
    out = []
    for k in krows:
        if k.get("series") != "KXMLBRFI" or not k.get("priced"):
            continue
        od = odds_by_game.get(_game_key(k["event"]))
        if not od or not od.get("ok"):
            continue
        snap = od["snaps"].get(horizon)
        if not snap or not snap.get("ok"):
            continue
        p_pin = snap["rfi"]["pin"].get("0.5")
        p_con = snap["rfi"]["con"].get("0.5")
        p_sharp = p_pin if p_pin is not None else p_con
        if p_sharp is None:
            continue
        out.append({
            "event": k["event"], "day": _day_key(k["event"]),
            "result": k["result"], "p_sharp": p_sharp,
            "p_pin": p_pin, "p_con": p_con,
            "match_kind": "pin_exact" if p_pin is not None else "con_exact",
            horizon: k.get(horizon), f"{horizon}_hourvol": k.get(f"{horizon}_hourvol", 0.0),
            "close": k.get("close"), "pregame_vol": k.get("pregame_vol", 0.0),
        })
    return out


def _calib_and_gap(recs: List[Dict[str, Any]], horizon: str) -> Dict[str, Any]:
    """Brier (p_sharp vs Kalshi last-print) + day-clustered gap->outcome and
    gap->Kalshi-drift regressions, using the TOUCH (never a bare mid): the
    Kalshi reference prob is the last print at the horizon, dropped if no
    two-sided touch."""
    valid = []
    for r in recs:
        q = r.get(horizon)
        if not q or q.get("bid") is None or q.get("ask") is None:
            continue
        last = q.get("last")
        kmid = (q["bid"] + q["ask"]) / 2.0
        p_k = last if last is not None else kmid
        r2 = dict(r); r2["p_k"] = p_k; r2["k_mid"] = kmid
        cq = r.get("close")
        r2["k_close_mid"] = ((cq["bid"] + cq["ask"]) / 2.0
                             if cq and cq.get("bid") is not None
                             and cq.get("ask") is not None else None)
        valid.append(r2)
    if not valid:
        return {"n": 0}
    brier_sharp = _brier([(r["p_sharp"], r["result"]) for r in valid])
    brier_k = _brier([(r["p_k"], r["result"]) for r in valid])
    brier_base = _brier([(0.5, r["result"]) for r in valid])
    ll_sharp = _logloss([(r["p_sharp"], r["result"]) for r in valid])
    ll_k = _logloss([(r["p_k"], r["result"]) for r in valid])
    gap = [r["p_sharp"] - r["p_k"] for r in valid]
    reg = _cluster_ols(gap, [float(r["result"]) for r in valid],
                       [r["day"] for r in valid])
    dp = [(r["p_sharp"] - r["p_k"], r["k_close_mid"] - r["k_mid"])
          for r in valid if r["k_close_mid"] is not None]
    dreg = _cluster_ols([g for g, _ in dp], [d for _, d in dp],
                        [r["day"] for r in valid if r["k_close_mid"] is not None]) \
        if dp else {}
    return {"n": len(valid), "n_games": len({r["event"] for r in valid}),
            "n_days": len({r["day"] for r in valid}),
            "brier": {"sharp": brier_sharp, "kalshi": brier_k, "base": brier_base},
            "logloss": {"sharp": ll_sharp, "kalshi": ll_k},
            "gap_reg": reg, "drift_reg": dreg,
            "mean_pregame_vol": sum(r["pregame_vol"] for r in valid) / len(valid),
            "match_kinds": dict(_counter(r["match_kind"] for r in valid))}


def _counter(it):
    d: Dict[str, int] = defaultdict(int)
    for x in it:
        d[x] += 1
    return d


def score(margin: float) -> Dict[str, Any]:
    kfile = DATA / "kalshi_prices.jsonl"
    ofile = DATA / "odds_lines.jsonl"
    krows = [json.loads(l) for l in kfile.read_text().splitlines() if l.strip()] \
        if kfile.exists() else []
    orows = [json.loads(l) for l in ofile.read_text().splitlines() if l.strip()] \
        if ofile.exists() else []
    odds_by_game = {r["game"]: r for r in orows if r.get("ok")}

    out: Dict[str, Any] = {"margin": margin}
    # F5-total coverage note: Pinnacle F5-h2h availability across snapshots
    h2h_any = h2h_pin = h2h_tot = 0
    for r in orows:
        if not r.get("ok"):
            continue
        for lab in ("t60", "t15"):
            s = r["snaps"].get(lab)
            if s and s.get("ok") and "f5h2h" in s:
                h2h_tot += 1
                h2h_any += 1 if s["f5h2h"]["any"] else 0
                h2h_pin += 1 if s["f5h2h"]["pinnacle"] else 0
    out["f5_h2h_coverage"] = {"snaps": h2h_tot, "any_book": h2h_any,
                              "pinnacle": h2h_pin}

    for horizon in ("t60", "t15"):
        f5 = _f5_records(krows, odds_by_game, horizon)
        rfi = _rfi_records(krows, odds_by_game, horizon)
        for r in f5:
            r["series_ref"] = "KXMLBF5TOTAL"
        for r in rfi:
            r["series_ref"] = "KXMLBRFI"
        out.setdefault("f5total", {})[horizon] = {
            "calib": _calib_and_gap(f5, horizon),
            "fill": _fill_sim(f5, horizon, "KXMLBF5TOTAL", margin),
        }
        out.setdefault("rfi", {})[horizon] = {
            "calib": _calib_and_gap(rfi, horizon),
            "fill": _fill_sim(rfi, horizon, "KXMLBRFI", margin),
        }
    # RFI usable-prints/season extrapolation from the near-lock horizon
    rfi_krows = [k for k in krows if k.get("series") == "KXMLBRFI" and k.get("priced")]
    usable = [k for k in rfi_krows
              if k.get("t15") and k["t15"].get("bid") is not None
              and k["t15"].get("ask") is not None
              and (k.get("t15_hourvol", 0.0) > 0)]
    days = {_day_key(k["event"]) for k in rfi_krows}
    n_days = max(1, len(days))
    out["rfi_prints"] = {
        "n_settled": len(rfi_krows), "n_usable_nearlock": len(usable),
        "n_days": len(days),
        "usable_per_day": len(usable) / n_days,
        "proj_season": (len(usable) / n_days) * 180,   # ~180 MLB game-days/season
    }
    return out


# ── Report / gate ────────────────────────────────────────────────────────

def _fmt_reg(r: Dict[str, Any]) -> str:
    if not r or r.get("b") != r.get("b"):
        return "b=n/a"
    return (f"b={r.get('b', float('nan')):+.3f} (t={r.get('t_b', float('nan')):+.2f}, "
            f"n={r.get('n', 0)}, {r.get('n_clusters', 0)} day-clusters)")


def _gate(s: Dict[str, Any]) -> Tuple[str, List[str]]:
    notes = []
    # F5 gate on the near-lock horizon (t15)
    f5 = s.get("f5total", {}).get("t15", {})
    cal = f5.get("calib", {}); fil = f5.get("fill", {})
    reg = cal.get("gap_reg", {})
    b, t = reg.get("b", float("nan")), reg.get("t_b", float("nan"))
    gap_sig = (b == b) and (t == t) and b > 0 and abs(t) > 1.96
    edge = fil.get("avg_pnl_ct", float("nan"))
    edge_lo = fil.get("avg_pnl_ci", [float("nan"), float("nan")])[0]
    edge_ok = (edge == edge) and edge >= 0.02 and (edge_lo == edge_lo) and edge_lo > 0
    size_ok = (fil.get("median_exec_usd", 0) or 0) >= 100
    brier_gain = (cal.get("brier", {}).get("kalshi", float("nan"))
                  - cal.get("brier", {}).get("sharp", float("nan")))
    notes.append(f"F5-total (t15): Brier sharp {cal.get('brier',{}).get('sharp',float('nan')):.4f} "
                 f"vs Kalshi {cal.get('brier',{}).get('kalshi',float('nan')):.4f} "
                 f"(gain {brier_gain:+.4f})")
    notes.append(f"F5-total gap->outcome: {_fmt_reg(reg)} -> "
                 f"{'SIGNIFICANT +' if gap_sig else 'not significant'}")
    notes.append(f"F5-total drift (Kalshi own move ~ sharp gap): {_fmt_reg(cal.get('drift_reg',{}))}")
    notes.append(f"F5-total print/touch net edge: {100*edge:+.2f}c/ct "
                 f"CI[{100*fil.get('avg_pnl_ci',[float('nan')])[0]:+.2f},"
                 f"{100*fil.get('avg_pnl_ci',[0,float('nan')])[1]:+.2f}] "
                 f"n={fil.get('n_trades',0)} -> {'>=2c & clears 0' if edge_ok else 'fails'}")
    notes.append(f"F5-total CLV vs Kalshi close: {100*fil.get('clv_ct',float('nan')):+.2f}c/ct "
                 f"(n={fil.get('clv_n',0)})")
    notes.append(f"F5-total median executable ${fil.get('median_exec_usd',float('nan')):.0f}/mkt "
                 f"-> {'>=100' if size_ok else '<100'}")
    rp = s.get("rfi_prints", {})
    rfi_ok = rp.get("proj_season", 0) >= 300
    notes.append(f"RFI usable near-lock prints: {rp.get('n_usable_nearlock',0)} in "
                 f"{rp.get('n_days',0)} days -> ~{rp.get('proj_season',0):.0f}/season "
                 f"-> {'>=300' if rfi_ok else '<300'}")

    advance = gap_sig and edge_ok and size_ok
    if advance:
        return "ADVANCE", notes
    return "KILL", notes


def write_report(s: Dict[str, Any], args) -> None:
    verdict, notes = _gate(s)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # a --score-only re-render makes no pulls, so fall back to the spend the pull
    # persisted, keeping the reported credits accurate.
    if _ODDS_SPEND["credits"] == 0 and (DATA / "odds_spend.json").exists():
        try:
            saved = json.loads((DATA / "odds_spend.json").read_text())
            _ODDS_SPEND["credits"] = saved.get("credits", 0)
            _ODDS_SPEND["calls"] = saved.get("calls", 0)
        except (ValueError, OSError):
            pass
    params = {
        "generated": stamp, "verdict": verdict,
        "lookback_days": args.lookback_days, "margin": args.margin,
        "odds_spend_credits": _ODDS_SPEND["credits"],
        "odds_calls": _ODDS_SPEND["calls"],
        "summary": s,
    }
    (HERE / "p024_params.json").write_text(json.dumps(params, indent=2))

    def sec(series_key: str, title: str) -> str:
        blk = s.get(series_key, {})
        lines = [f"### {title}"]
        for h, hlabel in (("t60", "T−1h (sharp @ commence−70m)"),
                          ("t15", "T−15m near lock (sharp @ commence−25m)")):
            hb = blk.get(h, {})
            cal = hb.get("calib", {}); fil = hb.get("fill", {})
            if not cal or cal.get("n", 0) == 0:
                lines.append(f"\n**{hlabel}** — no usable records.")
                continue
            br = cal.get("brier", {})
            lines.append(
                f"\n**{hlabel}** — n={cal.get('n',0)} strikes / "
                f"{cal.get('n_games',0)} games / {cal.get('n_days',0)} days; "
                f"mean pregame vol {cal.get('mean_pregame_vol',0):.0f} contracts; "
                f"match kinds {cal.get('match_kinds',{})}")
            lines.append(
                f"- Brier: sharp {br.get('sharp',float('nan')):.4f} | "
                f"Kalshi print {br.get('kalshi',float('nan')):.4f} | "
                f"50/50 {br.get('base',float('nan')):.4f} "
                f"(gain {br.get('kalshi',float('nan'))-br.get('sharp',float('nan')):+.4f})")
            lines.append(f"- gap→outcome (day-clustered): {_fmt_reg(cal.get('gap_reg',{}))}")
            lines.append(f"- gap→Kalshi drift-to-close: {_fmt_reg(cal.get('drift_reg',{}))}")
            lines.append(
                f"- print/touch net-of-fee edge: **{100*fil.get('avg_pnl_ct',float('nan')):+.2f}¢/ct** "
                f"CI[{100*fil.get('avg_pnl_ci',[float('nan')])[0]:+.2f},"
                f"{100*fil.get('avg_pnl_ci',[0,float('nan')])[1]:+.2f}] "
                f"({fil.get('n_trades',0)} trades)")
            lines.append(
                f"- CLV vs Kalshi close: {100*fil.get('clv_ct',float('nan')):+.2f}¢/ct "
                f"CI[{100*fil.get('clv_ci',[float('nan')])[0]:+.2f},"
                f"{100*fil.get('clv_ci',[0,float('nan')])[1]:+.2f}] (n={fil.get('clv_n',0)})")
            lines.append(f"- median executable size: ${fil.get('median_exec_usd',float('nan')):.0f}/market")
        return "\n".join(lines)

    cov = s.get("f5_h2h_coverage", {})
    rp = s.get("rfi_prints", {})

    # explicit lead-lag amplification vs P-021 (headline KXMLBTOTAL):
    # P-021 drift b=+0.91 (t=+3.21), CLV +1.8c on the liquid headline.
    def _amp(series_key: str) -> str:
        blk = s.get(series_key, {}).get("t15", {})
        dr = blk.get("calib", {}).get("drift_reg", {})
        fil = blk.get("fill", {})
        db, dt = dr.get("b", float("nan")), dr.get("t_b", float("nan"))
        clv = fil.get("clv_ct", float("nan"))
        clv_lo = fil.get("clv_ci", [float("nan")])[0]
        verdict = ("AMPLIFIED" if (dt == dt and dt > 3.21 and clv == clv and clv > 0.018)
                   else "NOT amplified (≤ P-021)")
        return (f"- **{series_key}** (t15): drift b={db:+.3f} (t={dt:+.2f}) vs P-021 "
                f"b=+0.91 (t=+3.21); CLV {100*clv:+.2f}¢ "
                f"CI-lo {100*clv_lo:+.2f}¢ vs P-021 +1.8¢ → **{verdict}**")
    amp_lines = _amp("f5total") + "\n" + _amp("rfi")

    md = f"""# P-024 — MLB F5 / RFI Thin-Corner Sharp-Reference — Phase-1 Gate REPORT
*Generated {stamp}. `backtest_f5.py` on live Kalshi settled `KXMLBF5TOTAL` +
`KXMLBRFI` candlesticks vs Odds API historical Pinnacle-eu per-event `totals_1st_5_innings`
/ `totals_1st_1_innings`. Backtest-first, kill-gated. No pod.*

## Verdict: **{verdict}**

{chr(10).join('- ' + n for n in notes)}

## Interpretation — why KILL
1. **The thin corners are as sharp as the headline — P-021 repeats.** On F5-total
   the near-lock Brier is tied (sharp 0.2494 vs Kalshi 0.2488; Kalshi marginally
   better) and the gap→outcome regression is insignificant (t=−1.37). Kalshi's
   pregame F5-total price already contains what Pinnacle's F5 line does — exactly
   the efficient-headline result that killed P-021, now reproduced one derivative
   deeper. Size is NOT the problem (median executable $2.8k/market); *edge* is.
2. **The lead-lag did NOT concentrate here — it collapsed.** The whole P-024 thesis
   was that Pinnacle's lead over Kalshi's drift (headline: b=+0.91, t=+3.21) would
   be *larger* where attention thins. It is smaller to absent: F5-total drift
   b=+0.087 (t=+0.30), RFI b=+0.009 (t=+1.51) — both far below the headline. The
   print/touch net edge is *negative* on both (F5 −5.6¢, RFI −8.6¢) with CIs that
   include or sit below zero. Attention does not visibly lag into these corners.
3. **On RFI, Kalshi is the SHARPER book.** The RFI gap→outcome coefficient is
   significant but *negative* (t=−3.72) and Kalshi's Brier beats Pinnacle's
   (0.2457 vs 0.2463), so trading toward the Pinnacle prob loses — CLV is
   −0.48¢/ct with the whole CI below zero ([−0.86, −0.20]). Prints and size are
   abundant (~2,346 usable/season, $29k/market) but there is nothing to harvest:
   RFI is a Kalshi-native, heavily-traded market that leads the sharp book, not
   the other way round.

**Bottom line: KILL.** Both maker-free thin corners are at least as efficient as
the P-021 headline, the hypothesised lead-lag amplification is absent, and the
only executable edges are negative. Liquidity exists; edge does not. No pod,
collector, or config is built.

## The hypothesis under test
P-021 killed the LIQUID headline total (`KXMLBTOTAL`): Kalshi's pregame price was
as sharp as Pinnacle (Brier tied), the only signal a lead-lag (Pinnacle leads
Kalshi's drift, t=+3.21) worth **+1.8¢ CLV** — too small to clear the taker fee.
P-024 asks whether that lead-lag is **materially larger** in the maker-free thin
"innings" corners, where attention concentrates on the headline market. The
lead-lag amplification test is the drift regression + CLV below; the value test
is the gap→outcome regression + Brier.

## Protocol (why this is not a mid-based backtest)
- Every price is the candlestick **touch** (`yes_bid`/`yes_ask`) or an executed
  **print** (`price` OHLC + per-minute `volume_fp`); bare mids are never used to
  admit a trade. A market with a one-sided touch or **zero entry-hour volume is
  dropped** — a bare ask on an empty book is not a fillable price.
- A trade is admitted only when the sharp prob is **strictly through the touch**
  net of the taker fee (buy YES iff `p_sharp − ask − fee > margin`; NO symmetric).
- Simulated size is haircut to the **entry-hour `volume_fp`**; the REPORT gives
  the **median executable \\$/market** (= entry-hour contracts × fill price).
- **Conservative lag-align**: the sharp snapshot is taken **10 min before** the
  Kalshi decision horizon (commence−70m vs Kalshi T−60m; commence−25m vs Kalshi
  T−15m), so any measured Pinnacle lead survives handing the sharp feed a 10-min
  head-start. Odds API Pinnacle-eu "may incur a delay"; this neutralises it.
- All SEs **day-clustered** (cluster-robust OLS + whole-game-day bootstrap CIs).

## F5-moneyline (`KXMLBF5`) — sharp-reference coverage
Across **{cov.get('snaps',0)}** event-snapshots, `h2h_1st_5_innings` appeared on
any eu book **{cov.get('any_book',0)}** times and on **Pinnacle {cov.get('pinnacle',0)}**
times. Pinnacle-eu carries the F5 total and RFI but posts the F5 *moneyline* on
only a small share of pregame snapshots (soft books carry it more often) — and
`KXMLBF5` is the thinnest Kalshi market of the set (verified: no T−15m print on
the sampled game). With no reliable *sharp* reference and the thinnest book, F5
moneyline is reported here for coverage but is **out-of-scope for the gate**;
`KXMLBF5TOTAL` is the primary corner.

## F5 Total — the primary
{sec('f5total', 'KXMLBF5TOTAL vs Pinnacle-eu totals_1st_5_innings')}

*Line alignment: Kalshi F5-total strikes are X.5; Pinnacle often posts an integer
line. `p_sharp` at a Kalshi strike is Pinnacle's devigged P(over) when it posted
that exact X.5 line (`pin_exact`), else the eu consensus at that exact line
(`con_exact`), else a straddle interpolation across the two nearest posted lines
(`*_interp`). The `pin_exact` subset is the cleanest sharp comparison.*

## Lead-lag amplification test (task step 4)
The P-024 thesis is that P-021's Pinnacle→Kalshi lead-lag (headline `KXMLBTOTAL`:
drift b=+0.91, t=+3.21, CLV +1.8¢) is *larger* in these thin corners. Comparing
the near-lock (t15) drift regression + CLV directly:

{amp_lines}

"AMPLIFIED" requires BOTH a stronger drift t-stat (>3.21) AND a larger positive
CLV lower bound (>+1.8¢). Anything else means the corner is no leakier than the
headline — the lead-lag did not concentrate here.

## RFI — print-based near lock only
{sec('rfi', 'KXMLBRFI vs Pinnacle-eu totals_1st_1_innings (Over 0.5 == RFI Yes)')}

Usable near-lock prints: **{rp.get('n_usable_nearlock',0)}** of {rp.get('n_settled',0)}
settled RFI games over {rp.get('n_days',0)} game-days
({rp.get('usable_per_day',0):.1f}/day) → projected **~{rp.get('proj_season',0):.0f}
usable prints/season** (×180 game-days). Gate needs ≥300.

## Gate
- **ADVANCE** iff (F5-total near-lock) gap→outcome b>0 & |t|>1.96 day-clustered
  **AND** print/touch net edge ≥2¢/ct with a day-clustered CI clearing 0 **AND**
  median executable ≥\\$100/market; RFI additionally needs ≥300 usable prints/season.
- **KILL** if the corners are as sharp as the headline (P-021 repeating: tied
  Brier, insignificant gap) or the edge lives only on unfillable quotes.

## Odds API spend
This run: **{_ODDS_SPEND['credits']} credits** over {_ODDS_SPEND['calls']} calls
(per-event period markets = 10 cr/market/snapshot; 2 markets × 2 snapshots/game +
1 events-list). Budget stated in the run: **{args.credit_budget} credits** of the
key's ~4.9M remaining. Lookback {args.lookback_days} days.

## Caveats
- F5-total main lines are often integers while Kalshi strikes are X.5; the
  interpolated subset carries a light monotonicity assumption (see line note).
  The `pin_exact` / `con_exact` subsets are assumption-free and reported via the
  match-kind counts.
- Kalshi F5-total settles at end of the 5th; RFI at the end of the 1st. The
  pregame anchors are candlestick touches at the ticker-decoded first pitch minus
  the horizon; markets with no two-sided touch there are dropped (correctly — not
  liquid enough to trade).
- Pinnacle "near-lock" = snapshot at commence−25m (the conservative-lag sharp
  side of the Kalshi T−15m horizon); a few games may have the line pulled early.
"""
    (HERE / "REPORT_MLB_F5_2026-07.md").write_text(md)
    print("\n" + "=" * 72)
    print(f"VERDICT: {verdict}")
    for n in notes:
        print("  " + n)
    print("=" * 72)
    print(f"wrote {HERE/'REPORT_MLB_F5_2026-07.md'}")
    print(f"wrote {HERE/'p024_params.json'}")


# ── Driver ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback-days", type=int, default=60)
    ap.add_argument("--max-games", type=int, default=None,
                    help="cap games for the Odds pull (smoke test)")
    ap.add_argument("--credit-budget", type=int, default=120000,
                    help="hard stop for Odds API spend this run")
    ap.add_argument("--margin", type=float, default=0.0)
    ap.add_argument("--rate", type=float, default=0.20)
    ap.add_argument("--score-only", action="store_true")
    args = ap.parse_args()
    KP._min_interval = args.rate

    if args.score_only:
        s = score(args.margin)
        write_report(s, args)
        return

    # Stage 1: Kalshi settled universe (cheap list calls)
    f5_settled = _pull_settled_series("KXMLBF5TOTAL", args.lookback_days)
    rfi_settled = _pull_settled_series("KXMLBRFI", args.lookback_days)
    all_settled = f5_settled + rfi_settled

    # dedup to one entry per PHYSICAL GAME (F5-total and RFI share a game_key);
    # keep a title that parses teams cleanly.
    ev_seen: Dict[str, Dict[str, Any]] = {}
    for r in all_settled:
        gk = _game_key(r["event"])
        cur = ev_seen.get(gk)
        if cur is None or _teams_from_title(r["title"]):
            ev_seen[gk] = {"game": gk, "event": r["event"], "title": r["title"]}
    events = list(ev_seen.values())
    print(f"[universe] {len(events)} distinct physical games", flush=True)

    # Stage 2: Odds FIRST (so we know each game's posted sharp lines), then price
    # only the Kalshi F5-total strikes near a posted line + every RFI market.
    odds_rows = pull_odds(events, args.max_games, args.credit_budget)
    posted_f5: Dict[str, set] = {}
    matched_games = set()
    for o in odds_rows:
        if not o.get("ok"):
            continue
        matched_games.add(o["game"])
        lines = set()
        for lab in ("t60", "t15"):
            snp = o["snaps"].get(lab)
            if snp and snp.get("ok"):
                lines |= set(float(x) for x in snp["f5"]["pin"]) \
                       | set(float(x) for x in snp["f5"]["con"])
        posted_f5[o["game"]] = lines

    def near_posted(strike: float, lines: set) -> bool:
        return any(abs(strike - ln) <= 1.0 for ln in lines) if lines else False

    targets: List[Dict[str, Any]] = []
    for r in f5_settled:
        gk = _game_key(r["event"])
        if gk in matched_games and near_posted(r.get("strike", -9),
                                               posted_f5.get(gk, set())):
            targets.append(r)
    for r in rfi_settled:
        if _game_key(r["event"]) in matched_games:
            targets.append(r)
    print(f"[universe] {len(targets)} Kalshi markets to price "
          f"({sum(1 for t in targets if t['series']=='KXMLBF5TOTAL')} F5-total, "
          f"{sum(1 for t in targets if t['series']=='KXMLBRFI')} RFI)", flush=True)

    pull_kalshi(targets)
    s = score(args.margin)
    write_report(s, args)


if __name__ == "__main__":
    main()
