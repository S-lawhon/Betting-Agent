"""
src/fuzzy_utils.py
──────────────────
Lightweight fuzzy string matching using only stdlib.

Implements token-sort ratio (same algorithm as fuzzywuzzy/thefuzz)
using SequenceMatcher from difflib.  No external dependencies.
"""

import re
from difflib import SequenceMatcher


def _normalise(s: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation."""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def token_sort_ratio(a: str, b: str) -> float:
    """Token-sort ratio: sort tokens alphabetically then compare.

    Returns 0-100 float (same scale as fuzzywuzzy).
    """
    a_sorted = " ".join(sorted(_normalise(a).split()))
    b_sorted = " ".join(sorted(_normalise(b).split()))
    if not a_sorted and not b_sorted:
        return 100.0
    if not a_sorted or not b_sorted:
        return 0.0
    return SequenceMatcher(None, a_sorted, b_sorted).ratio() * 100.0


def team_name_in_text(team_name: str, text: str) -> bool:
    """Check whether *team_name* appears inside *text* using flexible matching.

    Handles three common patterns:
      1.  Full name:   "Kansas City Chiefs"  in  "Chiefs Kansas City Las Vegas Raiders"
      2.  Nickname:    "Chiefs"              in  "Kansas City Chiefs beat Raiders"
      3.  Short-form:  "Trail Blazers"       in  "blazers grizzlies"

    Both *team_name* and *text* are normalised (lowered, punctuation stripped).
    The function splits the team_name into tokens and checks:
      - ALL tokens present in text  (handles full "Kansas City Chiefs"), OR
      - The *last* token (the nickname) present in text  (handles "Chiefs" in
        "Will Chiefs beat Raiders?").

    For two-word nicknames like "Trail Blazers", "Blue Jackets", "Maple Leafs",
    etc., the last two tokens are also checked as a unit.

    Returns True if any of these heuristics match.
    """
    name = _normalise(team_name)
    body = _normalise(text)
    if not name or not body:
        return False

    name_tokens = name.split()
    body_tokens = set(body.split())

    # Full-name match: every word in team_name appears in text
    if all(tok in body_tokens for tok in name_tokens):
        return True

    # Nickname match: last token (e.g. "Chiefs", "Lakers", "Ducks")
    nickname = name_tokens[-1]
    if nickname in body_tokens and len(nickname) >= 4:
        return True

    # Two-word nickname match: "Trail Blazers", "Blue Jackets", "Maple Leafs"
    if len(name_tokens) >= 3:
        two_word = " ".join(name_tokens[-2:])
        if two_word in body:
            return True

    return False


def both_teams_present(
    home_team: str,
    away_team: str,
    question_text: str,
) -> bool:
    """Return True only if *both* teams can be found in *question_text*.

    Uses the flexible team_name_in_text() heuristic for each team.
    """
    return (
        team_name_in_text(home_team, question_text)
        and team_name_in_text(away_team, question_text)
    )
