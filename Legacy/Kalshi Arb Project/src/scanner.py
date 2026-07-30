"""
src/scanner.py
──────────────
Orchestrated paper-mode scan loop.

Ties together KalshiClient → OddsClient → Matcher → EdgeCalculator →
RiskManager and writes results to trade_log.jsonl.

Architecture
────────────
  Scanner        — call .scan_once() once per polling interval.
                   Returns list of entry dicts written to the trade log.
  _mid_price()   — extracts YES mid-price from Kalshi market listing data.
  _fingerprint() — SHA-256 idempotency key (ticker + side + UTC-hour).

Paper mode
──────────
In paper mode no real orders are placed.  A PLACED entry in the trade log
means "would have traded": the paper fill price is the Kalshi mid-price and
order_id is None.

Phase 7: YES-side inference
────────────────────────────
  _infer_yes_side(market, home_team, away_team) determines which team or
  player the YES side resolves to:
    1. Returns None for TIE-suffix tickers (no h2h equivalent → skipped).
    2. Parses "Will [X] win?" titles and fuzzy-matches X to home/away.
    3. Falls back to the ticker's last hyphen segment (e.g. -FIL for Fils).
    4. Returns None (skip) when no signal is available — avoids phantom edges.

  • Kalshi market listing fields ``yes_bid_dollars`` / ``yes_ask_dollars``
    are decimal-dollar strings (e.g. ``"0.57"``).  Mid = avg of bid/ask.
    Legacy integer-cent fields (``yes_bid`` / ``yes_ask``) are also
    supported as a fallback.

  • list_markets(status="open", limit=200) is called once per scan cycle
    to avoid hammering the rate limiter.

Trade log fingerprint
─────────────────────
SHA-256( market_ticker + side + timestamp_utc[:13] )

timestamp_utc[:13] = "2024-01-15T18"  (year-month-day-hour)
→ one entry per ticker/side per hour.  The _seen set is pre-populated
  from PLACED entries in the existing trade log at startup, so a process
  restart within the same clock-hour will not re-place open positions.

Per-cycle event deduplication
──────────────────────────────
Within each scan_once() call, only one Kalshi market variant is traded
per underlying Odds API event.  Kalshi sometimes lists multiple markets
for the same matchup (e.g. "Will Fils win?" and "Will Mensik win?");
trading both would create contradictory or double-sized positions.
The first variant that clears edge/risk thresholds wins; subsequent
variants are skipped (logged as scan_skip_event_variant).
A variant that is SKIPPED_EDGE does *not* lock the event — the next
variant may offer a better-priced opportunity for the same match.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from collections import Counter

from edge_calculator import EdgeCalculator, EdgeResult
from logger_setup import get_logger
from matcher import MatchResult, Matcher, _NCAAB_ABBREV_MAP, _SPORT_ABBREV_MAPS
from odds_client import ConsensusProbability, OddsClient, OddsEvent
from polymarket_odds import (
    fetch_polymarket_tennis_events,
    build_polymarket_consensus,
)
from risk_manager import RiskManager

log = get_logger(__name__)


def _extract_event_key(ticker: str) -> str:
    """Extract an event-level key shared by ALL market types for one game.

    Strips the market-type prefix and any trailing outcome / set tokens so
    that match-winner, set-winner-1, set-winner-2, half-time-winner, etc.
    all collapse to a single key.

    Examples
    --------
    KXATPMATCH-26MAR24ATMTIA-TIA          → 26MAR24ATMTIA
    KXATPSETWINNER-26MAR24ATMTIA-1-TIA    → 26MAR24ATMTIA
    KXATPSETWINNER-26MAR24ATMTIA-2-ATM    → 26MAR24ATMTIA
    KXNBAGAME-26MAR23LALDET-LAL           → 26MAR23LALDET
    KXNBA1HWINNER-26MAR23LALDET-LAL       → 26MAR23LALDET
    KXNBA2HWINNER-26MAR23LALDET-LAL       → 26MAR23LALDET
    KXNCAAMBGAME-26MAR26TEXPUR-PUR        → 26MAR26TEXPUR
    """
    parts = ticker.split("-", 1)
    if len(parts) < 2:
        return ticker
    # The date+teams identifier is the first segment after the prefix
    return parts[1].split("-")[0]

# ── Sport-aware ticker prefix mapping ─────────────────────────────────────────
# Maps Odds API sport keys to Kalshi ticker prefixes.  When scanning a sport,
# only Kalshi markets whose ticker starts with one of these prefixes are
# passed to the matcher.  This prevents cross-sport false matches (e.g. NHL
# tickers matching against NBA events via shared city names) and eliminates
# thousands of futile fuzzy-match comparisons per scan cycle.
_SPORT_TICKER_PREFIXES: Dict[str, tuple] = {
    # ── Team sports ──────────────────────────────────────────────────────
    "americanfootball_nfl":  ("KXNFLGAME", "KXNFL1HWINNER", "KXNFL2HWINNER"),
    "basketball_nba":        ("KXNBAGAME", "KXNBA1HWINNER", "KXNBA2HWINNER"),
    "basketball_ncaab":      ("KXNCAABBGAME", "KXNCAAMBGAME", "KXNCAAMB1HWINNER"),
    # NCAAW — game-winner prefixes speculative (KXNCAAWBGAME, KXNCAAWGAME);
    # KXNCAAWBTOTAL confirmed live on Kalshi as of Mar 2026.
    "basketball_ncaaw":      ("KXNCAAWBGAME", "KXNCAAWGAME", "KXNCAAWBTOTAL"),
    "baseball_mlb":          ("KXMLBGAME",),
    # NHL — no game-winner/moneyline tickers on production as of Mar 2026;
    # only player props (KXNHLGOAL, KXNHLAST, etc.).  Keep prefix for when
    # Kalshi adds them.
    "icehockey_nhl":         ("KXNHLGAME",),
    # ── Tennis — match-winner tickers ────────────────────────────────
    # Keys are tournament-specific (e.g. tennis_atp_miami_open) and change
    # as the tour moves between events. Use prefix-based fallback below
    # so any tennis_atp_* or tennis_wta_* key works automatically.
    # Challenger prefixes included — Polymarket supplement provides odds
    # data for challengers/qualifying that The Odds API doesn't cover.
    "tennis_atp_miami_open":    ("KXATPMATCH", "KXATPSETWINNER", "KXATPCHALLENGERMATCH"),
    "tennis_wta_miami_open":    ("KXWTAMATCH", "KXWTACHALLENGERMATCH"),
    "tennis_atp_indian_wells":  ("KXATPMATCH", "KXATPSETWINNER", "KXATPCHALLENGERMATCH"),
    "tennis_wta_indian_wells":  ("KXWTAMATCH", "KXWTACHALLENGERMATCH"),
    # Generic fallback entries — any tennis_atp_* or tennis_wta_* key that
    # doesn't have its own entry will match here via the prefix-stripping
    # fallback logic in _scan_sport().
    "tennis_atp":               ("KXATPMATCH", "KXATPSETWINNER", "KXATPCHALLENGERMATCH"),
    "tennis_wta":               ("KXWTAMATCH", "KXWTACHALLENGERMATCH"),
}


# ── Series tickers for targeted fetching ─────────────────────────────────────
# Kalshi can have 10,000+ open markets.  The general list_markets pagination
# (25 pages × 200 = 5,000) often caps out before reaching sports game-winner
# markets.  After general pagination, we supplement with targeted series_ticker
# fetches for each series listed here to ensure game-winner markets are always
# included in the scan, regardless of how many non-sports markets exist.
_GAME_WINNER_SERIES: List[str] = [
    "KXMLBGAME",
    "KXNBAGAME",
    "KXNBA1HWINNER",
    "KXNBA2HWINNER",
    "KXNHLGAME",
    "KXNFLGAME",
    "KXNFL1HWINNER",
    "KXNFL2HWINNER",
    "KXNCAABBGAME",
    "KXNCAAMBGAME",
    "KXNCAAMB1HWINNER",
    "KXATPMATCH",
    "KXATPSETWINNER",
    "KXWTAMATCH",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mid_price(market: Dict[str, Any]) -> Optional[float]:
    """
    Extract YES mid-price from a Kalshi market dict.

    Kalshi's API now returns prices in two formats:

    * **Current (USD-dollar strings):** ``yes_bid_dollars`` /
      ``yes_ask_dollars`` — decimal-dollar strings like ``"0.5700"``.
    * **Legacy (integer cents):** ``yes_bid`` / ``yes_ask`` — integer
      cents (0–100), e.g. ``57``.

    This function tries the current format first, then falls back to
    the legacy format for backwards compatibility.

    Returns the YES mid-price as a probability in (0, 1), or None if
    neither format provides valid data.
    """
    # ── Current API format: yes_bid_dollars / yes_ask_dollars ────────
    bid_d = market.get("yes_bid_dollars")
    ask_d = market.get("yes_ask_dollars")
    if bid_d is not None and ask_d is not None:
        try:
            mid = (float(bid_d) + float(ask_d)) / 2.0
        except (TypeError, ValueError):
            pass
        else:
            if 0.0 < mid < 1.0:
                return mid

    # ── Legacy API format: yes_bid / yes_ask (integer cents) ────────
    yes_bid = market.get("yes_bid")
    yes_ask = market.get("yes_ask")
    if yes_bid is None or yes_ask is None:
        return None
    try:
        mid = (float(yes_bid) + float(yes_ask)) / 200.0
    except (TypeError, ValueError):
        return None
    if not (0.0 < mid < 1.0):
        return None
    return mid


def _make_fingerprint(ticker: str, side: str, now: datetime) -> str:
    """
    SHA-256 idempotency key: ticker + side + UTC-hour prefix.

    timestamp_utc[:13] = "2024-01-15T18" → deduplicates per clock-hour.
    """
    ts_hour = now.isoformat()[:13]
    raw = f"{ticker}{side}{ts_hour}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Phase 7: YES-side inference ────────────────────────────────────────────────

_WILL_WIN_RE = re.compile(r"\bWill\s+(.+?)\s+win\b", re.IGNORECASE)

# Market-type ticker suffixes that look like team abbreviations to fuzzy
# matching (e.g. "ML" matches "Minnesota" at 16%).  Exclude them so the
# suffix path only fires on genuine team codes.
_MARKET_TYPE_SUFFIXES = frozenset({
    "ML", "OU", "SP", "TIE", "TOT", "YES", "NO", "WIN", "LOSE",
    "DRAW", "OVER", "UNDER", "AH",
})


def _name_score(a: str, b: str) -> float:
    """Token-sort fuzzy score (0–100) between two name strings."""
    def _norm(s: str) -> str:
        return " ".join(sorted(s.lower().split()))
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio() * 100


def _infer_yes_side(
    market: Dict[str, Any],
    home_team: str,
    away_team: str,
    sport: str = "",
) -> Optional[str]:
    """
    Infer which team/player YES resolves to for a Kalshi h2h market.

    Returns ``"home"``, ``"away"``, or ``None`` (TIE markets have no h2h
    Odds API equivalent and should be skipped entirely).

    Algorithm
    ---------
    1. Skip TIE-suffix tickers  (e.g. ``KXNCAAHOCKEYGAME-...-TIE``).
    2. ``"Will [X] win?"`` title pattern → extract X, fuzzy-match vs home/away.
    3. Ticker suffix fallback — last hyphen segment (e.g. ``-FIL`` for Fils).
       Uses sport-specific abbreviation maps (MLB, NHL, NCAAB) to expand
       short codes like "TB" → "Tampa Bay Rays" before scoring.
    4. No confident signal → ``None`` (skip the market to avoid phantom edges).
    """
    title  = market.get("title",  "") or ""
    ticker = market.get("ticker", "") or ""

    # ── Pick the right abbreviation map for this sport ────────────────────
    sport_abbrev_map = _SPORT_ABBREV_MAPS.get(sport, {})

    # ── 1. TIE suffix → skip ─────────────────────────────────────────────
    suffix = ticker.rsplit("-", 1)[-1].upper() if "-" in ticker else ""
    if suffix == "TIE":
        return None

    # ── 2. "Will [X] win?" title pattern ─────────────────────────────────
    m = _WILL_WIN_RE.search(title)
    if m:
        yes_name = m.group(1).strip()
        # Expand sport-specific abbreviations, then fall back to NCAAB.
        yes_name_expanded = (
            sport_abbrev_map.get(yes_name.upper())
            or _NCAAB_ABBREV_MAP.get(yes_name.upper())
            or yes_name
        )
        home_score = _name_score(yes_name_expanded, home_team)
        away_score = _name_score(yes_name_expanded, away_team)
        return "home" if home_score >= away_score else "away"

    # ── 3. Ticker suffix fallback ─────────────────────────────────────────
    # Only act on the suffix when it meaningfully matches at least one team
    # (score ≥ 15.0).  Exclude known market-type codes (ML, OU, SP, etc.)
    # that can false-positive against team names (e.g. "ML" vs "Minnesota").
    if suffix and suffix not in _MARKET_TYPE_SUFFIXES:
        # Expand sport-specific abbreviations (MLB: "TB" → "Tampa Bay Rays",
        # NHL: "FLA" → "Florida Panthers"), then fall back to NCAAB.
        suffix_expanded = (
            sport_abbrev_map.get(suffix.upper())
            or _NCAAB_ABBREV_MAP.get(suffix.upper())
            or suffix
        )
        home_score = _name_score(suffix_expanded, home_team)
        away_score = _name_score(suffix_expanded, away_team)
        if home_score >= 15.0 or away_score >= 15.0:
            return "home" if home_score >= away_score else "away"

    # ── 4. No confident signal → skip the market ─────────────────────────
    # Previously defaulted to "home", which caused false edges when the
    # market was actually for the away team (e.g. Kings YES contract priced
    # at 37% got compared against Timberwolves' 62% consensus).
    log.warning(
        "infer_yes_side_unknown",
        ticker=ticker,
        title=title,
        home_team=home_team,
        away_team=away_team,
    )
    return None


# ── Scanner ───────────────────────────────────────────────────────────────────

class Scanner:
    """
    Orchestrated paper-mode scan loop.

    Parameters
    ----------
    kalshi_client :
        KalshiClient (or any object exposing ``list_markets``).
    odds_client :
        OddsClient (or any object exposing ``get_events`` / ``get_consensus``).
    matcher :
        Matcher instance.
    edge_calculator :
        EdgeCalculator instance.
    risk_manager :
        RiskManager instance.
    sports : list of str
        Configured sport keys to scan.
    trade_log_path : Path
        Where to append JSONL trade log entries.
    mode : "paper" | "live"
        Execution mode stamped on every log entry.  In paper mode no real
        order is placed regardless of action code.
    _now_fn : callable, optional
        Returns current UTC datetime — injectable for tests.
    """

    def __init__(
        self,
        kalshi_client: Any,
        odds_client: Any,
        matcher: Matcher,
        edge_calculator: EdgeCalculator,
        risk_manager: RiskManager,
        sports: List[str],
        trade_log_path: Path,
        mode: str = "paper",
        max_kalshi_pages: int = 10,
        min_market_volume: int = 100,
        fatigue_analyzer: Optional[Any] = None,
        _now_fn: Optional[Callable[[], datetime]] = None,
        clv_gate: Optional[dict] = None,
        sport_edge_overrides: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> None:
        self._kalshi           = kalshi_client
        self._odds             = odds_client
        self._matcher          = matcher
        self._edge             = edge_calculator
        self._risk             = risk_manager
        self._sports           = list(sports)
        self._log_path         = trade_log_path
        self._mode             = mode
        self._max_kalshi_pages = max_kalshi_pages
        self._min_volume       = min_market_volume
        self._fatigue          = fatigue_analyzer
        self._clv_gate         = clv_gate or {}
        self._sport_edge_overrides = sport_edge_overrides or {}
        self._sport_edge_cache: Dict[str, EdgeCalculator] = {}
        self._now              = _now_fn or (lambda: datetime.now(tz=timezone.utc))
        self._seen: Set[str] = set()   # session-level fingerprint dedup
        self._last_kalshi_markets: List[Dict[str, Any]] = []  # exposed for consistency checker
        self._load_seen_from_log()     # warm up from existing log on restart

    @classmethod
    def from_config(
        cls,
        config: dict,
        kalshi_client: Any,
        odds_client: Any,
        matcher: Matcher,
        edge_calculator: EdgeCalculator,
        risk_manager: RiskManager,
        fatigue_analyzer: Optional[Any] = None,
        **kwargs,
    ) -> "Scanner":
        """Construct from a loaded config.yaml dict + pre-built components."""
        sports    = config.get("odds_api", {}).get("sports", [])
        log_path  = Path(
            config.get("paths", {}).get(
                "trade_log", "data/trade_logs/trade_log.jsonl"
            )
        )
        mode = config.get("environment", "demo")
        # Treat "demo" the same as "paper" for the log mode field
        if mode not in ("paper", "live"):
            mode = "paper"
        max_kalshi_pages  = config.get("kalshi",     {}).get("max_pages", 25)
        min_market_volume = config.get("liquidity",  {}).get("min_market_volume", 100)
        return cls(
            kalshi_client=kalshi_client,
            odds_client=odds_client,
            matcher=matcher,
            edge_calculator=edge_calculator,
            risk_manager=risk_manager,
            sports=sports,
            trade_log_path=log_path,
            mode=mode,
            max_kalshi_pages=max_kalshi_pages,
            min_market_volume=min_market_volume,
            fatigue_analyzer=fatigue_analyzer,
            clv_gate=config.get("pods", {}).get("P-001", {}).get("clv_gate"),
            sport_edge_overrides=config.get("edge", {}).get("sport_overrides"),
            **kwargs,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def last_kalshi_markets(self) -> List[Dict[str, Any]]:
        """
        Markets fetched in the most recent scan_once() call.

        Exposed so downstream consumers (e.g. ConsistencyChecker) can
        reuse the same data without making additional Kalshi API calls.
        """
        return self._last_kalshi_markets

    def scan_once(self) -> List[Dict[str, Any]]:
        """
        Run one complete scan cycle across all configured sports.

        Paginates through Kalshi open markets (up to ``max_kalshi_pages``
        pages × 200 markets each) to ensure sport-specific markets are
        not missed when Kalshi returns other categories first.

        Returns
        -------
        List of trade-log entry dicts written during this cycle.
        """
        # ── Fetch Kalshi open markets with pagination ─────────────────────────
        kalshi_markets: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        try:
            for page in range(self._max_kalshi_pages):
                resp = self._kalshi.list_markets(
                    status="open", limit=200, cursor=cursor
                )
                page_markets = resp.get("markets", [])
                kalshi_markets.extend(page_markets)
                cursor = resp.get("cursor") or None
                if not cursor or len(page_markets) < 200:
                    break   # no more pages
            log.debug(
                "scan_kalshi_fetched",
                pages=page + 1,
                total_markets=len(kalshi_markets),
            )
        except Exception as exc:
            log.error("scan_kalshi_error", error=str(exc))
            return []

        # ── Targeted series fetches for game-winner markets ────────────────────
        # General pagination may not reach game-winner markets when Kalshi has
        # 10,000+ open markets.  Supplement with series_ticker-filtered calls.
        seen_tickers: Set[str] = {m.get("ticker", "") for m in kalshi_markets}
        series_added = 0
        for series in _GAME_WINNER_SERIES:
            try:
                series_cursor: Optional[str] = None
                for _ in range(5):  # max 5 pages per series (1000 markets)
                    resp = self._kalshi.list_markets(
                        status="open", limit=200, cursor=series_cursor,
                        series_ticker=series,
                    )
                    page_markets = resp.get("markets", [])
                    for m in page_markets:
                        t = m.get("ticker", "")
                        if t and t not in seen_tickers:
                            kalshi_markets.append(m)
                            seen_tickers.add(t)
                            series_added += 1
                    series_cursor = resp.get("cursor") or None
                    if not series_cursor or len(page_markets) < 200:
                        break
            except Exception as exc:
                log.warning("scan_series_fetch_error", series=series,
                            error=str(exc))
        if series_added:
            log.info(
                "scan_series_supplemented",
                series_added=series_added,
                total_markets=len(kalshi_markets),
            )

        # Expose for downstream consumers (e.g. consistency checker)
        self._last_kalshi_markets = kalshi_markets

        # Build ticker → market dict lookup for quick access during matching
        by_ticker: Dict[str, Dict[str, Any]] = {
            m.get("ticker", ""): m for m in kalshi_markets
        }

        all_entries: List[Dict[str, Any]] = []
        for sport in self._sports:
            entries = self._scan_sport(sport, kalshi_markets, by_ticker)
            all_entries.extend(entries)

        log.info(
            "scan_complete",
            sports=self._sports,
            markets_fetched=len(kalshi_markets),
            series_supplemented=series_added,
            entries_logged=len(all_entries),
        )
        return all_entries

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load_seen_from_log(self) -> None:
        """
        Pre-populate ``_seen`` from PLACED entries already in the trade log.

        Called once at startup so that a Scanner restart does not re-place
        positions that were opened in the same clock-hour by a previous
        process.  Fingerprints from earlier hours are harmless to load: they
        can never be regenerated (the UTC-hour component will differ).
        """
        if not self._log_path.exists():
            return
        loaded = 0
        try:
            for line in self._log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("action") == "PLACED":
                    fp = rec.get("fingerprint")
                    if fp:
                        self._seen.add(fp)
                        loaded += 1
        except OSError as exc:
            log.warning("scan_seen_load_error", path=str(self._log_path),
                        error=str(exc))
            return
        if loaded:
            log.debug("scan_seen_loaded", count=loaded,
                      path=str(self._log_path))

    def _open_game_keys(self) -> set:
        """
        Read the trade log and return event-level keys that currently have at
        least one open position (PLACED but not yet WIN/LOSS/VOID).

        Uses :func:`_extract_event_key` so that *all* market types for the
        same real-world event (match-winner, set-winner-1, set-winner-2,
        half-time-winner, etc.) share a single key.  This prevents the
        scanner from stacking correlated bets across market types.

        A reference-counted approach ensures that voiding one market type
        does not prematurely free the event while another market type is
        still open.

        BANKROLL_RESET entries clear all open keys, matching settler behaviour.
        """
        if not self._log_path.exists():
            return set()
        event_counts: Counter = Counter()
        voided_keys: set = set()
        try:
            for line in self._log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                action  = (rec.get("action")  or "").upper()
                outcome = (rec.get("outcome") or "").upper()
                ticker  = rec.get("market_ticker", "")
                if action == "BANKROLL_RESET":
                    event_counts.clear()
                elif action in ("PLACED", "EXECUTED") and ticker:
                    event_counts[_extract_event_key(ticker)] += 1
                elif outcome in ("WIN", "LOSS") and ticker:
                    event_counts[_extract_event_key(ticker)] -= 1
                elif outcome == "VOID" and ticker:
                    # Track voided events separately so the scanner does not
                    # re-bet on contracts Kalshi has already voided.  This
                    # prevents the place->void->place loop that occurs when
                    # Kalshi voids an entire day/tournament (e.g. rain at
                    # Monte Carlo).  voided_keys is unioned into the return
                    # value so the scanner treats them as permanently blocked.
                    voided_keys.add(_extract_event_key(ticker))
                    event_counts[_extract_event_key(ticker)] -= 1
        except OSError:
            pass
        return {k for k, v in event_counts.items() if v > 0} | voided_keys

    def _scan_sport(
        self,
        sport: str,
        kalshi_markets: List[Dict[str, Any]],
        by_ticker: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Scan one sport: match → evaluate → log."""
        # Fetch Odds API events for this sport
        _polymarket_event_ids: set = set()  # track which events came from Polymarket
        try:
            odds_events = self._odds.get_events(sport)
        except Exception as exc:
            log.error("scan_odds_error", sport=sport, error=str(exc))
            odds_events = []

        # ── Polymarket supplement for tennis ──────────────────────────────
        # The Odds API only covers major tournament main draws.  Kalshi has
        # hundreds of tennis markets across challengers and qualifying that
        # Odds API never covers.  Merge Polymarket events as a supplement
        # for any matches not already covered by Odds API.
        if "tennis" in sport or "atp" in sport or "wta" in sport:
            try:
                poly_events = fetch_polymarket_tennis_events(sport)
                if poly_events:
                    # Avoid duplicates: skip Polymarket events whose players
                    # already appear in Odds API events (by home_team name).
                    existing_teams = {
                        e.home_team.lower() for e in odds_events
                    }
                    new_events = [
                        e for e in poly_events
                        if e.home_team.lower() not in existing_teams
                    ]
                    for e in new_events:
                        _polymarket_event_ids.add(e.event_id)
                    odds_events.extend(new_events)
                    log.info(
                        "scan_polymarket_supplement",
                        sport=sport,
                        odds_api_events=len(odds_events) - len(new_events),
                        polymarket_events=len(new_events),
                        total=len(odds_events),
                    )
            except Exception as exc:
                log.warning(
                    "scan_polymarket_supplement_error",
                    sport=sport,
                    error=str(exc),
                )

        if not odds_events:
            log.info("scan_no_odds_events", sport=sport)
            return []

        # ── Filter Kalshi markets to this sport's ticker prefixes ─────────
        prefixes = _SPORT_TICKER_PREFIXES.get(sport)
        # Fallback: for tournament-specific sport keys like tennis_atp_miami_open,
        # try progressively shorter prefixes (tennis_atp → tennis) to find a match.
        if not prefixes:
            parts = sport.split("_")
            for i in range(len(parts) - 1, 0, -1):
                prefix_key = "_".join(parts[:i])
                prefixes = _SPORT_TICKER_PREFIXES.get(prefix_key)
                if prefixes:
                    break
        if prefixes:
            sport_markets = [
                m for m in kalshi_markets
                if any(m.get("ticker", "").startswith(p) for p in prefixes)
            ]
        else:
            # Sport not in mapping → pass all markets through
            sport_markets = kalshi_markets

        if not sport_markets:
            log.info("scan_no_sport_markets", sport=sport,
                     total_markets=len(kalshi_markets))
            return []

        log.debug("scan_sport_filtered", sport=sport,
                  sport_markets=len(sport_markets),
                  total_markets=len(kalshi_markets))

        # ── Update fatigue schedule cache with observed events ───────────
        if self._fatigue and sport in self._fatigue.sports:
            try:
                self._fatigue.update_cache(odds_events, sport)
            except Exception as exc:
                log.warning("fatigue_cache_update_error", sport=sport,
                            error=str(exc))

        matches: List[MatchResult] = self._matcher.match_all(
            sport_markets, odds_events, sport=sport
        )
        if not matches:
            log.info("scan_no_matches", sport=sport,
                     kalshi_count=len(sport_markets))
            return []

        entries: List[Dict[str, Any]] = []
        placed_event_ids: Set[str] = set()   # one trade per event per cycle
        open_game_keys: set = self._open_game_keys()  # games with open positions

        for match in matches:
            event_id = match.odds_event.event_id
            # Block any bet on a game that already has an open position —
            # regardless of side, market type, or hour boundary.
            game_key = _extract_event_key(match.kalshi_ticker)
            if game_key in open_game_keys:
                log.debug(
                    "scan_skip_open_position",
                    ticker=match.kalshi_ticker,
                    game_key=game_key,
                )
                continue
            if event_id in placed_event_ids:
                # A variant of this match was already handled this cycle.
                # Skip to prevent contradictory or duplicate positions.
                log.debug(
                    "scan_skip_event_variant",
                    ticker=match.kalshi_ticker,
                    event_id=event_id,
                )
                continue

            entry = self._evaluate_match(
                match, by_ticker, sport, odds_events,
                _polymarket_consensus=(match.odds_event.event_id in _polymarket_event_ids),
            )
            if entry:
                entries.append(entry)
                # Lock event for all outcomes except SKIPPED_EDGE.
                # SKIPPED_EDGE means no signal here → the next variant may
                # offer a better-priced market for the same event.
                if entry.get("action") != "SKIPPED_EDGE":
                    placed_event_ids.add(event_id)
                # If a position was actually placed this cycle, lock the game
                # key so no contradictory bet can fire in the same cycle.
                if entry.get("action") == "PLACED":
                    open_game_keys.add(game_key)

        return entries

    def _evaluate_match(
        self,
        match: MatchResult,
        by_ticker: Dict[str, Dict[str, Any]],
        sport: str,
        odds_events: Optional[List[Any]] = None,
        _polymarket_consensus: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluate one matched market.

        Returns a trade-log entry dict if an entry was written, else None.
        """
        now = self._now()
        ticker = match.kalshi_ticker
        event_label = f"{match.odds_event.home_team} vs {match.odds_event.away_team}"

        # Extract game start time from Odds API event
        _commence = getattr(match.odds_event, "commence_time", None)
        game_time_str = (
            _commence.isoformat()
            if hasattr(_commence, "isoformat")
            else str(_commence) if _commence else None
        )

        # ── Game-started guard ─────────────────────────────────────────────────
        # Skip games that have already commenced — pre-game odds are stale once
        # the event is live, and live in-play pricing follows different dynamics.
        if _commence is not None:
            try:
                if hasattr(_commence, "tzinfo") and _commence.tzinfo is not None:
                    commence_aware = _commence
                else:
                    # Assume UTC if naive
                    commence_aware = _commence.replace(tzinfo=timezone.utc)
                if now >= commence_aware:
                    log.info(
                        "scan_skip_game_started",
                        ticker=ticker,
                        match_event=event_label,
                        commence_time=game_time_str,
                        now=now.isoformat(),
                    )
                    return None
            except (TypeError, AttributeError):
                pass  # Non-datetime commence_time; skip guard safely

        # ── Mid-price ─────────────────────────────────────────────────────────
        market_dict = by_ticker.get(ticker, {})
        price = _mid_price(market_dict)
        if price is None:
            if self._mode != "paper":
                log.warning("scan_no_price", ticker=ticker)
                return None
            # Paper mode: defer price resolution until after consensus is
            # available.  We'll use a noisy synthetic price below so the
            # full pipeline (YES-side inference, edge calc, risk) can be
            # exercised even when the demo order book is empty.
            log.debug("scan_no_price_paper_deferred", ticker=ticker)

        # ── Volume / liquidity check ──────────────────────────────────────────
        # Kalshi API returns volume as ``volume_fp`` (dollar-denominated float
        # string).  Markets with negligible volume have meaningless mid-prices
        # — wide bid-ask spreads make the mid unreliable.
        _vol_raw = market_dict.get("volume_fp") or market_dict.get("volume") or 0
        volume_usd = float(_vol_raw)
        if volume_usd < self._min_volume:
            log.debug(
                "scan_skip_low_volume",
                ticker=ticker,
                volume_usd=round(volume_usd, 2),
                min_volume=self._min_volume,
            )
            return None   # skip — insufficient liquidity for reliable pricing

        # ── Consensus probability ─────────────────────────────────────────────
        if _polymarket_consensus:
            # Events sourced from Polymarket — build consensus directly
            # from their prices instead of querying The Odds API.
            cp = build_polymarket_consensus(match.odds_event)
        else:
            cp = self._odds.get_consensus(match.odds_event)
        if cp is None:
            log.warning("scan_no_consensus", ticker=ticker,
                        event_label=event_label)
            return None

        # Phase 7: infer which team/player YES resolves to
        yes_side = _infer_yes_side(market_dict, cp.home_team, cp.away_team, sport=sport)
        if yes_side is None:
            log.debug("scan_skip_tie", ticker=ticker)
            return None
        fair_yes_prob = cp.home_prob if yes_side == "home" else cp.away_prob

        # ── Paper-mode synthetic price (demo order book empty) ───────────────
        if price is None and self._mode == "paper":
            # Simulate a Kalshi mid-price offset from consensus by ±2-5 cents
            # so the edge calculator has something to evaluate.  Use a
            # deterministic offset derived from the ticker hash so repeated
            # scans produce stable results for the same market.
            _hash = hash(ticker) % 100
            offset = ((_hash % 7) + 2) / 100.0          # 0.02 – 0.08
            sign   = 1 if _hash < 50 else -1
            price  = max(0.02, min(0.98, fair_yes_prob + sign * offset))
            log.info(
                "scan_synthetic_price",
                ticker=ticker,
                synthetic_price=round(price, 4),
                fair_yes_prob=round(fair_yes_prob, 4),
                offset=round(sign * offset, 4),
            )

        # ── Sanity check: reject suspiciously large edges ────────────────────
        # An edge > 20% on a major-sport moneyline almost always indicates a
        # YES-side mapping error or stale data rather than real mispricing.
        raw_gap = abs(fair_yes_prob - price)
        if raw_gap > 0.20:
            log.warning(
                "scan_skip_suspicious_edge",
                ticker=ticker,
                match_event=event_label,
                yes_side=yes_side,
                fair_yes_prob=round(fair_yes_prob, 4),
                kalshi_mid=round(price, 4),
                gap=round(raw_gap, 4),
            )
            return None  # Skip — don't trade on likely data mismatch

        # ── Edge evaluation ───────────────────────────────────────────────────
        edge_calc = self._edge_for_sport(sport)
        yes_result, no_result = edge_calc.evaluate_both_sides(
            fair_yes_prob, price
        )
        best = edge_calc.best_side(fair_yes_prob, price)

        # ── Fatigue-adjusted re-evaluation (Strategy 1) ──────────────────────
        fatigue_ctx: Optional[Dict[str, Any]] = None

        if best is None and self._fatigue and sport in self._fatigue.sports:
            # Standard thresholds rejected this edge.  Check if fatigue gives
            # us higher conviction the Kalshi-consensus gap is real.
            try:
                matchup_fatigue = self._fatigue.analyze_matchup(
                    home_team=match.odds_event.home_team,
                    away_team=match.odds_event.away_team,
                    game_time=match.odds_event.commence_time,
                    current_events=odds_events,
                )
                if matchup_fatigue.edge_multiplier > 1.0:
                    # Re-evaluate with lowered thresholds
                    adjusted_edge = self._adjusted_edge_calc(
                        matchup_fatigue.edge_multiplier, base=edge_calc
                    )
                    best = adjusted_edge.best_side(fair_yes_prob, price)
                    if best is not None:
                        fatigue_ctx = {
                            "home_rest_days": matchup_fatigue.home_fatigue.rest_days,
                            "away_rest_days": matchup_fatigue.away_fatigue.rest_days,
                            "rest_differential": matchup_fatigue.rest_differential,
                            "home_b2b": matchup_fatigue.home_fatigue.is_second_of_b2b,
                            "away_b2b": matchup_fatigue.away_fatigue.is_second_of_b2b,
                            "multiplier": matchup_fatigue.edge_multiplier,
                            "fatigue_triggered": True,
                        }
                        log.info(
                            "fatigue_edge_promoted",
                            ticker=ticker,
                            side=best.side,
                            edge=round(best.raw_edge, 4),
                            multiplier=matchup_fatigue.edge_multiplier,
                            home_b2b=matchup_fatigue.home_fatigue.is_second_of_b2b,
                            away_b2b=matchup_fatigue.away_fatigue.is_second_of_b2b,
                        )
            except Exception as exc:
                log.warning("fatigue_eval_error", ticker=ticker, error=str(exc))

        if best is None:
            # Neither side clears min_edge_pct / min_ev thresholds
            # Log with whichever side had the higher EV (even if negative)
            log_result = yes_result if yes_result.ev >= no_result.ev else no_result
            entry = self._make_entry(
                now=now, ticker=ticker, sport=sport, event_label=event_label,
                result=log_result, fair_yes_prob=fair_yes_prob,
                kalshi_prob=price, size_usd=0.0,
                action="SKIPPED_EDGE",
                skip_reason=f"edge={log_result.raw_edge:.3f} ev={log_result.ev:.4f}",
                yes_side=yes_side,
                game_time=game_time_str,
            )
            self._write_log(entry)
            return entry

        # ── Fingerprint dedup ─────────────────────────────────────────────────
        fp = _make_fingerprint(ticker, best.side, now)
        if fp in self._seen:
            entry = self._make_entry(
                now=now, ticker=ticker, sport=sport, event_label=event_label,
                result=best, fair_yes_prob=fair_yes_prob,
                kalshi_prob=price, size_usd=0.0,
                action="SKIPPED_DUPLICATE",
                skip_reason="already evaluated this hour",
                yes_side=yes_side,
                game_time=game_time_str,
            )
            self._write_log(entry)
            return entry

        # ── Risk check ────────────────────────────────────────────────────────
        size_usd = self._risk.calculate_position_size(best.kelly_fractional)
        trade_decision = self._risk.can_trade(size_usd)

        if not trade_decision.allowed:
            entry = self._make_entry(
                now=now, ticker=ticker, sport=sport, event_label=event_label,
                result=best, fair_yes_prob=fair_yes_prob,
                kalshi_prob=price, size_usd=0.0,
                action="SKIPPED_RISK",
                skip_reason=f"{trade_decision.block_code}: {trade_decision.reason}",
                yes_side=yes_side,
                game_time=game_time_str,
            )
            self._write_log(entry)
            return entry

        # ── CLV gate: validated MLB rule (underdog side + positive net edge as maker)
        if self._clv_gate.get("enabled") and not self._clv_passes(
            fair_yes_prob if best.side == "YES" else (1.0 - fair_yes_prob),
            price if best.side == "YES" else (1.0 - price),
        ):
            self._seen.add(fp)
            entry = self._make_entry(
                now=now, ticker=ticker, sport=sport, event_label=event_label,
                result=best, fair_yes_prob=fair_yes_prob,
                kalshi_prob=price, size_usd=0.0,
                action="SKIPPED_CLV_GATE",
                skip_reason="clv_gate: not underdog+net-edge-maker",
                yes_side=yes_side,
                game_time=game_time_str,
            )
            self._write_log(entry)
            return entry

        # ── Paper PLACED ──────────────────────────────────────────────────────
        self._seen.add(fp)
        # Side-adjusted fill: YES fills at the YES mid, NO fills at (1 - YES mid).
        paper_fill = price if best.side == "YES" else (1.0 - price)
        entry = self._make_entry(
            now=now, ticker=ticker, sport=sport, event_label=event_label,
            result=best, fair_yes_prob=fair_yes_prob,
            kalshi_prob=price, size_usd=size_usd,
            action="PLACED",
            fill_price=paper_fill,   # paper fill at side-adjusted mid
            fatigue_context=fatigue_ctx,
            yes_side=yes_side,
            game_time=game_time_str,
        )
        self._write_log(entry)
        log.info(
            "scan_placed",
            ticker=ticker, side=best.side, size_usd=round(size_usd, 2),
            edge=round(best.raw_edge, 4), ev=round(best.ev, 4),
            mode=self._mode,
            fatigue_triggered=fatigue_ctx is not None,
        )
        return entry

    # ── Per-sport edge thresholds ────────────────────────────────────────────

    def _edge_for_sport(self, sport: str) -> EdgeCalculator:
        """
        EdgeCalculator for this sport: the shared one, unless
        ``edge.sport_overrides.<sport>`` supplies its own
        ``min_edge_pct`` / ``min_ev``. Only the two entry thresholds may
        differ — fee, Kelly and sizing caps always come from the shared
        calculator.
        """
        override = self._sport_edge_overrides.get(sport)
        if not override:
            return self._edge
        cached = self._sport_edge_cache.get(sport)
        if cached is not None:
            return cached
        calc = EdgeCalculator(
            fee_pct=self._edge.fee_pct,
            kelly_fraction=self._edge.kelly_fraction,
            base_bet_pct=self._edge.base_bet_pct,
            max_bet_pct=self._edge.max_bet_pct,
            high_edge_threshold=self._edge.high_edge_threshold,
            max_edge_threshold=self._edge.max_edge_threshold,
            min_edge_pct=float(override.get("min_edge_pct", self._edge.min_edge_pct)),
            min_ev=float(override.get("min_ev", self._edge.min_ev)),
        )
        self._sport_edge_cache[sport] = calc
        return calc

    # ── Fatigue-adjusted edge re-evaluation ──────────────────────────────────

    def _adjusted_edge_calc(self, multiplier: float,
                            base: Optional[EdgeCalculator] = None) -> EdgeCalculator:
        """
        Create a temporary EdgeCalculator with thresholds lowered by
        the fatigue confidence multiplier.

        The multiplier (e.g. 1.5 for B2B) divides into min_edge_pct and
        min_ev, so a 3% edge threshold becomes 2% when the multiplier is
        1.5.  All other parameters (fee, Kelly, caps) stay the same.
        ``base`` carries any per-sport threshold override; defaults to
        the shared calculator.
        """
        base = base or self._edge
        return EdgeCalculator(
            fee_pct=base.fee_pct,
            kelly_fraction=base.kelly_fraction,
            base_bet_pct=base.base_bet_pct,
            max_bet_pct=base.max_bet_pct,
            high_edge_threshold=base.high_edge_threshold,
            max_edge_threshold=base.max_edge_threshold,
            min_edge_pct=base.min_edge_pct / multiplier,
            min_ev=base.min_ev / multiplier,
        )

    def _make_entry(
        self,
        now: datetime,
        ticker: str,
        sport: str,
        event_label: str,
        result: EdgeResult,
        fair_yes_prob: float,
        kalshi_prob: float,
        size_usd: float,
        action: str,
        skip_reason: Optional[str] = None,
        fill_price: Optional[float] = None,
        fatigue_context: Optional[Dict[str, Any]] = None,
        yes_side: Optional[str] = None,
        game_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a trade-log entry dict (mirrors trade_log.schema.json)."""
        fp = _make_fingerprint(ticker, result.side, now)
        entry: Dict[str, Any] = {
            "fingerprint":      fp,
            "timestamp_utc":    now.isoformat(),
            "pod_id":           "P-001",
            "pod_name":         "Kalshi vs Sharp's Strategy",
            "mode":             self._mode,
            "sport":            sport,
            "event":            event_label,
            "market_ticker":    ticker,
            "side":             result.side,
            "fair_prob":        round(result.fair_prob, 6),
            "kalshi_prob":      round(result.kalshi_price, 6),
            "edge_pct":         round(result.raw_edge, 6),
            "ev":               round(result.ev, 6),
            "kelly_fraction":   round(result.kelly_fractional, 6),
            "position_size_usd": round(size_usd, 2),
            "action":           action,
            "order_id":         None,
            "fill_price":       round(fill_price, 6) if fill_price else None,
        }
        if skip_reason:
            entry["skip_reason"] = skip_reason
        if fatigue_context:
            entry["fatigue_context"] = fatigue_context
        if yes_side:
            entry["yes_side"] = yes_side
        if game_time:
            entry["game_time"] = game_time
        return entry

    def _clv_passes(self, fair: float, price: float) -> bool:
        """Validated MLB CLV rule: underdog side (price < max_entry_price) AND
        positive net edge vs the de-vigged sharp fair, using the maker fee."""
        g = self._clv_gate
        if price >= float(g.get("max_entry_price", 0.50)):
            return False
        try:
            from src.kalshi_fees import net_edge
            return net_edge(float(fair), float(price),
                            maker=bool(g.get("maker", True))) > float(g.get("min_net_edge", 0.0))
        except Exception:
            return False

    def _write_log(self, entry: Dict[str, Any]) -> None:
        """Append one JSONL record to the trade log."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError as exc:
            log.error(
                "trade_log_write_error",
                path=str(self._log_path),
                error=str(exc),
            )
