"""
src/pods/qualifier_favorite_pod.py
──────────────────────────────────
P-015: Tennis Qualifier Favorite pod.

Strategy (from tennis_research/REPORT.md §8b, Phase 0 extended backtest,
Jun 2025 – Jul 2026, n=6,814 pre-match observations):

  Kalshi's tennis match line is well calibrated tour-wide — EXCEPT in
  qualifying-round matches, where heavy favorites are systematically
  underpriced: favorites bought at ask ≥ 0.85 returned +4.1¢/contract
  net of taker fees (95.8% hit, n=238, bootstrap CI [+1.4, +6.3]),
  ATP stronger than WTA.  The inefficiency tracks attention: quals are
  the least-watched markets on the board.

Rules implemented here:
  * KXATPMATCH / KXWTAMATCH markets whose title marks a qualifying round
  * pre-match only (scheduled start still in the future)
  * buy the FAVORITE side at the ask when ask ∈ [floor, cap]
  * fair_prob = ask + edge_bump (calibration offset from the backtest;
    default is the conservative half of the CI, not the point estimate)
  * net edge after the quadratic taker fee must clear min_net_edge
  * per-trade size capped by displayed ask depth (we are the taker on a
    thin book; assume at most depth_haircut of what is shown)
  * hard cap on concurrent open positions (favorite losses are −ask on
    a ~94¢ contract; one bad session must not stack)

Validation plan: paper mode, sequential monitoring.  ~20 trades/month
expected.  Kill if realized hit rate drops below the ask-implied rate;
promote only if forward results match the retrospective +3–4¢.
"""

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.base_pod import BasePod, ScanResult
from src.kalshi_tennis_client import KalshiTennisClient, parse_dollars
from src.pod_registry import register_pod

logger = logging.getLogger(__name__)

QUAL_MARKERS = ("qualification", "qualifying", "qualifier")


def kalshi_taker_fee(price: float, rate: float = 0.07) -> float:
    """Kalshi taker fee per contract, rounded up to the cent."""
    raw = rate * price * (1.0 - price)
    return math.ceil(raw * 100.0) / 100.0


def _parse_start_time(market: Dict[str, Any]) -> Optional[datetime]:
    """Scheduled match start: occurrence_datetime, else expected_expiration.

    Verified on 1,612 dual-field Kalshi records: the two are identical
    when both are present.
    """
    raw = market.get("occurrence_datetime") or market.get("expected_expiration_time")
    if not raw:
        return None
    s = str(raw).replace("Z", "+00:00")
    if "." in s:  # tolerate odd-width fractional seconds
        head, tail = s.split(".", 1)
        tz = tail[tail.index("+"):] if "+" in tail else ""
        s = head + tz
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def is_qualifier(market: Dict[str, Any]) -> bool:
    text = " ".join(
        str(market.get(k) or "") for k in ("title", "rules_primary")
    ).lower()
    return any(marker in text for marker in QUAL_MARKERS)


@register_pod("P-015")
class QualifierFavoritePod(BasePod):
    """P-015: buy heavy favorites in tennis qualifying matches."""

    def __init__(
        self,
        kalshi_tennis_client: KalshiTennisClient,
        edge_calculator=None,
        risk_manager=None,
        trade_log_path: Optional[Path] = None,
        mode: str = "paper",
        ask_floor: float = 0.85,
        ask_cap: float = 0.975,
        edge_bump: float = 0.025,
        min_net_edge: float = 0.005,
        max_spread: float = 0.05,
        include_wta: bool = True,
        wta_size_mult: float = 0.5,
        max_open_positions: int = 6,
        slam_max_open_positions: Optional[int] = None,
        slam_windows: Optional[List[Any]] = None,
        depth_haircut: float = 0.5,
        min_lead_minutes: float = 10.0,
        _now_fn: Optional[Callable[[], datetime]] = None,
        aggregate_risk=None,
        trade_store=None,
    ):
        super().__init__(
            pod_id="P-015",
            pod_name="Tennis Qualifier Favorite",
            edge_calculator=edge_calculator,
            risk_manager=risk_manager,
            trade_log_path=trade_log_path or Path("data/pods/P-015.jsonl"),
            mode=mode,
            _now_fn=_now_fn,
            aggregate_risk=aggregate_risk,
            trade_store=trade_store,
        )
        self.client = kalshi_tennis_client
        self.ask_floor = ask_floor
        self.ask_cap = ask_cap
        self.edge_bump = edge_bump
        self.min_net_edge = min_net_edge
        self.max_spread = max_spread
        self.include_wta = include_wta
        self.wta_size_mult = wta_size_mult
        self.max_open_positions = max_open_positions
        # Unset → the elevated cap equals the base cap, i.e. windows change
        # nothing until both knobs are configured.
        self.slam_max_open_positions = (int(slam_max_open_positions)
                                        if slam_max_open_positions
                                        else max_open_positions)
        self.slam_windows = self._normalize_windows(slam_windows or [])
        self.depth_haircut = depth_haircut
        self.min_lead_minutes = min_lead_minutes
        self._open_count = 0

    @staticmethod
    def _normalize_windows(raw: List[Any]) -> List[tuple]:
        """Validate configured slam windows into (start, end) date-string pairs.

        Accepts ["YYYY-MM-DD", "YYYY-MM-DD"] pairs or {start:, end:} dicts.
        A malformed or inverted window is dropped with a warning rather than
        raised: a bad config line must degrade to the base cap, never take
        the pod down or silently widen it.
        """
        out: List[tuple] = []
        for w in raw:
            if isinstance(w, dict):
                start, end = w.get("start"), w.get("end")
            elif isinstance(w, (list, tuple)) and len(w) == 2:
                start, end = w
            else:
                logger.warning("P-015: ignoring malformed slam window %r", w)
                continue
            try:
                s = datetime.strptime(str(start), "%Y-%m-%d")
                e = datetime.strptime(str(end), "%Y-%m-%d")
            except (TypeError, ValueError):
                logger.warning("P-015: ignoring malformed slam window %r", w)
                continue
            if s > e:
                logger.warning("P-015: ignoring inverted slam window %r", w)
                continue
            out.append((str(start), str(end)))
        return out

    def _effective_max_open(self, now: datetime) -> int:
        """Concurrency cap for this scan — raised inside a slam qual window.

        Slam qualifying runs ~100+ matches over 5 days with many concurrent
        heavy favorites; the base cap plus settlement latency would throttle
        exactly the volume spike the 120-trade gate is counting on. Kalshi
        carries no field that marks a slam, so the windows are hand-listed
        UTC dates (inclusive) in config. Outside every window the base cap
        holds.
        """
        day = now.strftime("%Y-%m-%d")
        for start, end in self.slam_windows:
            if start <= day <= end:
                return self.slam_max_open_positions
        return self.max_open_positions

    # ── BasePod interface ────────────────────────────────────────────

    def venue_name(self) -> str:
        return "kalshi"

    def scan_once(self) -> List[ScanResult]:
        results: List[ScanResult] = []
        try:
            markets = self.client.get_open_match_markets()
        except Exception as exc:
            logger.error("P-015: market discovery failed: %s", exc)
            return []

        now = self._now_fn()
        n_qual = 0
        for m in markets:
            try:
                r = self._evaluate_market(m, now)
                if r is not None:
                    results.append(r)
                    n_qual += 1
            except Exception as exc:
                logger.error(
                    "P-015: error evaluating %s: %s", m.get("ticker", "?"), exc
                )
        logger.info(
            "P-015: scanned %d tennis markets, %d results", len(markets), n_qual
        )
        return results

    # ── Strategy ─────────────────────────────────────────────────────

    def _evaluate_market(
        self, m: Dict[str, Any], now: datetime
    ) -> Optional[ScanResult]:
        if not is_qualifier(m):
            return None

        series = m.get("_series") or ""
        tour = "WTA" if series == "KXWTAMATCH" else "ATP"
        if tour == "WTA" and not self.include_wta:
            return None

        # pre-match gate
        start = _parse_start_time(m)
        if start is None:
            return None
        lead_min = (start - now).total_seconds() / 60.0
        if lead_min < self.min_lead_minutes:
            return None  # in play or about to start

        bid = parse_dollars(m, "yes_bid_dollars")
        ask = parse_dollars(m, "yes_ask_dollars")
        if bid is None or ask is None:
            return None
        if ask - bid > self.max_spread:
            return None  # junk / defensive book

        # We only ever BUY YES on the favorite side.  Each match has two
        # markets (one per player); the favorite's own market is the one
        # whose ask is in our band.  The underdog's market never is.
        if not (self.ask_floor <= ask <= self.ask_cap):
            return None

        ticker = m.get("ticker", "")
        fee = kalshi_taker_fee(ask)
        fair_prob = min(ask + self.edge_bump, 0.995)
        net_edge = fair_prob - ask - fee
        if net_edge < self.min_net_edge:
            return self._log_skip(
                m, tour, ask, fair_prob, "SKIPPED_EDGE",
                f"net edge {net_edge:.3f} below {self.min_net_edge}",
            )

        # dedup / one position per market
        fp = self.make_fingerprint(ticker, "YES")
        if self.is_duplicate(fp) or self.has_open_position(ticker):
            return None

        cap = self._effective_max_open(now)
        if self._open_count >= cap:
            return self._log_skip(
                m, tour, ask, fair_prob, "SKIPPED_RISK",
                f"max_open_positions {cap} reached",
            )

        # sizing: Kelly for a binary buy at price a with win prob p is
        # (p - a) / (1 - a); apply fee to the effective price.
        a_eff = min(ask + fee, 0.999)
        kelly = max((fair_prob - a_eff) / (1.0 - a_eff), 0.0)
        size = self.compute_position_size(kelly)
        if tour == "WTA":
            size *= self.wta_size_mult

        # cap by displayed depth — thin books, honest taker assumption
        ask_size_contracts = 0.0
        v = m.get("yes_ask_size_fp")
        try:
            ask_size_contracts = float(v) if v not in (None, "") else 0.0
        except (TypeError, ValueError):
            pass
        if ask_size_contracts > 0:
            depth_usd = ask_size_contracts * ask * self.depth_haircut
            size = min(size, depth_usd)

        if size < 1.0:
            return self._log_skip(
                m, tour, ask, fair_prob, "SKIPPED_RISK", "size below $1 after caps",
            )

        if self.aggregate_risk is not None and not self.check_aggregate_risk(
            "kalshi", size
        ):
            return self._log_skip(
                m, tour, ask, fair_prob, "SKIPPED_RISK", "aggregate risk guard",
            )

        self.mark_seen(fp)
        self.mark_position_open(ticker)
        self._open_count += 1

        contracts = max(1, int(size / ask))
        ev = contracts * (fair_prob * 1.0 - ask - fee)

        result = ScanResult(
            fingerprint=fp,
            timestamp_utc=now.isoformat(),
            pod_id=self.pod_id,
            pod_name=self.pod_name,
            mode=self.mode,
            venue="kalshi",
            market_type="tennis_moneyline_qual",
            event=str(m.get("title") or ticker),
            market_id=ticker,
            side="YES",
            fair_prob=round(fair_prob, 4),
            venue_prob=ask,
            edge_pct=round((fair_prob - ask) / ask, 5),
            ev=round(ev, 2),
            kelly_fraction=round(kelly, 5),
            position_size_usd=round(size, 2),
            action="PLACED",
            fill_price=ask,  # paper: assume fill at displayed ask
            extra={
                "tour": tour,
                "series": series,
                "scheduled_start_utc": start.isoformat(),
                "lead_minutes": round(lead_min, 1),
                "bid": bid,
                "ask": ask,
                "spread": round(ask - bid, 4),
                "taker_fee": fee,
                "edge_bump": self.edge_bump,
                "ask_size_contracts": ask_size_contracts,
                "contracts": contracts,
            },
        )
        self.write_log(result)
        logger.info(
            "P-015: PLACED %s %s ask=%.2f size=$%.0f lead=%.0fm",
            tour, ticker, ask, size, lead_min,
        )
        return result

    def _log_skip(
        self,
        m: Dict[str, Any],
        tour: str,
        ask: float,
        fair_prob: float,
        action: str,
        reason: str,
    ) -> ScanResult:
        ticker = m.get("ticker", "")
        result = ScanResult(
            fingerprint=self.make_fingerprint(ticker, "SKIP"),
            timestamp_utc=self._now_fn().isoformat(),
            pod_id=self.pod_id,
            pod_name=self.pod_name,
            mode=self.mode,
            venue="kalshi",
            market_type="tennis_moneyline_qual",
            event=str(m.get("title") or ticker),
            market_id=ticker,
            side="YES",
            fair_prob=round(fair_prob, 4),
            venue_prob=ask,
            edge_pct=0.0,
            ev=0.0,
            kelly_fraction=0.0,
            position_size_usd=0.0,
            action=action,
            skip_reason=reason,
            extra={"tour": tour},
        )
        self.write_log(result)
        return result

    # settlement callback used by KalshiTennisSettler via engine loop
    def on_settlement(self, market_id: str) -> None:
        self.mark_position_closed(market_id)
        self._open_count = max(0, self._open_count - 1)

    # ── Construction ─────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "QualifierFavoritePod":
        pod_cfg = config.get("pods", {}).get("P-015", {}) or {}
        strat = pod_cfg.get("strategy", {}) or {}
        risk = pod_cfg.get("risk", {}) or {}

        client = overrides.get("kalshi_tennis_client") or \
            KalshiTennisClient.from_config(config)

        log_dir = Path(config.get("paths", {}).get("pod_logs", "data/pods"))
        log_path = overrides.get("trade_log_path") or log_dir / "P-015.jsonl"

        env = pod_cfg.get("environment", config.get("environment", "demo"))
        mode = {"demo": "paper", "live": "live"}.get(env, "paper")

        return cls(
            kalshi_tennis_client=client,
            edge_calculator=overrides.get("edge_calculator"),
            risk_manager=overrides.get("risk_manager"),
            trade_log_path=Path(log_path),
            mode=mode,
            ask_floor=float(strat.get("ask_floor", 0.85)),
            ask_cap=float(strat.get("ask_cap", 0.975)),
            edge_bump=float(strat.get("edge_bump", 0.025)),
            min_net_edge=float(strat.get("min_net_edge", 0.005)),
            max_spread=float(strat.get("max_spread", 0.05)),
            include_wta=bool(strat.get("include_wta", True)),
            wta_size_mult=float(strat.get("wta_size_mult", 0.5)),
            max_open_positions=int(risk.get("max_open_positions", 6)),
            slam_max_open_positions=(
                int(risk["slam_max_open_positions"])
                if risk.get("slam_max_open_positions") else None),
            slam_windows=risk.get("slam_windows"),
            depth_haircut=float(risk.get("depth_haircut", 0.5)),
            min_lead_minutes=float(strat.get("min_lead_minutes", 10.0)),
            _now_fn=overrides.get("_now_fn"),
            aggregate_risk=overrides.get("aggregate_risk"),
            trade_store=overrides.get("trade_store"),
        )
