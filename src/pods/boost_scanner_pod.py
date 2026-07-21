"""
src/pods/boost_scanner_pod.py
──────────────────────────────
P-010: Daily Odds Boost Grind pod.

Strategy
─────────
Sportsbooks offer "odds boosts" — a specific market gets enhanced payout
odds for a limited time.  For example, the Chiefs are normally +150 but
today FanDuel offers +350 on a max $25 stake.

If the boosted probability (1/boosted_decimal_odds) is below the fair
probability, the boost has positive expected value.

Modes
─────
EV MODE (default): place the bet at boosted odds, accept variance.
    Bet $25, EV = +$8 if boost is genuinely +EV.

LOCKED MODE: hedge on opposite side at Kalshi/Polymarket to lock in
    guaranteed profit regardless of outcome.
    Useful when the EV is large but you prefer zero variance.

Execution split
───────────────
- Sportsbook boost bet: manual (no programmatic API)
- Optional hedge:       automated via kalshi_client or polymarket_client

Config example (in config_multi_pod.yaml)
──────────────────────────────────────────
  P-010:
    enabled: true
    mode: ev            # "ev" or "locked"
    min_ev_pct: 0.05    # Minimum 5% EV per dollar wagered
    boosts:
      - promo_id: FD-BOOST-2026-01-15
        sportsbook: fanduel
        promo_type: odds_boost
        amount: 25
        original_odds: +150
        boosted_odds: +350
        fair_yes_prob: 0.38
        max_stake: 25
        hedge_venue: kalshi
        hedge_market_id: CHIEFS-2026-01
        hedge_no_price: 0.64
        expiry: "2026-01-15"
        status: pending
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.base_pod import BasePod, ScanResult
from src.promo_calculator import (
    PromoRecord,
    american_to_decimal,
    odds_boost_ev,
    odds_boost_ev_pct,
    odds_boost_hedge_size,
    odds_boost_locked_profit,
)

logger = logging.getLogger(__name__)

from src.pod_registry import register_pod

POD_ID = "P-010"
POD_NAME = "Daily Odds Boost Grind"


@register_pod("P-010")
class BoostScannerPod(BasePod):
    """Scans configured odds boosts for positive EV and places bets.

    Args:
        boosts:            List of PromoRecord with promo_type == "odds_boost".
        kalshi_client:     Optional Kalshi client for locked-mode hedges.
        polymarket_client: Optional Polymarket client for locked-mode hedges.
        boost_mode:        "ev" (take the boost as-is) or "locked" (hedge it).
        min_ev_pct:        Minimum EV as fraction of stake to act.
        trade_log_path:    Path to JSONL trade log.
        mode:              "paper" or "live".
    """

    def __init__(
        self,
        boosts: List[PromoRecord],
        kalshi_client=None,
        polymarket_client=None,
        boost_mode: str = "ev",
        min_ev_pct: float = 0.05,
        trade_log_path: Path = Path("data/pods/P-010.jsonl"),
        mode: str = "paper",
        _now_fn: Optional[Callable] = None,
    ):
        super().__init__(
            pod_id=POD_ID,
            pod_name=POD_NAME,
            edge_calculator=None,
            risk_manager=None,
            trade_log_path=trade_log_path,
            mode=mode,
            _now_fn=_now_fn,
        )
        self._boosts = boosts
        self._kalshi_client = kalshi_client
        self._polymarket_client = polymarket_client
        self.boost_mode = boost_mode
        self.min_ev_pct = min_ev_pct

    # ── BasePod interface ────────────────────────────────────────────

    @property
    def venue_name(self) -> str:  # type: ignore[override]
        return "multi"

    def scan_once(self) -> List[ScanResult]:
        """Process pending boosts and return ScanResults."""
        results: List[ScanResult] = []

        for boost in self._boosts:
            if not boost.is_active():
                continue
            if self._is_expired(boost):
                logger.info(
                    "%s: boost %s expired, skipping", POD_ID, boost.promo_id
                )
                continue

            try:
                result = self._evaluate_boost(boost)
                if result:
                    results.append(result)
            except Exception as exc:
                logger.error(
                    "%s: error evaluating boost %s: %s",
                    POD_ID, boost.promo_id, exc,
                )

        return results

    # ── Boost evaluation ─────────────────────────────────────────────

    def _evaluate_boost(self, boost: PromoRecord) -> Optional[ScanResult]:
        """Evaluate one odds boost and return a ScanResult."""
        if boost.promo_type != "odds_boost":
            return None

        if boost.boosted_odds is None:
            logger.warning("%s: boost %s missing boosted_odds", POD_ID, boost.promo_id)
            return None

        if boost.fair_yes_prob is None:
            logger.warning("%s: boost %s missing fair_yes_prob", POD_ID, boost.promo_id)
            return None

        stake = boost.effective_stake()
        boosted_decimal = american_to_decimal(boost.boosted_odds)
        fair_prob = boost.fair_yes_prob

        ev_pct = odds_boost_ev_pct(boosted_decimal, fair_prob)
        ev_usd = odds_boost_ev(stake, boosted_decimal, fair_prob)

        fp = self.make_fingerprint(boost.promo_id, "BOOST")

        if self.is_duplicate(fp):
            logger.debug(
                "%s: boost %s already processed this hour", POD_ID, boost.promo_id
            )
            return None

        # Reject if EV below threshold
        if ev_pct < self.min_ev_pct:
            result = self._build_result(
                fp=fp, boost=boost,
                boosted_decimal=boosted_decimal,
                fair_prob=fair_prob,
                stake=stake,
                ev_usd=ev_usd,
                ev_pct=ev_pct,
                action="SKIPPED_EDGE",
                skip_reason="ev_pct={:.1%} < min={:.1%}".format(
                    ev_pct, self.min_ev_pct
                ),
            )
            self.write_log(result)
            return result

        # Place (or log) the boost
        if self.boost_mode == "locked":
            return self._execute_locked(fp, boost, boosted_decimal, fair_prob, stake, ev_usd, ev_pct)
        else:
            return self._execute_ev(fp, boost, boosted_decimal, fair_prob, stake, ev_usd, ev_pct)

    def _execute_ev(
        self, fp, boost, boosted_decimal, fair_prob, stake, ev_usd, ev_pct
    ) -> ScanResult:
        """EV mode: log the boost recommendation, no automated execution."""
        # In paper mode, log as PLACED (the sportsbook bet is manual)
        # In live mode, log recommendation but note it needs manual placement
        if self.mode == "paper":
            action = "PLACED"
            order_id = "manual-{}".format(boost.promo_id)
            fill_price = 1.0 / boosted_decimal
        else:
            action = "PLACED"
            order_id = "manual-{}".format(boost.promo_id)
            fill_price = 1.0 / boosted_decimal

        self.mark_seen(fp)
        result = self._build_result(
            fp=fp, boost=boost,
            boosted_decimal=boosted_decimal,
            fair_prob=fair_prob,
            stake=stake,
            ev_usd=ev_usd,
            ev_pct=ev_pct,
            action=action,
            order_id=order_id,
            fill_price=fill_price,
        )
        self.write_log(result)
        return result

    def _execute_locked(
        self, fp, boost, boosted_decimal, fair_prob, stake, ev_usd, ev_pct
    ) -> ScanResult:
        """Locked mode: hedge the boost for guaranteed profit."""
        no_price = self._get_no_price(boost)
        if no_price is None:
            # Can't lock in — fall back to EV mode
            logger.info(
                "%s: no hedge price for %s, falling back to EV mode",
                POD_ID, boost.promo_id,
            )
            return self._execute_ev(fp, boost, boosted_decimal, fair_prob, stake, ev_usd, ev_pct)

        hedge_size = odds_boost_hedge_size(stake, boosted_decimal, no_price)
        locked = odds_boost_locked_profit(stake, boosted_decimal, no_price)

        order_id, fill_price = self._place_hedge(boost, hedge_size, no_price)
        action = "PLACED" if fill_price is not None else "SKIPPED_RISK"

        if action == "PLACED":
            self.mark_seen(fp)

        # Prefer locked profit as the EV when in locked mode
        effective_ev = locked if locked > 0 else ev_usd

        result = self._build_result(
            fp=fp, boost=boost,
            boosted_decimal=boosted_decimal,
            fair_prob=fair_prob,
            stake=stake,
            ev_usd=effective_ev,
            ev_pct=ev_pct,
            action=action,
            order_id=order_id,
            fill_price=fill_price,
            hedge_size=hedge_size,
            locked_profit=locked,
            no_price=no_price,
        )
        self.write_log(result)
        return result

    # ── Order placement ───────────────────────────────────────────────

    def _place_hedge(self, boost, hedge_size, no_price):
        """Place NO hedge at Kalshi or Polymarket. Returns (order_id, fill_price)."""
        if self.mode == "paper":
            return "paper-{}".format(boost.promo_id), no_price

        venue = boost.hedge_venue
        market_id = boost.hedge_market_id

        try:
            if venue == "kalshi" and self._kalshi_client:
                resp = self._kalshi_client.place_order(
                    ticker=market_id,
                    side="no",
                    count=int(hedge_size / no_price),
                    limit_price=no_price,
                )
                if resp and resp.get("status") == "filled":
                    return resp.get("order_id"), resp.get("fill_price", no_price)

            elif venue == "polymarket" and self._polymarket_client:
                resp = self._polymarket_client.place_order(
                    token_id=market_id,
                    side="NO",
                    amount=hedge_size,
                    price=no_price,
                )
                if resp and resp.success:
                    return resp.order_id, resp.fill_price

            logger.warning(
                "%s: no client for hedge venue=%s", POD_ID, venue
            )
            return None, None

        except Exception as exc:
            logger.error("%s: hedge placement failed: %s", POD_ID, exc)
            return None, None

    # ── Helpers ───────────────────────────────────────────────────────

    def _get_no_price(self, boost: PromoRecord) -> Optional[float]:
        """Get current NO price from client or configured price."""
        if boost.hedge_market_id and boost.hedge_venue == "kalshi" and self._kalshi_client:
            try:
                price = self._kalshi_client.get_price(boost.hedge_market_id, "no")
                if price is not None:
                    return price
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                logger.debug("P-010: Kalshi hedge price fetch failed: %s", exc)

        if boost.hedge_market_id and boost.hedge_venue == "polymarket" and self._polymarket_client:
            try:
                price = self._polymarket_client.get_price(boost.hedge_market_id, "NO")
                if price is not None:
                    return 1.0 - price
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                logger.debug("P-010: Polymarket hedge price fetch failed: %s", exc)

        return boost.hedge_no_price

    def _is_expired(self, boost: PromoRecord) -> bool:
        """Return True if boost has passed its expiry date."""
        if not boost.expiry:
            return False
        try:
            expiry = datetime.fromisoformat(boost.expiry).replace(tzinfo=timezone.utc)
            return self._now_fn() > expiry
        except (ValueError, TypeError):
            return False

    def _build_result(
        self,
        fp: str,
        boost: PromoRecord,
        boosted_decimal: float,
        fair_prob: float,
        stake: float,
        ev_usd: float,
        ev_pct: float,
        action: str,
        order_id: Optional[str] = None,
        fill_price: Optional[float] = None,
        skip_reason: Optional[str] = None,
        hedge_size: float = 0.0,
        locked_profit: float = 0.0,
        no_price: Optional[float] = None,
    ) -> ScanResult:
        return ScanResult(
            fingerprint=fp,
            timestamp_utc=self._now_fn().isoformat(),
            pod_id=POD_ID,
            pod_name=POD_NAME,
            mode=self.mode,
            venue=boost.sportsbook,
            market_type="promo_odds_boost",
            event="{} boost {} → {} on {}".format(
                boost.sportsbook,
                boost.original_odds or "?",
                boost.boosted_odds,
                boost.hedge_market_id or boost.promo_id,
            ),
            market_id=boost.promo_id,
            side="YES",                        # Boost is always on YES side
            fair_prob=fair_prob,
            venue_prob=1.0 / boosted_decimal,  # Implied prob at boosted odds
            edge_pct=ev_pct,
            ev=ev_usd,
            kelly_fraction=1.0,               # Always take +EV boost at max stake
            position_size_usd=stake,
            action=action,
            order_id=order_id,
            fill_price=fill_price,
            skip_reason=skip_reason,
            extra={
                "promo_id": boost.promo_id,
                "sportsbook": boost.sportsbook,
                "original_odds": boost.original_odds,
                "boosted_odds": boost.boosted_odds,
                "boosted_decimal": round(boosted_decimal, 4),
                "fair_yes_prob": fair_prob,
                "max_stake": boost.max_stake,
                "ev_pct": round(ev_pct, 4),
                "boost_mode": self.boost_mode,
                "hedge_size": hedge_size,
                "locked_profit": round(locked_profit, 2),
                "hedge_no_price": no_price,
            },
        )

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "BoostScannerPod":
        """Build from config dict.

        Config shape:
            pods:
              P-010:
                enabled: true
                boost_mode: ev
                min_ev_pct: 0.05
                boosts:
                  - promo_id: FD-BOOST-2026-01-15
                    sportsbook: fanduel
                    promo_type: odds_boost
                    amount: 25
                    boosted_odds: +350
                    fair_yes_prob: 0.38
                    max_stake: 25
                    hedge_venue: kalshi
                    hedge_market_id: CHIEFS-2026-01
                    hedge_no_price: 0.64
                    expiry: "2026-01-15"
                    status: pending
        """
        pod_cfg = config.get("pods", {}).get(POD_ID, {})
        paths_cfg = config.get("paths", {})

        raw_boosts = pod_cfg.get("boosts") or []
        boosts: List[PromoRecord] = []
        for rb in raw_boosts:
            try:
                boosts.append(PromoRecord(**{
                    k: v for k, v in rb.items()
                    if k in PromoRecord.__dataclass_fields__
                }))
            except Exception as exc:
                logger.warning("%s: bad boost config entry: %s", POD_ID, exc)

        return cls(
            boosts=overrides.get("boosts", boosts),
            kalshi_client=overrides.get("kalshi_client"),
            polymarket_client=overrides.get("polymarket_client"),
            boost_mode=pod_cfg.get("boost_mode", "ev"),
            min_ev_pct=pod_cfg.get("min_ev_pct", 0.05),
            trade_log_path=Path(
                paths_cfg.get("pod_logs", "data/pods")
            ) / "P-010.jsonl",
            mode=pod_cfg.get("environment", config.get("kalshi", {}).get("environment", "paper")),
        )
