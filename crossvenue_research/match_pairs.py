"""
crossvenue_research/match_pairs.py
──────────────────────────────────
P-020 Phase 1, stage C: pair Kalshi settled markets with Polymarket settled
markets and assign a match confidence.  Local only (no network).

Settlement-definition mismatch is the primary failure mode (SPEC §5), so a pair
must clear ALL of:
  1. fuzzy title score >= MIN_SCORE (token-sort on question text)
  2. every NUMBER / DATE token in either title appears in the other
     ("by June 30" != "by July 15"; "5.25%" != "5.00%")
  3. Kalshi close_time within MAX_DATE_SKEW of Polymarket endDate
  4. both venues resolved determinately (Kalshi result in {yes,no};
     Poly outcomePrices snapped to 1/0)
Pairs failing (2) or (3) are dropped, never "fudged".  Tier HIGH/MED is written
per pair so the backtest can restrict to HIGH.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import unicodedata
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.fuzzy_utils import token_sort_ratio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

MIN_SCORE = 62.0
MAX_DATE_SKEW_H = 60.0

MONTHS = ("january february march april may june july august september "
          "october november december").split()


def parse_iso(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


_num_re = re.compile(r"\d+(?:\.\d+)?")
_MONTH_ABBR = "|".join(m[:3] for m in MONTHS)
# a number that immediately follows a month name, or a 4-digit year, is a DATE,
# not a strike.  Strikes are what decide whether two questions are the same
# question ("above 175 transits" != "fewer than 150 ships").
_date_num_re = re.compile(rf"(?:{_MONTH_ABBR})[a-z]*\.?\s+(\d{{1,2}})")


def key_tokens(text: str) -> set:
    """Numbers and month names — the tokens that define WHICH question this is."""
    t = (text or "").lower()
    out = set(_num_re.findall(t))
    for m in MONTHS:
        if re.search(r"\b" + m[:3], t):
            out.add(m[:3])
    return out


def strike_tokens(text: str) -> set:
    """Numbers that act as STRIKES rather than calendar dates."""
    t = (text or "").lower()
    dates = set(_date_num_re.findall(t))
    out = set()
    for n in _num_re.findall(t):
        if n in dates:
            continue
        if re.fullmatch(r"(19|20)\d\d", n):        # year
            continue
        out.add(n.rstrip("0").rstrip(".") if "." in n else n)
    return out


# Cheap antonym guard: a "hike / above / more than" question is not the same
# question as a "cut / below / fewer than" one even when the strike matches.
_POLARITY = [
    ({"hike", "hikes", "increase", "increases", "raise", "raises", "up"},
     {"cut", "cuts", "decrease", "decreases", "lower", "lowers", "down"}),
    ({"above", "over", "more", "greater", "least", "exceed", "exceeds"},
     {"below", "under", "fewer", "less", "most"}),
]


def polarity_conflict(a: str, b: str) -> bool:
    wa = set(re.sub(r"[^a-z ]", " ", (a or "").lower()).split())
    wb = set(re.sub(r"[^a-z ]", " ", (b or "").lower()).split())
    for pos, neg in _POLARITY:
        if (wa & pos and wb & neg and not (wa & neg) and not (wb & pos)):
            return True
        if (wa & neg and wb & pos and not (wa & pos) and not (wb & neg)):
            return True
    return False


def deaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def strike_present(sub: str, poly_q: str) -> bool:
    """The Kalshi `yes_sub_title` IS the strike of the contract (the candidate
    name, the phrase Trump must say, the party).  If it cannot be located in the
    Polymarket question the two contracts do not resolve on the same event, no
    matter how well the surrounding boilerplate fuzzy-matches.

    This single gate removed every false match found in the first manual audit
    ("EV / Electric" vs "Coal"; "Gentner Drummond" vs "Donelle Harder";
    "Conservative" vs "David Ballantine"; "Fraud" vs "Iraq").
    """
    if not sub:
        return True
    pq = re.sub(r"[^a-z0-9 ]", " ", deaccent(poly_q).lower())
    pq = " " + re.sub(r"\s+", " ", pq) + " "
    for alt in re.split(r"[/,]| or ", deaccent(sub).lower()):
        alt = re.sub(r"[^a-z0-9 ]", " ", alt).strip()
        if not alt:
            continue
        toks = [t for t in alt.split() if len(t) > 3] or alt.split()
        if any(f" {t} " in pq for t in toks):
            return True
    return False


def norm_q(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\bwill\b|\bthe\b|\ba\b|\ban\b|\bbe\b|\bby\b|\bin\b|\bon\b", " ", s)
    s = re.sub(r"[^\w\s.%]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def poly_result(m: dict) -> Optional[str]:
    raw = m.get("outcomePrices")
    try:
        prices = json.loads(raw) if isinstance(raw, str) else (raw or [])
        prices = [float(x) for x in prices]
    except (ValueError, TypeError):
        return None
    if len(prices) < 2:
        return None
    if prices[0] >= 0.99 and prices[1] <= 0.01:
        return "yes"
    if prices[1] >= 0.99 and prices[0] <= 0.01:
        return "no"
    return None


def kalshi_result(m: dict) -> Optional[str]:
    r = (m.get("result") or "").lower()
    return r if r in ("yes", "no") else None


def main() -> None:
    poly = json.load(open(os.path.join(DATA, "poly_closed.json")))
    kals = json.load(open(os.path.join(DATA, "kalshi_settled.json")))
    cand = json.load(open(os.path.join(DATA, "stage_a_candidates.json")))

    by_series: Dict[str, List[dict]] = {}
    for m in kals:
        by_series.setdefault(m["_series"], []).append(m)

    # Precompute normalised text / token sets once per Kalshi market.
    # The Kalshi /v1/search recall pass pulls in whole high-frequency series
    # (crypto 15-min, per-game sports) that cannot be a politics counterpart;
    # matching every Poly question against all of them is O(10^7) SequenceMatcher
    # calls.  A cheap content-token prefilter removes >99% of the candidates
    # without touching the fuzzy threshold that actually decides a match.
    prep: Dict[str, dict] = {}
    for m in kals:
        title = m.get("title", "")
        sub = m.get("yes_sub_title") or ""
        full = (title + " " + sub).strip()
        prep[m["ticker"]] = {
            "n_full": norm_q(full), "n_title": norm_q(title),
            "key": key_tokens(full), "strike": strike_tokens(full),
            "raw": full,
            "words": {w for w in norm_q(full).split() if len(w) > 3},
            "close": parse_iso(m.get("close_time")),
            "res": kalshi_result(m),
        }

    poly_by_event: Dict[str, List[dict]] = {}
    for m in poly:
        e = (m.get("events") or [{}])[0]
        poly_by_event.setdefault(e.get("slug") or m.get("slug"), []).append(m)

    pairs = []
    stats = {"considered": 0, "score_fail": 0, "keytok_fail": 0,
             "date_fail": 0, "result_fail": 0}

    for ev_slug, series_list in cand.items():
        pmkts = poly_by_event.get(ev_slug, [])
        kmkts = [m for s in series_list for m in by_series.get(s, [])]
        if not pmkts or not kmkts:
            continue
        for pm in pmkts:
            pq = pm.get("question", "")
            pnorm = norm_q(pq)
            pkey = key_tokens(pq)
            pstrike = strike_tokens(pq)
            pwords = {w for w in pnorm.split() if len(w) > 3}
            pend = parse_iso(pm.get("endDate"))
            pres = poly_result(pm)
            if pend is None or pres is None:
                stats["result_fail"] += 1
                continue
            best = None
            for km in kmkts:
                pr = prep[km["ticker"]]
                if len(pwords & pr["words"]) < 2:
                    continue
                kclose = pr["close"]
                if kclose is None:
                    stats["date_fail"] += 1
                    continue
                skew = abs((kclose - pend).total_seconds()) / 3600.0
                if skew > MAX_DATE_SKEW_H:
                    stats["date_fail"] += 1
                    continue
                kres = pr["res"]
                if kres is None:
                    stats["result_fail"] += 1
                    continue
                # numbers/months must not CONTRADICT: if BOTH titles carry
                # numeric/date keys they must share at least one.
                if pkey and pr["key"] and not (pkey & pr["key"]):
                    stats["keytok_fail"] += 1
                    continue
                stats["considered"] += 1
                score = max(token_sort_ratio(pnorm, pr["n_full"]),
                            token_sort_ratio(pnorm, pr["n_title"]))
                if score < MIN_SCORE:
                    stats["score_fail"] += 1
                    continue
                if best is None or score > best["score"]:
                    best = {"score": score, "km": km, "skew": skew,
                            "kres": kres,
                            "strike_ok": pstrike == pr["strike"],
                            "sub_ok": strike_present(km.get("yes_sub_title") or "", pq),
                            "polarity_ok": not polarity_conflict(pq, pr["raw"])}
            if best is None:
                continue
            km = best["km"]
            toks = json.loads(km.get("clobTokenIds") or "[]") if False else []
            try:
                toks = json.loads(pm.get("clobTokenIds") or "[]")
            except (ValueError, TypeError):
                toks = []
            if len(toks) < 2:
                continue
            # NOTE: tier must NOT depend on outcome agreement — conditioning on
            # the realised result would select on the very thing the Brier test
            # measures.  Outcome agreement is reported as a match-quality
            # diagnostic only.
            if (best["score"] >= 78 and best["skew"] <= 48
                    and best["strike_ok"] and best["polarity_ok"]
                    and best["sub_ok"]):
                tier = "HIGH"
            elif best["score"] >= 70 and best["strike_ok"] and best["polarity_ok"]:
                tier = "MED"
            else:
                tier = "LOW"
            pairs.append({
                "poly_event": ev_slug,
                "poly_id": pm.get("id"),
                "poly_slug": pm.get("slug"),
                "poly_question": pq,
                "poly_token_yes": toks[0],
                "poly_end": pm.get("endDate"),
                "poly_result": pres,
                "poly_volume": float(pm.get("volumeNum") or 0.0),
                "kalshi_ticker": km.get("ticker"),
                "kalshi_event": km.get("event_ticker"),
                "kalshi_series": km.get("_series"),
                "kalshi_title": km.get("title"),
                "kalshi_sub": km.get("yes_sub_title"),
                "kalshi_close": km.get("close_time"),
                "kalshi_open": km.get("open_time"),
                "kalshi_result": best["kres"],
                "score": round(best["score"], 1),
                "strike_ok": best["strike_ok"],
                "polarity_ok": best["polarity_ok"],
                "sub_ok": best["sub_ok"],
                "date_skew_h": round(best["skew"], 1),
                "outcome_agree": best["kres"] == pres,
                "tier": tier,
            })

    # de-duplicate: one Kalshi ticker may be the best match for several Poly
    # markets in the same event; keep the highest-scoring assignment.
    bykt: Dict[str, dict] = {}
    for p in pairs:
        k = p["kalshi_ticker"]
        if k not in bykt or p["score"] > bykt[k]["score"]:
            bykt[k] = p
    final = sorted(bykt.values(), key=lambda p: -p["score"])

    json.dump(final, open(os.path.join(DATA, "pairs.json"), "w"), indent=1)
    print(json.dumps(stats, indent=1))
    print(f"pairs: {len(final)}  HIGH={sum(1 for p in final if p['tier']=='HIGH')}")
    print(f"outcome-agreement: "
          f"{sum(1 for p in final if p['outcome_agree'])}/{len(final)}")


if __name__ == "__main__":
    main()
