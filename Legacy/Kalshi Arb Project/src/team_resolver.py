"""
src/team_resolver.py
────────────────────
Static NBA team name ↔ abbreviation mapping for cross-tier market resolution.

Kalshi uses different naming conventions across market tiers:
  Game markets:       KXNBAGAME-26FEB23BOSKNK-BOS   (abbreviations in ticker)
  Playoff markets:    KXNBAPLAYOFF-26-BOS            (abbreviation suffix)
  Conference markets: KXNBAEAST-26-BOS               (abbreviation suffix)
  Championship:       KXNBA-26-BOS                   (abbreviation suffix)

This module provides a canonical team identity so the consistency checker
can group markets across tiers for the same team.
"""

from __future__ import annotations

import difflib
import re
from typing import Dict, Optional

from logger_setup import get_logger

log = get_logger(__name__)

# ── NBA Teams (30 teams, 2025-26 season) ─────────────────────────────────────

NBA_TEAMS: Dict[str, str] = {
    "ATL": "Atlanta Hawks",
    "BOS": "Boston Celtics",
    "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls",
    "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks",
    "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets",
    "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers",
    "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans",
    "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers",
    "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers",
    "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz",
    "WAS": "Washington Wizards",
}

# Reverse lookup: full name → abbreviation
NBA_TEAMS_REVERSE: Dict[str, str] = {v: k for k, v in NBA_TEAMS.items()}

# Common alternate abbreviations used by Kalshi or other sources
_ALTERNATE_ABBREVS: Dict[str, str] = {
    "GS":  "GSW",
    "NO":  "NOP",
    "NY":  "NYK",
    "SA":  "SAS",
    "PHO": "PHX",
    "UTAH": "UTA",
    "BKLYN": "BKN",
    "CHAR": "CHA",
    "WASH": "WAS",
    "PHIL": "PHI",
    "PORT": "POR",
    "MINN": "MIN",
    "OKL": "OKC",
    "OKLA": "OKC",
    "LAK": "LAL",
    "CLIP": "LAC",
    "LAKERS": "LAL",
    "CLIPPERS": "LAC",
    "CELTICS": "BOS",
    "KNICKS": "NYK",
    "WARRIORS": "GSW",
    "NETS": "BKN",
    "SIXERS": "PHI",
    "76ERS": "PHI",
    "HEAT": "MIA",
    "BUCKS": "MIL",
    "CAVS": "CLE",
    "CAVALIERS": "CLE",
    "HAWKS": "ATL",
    "BULLS": "CHI",
    "MAVS": "DAL",
    "MAVERICKS": "DAL",
    "NUGGETS": "DEN",
    "PISTONS": "DET",
    "ROCKETS": "HOU",
    "PACERS": "IND",
    "GRIZZLIES": "MEM",
    "WOLVES": "MIN",
    "TIMBERWOLVES": "MIN",
    "PELICANS": "NOP",
    "THUNDER": "OKC",
    "MAGIC": "ORL",
    "SUNS": "PHX",
    "BLAZERS": "POR",
    "KINGS": "SAC",
    "SPURS": "SAS",
    "RAPTORS": "TOR",
    "JAZZ": "UTA",
    "WIZARDS": "WAS",
    "HORNETS": "CHA",
}

# Eastern vs Western conference for series ticker routing
NBA_EAST: frozenset = frozenset({
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DET", "IND",
    "MIA", "MIL", "NYK", "ORL", "PHI", "TOR", "WAS",
})
NBA_WEST: frozenset = frozenset({
    "DAL", "DEN", "GSW", "HOU", "LAC", "LAL", "MEM", "MIN",
    "NOP", "OKC", "PHX", "POR", "SAC", "SAS", "UTA",
})

# ── Regex patterns for extracting team from Kalshi tickers ────────────────────

# Futures-style tickers: KXNBAPLAYOFF-26-BOS, KXNBA-26-BOS, KXNBAEAST-26-BOS
_FUTURES_TICKER_RE = re.compile(
    r"^KX(?:NBA|NCAA[A-Z]*)"   # prefix
    r"(?:PLAYOFF|EAST|WEST|SERIES)?"  # optional tier
    r"-\d{2}"                    # year suffix
    r"-([A-Z]{2,5})$"           # team abbreviation (capture group)
)

# Game-style tickers: KXNBAGAME-26FEB23BOSKNK-BOS
_GAME_TICKER_RE = re.compile(
    r"^KX(?:NBA|NCAA[A-Z]*)GAME"   # game prefix
    r"-\d{2}[A-Z]{3}\d{2}"         # date portion
    r"[A-Z]+"                       # team codes combined
    r"-([A-Z]{2,5})$"              # team abbreviation (capture group)
)

# "Will [team name] win?" pattern in titles
_WILL_WIN_RE = re.compile(r"\bWill\s+(.+?)\s+win\b", re.IGNORECASE)


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_team_from_ticker(ticker: str) -> Optional[str]:
    """
    Extract the canonical NBA abbreviation from a Kalshi market ticker.

    Returns the abbreviation (e.g., "BOS") or None if unrecognizable.

    Examples
    --------
    >>> resolve_team_from_ticker("KXNBAPLAYOFF-26-BOS")
    'BOS'
    >>> resolve_team_from_ticker("KXNBAGAME-26FEB23BOSKNK-BOS")
    'BOS'
    >>> resolve_team_from_ticker("KXNBA-26-LAL")
    'LAL'
    """
    for pattern in (_FUTURES_TICKER_RE, _GAME_TICKER_RE):
        m = pattern.match(ticker)
        if m:
            raw = m.group(1).upper()
            return _normalize_abbrev(raw)

    # Fallback: try last hyphen segment
    if "-" in ticker:
        suffix = ticker.rsplit("-", 1)[-1].upper()
        resolved = _normalize_abbrev(suffix)
        if resolved:
            return resolved

    return None


def resolve_team_from_title(title: str) -> Optional[str]:
    """
    Extract the canonical NBA abbreviation from a Kalshi market title.

    Uses "Will [X] win?" pattern, then falls back to fuzzy matching
    against known NBA team names.

    Examples
    --------
    >>> resolve_team_from_title("Will Boston Celtics win the NBA Championship?")
    'BOS'
    >>> resolve_team_from_title("Will the Lakers make the playoffs?")
    'LAL'
    """
    m = _WILL_WIN_RE.search(title)
    if m:
        team_text = m.group(1).strip()
        # Remove articles and prepositions
        team_text = re.sub(r"\b(the|a|an)\b", "", team_text, flags=re.IGNORECASE).strip()
        resolved = _match_team_name(team_text)
        if resolved:
            return resolved

    # Fallback: try matching the whole title against known team names
    return _match_team_name(title)


def resolve_team(ticker: str, title: str) -> Optional[str]:
    """
    Resolve team from both ticker and title, preferring ticker (more reliable).

    Parameters
    ----------
    ticker : str
        Kalshi market ticker.
    title : str
        Kalshi market title.

    Returns
    -------
    Canonical NBA abbreviation (e.g., "BOS") or None.
    """
    result = resolve_team_from_ticker(ticker)
    if result:
        return result
    return resolve_team_from_title(title)


def normalize_team(name: str) -> Optional[str]:
    """
    Normalize any team name string to a canonical abbreviation.

    Accepts: full names, abbreviations, nicknames, alternate abbreviations.
    Returns None if no match found.

    Examples
    --------
    >>> normalize_team("Boston Celtics")
    'BOS'
    >>> normalize_team("BOS")
    'BOS'
    >>> normalize_team("Celtics")
    'BOS'
    >>> normalize_team("GS")
    'GSW'
    """
    upper = name.strip().upper()

    # Direct abbreviation match
    if upper in NBA_TEAMS:
        return upper

    # Alternate abbreviation
    if upper in _ALTERNATE_ABBREVS:
        return _ALTERNATE_ABBREVS[upper]

    # Full name match (case-insensitive)
    lower = name.strip().lower()
    for full_name, abbrev in NBA_TEAMS_REVERSE.items():
        if full_name.lower() == lower:
            return abbrev

    # Nickname match
    return _match_team_name(name)


def get_conference(abbrev: str) -> Optional[str]:
    """
    Return "east" or "west" for an NBA team abbreviation.

    Returns None if abbreviation is not recognized.
    """
    if abbrev in NBA_EAST:
        return "east"
    if abbrev in NBA_WEST:
        return "west"
    return None


def abbrev_to_full(abbrev: str) -> Optional[str]:
    """Return the full NBA team name for an abbreviation, or None."""
    return NBA_TEAMS.get(abbrev)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _normalize_abbrev(raw: str) -> Optional[str]:
    """
    Normalize a raw abbreviation to a canonical one.

    Checks direct NBA_TEAMS keys first, then alternate abbreviations.
    Returns None if unrecognized.
    """
    upper = raw.upper()
    if upper in NBA_TEAMS:
        return upper
    if upper in _ALTERNATE_ABBREVS:
        return _ALTERNATE_ABBREVS[upper]
    return None


def _match_team_name(text: str) -> Optional[str]:
    """
    Fuzzy-match a text string against known NBA team names and nicknames.

    Returns the canonical abbreviation of the best match, or None if
    no match exceeds the similarity threshold.
    """
    text_lower = text.strip().lower()
    if not text_lower:
        return None

    # First try: exact substring match against nicknames (fast path)
    for nickname, abbrev in _ALTERNATE_ABBREVS.items():
        if nickname.lower() in text_lower:
            return abbrev

    # Second try: exact substring match against full names
    for full_name, abbrev in NBA_TEAMS_REVERSE.items():
        if full_name.lower() in text_lower:
            return abbrev

    # Third try: fuzzy match against full team names
    best_score = 0.0
    best_abbrev: Optional[str] = None

    candidates = list(NBA_TEAMS_REVERSE.keys())
    for full_name in candidates:
        # Token-sort ratio
        def _norm(s: str) -> str:
            return " ".join(sorted(s.lower().split()))
        score = difflib.SequenceMatcher(None, _norm(text), _norm(full_name)).ratio()
        if score > best_score:
            best_score = score
            best_abbrev = NBA_TEAMS_REVERSE[full_name]

    # Require at least 75% similarity for fuzzy match
    if best_score >= 0.75 and best_abbrev:
        return best_abbrev

    return None
