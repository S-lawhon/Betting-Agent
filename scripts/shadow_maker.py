#!/usr/bin/env python3
"""
scripts/shadow_maker.py
───────────────────────
Phase-2 MEASUREMENT harness for the tennis prop maker thesis.

Places NO orders and touches no credentials.  It logs what would have
happened if we had quoted two-sided markets on Kalshi tennis props, then
uses the public trades tape to detect which of those hypothetical quotes
would have been filled, and settlement to score them.

Why measure before building: the research (tennis_research/REPORT.md §7)
found prop series charge NO maker fee and quote 6-11c wide, but a taker
strategy loses.  The maker version is the higher-ceiling idea, and its
whole economics hinge on two unknowns that cannot be derived from
snapshots: (1) what fraction of our resting quotes actually fill, and
(2) how adversely selected those fills are.  This harness measures both
before any order-management code is written.

    python3 -m scripts.shadow_maker cycle     # run one full cycle
    python3 -m scripts.shadow_maker report    # summarize results so far

Cycle does three phases, each idempotent:
  QUOTE  — snapshot open prop markets, derive empirical fair from the
           concurrent Kalshi match line, log hypothetical bid/offer.
  FILL   — for logged quotes, scan the trades tape after the quote time;
           mark fills (see fill model below).
  SETTLE — for filled quotes whose market resolved, score P&L.

Fill model (deliberately conservative):
  We rest a YES bid at `b` and a YES offer at `a`.
    * A tape print with taker_side="no" at yes_price p means a resting
      YES bid at p was hit.  If p < b our bid was strictly more
      aggressive → we would have filled first ("aggressive" fill).
      If p == b we were in the queue at that price → recorded separately
      as an "at_touch" fill, since time priority is unknown.
    * Symmetrically for our offer with taker_side="yes" at p > a / p == a.
  Reported metrics separate aggressive from at-touch so the fill rate is
  not silently inflated by queue-position optimism.
"""

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tennis_research"))

from src.kalshi_tennis_client import KalshiTennisClient, parse_dollars  # noqa: E402

DEFAULT_LOG = ROOT / "data" / "shadow_maker" / "quotes.jsonl"
# Kept OUTSIDE tennis_research/data/ on purpose: deploy.sh rsync excludes
# any `data/` directory, so a curves file living there never reaches the VPS.
CURVES_PATH = ROOT / "tennis_research" / "empirical_curves.json"

PROP_SERIES = ("KXATPSETWINNER", "KXWTASETWINNER", "KXATPEXACTMATCH")
MATCH_SERIES = ("KXATPMATCH", "KXWTAMATCH")

# How far inside the empirical fair we would quote, each side.
QUOTE_HALF_WIDTH = 0.025
# Never quote inside this — if the book is tighter than our two-sided
# width we would be crossing ourselves into a worse price than the market.
MIN_BOOK_SPREAD = 0.04
# Skip books whose spread is absurd (stale/defensive, no real flow).
MAX_BOOK_SPREAD = 0.25


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_iso(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, tail = s.split(".", 1)
        tz = tail[tail.index("+"):] if "+" in tail else "+00:00"
        s = head + tz
    return datetime.fromisoformat(s)


# ── empirical fair pricing ───────────────────────────────────────────

def _load_curves() -> Optional[dict]:
    if not CURVES_PATH.exists():
        return None
    return json.loads(CURVES_PATH.read_text())


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def curve_fair(curves: dict, key: str, prop: str, p_fav: float) -> Optional[float]:
    seg = curves.get(key)
    if not seg or prop not in seg["curves"]:
        return None
    b = seg["curves"][prop]
    x = _logit(p_fav)
    return _sigmoid(b[0] + b[1] * x + b[2] * x * x)


# ── Kalshi helpers ───────────────────────────────────────────────────

class Tape:
    """Public trades-tape reader with simple pagination."""

    def __init__(self, client: KalshiTennisClient):
        self.c = client

    def trades_since(self, ticker: str, since: datetime,
                     max_pages: int = 5) -> List[Dict[str, Any]]:
        out, cursor, pages = [], "", 0
        while pages < max_pages:
            path = f"/markets/trades?ticker={ticker}&limit=1000"
            if cursor:
                path += f"&cursor={cursor}"
            d = self.c._get(path)
            if not d:
                break
            batch = d.get("trades", [])
            out.extend(batch)
            # tape is newest-first; stop once we're past `since`
            if batch:
                try:
                    oldest = parse_iso(batch[-1]["created_time"])
                    if oldest <= since:
                        break
                except Exception:
                    pass
            cursor = d.get("cursor") or ""
            pages += 1
            if not cursor or not batch:
                break
        res = []
        for t in out:
            try:
                if parse_iso(t["created_time"]) > since:
                    res.append(t)
            except Exception:
                continue
        return res


def match_lines(client: KalshiTennisClient) -> Dict[str, Dict[str, Any]]:
    """event-base → {p_fav, fav_code, tour} from live match markets."""
    out: Dict[str, Dict[str, Any]] = {}
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for m in client.get_open_match_markets(MATCH_SERIES):
        grouped[m["event_ticker"].split("-")[1]].append(m)
    for base, sides in grouped.items():
        if len(sides) != 2:
            continue
        quotes = []
        for m in sides:
            b, a = parse_dollars(m, "yes_bid_dollars"), parse_dollars(m, "yes_ask_dollars")
            if b is None or a is None:
                break
            quotes.append((m, (a + b) / 2))
        if len(quotes) != 2:
            continue
        (m0, mid0), (m1, mid1) = quotes
        p0 = (mid0 + (1 - mid1)) / 2  # devigged
        fav_m, p_fav = (m0, p0) if p0 >= 0.5 else (m1, 1 - p0)
        out[base] = {
            "p_fav": p_fav,
            "fav_code": fav_m["ticker"].split("-")[-1],
            "tour": "WTA" if fav_m.get("_series") == "KXWTAMATCH" else "ATP",
        }
    return out


def open_prop_markets(client: KalshiTennisClient) -> List[Dict[str, Any]]:
    out = []
    for s in PROP_SERIES:
        cursor = ""
        while True:
            path = f"/markets?series_ticker={s}&status=open&limit=1000"
            if cursor:
                path += f"&cursor={cursor}"
            d = client._get(path)
            if not d:
                break
            for m in d.get("markets", []):
                m["_series"] = s
                out.append(m)
            cursor = d.get("cursor") or ""
            if not cursor:
                break
    return out


# ── phases ───────────────────────────────────────────────────────────

def phase_quote(client, curves, log_path: Path) -> int:
    lines = match_lines(client)
    if not lines:
        return 0
    written = 0
    ts = now_utc()
    with log_path.open("a", encoding="utf-8") as f:
        for m in open_prop_markets(client):
            ticker = m["ticker"]
            ev = m["event_ticker"]
            base = ev.split("-")[1]
            ml = lines.get(base)
            if not ml:
                continue
            bid, ask = parse_dollars(m, "yes_bid_dollars"), parse_dollars(m, "yes_ask_dollars")
            if bid is None or ask is None:
                continue
            spread = ask - bid
            if not (MIN_BOOK_SPREAD <= spread <= MAX_BOOK_SPREAD):
                continue

            series = m["_series"]
            tour = ml["tour"]
            code = ticker.split("-")[-1]
            prop = None
            if series in ("KXATPSETWINNER", "KXWTASETWINNER"):
                if not ev.endswith("-1"):
                    continue  # set 1 only — clean pre-match pricing
                is_fav = code == ml["fav_code"]
                fv = curve_fair(curves, f"{tour}|all", "fav_set1", ml["p_fav"])
                if fv is None:
                    continue
                fair = fv if is_fav else 1 - fv
                prop = "set1"
            elif series == "KXATPEXACTMATCH":
                who, score = code[:3], code[3:]
                if score not in ("20", "21"):
                    continue
                is_fav = who == ml["fav_code"]
                key = ("fav_" if is_fav else "dog_") + score
                parts = {
                    k: curve_fair(curves, f"{tour}|all", k, ml["p_fav"])
                    for k in ("fav_20", "fav_21", "dog_20", "dog_21")
                }
                if any(v is None for v in parts.values()):
                    continue
                kf = ml["p_fav"] / (parts["fav_20"] + parts["fav_21"])
                kd = (1 - ml["p_fav"]) / (parts["dog_20"] + parts["dog_21"])
                scaled = {
                    "fav_20": parts["fav_20"] * kf, "fav_21": parts["fav_21"] * kf,
                    "dog_20": parts["dog_20"] * kd, "dog_21": parts["dog_21"] * kd,
                }
                fair = scaled[key]
                prop = "exact"
            if prop is None:
                continue

            our_bid = round(max(fair - QUOTE_HALF_WIDTH, 0.01), 2)
            our_ask = round(min(fair + QUOTE_HALF_WIDTH, 0.99), 2)
            # only quote where we improve the book (else we'd never be hit
            # first, and the measurement is meaningless)
            if our_bid <= bid and our_ask >= ask:
                continue

            f.write(json.dumps({
                "quote_id": f"{ticker}|{int(ts.timestamp())}",
                "ts": iso(ts), "ticker": ticker, "series": series,
                "prop": prop, "tour": tour, "p_fav": round(ml["p_fav"], 4),
                "fair": round(fair, 4),
                "our_bid": our_bid, "our_ask": our_ask,
                "book_bid": bid, "book_ask": ask, "spread": round(spread, 4),
                "status": "QUOTED",
            }) + "\n")
            written += 1
    return written


def phase_fill(client, log_path: Path) -> int:
    """Detect which resting quotes the tape would have filled."""
    records = _read(log_path)
    pending = [r for r in records if r.get("status") == "QUOTED"]
    if not pending:
        return 0
    tape = Tape(client)
    updates = []
    for r in pending:
        since = parse_iso(r["ts"])
        # give each quote a bounded life; after that it is a no-fill
        age_h = (now_utc() - since).total_seconds() / 3600.0
        trades = tape.trades_since(r["ticker"], since)
        agg_buy = agg_sell = touch_buy = touch_sell = 0.0
        for t in trades:
            p = t.get("yes_price_dollars")
            if p in (None, ""):
                continue
            p = float(p)
            qty = float(t.get("count_fp") or 0)
            side = (t.get("taker_side") or "").lower()
            if side == "no":       # a resting YES bid got hit
                if p < r["our_bid"]:
                    agg_buy += qty
                elif abs(p - r["our_bid"]) < 1e-9:
                    touch_buy += qty
            elif side == "yes":    # a resting YES offer got lifted
                if p > r["our_ask"]:
                    agg_sell += qty
                elif abs(p - r["our_ask"]) < 1e-9:
                    touch_sell += qty
        filled = (agg_buy + agg_sell) > 0
        if filled or age_h >= 6:
            r["status"] = "FILLED" if filled else "NOFILL"
            r["fill"] = {
                "agg_buy": agg_buy, "agg_sell": agg_sell,
                "touch_buy": touch_buy, "touch_sell": touch_sell,
                "checked_at": iso(now_utc()), "age_hours": round(age_h, 2),
            }
            updates.append(r)
    if updates:
        _rewrite(log_path, records)
    return len(updates)


def phase_settle(client, log_path: Path) -> int:
    records = _read(log_path)
    pending = [r for r in records if r.get("status") == "FILLED"]
    n = 0
    for r in pending:
        mk = client.get_market(r["ticker"])
        if not mk:
            continue
        result = str(mk.get("result") or "").strip().lower()
        if result not in ("yes", "no"):
            continue
        won_yes = result == "yes"
        fill = r["fill"]
        pnl = 0.0
        # bought YES at our_bid (maker, zero fee on prop series)
        if fill["agg_buy"] > 0:
            pnl += fill["agg_buy"] * ((1.0 if won_yes else 0.0) - r["our_bid"])
        # sold YES at our_ask == bought NO at 1-our_ask
        if fill["agg_sell"] > 0:
            pnl += fill["agg_sell"] * ((0.0 if won_yes else 1.0) - (1.0 - r["our_ask"]))
        r["status"] = "SETTLED"
        r["result"] = result
        r["pnl"] = round(pnl, 2)
        r["settled_at"] = iso(now_utc())
        n += 1
    if n:
        _rewrite(log_path, records)
    return n


# ── io ───────────────────────────────────────────────────────────────

def _read(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _rewrite(path: Path, records: List[Dict[str, Any]]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    tmp.replace(path)


# ── report ───────────────────────────────────────────────────────────

def report(log_path: Path) -> None:
    recs = _read(log_path)
    if not recs:
        print("shadow_maker: no quotes logged yet.")
        return
    by_status = defaultdict(int)
    for r in recs:
        by_status[r.get("status", "?")] += 1
    print("Shadow maker — tennis prop quoting measurement")
    print("=" * 55)
    print(f"  quotes logged : {len(recs)}")
    for k in ("QUOTED", "NOFILL", "FILLED", "SETTLED"):
        print(f"    {k:<8}: {by_status.get(k,0)}")

    resolved = [r for r in recs if r.get("status") in ("FILLED", "NOFILL", "SETTLED")]
    if resolved:
        filled = [r for r in resolved if r.get("status") in ("FILLED", "SETTLED")]
        print(f"\n  fill rate (aggressive only): {len(filled)}/{len(resolved)} "
              f"= {100*len(filled)/len(resolved):.1f}%")
        touch = sum(1 for r in resolved
                    if r.get("fill", {}).get("touch_buy", 0)
                    + r.get("fill", {}).get("touch_sell", 0) > 0)
        print(f"  additional at-touch (queue-position unknown): {touch}")

    settled = [r for r in recs if r.get("status") == "SETTLED"]
    if settled:
        tot = sum(r["pnl"] for r in settled)
        contracts = sum(r["fill"]["agg_buy"] + r["fill"]["agg_sell"] for r in settled)
        print(f"\n  settled fills : {len(settled)}  contracts={contracts:.0f}")
        print(f"  P&L           : ${tot:+.2f}")
        if contracts:
            print(f"  per contract  : {100*tot/contracts:+.2f}c   "
                  "(maker fee on prop series = 0)")
        print("\n  by prop:")
        agg = defaultdict(lambda: [0, 0.0, 0.0])
        for r in settled:
            a = agg[r["prop"]]
            a[0] += 1
            a[1] += r["pnl"]
            a[2] += r["fill"]["agg_buy"] + r["fill"]["agg_sell"]
        for prop, (n, pnl, c) in sorted(agg.items()):
            per = f"{100*pnl/c:+.2f}c" if c else "n/a"
            print(f"    {prop:<8} n={n:<4} pnl=${pnl:+8.2f}  {per}/contract")
    else:
        print("\n  no settled fills yet — run `cycle` periodically.")
    print("\n  NOTE: measurement only. No orders placed, no credentials used.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Tennis prop shadow-maker measurement")
    ap.add_argument("command", choices=["cycle", "report"])
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    args = ap.parse_args()

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if args.command == "report":
        report(log_path)
        return 0

    curves = _load_curves()
    if not curves:
        print(f"shadow_maker: missing curves at {CURVES_PATH}", file=sys.stderr)
        return 1
    client = KalshiTennisClient()

    q = phase_quote(client, curves, log_path)
    f = phase_fill(client, log_path)
    s = phase_settle(client, log_path)
    print(f"shadow_maker cycle: quoted={q} fill_checked={f} settled={s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
