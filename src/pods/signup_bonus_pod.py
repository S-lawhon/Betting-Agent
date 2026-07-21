"""
src/pods/signup_bonus_pod.py
─────────────────────────────
P-009: Sign-Up Bonus Blitz pod.

Strategy
─────────
Sportsbooks issue free bets and risk-free bets as acquisition bonuses.
These have a deterministic positive expected value because we hedge the
sportsbook bet at Kalshi/Polymarket:

  1. Manually place the free bet at FanDuel/DraftKings on event A (YES).
  2. This pod calculates the optimal hedge on NO at Kalshi/Polymarket.
  3. It places the hedge (the automated part) and logs the locked profit.

Execution split
───────────────
- Sportsbook bet: manual (no programmatic API)
- Hedge execution: automated via kalshi_client or polymarket_client

Workflow
────────
Each configured promo in the "promo" list goes through:
  1. Check status == "pending" and not expired
  2. Look up current NO price at hedge venue (or use configured price)
  3. Run free_bet_hedge_size() and free_bet_conversion_rate()
  4. If conversion_rate >= min_conversion_rate → place hedge → PLACED
  5. Otherwise → SKIPPED_EDGE (conversion too low, wait for better odds)

Config example (in config_multi_pod.yaml)
──────────────────────────────────────────
  P-009:
    enabled: true
    min_conversion_rate: 0.60   # Minimum 60% free-bet conversion
    promos:
      - promo_id: FD-FB-2026-01
        sportsbook: fanduel
        promo_type: free_bet
        amount: 100
        boosted_odds: +200
        fair_yes_prob: 0.40
        hedge_venue: kalshi
        hedge_market_id: CHIEFS-2026-01
        hedge_no_price: 0.62
        expiry: "2026-01-20"
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
    free_bet_conversion_rate,
    free_bet_guaranteed_profit,
    free_bet_hedge_size,
    risk_free_ev,
)

logger = logging.getLogger(__name__)

from src.pod_registry import register_pod

POD_ID = "P-009"
POD_NAME = "Sign-Up Bonus Blitz"


@register_pod("P-009")
class SignupBonusPod(BasePod):
    """Tracks and hedges sportsbook sign-up bonuses.

    Args:
        promos:            List of PromoRecord (injected for testing).
        kalshi_client:     Kalshi client for hedge execution.
        polymarket_client: Polymarket client for hedge execution.
        min_conversion_rate: Minimum free-bet conversion rate to place hedge.
        trade_log_path:    Path to JSONL trade log.
        mode:              "paper" or "live".
    """

    def __init__(
        self,
        promos: List[PromoRecord],
        kalshi_client=None,
        polymarket_client=None,
        min_conversion_rate: float = 0.60,
        trade_log_path: Path = Path("data/pods/P-009.jsonl"),
        mode: str = "paper",
        _now_fn: Optional[Callable] = None,
    ):
        # BasePod requires edge_calculator + risk_manager; promos don't need them
        # Pass lightweight stubs so we can inherit shared infra without coupling.
        super().__init__(
            pod_id=POD_ID,
            pod_name=POD_NAME,
            edge_calculator=None,
            risk_manager=None,
            trade_log_path=trade_log_path,
            mode=mode,
            _now_fn=_now_fn,
        )
        self._promos = promos
        self._kalshi_client = kalshi_client
        self._polymarket_client = polymarket_client
        self.min_conversion_rate = min_conversion_rate

    # ── BasePod interface ────────────────────────────────────────────

    @property
    def venue_name(self) -> str:  # type: ignore[override]
        return "multi"

    def scan_once(self) -> List[ScanResult]:
        """Process pending promos and place hedges where viable."""
        results: List[ScanResult] = []

        for promo in self._promos:
            if not promo.is_active():
                continue
            if self._is_expired(promo):
                logger.info(
                    "%s: promo %s expired, skipping", POD_ID, promo.promo_id
                )
                continue

            try:
                result = self._evaluate_promo(promo)
                if result:
                    results.append(result)
            except Exception as exc:
                logger.error(
                    "%s: error evaluating promo %s: %s",
                    POD_ID, promo.promo_id, exc,
                )

        return results

    # ── Promo evaluation ─────────────────────────────────────────────

    def _evaluate_promo(self, promo: PromoRecord) -> Optional[ScanResult]:
        """Evaluate one promo and return a ScanResult."""
        if promo.promo_type in ("free_bet", "risk_free"):
            return self._evaluate_free_bet(promo)
        elif promo.promo_type == "odds_boost":
            return None  # P-010 handles boosts
        else:
            logger.debug("%s: unknown promo type %s", POD_ID, promo.promo_type)
            return None

    def _evaluate_free_bet(self, promo: PromoRecord) -> Optional[ScanResult]:
        """Evaluate a free_bet or risk_free promo."""
        # Resolve hedge venue price
        no_price = self._get_no_price(promo)
        if no_price is None:
            logger.warning(
                "%s: no hedge price for promo %s — skipping", POD_ID, promo.promo_id
            )
            return None

        # Get sportsbook decimal odds
        if promo.boosted_odds is None:
            logger.warning(
                "%s: promo %s missing boosted_odds", POD_ID, promo.promo_id
            )
            return None

        decimal_odds_sb = american_to_decimal(promo.boosted_odds)
        fair_prob = promo.fair_yes_prob or 0.5

        # Calculate conversion metrics
        conv_rate = free_bet_conversion_rate(decimal_odds_sb, no_price)
        hedge_size = free_bet_hedge_size(promo.amount, decimal_odds_sb, no_price)
        locked_profit = free_bet_guaranteed_profit(promo.amount, decimal_odds_sb, no_price)

        fp = self.make_fingerprint(promo.promo_id, "HEDGE")

        if self.is_duplicate(fp):
            logger.debug("%s: promo %s already hedged this hour", POD_ID, promo.promo_id)
            return None

        # Decision: is conversion rate sufficient?
        if conv_rate < self.min_conversion_rate:
            result = self._build_result(
                fp=fp, promo=promo,
                decimal_odds_sb=decimal_odds_sb,
                fair_prob=fair_prob,
                no_price=no_price,
                conv_rate=conv_rate,
                hedge_size=hedge_size,
                locked_profit=locked_profit,
                action="SKIPPED_EDGE",
                skip_reason="conversion_rate={:.1%} < min={:.1%}".format(
                    conv_rate, self.min_conversion_rate
                ),
            )
            self.write_log(result)
            return result

        # Place the hedge
        order_id, fill_price = self._place_hedge(promo, hedge_size, no_price)

        action = "PLACED" if fill_price is not None else "SKIPPED_RISK"
        if action == "PLACED":
            self.mark_seen(fp)

        result = self._build_result(
            fp=fp, promo=promo,
            decimal_odds_sb=decimal_odds_sb,
            fair_prob=fair_prob,
            no_price=no_price,
            conv_rate=conv_rate,
            hedge_size=hedge_size,
            locked_profit=locked_profit,
            action=action,
            order_id=order_id,
            fill_price=fill_price,
        )
        self.write_log(result)
        return result

    # ── Order placement ───────────────────────────────────────────────

    def _place_hedge(
        self,
        promo: PromoRecord,
        hedge_size: float,
        no_price: float,
    ):
        """Place the hedge order. Returns (order_id, fill_price) or (None, None)."""
        venue = promo.hedge_venue
        market_id = promo.hedge_market_id

        if self.mode == "paper":
            # Paper: simulate immediate fill at no_price
            return "paper-{}".format(promo.promo_id), no_price

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
                "%s: no client available for hedge venue=%s", POD_ID, venue
            )
            return None, None

        except Exception as exc:
            logger.error("%s: hedge placement failed: %s", POD_ID, exc)
            return None, None

    # ── Helpers ───────────────────────────────────────────────────────

    def _get_no_price(self, promo: PromoRecord) -> Optional[float]:
        """Get current NO price from client or fall back to configured price."""
        if promo.hedge_market_id and promo.hedge_venue == "kalshi" and self._kalshi_client:
            try:
                price = self._kalshi_client.get_price(promo.hedge_market_id, "no")
                if price is not None:
                    return price
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                logger.debug("P-009: Kalshi hedge price fetch failed: %s", exc)

        if promo.hedge_market_id and promo.hedge_venue == "polymarket" and self._polymarket_client:
            try:
                price = self._polymarket_client.get_price(promo.hedge_market_id, "NO")
                if price is not None:
                    return 1.0 - price  # Polymarket returns YES price
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                logger.debug("P-009: Polymarket hedge price fetch failed: %s", exc)

        # Fall back to configured price
        return promo.hedge_no_price

    def _is_expired(self, promo: PromoRecord) -> bool:
        """Check if the promo's expiry date has passed."""
        if not promo.expiry:
            return False
        try:
            expiry = datetime.fromisoformat(promo.expiry).replace(tzinfo=timezone.utc)
            return self._now_fn() > expiry
        except (ValueError, TypeError):
            return False

    def _build_result(
        self,
        fp: str,
        promo: PromoRecord,
        decimal_odds_sb: float,
        fair_prob: float,
        no_price: float,
        conv_rate: float,
        hedge_size: float,
        locked_profit: float,
        action: str,
        order_id: Optional[str] = None,
        fill_price: Optional[float] = None,
        skip_reason: Optional[str] = None,
    ) -> ScanResult:
        return ScanResult(
            fingerprint=fp,
            timestamp_utc=self._now_fn().isoformat(),
            pod_id=POD_ID,
            pod_name=POD_NAME,
            mode=self.mode,
            venue=promo.hedge_venue,
            market_type="promo_free_bet",
            event="{} {} {} @ {}".format(
                promo.sportsbook, promo.promo_type,
                promo.promo_id, promo.hedge_market_id or "?",
            ),
            market_id=promo.promo_id,
            side="NO",                         # Hedge is always on NO
            fair_prob=fair_prob,
            venue_prob=no_price,
            edge_pct=conv_rate,
            ev=locked_profit,
            kelly_fraction=1.0,                # Always take positive-conversion free bets
            position_size_usd=hedge_size,
            action=action,
            order_id=order_id,
            fill_price=fill_price,
            skip_reason=skip_reason,
            extra={
                "promo_id": promo.promo_id,
                "sportsbook": promo.sportsbook,
                "promo_type": promo.promo_type,
                "promo_amount": promo.amount,
                "sportsbook_odds": promo.boosted_odds,
                "decimal_odds_sb": round(decimal_odds_sb, 4),
                "hedge_venue": promo.hedge_venue,
                "hedge_market_id": promo.hedge_market_id,
                "hedge_size": hedge_size,
                "locked_profit": locked_profit,
                "conversion_rate": round(conv_rate, 4),
            },
        )

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "SignupBonusPod":
        """Build from config dict.

        Config shape:
            pods:
              P-009:
                enabled: true
                min_conversion_rate: 0.60
                promos:
                  - promo_id: FD-FB-2026-01
                    sportsbook: fanduel
                    promo_type: free_bet
                    amount: 100
                    boosted_odds: 200
                    fair_yes_prob: 0.40
                    hedge_venue: kalshi
                    hedge_market_id: CHIEFS-2026-01
                    hedge_no_price: 0.62
                    expiry: "2026-01-20"
                    status: pending
        """
        pod_cfg = config.get("pods", {}).get(POD_ID, {})
        paths_cfg = config.get("paths", {})

        raw_promos = pod_cfg.get("promos") or []
        promos: List[PromoRecord] = []
        for rp in raw_promos:
            try:
                promos.append(PromoRecord(**{
                    k: v for k, v in rp.items()
                    if k in PromoRecord.__dataclass_fields__
                }))
            except Exception as exc:
                logger.warning("%s: bad promo config entry: %s", POD_ID, exc)

        return cls(
            promos=overrides.get("promos", promos),
            kalshi_client=overrides.get("kalshi_client"),
            polymarket_client=overrides.get("polymarket_client"),
            min_conversion_rate=pod_cfg.get("min_conversion_rate", 0.60),
            trade_log_path=Path(
                paths_cfg.get("pod_logs", "data/pods")
            ) / "P-009.jsonl",
            mode=pod_cfg.get("environment", config.get("kalshi", {}).get("environment", "paper")),
        )
