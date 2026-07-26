"""Instrumented closing-line lookup for the P-001 CLV forward test.

WHY THIS MODULE EXISTS
----------------------
`scripts/clv_settlement.py` used to inline a `close_fair()` with **five silent
`return None` paths** (HTTP failure, no team match, >3h commence delta, no
Pinnacle, Pinnacle-without-usable-h2h). All five looked identical to the caller,
so a capture miss was undiagnosable: the July-2026 "10 of 14 settled bets
produced no CLV record" question sat open for five days purely because nothing
recorded *which* branch fired.

This module returns `(result, reason)` for every call. `result` is exactly what
the old function returned (`(home, away, fair_home, commence)` or `None`), so
callers keep identical behaviour; `reason` is a stable tag from `REASONS` that
can be histogrammed.

It is deliberately transport-agnostic: pass your own `fetch` to replay from a
disk cache instead of spending Odds API historical credits, which are metered
and real.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime
from typing import Callable, Dict, Optional, Sequence, Tuple

from src.devig import american_to_prob, devig_two_way

BASE = "https://api.the-odds-api.com/v4"

#: Every distinguishable outcome of a closing-line lookup.
REASONS = (
    "OK",                  # priced
    "API_ERROR",           # HTTP/transport/JSON failure (incl. quota exhaustion)
    "SNAPSHOT_EMPTY",      # call succeeded, snapshot carried no events at all
    "NO_TEAM_MATCH",       # snapshot had events, none matched both team names
    "TIME_GT_3H",          # name-matched event, but commence >3h from expected
    "NO_PINNACLE",         # matched event carried no Pinnacle bookmaker
    "PINN_NO_H2H",         # Pinnacle present, no h2h market
    "PINN_NAME_MISMATCH",  # Pinnacle h2h present, outcome names not both found
)

MAX_COMMENCE_DELTA_S = 3 * 3600


def norm(s: str) -> str:
    """Lowercase letters only. Kept byte-identical to the production helper so
    the replay measures the *real* matcher, not a kinder one."""
    return re.sub(r"[^a-z]", "", s.lower())


def _http_fetch(url: str, timeout: float = 30.0) -> Dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def historical_url(api_key: str, expected: datetime,
                   sport: str = "baseball_mlb") -> str:
    """The Odds API historical-odds URL used for the closing snapshot.

    `sport` is a parameter rather than a constant because P-001 trades six
    sports while this job only ever asked baseball_mlb — see the capture
    report. Callers that need MLB keep the default.
    """
    return (f"{BASE}/historical/sports/{sport}/odds"
            f"?apiKey={api_key}&regions=eu&markets=h2h&oddsFormat=american"
            f"&date={expected.strftime('%Y-%m-%dT%H:%M:%SZ')}")


def close_fair(expected: datetime, names: Sequence[str], api_key: str,
               sport: str = "baseball_mlb",
               fetch: Optional[Callable[[str], Dict]] = None
               ) -> Tuple[Optional[Tuple[str, str, float, str]], str]:
    """De-vigged Pinnacle closing fair for the home side, plus a reason tag.

    Parameters
    ----------
    expected : timezone-aware UTC datetime of the expected event start.
    names    : the two team names, ALREADY passed through `norm()`.
    fetch    : callable(url) -> parsed JSON. Defaults to a live HTTP GET.
               Pass a cache-backed callable to avoid metered credits.

    Returns
    -------
    ((home_team, away_team, fair_home_prob, commence_time_iso), reason)
    or (None, reason).
    """
    fetch = fetch or _http_fetch
    try:
        d = fetch(historical_url(api_key, expected, sport))
    except Exception:
        return None, "API_ERROR"

    data = d.get("data") or []
    if not data:
        return None, "SNAPSHOT_EMPTY"

    want = set(names)
    cands = [e for e in data
             if {norm(e["home_team"]), norm(e["away_team"])} == want]
    if not cands:
        return None, "NO_TEAM_MATCH"

    def _delta(e):
        c = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
        return abs((c - expected).total_seconds())

    e = min(cands, key=_delta)
    if _delta(e) > MAX_COMMENCE_DELTA_S:
        return None, "TIME_GT_3H"

    for b in e.get("bookmakers", []):
        if b["key"] != "pinnacle":
            continue
        h = next((m for m in b["markets"] if m["key"] == "h2h"), None)
        if not h:
            return None, "PINN_NO_H2H"
        px = {o["name"]: o["price"] for o in h["outcomes"]}
        if e["home_team"] in px and e["away_team"] in px:
            fh = devig_two_way(american_to_prob(px[e["home_team"]]),
                               american_to_prob(px[e["away_team"]]))
            return (e["home_team"], e["away_team"], fh, e["commence_time"]), "OK"
        return None, "PINN_NAME_MISMATCH"
    return None, "NO_PINNACLE"
