"""
src/pods/cross_venue_arb.py
───────────────────────────
P-002: Kalshi-Polymarket Cross-Venue Arb pod.

Scans for pricing discrepancies on the same event across Kalshi and
Polymarket, then places dual-leg trades to lock in risk-free profit.

Dual-leg arb strategy:
  1. Fetch active sports markets from both venues
  2. Cross-match via CrossVenueMatcher (fuzzy + both-teams + settlement)
  3. Get live prices on both venues
  4. Compute fee-adjusted spread (Kalshi 7% profit fee, Polymarket 0%)
  5. If net spread > min_arb_pct → place on BOTH venues:
     - Buy cheaper side on one venue
     - Buy opposite side on other venue
     - Guaranteed profit = spread - fees regardless of outcome

Fee model:
  Polymarket: 0% fees on all trades
  Kalshi:     7% fee on PROFIT only (not on losses)

  For a dual-leg arb, one side wins and one side loses.
  The Kalshi leg's fee is only charged if it's the winning leg.
  Net profit = (payout - cost) across both legs, minus Kalshi fee on
  the Kalshi leg's profit (if any).
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.base_pod import BasePod, ScanResult
from src.cross_venue_matcher import (
    CrossVenueMatcher,
    CrossVenueMatch,
    _SPORT_CITY_TO_TEAM,
    _detect_sport_from_ticker,
)

from src.pod_registry import register_pod

logger = logging.getLogger(__name__)

# Kalshi charges 7% on profit (not on the principal or on losses)
KALSHI_PROFIT_FEE = 0.07
# Polymarket has 0% trading fees
POLYMARKET_FEE = 0.0


@dataclass
class ArbOpportunity:
    """A fee-adjusted arbitrage opportunity across two venues."""
    side: str           # "YES" or "NO" — the Kalshi side
    kalshi_price: float
    poly_price: float
    gross_spread: float
    net_spread: float   # after worst-case Kalshi fee
    buy_venue: str      # venue with lower price for this side
    sell_venue: str     # venue with higher price (opposite side)


@register_pod("P-002")
class CrossVenueArbPod(BasePod):
    """P-002: Kalshi-Polymarket Dual-Leg Cross-Venue Arb.

    Scan cycle:
      1. Fetch Kalshi sports markets (filtered by sport ticker prefixes)
      2. Fetch Polymarket sports markets (via Gamma /events API)
      3. Cross-match via CrossVenueMatcher (fuzzy + both-teams validation)
      4. For each match, get live prices from both venues
      5. Compute fee-adjusted spread accounting for Kalshi's 7% profit fee
      6. If net spread > min_arb_pct → place BOTH legs (dual-leg arb)
      7. Log both legs as linked PLACED entries
    """

    # Map Odds API sport keys → Polymarket league names for series_id lookup
    _SPORT_LEAGUE_NAME: Dict[str, str] = {
        "americanfootball_nfl":  "NFL",
        "basketball_nba":        "NBA",
        "basketball_ncaab":      "NCAAB",
        "basketball_ncaaw":      "NCAAW",
        "baseball_mlb":          "MLB",
        "icehockey_nhl":         "NHL",
        "tennis_atp":            "ATP",
        "tennis_wta":            "WTA",
    }

    # NOTE: tag_id filtering removed — the Gamma API silently returns 0
    # results when tag_id is passed.  series_id already filters to the
    # right league; we pick the moneyline market (first market) per event.

    def __init__(
        self,
        kalshi_client,
        polymarket_client,
        cross_venue_matcher: CrossVenueMatcher,
        edge_calculator,
        risk_manager,
        sports: Optional[List[str]] = None,
        min_arb_pct: float = 0.02,
        min_edge_pct: float = 0.01,
        trade_log_path: Optional[Path] = None,
        mode: str = "paper",
        _now_fn: Optional[Callable] = None,
        aggregate_risk=None,
        trade_store=None,
        min_kelly_fraction: Optional[float] = None,
    ):
        super().__init__(
            pod_id="P-002",
            pod_name="Kalshi-Polymarket Cross-Venue Arb",
            edge_calculator=edge_calculator,
            risk_manager=risk_manager,
            trade_log_path=trade_log_path or Path("data/trade_logs/trade_log.jsonl"),
            mode=mode,
            _now_fn=_now_fn,
            aggregate_risk=aggregate_risk,
            trade_store=trade_store,
            min_kelly_fraction=min_kelly_fraction,
        )
        self.kalshi_client = kalshi_client
        self.polymarket_client = polymarket_client
        self.cross_venue_matcher = cross_venue_matcher
        self.sports = sports or list(self._SPORT_LEAGUE_NAME.keys())
        self.min_arb_pct = min_arb_pct
        self.min_edge_pct = min_edge_pct

    def scan_once(self) -> List[ScanResult]:
        results: List[ScanResult] = []

        # 1. Fetch Kalshi sports markets
        try:
            kalshi_markets = self._fetch_kalshi_markets()
        except Exception as e:
            logger.error("P-002: Kalshi fetch failed: %s", e)
            return []

        if not kalshi_markets:
            logger.info("P-002: no Kalshi sports markets found")
            return []

        # 2. Fetch Polymarket sports markets
        try:
            poly_markets = self._fetch_polymarket_sports_markets()
        except Exception as e:
            logger.error("P-002: Polymarket fetch failed: %s", e)
            return []

        if not poly_markets:
            logger.info("P-002: no Polymarket sports markets found")
            return []

        logger.info(
            "P-002: scanning %d Kalshi × %d Polymarket markets",
            len(kalshi_markets), len(poly_markets),
        )

        # 3. Cross-match
        matches = self.cross_venue_matcher.match_all(kalshi_markets, poly_markets)
        logger.info("P-002: %d cross-venue matches found", len(matches))

        # 4. Evaluate each match
        placed_events: set = set()

        # Diagnostic counters
        _has_k_price = sum(1 for m in matches if m.kalshi_yes_price is not None)
        _skip_no_kalshi = 0
        _skip_no_poly = 0
        _skip_range = 0
        _skip_no_arb = 0
        _evaluated = 0

        logger.info(
            "P-002: price availability — %d/%d matches have Kalshi price from matcher",
            _has_k_price, len(matches),
        )

        for match in matches:
            try:
                match_results = self._evaluate_match(match, placed_events)
                results.extend(match_results)
            except Exception as e:
                logger.error(
                    "P-002: Error evaluating %s: %s",
                    match.kalshi_ticker, e,
                )

        placed = [r for r in results if r.action == "PLACED"]
        skipped = [r for r in results if r.action == "SKIPPED_EDGE"]
        logger.info(
            "P-002: cycle complete — %d matches, %d legs placed, "
            "%d skipped (sub-threshold), %d total results",
            len(matches), len(placed), len(skipped), len(results),
        )
        return results

    def _evaluate_match(
        self, match: CrossVenueMatch, placed_events: set
    ) -> List[ScanResult]:
        """Evaluate a single cross-venue match for dual-leg arb."""
        results: List[ScanResult] = []

        # Per-cycle dedup by event (Kalshi ticker)
        if match.kalshi_ticker in placed_events:
            return []

        # Get live prices from both venues
        kalshi_yes = match.kalshi_yes_price
        if kalshi_yes is None:
            kalshi_yes = self._get_kalshi_live_price(match.kalshi_ticker)
            if kalshi_yes is None:
                logger.debug("P-002: SKIP %s — no Kalshi price", match.kalshi_ticker)
                return []

        poly_yes_raw = self._get_poly_live_price(match.polymarket_token_yes)
        if poly_yes_raw is None:
            logger.debug(
                "P-002: SKIP %s — no Polymarket price", match.kalshi_ticker,
            )
            return []

        # ── Side alignment ───────────────────────────────────────
        # Kalshi YES = kalshi_yes_team wins.  Polymarket token_yes
        # corresponds to the first team listed in the question.
        # If they refer to different teams, flip poly_yes so both
        # probabilities answer the same question: "Does the Kalshi
        # YES team win?"
        poly_yes = poly_yes_raw
        if match.kalshi_yes_team:
            sides_aligned = self._sides_aligned(
                match.kalshi_yes_team, match.polymarket_question,
            )
            if not sides_aligned:
                poly_yes = 1.0 - poly_yes_raw
                logger.debug(
                    "P-002: flipped poly_yes for %s (kalshi_yes=%s, "
                    "poly_q=%s) %.3f → %.3f",
                    match.kalshi_ticker, match.kalshi_yes_team,
                    match.polymarket_question[:40],
                    poly_yes_raw, poly_yes,
                )

        if poly_yes <= 0.03 or poly_yes >= 0.97:
            logger.debug(
                "P-002: SKIP %s — poly_yes=%.4f out of range",
                match.kalshi_ticker, poly_yes,
            )
            return []
        if kalshi_yes <= 0.03 or kalshi_yes >= 0.97:
            logger.debug(
                "P-002: SKIP %s — kalshi_yes=%.4f out of range",
                match.kalshi_ticker, kalshi_yes,
            )
            return []

        kalshi_no = 1.0 - kalshi_yes
        poly_no = 1.0 - poly_yes

        # ── Find the best arb opportunity (YES or NO side) ─────────
        arb = self._find_best_arb(kalshi_yes, kalshi_no, poly_yes, poly_no)

        if arb is None:
            # Log SKIP for diagnostic purposes
            gross = abs(kalshi_yes - poly_yes)
            if gross >= 0.005:  # Only log if there's a notable spread
                fp = self.make_fingerprint(
                    match.kalshi_ticker + "|" + match.polymarket_condition_id, "SKIP"
                )
                result = ScanResult(
                    fingerprint=fp,
                    timestamp_utc=self._now_fn().isoformat(),
                    pod_id=self.pod_id,
                    pod_name=self.pod_name,
                    mode=self.mode,
                    venue="multi",
                    market_type="cross_venue_arb",
                    event=match.kalshi_title,
                    market_id=match.kalshi_ticker,
                    side="SKIP",
                    fair_prob=0.0,
                    venue_prob=0.0,
                    edge_pct=gross,
                    ev=0.0,
                    kelly_fraction=0.0,
                    position_size_usd=0.0,
                    action="SKIPPED_EDGE",
                    skip_reason=f"Net spread below {self.min_arb_pct:.1%} after fees",
                    extra={
                        "kalshi_yes": kalshi_yes,
                        "poly_yes": poly_yes,
                        "gross_spread": gross,
                        "fuzzy_score": match.fuzzy_score,
                        "settlement_aligned": match.settlement_aligned,
                        "teams_validated": match.teams_validated,
                    },
                )
                self.write_log(result)
                results.append(result)
            return results

        # ── Cross-cycle dedup by Polymarket condition ──────────────
        # Prevents re-betting the same Polymarket event across cycles.
        # (The hourly fingerprint resets every hour, allowing duplicates.)
        poly_cid = match.polymarket_condition_id
        if poly_cid and self.has_open_position(poly_cid):
            logger.debug(
                "P-002: skipping %s — already have open position on %s",
                match.kalshi_ticker, poly_cid[:16],
            )
            return results

        # ── Dedup check (within-hour fingerprint) ─────────────────
        event_key = match.kalshi_ticker + "|" + poly_cid
        fp_buy = self.make_fingerprint(event_key, arb.side + "_BUY")
        if self.is_duplicate(fp_buy):
            return results
        self.mark_seen(fp_buy)

        # ── Position sizing ────────────────────────────────────────
        # For dual-leg arb, use net spread as the "edge" for Kelly
        # But cap at a reasonable size since this is risk-free
        kelly_frac = min(arb.net_spread * 2.0, 0.10)  # aggressive since risk-free
        position_size = self.compute_position_size(kelly_frac)
        if position_size <= 0:
            return results

        # Aggregate risk check.  Both legs are reserved under one
        # synthetic key holding the combined size — the two real legs
        # register separately in post_cycle, and this hold is swept at
        # the cycle boundary rather than converted.
        if not self.check_aggregate_risk(
            "multi", position_size * 2, market_id=f"{event_key}:arb",
        ):
            return results

        # ── Place BOTH legs ────────────────────────────────────────
        # Determine which side goes where
        if arb.buy_venue == "kalshi":
            # Buy arb.side on Kalshi (cheaper), buy opposite on Polymarket
            kalshi_side = arb.side
            kalshi_buy_price = arb.kalshi_price
            poly_side = "NO" if arb.side == "YES" else "YES"
            poly_buy_price = 1.0 - arb.poly_price
        else:
            # Buy arb.side on Polymarket (cheaper), buy opposite on Kalshi
            poly_side = arb.side
            poly_buy_price = arb.poly_price
            kalshi_side = "NO" if arb.side == "YES" else "YES"
            kalshi_buy_price = 1.0 - arb.kalshi_price

        # Detect sport for settler resolution (Odds API needs it)
        detected_sport = _detect_sport_from_ticker(match.kalshi_ticker) or ""

        # Leg 1: Kalshi
        kalshi_order = self._place_kalshi(
            match, kalshi_side, kalshi_buy_price, position_size
        )
        fp_kalshi = self.make_fingerprint(event_key, "KALSHI_" + kalshi_side)
        self.mark_seen(fp_kalshi)
        leg1 = ScanResult(
            fingerprint=fp_kalshi,
            timestamp_utc=self._now_fn().isoformat(),
            pod_id=self.pod_id,
            pod_name=self.pod_name,
            mode=self.mode,
            venue="kalshi",
            market_type="cross_venue_arb",
            event=match.kalshi_title,
            market_id=match.kalshi_ticker,
            side=kalshi_side,
            fair_prob=1.0 - kalshi_buy_price,  # opposite venue's price as "fair"
            venue_prob=kalshi_buy_price,
            edge_pct=arb.net_spread,
            ev=arb.net_spread,
            kelly_fraction=kelly_frac,
            position_size_usd=position_size,
            action="PLACED",
            order_id=getattr(kalshi_order, "order_id", None),
            fill_price=getattr(kalshi_order, "fill_price", None),
            extra={
                "arb_type": "dual_leg",
                "leg": "kalshi",
                "linked_leg": "polymarket",
                "sport": detected_sport,
                "kalshi_yes": kalshi_yes,
                "poly_yes": poly_yes,
                "gross_spread": arb.gross_spread,
                "net_spread": arb.net_spread,
                "buy_venue": arb.buy_venue,
                "fuzzy_score": match.fuzzy_score,
                "settlement_aligned": match.settlement_aligned,
                "teams_validated": match.teams_validated,
            },
        )
        self.write_log(leg1)
        results.append(leg1)

        # Leg 2: Polymarket
        poly_order = self._place_polymarket(
            match, poly_side, poly_buy_price, position_size
        )
        fp_poly = self.make_fingerprint(event_key, "POLY_" + poly_side)
        self.mark_seen(fp_poly)
        leg2 = ScanResult(
            fingerprint=fp_poly,
            timestamp_utc=self._now_fn().isoformat(),
            pod_id=self.pod_id,
            pod_name=self.pod_name,
            mode=self.mode,
            venue="polymarket",
            market_type="cross_venue_arb",
            event=match.kalshi_title,
            market_id=match.polymarket_condition_id,
            side=poly_side,
            fair_prob=1.0 - poly_buy_price,
            venue_prob=poly_buy_price,
            edge_pct=arb.net_spread,
            ev=arb.net_spread,
            kelly_fraction=kelly_frac,
            position_size_usd=position_size,
            action="PLACED",
            order_id=getattr(poly_order, "order_id", None),
            fill_price=getattr(poly_order, "fill_price", None),
            extra={
                "arb_type": "dual_leg",
                "leg": "polymarket",
                "linked_leg": "kalshi",
                "sport": detected_sport,
                "kalshi_yes": kalshi_yes,
                "poly_yes": poly_yes,
                "gross_spread": arb.gross_spread,
                "net_spread": arb.net_spread,
                "buy_venue": arb.buy_venue,
                "fuzzy_score": match.fuzzy_score,
                "settlement_aligned": match.settlement_aligned,
                "teams_validated": match.teams_validated,
            },
        )
        self.write_log(leg2)
        results.append(leg2)

        # Mark event as placed this cycle
        placed_events.add(match.kalshi_ticker)

        # Track open position by Polymarket condition ID (cross-cycle dedup)
        if match.polymarket_condition_id:
            self.mark_position_open(match.polymarket_condition_id)

        logger.info(
            "P-002: DUAL_LEG_PLACED %s | Kalshi %s@%.2f + Poly %s@%.2f | "
            "gross=%.2f%% net=%.2f%% | $%.2f per leg",
            match.kalshi_title, kalshi_side, kalshi_buy_price,
            poly_side, poly_buy_price,
            arb.gross_spread * 100, arb.net_spread * 100, position_size,
        )

        return results

    # ── Fee-adjusted arb calculation ───────────────────────────────

    def _find_best_arb(
        self,
        kalshi_yes: float,
        kalshi_no: float,
        poly_yes: float,
        poly_no: float,
    ) -> Optional[ArbOpportunity]:
        """Find the best fee-adjusted arb opportunity.

        Evaluates both YES and NO sides. For each side, computes the
        guaranteed profit after accounting for Kalshi's 7% profit fee.

        Dual-leg arb mechanics:
          Buy YES on venue A at price P_a, buy NO on venue B at price P_b.
          Total cost = P_a + P_b per contract.
          Payout = $1 regardless of outcome.
          Gross profit = 1 - P_a - P_b.
          If the Kalshi leg wins, Kalshi takes 7% of that leg's profit.

        We evaluate both configurations and pick the most profitable.
        """
        best: Optional[ArbOpportunity] = None

        # Config 1: Buy YES on Kalshi, buy NO on Polymarket
        # Config 2: Buy NO on Kalshi, buy YES on Polymarket
        configs = [
            ("YES", kalshi_yes, poly_no, "kalshi", "polymarket"),
            ("NO", kalshi_no, poly_yes, "kalshi", "polymarket"),
            ("YES", poly_yes, kalshi_no, "polymarket", "kalshi"),
            ("NO", poly_no, kalshi_yes, "polymarket", "kalshi"),
        ]

        for side, buy_price, hedge_price, buy_venue, hedge_venue in configs:
            total_cost = buy_price + hedge_price
            if total_cost >= 1.0:
                continue  # No arb exists

            gross_spread = 1.0 - total_cost

            # Compute worst-case fee:
            # Kalshi charges 7% on profit of the winning leg.
            # Worst case: the Kalshi leg wins with maximum profit.
            if buy_venue == "kalshi":
                # Kalshi leg: bought at buy_price, pays out $1 if wins
                kalshi_leg_profit = 1.0 - buy_price
                kalshi_fee = kalshi_leg_profit * KALSHI_PROFIT_FEE
            else:
                # Kalshi leg is the hedge: bought at hedge_price
                kalshi_leg_profit = 1.0 - hedge_price
                kalshi_fee = kalshi_leg_profit * KALSHI_PROFIT_FEE

            net_spread = gross_spread - kalshi_fee

            if net_spread < self.min_arb_pct:
                continue

            # Determine which side is cheaper on which venue for the primary
            if best is None or net_spread > best.net_spread:
                # For the ArbOpportunity, record the PRIMARY side info
                if side == "YES":
                    k_price = kalshi_yes
                    p_price = poly_yes
                else:
                    k_price = kalshi_no
                    p_price = poly_no

                best = ArbOpportunity(
                    side=side,
                    kalshi_price=k_price,
                    poly_price=p_price,
                    gross_spread=gross_spread,
                    net_spread=net_spread,
                    buy_venue=buy_venue,
                    sell_venue=hedge_venue,
                )

        return best

    # ── Market fetching ────────────────────────────────────────────

    def _fetch_kalshi_markets(self) -> List[Dict[str, Any]]:
        """Fetch active Kalshi sports markets using series_ticker filtering.

        Instead of fetching ALL open markets and filtering client-side
        (which required many paginated calls and triggered 429 rate limits),
        we now query each known sport series_ticker directly.  This reduces
        API calls from dozens of pages to ~10 targeted queries.
        """
        if not hasattr(self.kalshi_client, "list_markets"):
            logger.debug("P-002: Kalshi client has no list_markets method")
            return []

        from src.cross_venue_matcher import _SPORT_TICKER_PREFIXES

        all_sport: List[Dict[str, Any]] = []

        # Collect all unique series ticker prefixes across sports
        series_tickers = []
        for prefixes in _SPORT_TICKER_PREFIXES.values():
            series_tickers.extend(prefixes)

        for series_ticker in series_tickers:
            cursor: Optional[str] = None
            while True:
                try:
                    resp = self.kalshi_client.list_markets(
                        status="open", limit=200, cursor=cursor,
                        series_ticker=series_ticker,
                    )
                except Exception as e:
                    logger.error(
                        "P-002: Kalshi list_markets error for %s: %s",
                        series_ticker, e,
                    )
                    break

                page = resp.get("markets") or []
                all_sport.extend(page)

                cursor = resp.get("cursor") or None
                if not cursor or len(page) < 200:
                    break

        # Kalshi lists TWO markets per game (one per team, e.g. -DET and
        # -AZ).  Both share the same title but YES means a different team.
        # Keep only one market per event_ticker to avoid double-matching the
        # same Polymarket market and producing phantom spreads.
        seen_events: dict = {}
        for m in all_sport:
            evt = m.get("event_ticker", m.get("ticker", ""))
            if evt not in seen_events:
                seen_events[evt] = m

        deduped = list(seen_events.values())
        logger.info(
            "P-002: fetched %d sport markets from Kalshi "
            "(%d series queried, %d after event dedup)",
            len(all_sport), len(series_tickers), len(deduped),
        )
        return deduped

    def _fetch_polymarket_sports_markets(self) -> List:
        """Fetch active Polymarket sports markets via Gamma /events API.

        Uses series_id to filter by league.  No tag_id filter — the Gamma
        API silently returns 0 results when tag_id is passed.

        Picks the moneyline market from each event (first market whose
        question matches the event title, i.e. "Team A vs. Team B").
        """
        import json as _json
        from src.polymarket_client import PolymarketMarket

        all_markets: List[PolymarketMarket] = []

        for sport in self.sports:
            league_name = self._SPORT_LEAGUE_NAME.get(sport)
            if not league_name:
                continue

            sport_count = 0

            try:
                series_id = self.polymarket_client.find_series_id(league_name)
                if not series_id:
                    logger.debug("P-002: no series_id for %s, skipping", sport)
                    continue

                cursor = None
                for _ in range(5):  # max 5 pages per sport
                    resp = self.polymarket_client.get_sport_events(
                        series_id=series_id,
                        next_cursor=cursor,
                    )
                    events = resp.get("data", [])
                    if not events:
                        break

                    for event in events:
                        markets = event.get("markets", [])
                        title = event.get("title", "")
                        if not markets or not title:
                            continue

                        # Find the moneyline market: its question == event title
                        # and it has exactly 2 outcomes (team names).
                        mkt = self._find_moneyline_market(markets, title)
                        if mkt is None:
                            continue

                        # Parse clobTokenIds — may be JSON string or list
                        raw_tokens = mkt.get("clobTokenIds") or []
                        if isinstance(raw_tokens, str):
                            try:
                                raw_tokens = _json.loads(raw_tokens)
                            except (_json.JSONDecodeError, ValueError):
                                raw_tokens = []
                        tokens = raw_tokens if isinstance(raw_tokens, list) else []

                        if len(tokens) < 2:
                            continue

                        # Parse outcome prices — may be JSON string or list
                        outcome_prices = mkt.get("outcomePrices")
                        yes_price = None
                        if isinstance(outcome_prices, str):
                            try:
                                outcome_prices = _json.loads(outcome_prices)
                            except (_json.JSONDecodeError, ValueError):
                                outcome_prices = None
                        if outcome_prices and isinstance(outcome_prices, list):
                            try:
                                yes_price = float(outcome_prices[0])
                            except (ValueError, TypeError, IndexError):
                                pass

                        market_obj = PolymarketMarket(
                            condition_id=mkt.get("conditionId", mkt.get("condition_id", "")),
                            token_id_yes=tokens[0],
                            token_id_no=tokens[1],
                            question=title,  # Use event title for matching
                            slug=event.get("slug", ""),
                            active=True,
                            closed=False,
                            end_date=event.get("endDate"),
                            game_start_time=event.get("startDate"),
                            yes_price=yes_price,
                        )
                        all_markets.append(market_obj)
                        sport_count += 1

                    cursor = resp.get("next_cursor")
                    if not cursor:
                        break

                logger.info(
                    "P-002: sport=%s league=%s moneyline_markets=%d",
                    sport, league_name, sport_count,
                )

            except Exception as exc:
                logger.warning("P-002: failed to fetch %s markets: %s", sport, exc)

        logger.info("P-002: total Polymarket sports markets: %d", len(all_markets))
        return all_markets

    @staticmethod
    def _find_moneyline_market(
        markets: List[Dict[str, Any]], event_title: str
    ) -> Optional[Dict[str, Any]]:
        """Find the moneyline / game-winner market from an event's market list.

        The moneyline market's question matches the event title
        (e.g. both are "Mavericks vs. Bucks") and has exactly 2 outcomes.
        """
        import json as _json
        for mkt in markets:
            q = mkt.get("question", "")
            if q == event_title:
                # Verify 2 outcomes
                outcomes = mkt.get("outcomes")
                if isinstance(outcomes, str):
                    try:
                        outcomes = _json.loads(outcomes)
                    except (_json.JSONDecodeError, ValueError):
                        outcomes = []
                if isinstance(outcomes, list) and len(outcomes) == 2:
                    return mkt
        # Fallback: first market with exactly 2 outcomes
        for mkt in markets:
            outcomes = mkt.get("outcomes")
            if isinstance(outcomes, str):
                try:
                    outcomes = _json.loads(outcomes)
                except (_json.JSONDecodeError, ValueError):
                    outcomes = []
            raw_tokens = mkt.get("clobTokenIds") or []
            if isinstance(raw_tokens, str):
                try:
                    raw_tokens = _json.loads(raw_tokens)
                except (_json.JSONDecodeError, ValueError):
                    raw_tokens = []
            if isinstance(outcomes, list) and len(outcomes) == 2 and len(raw_tokens) == 2:
                return mkt
        return None

    # ── Side alignment ─────────────────────────────────────────────

    def _sides_aligned(
        self, kalshi_yes_team: str, polymarket_question: str,
    ) -> bool:
        """Check whether the Kalshi YES team is the *first* team in the
        Polymarket question.

        Kalshi ``yes_sub_title`` is typically a city name (e.g. "Detroit")
        while Polymarket questions use team nicknames
        (e.g. "Tigers vs. Diamondbacks").

        Algorithm:
          1. Normalise kalshi_yes_team to lowercase.
          2. Look it up in ``_SPORT_CITY_TO_TEAM`` (across all sports) to
             get possible team nicknames.
          3. Split the Polymarket question at "vs" / "beat" / "win over"
             to get the *first* team token.
          4. If the Kalshi city name OR any resolved team nickname appears
             in the first-team portion, sides are aligned.  Otherwise
             they are flipped.

        Returns True if aligned (no flip needed), False if flipped.
        Falls back to True (assume aligned) if we cannot determine.
        """
        if not kalshi_yes_team or not polymarket_question:
            return True  # cannot determine → assume aligned

        k_lower = kalshi_yes_team.strip().lower()
        q_lower = polymarket_question.strip().lower().rstrip("?").strip()

        # Build set of names that represent the Kalshi YES team.
        # Kalshi yes_sub_title can be:
        #   - City name: "Detroit", "Boston"
        #   - Abbrev + team: "EDM Oilers", "PHI Flyers", "TB Lightning"
        #   - Full team name: "Golden Knights"
        kalshi_names: set = {k_lower}
        for sport_map in _SPORT_CITY_TO_TEAM.values():
            if k_lower in sport_map:
                kalshi_names.add(sport_map[k_lower].lower())

        # Also add individual words (>2 chars) from the yes_sub_title
        # to catch "EDM Oilers" → "oilers", "PHI Flyers" → "flyers"
        for word in k_lower.split():
            if len(word) > 2:
                kalshi_names.add(word)
                # Also look up each word in the city→team maps
                for sport_map in _SPORT_CITY_TO_TEAM.values():
                    if word in sport_map:
                        kalshi_names.add(sport_map[word].lower())

        # Split Polymarket question into first-team / second-team halves
        # Common patterns:
        #   "Tigers vs. Diamondbacks"
        #   "Will the Tigers beat the Diamondbacks?"
        #   "Tigers vs Diamondbacks"
        # Remove leading "Will the " etc.
        q_clean = re.sub(r"^(will|do|does|can)\s+(the\s+)?", "", q_lower)

        # Split on "vs.", "vs", "beat", "defeat", "win over", "win against"
        parts = re.split(
            r"\s+(?:vs\.?|beat|defeat|win\s+over|win\s+against)\s+",
            q_clean,
            maxsplit=1,
        )

        if len(parts) < 2:
            # Couldn't split → can't determine alignment
            logger.debug(
                "P-002: _sides_aligned: could not split question '%s'",
                polymarket_question[:60],
            )
            return True  # assume aligned

        first_team_part = parts[0].strip()
        second_team_part = parts[1].strip()

        # Check if any Kalshi name appears in the first-team portion
        for name in kalshi_names:
            if name in first_team_part:
                return True   # aligned — Kalshi YES = Poly first team

        # Check if any Kalshi name appears in the second-team portion
        for name in kalshi_names:
            if name in second_team_part:
                return False  # flipped — Kalshi YES = Poly second team

        # Fallback: check using "the" stripped team name fragments
        # e.g. "the diamondbacks" → "diamondbacks"
        first_clean = re.sub(r"^the\s+", "", first_team_part)
        second_clean = re.sub(r"^the\s+", "", second_team_part)
        for name in kalshi_names:
            if name in first_clean:
                return True
            if name in second_clean:
                return False

        logger.debug(
            "P-002: _sides_aligned: no match for kalshi_yes=%s "
            "in poly_q='%s' (names=%s)",
            kalshi_yes_team, polymarket_question[:60], kalshi_names,
        )
        return True  # assume aligned if we genuinely can't tell

    # ── Price fetching ─────────────────────────────────────────────

    def _get_kalshi_live_price(self, ticker: str) -> Optional[float]:
        """Get live Kalshi midpoint price.

        The Kalshi API returns dollar-denominated fields
        (``yes_bid_dollars``, ``yes_ask_dollars``) as strings like
        ``"0.5700"``.  The response from ``get_market()`` wraps the
        market object under a ``"market"`` key.
        """
        try:
            if hasattr(self.kalshi_client, "get_market"):
                resp = self.kalshi_client.get_market(ticker)
                # get_market returns {"market": {...}}
                market = resp.get("market", resp) if isinstance(resp, dict) else resp
                # Try new dollar fields first, fall back to legacy
                yes_bid = market.get("yes_bid_dollars") or market.get("yes_bid")
                yes_ask = market.get("yes_ask_dollars") or market.get("yes_ask")
                if yes_bid is not None and yes_ask is not None:
                    bid_f = float(yes_bid)
                    ask_f = float(yes_ask)
                    # Dollar fields are 0-1; legacy cent fields are 0-99
                    if bid_f > 1.0 or ask_f > 1.0:
                        bid_f /= 100.0
                        ask_f /= 100.0
                    return (bid_f + ask_f) / 2.0
            elif hasattr(self.kalshi_client, "get_price"):
                return self.kalshi_client.get_price(ticker)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.debug("P-002: Kalshi price fetch failed for %s: %s", ticker, exc)
        except Exception as exc:
            logger.debug("P-002: Kalshi price fetch error for %s: %s", ticker, exc)
        return None

    def _get_poly_live_price(self, token_id: str) -> Optional[float]:
        """Get live Polymarket midpoint price."""
        if not token_id:
            return None
        try:
            return self.polymarket_client.get_midpoint(token_id)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.debug("P-002: Polymarket price fetch failed for %s: %s", token_id, exc)
            return None

    # ── Order placement ────────────────────────────────────────────

    def _place_kalshi(
        self, match: CrossVenueMatch, side: str,
        price: float, position_size: float,
    ):
        """Place order on Kalshi (paper fill in paper/demo mode)."""
        quantity = max(1, int(position_size / price)) if price > 0 else 1

        # Paper mode → simulate fill without hitting the API
        if self.mode == "paper":
            class _PaperResult:
                order_id = f"PAPER-K-{match.kalshi_ticker}-{side}"
                fill_price = price
            return _PaperResult()

        if hasattr(self.kalshi_client, "place_order"):
            order_payload: dict = {
                "ticker": match.kalshi_ticker,
                "action": "buy",
                "side": side.lower(),
                "type": "limit",
                "count": quantity,
            }
            if side == "YES":
                order_payload["yes_price"] = int(price * 100)
            else:
                order_payload["no_price"] = int(price * 100)
            return self.kalshi_client.place_order(order_payload)

        class _PaperResultFallback:
            order_id = f"PAPER-K-{match.kalshi_ticker}-{side}"
            fill_price = price
        return _PaperResultFallback()

    def _place_polymarket(
        self, match: CrossVenueMatch, side: str,
        price: float, position_size: float,
    ):
        """Place order on Polymarket (paper fill in paper/demo mode)."""
        quantity = max(1, int(position_size / price)) if price > 0 else 1

        # Paper mode → simulate fill without hitting the API
        if self.mode == "paper":
            class _PaperResult:
                order_id = f"PAPER-P-{match.polymarket_condition_id}-{side}"
                fill_price = price
            return _PaperResult()

        token_id = (
            match.polymarket_token_yes if side == "YES"
            else match.polymarket_token_no
        )

        if hasattr(self.polymarket_client, "place_order"):
            return self.polymarket_client.place_order(
                token_id=token_id,
                side="BUY",
                price=price,
                size=quantity,
            )

        class _PaperResultFallback:
            order_id = f"PAPER-P-{match.polymarket_condition_id}-{side}"
            fill_price = price
        return _PaperResultFallback()

    def venue_name(self) -> str:
        return "multi"

    @classmethod
    def from_config(cls, config: dict, **overrides):
        """Build P-002 from config."""
        pod_cfg = config.get("pods", {}).get("P-002", {})

        matcher = overrides.get("cross_venue_matcher") or CrossVenueMatcher.from_config(config)

        log_path = overrides.get("trade_log_path")
        if not log_path:
            log_dir = Path(config.get("paths", {}).get("pod_logs", "data/pods"))
            log_path = log_dir / "P-002.jsonl"

        env = pod_cfg.get("environment", config.get("environment", "demo"))
        mode_map = {"demo": "paper", "live": "live"}

        # Sports list: use config if specified, else default to all mapped sports
        sports = pod_cfg.get("sports")
        if not sports:
            odds_sports = config.get("odds_api", {}).get("sports", [])
            if odds_sports:
                sports = odds_sports

        return cls(
            kalshi_client=overrides.get("kalshi_client"),
            polymarket_client=overrides.get("polymarket_client") or overrides.get("polymarket"),
            cross_venue_matcher=matcher,
            edge_calculator=overrides.get("edge_calculator"),
            risk_manager=overrides.get("risk_manager"),
            sports=sports,
            min_arb_pct=pod_cfg.get("min_arb_pct", 0.02),
            min_edge_pct=pod_cfg.get("min_edge_pct", 0.01),
            trade_log_path=log_path,
            mode=mode_map.get(env, "paper"),
            _now_fn=overrides.get("_now_fn"),
            aggregate_risk=overrides.get("aggregate_risk"),
            trade_store=overrides.get("trade_store"),
            min_kelly_fraction=pod_cfg.get("min_kelly_fraction"),
        )
