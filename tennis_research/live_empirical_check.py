"""Compare live Kalshi prop quotes to EMPIRICAL fair prices.

Fair = smoothed 2022-26 conditional frequency given the devigged Kalshi
match line (per tour; clay curves for the current European clay swing).
Exact-score fairs are renormalized so fav_20+fav_21 = p_fav (coherence
with the match line). Edges are net of taker fees at the quoted price.

Usage: python3 live_empirical_check.py <snapshot.json> <curves.json>
"""
import json
import sys
from analyze_live import load_matches, fnum, mid
from empirical_fair import fair
from tennis_pricer import kalshi_taker_fee

snap_path, curves_path = sys.argv[1], sys.argv[2]
CURVES = json.load(open(curves_path))
SURF = "clay"  # July European swing; Los Cabos is hard but minor


def emp_fair(tour, prop, p_fav):
    c = CURVES[f"{tour}|{SURF}"]["curves"]
    return fair(c, prop, p_fav)


def coherent_exacts(tour, p_fav):
    """Renormalized exact-score fairs (favorite perspective)."""
    f20 = emp_fair(tour, "fav_20", p_fav)
    f21 = emp_fair(tour, "fav_21", p_fav)
    d21 = emp_fair(tour, "dog_21", p_fav)
    d20 = emp_fair(tour, "dog_20", p_fav)
    kf = p_fav / (f20 + f21)
    kd = (1 - p_fav) / (d20 + d21)
    return {"fav_20": f20 * kf, "fav_21": f21 * kf,
            "dog_21": d21 * kd, "dog_20": d20 * kd}


matches = load_matches(snap_path)
rows = []
for (tour, base), mm in sorted(matches.items()):
    if len(mm["match"]) != 2:
        continue
    (sfx_a, ma), (sfx_b, mb) = sorted(mm["match"].items())
    pa_mid, pb_mid = mid(ma), mid(mb)
    if pa_mid is None or pb_mid is None:
        continue
    p_a = (pa_mid + (1 - pb_mid)) / 2
    if not 0.03 < p_a < 0.97:
        continue
    fav_is_a = p_a >= 0.5
    p_fav = p_a if fav_is_a else 1 - p_a
    fav_sfx = sfx_a if fav_is_a else sfx_b
    ex = coherent_exacts(tour, p_fav)

    def check(prop_desc, fair_v, mkt):
        b, a = fnum(mkt, "yes_bid_dollars"), fnum(mkt, "yes_ask_dollars")
        if b is None or a is None or a - b > 0.25 or a >= 0.99 or b <= 0.01:
            return
        row = {"tour": tour, "base": base, "p_fav": round(p_fav, 3),
               "prop": prop_desc, "fair": round(fair_v, 3), "bid": b, "ask": a,
               "ticker": mkt["ticker"],
               "bid_sz": fnum(mkt, "yes_bid_size_fp") or 0,
               "ask_sz": fnum(mkt, "yes_ask_size_fp") or 0}
        if fair_v > a:
            row["side"], row["net"] = "buy_yes", fair_v - a - kalshi_taker_fee(a)
            row["size"] = row["ask_sz"]
        elif fair_v < b:
            row["side"], row["net"] = "buy_no", b - fair_v - kalshi_taker_fee(b)
            row["size"] = row["bid_sz"]
        else:
            row["side"], row["net"], row["size"] = "none", max(fair_v - a, b - fair_v), 0
        rows.append(row)

    for sfx, mkt in mm["set1"].items():
        f = emp_fair(tour, "fav_set1", p_fav)
        check("set1_" + ("fav" if sfx == fav_sfx else "dog"),
              f if sfx == fav_sfx else 1 - f, mkt)
    for sfx, mkt in mm["exact"].items():
        who, score = sfx[:3], sfx[3:]
        key = ("fav_" if who == fav_sfx else "dog_") + score
        check("exact_" + key, ex[key], mkt)
    for line, mkt in mm["totals"].items():
        k = f"over_{line}"
        c = CURVES[f"{tour}|{SURF}"]["curves"]
        if k in c:
            check(f"over_{line}", emp_fair(tour, k, p_fav), mkt)
    for (who, line), mkt in mm["spreads"].items():
        k = f"fav_sp_{line}"
        c = CURVES[f"{tour}|{SURF}"]["curves"]
        if k not in c:
            continue
        f = emp_fair(tour, k, p_fav)
        # dog -L.5 spread YES = dog wins by > L; approx via 1 - P(fav > -L)
        # only price the favorite-side spreads to stay on solid ground
        if who == fav_sfx:
            check(f"sp_fav_{line}", f, mkt)

json.dump(rows, open("live_empirical_findings.json", "w"), indent=1)
pos = [r for r in rows if r["side"] != "none" and r["net"] > 0]
print(f"quotes checked: {len(rows)}; net-positive edges: {len(pos)}\n")
by = {}
for r in rows:
    k = r["prop"].split("_")[0] if not r["prop"].startswith("exact") else "exact"
    by.setdefault(k, []).append(r)
for k, v in sorted(by.items()):
    gaps = sorted(r["fair"] - (r["bid"] + r["ask"]) / 2 for r in v)
    print(f"{k:<8} n={len(v):>3}  median(fair-mid)={gaps[len(gaps)//2]*100:+.1f}c")
print()
for r in sorted(pos, key=lambda r: -r["net"]):
    print(f"{r['side']:<8}{r['prop']:<14}{r['tour']} {r['base']:<15} p_fav={r['p_fav']:.2f} "
          f"fair={r['fair']:.3f} bid/ask={r['bid']:.2f}/{r['ask']:.2f} "
          f"net={r['net']*100:+.1f}c size={r['size']:.0f}")
