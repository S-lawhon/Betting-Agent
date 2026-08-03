#!/usr/bin/env python
"""Kalshi-vs-ProphetX cross-venue basis collector (STANDALONE, READ-ONLY).

Runs the pre-registered falsification for P-030
(research/SPEC_P030_CrossVenue_Kalshi_ProphetX.md). This process only MEASURES.

WHY THIS IS A NEW FILE, NOT AN EDIT TO collect_inplay_basis.py
--------------------------------------------------------------
The spec said "extend scripts/collect_inplay_basis.py". That script is running
under systemd (scripts/betting-inplay-basis.service) accruing the Q3
Kalshi-vs-Polymarket sample, and a 14-day collection run is exactly the kind of
thing you do not want to restart mid-flight. So this borrows its structure --
Kalshi anchors the slate, append-only JSONL, rows are raw inputs not
conclusions, separate process, never imports place_order -- and leaves the
running collector untouched. Same discipline, different question.

WHAT IT WRITES
--------------
data/crossvenue_basis/YYYY-MM-DD.jsonl, one row per (game, side, snapshot).
Rows carry BOTH books, depth on both sides, and the executable gap in both
directions. Basis interpretation is left to analyze_crossvenue_gates.py --
nothing here decides anything.

UNIVERSE (locked by the spec; do not widen without a new pre-registration)
--------------------------------------------------------------------------
MLB / NBA / NHL moneyline + tennis match winner ONLY. Excluded for cause:
  * golf        -- ProphetX's certified spec cross-references dead heats to
                   "Rule F", which is *Player Must Start*. Dead-heat rules are
                   NEVER DEFINED in the filing. This book has already been
                   burned once assuming golf tie payouts.
  * spreads/totals -- needs Kalshi's strike grid and ProphetX's sportsbook line
                   grid to coincide; unverified. ProphetX VOIDS an exact-line
                   landing, Kalshi does not push.
  * player props -- opposite DNP conventions (Kalshi marks to last fair price,
                   ProphetX voids and refunds).
  * P > 0.75    -- Rule 4.6's No-Cancellation Range is 15% OF PRICE, so the
                   corridor is closed in the tails. Sampled anyway (recording
                   is free) but flagged `in_spec_band` for the gates.

Production collection remains blocked on production credentials and Gate 0.
Sandbox authentication and shapes were validated 2026-08-02. With no
credentials the collector exits 2 rather than writing an empty sample that
later looks like evidence. Sandbox rows are written to a separate directory
and carry `px_environment=sandbox` so they cannot enter the production gate.

Usage:
  python3 scripts/validate_prophetx_readiness.py --env sandbox
  python3 scripts/collect_crossvenue_basis.py --probe        # credential/liveness check
  python3 scripts/collect_crossvenue_basis.py --discover     # dump ProphetX vocab, no writes
  python3 scripts/collect_crossvenue_basis.py --match-only   # show matched pairs, no writes
  python3 scripts/collect_crossvenue_basis.py --once         # one snapshot pass
  python3 scripts/collect_crossvenue_basis.py                # continuous
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.kalshi_public import KalshiPublic                     # noqa: E402
from src.prophetx_client import ProphetXClient, ProphetXAuthError  # noqa: E402
from src.prophetx_fees import (breakeven_gap, no_cancel_ceiling,  # noqa: E402
                               expected_fee_per_contract,
                               kalshi_fee_per_contract)

OUT_DIR = os.path.join(ROOT, "data", "crossvenue_basis")
SANDBOX_OUT_DIR = os.path.join(ROOT, "data", "crossvenue_basis_sandbox")
log = logging.getLogger("crossvenue_basis")
EASTERN = ZoneInfo("America/New_York")
MAX_START_SKEW_SECONDS = 15 * 60
MAX_START_SKEW_BY_SPORT = {"tennis": 6 * 60 * 60}

# Kalshi series that carry a two-sided game winner. Off-season series simply
# return zero open markets, so leaving them all on costs one cheap call each.
SERIES = {
    "KXMLBGAME": "mlb", "KXNBAGAME": "nba", "KXNHLGAME": "nhl",
    "KXATPMATCH": "tennis", "KXWTAMATCH": "tennis",
}

# Kalshi maker-fee status for the universe. ALL FIVE CHARGE MAKER FEES (they are
# in the 130-series `quadratic_with_maker_fees` set, verified against the live
# /series census 2026-07-29). Recorded explicitly because the cheap-fee golf
# series are exactly the ones disqualified above -- the cheap fee and the clean
# contract do not overlap.
SERIES_CHARGES_MAKER = {s: True for s in SERIES}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _kalshi_abbr(ticker: str):
    return ticker.rsplit("-", 1)[1].upper() if "-" in ticker else None


def _kalshi_start(ticker: str):
    match = re.search(r"-(\d{2})([A-Z]{3})(\d{2})(\d{4})", ticker.upper())
    if not match:
        return None
    try:
        parsed = datetime.strptime("".join(match.groups()), "%y%b%d%H%M")
    except ValueError:
        return None
    return parsed.replace(tzinfo=EASTERN).astimezone(timezone.utc)


def _iso_start(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ── Kalshi side: collapse per-team tickers into one game ─────────────────────

def kalshi_games(kp: KalshiPublic, series: dict = None) -> dict:
    """{game_id: {series, sport, tickers:{ABBR: ticker}, titles:{ABBR: str}}}
    for open two-sided markets. Same shape as collect_inplay_basis.py."""
    games = {}
    for stick, sport in (series or SERIES).items():
        for m in kp.open_markets(stick) or []:
            tk = m.get("ticker") or ""
            abbr = _kalshi_abbr(tk)
            if not tk or "-" not in tk or not abbr:
                continue
            gid = tk.rsplit("-", 1)[0]
            # Tennis tickers encode only the date, not the scheduled time.
            # `occurrence_datetime` is Kalshi's explicit event timestamp and is
            # preferred for every sport; the ticker timestamp remains a
            # verified fallback for older game-market payloads.
            ticker_start = _kalshi_start(gid)
            explicit_start = _iso_start(m.get("occurrence_datetime"))
            start = ticker_start or explicit_start
            g = games.setdefault(gid, {"series": stick, "sport": sport,
                                       "tickers": {}, "titles": {},
                                       "_scheduled_starts": set()})
            if start:
                g["_scheduled_starts"].add(start.isoformat())
            g["tickers"][abbr] = tk
            g["titles"][abbr] = m.get("yes_sub_title") or m.get("title") or ""
    out = {}
    for gid, game in games.items():
        if len(game["tickers"]) != 2:
            continue
        starts = game.pop("_scheduled_starts")
        # Opposing contracts must agree on the event time. Conflicting or
        # absent timestamps become None and are rejected by match_pairs.
        game["scheduled_start_at"] = next(iter(starts)) if len(starts) == 1 else None
        out[gid] = game
    return out


# ── ProphetX side ────────────────────────────────────────────────────────────

def prophetx_moneylines(px: ProphetXClient, max_events: int = 400) -> list:
    """[{event_id, title, market, book, names}] for two-sided ProphetX markets.

    Sandbox field names were reconciled on 2026-08-02. Anything this cannot
    parse is still counted, not silently dropped -- a matcher that quietly
    matches nothing looks identical to a venue with no mispricing, and that
    failure mode would produce a false KILL.
    """
    out, skipped = [], 0
    for ev in (px.sport_events() or [])[:max_events]:
        eid = (ev.get("event_id") or ev.get("id") or ev.get("sport_event_id"))
        if not eid:
            skipped += 1
            continue
        title = ev.get("name") or ev.get("display_name") or ev.get("title") or ""
        for mk in px.markets_for_event(str(eid)) or []:
            mtype = _norm(mk.get("type") or mk.get("market_type") or "")
            mname = _norm(mk.get("name") or "")
            if mtype not in ("moneyline", "matchwinner", "winner", "h2h"):
                continue
            # `type=moneyline` also appears on period/inning and YES/NO props.
            # Only the exact main-game market is contract-comparable here.
            if mname not in ("moneyline", "matchwinner", "winner", "h2h"):
                continue
            book = px.moneyline_book(mk)
            names = [k for k in book if k != "_unparsed"]
            if len(names) != 2:
                skipped += 1
                continue
            out.append({"event_id": str(eid), "title": title, "names": names,
                        "book": book,
                        "scheduled_start_at": ev.get("scheduled"),
                        "market_id": mk.get("market_id") or mk.get("id")})
    if skipped:
        log.warning("prophetx: %d events/markets unparsed -- reconcile with "
                    "--dump-shapes before trusting the sample", skipped)
    return out


# ── matching ─────────────────────────────────────────────────────────────────
# Kalshi supplies display titles as well as ticker abbreviations. Exact
# normalized display-name pairs are the primary path and work for changing
# player universes such as tennis without maintaining a guessed alias table.
# MLB retains the verified canonical fallback because Kalshi uses city labels
# ("Boston") while ProphetX uses full club names ("Boston Red Sox"). NBA/NHL
# maps remain empty until their live title shapes can be verified in season.
# There is deliberately no fuzzy fallback: a wrong match is worse than no match
# because it manufactures a basis between two different events.

VOCAB = {
    "mlb": {
        "arizonadiamondbacks": "AZ", "athletics": "ATH", "atlantabraves": "ATL",
        "baltimoreorioles": "BAL", "bostonredsox": "BOS", "chicagocubs": "CHC",
        "chicagowhitesox": "CWS", "cincinnatireds": "CIN",
        "clevelandguardians": "CLE", "coloradorockies": "COL",
        "detroittigers": "DET", "houstonastros": "HOU", "kansascityroyals": "KC",
        "losangelesangels": "LAA", "losangelesdodgers": "LAD",
        "miamimarlins": "MIA", "milwaukeebrewers": "MIL", "minnesotatwins": "MIN",
        "newyorkmets": "NYM", "newyorkyankees": "NYY",
        "philadelphiaphillies": "PHI", "pittsburghpirates": "PIT",
        "sandiegopadres": "SD", "sanfranciscogiants": "SF",
        "seattlemariners": "SEA", "stlouiscardinals": "STL", "tampabayrays": "TB",
        "texasrangers": "TEX", "torontobluejays": "TOR",
        "washingtonnationals": "WSH",
    },
    "nba": {}, "nhl": {}, "tennis": {},
}


def match_pairs(kgames: dict, pxmls: list) -> tuple:
    """Return exact/schedule-safe pairs and explicit unmatched diagnostics."""
    by_pair, by_names = {}, {}
    for m in pxmls:
        normalized_names = [_norm(name) for name in m["names"]]
        if all(normalized_names) and len(set(normalized_names)) == 2:
            by_names.setdefault(frozenset(normalized_names), []).append(m)
        sport_map = None
        keys = []
        for sport, vocab in VOCAB.items():
            cand = [vocab.get(_norm(n)) for n in m["names"]]
            if all(cand) and len(set(cand)) == 2:
                sport_map, keys = sport, cand
                break
        if not sport_map:
            continue
        by_pair.setdefault(frozenset(keys), []).append(
            (m, dict(zip(keys, m["names"]))))

    pairs, reasons = [], {
        "no_px_market": 0, "no_vocab_for_sport": 0,
        "schedule_unavailable": 0, "schedule_mismatch": 0,
        "ambiguous_px_market": 0,
        "by_sport": {},
    }

    def reject(sport, reason):
        reasons[reason] += 1
        sport_row = reasons["by_sport"].setdefault(sport, {
            "matched": 0, "no_px_market": 0, "no_vocab_for_sport": 0,
            "schedule_unavailable": 0, "schedule_mismatch": 0,
            "ambiguous_px_market": 0,
        })
        sport_row[reason] += 1

    for gid, g in kgames.items():
        sport = g["sport"]
        title_map = {_norm(title): abbr for abbr, title in g["titles"].items()
                     if _norm(title)}
        exact = []
        if len(title_map) == 2:
            for market in by_names.get(frozenset(title_map), []):
                exact.append((market, {
                    title_map[_norm(name)]: name for name in market["names"]
                }))
        if exact:
            cands = exact
        elif VOCAB.get(sport):
            cands = by_pair.get(frozenset(g["tickers"].keys()))
        else:
            reject(sport, "no_vocab_for_sport" if len(title_map) != 2
                   else "no_px_market")
            continue
        if not cands:
            reject(sport, "no_px_market")
            continue
        kalshi_start = _iso_start(g.get("scheduled_start_at"))
        if not kalshi_start:
            reject(sport, "schedule_unavailable")
            continue
        aligned = []
        had_px_start = False
        tolerance = MAX_START_SKEW_BY_SPORT.get(sport, MAX_START_SKEW_SECONDS)
        for m, keymap in cands:
            px_start = _iso_start(m.get("scheduled_start_at"))
            if not px_start:
                continue
            had_px_start = True
            if abs((kalshi_start - px_start).total_seconds()) <= tolerance:
                aligned.append((m, keymap, px_start))
        if not aligned:
            reject(sport, "schedule_mismatch" if had_px_start
                   else "schedule_unavailable")
            continue
        if len(aligned) != 1:
            reject(sport, "ambiguous_px_market")
            continue
        m, keymap, px_start = aligned[0]
        pairs.append({"game_id": gid, "series": g["series"], "sport": g["sport"],
                      "kalshi_tickers": g["tickers"],
                      "px_event_id": m["event_id"], "px_title": m["title"],
                      "px_names": keymap, "px_book": m["book"],
                      "kalshi_scheduled_start_at": kalshi_start.isoformat(),
                      "px_scheduled_start_at": px_start.isoformat(),
                      "schedule_tolerance_seconds": tolerance})
        sport_row = reasons["by_sport"].setdefault(sport, {
            "matched": 0, "no_px_market": 0, "no_vocab_for_sport": 0,
            "schedule_unavailable": 0, "schedule_mismatch": 0,
            "ambiguous_px_market": 0,
        })
        sport_row["matched"] += 1
    return pairs, reasons


# ── snapshot ─────────────────────────────────────────────────────────────────

def _gap(buy_a: float, buy_b: float):
    """Gross lock in dollars from buying complementary sides at two asks."""
    if buy_a is None or buy_b is None:
        return None
    return 1.0 - buy_a - buy_b


def snapshot(kp: KalshiPublic, px: ProphetXClient, pair: dict,
             tax_rate: float) -> list:
    """One row per side. Records both books, depth, and the executable gap in
    both directions. NOTE: no ladder snapping is applied -- both venues'
    OBSERVED book prices are already on their own grids, so snapping here would
    manufacture edge. Snapping only matters when posting, which this cannot do.
    """
    ts = datetime.now(timezone.utc).isoformat()
    kalshi_start = _iso_start(pair.get("kalshi_scheduled_start_at"))
    px_start = _iso_start(pair.get("px_scheduled_start_at"))
    schedule_skew = (abs((kalshi_start - px_start).total_seconds())
                     if kalshi_start and px_start else None)
    charges_maker = SERIES_CHARGES_MAKER.get(pair["series"], True)
    k_books, px_books = {}, {}
    for abbr, tk in pair["kalshi_tickers"].items():
        k_books[abbr] = kp.orderbook(tk) or {}
        px_books[abbr] = pair["px_book"].get(pair["px_names"].get(abbr), {})

    rows = []
    abbrs = list(pair["kalshi_tickers"].keys())
    for i, abbr in enumerate(abbrs):
        other = abbrs[1 - i]
        kb, pb_other = k_books[abbr], px_books[other]
        k_bid, k_ask = kb.get("yes_bid"), kb.get("yes_ask")
        k_mid = ((k_bid + k_ask) / 2
                 if k_bid is not None and k_ask is not None else None)
        px_ask_other = pb_other.get("ask_prob")

        gap = _gap(k_ask, px_ask_other)
        # Kalshi is the deeper and sharper venue on every prior measurement
        # (P-020: Brier 0.1373 vs 0.1445, optimal blend weight on the other
        # venue exactly 0.0), so its mid is the FMV proxy for the Rule 4.6
        # check. Inputs are stored so analysis can substitute a better one.
        fmv_px_leg = (1.0 - k_mid) if k_mid is not None else None
        px_leg_edge = ((fmv_px_leg - px_ask_other)
                       if (fmv_px_leg is not None and px_ask_other is not None)
                       else None)
        ceiling = (no_cancel_ceiling(px_ask_other)
                   if px_ask_other is not None else None)

        rows.append({
            "ts_utc": ts, "game_id": pair["game_id"], "series": pair["series"],
            "sport": pair["sport"], "team": abbr, "other": other,
            "px_environment": pair.get("px_environment") or "unknown",
            "kalshi_scheduled_start_at": pair.get("kalshi_scheduled_start_at"),
            "px_scheduled_start_at": pair.get("px_scheduled_start_at"),
            "schedule_skew_seconds": schedule_skew,
            "schedule_tolerance_seconds": pair.get("schedule_tolerance_seconds",
                                                   MAX_START_SKEW_SECONDS),
            "kalshi_ticker": pair["kalshi_tickers"][abbr],
            "px_event_id": pair["px_event_id"],
            "k_yes_bid": k_bid, "k_yes_ask": k_ask, "k_mid": k_mid,
            "k_bid_qty": kb.get("bid_qty"), "k_ask_qty": kb.get("ask_qty"),
            "px_other_bid_prob": pb_other.get("bid_prob"),
            "px_other_ask_prob": px_ask_other,
            "px_other_bid_odds": pb_other.get("bid_odds"),
            "px_other_ask_odds": pb_other.get("ask_odds"),
            "px_other_bid_size": pb_other.get("bid_size"),
            "px_other_ask_size": pb_other.get("ask_size"),
            # gate inputs -- thresholds are applied in the analyzer, not here
            "gap": gap,
            "breakeven_fees_only": (breakeven_gap(k_mid, False, charges_maker)
                                    if k_mid is not None else None),
            "breakeven_with_tax": (breakeven_gap(k_mid, False, charges_maker,
                                                 tax_rate)
                                   if k_mid is not None else None),
            "k_fee": (kalshi_fee_per_contract(k_ask) if k_ask is not None else None),
            "px_expected_fee": (expected_fee_per_contract(px_ask_other)
                                if px_ask_other is not None else None),
            "px_leg_edge_vs_fmv": px_leg_edge,
            "rule46_ceiling": ceiling,
            "rule46_bustable": (None if (px_leg_edge is None or ceiling is None)
                                else bool(px_leg_edge > ceiling)),
            "in_spec_band": (None if k_mid is None else bool(0.40 <= k_mid <= 0.65)),
        })
    return rows


def append_rows(rows, output_dir=OUT_DIR) -> int:
    if not rows:
        return 0
    os.makedirs(output_dir, exist_ok=True)
    day = datetime.now(timezone.utc).date().isoformat()
    with open(os.path.join(output_dir, f"{day}.jsonl"), "a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return len(rows)


# ── entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true",
                    help="credential + liveness check, then exit")
    ap.add_argument("--discover", action="store_true",
                    help="dump ProphetX vocab for building VOCAB maps; no writes")
    ap.add_argument("--match-only", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--sample-secs", type=int, default=60)
    ap.add_argument("--refresh-secs", type=int, default=600)
    ap.add_argument("--tax-rate", type=float, default=0.32,
                    help="marginal rate for the s165(d) phantom-tax term. "
                         "Set 0 for capital/s1256 characterization. This is "
                         "GATE 0 and it is not resolved -- ask a CPA.")
    ap.add_argument("--env", default=os.getenv("PROPHETX_ENV", "sandbox"),
                    choices=["sandbox", "production"])
    ap.add_argument("--dump-shapes", metavar="PATH",
                    help="record one response skeleton per endpoint")
    ap.add_argument("--output-dir",
                    help="observation directory; defaults to an isolated "
                         "sandbox directory when --env=sandbox")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    kp = KalshiPublic()
    px = ProphetXClient(env=args.env, dump_shapes_path=args.dump_shapes)
    output_dir = (args.output_dir or
                  (SANDBOX_OUT_DIR if args.env == "sandbox" else OUT_DIR))

    if args.probe:
        info = px.probe()
        log.info("prophetx probe: %s", json.dumps(info))
        if not info["has_credentials"]:
            log.error("BLOCKED: no ProphetX credentials. Request access at "
                      "prophethelp.zendesk.com/hc/en-us/requests/new or "
                      "marketmaking@prophetexchange.com, then set "
                      "PROPHETX_ACCESS_KEY / PROPHETX_SECRET_KEY.")
            return 2
        return 0 if info["authenticated"] else 1

    if not px.has_credentials:
        log.error("BLOCKED: no ProphetX credentials -- refusing to write an "
                  "empty sample that would later read as evidence of no basis. "
                  "Run --probe for the onboarding path.")
        return 2

    if args.discover:
        try:
            evs = px.sport_events()
        except ProphetXAuthError as exc:
            log.error("%s", exc)
            return 2
        log.info("prophetx sport_events: %d", len(evs))
        seen = set()
        for ev in evs[:200]:
            eid = ev.get("event_id") or ev.get("id")
            for mk in px.markets_for_event(str(eid)) or []:
                for n in px.moneyline_book(mk):
                    if n != "_unparsed" and n not in seen:
                        seen.add(n)
        log.info("distinct selection names (%d) -- map these into VOCAB:", len(seen))
        for n in sorted(seen):
            mapped = next((v.get(_norm(n)) for v in VOCAB.values()
                           if v.get(_norm(n))), None)
            log.info("  %-40s -> %s", n, mapped or "UNMAPPED")
        return 0

    def build():
        kg = kalshi_games(kp)
        pxm = prophetx_moneylines(px)
        pairs, reasons = match_pairs(kg, pxm)
        for pair in pairs:
            pair["px_environment"] = args.env
        log.info("kalshi games=%d  prophetx moneylines=%d  matched=%d  %s",
                 len(kg), len(pxm), len(pairs), reasons)
        return pairs

    if args.match_only or args.once:
        pairs = build()
        for p in pairs:
            log.info("  MATCH %s [%s] px_event=%s teams=%s",
                     p["game_id"], p["sport"], p["px_event_id"],
                     list(p["kalshi_tickers"]))
        if args.match_only:
            return 0
        total = sum(append_rows(snapshot(kp, px, p, args.tax_rate), output_dir)
                    for p in pairs)
        log.info("--once: wrote %d rows to %s", total, output_dir)
        return 0

    stop = {"v": False}
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.__setitem__("v", True))
    pairs, last = [], 0.0
    while not stop["v"]:
        if time.time() - last >= args.refresh_secs or not pairs:
            try:
                pairs = build()
            except ProphetXAuthError as exc:
                log.error("auth lost: %s -- stopping", exc)
                return 2
            except Exception as exc:
                log.error("refresh failed: %s", exc)
            last = time.time()
        wrote = 0
        for p in pairs:
            try:
                wrote += append_rows(
                    snapshot(kp, px, p, args.tax_rate), output_dir)
            except Exception as exc:
                log.error("snapshot failed for %s: %s", p.get("game_id"), exc)
        if wrote:
            log.info("wrote %d rows", wrote)
        slept = 0
        while slept < args.sample_secs and not stop["v"]:
            time.sleep(min(2, args.sample_secs - slept))
            slept += 2
    log.info("stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
