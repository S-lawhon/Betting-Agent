"""
src/pods/polymarket_consensus.py
────────────────────────────────
P-006: Sportsbook-Polymarket Consensus pod.

Uses Odds API multi-book consensus (Pinnacle-weighted) as fair value,
scans Polymarket sports markets for mispricing.  Identical logic to
P-001 (Kalshi Moneyline Value) but:
  - Execution venue: Polymarket (0% fees)
  - Lower edge threshold (no Kalshi fee drag)
  - Different matching rules (Polymarket question-style naming)
  - Paper mode by default (US Polymarket access pending)
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from src.base_pod import BasePod, ScanResult
from src.polymarket_client import PolymarketClient, PolymarketMarket
from src.polymarket_matcher import PolymarketMatcher, PolymarketMatchResult

logger = logging.getLogger(__name__)


def _infer_yes_team(match: PolymarketMatchResult) -> Optional[str]:
    """Determine which team YES (outcomePrices[0]) corresponds to.

    Returns "home" or "away" (matching Odds API event perspective),
    or None if the question doesn't map to a team (e.g. ties).

    Resolution priority:
      1. ``yes_outcome_name`` — the actual team/outcome name from the market's
         outcomes array (most reliable, eliminates title-ordering bugs).
      2. "Will [team] win/beat" question pattern (Format A).
      3. First team in "Team1 vs. Team2" event title (Format B).
    """
    event = match.odds_event
    home = getattr(event, "home_team", "").lower()
    away = getattr(event, "away_team", "").lower()

    if not home or not away:
        return "home"  # Default fallback

    home_tokens = set(home.split())
    away_tokens = set(away.split())

    # ── Priority 1: use yes_outcome_name from the market data ────────────
    # This is the actual name from outcomes[0] / groupItemTitle, which maps
    # directly to outcomePrices[0].  Most reliable signal.
    yes_name = getattr(match, "yes_outcome_name", None)
    if yes_name and yes_name.lower() not in ("yes", "no"):
        name_tokens = set(yes_name.lower().split())
        home_overlap = len(name_tokens & home_tokens)
        away_overlap = len(name_tokens & away_tokens)
        if home_overlap > away_overlap:
            return "home"
        elif away_overlap > home_overlap:
            return "away"
        # If no overlap, fall through to other heuristics

    question = match.polymarket_question.lower()

    # ── Priority 2: "Will [team] win/beat" patterns (Format A) ───────────
    will_match = re.search(r"will\s+(?:the\s+)?(.+?)(?:\s+(?:win|beat|defeat))", question)
    if will_match:
        team_text = will_match.group(1).strip()
        text_tokens = set(team_text.split())

        home_overlap = len(text_tokens & home_tokens)
        away_overlap = len(text_tokens & away_tokens)

        if home_overlap > away_overlap:
            return "home"
        elif away_overlap > home_overlap:
            return "away"

    # ── Priority 3: "Team1 vs. Team2" — first team = outcomePrices[0] ────
    vs_split = re.split(r"\s+vs\.?\s+", question, maxsplit=1)
    if len(vs_split) == 2:
        first_team = vs_split[0].strip()
        # Remove tournament prefix for tennis: "BNP Paribas Open: Player1"
        if ":" in first_team:
            first_team = first_team.split(":", 1)[1].strip()
        first_tokens = set(first_team.split())

        home_overlap = len(first_tokens & home_tokens)
        away_overlap = len(first_tokens & away_tokens)

        if home_overlap > away_overlap:
            return "home"
        elif away_overlap > home_overlap:
            return "away"

    # Check if question contains one team name more prominently
    if home in question and away not in question:
        return "home"
    if away in question and home not in question:
        return "away"

    return "home"  # Default: assume YES = home


def _validate_and_correct_mapping(
    yes_team: str,
    home_prob: float,
    away_prob: float,
    mid: float,
    question: str,
) -> Optional[str]:
    """Validate the YES-side mapping using the Polymarket price as a sanity signal.

    If fair_yes_prob and mid are on opposite sides of 0.50, the mapping is
    almost certainly inverted (e.g. the system thinks YES = underdog but the
    Polymarket price is for the favourite, or vice-versa).

    In that case, try flipping the mapping.  If the flipped version also
    fails, return None to skip the trade entirely.

    Returns the corrected *yes_team* ("home" or "away"), or None if the
    mapping cannot be trusted.
    """
    fair_yes = home_prob if yes_team == "home" else away_prob
    fair_alt = away_prob if yes_team == "home" else home_prob
    flipped = "away" if yes_team == "home" else "home"

    # ── Check 1: opposite-side-of-midpoint ────────────────────────────────
    # If fair_yes > 0.50 but mid < 0.50 (or vice-versa), the mapping is
    # likely wrong.  This catches the Korda/de-Minaur class of bug where
    # the system assigns the favourite's probability to the underdog's price.
    chosen_team = yes_team
    chosen_fair = fair_yes

    yes_side_of_mid = fair_yes >= 0.50
    price_side_of_mid = mid >= 0.50

    if yes_side_of_mid != price_side_of_mid:
        # Check if flipping fixes it
        alt_side_of_mid = fair_alt >= 0.50
        if alt_side_of_mid == price_side_of_mid:
            logger.info(
                "P-006: FLIP_MAPPING event=%r mid=%.3f fair_yes=%.3f(wrong) "
                "fair_alt=%.3f(correct) %s→%s",
                question[:60], mid, fair_yes, fair_alt, yes_team, flipped,
            )
            chosen_team = flipped
            chosen_fair = fair_alt
        else:
            # Both assignments disagree with the price → skip
            logger.warning(
                "P-006: AMBIGUOUS_SKIP event=%r mid=%.3f fair_yes=%.3f "
                "fair_alt=%.3f — neither mapping aligns with Polymarket price",
                question[:60], mid, fair_yes, fair_alt,
            )
            return None

    # ── Check 2: absolute-difference sanity check ─────────────────────────
    # A difference > 20pp between the (possibly corrected) fair prob and the
    # Polymarket price suggests the mapping is still unreliable, or the
    # consensus data is stale / wrong.  Try flipping if not already flipped;
    # otherwise skip.
    raw_diff = abs(chosen_fair - mid)
    if raw_diff > 0.20:
        # If we haven't flipped yet, try flipping
        if chosen_team == yes_team:
            alt_diff = abs(fair_alt - mid)
            if alt_diff < raw_diff and alt_diff <= 0.20:
                logger.info(
                    "P-006: FLIP_MAPPING (diff) event=%r mid=%.3f fair_yes=%.3f "
                    "(diff=%.3f) fair_alt=%.3f(diff=%.3f) %s→%s",
                    question[:60], mid, fair_yes, raw_diff,
                    fair_alt, alt_diff, yes_team, flipped,
                )
                return flipped
        logger.warning(
            "P-006: SANITY_SKIP event=%r mid=%.3f fair=%.3f "
            "diff=%.3f yes_team=%s — probable mapping error or stale data",
            question[:60], mid, chosen_fair, raw_diff, chosen_team,
        )
        return None

    return chosen_team

from src.pod_registry import register_pod


@register_pod("P-006")
class PolymarketConsensusPod(BasePod):
    """P-006: Sportsbook-Polymarket Consensus.

    Scan cycle:
      1. Fetch Polymarket sports markets
      2. Fetch Odds API events + consensus probabilities
      3. Match Polymarket markets to Odds API events
      4. Evaluate edge (fee_pct=0.0 for Polymarket)
      5. Risk check + dedup
      6. Place (paper or live)
    """

    def __init__(
        self,
        polymarket_client: PolymarketClient,
        odds_client,
        matcher: PolymarketMatcher,
        edge_calculator,
        risk_manager,
        sports: List[str],
        trade_log_path: Path,
        mode: str = "paper",
        _now_fn: Optional[Callable] = None,
        aggregate_risk=None,
        trade_store=None,
        min_kelly_fraction: Optional[float] = None,
        fair_value_estimator=None,
        sport_overrides: Optional[dict] = None,
    ):
        super().__init__(
            pod_id="P-006",
            pod_name="Sportsbook-Polymarket Consensus",
            edge_calculator=edge_calculator,
            risk_manager=risk_manager,
            trade_log_path=trade_log_path,
            mode=mode,
            _now_fn=_now_fn,
            aggregate_risk=aggregate_risk,
            trade_store=trade_store,
            min_kelly_fraction=min_kelly_fraction,
        )
        self.polymarket_client = polymarket_client
        self.odds_client = odds_client
        self.matcher = matcher
        self.sports = sports
        self._fair_value_estimator = fair_value_estimator
        self._sport_overrides = sport_overrides or {}

    def scan_once(self) -> List[ScanResult]:
        results = []
        sports_with_markets = 0
        for sport in self.sports:
            sport_results = self._scan_sport(sport)
            if sport_results:
                sports_with_markets += 1
            results.extend(sport_results)

        if results:
            logger.info(
                "P-006: cycle complete — results=%d sports_active=%d/%d",
                len(results), sports_with_markets, len(self.sports),
            )
        else:
            logger.info(
                "P-006: cycle complete — no markets found across %d sports",
                len(self.sports),
            )
        return results

    def _get_sport_override(self, sport: str, key: str, default=None):
        """Look up a sport-specific override from config.

        Checks sport_overrides for the exact sport key first, then tries
        the base sport name (e.g. "tennis_atp_indian_wells" → "tennis_atp").
        """
        # Exact match
        overrides = self._sport_overrides.get(sport, {})
        if key in overrides:
            return overrides[key]

        # Try base sport (strip tournament suffix for tennis)
        # e.g. "tennis_atp_indian_wells" → "tennis_atp"
        parts = sport.split("_")
        if len(parts) >= 3:
            base_sport = "_".join(parts[:2])
            base_overrides = self._sport_overrides.get(base_sport, {})
            if key in base_overrides:
                return base_overrides[key]

        return default

    def _scan_sport(self, sport: str) -> List[ScanResult]:
        """Scan one sport category."""
        results = []

        # ── Check if sport is disabled via sport_overrides ──────────────
        if not self._get_sport_override(sport, "enabled", True):
            logger.info("P-006: sport %s is disabled via sport_overrides — skipping", sport)
            return []

        # 1. Fetch Polymarket markets
        try:
            poly_markets = self._fetch_sport_markets(sport)
        except Exception as e:
            logger.error("P-006: Polymarket fetch failed for %s: %s", sport, e)
            return []

        if not poly_markets:
            logger.debug("P-006: no sport markets found for %s — skipping", sport)
            return []

        # 2. Fetch Odds API events
        try:
            odds_events = self.odds_client.get_events(sport)
        except Exception as e:
            logger.error("P-006: Odds API fetch failed for %s: %s", sport, e)
            return []

        if not odds_events:
            logger.info("P-006: no Odds API events for %s — skipping", sport)
            return []

        # ── Game-started guard ────────────────────────────────────────
        # Filter out events whose commence_time is in the past. Pre-game
        # odds are stale once the event is live, and Polymarket markets
        # can linger after the game starts. Use Odds API commence_time
        # (reliable) rather than Polymarket startDate (unreliable).
        now = self._now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        pre_filter_count = len(odds_events)
        filtered_events = []
        for ev in odds_events:
            ct = getattr(ev, "commence_time", None)
            if ct is None:
                filtered_events.append(ev)  # keep if no time info
                continue
            ct_aware = ct if ct.tzinfo is not None else ct.replace(tzinfo=timezone.utc)
            if now < ct_aware:
                filtered_events.append(ev)
            else:
                home = getattr(ev, "home_team", "?")
                away = getattr(ev, "away_team", "?")
                logger.info(
                    "P-006: skip_game_started sport=%s event=%s vs %s commence=%s",
                    sport, home, away, ct_aware.isoformat(),
                )
        odds_events = filtered_events
        if pre_filter_count != len(odds_events):
            logger.info(
                "P-006: game-started filter removed %d/%d events for %s",
                pre_filter_count - len(odds_events), pre_filter_count, sport,
            )
        if not odds_events:
            logger.info("P-006: no upcoming Odds API events for %s after time filter — skipping", sport)
            return []

        logger.info(
            "P-006: matching sport=%s poly_markets=%d odds_events=%d",
            sport, len(poly_markets), len(odds_events),
        )
        # 3. Match
        # skip_time_check=True because Polymarket event startDate is the
        # event creation date, not the actual game date.  We already filter
        # by series_id+tag_id so all returned markets are current game bets.
        matches = self.matcher.match_all(poly_markets, odds_events, sport, skip_time_check=True)
        logger.info("P-006: matched sport=%s matches=%d", sport, len(matches))
        if not matches and poly_markets:
            # Diagnostic: log top-scoring pairs to understand fuzzy failures.
            # Import the same helpers the matcher uses.
            from src.polymarket_matcher import _extract_teams_from_question, _fuzzy_score
            event_texts = [
                "{} {}".format(
                    getattr(e, "home_team", ""), getattr(e, "away_team", "")
                ).strip()
                for e in odds_events
            ]
            diag_rows = []
            for m in poly_markets[:10]:
                match_text = _extract_teams_from_question(m.question)
                best = max(
                    (_fuzzy_score(match_text, et) for et in event_texts),
                    default=0.0,
                )
                diag_rows.append((best, m.question[:70], match_text[:50]))
            diag_rows.sort(reverse=True)
            for score, q, ext in diag_rows[:5]:
                logger.info(
                    "P-006: DIAG score=%.1f  Q=%r  extract=%r",
                    score, q, ext,
                )

        # 4. Evaluate each match
        # Track events we've already placed on this cycle (per-cycle dedup)
        placed_events = set()

        # ── Sport-specific edge thresholds ──────────────────────────────
        sport_min_edge = self._get_sport_override(sport, "min_edge_pct", None)
        sport_min_ev = self._get_sport_override(sport, "min_ev", None)

        for match in matches:
            # Get fair value — use enhanced estimator if available,
            # otherwise fall back to legacy consensus
            fair_result = None
            if self._fair_value_estimator is not None:
                try:
                    fair_result = self._fair_value_estimator.estimate(
                        match.odds_event, sport=sport
                    )
                except Exception as e:
                    logger.warning("P-006: FairValue estimate failed: %s — falling back to consensus", e)

            if fair_result is not None:
                home_prob = fair_result.home_prob
                away_prob = fair_result.away_prob
                fv_confidence = fair_result.confidence
                fv_method = fair_result.method
            else:
                # Legacy path: use odds_client consensus directly
                try:
                    consensus = self.odds_client.get_consensus(match.odds_event)
                except Exception:
                    consensus = None
                if not consensus:
                    continue
                home_prob = getattr(consensus, "home_prob", None)
                away_prob = getattr(consensus, "away_prob", None)
                if home_prob is None or away_prob is None:
                    continue
                fv_confidence = 1.0
                fv_method = "consensus"

            event_id = getattr(match.odds_event, "event_id", None)

            # Per-cycle event dedup (same as P-001 behaviour)
            if event_id and event_id in placed_events:
                continue

            # Get Polymarket price — prefer Gamma outcomePrices (already in
            # the match result) over CLOB midpoint API to avoid URL encoding
            # issues with py-clob-client.
            mid = match.yes_price
            if mid is None:
                mid = self.polymarket_client.get_midpoint(match.token_id_yes)
            if mid is None or mid <= 0.03 or mid >= 0.97:
                continue

            # Determine which side is YES (home or away)
            yes_team = _infer_yes_team(match)
            if yes_team is None:
                continue  # Skip ties / ambiguous

            # ── Validate & auto-correct YES-side mapping ──────────────────
            # Uses the Polymarket price itself as a sanity signal: if the
            # inferred fair_yes_prob and mid are on opposite sides of 0.50,
            # the mapping is almost certainly inverted.  The validator will
            # try flipping before giving up.
            yes_team = _validate_and_correct_mapping(
                yes_team, home_prob, away_prob, mid,
                match.polymarket_question,
            )
            if yes_team is None:
                continue  # Neither mapping is trustworthy

            logger.debug(
                "P-006: EDGE_MAP event=%r yes_team=%s home=%s(%.3f) away=%s(%.3f) "
                "mid=%.3f yes_outcome=%s",
                match.polymarket_question[:50], yes_team,
                getattr(match.odds_event, "home_team", "?"), home_prob,
                getattr(match.odds_event, "away_team", "?"), away_prob,
                mid, getattr(match, "yes_outcome_name", None),
            )

            fair_yes_prob = home_prob if yes_team == "home" else away_prob

            # Evaluate edge (fee_pct=0.0 for Polymarket)
            # Pass confidence from fair value estimator for Kelly sizing
            edge_result = self.edge_calculator.best_side(
                fair_yes_prob=fair_yes_prob,
                kalshi_yes_price=mid,
                confidence=fv_confidence,
            )

            # ── Sport-specific edge filter ──────────────────────────────
            # If sport has tighter thresholds than the global P-006 config,
            # apply them here as a secondary gate.
            if edge_result is not None and (sport_min_edge is not None or sport_min_ev is not None):
                passes_sport_edge = True
                if sport_min_edge is not None and edge_result.raw_edge < sport_min_edge:
                    passes_sport_edge = False
                if sport_min_ev is not None and edge_result.ev < sport_min_ev:
                    passes_sport_edge = False
                if not passes_sport_edge:
                    logger.info(
                        "P-006: SKIP_SPORT_EDGE sport=%s event=%r edge=%.4f(min=%.4f) "
                        "ev=%.4f(min=%s)",
                        sport, match.polymarket_question[:50],
                        edge_result.raw_edge, sport_min_edge or 0,
                        edge_result.ev, sport_min_ev,
                    )
                    edge_result = None

            if edge_result is None:
                # Diagnostic: log the raw numbers so we can see how close we are
                raw_yes_edge = fair_yes_prob - mid
                raw_no_edge = (1.0 - fair_yes_prob) - (1.0 - mid)
                best_raw = max(raw_yes_edge, -raw_yes_edge)
                logger.info(
                    "P-006: SKIP_EDGE event=%r mid=%.3f fair=%.3f "
                    "raw_edge=%.4f min_edge=%.4f",
                    match.polymarket_question[:50], mid, fair_yes_prob,
                    best_raw, getattr(self.edge_calculator, "min_edge_pct", 0),
                )
                fp = self.make_fingerprint(match.polymarket_condition_id, "NONE")
                result = ScanResult(
                    fingerprint=fp,
                    timestamp_utc=self._now_fn().isoformat(),
                    pod_id=self.pod_id,
                    pod_name=self.pod_name,
                    mode=self.mode,
                    venue="polymarket",
                    market_type="sports_{}".format(sport),
                    event=match.polymarket_question,
                    market_id=match.polymarket_condition_id,
                    side="NONE",
                    fair_prob=fair_yes_prob,
                    venue_prob=mid,
                    edge_pct=0.0,
                    ev=0.0,
                    kelly_fraction=0.0,
                    position_size_usd=0.0,
                    action="SKIPPED_EDGE",
                    skip_reason="Below edge threshold",
                )
                self.write_log(result)
                results.append(result)
                continue

            # Skip if we already hold an open position on this market
            if self.has_open_position(match.polymarket_condition_id):
                continue

            # Dedup check
            fp = self.make_fingerprint(
                match.polymarket_condition_id, edge_result.side
            )
            if self.is_duplicate(fp):
                continue
            self.mark_seen(fp)

            # Risk check — position sizing (via BasePod helper)
            position_size = self.compute_position_size(edge_result.kelly_fractional)

            if position_size <= 0:
                logger.info(
                    "P-006: SKIP_KELLY event=%r side=%s edge=%.4f "
                    "kelly_frac=%.4f min_kelly=%.4f",
                    match.polymarket_question[:50], edge_result.side,
                    edge_result.raw_edge, edge_result.kelly_fractional,
                    self._min_kelly_fraction if self._min_kelly_fraction is not None
                    else 0.02,
                )
                result = ScanResult(
                    fingerprint=fp,
                    timestamp_utc=self._now_fn().isoformat(),
                    pod_id=self.pod_id,
                    pod_name=self.pod_name,
                    mode=self.mode,
                    venue="polymarket",
                    market_type="sports_{}".format(sport),
                    event=match.polymarket_question,
                    market_id=match.polymarket_condition_id,
                    side=edge_result.side,
                    fair_prob=fair_yes_prob,
                    venue_prob=mid,
                    edge_pct=edge_result.raw_edge,
                    ev=edge_result.ev,
                    kelly_fraction=edge_result.kelly_fractional,
                    position_size_usd=0.0,
                    action="SKIPPED_RISK",
                    skip_reason="Position size zero",
                )
                self.write_log(result)
                results.append(result)
                continue

            # ── Aggregate risk gate ─────────────────────────────────────
            if not self.check_aggregate_risk(
                "polymarket", position_size,
                market_id=match.polymarket_condition_id,
            ):
                result = ScanResult(
                    fingerprint=fp,
                    timestamp_utc=self._now_fn().isoformat(),
                    pod_id=self.pod_id,
                    pod_name=self.pod_name,
                    mode=self.mode,
                    venue="polymarket",
                    market_type="sports_{}".format(sport),
                    event=match.polymarket_question,
                    market_id=match.polymarket_condition_id,
                    side=edge_result.side,
                    fair_prob=fair_yes_prob,
                    venue_prob=mid,
                    edge_pct=edge_result.raw_edge,
                    ev=edge_result.ev,
                    kelly_fraction=edge_result.kelly_fractional,
                    position_size_usd=position_size,
                    action="SKIPPED_RISK",
                    skip_reason="aggregate_risk: pod/portfolio exposure limit",
                )
                self.write_log(result)
                results.append(result)
                continue

            # Determine token to trade
            token_id = (
                match.token_id_yes if edge_result.side == "YES"
                else match.token_id_no
            )
            contracts = position_size / mid if mid > 0 else 0

            # Place order (paper or live)
            logger.info(
                "P-006: PLACING side=%s event=%r mid=%.3f fair=%.3f "
                "edge=%.4f kelly=%.4f size=$%.2f",
                edge_result.side, match.polymarket_question[:50],
                mid, fair_yes_prob, edge_result.raw_edge,
                edge_result.kelly_fractional, position_size,
            )
            order_result = self.polymarket_client.place_order(
                token_id=token_id,
                side="BUY",
                price=mid,
                size=contracts,
            )

            # Build "Home vs Away" event label for dashboard display
            home_name = getattr(match.odds_event, "home_team", "")
            away_name = getattr(match.odds_event, "away_team", "")
            # Game start time from Odds API (ISO string)
            commence_raw = getattr(match.odds_event, "commence_time", None)
            game_time: str = (
                commence_raw.isoformat()  # type: ignore[union-attr]
                if hasattr(commence_raw, "isoformat")
                else str(commence_raw) if commence_raw else ""
            )
            event_label = (
                "{} vs {}".format(home_name, away_name)
                if home_name and away_name
                else match.polymarket_question
            )

            result = ScanResult(
                fingerprint=fp,
                timestamp_utc=self._now_fn().isoformat(),
                pod_id=self.pod_id,
                pod_name=self.pod_name,
                mode=self.mode,
                venue="polymarket",
                market_type="sports_{}".format(sport),
                event=event_label,
                market_id=match.polymarket_condition_id,
                side=edge_result.side,
                fair_prob=fair_yes_prob,
                venue_prob=mid,
                edge_pct=edge_result.raw_edge,
                ev=edge_result.ev,
                kelly_fraction=edge_result.kelly_fractional,
                position_size_usd=position_size,
                action="PLACED",
                order_id=order_result.order_id,
                fill_price=order_result.fill_price,
                extra={
                    "token_id": token_id,
                    "contracts": contracts,
                    "yes_team": yes_team,
                    "home_team": home_name,
                    "away_team": away_name,
                    "game_time": game_time,
                    "fuzzy_score": match.fuzzy_score,
                    "polymarket_question": match.polymarket_question,
                    "market_slug": match.polymarket_slug,
                    "fair_value_method": fv_method,
                    "fair_value_confidence": fv_confidence,
                },
            )
            self.write_log(result)
            results.append(result)
            self.mark_position_open(match.polymarket_condition_id)

            # (The guard already holds this trade's exposure — the risk
            # gate above reserved it under polymarket_condition_id.  This
            # used to poke aggregate_risk._add_position directly, which
            # double-counted the venue/pod buckets once post_cycle added
            # the same market again.)

            # Mark event as placed this cycle
            if event_id:
                placed_events.add(event_id)

        return results

    # ── Sport keyword map ─────────────────────────────────────────────
    # Primary league-name keywords — sport-agnostic, no team lists needed.
    # Any Polymarket question mentioning the league or a common game-phrase
    # for that sport passes the pre-filter; the fuzzy matcher does the rest.
    _SPORT_LEAGUE_KEYWORDS: dict = {
        "americanfootball_nfl":  ["nfl", "super bowl", "afc", "nfc"],
        "americanfootball_ncaaf":["ncaaf", "college football", "cfp"],
        "basketball_nba":        ["nba"],
        "basketball_ncaab":      ["ncaab", "ncaa", "college basketball",
                                  "march madness"],
        "basketball_ncaaw":      ["ncaaw", "ncaa women", "women's basketball"],
        "baseball_mlb":          ["mlb", "world series", "baseball"],
        "icehockey_nhl":         ["nhl", "stanley cup"],
        "tennis_atp":            ["atp", "tennis"],
        "tennis_wta":            ["wta", "tennis"],
    }

    # Polymarket Gamma API: map our Odds API sport keys → Polymarket 'sport'
    # abbreviation used in /sports metadata for dynamic series_id lookup.
    # The /sports endpoint has: series=<series_id_number>, sport=<abbrev>.
    # We match on the 'sport' field (e.g. "nba"), not the 'series' field.
    _SPORT_LEAGUE_NAME: dict = {
        "americanfootball_nfl":  "NFL",
        "americanfootball_ncaaf":"NCAAF",
        "basketball_nba":        "NBA",
        "basketball_ncaab":      "NCAAB",
        "basketball_ncaaw":      "NCAAW",
        "baseball_mlb":          "MLB",
        "icehockey_nhl":         "NHL",
        "tennis_atp":            "ATP",
        "tennis_wta":            "WTA",
    }
    # Note: find_series_id() matches case-insensitively against the 'sport'
    # field from /sports (e.g. sport="nba" matches our "NBA").

    # tag_id for game-day bets only (excludes futures/championship markets).
    # Discovered from Polymarket docs / Poly-Trader reference code.
    GAME_BET_TAG_ID = 100639
    # Generic pattern for "will X beat/defeat Y?" market questions.
    # Requires an explicit opponent — excludes "will X win?" (elections, futures).
    _GAME_PATTERN = re.compile(
        r"will .{3,40} (beat|defeat|win against|win over) .{3,40}\?",
        re.IGNORECASE,
    )

    # VS-style matchup title — matches Polymarket EVENT titles like
    # "Golden State Warriors vs. Oklahoma City Thunder" or "Lakers vs Celtics".
    # These titles score ~97% in token_sort_ratio against Odds API event names.
    _VS_PATTERN = re.compile(
        r".{3,50}\s+vs\.?\s+.{3,50}",
        re.IGNORECASE,
    )

    def _is_sport_market(self, title: str, sport: str) -> bool:
        """Three-tier check: league keyword, beat/defeat pattern, or VS matchup title."""
        t_lower = title.lower()
        # Tier 1: league keyword present in title/question
        sport_base = sport.rsplit("_", 1)[-1]
        league_keys = self._SPORT_LEAGUE_KEYWORDS.get(
            sport,
            [sport_base, sport.split("_")[-1]],
        )
        if any(kw in t_lower for kw in league_keys):
            return True
        # Tier 2: explicit head-to-head question ("Will X beat/defeat Y?")
        if self._GAME_PATTERN.search(title):
            return True
        # Tier 3: VS-style event title ("Warriors vs. Thunder")
        # Primary signal for the /events endpoint
        return bool(self._VS_PATTERN.search(title))

    def _verify_market_slug(
        self, slug: str, event_title: str
    ) -> bool:
        """Secondary verification: query Gamma /markets?slug=X and check that
        the returned market's question relates to the event title.

        The Gamma ``/markets?condition_id=X`` endpoint is BROKEN for sports
        markets (always returns the Biden-COVID market).  Slug-based lookup
        works reliably.  Returns True if verified, False otherwise.
        """
        if not slug:
            logger.warning(
                "P-006: SLUG_VERIFY no slug for event=%r — rejecting",
                event_title[:60],
            )
            return False

        try:
            info = self.polymarket_client.get_market_resolution(
                condition_id="", slug=slug,
            )
        except Exception as exc:
            logger.warning(
                "P-006: SLUG_VERIFY lookup failed slug=%s err=%s — rejecting",
                slug[:40], exc,
            )
            return False

        if info is None:
            logger.warning(
                "P-006: SLUG_VERIFY no data for slug=%s — rejecting",
                slug[:40],
            )
            return False

        raw_market = info.get("raw", {})
        verify_q = (raw_market.get("question") or "").lower()
        if not verify_q:
            logger.warning(
                "P-006: SLUG_VERIFY no question for slug=%s — rejecting",
                slug[:40],
            )
            return False

        # Check word overlap between event title and the verified question
        _STOP = {"the", "will", "does", "this", "that", "from", "with",
                 "for", "are", "was", "were", "been", "have", "has",
                 "before", "after", "against", "win", "beat", "defeat"}
        title_words = set(
            w for w in event_title.lower().split() if len(w) >= 3
        ) - _STOP
        q_words = set(
            w for w in verify_q.split() if len(w) >= 3
        ) - _STOP

        overlap = len(title_words & q_words)
        if title_words and overlap == 0:
            logger.warning(
                "P-006: SLUG_VERIFY mismatch! event=%r verified_q=%r "
                "slug=%s — rejecting (no word overlap)",
                event_title[:60], verify_q[:60], slug[:40],
            )
            return False

        return True

    def _fetch_sport_markets(self, sport: str) -> List[PolymarketMarket]:
        """Fetch active Polymarket game markets via the Gamma /events endpoint.

        Uses series_id (from /sports metadata) + tag_id=100639 (game bets only)
        to filter directly to individual game events (e.g. "Bucks vs Bulls"),
        bypassing the global feed and excluding futures/championship markets.

        For each matched event we create ONE PolymarketMarket using the event
        TITLE as the question field so the fuzzy matcher can score it against
        Odds API event names (getting ~97% match instead of ~30-50% for question text).
        """
        markets = []
        cursor = None
        max_pages = 10

        # Look up Polymarket series_id for this sport.
        # For tournament-specific keys like "tennis_wta_indian_wells", try
        # progressively shorter prefixes until we find a match in the map.
        league_name = self._SPORT_LEAGUE_NAME.get(sport)
        if not league_name:
            # Try prefix matching: tennis_wta_indian_wells → tennis_wta → tennis
            parts = sport.split("_")
            for i in range(len(parts) - 1, 0, -1):
                prefix = "_".join(parts[:i])
                league_name = self._SPORT_LEAGUE_NAME.get(prefix)
                if league_name:
                    break
            if not league_name:
                league_name = parts[-1].upper()
        series_id = self.polymarket_client.find_series_id(league_name)

        if series_id:
            logger.info(
                "P-006: sport=%s league=%s series_id=%d",
                sport, league_name, series_id,
            )
        else:
            # Strict mode: reject sports without a known Polymarket series_id
            # to prevent false-positive fuzzy matches from the global feed.
            logger.info(
                "P-006: sport=%s league=%s — no series_id found, "
                "skipping (require_series_id=true)",
                sport, league_name,
            )
            return []

        # series_id is always set at this point (we return [] above otherwise).
        # The API already filtered by sport+game-bets — no client-side sport
        # filter needed.

        page_num = 0
        _logged_sample = False
        for page_num in range(max_pages):
            resp = self.polymarket_client.get_sport_events(
                series_id=series_id,
                tag_id=self.GAME_BET_TAG_ID,
                next_cursor=cursor,
            )
            events = resp.get("data", [])
            if not events:
                break

            # Log first few event titles for diagnostics
            if not _logged_sample and events:
                _logged_sample = True
                for ev in events[:3]:
                    logger.info(
                        "P-006: SAMPLE sport=%s title=%r startDate=%s n_mkts=%d",
                        sport,
                        (ev.get("title") or ev.get("slug", ""))[:80],
                        (ev.get("startDate") or "?")[:20],
                        len(ev.get("markets", [])),
                    )

            for event in events:
                # ── Expired-event filter (always applied) ─────────────────
                # Polymarket keeps old events marked "active" long after the
                # game is over.  If endDate has passed, skip — these would
                # fuzzy-match to a fresh Odds API game between the same teams
                # and produce phantom edges from stale prices.
                end_date_str = event.get("endDate") or event.get("end_date")
                if end_date_str:
                    try:
                        eds = end_date_str
                        if eds.endswith("Z"):
                            eds = eds[:-1] + "+00:00"
                        end_dt = datetime.fromisoformat(eds)
                        if end_dt.tzinfo is None:
                            end_dt = end_dt.replace(tzinfo=timezone.utc)
                        days_until_end = (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400
                        if days_until_end < -1:
                            # Event ended more than a day ago — definitely stale
                            continue
                    except (ValueError, TypeError):
                        pass

                event_title = event.get("title") or event.get("slug", "")
                if not event_title:
                    continue

                # Extract primary market — find the moneyline (2-outcome) market.
                # Markets inside /events can have two formats:
                #   A) tokens: [{tokenId, outcome}, ...]   (old/CLOB format)
                #   B) outcomes: ["Yes","No"], outcomePrices: ["0.55","0.45"],
                #      clobTokenIds: "id1,id2"              (Gamma format)
                event_markets = event.get("markets", [])

                # Log first market's structure once per sport for debugging
                if event_markets and not hasattr(self, "_mkt_diag_done"):
                    self._mkt_diag_done = True
                    mkt0 = event_markets[0]
                    logger.info(
                        "P-006: MKT_DIAG event=%r keys=%s outcomes=%s "
                        "tokens_len=%d mkt_q=%r cid=%s",
                        event_title[:50],
                        sorted(mkt0.keys())[:15],
                        mkt0.get("outcomes", mkt0.get("groupItemTitle", "?")),
                        len(mkt0.get("tokens", [])),
                        (mkt0.get("question") or "")[:60],
                        (mkt0.get("conditionId") or mkt0.get("condition_id", ""))[:20],
                    )

                primary_market = None
                outcomes = []
                for mkt in event_markets:
                    # Format A: tokens array
                    if len(mkt.get("tokens", [])) >= 2:
                        primary_market = mkt
                        break
                    # Format B: outcomes array (Gamma /events format)
                    outcomes = mkt.get("outcomes") or []
                    if isinstance(outcomes, str):
                        try:
                            import json as _json
                            outcomes = _json.loads(outcomes)
                        except (ValueError, TypeError):
                            outcomes = []
                    if len(outcomes) >= 2:
                        primary_market = mkt
                        break

                if not primary_market:
                    continue

                # Extract token IDs and condition_id from whichever format
                tokens = primary_market.get("tokens", [])
                condition_id = (
                    primary_market.get("conditionId")
                    or primary_market.get("condition_id", "")
                )

                # ── Cross-validate: market question must relate to event ──
                # The Gamma /events API sometimes nests unrelated markets
                # inside sports events (e.g. political markets inside NBA
                # events).  Verify the market's own question shares at least
                # one meaningful word with the event title to catch this.
                #
                # If the market has NO question field at all, we can't
                # validate it — skip to be safe rather than trade blind.
                mkt_q = (primary_market.get("question") or "").lower()
                title_words = set(w for w in event_title.lower().split() if len(w) >= 3)
                _STOP = {"the", "will", "does", "this", "that", "from", "with",
                         "for", "are", "was", "were", "been", "have", "has",
                         "before", "after", "against"}
                title_words -= _STOP

                if not mkt_q:
                    logger.warning(
                        "P-006: MARKET_NO_QUESTION event=%r cid=%s "
                        "— skipping (cannot validate condition_id)",
                        event_title[:60], condition_id[:20],
                    )
                    continue

                mkt_words = set(w for w in mkt_q.split() if len(w) >= 3)
                mkt_words -= _STOP
                word_overlap = len(title_words & mkt_words)
                if title_words and word_overlap == 0:
                    logger.warning(
                        "P-006: MARKET_MISMATCH event=%r market_q=%r "
                        "cid=%s — skipping (no word overlap)",
                        event_title[:60], mkt_q[:60], condition_id[:20],
                    )
                    continue

                if tokens and len(tokens) >= 2:
                    # Format A: tokens array with tokenId + outcome
                    yes_tok_id = ""
                    no_tok_id = ""
                    for t in tokens:
                        outcome = (t.get("outcome") or "").lower()
                        tid = t.get("tokenId") or t.get("token_id", "")
                        if outcome == "yes":
                            yes_tok_id = tid
                        elif outcome == "no":
                            no_tok_id = tid
                    if not yes_tok_id:
                        yes_tok_id = tokens[0].get("tokenId") or tokens[0].get("token_id", "")
                    if not no_tok_id and len(tokens) > 1:
                        no_tok_id = tokens[1].get("tokenId") or tokens[1].get("token_id", "")
                else:
                    # Format B: clobTokenIds as JSON array string or list.
                    # Gamma API returns: '["78246210...", "39393939..."]'
                    # MUST JSON-parse — do NOT split on comma.
                    clob_ids_raw = primary_market.get("clobTokenIds") or ""
                    if isinstance(clob_ids_raw, str):
                        try:
                            import json as _json
                            clob_ids = [str(x) for x in _json.loads(clob_ids_raw)]
                        except (ValueError, TypeError):
                            # Fallback: maybe it's actually comma-separated
                            clob_ids = [x.strip() for x in clob_ids_raw.split(",") if x.strip()]
                    elif isinstance(clob_ids_raw, list):
                        clob_ids = [str(x) for x in clob_ids_raw]
                    else:
                        clob_ids = []

                    outcomes = primary_market.get("outcomes") or []
                    if isinstance(outcomes, str):
                        try:
                            import json as _json
                            outcomes = _json.loads(outcomes)
                        except (ValueError, TypeError):
                            outcomes = []

                    # Polymarket sports markets use team names as outcomes
                    # (e.g. ["Nuggets", "Grizzlies"]), NOT "Yes"/"No".
                    # outcomePrices[0] corresponds to outcomes[0] / clobTokenIds[0].
                    # We treat outcomes[0] as the "yes" side (first team wins)
                    # and outcomes[1] as the "no" side (second team wins).
                    yes_tok_id = ""
                    no_tok_id = ""
                    for i, oc in enumerate(outcomes):
                        tid = clob_ids[i] if i < len(clob_ids) else ""
                        oc_lower = oc.lower()
                        if oc_lower == "yes" or i == 0:
                            if not yes_tok_id:
                                yes_tok_id = tid
                        elif oc_lower == "no" or i == 1:
                            if not no_tok_id:
                                no_tok_id = tid

                # Extract yes_price from outcomePrices (Gamma API provides
                # prices inline so we don't need CLOB midpoint calls).
                yes_price = None
                raw_prices = primary_market.get("outcomePrices") or ""
                if isinstance(raw_prices, str) and raw_prices:
                    try:
                        import json as _json
                        price_list = _json.loads(raw_prices)
                    except (ValueError, TypeError):
                        price_list = []
                elif isinstance(raw_prices, list):
                    price_list = raw_prices
                else:
                    price_list = []
                if price_list:
                    try:
                        yes_price = float(price_list[0])
                    except (ValueError, TypeError, IndexError):
                        pass

                # ── Resolved-price filter ─────────────────────────────────
                # If yes_price is near 0 or 1, the market is effectively
                # resolved (game already happened).  Skip to avoid matching
                # stale events to fresh Odds API games.
                if yes_price is not None and (yes_price < 0.03 or yes_price > 0.97):
                    continue

                # Determine the actual team/outcome name that outcomePrices[0]
                # corresponds to.  This is critical for correct edge calculation:
                #   - groupItemTitle: "Golden Knights" (multi-market events)
                #   - outcomes[0]: team name if not "Yes"/"No"
                #   - question field on the market itself
                yes_outcome_name = None
                git_title = primary_market.get("groupItemTitle") or ""
                if git_title and git_title.lower() not in ("yes", "no", ""):
                    yes_outcome_name = git_title
                elif outcomes and isinstance(outcomes, list) and outcomes[0].lower() not in ("yes", "no"):
                    yes_outcome_name = outcomes[0]
                # Also try the market's own question if it names a team
                if not yes_outcome_name:
                    mkt_question = primary_market.get("question") or ""
                    mkt_will = re.search(
                        r"will\s+(?:the\s+)?(.+?)(?:\s+(?:win|beat|defeat))",
                        mkt_question, re.IGNORECASE,
                    )
                    if mkt_will:
                        yes_outcome_name = mkt_will.group(1).strip()

                # Extract slug for settlement lookups.  The Gamma
                # /markets?condition_id=X endpoint is BROKEN (always returns
                # Biden-COVID market).  We use slug-based lookups instead.
                # The cross-validation above (word overlap between event title
                # and market question) is our safety check for data integrity.
                market_slug = primary_market.get("slug") or event.get("slug", "")

                try:
                    market = PolymarketMarket(
                        condition_id=condition_id,
                        token_id_yes=yes_tok_id,
                        token_id_no=no_tok_id,
                        # Use event title as the question — it reads "X vs Y" and
                        # scores ~97% against Odds API "{home} {away}" format.
                        question=event_title,
                        slug=market_slug,
                        active=primary_market.get("active", event.get("active", False)),
                        closed=primary_market.get("closed", event.get("closed", False)),
                        end_date=event.get("endDate") or primary_market.get("endDate"),
                        game_start_time=(
                            event.get("startDate") or primary_market.get("startDate")
                        ),
                        yes_price=yes_price,
                        yes_outcome_name=yes_outcome_name,
                    )
                    if market.active and not market.closed:
                        markets.append(market)
                except (KeyError, TypeError):
                    continue

            cursor = resp.get("next_cursor")
            if not cursor:
                break

        logger.info(
            "P-006: _fetch_sport_markets sport=%s pages=%d markets=%d",
            sport, page_num + 1, len(markets),
        )
        return markets

    def venue_name(self) -> str:
        return "polymarket"

    @classmethod
    def from_config(cls, config: dict, **overrides):
        """Build P-006 from config.

        Requires polymarket_client, odds_client, matcher, edge_calculator,
        risk_manager to be passed via overrides or built from config.

        The shared EdgeCalculator (built for P-001) uses fee_pct=0.07 and
        the global min_edge_pct.  P-006 needs its own copy with fee_pct=0.0
        (Polymarket charges no profit fee) and P-006's min_edge_pct from
        config (defaults to 3%).
        """
        poly_client = overrides.get("polymarket_client") or PolymarketClient.from_config(config)
        matcher = overrides.get("matcher") or PolymarketMatcher.from_config(config)

        pod_cfg = config.get("pods", {}).get("P-006", {})

        # ── Edge calculator: clone from shared but override fee + edge ──
        # The shared edge_calculator uses Kalshi's 7% fee — P-006 needs 0%.
        shared_edge = overrides.get("edge_calculator")
        if shared_edge is not None:
            try:
                # Import EdgeCalculator to create a P-006-specific instance
                from edge_calculator import EdgeCalculator  # type: ignore
                p006_fee = float(pod_cfg.get("fee_pct", 0.0))
                p006_min_edge = float(pod_cfg.get("min_edge_pct", 0.03))
                p006_min_ev = float(pod_cfg.get("min_ev", getattr(shared_edge, "min_ev", 0.01)))
                edge_calc = EdgeCalculator(
                    fee_pct=p006_fee,
                    kelly_fraction=getattr(shared_edge, "kelly_fraction", 0.25),
                    base_bet_pct=getattr(shared_edge, "base_bet_pct", 0.03),
                    max_bet_pct=getattr(shared_edge, "max_bet_pct", 0.06),
                    high_edge_threshold=getattr(shared_edge, "high_edge_threshold", 0.10),
                    max_edge_threshold=getattr(shared_edge, "max_edge_threshold", 0.25),
                    min_edge_pct=p006_min_edge,
                    min_ev=p006_min_ev,
                )
                logger.info(
                    "P-006: built dedicated EdgeCalculator "
                    "(fee=%.0f%%, min_edge=%.1f%%, min_ev=%.3f)",
                    p006_fee * 100, p006_min_edge * 100, p006_min_ev,
                )
            except ImportError:
                logger.warning(
                    "P-006: EdgeCalculator import failed — using shared "
                    "instance (fee_pct may be wrong)"
                )
                edge_calc = shared_edge
        else:
            edge_calc = None

        sports = pod_cfg.get(
            "sports", config.get("odds_api", {}).get("sports", [])
        )
        log_dir = Path(config.get("paths", {}).get("pod_logs", "data/pods"))
        log_path = overrides.get("trade_log_path") or log_dir / "P-006.jsonl"

        env = pod_cfg.get("environment", config.get("environment", "demo"))
        mode_map = {"demo": "paper", "live": "live"}

        # ── Per-pod Kelly fraction floor ──────────────────────────────
        # Polymarket has 0% fees, so even small edges are genuinely +EV.
        # The global MIN_KELLY_FRACTION (2%) was calibrated for Kalshi
        # (7% fee) and creates a double-filter that blocks P-006 trades.
        # Default to 0.005 (0.5%) for P-006 to allow 3%+ edge trades
        # at all price levels while still filtering zero-conviction noise.
        min_kelly = float(pod_cfg.get("min_kelly_fraction", 0.005))
        logger.info(
            "P-006: from_config min_kelly_fraction=%.4f min_edge=%.4f fee=%.2f",
            min_kelly,
            getattr(edge_calc, "min_edge_pct", 0.0) if edge_calc else 0.0,
            getattr(edge_calc, "fee_pct", 0.0) if edge_calc else 0.0,
        )

        return cls(
            polymarket_client=poly_client,
            odds_client=overrides.get("odds_client"),
            matcher=matcher,
            edge_calculator=edge_calc,
            risk_manager=overrides.get("risk_manager"),
            sports=sports,
            trade_log_path=log_path,
            mode=mode_map.get(env, "paper"),
            _now_fn=overrides.get("_now_fn"),
            aggregate_risk=overrides.get("aggregate_risk"),
            trade_store=overrides.get("trade_store"),
            min_kelly_fraction=min_kelly,
        )
