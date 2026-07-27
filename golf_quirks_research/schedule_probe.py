"""
golf_quirks_research/schedule_probe.py
──────────────────────────────────────
P-022 blocker research: can the true round-end (Kalshi's *rewritten*
``close_time``) be reconstructed from an EXTERNAL schedule, well enough for
a [12h, 24h] placement window?

Two data sources, joined:

  A. Kalshi settled round-leader markets (``data/settled_meta.jsonl``, already
     cached).  On a SETTLED market ``close_time`` has been rewritten to the
     real early-close stamp, so this is ground truth.  ``open_time`` is real in
     both states, so the open->close span is measurable here too (the
     "secondary" fallback the task asks about).

  B. ESPN's public golf scoreboard, one call per tour per season:
       https://site.api.essn.com/apis/site/v2/sports/golf/{league}/scoreboard?dates=YYYY
     Gives ``date`` (tournament start) and ``endDate`` per event, at day
     granularity stamped 04:00Z.

The join key is Kalshi's ``product_metadata.competition`` (the human-readable
tournament name) against ESPN's ``name``.

Outputs ``golf_quirks_research/schedule_probe.json`` — every settled event with
its true close, its matched ESPN event, the naive predicted close, and the
residual.  Nothing here is used by the pod; ``src/golf_schedule.py`` is.

Run:  python3 -m golf_quirks_research.schedule_probe
"""
from __future__ import annotations

import collections
import json
import os
import statistics
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.kalshi_public import KalshiPublic

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
META = os.path.join(DATA, "settled_meta.jsonl")
EVENT_CACHE = os.path.join(DATA, "leader_event_meta.json")
ESPN_CACHE = os.path.join(DATA, "espn_schedule.json")
OUT = os.path.join(HERE, "schedule_probe.json")

# Kalshi series prefix -> ESPN golf league slug.
TOUR_LEAGUE = {
    "KXPGA": "pga",
    "KXLPGA": "lpga",
    "KXCHAMPTOUR": "champions-tour",
    "KXDPWORLDTOUR": "eur",
    "KXLIV": "liv",
}

ESPN_URL = ("https://site.api.espn.com/apis/site/v2/sports/golf/"
            "{league}/scoreboard?dates={year}")


def iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def tour_of(series: str) -> Optional[str]:
    """Longest-prefix match, so KXLPGA... never resolves to KXPGA."""
    best = None
    for pref in TOUR_LEAGUE:
        if series.startswith(pref) and (best is None or len(pref) > len(best)):
            best = pref
    return best


def round_of(series: str) -> Optional[int]:
    for n in (1, 2, 3, 4):
        if f"R{n}LEAD" in series:
            return n
    return None


# ── A. Kalshi ground truth ───────────────────────────────────────────

def load_settled_events() -> Dict[str, Dict[str, Any]]:
    """event_ticker -> {series, open, close, n, ...}.

    Within an event, markets do NOT all close together, and BOTH obvious
    summary statistics are wrong:

    * ``min`` picks up **withdrawals** — a competitor who pulls out has their
      own market closed days early (KXPGAR1LEAD-3MO26: two markets closed
      07-21T18:49Z against a real round end of 07-24T00:11Z).
    * ``mode`` picks up **the cut** on R3 events — after R2 roughly half the
      field is eliminated and their R3-leader markets all close at the R2
      stamp, which is frequently the *larger* cluster
      (KXLPGAR3LEAD-MEILCFSG26: 76 of 149 markets close at the cut).

    The round end is the LAST cluster of any real size.  Clusters are formed
    to the minute and a cluster needs >= 3 markets to count, which drops the
    one-off stragglers without letting a withdrawal or a cut win.
    """
    by_event: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    with open(META) as f:
        for line in f:
            r = json.loads(line)
            if "LEAD" not in r.get("series", ""):
                continue
            if not r.get("close_time") or not r.get("open_time"):
                continue
            by_event[r["event_ticker"]].append(r)

    out: Dict[str, Dict[str, Any]] = {}
    for et, rows in by_event.items():
        minutes = collections.Counter(r["close_time"][:16] for r in rows)
        big = [m for m, n in minutes.items() if n >= 3] or list(minutes)
        last_min = max(big)
        closes = [r["close_time"] for r in rows
                  if r["close_time"].startswith(last_min)]
        opens = collections.Counter(r["open_time"] for r in rows)
        out[et] = {
            "series": rows[0]["series"],
            "n_markets": len(rows),
            "n_at_close_cluster": minutes[last_min],
            "close": max(closes),
            "open": opens.most_common(1)[0][0],
            "n_distinct_close_minutes": len(minutes),
        }
    return out


REPAIR_CACHE = os.path.join(DATA, "leader_event_close_repair.json")


def repair_thin_events(events: Dict[str, Dict[str, Any]],
                       min_markets: int = 20) -> int:
    """Re-fetch close times for events the cache only partly captured.

    ``settled_meta.jsonl`` was pulled while some events were still settling,
    so a handful hold only the two or three markets that had closed by then —
    and those are exactly the WITHDRAWALS, whose close stamp is days off the
    round end.  Left alone they read as catastrophic resolver errors (-95h)
    that are entirely an artefact of the cache.  Repair from the API rather
    than dropping them: three real observations beat three exclusions.
    """
    thin = [et for et, d in events.items() if d["n_markets"] < min_markets]
    if not thin:
        return 0
    cache: Dict[str, Any] = {}
    if os.path.exists(REPAIR_CACHE):
        cache = json.load(open(REPAIR_CACHE))
    k = None
    for et in thin:
        if et not in cache:
            k = k or KalshiPublic()
            d = k.get("/markets", {"event_ticker": et, "limit": 200})
            ms = (d or {}).get("markets", [])
            cache[et] = [m.get("close_time") for m in ms if m.get("close_time")]
        cl = cache[et]
        if not cl:
            continue
        minutes = collections.Counter(c[:16] for c in cl)
        big = [m for m, n in minutes.items() if n >= 3] or list(minutes)
        last_min = max(big)
        events[et].update({
            "close": max(c for c in cl if c.startswith(last_min)),
            "n_markets": len(cl),
            "n_at_close_cluster": minutes[last_min],
            "truth_repaired": True,
        })
    json.dump(cache, open(REPAIR_CACHE, "w"), indent=1, sort_keys=True)
    return len(thin)


def fetch_event_meta(event_tickers: List[str]) -> Dict[str, Dict[str, Any]]:
    """/events/{ticker} -> product_metadata.  Cached; Kalshi is the slow leg."""
    cache: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(EVENT_CACHE):
        cache = json.load(open(EVENT_CACHE))
    missing = [e for e in event_tickers if e not in cache]
    if missing:
        k = KalshiPublic()
        for i, et in enumerate(missing, 1):
            d = k.get(f"/events/{et}")
            ev = (d or {}).get("event", {}) or {}
            pm = ev.get("product_metadata") or {}
            cache[et] = {
                "competition": pm.get("competition"),
                "competition_scope": pm.get("competition_scope"),
                "title": ev.get("title"),
                "sub_title": ev.get("sub_title"),
            }
            if i % 10 == 0:
                print(f"  ...{i}/{len(missing)} events")
        os.makedirs(DATA, exist_ok=True)
        json.dump(cache, open(EVENT_CACHE, "w"), indent=1, sort_keys=True)
    return cache


# ── B. ESPN schedule ─────────────────────────────────────────────────

def fetch_espn(years=(2025, 2026)) -> Dict[str, List[Dict[str, Any]]]:
    if os.path.exists(ESPN_CACHE):
        return json.load(open(ESPN_CACHE))
    out: Dict[str, List[Dict[str, Any]]] = {}
    for league in sorted(set(TOUR_LEAGUE.values())):
        evs: List[Dict[str, Any]] = []
        for y in years:
            url = ESPN_URL.format(league=league, year=y)
            with urllib.request.urlopen(url, timeout=30) as resp:
                d = json.loads(resp.read().decode())
            for e in d.get("events", []):
                evs.append({
                    "id": e.get("id"),
                    "name": e.get("name"),
                    "shortName": e.get("shortName"),
                    "date": e.get("date"),
                    "endDate": e.get("endDate"),
                    "season": y,
                })
        # de-dup by id
        seen, uniq = set(), []
        for e in evs:
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            uniq.append(e)
        out[league] = uniq
        print(f"  ESPN {league}: {len(uniq)} events")
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(ESPN_CACHE, "w"), indent=1)
    return out


# Only strip words that carry NO discriminating power at all.  The first cut
# also stripped "open", "championship" and "tournament" — which normalises
# "The Open Championship" to the EMPTY SET and silently drops the three biggest
# events in the sample.  Sponsor noise is handled by the Jaccard score instead.
_STOP = {"the", "presented", "pres", "by", "golf", "of", "at", "and", "a"}
_ACCENTS = str.maketrans("áàâäãéèêëíìîïóòôöõúùûüñç", "aaaaaeeeeiiiiooooouuuunc")


def norm(s: str) -> set:
    s = (s or "").lower().translate(_ACCENTS)
    s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
    return {w for w in s.split() if w and w not in _STOP}


def match_espn(competition: str, league_events: List[Dict[str, Any]],
               true_close: datetime) -> Optional[Dict[str, Any]]:
    """Name match, disambiguated by date proximity to the true close.

    Name alone is not enough (ESPN carries two seasons and some events repeat);
    date alone is not enough either (two tour events can run the same week).
    """
    want = norm(competition)
    scored = []
    for e in league_events:
        have = norm(e["name"])
        if not want or not have:
            continue
        jac = len(want & have) / len(want | have)
        if jac <= 0:
            continue
        try:
            d0 = iso(e["date"])
        except Exception:                      # noqa: BLE001
            continue
        days = abs((true_close - d0).total_seconds()) / 86400.0
        scored.append((jac, -days, e))
    if not scored:
        return None
    # Prefer a high name score; among comparable names take the nearest date.
    scored.sort(key=lambda t: (round(t[0], 3), t[1]), reverse=True)
    best = scored[0]
    if best[0] < 0.34 or -best[1] > 30:
        return None
    return best[2]


# ── C. ESPN tee times (the precise leg) ──────────────────────────────

LB_URL = ("https://site.api.espn.com/apis/site/v2/sports/golf/leaderboard"
          "?league={league}&event={eid}")
LB_CACHE = os.path.join(DATA, "espn_teetimes.json")


def fetch_tee_times(pairs) -> Dict[str, Dict[str, str]]:
    """(league, espn_event_id) -> {round_number: last tee time ISO}.

    ``linescores[].teeTime`` on the leaderboard is a real per-round UTC stamp.
    The LAST group off is what determines when the round — and therefore the
    round-leader market — ends.
    """
    cache: Dict[str, Dict[str, str]] = {}
    if os.path.exists(LB_CACHE):
        cache = json.load(open(LB_CACHE))
    todo = [p for p in pairs if p[1] not in cache]
    for league, eid in todo:
        try:
            with urllib.request.urlopen(
                    LB_URL.format(league=league, eid=eid), timeout=30) as resp:
                d = json.loads(resp.read().decode())
        except Exception as exc:               # noqa: BLE001
            print(f"  leaderboard {eid} failed: {exc}")
            cache[eid] = {}
            continue
        last: Dict[str, Dict[str, str]] = {}
        for e in d.get("events", []):
            for c in e.get("competitions", []):
                for comp in c.get("competitors", []):
                    for ls in comp.get("linescores") or []:
                        tt, per = ls.get("teeTime"), ls.get("period")
                        if not tt or per is None:
                            continue
                        k = str(int(per))
                        slot = last.setdefault(k, {"first": tt, "last": tt})
                        slot["first"] = min(slot["first"], tt)
                        slot["last"] = max(slot["last"], tt)
        cache[eid] = last
    if todo:
        json.dump(cache, open(LB_CACHE, "w"), indent=1, sort_keys=True)
    return cache


# ── Join and residuals ───────────────────────────────────────────────

def main() -> None:
    print("Loading Kalshi settled leader events...")
    events = load_settled_events()
    n_rep = repair_thin_events(events)
    print(f"  {len(events)} settled round-leader events "
          f"({n_rep} repaired from the API — cache was pulled mid-settlement)")

    print("Fetching /events product_metadata...")
    meta = fetch_event_meta(sorted(events))

    print("Fetching ESPN schedules...")
    espn = fetch_espn()

    rows: List[Dict[str, Any]] = []
    for et, d in sorted(events.items()):
        series = d["series"]
        tour = tour_of(series)
        rnd = round_of(series)
        league = TOUR_LEAGUE.get(tour or "")
        m = meta.get(et, {})
        comp = m.get("competition")
        close = iso(d["close"])
        open_ = iso(d["open"])
        row = {
            "event": et, "series": series, "tour": tour, "round": rnd,
            "competition": comp,
            "true_close": d["close"], "open": d["open"],
            "open_to_close_h": (close - open_).total_seconds() / 3600.0,
            "n_markets": d["n_markets"],
            "n_at_close_cluster": d["n_at_close_cluster"],
            "truth_repaired": d.get("truth_repaired", False),
            "n_distinct_close_minutes": d["n_distinct_close_minutes"],
        }
        e = match_espn(comp, espn.get(league, []), close) if (comp and league) else None
        if e:
            start = iso(e["date"])
            end = iso(e["endDate"]) if e.get("endDate") else None
            n_rounds = int(round((end - start).total_seconds() / 86400.0)) + 1 if end else None
            rnd_day = start + timedelta(days=(rnd or 1) - 1)
            row.update({
                "espn_id": e["id"],
                "espn_name": e["name"], "espn_start": e["date"],
                "espn_end": e.get("endDate"), "espn_n_rounds": n_rounds,
                "round_day_04z": rnd_day.isoformat().replace("+00:00", "Z"),
                # residual: how many hours after ESPN's 04:00Z round-day stamp
                # did the market actually close?
                "resid_h": (close - rnd_day).total_seconds() / 3600.0,
            })
        rows.append(row)

    # ── tee times for every matched ESPN event ──
    pairs = sorted({(TOUR_LEAGUE[r["tour"]], r["espn_id"])
                    for r in rows if r.get("espn_id") and r.get("tour")})
    print(f"Fetching ESPN tee times for {len(pairs)} tournaments...")
    tee = fetch_tee_times(pairs)
    for r in rows:
        eid, rnd = r.get("espn_id"), r.get("round")
        slot = (tee.get(eid or "") or {}).get(str(rnd or "")) or {}
        close = iso(r["true_close"])
        if slot.get("last"):
            r["last_tee_time"] = slot["last"]
            r["resid_tee_h"] = (close - iso(slot["last"])).total_seconds() / 3600.0
        if slot.get("first"):
            r["first_tee_time"] = slot["first"]
            # How long the round is actually live: this is when "posting into
            # a round already underway" starts, and it is what makes the 12h
            # lower edge of the window tight.
            r["round_span_h"] = (close - iso(slot["first"])).total_seconds() / 3600.0
        # Intra-event anchor: this tournament's OWN round-1 tee times, plus
        # (n-1) days.  Available for R2/R3 long before their own pairings are
        # published, and it inherits the venue's timezone for free.
        r1 = (tee.get(eid or "") or {}).get("1") or {}
        if r1.get("last") and rnd:
            pred = iso(r1["last"]) + timedelta(days=rnd - 1)
            r["resid_r1anchor_h"] = (close - pred).total_seconds() / 3600.0

    matched = [r for r in rows if r.get("espn_name")]
    print(f"\nMatched {len(matched)}/{len(rows)} events to ESPN")
    unmatched = [r for r in rows if not r.get("espn_name")]
    for r in unmatched:
        print(f"  UNMATCHED {r['event']}  comp={r['competition']!r} tour={r['tour']}")

    def summarize(label: str, vals: List[float]) -> None:
        if not vals:
            print(f"  {label}: n=0")
            return
        vals = sorted(vals)
        q = statistics.quantiles(vals, n=100) if len(vals) >= 4 else None
        print(f"  {label}: n={len(vals)} min={vals[0]:.2f} med={statistics.median(vals):.2f} "
              f"max={vals[-1]:.2f} sd={statistics.pstdev(vals):.2f}"
              + (f" p05={q[4]:.2f} p95={q[94]:.2f}" if q else ""))

    print("\n── Residual: true_close − (ESPN round day @04:00Z), hours ──")
    summarize("ALL", [r["resid_h"] for r in matched])
    by_tour = collections.defaultdict(list)
    for r in matched:
        by_tour[r["tour"]].append(r["resid_h"])
    for t, v in sorted(by_tour.items()):
        summarize(f"  {t}", v)
    by_tr = collections.defaultdict(list)
    for r in matched:
        by_tr[(r["tour"], r["round"])].append(r["resid_h"])
    print("\n  by (tour, round):")
    for k, v in sorted(by_tr.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or 0)):
        summarize(f"  {k[0]} R{k[1]}", v)

    print("\n── Residual: true_close − (last ESPN tee time for that round), hours ──")
    tees = [r for r in matched if r.get("resid_tee_h") is not None]
    print(f"  tee times available for {len(tees)}/{len(matched)} matched events")
    summarize("ALL", [r["resid_tee_h"] for r in tees])
    bt = collections.defaultdict(list)
    for r in tees:
        bt[r["tour"]].append(r["resid_tee_h"])
    for t, v in sorted(bt.items()):
        summarize(f"  {t}", v)
    tight = [r for r in tees if abs(r["resid_tee_h"] - 5.0) <= 6.0]
    print(f"  within [-1h, +11h] of last tee: {len(tight)}/{len(tees)}")
    print("  worst:")
    for r in sorted(tees, key=lambda x: -abs(x["resid_tee_h"]))[:8]:
        print(f"    {r['event']:34} tee={r['last_tee_time']} close={r['true_close']} "
              f"resid={r['resid_tee_h']:7.2f}  (markets={r['n_markets']}, "
              f"cluster={r['n_at_close_cluster']})")

    print("\n── Round span: true_close − FIRST tee of the round, hours ──")
    print("   (this is when 'the round is underway' begins, measured from close)")
    summarize("ALL", [r["round_span_h"] for r in matched
                      if r.get("round_span_h") is not None])

    print("\n── Residual: true_close − (this event's R1 last tee + (n−1)·24h) ──")
    ra = [r for r in matched if r.get("resid_r1anchor_h") is not None]
    summarize("ALL", [r["resid_r1anchor_h"] for r in ra])
    br = collections.defaultdict(list)
    for r in ra:
        br[(r["tour"], r["round"])].append(r["resid_r1anchor_h"])
    for k, v in sorted(br.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or 0)):
        summarize(f"  {k[0]} R{k[1]}", v)

    print("\n── Secondary: open → true close, hours (the Kalshi-only fallback) ──")
    summarize("ALL", [r["open_to_close_h"] for r in rows])
    by_tr2 = collections.defaultdict(list)
    for r in rows:
        by_tr2[(r["tour"], r["round"])].append(r["open_to_close_h"])
    for k, v in sorted(by_tr2.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or 0)):
        summarize(f"  {k[0]} R{k[1]}", v)

    json.dump(rows, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
