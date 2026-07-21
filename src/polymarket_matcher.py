"""
src/polymarket_matcher.py
─────────────────────────
Matches Odds API events to Polymarket markets.

Mirrors the Matcher interface: match_all(markets, events, sport) → [result]

Key differences from Kalshi matcher:
  - Polymarket uses full questions ("Will the Kansas City Chiefs beat the...?")
    vs Kalshi titles ("Kansas City Chiefs vs Las Vegas Raiders")
  - Polymarket has condition_id + two token_ids (YES/NO)
  - Lower fuzzy threshold (55 vs 85) for nickname-only vs city+team matching
  - Wider time window (60 min vs 30) since Polymarket timing less precise
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.fuzzy_utils import both_teams_present, token_sort_ratio
from src.polymarket_client import PolymarketMarket

logger = logging.getLogger(__name__)


# ── Rejection reasons (match existing Matcher pattern) ──────────────

REASON_SPORT_NOT_CONFIGURED = "SPORT_NOT_CONFIGURED"
REASON_ODDS_API_NO_DATA = "ODDS_API_NO_DATA"
REASON_NO_FUZZY_MATCH = "NO_FUZZY_MATCH"
REASON_TEAM_MISMATCH = "TEAM_MISMATCH"
REASON_TIME_WINDOW_EXCEEDED = "TIME_WINDOW_EXCEEDED"


@dataclass(frozen=True)
class PolymarketMatchResult:
    """Result of matching an Odds API event to a Polymarket market."""
    polymarket_condition_id: str
    polymarket_question: str
    polymarket_slug: str
    token_id_yes: str
    token_id_no: str
    odds_event: Any           # OddsEvent
    fuzzy_score: float
    time_delta_minutes: Optional[float]
    yes_price: Optional[float] = None   # from Gamma outcomePrices
    yes_outcome_name: Optional[str] = None  # team/outcome name for outcomePrices[0]


def _fuzzy_score(a: str, b: str) -> float:
    """Token-sort ratio (case-insensitive), 0-100 scale."""
    return token_sort_ratio(a, b)


def _extract_teams_from_question(question: str) -> str:
    """Extract matchup text from a Polymarket question.

    "Will the Kansas City Chiefs beat the Las Vegas Raiders?"
    → "Kansas City Chiefs Las Vegas Raiders"

    "Timberwolves vs. Nuggets"  (event title from /events endpoint)
    → "Timberwolves Nuggets"
    """
    q = question.strip().rstrip("?").strip()
    # Remove common prefixes
    q = re.sub(r"^(Will|will)\s+(the\s+)?", "", q)
    # Remove common suffixes
    q = re.sub(r"\s+(beat|defeat|win against|win over)\s+(the\s+)?", " ", q)
    q = re.sub(r"\s+(win|lose)\s*$", "", q)
    # Strip "vs." / "vs" — Polymarket event titles use "X vs. Y" but Odds API
    # uses "City Team City Team" format, so "vs." is just noise for matching.
    q = re.sub(r"\bvs\.?", " ", q)
    # Collapse whitespace
    q = re.sub(r"\s+", " ", q)
    return q.strip()


def _parse_polymarket_time(market: PolymarketMarket) -> Optional[datetime]:
    """Extract datetime from a PolymarketMarket."""
    time_str = market.game_start_time or market.end_date
    if not time_str:
        return None
    try:
        if time_str.endswith("Z"):
            time_str = time_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(time_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


class PolymarketMatcher:
    """Matches Odds API events to Polymarket markets.

    Interface mirrors Matcher: match_all(markets, events, sport) → [result]
    """

    def __init__(
        self,
        fuzzy_threshold: float = 55.0,
        time_window_minutes: float = 60.0,
        configured_sports: Optional[List[str]] = None,
        unmatched_log_path: Optional[Path] = None,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ):
        self._threshold = fuzzy_threshold
        self._window_minutes = time_window_minutes
        self._sports = frozenset(configured_sports) if configured_sports else frozenset()
        self._log_path = unmatched_log_path
        self._now_fn = _now_fn or (lambda: datetime.now(timezone.utc))

    def match(
        self,
        market: PolymarketMarket,
        odds_events: list,
        sport: Optional[str] = None,
        skip_time_check: bool = False,
    ) -> Optional[PolymarketMatchResult]:
        """Try to match a single Polymarket market to an Odds API event.

        Returns PolymarketMatchResult on success, None on rejection.
        Writes rejection to unmatched log.
        """
        # Sport check
        if sport and self._sports and sport not in self._sports:
            self._write_rejection(market, REASON_SPORT_NOT_CONFIGURED)
            return None

        # Empty events check
        if not odds_events:
            self._write_rejection(market, REASON_ODDS_API_NO_DATA)
            return None

        # Extract matching text from Polymarket question
        match_text = _extract_teams_from_question(market.question)

        # Score all events
        best_event = None
        best_score = 0.0

        for event in odds_events:
            # Build comparison string from Odds API event
            event_text = "{} {}".format(
                getattr(event, "home_team", ""),
                getattr(event, "away_team", ""),
            ).strip()

            score = _fuzzy_score(match_text, event_text)
            if score > best_score:
                best_score = score
                best_event = event

        # Fuzzy threshold check
        if best_score < self._threshold:
            self._write_rejection(
                market, REASON_NO_FUZZY_MATCH,
                best_candidate=str(getattr(best_event, "event_id", None)),
                best_score=best_score,
            )
            return None

        # ── Two-team verification ─────────────────────────────────
        # Even if the fuzzy score is above threshold, confirm that
        # BOTH teams from the Odds API event appear in the Polymarket
        # question.  This prevents single-team false matches like
        #   Event: "Jets vs Blackhawks"  matched to  "Canucks vs Blackhawks"
        home = getattr(best_event, "home_team", "") or ""
        away = getattr(best_event, "away_team", "") or ""
        if home and away and not both_teams_present(home, away, match_text):
            self._write_rejection(
                market, REASON_TEAM_MISMATCH,
                best_candidate=str(getattr(best_event, "event_id", None)),
                best_score=best_score,
            )
            logger.debug(
                "Team mismatch: home=%s away=%s  question=%s  score=%.1f",
                home, away, market.question, best_score,
            )
            return None

        # Time window check
        # Skip when markets come from API-filtered queries (series_id) because
        # Polymarket startDate is the event creation date, not the game date.
        poly_time = _parse_polymarket_time(market)
        event_time = getattr(best_event, "commence_time", None)
        time_delta = 0.0

        if not skip_time_check and poly_time and event_time:
            delta_seconds = abs((poly_time - event_time).total_seconds())
            time_delta = delta_seconds / 60.0
            if time_delta > self._window_minutes:
                self._write_rejection(
                    market, REASON_TIME_WINDOW_EXCEEDED,
                    best_candidate=str(getattr(best_event, "event_id", None)),
                    best_score=best_score,
                )
                return None

        return PolymarketMatchResult(
            polymarket_condition_id=market.condition_id,
            polymarket_question=market.question,
            polymarket_slug=market.slug,
            token_id_yes=market.token_id_yes,
            token_id_no=market.token_id_no,
            odds_event=best_event,
            fuzzy_score=best_score,
            time_delta_minutes=time_delta,
            yes_price=getattr(market, "yes_price", None),
            yes_outcome_name=getattr(market, "yes_outcome_name", None),
        )

    def match_all(
        self,
        polymarket_markets: List[PolymarketMarket],
        odds_events: list,
        sport: Optional[str] = None,
        skip_time_check: bool = False,
    ) -> List[PolymarketMatchResult]:
        """Match all Polymarket markets to Odds API events.
        Returns only successful matches.
        """
        results = []
        for market in polymarket_markets:
            result = self.match(market, odds_events, sport, skip_time_check=skip_time_check)
            if result is not None:
                results.append(result)
        return results

    def _write_rejection(
        self,
        market: PolymarketMarket,
        reason: str,
        best_candidate: Optional[str] = None,
        best_score: Optional[float] = None,
    ) -> None:
        """Write rejection entry to unmatched log (JSONL)."""
        if not self._log_path:
            return

        poly_time = _parse_polymarket_time(market)
        record: Dict[str, Any] = {
            "timestamp_utc": self._now_fn().isoformat(),
            "condition_id": market.condition_id,
            "question": market.question,
            "event_start_utc": poly_time.isoformat() if poly_time else None,
            "reason": reason,
        }
        if best_candidate is not None:
            record["best_candidate"] = best_candidate
        if best_score is not None:
            record["best_score"] = best_score

        try:
            # Rotate if file exceeds 50 MB
            if (
                self._log_path.exists()
                and self._log_path.stat().st_size > 50 * 1024 * 1024
            ):
                self._rotate_log(self._log_path)

            with open(self._log_path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            pass

    @staticmethod
    def _rotate_log(path: Path, keep_lines: int = 10_000) -> None:
        """Truncate log to the most recent *keep_lines* lines."""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            keep = lines[-keep_lines:]
            path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def from_config(cls, config: dict) -> "PolymarketMatcher":
        """Build PolymarketMatcher from YAML config dict."""
        poly_cfg = config.get("polymarket", {})
        matcher_cfg = poly_cfg.get("matcher", {})
        return cls(
            fuzzy_threshold=matcher_cfg.get("fuzzy_threshold", 55.0),
            time_window_minutes=matcher_cfg.get("time_window_minutes", 60.0),
            configured_sports=config.get("odds_api", {}).get("sports"),
            unmatched_log_path=Path(
                config.get("paths", {}).get(
                    "polymarket_unmatched_log",
                    "data/trade_logs/polymarket_unmatched.jsonl",
                )
            ),
        )
