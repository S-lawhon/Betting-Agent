"""
crossvenue_research/shortlist_series.py
───────────────────────────────────────
Pick the Kalshi Politics/World/Economics series worth pulling settled markets
for, by testing each series title against the harvested Polymarket question
corpus.  Purely local (no network).

Rationale: querying all 2,851 politics/world/econ series would burn ~40 min of
a shared 2 req/s Kalshi budget.  A series whose title shares no distinctive
content token with ANY Polymarket question in the window cannot produce a pair.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

STOP = set("""a an the will be is are was were do does did to of in on at by for
with from and or not no yes any all more than before after during between over
under this that these those it its as if then when who whom which what how many
much most least new next last first second third year years month months week
weeks day days end ends ending get gets got make makes made take takes say says
said 2025 2026 2027 us u.s. usa american america president""".split())


def toks(s: str) -> set:
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    return {t for t in s.split() if t not in STOP and len(t) > 2}


def main() -> None:
    series = json.load(open(os.path.join(DATA, "kalshi_series.json")))
    poly = json.load(open(os.path.join(DATA, "poly_closed.json")))

    # inverted index over poly question tokens
    idx = defaultdict(int)
    poly_tok = []
    for m in poly:
        t = toks(m.get("question", "")) | toks(m.get("slug", "").replace("-", " "))
        poly_tok.append(t)
        for x in t:
            idx[x] += 1

    # drop tokens that appear in >15% of poly questions (they carry no signal)
    n = max(1, len(poly_tok))
    common = {t for t, c in idx.items() if c / n > 0.15}

    keep = []
    for tk, s in series.items():
        st = (toks(s.get("title", "")) | toks(s.get("category", ""))) - common
        if not st:
            continue
        hit = any(len(st & pt) >= 2 for pt in poly_tok)
        if not hit:
            # single very-rare token is also enough (e.g. "khamenei")
            rare = {x for x in st if idx.get(x, 0) and idx[x] <= 40}
            hit = any(rare & pt for pt in poly_tok)
        if hit:
            keep.append(tk)

    keep.sort()
    json.dump(keep, open(os.path.join(DATA, "series_shortlist.json"), "w"))
    print(f"shortlisted {len(keep)} / {len(series)} kalshi series")


if __name__ == "__main__":
    main()
