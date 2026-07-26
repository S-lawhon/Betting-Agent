"""
satellites_research/pull_wayback_rt.py
──────────────────────────────────────
Study B, external leg — how far does the Tomatometer actually move between the
opening-weekend score retail anchors on and the Monday-10:00-ET settlement read?

Uses the Wayback CDX API to find archived rottentomatoes.com/m/<slug> snapshots
in the release-Friday -> settlement-Monday window and pulls the Tomatometer out
of the archived HTML.

Coverage is expected to be PARTIAL and is logged honestly — every film is
recorded with the snapshots actually found, including "none".  Nothing here is
inferred when a snapshot is missing.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "wayback_rt.json")

# (kalshi event, RT slug, settlement Monday 10:00 ET as YYYYMMDD)
FILMS = [
    ("KXRT-BAC",  "backrooms",                "20260601"),
    ("KXRT-TOY",  "toy_story_5",              "20260622"),
    ("KXRT-ODY",  "the_odyssey",              "20260720"),
    ("KXRT-MOA",  "moana_2026",               "20260713"),
    ("KXRT-SUPE", "supergirl_2026",           "20260629"),
    ("KXRT-STA",  "the_mandalorian_and_grogu", "20260525"),
    ("KXRT-SCA",  "scary_movie_6",            "20260608"),
    ("KXRT-MAS",  "masters_of_the_universe",  "20260608"),
    ("KXRT-EVI",  "evil_dead_burn",           "20260713"),
    ("KXRT-PRE",  "pressure",                 "20260601"),
]

# Verified against a real archived page (backrooms, 2026-06-01): the "All
# Critics" Tomatometer lives in the embedded JSON as
#   "name":"Tomatometer","ratingCount":180,"ratingValue":"89"
# and again as  "scorePercent":"89%","title":"Tomatometer".
SCORE_RX = [
    re.compile(r'"name"\s*:\s*"Tomatometer"\s*,\s*"ratingCount"\s*:\s*(\d+)\s*,'
               r'\s*"ratingValue"\s*:\s*"?(\d{1,3})"?', re.I),
    re.compile(r'"scorePercent"\s*:\s*"(\d{1,3})%"\s*,\s*"title"\s*:\s*"Tomatometer"', re.I),
]
REVIEWS_RX = re.compile(r'"reviewCount"\s*:\s*(\d+)')


def cdx(slug: str, frm: str, to: str):
    u = ("http://web.archive.org/cdx/search/cdx?url=rottentomatoes.com/m/"
         f"{slug}&output=json&from={frm}&to={to}&filter=statuscode:200"
         "&collapse=digest&limit=60")
    for _ in range(3):
        try:
            r = requests.get(u, timeout=60)
            rows = json.loads(r.text or "[]")[1:]
            if rows:
                return rows
        except Exception:
            pass
        time.sleep(3)
    return []


def score_from(ts: str, slug: str):
    u = f"https://web.archive.org/web/{ts}id_/https://www.rottentomatoes.com/m/{slug}"
    try:
        r = requests.get(u, timeout=60)
    except Exception:
        return None, None
    if r.status_code != 200:
        return None, None
    txt = r.text
    nrev = None
    mr = REVIEWS_RX.search(txt)
    if mr:
        nrev = int(mr.group(1))
    for rx in SCORE_RX:
        m = rx.search(txt)
        if m:
            v = int(m.group(m.lastindex))
            if m.lastindex == 2 and nrev is None:
                nrev = int(m.group(1))
            if 0 <= v <= 100:
                return v, nrev
    return None, nrev


def main() -> None:
    out = json.load(open(OUT)) if os.path.exists(OUT) else {}
    for ev, slug, monday in FILMS:
        if ev in out:
            continue
        # window: the Wednesday before release through settlement Monday
        y, m, d = int(monday[:4]), int(monday[4:6]), int(monday[6:])
        import datetime as dt
        mon = dt.date(y, m, d)
        frm = (mon - dt.timedelta(days=6)).strftime("%Y%m%d")
        rows = cdx(slug, frm, monday)
        recs = []
        for r in rows[:12]:
            ts = r[1]
            sc, nrev = score_from(ts, slug)
            recs.append({"ts": ts, "score": sc, "reviews": nrev})
            time.sleep(1.0)
        out[ev] = {"slug": slug, "monday": monday, "n_snapshots": len(rows),
                   "records": recs}
        print(f"{ev:12s} {slug:28s} snapshots={len(rows):3d} "
              f"scored={sum(1 for x in recs if x['score'] is not None)}", flush=True)
        with open(OUT, "w") as fh:
            json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
