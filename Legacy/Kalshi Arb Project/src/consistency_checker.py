"""
src/consistency_checker.py
──────────────────────────
Implied probability consistency arbitrage across Kalshi market tiers.

Core thesis
───────────
Kalshi lists multiple market tiers for the same team — game winners,
playoff qualifiers, conference champions, NBA champion.  Each tier embeds
an implied probability that should be internally consistent but often
isn't, because different liquidity pools price them independently.

This module ingests all Kalshi basketball markets simultaneously, groups
them by team, and checks cross-tier consistency.  Violations are flagged
as ``ConsistencySignal`` objects — tradeable structural arbitrage.

Architecture
────────────
  GameMarketInfo       — one upcoming game market for a team (frozen).
  TeamMarketSnapshot   — all market data for one team across tiers (frozen).
  ConsistencySignal    — detected inconsistency with trade recommendation (frozen).
  ConsistencyChecker   — stateful: runs checks, writes signals to log.

Consistency checks
──────────────────
  1. Tier monotonicity:  P(champ) ≤ P(conf) ≤ P(playoff)
  2. Game-vs-playoff:    aggregate game-win rate vs playoff probability
  3. Conf-vs-champ:      P(champ)/P(conf) conditional probability sanity

Data flow
─────────
  Main loop calls scan_cycle(kalshi_markets) after Scanner.scan_once().
  The markets list is already fetched — zero extra Kalshi API calls.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from logger_setup import get_logger
from team_resolver import (
    get_conference,
    resolve_team,
)

log = get_logger(__name__)


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GameMarketInfo:
    """One upcoming game market for a team."""
    ticker: str
    opponent: Optional[str]
    implied_win_prob: float       # mid-price = implied probability
    commence_time: Optional[datetime]
    volume: int


@dataclass(frozen=True)
class TeamMarketSnapshot:
    """All Kalshi market data for one team across all tiers."""
    team: str                                # canonical abbreviation (e.g. "BOS")
    game_markets: tuple                      # tuple of GameMarketInfo
    playoff_prob: Optional[float]            # implied P(make playoffs)
    conference_prob: Optional[float]         # implied P(win conference)
    championship_prob: Optional[float]       # implied P(win title)
    playoff_ticker: Optional[str]
    conference_ticker: Optional[str]
    championship_ticker: Optional[str]


@dataclass(frozen=True)
class ConsistencySignal:
    """
    A detected inconsistency between market tiers.

    Attributes
    ----------
    team : str
        Canonical abbreviation.
    signal_type : str
        "TIER_MONOTONICITY" | "GAME_VS_PLAYOFF" | "CONF_VS_CHAMP"
    description : str
        Human-readable description of the inconsistency.
    divergence : float
        Magnitude of the inconsistency (absolute probability difference).
    recommended_side : str
        "YES" or "NO" — which side to trade on the recommended market.
    recommended_ticker : str
        Which Kalshi market to trade.
    recommended_price : float
        Current mid-price of the recommended market.
    confidence : float
        0.0–1.0, based on divergence magnitude and data quality.
    supporting_data : dict
        Additional context for logging and analysis.
    """
    team: str
    signal_type: str
    description: str
    divergence: float
    recommended_side: str
    recommended_ticker: str
    recommended_price: float
    confidence: float
    supporting_data: dict = field(default_factory=dict)


# ── Mid-price extraction ─────────────────────────────────────────────────────

def _mid_price(market: Dict[str, Any]) -> Optional[float]:
    """
    Extract YES mid-price from a Kalshi market dict.

    Kalshi returns yes_bid / yes_ask as integer cents (0–100).
    Mid = (yes_bid + yes_ask) / 200 → probability in (0, 1).
    """
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


# ── ConsistencyChecker ───────────────────────────────────────────────────────

class ConsistencyChecker:
    """
    Cross-tier implied probability consistency checker.

    Parameters
    ----------
    series_tickers : dict
        Mapping of tier name → Kalshi series ticker prefix.
        Example: {"game": "KXNBAGAME", "playoff": "KXNBAPLAYOFF", ...}
    min_divergence : float
        Minimum probability inconsistency to flag (e.g. 0.05 = 5%).
    min_game_markets : int
        Need ≥ this many upcoming game markets for game-rate estimate.
    min_volume : int
        Per-market volume floor.
    log_path : Path
        Where to write consistency_log.jsonl.
    max_conditional_finals_prob : float
        Maximum realistic P(win finals | won conference). Above this
        is flagged as CONF_VS_CHAMP inconsistency.
    _now_fn : callable, optional
        Injectable clock for testing.
    """

    def __init__(
        self,
        series_tickers: Optional[Dict[str, str]] = None,
        min_divergence: float = 0.05,
        min_game_markets: int = 3,
        min_volume: int = 200,
        log_path: Optional[Path] = None,
        max_conditional_finals_prob: float = 0.75,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._series_tickers = series_tickers or {
            "game": "KXNBAGAME",
            "playoff": "KXNBAPLAYOFF",
            "conference_east": "KXNBAEAST",
            "conference_west": "KXNBAWEST",
            "championship": "KXNBA",
        }
        self._min_divergence = min_divergence
        self._min_game_markets = min_game_markets
        self._min_volume = min_volume
        self._log_path = log_path or Path("data/trade_logs/consistency_log.jsonl")
        self._max_cond_finals = max_conditional_finals_prob
        self._now = _now_fn or (lambda: datetime.now(tz=timezone.utc))

    @classmethod
    def from_config(cls, config: dict, **kwargs) -> "ConsistencyChecker":
        """Construct from a loaded config.yaml dict."""
        cfg = config.get("consistency", {})
        return cls(
            series_tickers=cfg.get("series_tickers"),
            min_divergence=cfg.get("min_divergence", 0.05),
            min_game_markets=cfg.get("min_game_markets", 3),
            min_volume=cfg.get("min_volume", 200),
            log_path=Path(cfg.get("log_path", "data/trade_logs/consistency_log.jsonl")),
            max_conditional_finals_prob=cfg.get("max_conditional_finals_prob", 0.75),
            **kwargs,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def scan_cycle(
        self,
        kalshi_markets: List[Dict[str, Any]],
    ) -> List[ConsistencySignal]:
        """
        Run a full consistency check across all teams and tiers.

        Parameters
        ----------
        kalshi_markets : list of dict
            Raw Kalshi market dicts (already fetched by Scanner).

        Returns
        -------
        List of ConsistencySignal objects for detected inconsistencies.
        """
        snapshots = self._group_by_team(kalshi_markets)

        signals: List[ConsistencySignal] = []
        for team, snapshot in snapshots.items():
            signals.extend(self._check_tier_monotonicity(snapshot))
            signals.extend(self._check_game_vs_playoff(snapshot))
            signals.extend(self._check_conference_vs_championship(snapshot))

        for signal in signals:
            self._write_log(signal)

        if signals:
            log.info(
                "consistency_scan_complete",
                teams_checked=len(snapshots),
                signals_found=len(signals),
                signal_types=[s.signal_type for s in signals],
            )
        else:
            log.debug(
                "consistency_scan_clean",
                teams_checked=len(snapshots),
            )

        return signals

    # ── Market grouping ──────────────────────────────────────────────────────

    def _group_by_team(
        self,
        kalshi_markets: List[Dict[str, Any]],
    ) -> Dict[str, TeamMarketSnapshot]:
        """
        Group raw Kalshi markets by team across all tiers.

        Returns a dict mapping canonical team abbreviation → TeamMarketSnapshot.
        Only includes teams that have at least one recognized market.
        """
        # Categorize markets by tier
        game_prefix = self._series_tickers.get("game", "KXNBAGAME")
        playoff_prefix = self._series_tickers.get("playoff", "KXNBAPLAYOFF")
        conf_east_prefix = self._series_tickers.get("conference_east", "KXNBAEAST")
        conf_west_prefix = self._series_tickers.get("conference_west", "KXNBAWEST")
        champ_prefix = self._series_tickers.get("championship", "KXNBA")

        # Per-team accumulator
        team_games: Dict[str, List[GameMarketInfo]] = defaultdict(list)
        team_playoff: Dict[str, tuple] = {}     # team → (prob, ticker)
        team_conference: Dict[str, tuple] = {}   # team → (prob, ticker)
        team_championship: Dict[str, tuple] = {} # team → (prob, ticker)

        for market in kalshi_markets:
            ticker = market.get("ticker", "")
            title = market.get("title", "")
            series = market.get("series_ticker", "")

            # Volume filter
            volume = int(market.get("volume") or 0)
            if volume < self._min_volume:
                continue

            mid = _mid_price(market)
            if mid is None:
                continue

            team = resolve_team(ticker, title)
            if team is None:
                continue

            # Categorize by series/ticker prefix
            if series.startswith(game_prefix) or ticker.startswith(game_prefix):
                commence_str = market.get("close_time") or market.get("expiration_time", "")
                commence_time = None
                if commence_str:
                    try:
                        commence_time = datetime.fromisoformat(
                            str(commence_str).replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        pass
                team_games[team].append(GameMarketInfo(
                    ticker=ticker,
                    opponent=None,  # could parse from ticker but not needed for v1
                    implied_win_prob=mid,
                    commence_time=commence_time,
                    volume=volume,
                ))
            elif series.startswith(playoff_prefix) or ticker.startswith(playoff_prefix):
                team_playoff[team] = (mid, ticker)
            elif (series.startswith(conf_east_prefix) or ticker.startswith(conf_east_prefix)
                  or series.startswith(conf_west_prefix) or ticker.startswith(conf_west_prefix)):
                team_conference[team] = (mid, ticker)
            elif series.startswith(champ_prefix) or ticker.startswith(champ_prefix):
                # Championship prefix is a substring of other tickers (KXNBA matches
                # KXNBAPLAYOFF, KXNBAEAST, etc).  Only match if it's the exact prefix
                # and not already categorized above.
                if not (ticker.startswith(game_prefix)
                        or ticker.startswith(playoff_prefix)
                        or ticker.startswith(conf_east_prefix)
                        or ticker.startswith(conf_west_prefix)):
                    team_championship[team] = (mid, ticker)

        # Build snapshots
        all_teams: Set[str] = (
            set(team_games.keys())
            | set(team_playoff.keys())
            | set(team_conference.keys())
            | set(team_championship.keys())
        )

        snapshots: Dict[str, TeamMarketSnapshot] = {}
        for team in sorted(all_teams):
            playoff_data = team_playoff.get(team)
            conf_data = team_conference.get(team)
            champ_data = team_championship.get(team)

            snapshots[team] = TeamMarketSnapshot(
                team=team,
                game_markets=tuple(team_games.get(team, [])),
                playoff_prob=playoff_data[0] if playoff_data else None,
                conference_prob=conf_data[0] if conf_data else None,
                championship_prob=champ_data[0] if champ_data else None,
                playoff_ticker=playoff_data[1] if playoff_data else None,
                conference_ticker=conf_data[1] if conf_data else None,
                championship_ticker=champ_data[1] if champ_data else None,
            )

        return snapshots

    # ── Consistency checks ───────────────────────────────────────────────────

    def _check_tier_monotonicity(
        self,
        snapshot: TeamMarketSnapshot,
    ) -> List[ConsistencySignal]:
        """
        P(champion) ≤ P(conference) ≤ P(playoff) must always hold.

        Any violation is a pure structural arbitrage opportunity.
        """
        signals: List[ConsistencySignal] = []
        team = snapshot.team

        # Check: P(championship) ≤ P(conference)
        if (snapshot.championship_prob is not None
                and snapshot.conference_prob is not None):
            if snapshot.championship_prob > snapshot.conference_prob + self._min_divergence:
                div = snapshot.championship_prob - snapshot.conference_prob
                signals.append(ConsistencySignal(
                    team=team,
                    signal_type="TIER_MONOTONICITY",
                    description=(
                        f"{team}: P(champ)={snapshot.championship_prob:.3f} > "
                        f"P(conf)={snapshot.conference_prob:.3f} — "
                        f"championship overpriced or conference underpriced"
                    ),
                    divergence=div,
                    recommended_side="NO",
                    recommended_ticker=snapshot.championship_ticker or "",
                    recommended_price=snapshot.championship_prob,
                    confidence=min(1.0, div / 0.10),  # max confidence at 10% divergence
                    supporting_data={
                        "championship_prob": snapshot.championship_prob,
                        "conference_prob": snapshot.conference_prob,
                        "conference_ticker": snapshot.conference_ticker,
                    },
                ))

        # Check: P(conference) ≤ P(playoff)
        if (snapshot.conference_prob is not None
                and snapshot.playoff_prob is not None):
            if snapshot.conference_prob > snapshot.playoff_prob + self._min_divergence:
                div = snapshot.conference_prob - snapshot.playoff_prob
                signals.append(ConsistencySignal(
                    team=team,
                    signal_type="TIER_MONOTONICITY",
                    description=(
                        f"{team}: P(conf)={snapshot.conference_prob:.3f} > "
                        f"P(playoff)={snapshot.playoff_prob:.3f} — "
                        f"conference overpriced or playoff underpriced"
                    ),
                    divergence=div,
                    recommended_side="NO",
                    recommended_ticker=snapshot.conference_ticker or "",
                    recommended_price=snapshot.conference_prob,
                    confidence=min(1.0, div / 0.10),
                    supporting_data={
                        "conference_prob": snapshot.conference_prob,
                        "playoff_prob": snapshot.playoff_prob,
                        "playoff_ticker": snapshot.playoff_ticker,
                    },
                ))

        # Check: P(championship) ≤ P(playoff)
        if (snapshot.championship_prob is not None
                and snapshot.playoff_prob is not None):
            if snapshot.championship_prob > snapshot.playoff_prob + self._min_divergence:
                div = snapshot.championship_prob - snapshot.playoff_prob
                signals.append(ConsistencySignal(
                    team=team,
                    signal_type="TIER_MONOTONICITY",
                    description=(
                        f"{team}: P(champ)={snapshot.championship_prob:.3f} > "
                        f"P(playoff)={snapshot.playoff_prob:.3f} — "
                        f"championship overpriced or playoff underpriced"
                    ),
                    divergence=div,
                    recommended_side="NO",
                    recommended_ticker=snapshot.championship_ticker or "",
                    recommended_price=snapshot.championship_prob,
                    confidence=min(1.0, div / 0.10),
                    supporting_data={
                        "championship_prob": snapshot.championship_prob,
                        "playoff_prob": snapshot.playoff_prob,
                        "playoff_ticker": snapshot.playoff_ticker,
                    },
                ))

        return signals

    def _check_game_vs_playoff(
        self,
        snapshot: TeamMarketSnapshot,
    ) -> List[ConsistencySignal]:
        """
        Compare aggregate game-win rate to playoff probability.

        If the average game-win rate is very high but playoff probability
        is low (or vice versa), one tier is likely mispriced.
        """
        if snapshot.playoff_prob is None:
            return []
        if len(snapshot.game_markets) < self._min_game_markets:
            return []

        # Compute volume-weighted average game-win probability
        total_vol = sum(gm.volume for gm in snapshot.game_markets)
        if total_vol == 0:
            return []

        avg_win_prob = sum(
            gm.implied_win_prob * gm.volume for gm in snapshot.game_markets
        ) / total_vol

        # Rough mapping: game win rate → expected playoff probability
        # This is a simplified heuristic.  In the NBA:
        #   ~60% win rate ≈ 49 wins ≈ 85-90% playoff probability
        #   ~50% win rate ≈ 41 wins ≈ 50-55% playoff probability
        #   ~40% win rate ≈ 33 wins ≈ 10-15% playoff probability
        expected_playoff_prob = self._game_rate_to_playoff_prob(avg_win_prob)

        divergence = abs(expected_playoff_prob - snapshot.playoff_prob)
        if divergence < self._min_divergence:
            return []

        # Determine which direction to trade
        if snapshot.playoff_prob < expected_playoff_prob:
            # Playoff market is underpriced relative to game performance
            side = "YES"
            ticker = snapshot.playoff_ticker or ""
            price = snapshot.playoff_prob
            desc = (
                f"{snapshot.team}: game win rate {avg_win_prob:.3f} implies "
                f"~{expected_playoff_prob:.3f} playoff prob, but market is "
                f"{snapshot.playoff_prob:.3f} — playoff YES underpriced"
            )
        else:
            # Playoff market is overpriced relative to game performance
            side = "NO"
            ticker = snapshot.playoff_ticker or ""
            price = snapshot.playoff_prob
            desc = (
                f"{snapshot.team}: game win rate {avg_win_prob:.3f} implies "
                f"~{expected_playoff_prob:.3f} playoff prob, but market is "
                f"{snapshot.playoff_prob:.3f} — playoff YES overpriced"
            )

        return [ConsistencySignal(
            team=snapshot.team,
            signal_type="GAME_VS_PLAYOFF",
            description=desc,
            divergence=divergence,
            recommended_side=side,
            recommended_ticker=ticker,
            recommended_price=price,
            confidence=min(1.0, divergence / 0.15),
            supporting_data={
                "avg_game_win_prob": round(avg_win_prob, 4),
                "expected_playoff_prob": round(expected_playoff_prob, 4),
                "actual_playoff_prob": snapshot.playoff_prob,
                "game_count": len(snapshot.game_markets),
                "total_volume": total_vol,
            },
        )]

    def _check_conference_vs_championship(
        self,
        snapshot: TeamMarketSnapshot,
    ) -> List[ConsistencySignal]:
        """
        P(champion) = P(conference) × P(finals win | won conference).

        If the implied conditional P(finals win | won conf) exceeds
        max_conditional_finals_prob, the relationship is inconsistent.
        Historically this averages ~0.50 with a range of 0.35-0.65.
        """
        if snapshot.conference_prob is None or snapshot.championship_prob is None:
            return []
        if snapshot.conference_prob < 0.01:
            return []  # avoid division by near-zero

        conditional = snapshot.championship_prob / snapshot.conference_prob

        if conditional <= self._max_cond_finals:
            return []

        divergence = conditional - self._max_cond_finals
        # The championship is overpriced relative to conference, OR
        # the conference is underpriced relative to championship.
        # Trade whichever has more volume (approximated by picking
        # championship NO as default since it's the overpriced leg).
        return [ConsistencySignal(
            team=snapshot.team,
            signal_type="CONF_VS_CHAMP",
            description=(
                f"{snapshot.team}: P(champ)/P(conf) = "
                f"{snapshot.championship_prob:.3f}/{snapshot.conference_prob:.3f} "
                f"= {conditional:.3f} — implies unrealistic "
                f"{conditional*100:.0f}% finals win rate"
            ),
            divergence=divergence,
            recommended_side="NO",
            recommended_ticker=snapshot.championship_ticker or "",
            recommended_price=snapshot.championship_prob,
            confidence=min(1.0, divergence / 0.25),
            supporting_data={
                "championship_prob": snapshot.championship_prob,
                "conference_prob": snapshot.conference_prob,
                "implied_conditional": round(conditional, 4),
                "max_conditional": self._max_cond_finals,
                "conference_ticker": snapshot.conference_ticker,
            },
        )]

    # ── Heuristic helpers ────────────────────────────────────────────────────

    @staticmethod
    def _game_rate_to_playoff_prob(win_rate: float) -> float:
        """
        Simplified mapping from per-game win rate to playoff probability.

        Based on historical NBA data (approximate logistic curve):
          0.40 win rate → ~10% playoff
          0.45 win rate → ~30% playoff
          0.50 win rate → ~50% playoff
          0.55 win rate → ~75% playoff
          0.60 win rate → ~90% playoff
          0.65 win rate → ~97% playoff

        Uses a logistic function: P(playoff) = 1 / (1 + exp(-k*(x - x0)))
        where k ≈ 20 and x0 ≈ 0.50 (calibrated to historical data).
        """
        import math
        k = 20.0      # steepness
        x0 = 0.50     # midpoint (50% win rate ≈ 50% playoff)
        raw = 1.0 / (1.0 + math.exp(-k * (win_rate - x0)))
        return max(0.01, min(0.99, raw))

    # ── Logging ──────────────────────────────────────────────────────────────

    def _write_log(self, signal: ConsistencySignal) -> None:
        """Append one JSONL record to the consistency log."""
        now = self._now()
        record = {
            "timestamp_utc": now.isoformat(),
            "team": signal.team,
            "signal_type": signal.signal_type,
            "description": signal.description,
            "divergence": round(signal.divergence, 6),
            "recommended_side": signal.recommended_side,
            "recommended_ticker": signal.recommended_ticker,
            "recommended_price": round(signal.recommended_price, 6),
            "confidence": round(signal.confidence, 4),
            "supporting_data": signal.supporting_data,
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.error(
                "consistency_log_write_error",
                path=str(self._log_path),
                error=str(exc),
            )
