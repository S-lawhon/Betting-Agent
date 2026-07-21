"""Q1 — model-free cross-contract consistency scan on live snapshots.

Logical constraints between independently-traded books of the same game
(event key = date+time+teams, shared across all series):

  L1 ladder:      P(>=N+1) <= P(>=N)          arb iff ask(N) < bid(N+1)
  L2 exhaustive:  F5 {home,away,tie} sum to 1  arb iff sum(asks) < 1 or sum(bids) > 1
                  ML {home,away} likewise
  L3 implication: A => B means P(B) >= P(A)    arb iff ask(B) < bid(A)
      SPREAD team N   => GAME team wins
      SPREAD team N   => TEAMTOTAL team >= N
      TEAMTOTAL t >= N => TOTAL >= N

Sides count as quoted only when displayed size > 0 (empty-book placeholders).
Edge reported gross; net edge subtracts taker fee 0.07*P*(1-P) per leg
(worst case: both legs taken).

Usage: python consistency_scan.py [data/live/snapshots_YYYYMMDD.jsonl]
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "live"

TKRE = re.compile(r"^(KXMLB[A-Z]+)-([0-9]{2}[A-Z]{3}[0-9]{6}[A-Z]+)-?(.*)$")


def fee(p):
    return 0.07 * p * (1 - p)


def parse(tk):
    m = TKRE.match(tk)
    if not m:
        return None
    series, game, suffix = m.groups()
    return series, game, suffix


def load_cycles(path):
    """Yield (ts, {ticker: (yb, ya, ybs, yas)}) per snapshot cycle."""
    cur_ts, cur = None, {}
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r["t"] != "mkt":
                continue
            if r["ts"] != cur_ts:
                if cur:
                    yield cur_ts, cur
                cur_ts, cur = r["ts"], {}
            cur[r["tk"]] = (r.get("yb"), r.get("ya"), r.get("ybs"), r.get("yas"))
    if cur:
        yield cur_ts, cur


def real_bid(q):
    return q[0] if q and q[0] and q[2] and q[2] > 0 else None


def real_ask(q):
    return q[1] if q and q[1] and q[3] and q[3] > 0 and q[1] < 1 else None


def check_pair(quotes, a_tk, b_tk, kind, viols, ts):
    """A => B. Arb iff ask(B) < bid(A). Size = min displayed."""
    qa, qb = quotes.get(a_tk), quotes.get(b_tk)
    if not qa or not qb:
        return
    bid_a, ask_b = real_bid(qa), real_ask(qb)
    if bid_a is None or ask_b is None:
        return
    gross = bid_a - ask_b
    if gross <= 0:
        return
    net = gross - fee(bid_a) - fee(ask_b)  # both legs taker, worst case
    viols.append({"ts": ts, "kind": kind, "buy": b_tk, "sell": a_tk,
                  "gross_c": round(100 * gross, 1), "net_c": round(100 * net, 1),
                  "size": min(qa[2], qb[3])})


def scan_cycle(ts, quotes):
    viols = []
    ladders = defaultdict(dict)   # (series, game, entity) -> {n: tk}
    games = defaultdict(dict)     # game -> {role: tk}
    for tk in quotes:
        p = parse(tk)
        if not p:
            continue
        series, game, suffix = p
        m = re.match(r"^([A-Z]*?)(\d+)$", suffix)
        if series in ("KXMLBTOTAL", "KXMLBTEAMTOTAL", "KXMLBSPREAD",
                      "KXMLBKS", "KXMLBHIT", "KXMLBTB", "KXMLBHRR",
                      "KXMLBHR", "KXMLBSB") and m:
            entity, n = m.group(1), int(m.group(2))
            ladders[(series, game, entity)][n] = tk
        # KS/player ladders have PLAYERID-N suffixes
        m2 = re.match(r"^(.+)-(\d+)$", suffix)
        if m2 and series in ("KXMLBKS", "KXMLBHIT", "KXMLBTB", "KXMLBHRR",
                             "KXMLBHR", "KXMLBSB"):
            ladders[(series, game, m2.group(1))][int(m2.group(2))] = tk
        if series in ("KXMLBGAME", "KXMLBF5"):
            games[(series, game)][suffix] = tk

    # L1 ladder monotonicity
    for (series, game, entity), lad in ladders.items():
        ns = sorted(lad)
        for n1, n2 in zip(ns, ns[1:]):
            if n2 == n1 + 1 or True:  # any higher strike implies the lower
                check_pair(quotes, lad[n2], lad[n1],
                           f"ladder:{series}", viols, ts)

    # L2 exhaustive sets
    for (series, game), mkts in games.items():
        need = 2 if series == "KXMLBGAME" else 3
        if len(mkts) != need:
            continue
        asks = [real_ask(quotes[t]) for t in mkts.values()]
        bids = [real_bid(quotes[t]) for t in mkts.values()]
        if all(a is not None for a in asks):
            s = sum(asks)
            if s < 1:
                net = (1 - s) - sum(fee(a) for a in asks)
                viols.append({"ts": ts, "kind": f"exhaustive-buy:{series}",
                              "buy": "+".join(mkts.values()), "sell": "",
                              "gross_c": round(100 * (1 - s), 1),
                              "net_c": round(100 * net, 1), "size": None})
        if all(b is not None for b in bids):
            s = sum(bids)
            if s > 1:
                net = (s - 1) - sum(fee(b) for b in bids)
                viols.append({"ts": ts, "kind": f"exhaustive-sell:{series}",
                              "buy": "", "sell": "+".join(mkts.values()),
                              "gross_c": round(100 * (s - 1), 1),
                              "net_c": round(100 * net, 1), "size": None})

    # L3 cross-series implications
    for (series, game, entity), lad in ladders.items():
        if series == "KXMLBSPREAD":
            gm = games.get(("KXMLBGAME", game), {})
            for n, tk in lad.items():
                if entity in gm:  # SPREAD team N => GAME team wins
                    check_pair(quotes, tk, gm[entity], "spread=>ml", viols, ts)
                tt = ladders.get(("KXMLBTEAMTOTAL", game, entity), {})
                if n in tt:       # win by >=N => scored >=N
                    check_pair(quotes, tk, tt[n], "spread=>teamtotal", viols, ts)
        if series == "KXMLBTEAMTOTAL":
            tot = ladders.get(("KXMLBTOTAL", game, ""), {})
            for n, tk in lad.items():
                if n in tot:      # team >=N => total >=N
                    check_pair(quotes, tk, tot[n], "teamtotal=>total", viols, ts)
    return viols


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        sorted(DATA.glob("snapshots_*.jsonl"))[-1]
    print(f"scanning {path}")
    all_v, cycles = [], 0
    for ts, quotes in load_cycles(path):
        cycles += 1
        all_v.extend(scan_cycle(ts, quotes))
    print(f"{cycles} cycles scanned; {len(all_v)} gross violations")
    net = [v for v in all_v if v["net_c"] > 0]
    print(f"{len(net)} NET-positive after taker fees both legs")
    from collections import Counter
    print("\nby kind (gross):", dict(Counter(v['kind'] for v in all_v)))
    print("by kind (net+):", dict(Counter(v['kind'] for v in net)))
    for v in sorted(net, key=lambda v: -v["net_c"])[:25]:
        print(f"  {v['ts']} {v['kind']:24s} net={v['net_c']:5.1f}c "
              f"size={v['size']} buy={v['buy'][:60]} sell={v['sell'][:60]}")


if __name__ == "__main__":
    main()
