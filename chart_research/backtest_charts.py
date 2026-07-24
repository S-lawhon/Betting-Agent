"""
chart_research/backtest_charts.py
─────────────────────────────────
P-025 settled-data backtest. Joins the cached Kalshi candles (pull_charts.py)
with the reconstructed free-data signal (pull_reference.py) and simulates the
HONEST tradeable strategy under a thin-book fill model.

Honest-strategy rule (the key methodological point): at each decision time we
buy YES on the SIGNAL LEADER — the song/album the free public data points at
ex-ante — NOT the eventual Billboard winner (which is only known ex-post).
On weeks the free signal is wrong, we buy a strike that settles to $0 and eat
the full stake. This is what a trader reading HITS/kworb would actually have
done.

Fill realism (per the guardrails):
  entry price  = yes_ask.close_dollars at the decision hour (taker crosses)
  spread proxy = yes_ask - yes_bid at that hour
  fillable size = the decision-hour volume_fp (never credit more than traded)
  taker fee    = 0.07 * P * (1-P) per contract   (quadratic series, maker=0)
  pnl/contract = settlement_value(signal_leader) - entry - fee
Weeks where the entry hour had zero traded volume are flagged (untradeable).

Trade trigger: (signal_implied - entry_ask) > fee + 0.03, with signal_implied
fixed at 1.0 (max confidence in the free-data leader). So we take any signal
leader priced below ~0.96 — i.e. wherever a visible gap exists.

Stats are clustered by chart-week (one week = one observation); the bootstrap
resamples weeks, not contracts.
"""
from __future__ import annotations

import json
import math
import os
import random
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CANDLE_DIR = os.path.join(DATA, "candles")

WIDE_MARGIN = 0.20          # contested-week threshold (HITS/kworb margin)
GAP_TRIGGER = 0.03          # take YES when gap > fee + this
SIGNAL_IMPLIED = 1.0        # max confidence in the free-data leader
DESIRED_CONTRACTS = 10_000  # target size before the honest volume cap


def taker_fee(p: float) -> float:
    return 0.07 * p * (1.0 - p)


def load(path):
    return json.load(open(path))


def norm(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def title_match(a: str, b: str) -> bool:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    wa = set(na.split()) - {"the", "a", "of", "for", "in", "to", "and", "part", "ii", "i"}
    wb = set(nb.split()) - {"the", "a", "of", "for", "in", "to", "and", "part", "ii", "i"}
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.6


def candle_at(candles, epoch):
    """Last hourly candle ending at/before epoch (+1h grace). Returns
    (ask, bid, volume, ts) using close_dollars quotes."""
    best = None
    for c in candles:
        if c["end_period_ts"] <= epoch + 3600:
            ya = c.get("yes_ask", {}).get("close_dollars")
            yb = c.get("yes_bid", {}).get("close_dollars")
            best = (float(ya) if ya else None,
                    float(yb) if yb else None,
                    float(c.get("volume_fp") or 0),
                    c["end_period_ts"])
    return best


def find_strike(event, leader_title):
    """Match the signal leader to a Kalshi strike; return (strike, candles)."""
    for s in event["strikes"]:
        if title_match(s["sub"], leader_title):
            p = os.path.join(CANDLE_DIR, s["ticker"] + ".json")
            candles = load(p)["candles"] if os.path.exists(p) else []
            return s, candles
    return None, []


def simulate():
    events = {e["event"]: e for e in load(os.path.join(DATA, "events.json"))}
    ref = load(os.path.join(DATA, "reference.json"))
    trades = []                      # one per (week, decision_time)
    for r in ref:
        ev = events.get(r["event"])
        if not ev:
            continue
        for t in ("T_mid", "T_close", "T_late"):
            # T_late uses the T_close signal (same public data, book still open)
            sig = r.get(t) if t != "T_late" else r.get("T_close")
            if not sig or not sig.get("ok"):
                continue
            leader = sig["leader"]
            strike, candles = find_strike(ev, leader)
            rec = {"event": r["event"], "series": r["series"], "decision": t,
                   "chart_sat": r["chart_sat"], "winner": r["winner"],
                   "signal_leader": leader, "margin": sig.get("margin"),
                   "wide": (sig.get("margin") or 0) >= WIDE_MARGIN,
                   "signal_correct": sig.get("signal_correct")}
            if strike is None:
                rec.update({"tradeable": False, "reason": "leader not a Kalshi strike"})
                trades.append(rec); continue
            c = candle_at(candles, ev["decisions"][t]["epoch"])
            if not c or c[0] is None:
                rec.update({"tradeable": False, "reason": "no ask quote at decision hour",
                            "strike_sv": strike["sv"]})
                trades.append(rec); continue
            ask, bid, vol, ts = c
            fee = taker_fee(ask)
            gap = SIGNAL_IMPLIED - ask
            sv = strike["sv"]                # actual settlement of THIS strike
            pnl_ct = sv - ask - fee          # per contract, net of taker fee
            size = min(DESIRED_CONTRACTS, vol)
            rec.update({
                "tradeable": True,
                "ask": ask, "bid": bid, "spread": (ask - bid) if bid else None,
                "hour_volume": vol, "fee": fee, "gap": gap,
                "strike_sv": sv, "pnl_per_ct": pnl_ct,
                "triggered": gap > fee + GAP_TRIGGER,
                "fill_size": size,
                "capital": ask * size,
                "ev_usd": pnl_ct * size,
                "zero_volume": vol == 0,
            })
            trades.append(rec)
    with open(os.path.join(DATA, "trades_sim.json"), "w") as f:
        json.dump(trades, f, indent=1)
    return trades


# ── metrics ────────────────────────────────────────────────────────────────

def bootstrap_ci(week_values, n=5000, seed=42):
    """Week-clustered bootstrap CI of the mean. week_values: list of per-week
    mean pnl_per_ct (¢). Returns (mean, lo, hi) in cents."""
    if not week_values:
        return None, None, None
    rng = random.Random(seed)
    k = len(week_values)
    means = []
    for _ in range(n):
        s = [week_values[rng.randrange(k)] for _ in range(k)]
        means.append(sum(s) / k)
    means.sort()
    mean = sum(week_values) / k
    return mean, means[int(0.025 * n)], means[int(0.975 * n)]


def summarize(trades):
    lines = []

    def acc(subset, label):
        graded = [t for t in subset if t.get("signal_correct") is not None]
        correct = sum(1 for t in graded if t["signal_correct"])
        n = len(graded)
        lines.append(f"  {label:34} {correct}/{n} "
                     f"= {100*correct/n:.0f}%" if n else f"  {label:34} n/a")

    # ---- signal accuracy (dedup by week; use T_mid & T_close) ----
    for series in ("KXTOPSONG", "KXTOPALBUM"):
        for t in ("T_mid", "T_close"):
            sub = [x for x in trades if x["series"] == series and x["decision"] == t]
            acc(sub, f"{series} {t} (all weeks)")
            acc([x for x in sub if x["wide"]], f"{series} {t} (wide-margin >=20%)")

    lines.append("")
    # ---- gap / PnL / capacity by series x decision, triggered trades ----
    lines.append("PnL & capacity (triggered trades only; per-contract in cents):")
    header = ("  %-10s %-8s %5s %6s %7s %8s %9s %9s"
              % ("series", "decision", "n", "hit%", "cts/ct", "medVol", "cap$/wk", "ev$/wk"))
    lines.append(header)
    for series in ("KXTOPSONG", "KXTOPALBUM"):
        for t in ("T_mid", "T_close", "T_late"):
            sub = [x for x in trades if x["series"] == series and x["decision"] == t
                   and x.get("triggered")]
            if not sub:
                lines.append("  %-10s %-8s   (no triggered trades)" % (series, t))
                continue
            # cluster: one value per week
            byweek = defaultdict(list)
            for x in sub:
                byweek[x["event"]].append(x)
            wk_pnl = [sum(y["pnl_per_ct"] for y in v) / len(v) * 100 for v in byweek.values()]
            hit = sum(1 for x in sub if x["strike_sv"] > 0) / len(sub)
            medvol = sorted(x["hour_volume"] for x in sub)[len(sub) // 2]
            cap = sum(x["capital"] for x in sub) / len(byweek)
            ev = sum(x["ev_usd"] for x in sub) / len(byweek)
            m, lo, hi = bootstrap_ci(wk_pnl)
            lines.append("  %-10s %-8s %5d %5.0f%% %6.1f %8.0f %9.0f %9.0f   CI[%.1f,%.1f]"
                         % (series, t, len(sub), 100 * hit, m, medvol, cap, ev, lo, hi))

    lines.append("")
    # ---- contested split at T_close (songs, where capacity lives) ----
    lines.append("Contested-week split — KXTOPSONG @ T_close (triggered):")
    for wide, lab in ((True, "wide-margin >=20%"), (False, "contested <20%")):
        sub = [x for x in trades if x["series"] == "KXTOPSONG" and x["decision"] == "T_close"
               and x.get("triggered") and x["wide"] == wide]
        if not sub:
            lines.append(f"  {lab:20} (no triggered trades)"); continue
        byweek = defaultdict(list)
        for x in sub:
            byweek[x["event"]].append(x)
        wk_pnl = [sum(y["pnl_per_ct"] for y in v) / len(v) * 100 for v in byweek.values()]
        hit = sum(1 for x in sub if x["strike_sv"] > 0) / len(sub)
        m, lo, hi = bootstrap_ci(wk_pnl)
        lines.append("  %-20s n=%d hit=%.0f%% mean=%.1f c/ct CI[%.1f,%.1f]"
                     % (lab, len(sub), 100 * hit, m, lo, hi))

    lines.append("")
    # ---- zero-volume gap weeks (visible gap, no tradeable size) ----
    zv = [x for x in trades if x.get("tradeable") is False
          and x.get("reason") == "no ask quote at decision hour"]
    novol = [x for x in trades if x.get("zero_volume")]
    lines.append(f"Untradeable flags: {len(zv)} (no quote at decision hour), "
                 f"{len(novol)} (zero volume in decision hour)")
    return "\n".join(lines)


if __name__ == "__main__":
    trades = simulate()
    print(summarize(trades))
