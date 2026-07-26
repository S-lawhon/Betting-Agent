"""
crossvenue_research/audit_matches.py
────────────────────────────────────
Print a reproducible random sample of matched pairs for manual settlement-
definition review, so the REPORT can quote a measured false-match rate rather
than assert one.  Local only.
"""
from __future__ import annotations

import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
TIER = sys.argv[2] if len(sys.argv) > 2 else "HIGH"

pairs = [p for p in json.load(open(os.path.join(DATA, "pairs.json")))
         if p["tier"] == TIER and p["poly_volume"] >= 25_000]
random.Random(4242).shuffle(pairs)
for i, p in enumerate(pairs[:N]):
    print(f"[{i+1:>2}] score={p['score']:<5} skew={p['date_skew_h']:>5}h "
          f"agree={p['outcome_agree']}  vol=${p['poly_volume']:,.0f}")
    print(f"     K: {p['kalshi_ticker']}  {p['kalshi_title']} "
          f"| {p['kalshi_sub']}  -> {p['kalshi_result']}  close={p['kalshi_close']}")
    print(f"     P: {p['poly_question']}  -> {p['poly_result']}  end={p['poly_end']}")
