"""Empirically verify which NWS station settles each weather series.

For each series and candidate station: does the station's official CLI high
satisfy the winning bracket of each settled daily event? Match rate ~1.0
identifies the settlement station; ambiguous cities (two airports) resolve
themselves.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from weather_pipeline import _session

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

CANDIDATES = {
    "KXHIGHTATL":  ["KATL"],
    "KXHIGHDEN":   ["KDEN"],
    "KXHIGHTSEA":  ["KSEA"],
    "KXHIGHTOKC":  ["KOKC"],
    "KXHIGHTDC":   ["KDCA", "KIAD"],
    "KXHIGHTDAL":  ["KDAL", "KDFW"],
    "KXHIGHTBOS":  ["KBOS"],
    "KXHIGHTHOU":  ["KHOU", "KIAH"],
    "KXHIGHTSATX": ["KSAT"],
    "KXHIGHTMIN":  ["KMSP"],
}

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def cli_highs(station, year=2026):
    r = _session.get("https://mesonet.agron.iastate.edu/json/cli.py",
                     params={"station": station, "year": year}, timeout=45)
    r.raise_for_status()
    return {row["valid"]: row.get("high") for row in r.json().get("results", [])
            if row.get("high") is not None}


def event_date(event_ticker):
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker)
    if not m:
        return None
    y, mon, d = m.groups()
    return f"20{y}-{MONTHS[mon]:02d}-{int(d):02d}"


def satisfied(high, strike_type, floor, cap):
    if strike_type == "between":
        return floor <= high <= cap
    if strike_type == "greater":
        return high > floor
    if strike_type == "greater_or_equal":
        return high >= floor
    if strike_type == "less":
        return high < (cap if cap is not None else floor)
    if strike_type == "less_or_equal":
        return high <= (cap if cap is not None else floor)
    return None


def main():
    verdicts = {}
    for series, cands in CANDIDATES.items():
        path = DATA_DIR / "raw" / f"settled_{series}.json"
        if not path.exists():
            print(f"[skip] {series}: no settled cache")
            continue
        markets = json.load(open(path))
        winners = [m for m in markets if m.get("result") == "yes"]
        scores = {}
        for st in cands:
            try:
                highs = cli_highs(st)
            except Exception as e:
                print(f"[warn] {st}: {e}")
                continue
            hit = tot = 0
            for m in winners:
                d = event_date(m.get("event_ticker") or "")
                if d not in highs:
                    continue
                ok = satisfied(highs[d], m.get("strike_type"),
                               m.get("floor_strike"), m.get("cap_strike"))
                if ok is None:
                    continue
                tot += 1
                hit += int(ok)
            scores[st] = (hit, tot, hit / tot if tot else float("nan"))
        best = max(scores, key=lambda s: scores[s][2]) if scores else None
        verdicts[series] = {"scores": {s: [v[0], v[1], round(v[2], 3)]
                                       for s, v in scores.items()},
                            "station": best}
        print(series, scores, "->", best, flush=True)
    with open(DATA_DIR / "station_verification.json", "w") as f:
        json.dump(verdicts, f, indent=1)


if __name__ == "__main__":
    main()
