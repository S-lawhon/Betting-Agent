"""
src/pods/live_game_pod.py
─────────────────────────
P-014: Live Game Agent pod.

Scans live/in-play sports betting markets for mispricings between
Kalshi and the sharp sportsbook consensus (via Odds API).  Designed
for fast-cycle operation (every 10-30 seconds) during live games.

Strategy:
  1. Poll Odds API for live odds across multiple sportsbooks
  2. Poll scores to track game state
  3. Discover matching Kalshi market via KalshiLiveDiscovery
  4. Compute game-state-aware fair value via LiveFairValueEngine
  5. Compare to Kalshi live market price
  6. Check MomentumDetector for overreaction signals
  7. When edge exceeds threshold → generate trade signal

Phase 2: Full pipeline — discovery, fair value, momentum, execution.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.base_pod import BasePod, ScanResult
from src.game_state import GamePhase, GameState, GameStateTracker
from src.kalshi_live_discovery import KalshiLiveDiscovery, KalshiMarketMatch
from src.live_fair_value import LiveFairValueEngine, LiveFairValueResult
from src.live_odds_poller import LiveOddsPoller, LiveOddsSnapshot
from src.momentum_detector import MomentumDetector, MomentumSignal
from src.pod_registry import register_pod

logger = logging.getLogger(__name__)


@register_pod("P-014")
class LiveGamePod(BasePod):
    """P-014: Live Game Agent.

    Scans live in-game markets for consensus-vs-venue edge on Kalshi.
    Operates at a faster cycle than pre-game pods (10-30s vs 5-10min).
    """

    def __init__(
        self,
        poller: LiveOddsPoller,
        game_tracker: GameStateTracker,
        kalshi_client=None,
        discovery: Optional[KalshiLiveDiscovery] = None,
        fair_value_engine: Optional[LiveFairValueEngine] = None,
        momentum_detector: Optional[MomentumDetector] = None,
        edge_calculator=None,
        risk_manager=None,
        trade_log_path=None,
        mode: str = "paper",
        _now_fn=None,
        trade_store=None,
        # P-014 specific config
        sports: List[str] = None,
        min_edge_pct: float = 0.02,
        min_books_for_consensus: int = 3,
        confidence_floor: float = 0.6,
        max_exposure_per_game_usd: float = 150.0,
        max_trades_per_game: int = 2,
        max_concurrent_games: int = 5,
        kalshi_market_map: Optional[Dict[str, str]] = None,
    ):
        super().__init__(
            pod_id="P-014",
            pod_name="Live Game Agent",
            edge_calculator=edge_calculator,
            risk_manager=risk_manager,
            trade_log_path=trade_log_path or Path("data/trade_logs/trade_log.jsonl"),
            mode=mode,
            _now_fn=_now_fn,
            trade_store=trade_store,
        )
        self._poller = poller
        self._game_tracker = game_tracker
        self._kalshi_client = kalshi_client
        self._sports = sports or ["basketball_nba", "baseball_mlb"]
        self._min_edge_pct = min_edge_pct
        self._min_books = min_books_for_consensus
        self._confidence_floor = confidence_floor
        self._max_exposure_per_game = max_exposure_per_game_usd
        self._max_trades_per_game = max_trades_per_game
        self._max_concurrent_games = max_concurrent_games

        # Phase 2 components
        self._discovery = discovery
        if self._discovery is None and kalshi_client is not None:
            self._discovery = KalshiLiveDiscovery(kalshi_client)
        self._fv_engine = fair_value_engine or LiveFairValueEngine()
        self._momentum = momentum_detector or MomentumDetector()

        # game_id → Kalshi market ticker (discovery cache)
        self._kalshi_market_map: Dict[str, str] = kalshi_market_map or {}

        # Per-game exposure tracking: game_id → total USD exposed
        self._game_exposure: Dict[str, float] = {}
        # Per-game trade count: game_id → number of trades placed
        self._game_trade_count: Dict[str, int] = {}

        # Load existing game exposure/counts from trade log so limits
        # survive service restarts
        self._load_game_limits_from_log()

        # Cycle metrics
        self._cycle_count: int = 0
        self._total_signals: int = 0
        self._total_trades: int = 0

    def _load_game_limits_from_log(self) -> None:
        """Rebuild per-game exposure and trade counts from the trade log.

        This ensures that per-game limits survive service restarts.
        Only counts trades from today (UTC) to avoid stale limits from
        yesterday's games blocking today's trades.
        """
        from datetime import datetime, timezone
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if self._trade_store is not None:
            # Read from trade store's in-memory placed entries
            for rec in self._trade_store.get_placed_entries(pod_id=self.pod_id):
                ts = rec.get("timestamp_utc", "")
                if not ts.startswith(today_str):
                    continue
                game_id = (rec.get("extra") or {}).get("game_id", "")
                if not game_id:
                    ticker = rec.get("market_id") or rec.get("market_ticker") or ""
                    game_id = ticker
                size = float(rec.get("position_size_usd") or 0)
                self._game_exposure[game_id] = self._game_exposure.get(game_id, 0.0) + size
                self._game_trade_count[game_id] = self._game_trade_count.get(game_id, 0) + 1
        elif self.trade_log_path.exists():
            import json as _json
            for line in self.trade_log_path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    rec = _json.loads(line)
                except (ValueError, _json.JSONDecodeError):
                    continue
                if rec.get("pod_id") != self.pod_id:
                    continue
                if (rec.get("action") or "").upper() != "PLACED":
                    continue
                ts = rec.get("timestamp_utc", "")
                if not ts.startswith(today_str):
                    continue
                game_id = (rec.get("extra") or {}).get("game_id", "")
                if not game_id:
                    ticker = rec.get("market_id") or rec.get("market_ticker") or ""
                    game_id = ticker
                size = float(rec.get("position_size_usd") or 0)
                self._game_exposure[game_id] = self._game_exposure.get(game_id, 0.0) + size
                self._game_trade_count[game_id] = self._game_trade_count.get(game_id, 0) + 1

    def scan_once(self) -> List[ScanResult]:
        """Run one live scan cycle across all configured sports.

        Pipeline per live game:
          1. Poll live odds from sportsbooks
          2. Update game state (scores)
          3. Compute fair value (LiveFairValueEngine)
          4. Discover Kalshi market (KalshiLiveDiscovery)
          5. Record snapshot for MomentumDetector
          6. Check consensus edge + momentum signals
          7. Place paper trade if edge exceeds threshold
        """
        self._cycle_count += 1
        results: List[ScanResult] = []
        now = self._now_fn()
        now_str = now.isoformat()

        active_game_count = len([
            gid for gid, exp in self._game_exposure.items() if exp > 0
        ])

        for sport in self._sports:
            # Step 1: Poll live odds
            snapshots = self._poller.poll(sport)
            if not snapshots:
                continue

            # Step 2: Update game state
            self._game_tracker.update(sport)

            for snapshot in snapshots:
                game_id = snapshot.game_id

                # Max concurrent games check
                if (game_id not in self._game_exposure and
                        active_game_count >= self._max_concurrent_games):
                    continue

                # Min books check
                if snapshot.num_books < self._min_books:
                    continue

                game_state = self._game_tracker.get_game(game_id)

                # Step 3: Compute fair value
                fv_result = self._fv_engine.estimate(snapshot, game_state)
                if fv_result is None:
                    continue
                fair_home = fv_result.home_prob
                fair_away = fv_result.away_prob

                # Confidence floor check
                if fv_result.confidence < self._confidence_floor:
                    continue

                # Step 4: Discover Kalshi market
                kalshi_match = self._find_kalshi_market(snapshot, game_id)

                if kalshi_match is None:
                    # No Kalshi market — log data for analysis
                    result = self._build_data_result(
                        snapshot, game_state, fv_result, now_str,
                    )
                    if result:
                        results.append(result)
                    continue

                # Step 5: Record for momentum detection
                self._momentum.record_snapshot(
                    game_id=game_id,
                    kalshi_home_prob=kalshi_match.home_yes_prob,
                    consensus_home_prob=fair_home,
                    timestamp=now_str,
                    home_score=game_state.home_score if game_state else 0,
                    away_score=game_state.away_score if game_state else 0,
                )

                # Step 6: Check for edge opportunities
                # 6a: Consensus edge (primary signal)
                trade_results = self._evaluate_consensus_edge(
                    snapshot, game_state, fv_result, kalshi_match, now_str,
                )
                results.extend(trade_results)

                # 6b: Momentum signals (secondary)
                momentum_results = self._evaluate_momentum_signals(
                    snapshot, game_state, fv_result, kalshi_match, now_str,
                )
                results.extend(momentum_results)

                # Update active game count
                active_game_count = len([
                    gid for gid, exp in self._game_exposure.items() if exp > 0
                ])

        if results:
            placed = sum(1 for r in results if r.action == "PLACED")
            logger.info(
                "P-014: cycle %d — %d results (%d placed), %d live games, %d momentum tracked",
                self._cycle_count, len(results), placed,
                active_game_count, self._momentum.tracked_games,
            )

        return results

    def venue_name(self) -> str:
        return "kalshi"

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "LiveGamePod":
        """Build from config_multi_pod.yaml P-014 section."""
        p014_cfg = config.get("pods", {}).get("P-014", {})
        scan_cfg = p014_cfg.get("scan", {})
        signal_cfg = p014_cfg.get("signals", {})
        risk_cfg = p014_cfg.get("risk", {})
        fv_cfg = p014_cfg.get("fair_value", {})

        mode_map = {"demo": "paper", "live": "live"}
        env = p014_cfg.get("environment", "demo")
        mode = mode_map.get(env, "paper")

        poller = overrides.get("poller")
        if poller is None:
            poller = LiveOddsPoller.from_config(config)

        game_tracker = overrides.get("game_tracker")
        if game_tracker is None:
            game_tracker = GameStateTracker.from_config(config)

        return cls(
            poller=poller,
            game_tracker=game_tracker,
            kalshi_client=overrides.get("kalshi_client"),
            discovery=overrides.get("discovery"),
            fair_value_engine=overrides.get("fair_value_engine"),
            momentum_detector=overrides.get("momentum_detector"),
            edge_calculator=overrides.get("edge_calculator"),
            risk_manager=overrides.get("risk_manager"),
            trade_log_path=overrides.get("trade_log_path"),
            mode=mode,
            _now_fn=overrides.get("_now_fn"),
            trade_store=overrides.get("trade_store"),
            sports=p014_cfg.get("sports", ["basketball_nba", "baseball_mlb"]),
            min_edge_pct=signal_cfg.get("min_edge_pct", 0.02),
            min_books_for_consensus=fv_cfg.get("min_books_for_consensus", 3),
            confidence_floor=fv_cfg.get("confidence_floor", 0.6),
            max_exposure_per_game_usd=risk_cfg.get("max_exposure_per_game_usd", 500.0),
            max_concurrent_games=scan_cfg.get("max_concurrent_games", 5),
        )

    # ── Core pipeline methods ─────────────────────────────────────────

    def _find_kalshi_market(
        self, snapshot: LiveOddsSnapshot, game_id: str
    ) -> Optional[KalshiMarketMatch]:
        """Discover or retrieve cached Kalshi market for a game."""
        if self._discovery is None:
            return None

        return self._discovery.find_market(
            sport=snapshot.sport,
            home_team=snapshot.home_team,
            away_team=snapshot.away_team,
            game_id=game_id,
        )

    def _evaluate_consensus_edge(
        self,
        snapshot: LiveOddsSnapshot,
        game_state: Optional[GameState],
        fv_result: LiveFairValueResult,
        kalshi_match: KalshiMarketMatch,
        now_str: str,
    ) -> List[ScanResult]:
        """Evaluate consensus edge for both sides of a Kalshi market."""
        results: List[ScanResult] = []
        game_id = snapshot.game_id

        # Determine Kalshi probabilities based on YES side
        if kalshi_match.yes_side == "home":
            sides = [
                ("YES", fv_result.home_prob, kalshi_match.mid_price),
                ("NO", fv_result.away_prob, 1.0 - kalshi_match.mid_price),
            ]
        else:
            sides = [
                ("YES", fv_result.away_prob, kalshi_match.mid_price),
                ("NO", fv_result.home_prob, 1.0 - kalshi_match.mid_price),
            ]

        # Game-progress-aware edge threshold: late-game edges are more
        # reliable (less remaining variance), so we accept smaller edges.
        # At game start: full min_edge_pct.  At game end: 60% of min_edge_pct.
        progress = game_state.game_progress_pct() if game_state else 0.0
        adjusted_min_edge = self._min_edge_pct * (1.0 - progress * 0.40)

        # Spread-aware floor: don't trade if the spread eats too much edge
        spread_floor = (kalshi_match.spread_cents / 2.0) / 100.0 if kalshi_match.spread_cents > 0 else 0.0
        effective_min_edge = max(adjusted_min_edge, spread_floor * 1.2)

        # Minimum absolute edge: percentage edge is misleading on extreme
        # probabilities (e.g. 3.5¢ difference on a 5.5¢ contract = 63% edge
        # but only 3.5¢ absolute).  Require at least 5¢ absolute difference.
        min_absolute_edge = 0.05

        # Maximum fair-vs-venue disagreement: if our model and the market
        # disagree by more than 20 points, the model is likely wrong rather
        # than the market being wildly mispriced.
        max_divergence = 0.20

        for side, fair_prob, kalshi_prob in sides:
            # Skip extreme probabilities — too noisy for reliable edge
            if kalshi_prob <= 0.10 or kalshi_prob >= 0.90:
                continue

            # Absolute edge check first
            abs_edge = fair_prob - kalshi_prob
            if abs_edge < min_absolute_edge:
                continue

            # Sanity check: reject implausibly large model-vs-market gaps
            if abs_edge > max_divergence:
                continue

            edge = abs_edge / kalshi_prob
            if edge < effective_min_edge:
                continue

            result = self._try_place_trade(
                snapshot=snapshot,
                game_state=game_state,
                kalshi_match=kalshi_match,
                side=side,
                fair_prob=fair_prob,
                venue_prob=kalshi_prob,
                edge=edge,
                now_str=now_str,
                signal_source="consensus",
                fv_result=fv_result,
            )
            if result:
                results.append(result)

        return results

    def _evaluate_momentum_signals(
        self,
        snapshot: LiveOddsSnapshot,
        game_state: Optional[GameState],
        fv_result: LiveFairValueResult,
        kalshi_match: KalshiMarketMatch,
        now_str: str,
    ) -> List[ScanResult]:
        """Check momentum detector for overreaction signals."""
        results: List[ScanResult] = []
        game_id = snapshot.game_id

        signals = self._momentum.detect(game_id)
        if not signals:
            return results

        for signal in signals:
            # Convert momentum direction to trade side
            if kalshi_match.yes_side == "home":
                if signal.direction == "buy_home":
                    side = "YES"
                    fair_prob = fv_result.home_prob
                    kalshi_prob = kalshi_match.mid_price
                else:
                    side = "NO"
                    fair_prob = fv_result.away_prob
                    kalshi_prob = 1.0 - kalshi_match.mid_price
            else:
                if signal.direction == "buy_away":
                    side = "YES"
                    fair_prob = fv_result.away_prob
                    kalshi_prob = kalshi_match.mid_price
                else:
                    side = "NO"
                    fair_prob = fv_result.home_prob
                    kalshi_prob = 1.0 - kalshi_match.mid_price

            if kalshi_prob <= 0.10 or kalshi_prob >= 0.90:
                continue

            # Momentum requires both: overreaction detected AND positive edge
            abs_edge = fair_prob - kalshi_prob
            if abs_edge < 0.05:  # 5¢ minimum absolute edge
                continue
            if abs_edge > 0.20:  # Reject implausibly large gaps
                continue
            edge = abs_edge / kalshi_prob
            if edge < self._min_edge_pct * 0.5:  # Lower threshold for momentum-confirmed trades
                continue

            result = self._try_place_trade(
                snapshot=snapshot,
                game_state=game_state,
                kalshi_match=kalshi_match,
                side=side,
                fair_prob=fair_prob,
                venue_prob=kalshi_prob,
                edge=edge,
                now_str=now_str,
                signal_source=f"momentum_{signal.signal_type.lower()}",
                fv_result=fv_result,
                momentum_signal=signal,
            )
            if result:
                results.append(result)

        return results

    def _try_place_trade(
        self,
        snapshot: LiveOddsSnapshot,
        game_state: Optional[GameState],
        kalshi_match: KalshiMarketMatch,
        side: str,
        fair_prob: float,
        venue_prob: float,
        edge: float,
        now_str: str,
        signal_source: str,
        fv_result: LiveFairValueResult,
        momentum_signal: Optional[MomentumSignal] = None,
    ) -> Optional[ScanResult]:
        """Attempt to place a trade, handling all risk checks."""
        game_id = snapshot.game_id
        market_id = kalshi_match.ticker

        self._total_signals += 1

        # Per-game trade count limit (prevents stacking multiple bets on
        # the same game as odds shift throughout the event)
        current_trades = self._game_trade_count.get(game_id, 0)
        if current_trades >= self._max_trades_per_game:
            return self._build_signal_result(
                snapshot, game_state, kalshi_match, side, fair_prob,
                venue_prob, edge, now_str, fv_result,
                action="SKIPPED_RISK", skip_reason="max_trades_per_game",
                signal_source=signal_source,
            )

        # Per-game exposure check
        current_exposure = self._game_exposure.get(game_id, 0.0)
        if current_exposure >= self._max_exposure_per_game:
            return self._build_signal_result(
                snapshot, game_state, kalshi_match, side, fair_prob,
                venue_prob, edge, now_str, fv_result,
                action="SKIPPED_RISK", skip_reason="max_game_exposure",
                signal_source=signal_source,
            )

        # Dedup check
        fingerprint = self.make_fingerprint(market_id, side)
        if self.is_duplicate(fingerprint):
            return None

        # Position sizing (with live-specific adjustments)
        game_progress = game_state.game_progress_pct() if game_state else 0.0
        position_size = self._compute_live_position_size(
            edge=edge,
            fair_prob=fair_prob,
            venue_prob=venue_prob,
            confidence=fv_result.confidence,
            game_progress=game_progress,
            spread_cents=kalshi_match.spread_cents,
        )
        if position_size <= 0:
            return None

        # Aggregate risk check
        if not self.check_aggregate_risk("kalshi", position_size):
            return self._build_signal_result(
                snapshot, game_state, kalshi_match, side, fair_prob,
                venue_prob, edge, now_str, fv_result,
                action="SKIPPED_RISK", skip_reason="aggregate_risk_limit",
                signal_source=signal_source,
            )

        # Place order
        order_id = self._place_order(market_id, side, position_size, venue_prob)

        action = "PLACED" if order_id else "SKIPPED_RISK"
        if order_id:
            self._total_trades += 1
            self._game_exposure[game_id] = current_exposure + position_size
            self._game_trade_count[game_id] = current_trades + 1
            self.mark_seen(fingerprint)
            self.mark_position_open(market_id)

        kelly = self._kelly_fraction(edge, fair_prob, venue_prob)

        extra = self._build_extra(snapshot, game_state)
        extra["signal_source"] = signal_source
        extra["kalshi_ticker"] = kalshi_match.ticker
        extra["kalshi_yes_side"] = kalshi_match.yes_side
        extra["kalshi_mid_price"] = kalshi_match.mid_price
        extra["kalshi_spread"] = kalshi_match.spread_cents
        extra["kalshi_volume"] = kalshi_match.volume
        extra["fv_method"] = fv_result.method
        extra["fv_confidence"] = fv_result.confidence
        if momentum_signal:
            extra["momentum_type"] = momentum_signal.signal_type
            extra["momentum_score"] = momentum_signal.overreaction_score
            extra["momentum_direction"] = momentum_signal.direction

        result = ScanResult(
            fingerprint=fingerprint,
            timestamp_utc=now_str,
            pod_id=self.pod_id,
            pod_name=self.pod_name,
            mode=self.mode,
            venue="kalshi",
            market_type=f"live_{snapshot.sport}",
            event=f"{snapshot.away_team} @ {snapshot.home_team} (LIVE)",
            market_id=market_id,
            side=side,
            fair_prob=fair_prob,
            venue_prob=venue_prob,
            edge_pct=min(edge, 1.0),  # Cap display edge at 100%
            ev=edge,
            kelly_fraction=kelly,
            position_size_usd=position_size,
            action=action,
            order_id=order_id,
            skip_reason="order_failed" if not order_id and action == "SKIPPED_RISK" else None,
            extra=extra,
        )
        self.write_log(result)
        return result

    # ── Helper methods ────────────────────────────────────────────────

    def _compute_live_position_size(self, edge: float, fair_prob: float,
                                    venue_prob: float = 0.0,
                                    confidence: float = 1.0,
                                    game_progress: float = 0.0,
                                    spread_cents: float = 0.0) -> float:
        """Compute position size with live-specific adjustments.

        Applies confidence scaling (lower confidence → smaller size),
        game-progress scaling (later in game → more aggressive), and
        spread-cost deduction (wide spreads reduce effective edge).
        """
        kelly = self._kelly_fraction(edge, fair_prob, venue_prob)

        # ── Confidence scaling: scale Kelly by FV confidence ──────────
        # confidence 1.0 → full Kelly, 0.6 → 60% Kelly
        kelly *= confidence

        # ── Game-progress bonus: later game = less variance ───────────
        # +0% at game start, +20% at game end (max)
        kelly *= (1.0 + game_progress * 0.20)

        # ── Spread cost deduction: wide spreads eat into edge ─────────
        # Each cent of spread is ~1% round-trip cost; halve for one-way
        if spread_cents > 0:
            spread_cost_pct = (spread_cents / 2.0) / 100.0
            # Reduce Kelly proportional to how much spread eats edge
            if edge > 0:
                spread_drag = min(0.5, spread_cost_pct / edge)
                kelly *= (1.0 - spread_drag)

        return self.compute_position_size(kelly)

    def _kelly_fraction(self, edge: float, fair_prob: float,
                        venue_prob: float = 0.0) -> float:
        """Compute Kelly fraction for a Kalshi binary contract.

        For a contract bought at ``venue_prob`` that pays $1 on win::

            f* = (fair_prob - venue_prob) / (1 - venue_prob)

        This is the standard Kelly criterion for a binary bet where
        the payoff odds are ``b = (1 - venue_prob) / venue_prob``.

        Falls back to the edge-based approximation when ``venue_prob``
        is not supplied (backward compatibility).
        """
        if fair_prob <= 0 or fair_prob >= 1:
            return 0.0
        if venue_prob > 0.01 and venue_prob < 0.99:
            # Correct Kelly for binary bet at price venue_prob
            kelly = (fair_prob - venue_prob) / (1.0 - venue_prob)
        else:
            # Fallback: approximation from edge
            kelly = edge * fair_prob / (1.0 - fair_prob)
        return max(0.0, min(kelly, 0.05))

    def _place_order(
        self, market_id: str, side: str, size: float, limit_price: float,
    ) -> Optional[str]:
        if self.mode == "paper":
            import hashlib
            now_str = self._now_fn().isoformat()
            fake_id = hashlib.sha256(
                f"paper|{market_id}|{side}|{now_str}".encode()
            ).hexdigest()[:16]
            logger.info(
                "P-014 [PAPER]: %s %s on %s — size=$%.2f, limit=%.4f, id=%s",
                side, market_id, "kalshi", size, limit_price, fake_id,
            )
            return f"paper-{fake_id}"

        if self._kalshi_client is None:
            logger.error("P-014: no Kalshi client for live order placement")
            return None

        try:
            contracts = max(1, int(size / limit_price))
            result = self._kalshi_client.place_order(
                ticker=market_id,
                side=side.lower(),
                count=contracts,
                yes_price=int(limit_price * 100) if side == "YES" else None,
                no_price=int((1 - limit_price) * 100) if side == "NO" else None,
            )
            return result.get("order_id") if result else None
        except Exception as exc:
            logger.error("P-014: order placement failed: %s", exc)
            return None

    def _build_signal_result(
        self,
        snapshot: LiveOddsSnapshot,
        game_state: Optional[GameState],
        kalshi_match: KalshiMarketMatch,
        side: str,
        fair_prob: float,
        venue_prob: float,
        edge: float,
        now_str: str,
        fv_result: LiveFairValueResult,
        action: str = "SKIPPED_RISK",
        skip_reason: Optional[str] = None,
        signal_source: str = "consensus",
    ) -> ScanResult:
        fingerprint = self.make_fingerprint(kalshi_match.ticker, side)
        kelly = self._kelly_fraction(edge, fair_prob, venue_prob)
        extra = self._build_extra(snapshot, game_state)
        extra["signal_source"] = signal_source
        extra["kalshi_ticker"] = kalshi_match.ticker

        return ScanResult(
            fingerprint=fingerprint,
            timestamp_utc=now_str,
            pod_id=self.pod_id,
            pod_name=self.pod_name,
            mode=self.mode,
            venue="kalshi",
            market_type=f"live_{snapshot.sport}",
            event=f"{snapshot.away_team} @ {snapshot.home_team} (LIVE)",
            market_id=kalshi_match.ticker,
            side=side,
            fair_prob=fair_prob,
            venue_prob=venue_prob,
            edge_pct=min(edge, 1.0),  # Cap display edge at 100%
            ev=0.0,
            kelly_fraction=kelly,
            position_size_usd=0.0,
            action=action,
            skip_reason=skip_reason,
            extra=extra,
        )

    def _build_data_result(
        self,
        snapshot: LiveOddsSnapshot,
        game_state: Optional[GameState],
        fv_result: LiveFairValueResult,
        now_str: str,
    ) -> Optional[ScanResult]:
        """Log data even when no Kalshi market is found."""
        fingerprint = self.make_fingerprint(snapshot.game_id, "DATA")
        if self.is_duplicate(fingerprint):
            return None
        self.mark_seen(fingerprint)

        extra = self._build_extra(snapshot, game_state)
        extra["fair_home"] = fv_result.home_prob
        extra["fair_away"] = fv_result.away_prob
        extra["fv_method"] = fv_result.method
        extra["fv_confidence"] = fv_result.confidence
        extra["num_books"] = fv_result.num_books

        result = ScanResult(
            fingerprint=fingerprint,
            timestamp_utc=now_str,
            pod_id=self.pod_id,
            pod_name=self.pod_name,
            mode=self.mode,
            venue="kalshi",
            market_type=f"live_{snapshot.sport}",
            event=f"{snapshot.away_team} @ {snapshot.home_team} (LIVE)",
            market_id=snapshot.game_id,
            side="DATA",
            fair_prob=fv_result.home_prob,
            venue_prob=0.0,
            edge_pct=0.0,
            ev=0.0,
            kelly_fraction=0.0,
            position_size_usd=0.0,
            action="DATA_COLLECTION",
            skip_reason="no_kalshi_market",
            extra=extra,
        )
        self.write_log(result)
        return result

    def _build_extra(
        self,
        snapshot: LiveOddsSnapshot,
        game_state: Optional[GameState],
    ) -> Dict[str, Any]:
        extra: Dict[str, Any] = {
            "sport": snapshot.sport,
            "home_team": snapshot.home_team,
            "away_team": snapshot.away_team,
            "num_books": snapshot.num_books,
            "poll_timestamp": snapshot.poll_timestamp,
            "game_time": snapshot.commence_time,
            "game_id": snapshot.game_id,
        }
        pinnacle = snapshot.get_pinnacle_line()
        if pinnacle:
            extra["pinnacle_home_odds"] = pinnacle.home_price
            extra["pinnacle_away_odds"] = pinnacle.away_price
        if game_state:
            extra["home_score"] = game_state.home_score
            extra["away_score"] = game_state.away_score
            extra["game_phase"] = game_state.phase.value
            extra["game_progress"] = game_state.game_progress_pct()
            if game_state.period:
                extra["period"] = game_state.period
        return extra
