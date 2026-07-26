"""Kalshi MLB ticker → team names.

A Kalshi MLB game ticker encodes the matchup as concatenated team codes:

    KXMLBGAME-26JUL201910BALBOS-BOS
                         ^^^^^^ away=BAL home=BOS

The CLV settlement job used to take its team names from the trade row's
free-text `event` field instead. That field records the *Odds API event the pod
priced from*, which is not always the market it traded — measured 2026-07-26,
4 of 671 settled MLB rows carried an `event` naming two teams that appear
nowhere in their own ticker (e.g. ticker `...BALBOS-BOS`, event "Houston Astros
vs Baltimore Orioles"). Those rows can never match a closing snapshot.

The ticker is the market that was actually traded, so it is the correct source
of truth for pricing that market's close.

Codes are 2 or 3 characters and are split by trying the 2/3 boundary and
keeping the split where **both** halves are known codes.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

#: Kalshi MLB team code → the Odds API `home_team`/`away_team` string.
KALSHI_TO_ODDS_API = {
    "ATL": "Atlanta Braves",
    "AZ": "Arizona Diamondbacks",
    "ARI": "Arizona Diamondbacks",
    "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs",
    "CIN": "Cincinnati Reds",
    "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies",
    "CWS": "Chicago White Sox",
    "DET": "Detroit Tigers",
    "HOU": "Houston Astros",
    "KC": "Kansas City Royals",
    "LAA": "Los Angeles Angels",
    "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",
    "NYM": "New York Mets",
    "NYY": "New York Yankees",
    # Kalshi carries both spellings across the 2025 rename; the Odds API
    # dropped the city entirely.
    "OAK": "Athletics",
    "ATH": "Athletics",
    "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates",
    "SD": "San Diego Padres",
    "SEA": "Seattle Mariners",
    "SF": "San Francisco Giants",
    "STL": "St. Louis Cardinals",
    "TB": "Tampa Bay Rays",
    "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",
    "WSH": "Washington Nationals",
}

# 11 chars of YYMONDDHHMM before the team codes: 26JUL201910
_CODE_OFFSET = 11

# Doubleheaders append a game number: ...26JUL071945MILSTLG2. Observed on 4 of
# 541 distinct MLB tickers in the P-001 corpus.
_GAME_SUFFIX_RE = re.compile(r"G\d$")


def split_codes(blob: str) -> Optional[Tuple[str, str]]:
    """('BAL', 'BOS') from 'BALBOS'. None if no unambiguous split exists."""
    hits = []
    for i in (2, 3):
        a, b = blob[:i], blob[i:]
        if a in KALSHI_TO_ODDS_API and b in KALSHI_TO_ODDS_API:
            hits.append((a, b))
    return hits[0] if len(hits) == 1 else None


def teams_from_mlb_ticker(ticker: str) -> Optional[Tuple[str, str]]:
    """(away_team, home_team) as Odds API names, or None if unparseable.

    >>> teams_from_mlb_ticker("KXMLBGAME-26JUL201910BALBOS-BOS")
    ('Baltimore Orioles', 'Boston Red Sox')
    """
    parts = (ticker or "").split("-")
    if len(parts) < 2 or not parts[0].upper().startswith("KXMLBGAME"):
        return None
    blob = _GAME_SUFFIX_RE.sub("", parts[1][_CODE_OFFSET:])
    codes = split_codes(blob)
    if codes is None:
        return None
    return KALSHI_TO_ODDS_API[codes[0]], KALSHI_TO_ODDS_API[codes[1]]
