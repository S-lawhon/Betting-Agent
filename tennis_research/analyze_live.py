"""Cross-sectional analysis of live Kalshi tennis markets vs model.

For every match with prop coverage in a snapshot (open_markets.json):
  1. read the match-winner mid (devigged across both sides' books)
  2. invert to serve-point probs (pa, pb) given a surface serve level
  3. price set winner / exact score / totals / spreads from the engine
  4. compare model fair value to quoted bid/ask, net of taker fees
  5. run model-free consistency checks (adding-up, complements, monotone)

Usage: python3 analyze_live.py <path-to-open_markets.json>
"""
import json
import re
import sys
from tennis_pricer import price_props, implied_holds, kalshi_taker_fee

AVG_SPW = {"ATP": 0.635, "WTA": 0.565}  # July = mostly clay; sensitivity ±0.03


def fnum(m, k):
    v = m.get(k)
    return float(v) if v not in (None, "") else None


def mid(m):
    b, a = fnum(m, "yes_bid_dollars"), fnum(m, "yes_ask_dollars")
    if b is None or a is None:
        return None
    return (b + a) / 2


def load_matches(snap_path):
    d = json.load(open(snap_path))
    matches = {}

    def M(base, tour):
        key = (tour, base)
        if key not in matches:
            matches[key] = {"tour": tour, "base": base, "match": {},
                            "set1": {}, "set2": {}, "exact": {},
                            "totals": {}, "spreads": {}}
        return matches[key]

    for s, mkts in d.items():
        tour = "WTA" if s.startswith("KXWTA") else "ATP"
        for m in mkts:
            ev = m["event_ticker"]
            base = ev.split("-")[1]
            suffix = m["ticker"].split("-")[-1]
            if s in ("KXATPMATCH", "KXWTAMATCH"):
                M(base, tour)["match"][suffix] = m
            elif s in ("KXATPSETWINNER", "KXWTASETWINNER"):
                setno = ev.split("-")[-1]
                slot = "set1" if setno == "1" else "set2"
                M(base, tour)[slot][suffix] = m
            elif s == "KXATPEXACTMATCH":
                M(base, tour)["exact"][suffix] = m
            elif s in ("KXATPGTOTAL",):
                line = fnum(m, "floor_strike")
                if line:
                    M(base, tour)["totals"][line] = m
            elif s in ("KXATPGSPREAD",):
                sub = m.get("yes_sub_title") or ""
                mm = re.search(r"-([\d.]+) games", sub)
                if mm:
                    M(base, tour)["spreads"][(suffix[:3], float(mm.group(1)))] = m
    return matches


def collect_quotes(mm, sfx_a):
    """Gather (prop_kind, param, market, weight) for all sanely-quoted props."""
    quotes = []

    def ok(mkt):
        b, a = fnum(mkt, "yes_bid_dollars"), fnum(mkt, "yes_ask_dollars")
        if b is None or a is None:
            return False
        if a - b > 0.25 or b <= 0.01 or a >= 0.99:
            return False
        return True

    for sfx, mkt in mm["set1"].items():
        if ok(mkt):
            quotes.append(("set1", sfx == sfx_a, mkt))
    for sfx, mkt in mm["set2"].items():
        if ok(mkt):
            quotes.append(("set2", sfx == sfx_a, mkt))
    for sfx, mkt in mm["exact"].items():
        if ok(mkt):
            quotes.append(("exact", (sfx[:3] == sfx_a, sfx[3:]), mkt))
    for line, mkt in mm["totals"].items():
        if ok(mkt):
            quotes.append(("total", line, mkt))
    for (who, line), mkt in mm["spreads"].items():
        if ok(mkt):
            quotes.append(("spread", (who == sfx_a, line), mkt))
    return quotes


def fair_of(pr, kind, param):
    if kind == "set1":
        return pr["set1_a"] if param else 1 - pr["set1_a"]
    if kind == "set2":
        return pr["set2_a"] if param else 1 - pr["set2_a"]
    if kind == "exact":
        is_a, score = param
        if is_a:
            return pr["exact_20"] if score == "20" else pr["exact_21"]
        return pr["exact_02"] if score == "20" else pr["exact_12"]
    if kind == "total":
        return pr["total_games_over"].get(param)
    if kind == "spread":
        is_a, line = param
        v = pr["spread_a"].get(line)
        if v is None:
            return None
        if is_a:
            return v
        vb = pr["spread_a"].get(-line)
        return 1 - vb if vb is not None else None
    return None


def fit_holds(p_match_a, quotes, tour):
    """Best-fit (pa, pb) to the match line (hard constraint) + all prop
    quotes (least squares on mids, weighted by 1/spread)."""
    best, best_loss, best_pr = None, float("inf"), None
    for s100 in range(112, 145, 2):  # pa+pb in [1.12, 1.44]
        s = s100 / 100
        pa, pb = implied_holds(p_match_a, best_of=3, avg_spw=s / 2)
        pr = price_props(pa, pb)
        loss = 0.0
        for kind, param, mkt in quotes:
            f = fair_of(pr, kind, param)
            if f is None:
                continue
            b, a = fnum(mkt, "yes_bid_dollars"), fnum(mkt, "yes_ask_dollars")
            m, w = (b + a) / 2, 1.0 / max(a - b, 0.02)
            loss += w * (f - m) ** 2
        if loss < best_loss:
            best, best_loss, best_pr = (pa, pb), loss, pr
    return best[0], best[1], best_pr


def analyze(matches, fee_rate=0.07):
    findings = []
    for (tour, base), mm in sorted(matches.items()):
        if len(mm["match"]) != 2:
            continue
        (sfx_a, ma), (sfx_b, mb) = sorted(mm["match"].items())
        pa_mid, pb_mid = mid(ma), mid(mb)
        if pa_mid is None or pb_mid is None:
            continue
        overround = pa_mid + pb_mid - 1.0
        p_match_a = (pa_mid + (1 - pb_mid)) / 2  # devigged

        if p_match_a < 0.02 or p_match_a > 0.98:
            continue
        quotes = collect_quotes(mm, sfx_a)
        pa, pb, pr = fit_holds(p_match_a, quotes, tour)

        rec = {"tour": tour, "base": base, "p_match_a": p_match_a,
               "overround_match": overround, "pa": pa, "pb": pb,
               "side_a": sfx_a, "side_b": sfx_b, "edges": [],
               "checks": {}}

        def compare(desc, fair, mkt):
            if mkt is None:
                return
            b, a = fnum(mkt, "yes_bid_dollars"), fnum(mkt, "yes_ask_dollars")
            if b is None or a is None:
                return
            row = {"prop": desc, "fair": round(fair, 4), "bid": b, "ask": a,
                   "bid_sz": fnum(mkt, "yes_bid_size_fp"),
                   "ask_sz": fnum(mkt, "yes_ask_size_fp"),
                   "ticker": mkt["ticker"]}
            # edge net of taker fee, per contract
            if fair > a:
                row["side"] = "buy_yes"
                row["gross_edge"] = fair - a
                row["net_edge"] = fair - a - kalshi_taker_fee(a, 1, fee_rate)
                row["size"] = row["ask_sz"]
            elif fair < b:
                row["side"] = "buy_no"
                row["gross_edge"] = b - fair
                row["net_edge"] = b - fair - kalshi_taker_fee(b, 1, fee_rate)
                row["size"] = row["bid_sz"]
            else:
                row["side"] = "none"
                row["gross_edge"] = 0.0
                row["net_edge"] = max(fair - a, b - fair)  # negative
            rec["edges"].append(row)

        # --- model-priced props (only sane quotes, same set used in the fit)
        for kind, param, mkt in quotes:
            fair = fair_of(pr, kind, param)
            if fair is not None:
                compare(f"{kind}[{param}]", fair, mkt)

        # --- model-free consistency checks
        ex = {k: mid(v) for k, v in mm["exact"].items()}
        if len(ex) == 4 and all(v is not None for v in ex.values()):
            rec["checks"]["sum_exact_mids"] = sum(ex.values())
            a_scores = [v for k, v in ex.items() if k.startswith(sfx_a)]
            rec["checks"]["exact_vs_match"] = sum(a_scores) - p_match_a
        s1 = {k: mid(v) for k, v in mm["set1"].items()}
        if len(s1) == 2 and all(v is not None for v in s1.values()):
            rec["checks"]["sum_set1_mids"] = sum(s1.values())
        # complement arb on the match itself (two books)
        ask_a, ask_b = fnum(ma, "yes_ask_dollars"), fnum(mb, "yes_ask_dollars")
        if ask_a and ask_b:
            rec["checks"]["match_complement_ask_sum"] = ask_a + ask_b
        findings.append(rec)
    return findings


if __name__ == "__main__":
    snap = sys.argv[1] if len(sys.argv) > 1 else "kalshi_snap/open_markets.json"
    matches = load_matches(snap)
    findings = analyze(matches)
    print(f"analyzed {len(findings)} matches\n")
    n_edge = 0
    for r in findings:
        pos = [e for e in r["edges"] if e.get("net_edge", 0) > 0 and e["side"] != "none"]
        line = (f"{r['tour']} {r['base']}: P(A)={r['p_match_a']:.3f} "
                f"pa={r['pa']:.3f} pb={r['pb']:.3f} "
                f"props={len(r['edges'])} net+_edges={len(pos)}")
        print(line)
        for e in pos:
            n_edge += 1
            print(f"    {e['side']:<8}{e['prop']:<22} fair={e['fair']:.3f} "
                  f"bid/ask={e['bid']:.2f}/{e['ask']:.2f} "
                  f"net_edge={e['net_edge']*100:+.1f}c size={e['size']:.0f}")
        for k, v in r["checks"].items():
            tag = ""
            if k == "sum_exact_mids" and abs(v - 1) > 0.05:
                tag = "  <-- inconsistent"
            if k == "exact_vs_match" and abs(v) > 0.04:
                tag = "  <-- inconsistent"
            print(f"      check {k} = {v:.3f}{tag}")
    print(f"\ntotal net-positive edges at quoted prices: {n_edge}")
    json.dump(findings, open("live_findings.json", "w"), indent=1)
    print("wrote live_findings.json")
