#!/usr/bin/env python
"""Kalshi-vs-Polymarket in-play MLB basis collector (STANDALONE, READ-ONLY).

Answers research question Q3 (research/OPEN_QUESTIONS_2026-07-20.md): is there a
persistent, tradeable in-play basis between Kalshi KXMLBGAME moneyline and the
matching Polymarket series_id=3 moneyline? This process only MEASURES it.

Design (per the Q3 spec):
- NOT a pod, NOT in pods.active. Runs its own slow loop like run_golf_maker.py.
- READ-ONLY on both venues: KalshiPublic (public data client) and
  PolymarketClient READ paths only (get_sport_events / get_book / get_midpoint).
  It never imports or calls place_order / cancel_order.
- Kalshi anchors the live slate (its open KXMLBGAME set is exactly today's games);
  Polymarket events are matched to it by normalized team-pair + game date.
- Do NOT trust top-of-book: Polymarket MLB books are thin with a ~40c inside
  spread, so we record midpoint, depth, spread and liquidity on every row and
  leave basis interpretation to analysis. Rows are raw inputs, not conclusions.
- Append-only JSONL under data/inplay_basis/YYYY-MM-DD.jsonl. A collector crash
  can never touch the live engine (separate process, separate log).

Usage:
  python3 scripts/collect_inplay_basis.py --once          # one match+snapshot pass, prints summary
  python3 scripts/collect_inplay_basis.py --match-only     # show matched pairs, no book snapshots, no writes
  python3 scripts/collect_inplay_basis.py                  # continuous collection loop
Flags: --inplay-only (default on) samples only after game start; --all-open also
samples pre-game. --sample-secs / --refresh-secs tune cadence. --pm-pages caps
Polymarket pagination per refresh.
"""
from __future__ import annotations
import os, sys, re, json, time, argparse, signal, logging
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.kalshi_public import KalshiPublic
from src.polymarket_client import PolymarketClient
try:
    from src.et_time import parse_mlb_ticker_start
except Exception:                                   # pragma: no cover
    parse_mlb_ticker_start = None

OUT_DIR = os.path.join(ROOT, "data", "inplay_basis")
log = logging.getLogger("inplay_basis")

# ── team canonicalization ────────────────────────────────────────────────────
# Kalshi and Polymarket name teams differently: Kalshi yes_sub_title is a short
# city form ("San Francisco", "St. Louis", "A's") while Polymarket uses the full
# "City Nickname" ("San Francisco Giants", "St. Louis Cardinals", "Athletics").
# Exact string equality never matches. The canonical key we use for BOTH sides is
# Kalshi's own team abbreviation, which every KXMLBGAME ticker carries as its
# suffix (e.g. KXMLBGAME-...LAASF-SF -> "SF"). We map Polymarket's 30 full names
# onto the same abbreviations. Verified against the live vocab of both venues
# 2026-07-22 (all 30 teams present on each side).
def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())

_PM_TO_ABBR = {
    "arizonadiamondbacks": "AZ",  "athletics": "ATH",           "atlantabraves": "ATL",
    "baltimoreorioles": "BAL",    "bostonredsox": "BOS",         "chicagocubs": "CHC",
    "chicagowhitesox": "CWS",     "cincinnatireds": "CIN",       "clevelandguardians": "CLE",
    "coloradorockies": "COL",     "detroittigers": "DET",        "houstonastros": "HOU",
    "kansascityroyals": "KC",     "losangelesangels": "LAA",     "losangelesdodgers": "LAD",
    "miamimarlins": "MIA",        "milwaukeebrewers": "MIL",     "minnesotatwins": "MIN",
    "newyorkmets": "NYM",         "newyorkyankees": "NYY",       "philadelphiaphillies": "PHI",
    "pittsburghpirates": "PIT",   "sandiegopadres": "SD",        "sanfranciscogiants": "SF",
    "seattlemariners": "SEA",     "stlouiscardinals": "STL",     "tampabayrays": "TB",
    "texasrangers": "TEX",        "torontobluejays": "TOR",      "washingtonnationals": "WSH",
}

def _kalshi_abbr(ticker: str):
    """Team abbreviation = the ticker's trailing -SUFFIX (Kalshi's own code)."""
    return ticker.rsplit("-", 1)[1].upper() if "-" in ticker else None

def _pm_abbr(full_name: str):
    return _PM_TO_ABBR.get(_norm(full_name))

def _f(v):
    """Coerce Polymarket numeric-ish fields (often strings) to float, else None."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _date_delta_days(a, b):
    if not a or not b:
        return 999
    try:
        return abs((datetime.fromisoformat(a).date() - datetime.fromisoformat(b).date()).days)
    except Exception:
        return 999

def _game_date_from_ticker(ticker: str):
    """YYYY-MM-DD (UTC) of the game encoded in a KXMLBGAME ticker, or None."""
    if parse_mlb_ticker_start is None:
        return None
    try:
        dt = parse_mlb_ticker_start(ticker)
        return dt.date().isoformat() if dt else None
    except Exception:
        return None


# ── Kalshi side: collapse the two per-team tickers into one game ──────────────
def kalshi_games(kp: KalshiPublic):
    """Return {game_id: {date, tickers:{ABBR: ticker}, start_dt}} for open
    KXMLBGAME markets. game_id = ticker minus the trailing -TEAM suffix; teams
    are keyed by Kalshi abbreviation (the ticker suffix)."""
    games = {}
    for m in kp.open_markets("KXMLBGAME") or []:
        tk = m.get("ticker") or ""
        abbr = _kalshi_abbr(tk)
        if not tk or "-" not in tk or not abbr:
            continue
        gid = tk.rsplit("-", 1)[0]                  # KXMLBGAME-26JUL242215LAASF
        g = games.setdefault(gid, {"date": _game_date_from_ticker(tk),
                                    "tickers": {}, "start_dt": None})
        g["tickers"][abbr] = tk
        if parse_mlb_ticker_start is not None and g["start_dt"] is None:
            try: g["start_dt"] = parse_mlb_ticker_start(tk)
            except Exception: pass
    # keep only complete 2-sided games
    return {gid: g for gid, g in games.items() if len(g["tickers"]) == 2}


# ── Polymarket side: pull events, extract the MONEYLINE market per event ──────
def _parse_jsonish(v):
    if isinstance(v, (list, dict)): return v
    if isinstance(v, str):
        try: return json.loads(v)
        except Exception: return None
    return None

def polymarket_moneylines(pc: PolymarketClient, max_pages: int, want_dates: set = None):
    """Return {frozenset(ABBR pair) -> [candidate, ...]} for MLB moneyline markets.
    Each candidate: {date, tokens:{ABBR: token_id}, slug, condition_id, liquidity,
    spread}. Multiple candidates per pair are kept (a series repeats a matchup);
    match_pairs disambiguates by nearest date. Paginates up to max_pages."""
    out = {}
    cursor = None
    for _ in range(max_pages):
        resp = pc.get_sport_events(series_id=3, next_cursor=cursor)
        events = resp.get("data", []) or []
        for e in events:
            title = e.get("title") or ""
            for m in (e.get("markets") or []):
                outcomes = _parse_jsonish(m.get("outcomes"))
                toks = _parse_jsonish(m.get("clobTokenIds"))
                if not (outcomes and toks) or len(outcomes) != 2 or len(toks) != 2:
                    continue
                # MONEYLINE = market whose two outcomes are the two team names
                # (props/F5 markets have Yes/No or Over/Under outcomes instead),
                # and whose question equals the event title.
                a0, a1 = _pm_abbr(outcomes[0]), _pm_abbr(outcomes[1])
                if a0 is None or a1 is None or a0 == a1:
                    continue
                if _norm(m.get("question") or "") != _norm(title):
                    continue
                # game date: prefer a near-term gameStartTime; fall back to the
                # date embedded in the slug (mlb-tb-nyy-2026-05-23). Stale events
                # carry bogus far-future gameStartTime, so trust the slug date.
                gdate = None
                ms = re.search(r"(\d{4}-\d{2}-\d{2})$", m.get("slug") or "")
                slug_date = ms.group(1) if ms else None
                gs = m.get("gameStartTime") or m.get("startDate") or e.get("startDate")
                if gs:
                    try: gdate = datetime.fromisoformat(str(gs).replace("Z", "+00:00")).date().isoformat()
                    except Exception: pass
                if want_dates:
                    # pick whichever of {gameStartTime date, slug date} lands in
                    # the wanted window; else keep gdate for nearest-date matching
                    if gdate not in want_dates and slug_date in want_dates:
                        gdate = slug_date
                pair = frozenset((a0, a1))
                out.setdefault(pair, []).append({
                    "date": gdate, "slug_date": slug_date,
                    "tokens": {a0: str(toks[0]), a1: str(toks[1])},
                    "slug": m.get("slug"), "condition_id": m.get("conditionId"),
                    "liquidity": _f(m.get("liquidityNum")) or _f(m.get("liquidity")),
                    "spread": _f(m.get("spread")),
                })
        cursor = resp.get("next_cursor")
        if not cursor:
            break
    return out


# ── pairing ──────────────────────────────────────────────────────────────────
def match_pairs(kgames: dict, pmls: dict, max_day_delta: int = 2):
    """Return matched games present on BOTH venues, paired by unordered ABBR pair.
    When a matchup repeats (series), pick the Polymarket candidate whose date is
    closest to the Kalshi game date, within max_day_delta days (tolerates the
    UTC-vs-ET date rolloff of night games)."""
    pairs = []
    for gid, g in kgames.items():
        pair = frozenset(g["tickers"].keys())
        cands = pmls.get(pair)
        if not cands:
            continue
        # choose nearest-date candidate (use slug_date when the gameStartTime date
        # is bogus/far-future); ties -> highest liquidity
        def score(c):
            dd = min(_date_delta_days(g["date"], c.get("date")),
                     _date_delta_days(g["date"], c.get("slug_date")))
            return (dd, -(c.get("liquidity") or 0))
        pm = min(cands, key=score)
        best_dd = score(pm)[0]
        if best_dd > max_day_delta:
            continue
        sides = {}
        for abbr, ktk in g["tickers"].items():
            tok = pm["tokens"].get(abbr)
            if tok:
                sides[abbr] = {"kalshi_ticker": ktk, "pm_token": tok}
        if len(sides) == 2:
            pairs.append({"game_id": gid, "date": g["date"], "start_dt": g["start_dt"],
                          "sides": sides, "pm_liquidity": pm["liquidity"],
                          "pm_slug": pm["slug"], "date_delta": best_dd})
    return pairs


# ── snapshot one matched game -> rows ────────────────────────────────────────
# A game is treated as "in play" from first pitch until this many hours later.
# `now >= start` alone mislabels finished games as live (Kalshi keeps the market
# open for ~hours post-game, until settlement), which wastes calls and pollutes
# the sample. 5h comfortably covers a long game plus delays.
MAX_INPLAY_HOURS = 5

def snapshot(kp, pc, pair, inplay_only: bool):
    start = pair.get("start_dt")
    now = datetime.now(timezone.utc)
    in_play = (start is not None and
               start <= now <= start + timedelta(hours=MAX_INPLAY_HOURS))
    if inplay_only and not in_play:
        return []
    rows = []
    ts = now.isoformat()
    for nteam, s in pair["sides"].items():
        kob = kp.orderbook(s["kalshi_ticker"]) or {}
        k_bid, k_ask = kob.get("yes_bid"), kob.get("yes_ask")
        k_mid = (k_bid + k_ask) / 2 if (k_bid is not None and k_ask is not None) else None
        pmb = pc.get_book(s["pm_token"])
        pm_bid, pm_ask, pm_mid = pmb.best_bid, pmb.best_ask, pmb.midpoint
        rows.append({
            "ts_utc": ts, "game_id": pair["game_id"], "date": pair["date"],
            "team": nteam, "in_play": in_play,
            "kalshi_ticker": s["kalshi_ticker"], "pm_token_id": s["pm_token"],
            "k_yes_bid": k_bid, "k_yes_ask": k_ask, "k_mid": k_mid,
            "k_bid_qty": kob.get("bid_qty"), "k_ask_qty": kob.get("ask_qty"),
            "pm_bid": pm_bid, "pm_ask": pm_ask, "pm_mid": pm_mid,
            "pm_liquidity_usd": pair.get("pm_liquidity"),
            # basis (analysis re-derives; stored for convenience)
            "basis_mid": (pm_mid - k_mid) if (pm_mid is not None and k_mid is not None) else None,
            "basis_pm_bid_minus_k_ask": (pm_bid - k_ask) if (pm_bid is not None and k_ask is not None) else None,
            "basis_k_bid_minus_pm_ask": (k_bid - pm_ask) if (k_bid is not None and pm_ask is not None) else None,
            "game_state": None,     # inning/score not cheaply available yet; left null
        })
    return rows


def append_rows(rows):
    if not rows:
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    day = datetime.now(timezone.utc).date().isoformat()
    path = os.path.join(OUT_DIR, f"{day}.jsonl")
    with open(path, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return len(rows)


def build_pairs(kp, pc, pm_pages):
    kg = kalshi_games(kp)
    want = {g["date"] for g in kg.values() if g["date"]}
    pmls = polymarket_moneylines(pc, pm_pages, want)
    pairs = match_pairs(kg, pmls)
    return kg, pmls, pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one match+snapshot pass then exit")
    ap.add_argument("--match-only", action="store_true", help="print matched pairs, no snapshots, no writes")
    ap.add_argument("--sample-secs", type=int, default=45)
    ap.add_argument("--refresh-secs", type=int, default=600, help="re-match the slate this often")
    ap.add_argument("--pm-pages", type=int, default=8, help="max Polymarket event pages per refresh")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--inplay-only", dest="inplay_only", action="store_true", default=True)
    grp.add_argument("--all-open", dest="inplay_only", action="store_false")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    kp = KalshiPublic()
    pc = PolymarketClient()

    if args.match_only or args.once:
        kg, pmls, pairs = build_pairs(kp, pc, args.pm_pages)
        log.info("kalshi live games=%d  polymarket moneylines=%d  matched=%d",
                 len(kg), len(pmls), len(pairs))
        for p in pairs:
            log.info("  MATCH %s date=%s teams=%s pm_liq=%s slug=%s",
                     p["game_id"], p["date"], list(p["sides"].keys()),
                     p.get("pm_liquidity"), p.get("pm_slug"))
        if args.match_only:
            return
        total = 0
        for p in pairs:
            total += append_rows(snapshot(kp, pc, p, args.inplay_only))
        log.info("--once: wrote %d rows to %s", total, OUT_DIR)
        return

    # continuous loop
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("v", True))
    pairs = []
    last_refresh = 0.0
    while not stop["v"]:
        now = time.time()
        if now - last_refresh >= args.refresh_secs or not pairs:
            try:
                kg, pmls, pairs = build_pairs(kp, pc, args.pm_pages)
                log.info("refresh: kalshi=%d pm=%d matched=%d", len(kg), len(pmls), len(pairs))
            except Exception as e:
                log.error("refresh failed: %s", e)
            last_refresh = now
        wrote = 0
        for p in pairs:
            try:
                wrote += append_rows(snapshot(kp, pc, p, args.inplay_only))
            except Exception as e:
                log.error("snapshot failed for %s: %s", p.get("game_id"), e)
        if wrote:
            log.info("wrote %d rows", wrote)
        # sleep in small slices so SIGTERM is responsive
        slept = 0
        while slept < args.sample_secs and not stop["v"]:
            time.sleep(min(2, args.sample_secs - slept)); slept += 2
    log.info("stopped cleanly")


if __name__ == "__main__":
    main()
