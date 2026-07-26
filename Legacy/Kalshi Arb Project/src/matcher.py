"""
src/matcher.py
──────────────
Matches Kalshi moneyline markets to The Odds API events using fuzzy team-name
comparison and a start-time proximity window.

Architecture
────────────
  _fuzzy_score(a, b)    — token-sort ratio (0–100) via stdlib difflib;
                          mirrors rapidfuzz.fuzz.token_sort_ratio.
  MatchResult           — frozen dataclass returned on successful match.
  UnmatchedEntry        — internal record appended to the unmatched JSONL log.
  Matcher               — stateless core; call .match() per Kalshi market.

Match algorithm (moneyline only, Phase 5)
──────────────────────────────────────────
  1. Reject markets flagged as spread / total / prop → MARKET_TYPE_UNSUPPORTED
  2. Reject markets for unconfigured sports          → SPORT_NOT_CONFIGURED
  3. No OddsEvents provided                          → ODDS_API_NO_DATA
  4. Best token-sort score < fuzzy_threshold         → NO_FUZZY_MATCH
  5. |start-time delta| > time_window_minutes        → TIME_WINDOW_EXCEEDED
  6. Otherwise                                       → MatchResult returned

Unmatched logging
─────────────────
Every rejection appends one JSON line to the path in
config.paths.unmatched_log (default data/trade_logs/unmatched_markets.jsonl).
The format matches data/schema/unmatched_markets.schema.json.

Fuzzy matching
──────────────
token_sort_ratio:
  - lower-case both strings
  - split on whitespace, sort tokens alphabetically, re-join
  - compute difflib.SequenceMatcher ratio → scale to 0–100

This handles word-order differences (e.g. "Chiefs Raiders" vs
"Kansas City Chiefs vs Las Vegas Raiders") gracefully.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from logger_setup import get_logger
from odds_client import OddsEvent

log = get_logger(__name__)

# ── Title normalisation ────────────────────────────────────────────────────────

# Words that appear in Kalshi market titles but not in team names, and which
# hurt fuzzy scores without adding signal (e.g. "winner?", "at", "vs").
_TITLE_NOISE = re.compile(
    r"\b(will|win|winner|winners|at|vs\.?|v|the|by|over|in|match|game|series|qualification|final|round)\b|[?!:()]",
    re.IGNORECASE,
)

# Detects single-outcome "Will X win?" markets where only one team appears in
# the title.  Used to skip the _both_teams_present check — that check was
# designed for game-level (h2h) titles; it's counter-productive here because
# "Will UK win?" intentionally names only the YES-side team.
_SINGLE_TEAM_TITLE_RE = re.compile(r"\bWill\s+.+?\s+win\b", re.IGNORECASE)


def _normalize_title(s: str) -> str:
    """
    Strip Kalshi title noise words before fuzzy matching.

    e.g. "Connecticut vs Arizona winner?" → "Connecticut Arizona"
    """
    return " ".join(_TITLE_NOISE.sub(" ", s).split())


# ── NCAAB college team abbreviation lookup ─────────────────────────────────────
# Kalshi uses 2–5 letter school codes in NCAAB market tickers and titles
# (e.g. UK, ISU, KU, SJU, MIA, PUR).  These score below min_team_score=50
# when compared against Odds API full names ("Kentucky Wildcats", etc.).
# Map each code → the canonical full name used by The Odds API so fuzzy
# matching resolves correctly.
#
# Ordering: more specific / less ambiguous codes first; ambiguous codes use the
# highest-profile NCAAB program (verified against Odds API March Madness data).
_NCAAB_ABBREV_MAP: Dict[str, str] = {
    # ── Explicitly mentioned in issue ──────────────────────────────────────
    "UK":    "Kentucky Wildcats",
    "ISU":   "Iowa State Cyclones",
    "KU":    "Kansas Jayhawks",
    "SJU":   "St John's Red Storm",
    "MIA":   "Miami Hurricanes",
    "PUR":   "Purdue Boilermakers",
    # ── ACC ────────────────────────────────────────────────────────────────
    "UNC":   "North Carolina Tar Heels",
    "DUKE":  "Duke Blue Devils",
    "UVA":   "Virginia Cavaliers",
    "VT":    "Virginia Tech Hokies",
    "SYR":   "Syracuse Orange",
    "PITT":  "Pittsburgh Panthers",
    "CLEM":  "Clemson Tigers",
    "GT":    "Georgia Tech Yellow Jackets",
    "ND":    "Notre Dame Fighting Irish",
    "WAKE":  "Wake Forest Demon Deacons",
    "NCSU":  "NC State Wolfpack",
    "FSU":   "Florida State Seminoles",
    "LOU":   "Louisville Cardinals",
    "BC":    "Boston College Eagles",
    # ── Big Ten ────────────────────────────────────────────────────────────
    "MSU":   "Michigan State Spartans",
    "OSU":   "Ohio State Buckeyes",
    "MICH":  "Michigan Wolverines",
    "PSU":   "Penn State Nittany Lions",
    "ILL":   "Illinois Fighting Illini",
    "IU":    "Indiana Hoosiers",
    "IND":   "Indiana Hoosiers",
    "IOWA":  "Iowa Hawkeyes",
    "MINN":  "Minnesota Golden Gophers",
    "NEB":   "Nebraska Cornhuskers",
    "NW":    "Northwestern Wildcats",
    "NU":    "Northwestern Wildcats",
    "RUT":   "Rutgers Scarlet Knights",
    "WIS":   "Wisconsin Badgers",
    "MD":    "Maryland Terrapins",
    # ── Big 12 ────────────────────────────────────────────────────────────
    "KSU":   "Kansas State Wildcats",
    "OU":    "Oklahoma Sooners",
    "TCU":   "TCU Horned Frogs",
    "WVU":   "West Virginia Mountaineers",
    "BYU":   "BYU Cougars",
    "CIN":   "Cincinnati Bearcats",
    "HOU":   "Houston Cougars",
    "UCF":   "UCF Knights",
    "SMU":   "SMU Mustangs",
    "AZ":    "Arizona Wildcats",
    "ARIZ":  "Arizona Wildcats",
    "UTAH":  "Utah Utes",
    "COL":   "Colorado Buffaloes",
    "WSU":   "Washington State Cougars",
    "TEX":   "Texas Longhorns",
    "TAMU":  "Texas A&M Aggies",
    # ── SEC ────────────────────────────────────────────────────────────────
    "LSU":   "LSU Tigers",
    "ALA":   "Alabama Crimson Tide",
    "BAMA":  "Alabama Crimson Tide",
    "UGA":   "Georgia Bulldogs",
    "AUB":   "Auburn Tigers",
    "TEN":   "Tennessee Volunteers",
    "TENN":  "Tennessee Volunteers",
    "ARK":   "Arkansas Razorbacks",
    "FLA":   "Florida Gators",
    "UF":    "Florida Gators",
    "MIZ":   "Missouri Tigers",
    "MIZZ":  "Missouri Tigers",
    "VAN":   "Vanderbilt Commodores",
    "VAND":  "Vanderbilt Commodores",
    "SC":    "South Carolina Gamecocks",
    # ── Big East ───────────────────────────────────────────────────────────
    "GONZ":  "Gonzaga Bulldogs",
    "MARQ":  "Marquette Golden Eagles",
    "CREI":  "Creighton Bluejays",
    "VIL":   "Villanova Wildcats",
    "NOVA":  "Villanova Wildcats",
    "GEO":   "Georgetown Hoyas",
    "PROV":  "Providence Friars",
    "XAV":   "Xavier Musketeers",
    "XAVI":  "Xavier Musketeers",
    "BUT":   "Butler Bulldogs",
    "UCONN": "Connecticut Huskies",
    "CONN":  "Connecticut Huskies",
    # ── Mountain West / West ───────────────────────────────────────────────
    "SDSU":  "San Diego State Aztecs",
    "UNLV":  "UNLV Runnin Rebels",
    "UNL":   "UNLV Runnin Rebels",      # ticker code variant
    "UCLA":  "UCLA Bruins",
    "USC":   "USC Trojans",
    "ORE":   "Oregon Ducks",
    "ORST":  "Oregon State Beavers",
    "WASH":  "Washington Huskies",
    "STAN":  "Stanford Cardinal",
    # ── American Athletic / Other ──────────────────────────────────────────
    "MEMPH": "Memphis Tigers",
    "MEM":   "Memphis Tigers",
    "VCU":   "VCU Rams",
    "UAB":   "UAB Blazers",
    "TUL":   "Tulane Green Wave",
    "RICE":  "Rice Owls",
    "WKU":   "Western Kentucky Hilltoppers",
    "MURR":  "Murray State Racers",
    "DREX":  "Drexel Dragons",
    "TEMP":  "Temple Owls",
    "PENN":  "Pennsylvania Quakers",
    "URI":   "Rhode Island Rams",
    # ── Session 9: codes from active March Madness 2026 tickers ──────────
    # These were causing NO_FUZZY_MATCH / TEAM_MISMATCH on the VPS.
    # Names match Odds API exactly (e.g. "Wichita St Shockers", not "Wichita State").
    "WICH":  "Wichita St Shockers",
    "TLSA":  "Tulsa Golden Hurricane",
    "JOES":  "Saint Joseph's Hawks",
    "UNM":   "New Mexico Lobos",
    "NEV":   "Nevada Wolf Pack",
    "ILST":  "Illinois St Redbirds",
    "DAY":   "Dayton Flyers",
    "ARS":   "Arizona State Sun Devils",
    "TTU":   "Texas Tech Red Raiders",
    "BAY":   "Baylor Bears",
    "OKLA":  "Oklahoma Sooners",
    "OKST":  "Oklahoma State Cowboys",
    "STAN":  "Stanford Cardinal",
    # ── NIT / CBI / smaller tournaments (less common) ────────────────────
    "NWI":   "Northwest Indiana",
    "POR":   "Portland Pilots",
    "LBS":   "Long Beach State 49ers",
    "FRB":   "Furman Paladins",
    "DAV":   "Davidson Wildcats",
    "CPM":   "Cal Poly Mustangs",
    "FGCU":  "Florida Gulf Coast Eagles",
    "CHSO":  "Charleston Southern Buccaneers",
    "UIC":   "UIC Flames",
    "NE":    "Northeastern Huskies",
    # ── Additional common tournament codes ────────────────────────────────
    "MSST":  "Mississippi State Bulldogs",
    "MISS":  "Ole Miss Rebels",
    "FAU":   "FAU Owls",
    "NMSU":  "New Mexico State Aggies",
    "USU":   "Utah State Aggies",
    "CSU":   "Colorado State Rams",
    "BSU":   "Boise State Broncos",
    "UNR":   "Nevada Wolf Pack",          # alternate code
    "SJSU":  "San Jose State Spartans",
    "SHSU":  "Sam Houston Bearkats",
    "APST":  "Appalachian State Mountaineers",
    "ETSU":  "East Tennessee State Buccaneers",
    "MTSU":  "Middle Tennessee Blue Raiders",
    "UMBC":  "UMBC Retrievers",
    "STBK":  "Stony Brook Seawolves",
    "SFA":   "Stephen F. Austin Lumberjacks",
    "GRAM":  "Grambling Tigers",
    "DRAKE": "Drake Bulldogs",
    "BRAD":  "Bradley Braves",
    "EVAN":  "Evansville Purple Aces",
    "MOH":   "Morehead State Eagles",
    "OAK":   "Oakland Golden Grizzlies",
    "FAIR":  "Fairleigh Dickinson Knights",
    "LONG":  "Longwood Lancers",
    "WINT":  "Winthrop Eagles",
    "CHATT": "Chattanooga Mocs",
    "WRST":  "Wright State Raiders",
}

# ── MLB Teams (30 teams, 2026 season) ────────────────────────────────────────
# Maps Kalshi ticker abbreviations → full team names used by The Odds API.
# Needed because Kalshi uses 2-3 letter city abbreviations (e.g. "TB", "KC")
# that are too short for fuzzy scoring to resolve against full names like
# "Tampa Bay Rays" or "Kansas City Royals".
_MLB_ABBREV_MAP: Dict[str, str] = {
    # ── AL East ──────────────────────────────────────────────────────────────
    "BAL":  "Baltimore Orioles",
    "BOS":  "Boston Red Sox",
    "NYY":  "New York Yankees",
    "TB":   "Tampa Bay Rays",
    "TOR":  "Toronto Blue Jays",
    # ── AL Central ───────────────────────────────────────────────────────────
    "CWS":  "Chicago White Sox",
    "CLE":  "Cleveland Guardians",
    "DET":  "Detroit Tigers",
    "KC":   "Kansas City Royals",
    "MIN":  "Minnesota Twins",
    # ── AL West ──────────────────────────────────────────────────────────────
    "HOU":  "Houston Astros",
    "LAA":  "Los Angeles Angels",
    "ATH":  "Athletics",
    "SEA":  "Seattle Mariners",
    "TEX":  "Texas Rangers",
    # ── NL East ──────────────────────────────────────────────────────────────
    "ATL":  "Atlanta Braves",
    "MIA":  "Miami Marlins",
    "NYM":  "New York Mets",
    "PHI":  "Philadelphia Phillies",
    "WSH":  "Washington Nationals",
    # ── NL Central ───────────────────────────────────────────────────────────
    "CHC":  "Chicago Cubs",
    "CIN":  "Cincinnati Reds",
    "MIL":  "Milwaukee Brewers",
    "PIT":  "Pittsburgh Pirates",
    "STL":  "St. Louis Cardinals",
    # ── NL West ──────────────────────────────────────────────────────────────
    "AZ":   "Arizona Diamondbacks",
    "COL":  "Colorado Rockies",
    "LAD":  "Los Angeles Dodgers",
    "SD":   "San Diego Padres",
    "SF":   "San Francisco Giants",
}

# ── NHL Teams (32 teams, 2025-26 season) ─────────────────────────────────────
_NHL_ABBREV_MAP: Dict[str, str] = {
    # ── Atlantic ─────────────────────────────────────────────────────────────
    "BOS":  "Boston Bruins",
    "BUF":  "Buffalo Sabres",
    "DET":  "Detroit Red Wings",
    "FLA":  "Florida Panthers",
    "MTL":  "Montreal Canadiens",
    "OTT":  "Ottawa Senators",
    "TB":   "Tampa Bay Lightning",
    "TOR":  "Toronto Maple Leafs",
    # ── Metropolitan ─────────────────────────────────────────────────────────
    "CAR":  "Carolina Hurricanes",
    "CBJ":  "Columbus Blue Jackets",
    "NJ":   "New Jersey Devils",
    "NYI":  "New York Islanders",
    "NYR":  "New York Rangers",
    "PHI":  "Philadelphia Flyers",
    "PIT":  "Pittsburgh Penguins",
    "WSH":  "Washington Capitals",
    # ── Central ──────────────────────────────────────────────────────────────
    "ARI":  "Utah Hockey Club",
    "CHI":  "Chicago Blackhawks",
    "COL":  "Colorado Avalanche",
    "DAL":  "Dallas Stars",
    "MIN":  "Minnesota Wild",
    "NSH":  "Nashville Predators",
    "STL":  "St. Louis Blues",
    "WPG":  "Winnipeg Jets",
    # ── Pacific ──────────────────────────────────────────────────────────────
    "ANA":  "Anaheim Ducks",
    "CGY":  "Calgary Flames",
    "EDM":  "Edmonton Oilers",
    "LA":   "Los Angeles Kings",
    "SJ":   "San Jose Sharks",
    "SEA":  "Seattle Kraken",
    "VAN":  "Vancouver Canucks",
    "VGK":  "Vegas Golden Knights",
}

# Unified sport-specific abbreviation maps keyed by Odds API sport key.
# Used by scanner._infer_yes_side to expand short ticker suffixes.
_SPORT_ABBREV_MAPS: Dict[str, Dict[str, str]] = {
    "baseball_mlb":    _MLB_ABBREV_MAP,
    "icehockey_nhl":   _NHL_ABBREV_MAP,
}

# Sports keys that use NCAAB/NCAAW abbreviations (women's teams share the same
# school codes as men's — e.g. Kentucky, Iowa State, etc.)
_NCAAB_SPORTS: frozenset = frozenset({"basketball_ncaab", "basketball_ncaaw"})

# Regex that matches a whole-word abbreviation token (with optional trailing
# punctuation) so we can do a clean lookup without false-positives.
_ABBREV_TOKEN_RE = re.compile(r"^([A-Za-z]+)[^A-Za-z]*$")


def _expand_ncaab_abbrevs(text: str) -> str:
    """
    Replace known NCAAB school abbreviations with full team names.

    Expansion rules (applied per whitespace-separated token):
      • The alphabetic part of the token must be ALL-UPPERCASE (e.g. "UK",
        "ISU", "WAKE") **or** at most 2 characters long (e.g. "uk").
      • This prevents mixed-case first-words like "Wake", "Iowa", "Duke" from
        being misidentified as abbreviations when the Kalshi title already
        contains the full team name.
      • Trailing punctuation (e.g. "?" in "UK?") is preserved.

    Unknown / non-matching tokens pass through unchanged so NBA / NFL titles
    are never altered.

    Examples
    --------
    >>> _expand_ncaab_abbrevs("Will UK win?")
    "Will Kentucky Wildcats win?"
    >>> _expand_ncaab_abbrevs("UK vs ISU")
    "Kentucky Wildcats vs Iowa State Cyclones"
    >>> _expand_ncaab_abbrevs("Wake Forest vs Virginia winner?")
    "Wake Forest vs Virginia winner?"   # full name — unchanged
    >>> _expand_ncaab_abbrevs("Lakers vs Warriors")
    "Lakers vs Warriors"                # no NCAAB codes → unchanged
    """
    tokens = text.split()
    result = []
    for token in tokens:
        m = _ABBREV_TOKEN_RE.match(token)
        if m:
            word = m.group(1)
            # Only expand if the token is all-uppercase (a genuine abbreviation)
            # or is very short (≤2 chars, handles lowercase "uk" etc.).
            # Mixed-case tokens like "Wake" or "Iowa" are already full words.
            if word == word.upper() or len(word) <= 2:
                full = _NCAAB_ABBREV_MAP.get(word.upper())
                if full:
                    suffix = token[len(word):]
                    result.append(full + suffix)
                    continue
        result.append(token)
    return " ".join(result)

# ── Reason codes (must match unmatched_markets.schema.json enum) ──────────────

REASON_NO_FUZZY_MATCH          = "NO_FUZZY_MATCH"
REASON_TIME_WINDOW_EXCEEDED    = "TIME_WINDOW_EXCEEDED"
REASON_SPORT_NOT_CONFIGURED    = "SPORT_NOT_CONFIGURED"
REASON_MARKET_TYPE_UNSUPPORTED = "MARKET_TYPE_UNSUPPORTED"
REASON_ODDS_API_NO_DATA        = "ODDS_API_NO_DATA"
REASON_TEAM_MISMATCH           = "TEAM_MISMATCH"
REASON_TICKER_PREFIX_BLOCKED   = "TICKER_PREFIX_BLOCKED"

# Keywords that identify non-moneyline Kalshi markets
_NON_MONEYLINE_KEYWORDS = frozenset(
    {"spread", "over/under", "total", "prop", "futures", "handicap"}
)

# Ticker prefixes for exotic / non-h2h Kalshi markets that can never match
# Odds API h2h events.  Skipping these early avoids thousands of futile
# fuzzy-match comparisons per scan cycle.
_BLOCKED_TICKER_PREFIXES = (
    "KXUCLGOAL",          # UCL goal-scorer props
    "KXMVEC",             # Cross-category, championship, esports multi-game
    "KXQUICKSETTLE",      # Quick-settle novelty markets
    "KXMVESPORTS",        # Esports multi-game extended
    "KXSOCCERGOAL",       # Soccer goal-scorer props
    "KXSOCCERCARD",       # Soccer card props
    "KXPLAYERPROPS",      # Generic player props
    "KXFIRSTGOAL",        # First goal scorer
    "KXPENALTY",           # Penalty markets
    # ── First-half winner markets — Odds API has no first-half lines ────
    "KXNCAAMB1H",         # NCAAB Men's first-half winner
    "KXNCAABB1H",         # NCAAB (alternate prefix) first-half winner
    "KXNBAGAME1H",        # NBA first-half winner
    "KXNBA1H",            # NBA first-half (alternate)
    "KXNHL1H",            # NHL first-half/period winner
    "KXNFL1H",            # NFL first-half winner
    "KXMLB1H",            # MLB first-half (first 5 innings)
)


# ── Event start time from the ticker ──────────────────────────────────────────
#
# Kalshi encodes the ET wall-clock start in the ticker body: the segment
# 26JUL201910 in KXMLBGAME-26JUL201910BALBOS-BOS is 2026-07-20 19:10 ET.
# Verified against 650 P-001 MLB games on 2026-07-26: the Odds API
# commence_time for the matched game sits a MEDIAN OF ONE MINUTE from the
# ticker-encoded start, i.e. this is a far better event-start reference than
# close_time (which is the market's *closing* stamp, hours later, and per
# CLAUDE.md is a far-future placeholder on several Kalshi series).
#
# Deliberately implemented here rather than imported from src.et_time: legacy
# modules are loaded by bare name off a separate sys.path entry, and adding a
# cross-package import would couple them.

_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

# Only matches when the ticker carries a TIME as well as a date. Date-only
# tickers (KXNBAGAME-26APR03UTAHOU) deliberately fall through to close_time.
_TICKER_DATETIME_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})")

try:                                    # pragma: no cover - trivial
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                       # pragma: no cover - host without tzdata
    _ET = timezone(timedelta(hours=-4))


def _ticker_start_time(ticker: str) -> Optional[datetime]:
    """UTC start encoded in a Kalshi ticker, or None if it encodes no time."""
    parts = (ticker or "").split("-")
    if len(parts) < 2:
        return None
    m = _TICKER_DATETIME_RE.match(parts[1])
    if not m:
        return None
    yy, mon, dd, hh, mm = m.groups()
    month = _MONTHS.get(mon)
    if month is None:
        return None
    try:
        local = datetime(2000 + int(yy), month, int(dd), int(hh), int(mm),
                         tzinfo=_ET)
    except ValueError:                  # impossible date, e.g. 26FEB31
        return None
    return local.astimezone(timezone.utc)


# ── Fuzzy matching ─────────────────────────────────────────────────────────────

def _fuzzy_score(a: str, b: str) -> float:
    """
    Token-sort ratio (0–100), mirroring rapidfuzz.fuzz.token_sort_ratio.

    Both strings are lower-cased, split into tokens, sorted alphabetically,
    re-joined, then compared with difflib.SequenceMatcher.

    Returns a float in [0.0, 100.0].
    """
    def _norm(s: str) -> str:
        return " ".join(sorted(s.lower().split()))

    ratio = difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()
    return round(ratio * 100, 2)


def _token_set_ratio(a: str, b: str) -> float:
    """
    Token-set ratio (0–100).

    Handles the case where one string's meaningful tokens are a subset of
    the other — e.g. "Connecticut Arizona" vs "Connecticut Huskies Arizona
    Wildcats" scores 100 even though the Odds API adds team nickname suffixes.

    Algorithm:
      1. Compute token intersection and the two remainders.
      2. Build three comparison strings and take the max SequenceMatcher ratio.
    """
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0

    inter  = tokens_a & tokens_b
    a_only = tokens_a - inter
    b_only = tokens_b - inter

    s_int = " ".join(sorted(inter))
    s_a   = " ".join(sorted(a_only))
    s_b   = " ".join(sorted(b_only))

    ca = (s_int + " " + s_a).strip()
    cb = (s_int + " " + s_b).strip()

    r1 = difflib.SequenceMatcher(None, s_int, ca).ratio()
    r2 = difflib.SequenceMatcher(None, s_int, cb).ratio()
    r3 = difflib.SequenceMatcher(None, ca,    cb).ratio()

    return round(max(r1, r2, r3) * 100, 2)


def _match_score(kalshi_title: str, odds_label: str) -> float:
    """
    Best-effort match score between a Kalshi market title and an Odds API label.

    Combines title normalisation (strips "winner?", "vs", "at", etc.) with
    token-set ratio so college team nicknames ("Huskies", "Wildcats") don't
    penalise a correct match.

    Use this in the Matcher class; keep _fuzzy_score for backward-compatible
    unit tests.
    """
    return _token_set_ratio(_normalize_title(kalshi_title), odds_label)


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MatchResult:
    """Returned by Matcher.match() for a successfully matched market."""
    kalshi_ticker: str
    kalshi_title: str
    odds_event: OddsEvent
    fuzzy_score: float          # 0–100
    time_delta_minutes: float   # |start-time delta| in minutes


@dataclass(frozen=True)
class UnmatchedEntry:
    """Internal record written to the unmatched JSONL log."""
    timestamp_utc: str
    market_ticker: str
    market_title: str
    event_start_utc: Optional[str]
    reason: str
    best_candidate: Optional[str] = None
    best_score: Optional[float] = None


# ── Matcher ───────────────────────────────────────────────────────────────────

class Matcher:
    """
    Matches Kalshi moneyline markets to The Odds API OddsEvents.

    Parameters
    ----------
    fuzzy_threshold : float
        Minimum token-sort-ratio score (0–100) to accept a name match
        between the full Kalshi title and the combined Odds API event label.
        Default: 60.0  (from config.yaml matcher.fuzzy_threshold).
    min_team_score : float
        After the best Odds API event is selected, each of its two teams
        must individually score at least this value against the Kalshi title.
        This prevents one-team partial matches (e.g. "San Antonio vs Detroit"
        matching "San Antonio vs Sacramento" because SAS is shared).
        Default: 50.0  (from config.yaml matcher.min_team_score).
    time_window_minutes : float
        Maximum |start-time delta| in minutes for a valid match.
        Default: 43200  (30 days — Kalshi lists markets ~2 weeks early).
    configured_sports : list of str, optional
        Sport keys the agent is allowed to trade.  Pass an empty list or
        None to skip the sport-configured check entirely.
    unmatched_log_path : Path, optional
        Where to append JSONL records for unmatched markets.
    _now_fn : callable, optional
        Returns current UTC datetime — injectable for deterministic tests.
    """

    def __init__(
        self,
        fuzzy_threshold: float = 60.0,
        min_team_score: float = 50.0,
        time_window_minutes: float = 43200.0,
        configured_sports: Optional[List[str]] = None,
        unmatched_log_path: Optional[Path] = None,
        blocked_ticker_prefixes: Optional[tuple] = None,
        _now_fn: Optional[Callable[[], datetime]] = None,
        ticker_time_window_minutes: float = 720.0,
        score_tie_epsilon: float = 1.0,
    ) -> None:
        self._threshold      = float(fuzzy_threshold)
        self._min_team_score = float(min_team_score)
        self._window_minutes = float(time_window_minutes)
        self._ticker_window_minutes = float(ticker_time_window_minutes)
        self._tie_epsilon = float(score_tie_epsilon)
        self._sports: frozenset = frozenset(configured_sports or [])
        self._log_path = (
            unmatched_log_path
            or Path("data/trade_logs/unmatched_markets.jsonl")
        )
        self._blocked_prefixes = blocked_ticker_prefixes or _BLOCKED_TICKER_PREFIXES
        self._now = _now_fn or (lambda: datetime.now(tz=timezone.utc))

    @classmethod
    def from_config(cls, config: dict, **kwargs) -> "Matcher":
        """Construct from a loaded config.yaml dict."""
        m_cfg = config.get("matcher", {})
        sports = config.get("odds_api", {}).get("sports", [])
        paths_cfg = config.get("paths", {})
        log_path = Path(
            paths_cfg.get(
                "unmatched_log",
                "data/trade_logs/unmatched_markets.jsonl",
            )
        )
        # Allow config to extend (not replace) the default blocked prefixes.
        # matcher.extra_blocked_prefixes: ["KXCUSTOM", "KXOTHER"]
        extra = m_cfg.get("extra_blocked_prefixes", [])
        blocked = _BLOCKED_TICKER_PREFIXES + tuple(extra) if extra else None
        return cls(
            fuzzy_threshold=m_cfg.get("fuzzy_threshold", 60.0),
            min_team_score=m_cfg.get("min_team_score", 50.0),
            time_window_minutes=m_cfg.get("time_window_minutes", 43200.0),
            ticker_time_window_minutes=m_cfg.get("ticker_time_window_minutes", 720.0),
            score_tie_epsilon=m_cfg.get("score_tie_epsilon", 1.0),
            configured_sports=sports,
            unmatched_log_path=log_path,
            blocked_ticker_prefixes=blocked,
            **kwargs,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def match(
        self,
        kalshi_market: Dict[str, Any],
        odds_events: List[OddsEvent],
        sport: Optional[str] = None,
    ) -> Optional[MatchResult]:
        """
        Try to match one Kalshi market to an OddsEvent.

        Parameters
        ----------
        kalshi_market : dict
            Raw Kalshi market dict.  Required keys: ``ticker``, ``title``.
            Time extracted from: ``close_time``, ``expiration_time``,
            ``event_start_time``, or ``open_time`` (first found).
        odds_events : list of OddsEvent
            Candidate events (typically for one sport key).
        sport : str, optional
            Sport key used for the configured-sport check.  If None the
            check is skipped.

        Returns
        -------
        MatchResult on success, None if rejected (rejection is also logged).
        """
        ticker      = kalshi_market.get("ticker", "")
        title       = kalshi_market.get("title", "")
        # Prefer the ticker-encoded start when the ticker carries one: it is
        # the true event start (median 1 min from the Odds API commence_time
        # over 650 MLB games), whereas close_time is the market's closing
        # stamp. `ticker_time` being non-None also means the tighter
        # ticker_time_window_minutes applies — a loose window is only needed
        # for the untrustworthy close_time/open_time fallbacks.
        ticker_time = _ticker_start_time(ticker)
        kalshi_time = ticker_time or self._parse_kalshi_time(kalshi_market)

        # ── 0. Ticker prefix blocklist (fast reject) ─────────────────────────
        ticker_upper = ticker.upper()
        if any(ticker_upper.startswith(p) for p in self._blocked_prefixes):
            log.debug("matcher_prefix_blocked", ticker=ticker)
            return None

        # ── 0b. Catch-all for first-half markets (any sport) ────────────────
        if "1HWINNER" in ticker_upper or "1HWIN" in ticker_upper:
            log.debug("matcher_first_half_blocked", ticker=ticker)
            return None

        # ── 1. Market type ────────────────────────────────────────────────────
        if not self._is_moneyline(kalshi_market):
            self._reject(ticker, title, kalshi_time, REASON_MARKET_TYPE_UNSUPPORTED)
            return None

        # ── 2. Sport configured ───────────────────────────────────────────────
        if sport is not None and self._sports and sport not in self._sports:
            self._reject(ticker, title, kalshi_time, REASON_SPORT_NOT_CONFIGURED,
                         extra={"sport": sport})
            return None

        # ── 3. Odds data available ────────────────────────────────────────────
        if not odds_events:
            self._reject(ticker, title, kalshi_time, REASON_ODDS_API_NO_DATA)
            return None

        # ── 3b. NCAAB abbreviation expansion ─────────────────────────────────
        # Kalshi uses short school codes (UK, ISU, KU, SJU, MIA, PUR …) in
        # NCAAB market titles.  These score far below min_team_score=50 when
        # compared against Odds API full names ("Kentucky Wildcats", etc.).
        # Expand the title to full names before all fuzzy comparisons so that
        # the short codes match correctly.  NBA / NFL titles are unaffected
        # because none of their city/team names appear in _NCAAB_ABBREV_MAP.
        working_title = title
        if sport in _NCAAB_SPORTS:
            expanded = _expand_ncaab_abbrevs(title)
            if expanded != title:
                log.debug(
                    "matcher_ncaab_title_expanded",
                    ticker=ticker,
                    original=title,
                    expanded=expanded,
                )
            working_title = expanded

        # ── 4. Fuzzy name match ───────────────────────────────────────────────
        #
        # Ties are broken by START-TIME PROXIMITY, not by list order.
        #
        # This used to be a plain `if score > best_score`, which keeps the
        # FIRST maximal event. Every game of an MLB series carries the same
        # two team names, so all of them score identically and the winner was
        # whichever the Odds API happened to return first. Measured over the
        # whole P-001 corpus on 2026-07-26: 528 of 671 settled MLB bets
        # (79%) had been placed on a Kalshi market for a *different day's*
        # game than the Odds API event whose price produced the edge — the
        # pod priced Friday's game and traded Saturday's market. Their CLV
        # rows were consequently ~0 (+0.19c/ct) while the correctly-matched
        # rows ran +7.65c/ct.
        scored = []
        for event in odds_events:
            label = f"{event.home_team} {event.away_team}"
            scored.append((_match_score(working_title, label), event, label))

        best_score = max(s for s, _, _ in scored)
        near_best = [t for t in scored if t[0] >= best_score - self._tie_epsilon]
        if kalshi_time is not None and len(near_best) > 1:
            _, best_event, best_label = min(
                near_best,
                key=lambda t: abs((t[1].commence_time - kalshi_time).total_seconds()),
            )
        else:
            _, best_event, best_label = near_best[0]

        if best_score < self._threshold:
            self._reject(
                ticker, title, kalshi_time, REASON_NO_FUZZY_MATCH,
                best_candidate=best_label or None,
                best_score=best_score if best_score >= 0 else None,
            )
            return None

        assert best_event is not None  # guaranteed by non-empty odds_events

        # ── 4b. Both-teams presence check ─────────────────────────────────────
        # The fuzzy score above compares the *full* Kalshi title against the
        # *combined* Odds API label.  A single shared team (e.g. San Antonio)
        # can inflate the score enough to pass the threshold even when the
        # opponent differs (e.g. Detroit vs Sacramento).  Verify that each
        # team individually has a reasonable match in the Kalshi title.
        # Use working_title (abbreviations already expanded for NCAAB) so that
        # "UK" → "Kentucky Wildcats" passes the per-team threshold check.
        #
        # Exception: "Will X win?" titles name only the YES-side team by
        # design.  Requiring both teams would always reject these markets, so
        # we skip the check when the single-outcome pattern is detected.  The
        # overall fuzzy score (step 4) is sufficient to anchor the right event.
        is_single_team_title = bool(_SINGLE_TEAM_TITLE_RE.search(working_title))
        if not is_single_team_title and not self._both_teams_present(working_title, best_event.home_team, best_event.away_team):
            self._reject(
                ticker, title, kalshi_time, REASON_TEAM_MISMATCH,
                best_candidate=best_label,
                best_score=best_score,
                extra={
                    "home_team": best_event.home_team,
                    "away_team": best_event.away_team,
                    "home_score": round(
                        _token_set_ratio(best_event.home_team, _normalize_title(working_title)), 1
                    ),
                    "away_score": round(
                        _token_set_ratio(best_event.away_team, _normalize_title(working_title)), 1
                    ),
                    "min_team_score": self._min_team_score,
                },
            )
            return None

        # ── 5. Time window ────────────────────────────────────────────────────
        if kalshi_time is not None:
            delta_minutes = abs(
                (best_event.commence_time - kalshi_time).total_seconds() / 60.0
            )
            window = (self._ticker_window_minutes if ticker_time is not None
                      else self._window_minutes)
            if delta_minutes > window:
                self._reject(
                    ticker, title, kalshi_time, REASON_TIME_WINDOW_EXCEEDED,
                    best_candidate=best_label,
                    best_score=best_score,
                    extra={"delta_minutes": round(delta_minutes, 1)},
                )
                return None
            time_delta_minutes = delta_minutes
        else:
            time_delta_minutes = 0.0   # no Kalshi time → skip check

        log.info(
            "matcher_match",
            ticker=ticker,
            event_id=best_event.event_id,
            score=best_score,
            time_delta_min=round(time_delta_minutes, 2),
        )
        return MatchResult(
            kalshi_ticker=ticker,
            kalshi_title=title,
            odds_event=best_event,
            fuzzy_score=best_score,
            time_delta_minutes=time_delta_minutes,
        )

    def match_all(
        self,
        kalshi_markets: List[Dict[str, Any]],
        odds_events: List[OddsEvent],
        sport: Optional[str] = None,
    ) -> List[MatchResult]:
        """
        Match a list of Kalshi markets, returning only successful results.
        """
        results = []
        for market in kalshi_markets:
            result = self.match(market, odds_events, sport=sport)
            if result is not None:
                results.append(result)
        return results

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _is_moneyline(market: Dict[str, Any]) -> bool:
        """
        Return True when the Kalshi market looks like a moneyline (h2h) market.

        Rejects markets whose title or subtitle contains non-moneyline
        keywords (spread, total, over/under, prop, futures, handicap).
        Defaults to True for unlabelled markets (Phase 5 conservative stance).
        """
        combined = (
            (market.get("subtitle") or "") + " " + (market.get("title") or "")
        ).lower()
        return not any(kw in combined for kw in _NON_MONEYLINE_KEYWORDS)

    @staticmethod
    def _parse_kalshi_time(market: Dict[str, Any]) -> Optional[datetime]:
        """
        Extract start time from a raw Kalshi market dict.

        Tries the keys: close_time → expiration_time → event_start_time →
        open_time.  Returns a timezone-aware UTC datetime or None.
        """
        for key in ("close_time", "expiration_time", "event_start_time", "open_time"):
            raw = market.get(key)
            if raw:
                try:
                    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except (ValueError, AttributeError):
                    continue
        return None

    def _both_teams_present(
        self,
        kalshi_title: str,
        home_team: str,
        away_team: str,
    ) -> bool:
        """
        Return True only when **both** Odds API teams score ≥ min_team_score
        against the normalized Kalshi market title.

        This is a post-selection guard that fires after the best-scoring Odds
        API event has been chosen.  It prevents one-team partial matches —
        e.g. "San Antonio vs Detroit" matching "San Antonio vs Sacramento"
        because both share the "San Antonio Spurs" tokens.

        Sacramento Kings would score near 0 against a Kalshi title that
        contains "San Antonio Spurs Detroit Pistons", so the match is rejected
        before any edge calculation is attempted.
        """
        normalized = _normalize_title(kalshi_title)
        home_score = _token_set_ratio(home_team, normalized)
        away_score = _token_set_ratio(away_team, normalized)
        return (
            home_score >= self._min_team_score
            and away_score >= self._min_team_score
        )

    def _reject(
        self,
        ticker: str,
        title: str,
        kalshi_time: Optional[datetime],
        reason: str,
        best_candidate: Optional[str] = None,
        best_score: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log and write one unmatched entry."""
        log.info("matcher_skip", ticker=ticker, reason=reason, **(extra or {}))
        self._write_unmatched(UnmatchedEntry(
            timestamp_utc=self._now().isoformat(),
            market_ticker=ticker,
            market_title=title,
            event_start_utc=kalshi_time.isoformat() if kalshi_time else None,
            reason=reason,
            best_candidate=best_candidate,
            best_score=best_score,
        ))

    # Max unmatched log size before rotation (50 MB)
    _UNMATCHED_LOG_MAX_BYTES: int = 50 * 1024 * 1024
    _UNMATCHED_LOG_KEEP_LINES: int = 10_000

    def _write_unmatched(self, entry: UnmatchedEntry) -> None:
        """Append one JSONL record to the unmatched markets log."""
        record: Dict[str, Any] = {
            "timestamp_utc":   entry.timestamp_utc,
            "market_ticker":   entry.market_ticker,
            "market_title":    entry.market_title,
            "event_start_utc": entry.event_start_utc,
            "reason":          entry.reason,
        }
        if entry.best_candidate is not None:
            record["best_candidate"] = entry.best_candidate
        if entry.best_score is not None:
            record["best_score"] = entry.best_score

        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

            # Rotate if file exceeds size cap
            if (
                self._log_path.exists()
                and self._log_path.stat().st_size > self._UNMATCHED_LOG_MAX_BYTES
            ):
                self._rotate_unmatched_log()

            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.error(
                "unmatched_log_write_error",
                path=str(self._log_path),
                error=str(exc),
            )

    def _rotate_unmatched_log(self) -> None:
        """Truncate unmatched log to the most recent N lines."""
        try:
            lines = self._log_path.read_text(encoding="utf-8").splitlines()
            keep = lines[-self._UNMATCHED_LOG_KEEP_LINES:]
            self._log_path.write_text(
                "\n".join(keep) + "\n", encoding="utf-8",
            )
            log.info(
                "unmatched_log_rotated",
                path=str(self._log_path),
                old_lines=len(lines),
                kept_lines=len(keep),
            )
        except OSError as exc:
            log.warning("unmatched_log_rotate_error", error=str(exc))
