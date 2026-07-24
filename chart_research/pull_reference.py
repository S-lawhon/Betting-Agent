"""
chart_research/pull_reference.py
────────────────────────────────
Reconstructs the FREE PUBLIC SIGNAL for each chart-week from timestamped
sources, per the P-025 survivorship guard: a week's signal is used only if it
can be rebuilt from a dated/archived source. Weeks that cannot are logged and
EXCLUDED (never back-filled).

Sources (all confirmed reachable & parseable 2026-07-23):
  ALBUMS  → HITS Daily Double "Midweek 20" album building chart, dated-archive
            URLs /charts/midweek-20/<Thursday>. Server-rendered rows live in
            the Next.js RSC payload as {"this_week":N,"artist":..,"album":..,
            "data":{"Activity":"101,000","Albums":"80,000"}}. "Activity" is the
            Billboard-200-style consumption-unit estimate → #1 by Activity is a
            direct predictor of the Billboard 200 #1. HITS midweek is dated the
            Thursday of the tracking week (= chart_sat - 9).
  SONGS   → kworb.net US Spotify daily (kworb.net/spotify/country/us_daily.html)
            via the Wayback Machine (WebFetch is blocked from archive.org, but
            curl reaches it). The "7Day" column is the trailing-7-day US Spotify
            stream total — the closest free proxy for the Hot 100 streaming
            input. CAVEAT recorded in the REPORT: Spotify-only misses radio +
            sales + digital, so a country/radio #1 can sit at Spotify #2. kworb
            is therefore a WEAKER song signal than Talk of the Charts'
            multi-metric call.
  ToTC    → NOT machine-re-gradeable here (calls live in timestamped X posts we
            cannot fetch). We do NOT launder its 52/52 self-audit into a
            "verified" number; the REPORT cites it as the source's own claim and
            grades independently only what we can reconstruct (kworb / HITS).

Cache: data/reference.json  (one record per event, with the reconstructed
       signal at T-mid and T-close, or an "excluded" reason).
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "reference_raw")

_S = requests.Session()
_S.headers.update({"User-Agent": "Mozilla/5.0 (chart-research/P-025)"})
_last = [0.0]


def throttle(min_int: float = 0.5) -> None:
    now = time.time()
    w = _last[0] + min_int - now
    if w > 0:
        time.sleep(w)
    _last[0] = max(now, _last[0] + min_int)


def fetch(url: str, retries: int = 3) -> str | None:
    for i in range(retries):
        try:
            throttle()
            r = _S.get(url, timeout=45)
            if r.status_code == 200:
                return r.text
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(3 * (i + 1))
                continue
            return None
        except Exception:  # noqa: BLE001
            time.sleep(3 * (i + 1))
    return None


def norm(s: str) -> str:
    """Normalize a title for matching (uppercase alnum words)."""
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def title_match(a: str, b: str) -> bool:
    """True if two titles plausibly refer to the same work."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    wa, wb = set(na.split()), set(nb.split())
    stop = {"the", "a", "of", "for", "in", "to", "and", "part", "ii", "i"}
    wa -= stop; wb -= stop
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.6


def to_int(s: str) -> int | None:
    try:
        return int(re.sub(r"[^0-9]", "", s))
    except (ValueError, TypeError):
        return None


# ── HITS album midweek ────────────────────────────────────────────────────

def parse_hits(html: str) -> list[dict]:
    u = html.replace('\\"', '"')
    rows = re.findall(
        r'"this_week":(\d+),"rank":[^,]*,"artist":"([^"]*)","album":"([^"]*)",'
        r'"label":"([^"]*)","data":\{([^}]*)\}', u)
    out = []
    for tw, artist, album, label, data in rows:
        act = re.search(r'"Activity":"([^"]*)"', data)
        alb = re.search(r'"Albums":"([^"]*)"', data)
        out.append({"pos": int(tw), "artist": artist, "title": album,
                    "activity": to_int(act.group(1)) if act else None,
                    "albums": to_int(alb.group(1)) if alb else None})
    out.sort(key=lambda r: r["pos"])
    return out


def hits_signal(chart_sat: datetime) -> dict:
    thu = (chart_sat - timedelta(days=9)).date().isoformat()
    url = f"https://www.hitsdailydouble.com/charts/midweek-20/{thu}"
    cache = os.path.join(RAW, f"hits_{thu}.html")
    html = _cached(cache, url)
    if not html:
        return {"ok": False, "reason": f"HITS archive fetch failed ({thu})"}
    rows = parse_hits(html)
    if len(rows) < 2:
        return {"ok": False, "reason": f"HITS archive had no rows ({thu})"}
    top1, top2 = rows[0], rows[1]
    a1, a2 = top1.get("activity"), top2.get("activity")
    margin = ((a1 - a2) / a1) if (a1 and a2 and a1 > 0) else None
    return {"ok": True, "source": "HITS-midweek-20", "date": thu,
            "leader": top1["title"], "leader_artist": top1["artist"],
            "runner_up": top2["title"], "leader_units": a1, "runner_units": a2,
            "margin": margin, "top5": rows[:5]}


# ── kworb song daily via Wayback ──────────────────────────────────────────

def wayback_url(target_url: str, ts: str) -> str | None:
    j = fetch(f"http://archive.org/wayback/available?url={target_url}&timestamp={ts}")
    if not j:
        return None
    try:
        snap = json.loads(j).get("archived_snapshots", {}).get("closest", {})
        return snap.get("url") if snap.get("available") else None
    except json.JSONDecodeError:
        return None


def parse_kworb(html: str) -> list[dict]:
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.S)
    out = []
    for r in rows:
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        # Pos, P+, "Artist - Title", Days, Pk, (x?), Streams, Streams+, 7Day...
        if len(cells) < 9:
            continue
        at = cells[2]
        artist, _, title = at.partition(" - ")
        out.append({"pos": to_int(cells[0]), "artist": artist.strip(),
                    "title": title.strip(), "day_streams": to_int(cells[6]),
                    "seven_day": to_int(cells[8])})
    return [r for r in out if r["pos"]]


def kworb_signal(chart_sat: datetime, days_before: int) -> dict:
    target = (chart_sat - timedelta(days=days_before))
    ts = target.strftime("%Y%m%d")
    tgt_url = "kworb.net/spotify/country/us_daily.html"
    cache = os.path.join(RAW, f"kworb_{ts}.html")
    html = None
    if os.path.exists(cache):
        html = open(cache, encoding="utf-8", errors="replace").read()
    else:
        snap = wayback_url(tgt_url, ts)
        if snap:
            html = fetch(snap)
            if html:
                os.makedirs(RAW, exist_ok=True)
                open(cache, "w", encoding="utf-8").write(html)
    if not html:
        return {"ok": False, "reason": f"kworb wayback miss ({ts})"}
    rows = parse_kworb(html)
    if len(rows) < 2:
        return {"ok": False, "reason": f"kworb parse empty ({ts})"}
    # Rank by trailing-7-day streams (the tracking-week proxy).
    ranked = sorted([r for r in rows if r["seven_day"]],
                    key=lambda r: -r["seven_day"])
    if len(ranked) < 2:
        return {"ok": False, "reason": f"kworb no 7day data ({ts})"}
    top1, top2 = ranked[0], ranked[1]
    s1, s2 = top1["seven_day"], top2["seven_day"]
    margin = ((s1 - s2) / s1) if s1 else None
    return {"ok": True, "source": "kworb-us-daily-7day", "date": ts,
            "leader": top1["title"], "leader_artist": top1["artist"],
            "runner_up": top2["title"], "leader_units": s1, "runner_units": s2,
            "margin": margin, "top5": ranked[:5]}


def _cached(path: str, url: str) -> str | None:
    if os.path.exists(path):
        return open(path, encoding="utf-8", errors="replace").read()
    html = fetch(url)
    if html:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(html)
    return html


# ── Driver ────────────────────────────────────────────────────────────────

def build() -> None:
    os.makedirs(RAW, exist_ok=True)
    events = json.load(open(os.path.join(DATA, "events.json")))
    out = []
    for e in events:
        cs = datetime.fromisoformat(e["chart_sat"]).replace(tzinfo=ET)
        rec = {"event": e["event"], "series": e["series"],
               "chart_sat": e["chart_sat"], "winner": e["winner"]}
        if e["series"] == "KXTOPALBUM":
            # HITS midweek reflects data ~= T-mid; reuse for T-close (no dated
            # album-week snapshot between Thu-archive and Fri close).
            sig = hits_signal(cs)
            rec["T_mid"] = sig
            rec["T_close"] = sig
        else:
            rec["T_mid"] = kworb_signal(cs, days_before=10)   # Wed tracking wk
            rec["T_close"] = kworb_signal(cs, days_before=8)  # Fri after close
        # grade vs Billboard result (Kalshi settled winner)
        for t in ("T_mid", "T_close"):
            s = rec.get(t, {})
            if s.get("ok") and e["winner"]:
                s["signal_correct"] = title_match(s["leader"], e["winner"])
            elif s.get("ok"):
                s["signal_correct"] = None
        out.append(rec)
        ok_mid = rec["T_mid"].get("ok")
        print(f"[ref] {e['event']:22} winner={str(e['winner'])[:20]:20} "
              f"T_mid={'OK' if ok_mid else rec['T_mid'].get('reason')}",
              flush=True)
    with open(os.path.join(DATA, "reference.json"), "w") as f:
        json.dump(out, f, indent=1)
    n_ok = sum(1 for r in out if r["T_mid"].get("ok"))
    print(f"[ref] reconstructed {n_ok}/{len(out)} weeks at T-mid")


if __name__ == "__main__":
    build()
