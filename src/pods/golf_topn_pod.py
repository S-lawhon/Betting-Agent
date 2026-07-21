"""
src/pods/golf_topn_pod.py
─────────────────────────
P-017: Golf Top-N pre-tournament value pod (Kalshi, taker).

Strategy (backtest/REPORT_Golf_TopN_2026-07.md, H1-2026, n=926 bets /
10 tournaments):

  Mid-tier golfers priced 8-45c to finish top-10/20 are systematically
  UNDERPRICED on the "Wednesday" pre-tournament quote (4-10 days before
  the market closes). Buying YES at ask across the band returned
  +6.8c/contract NET of the Kalshi taker fee, 95% CI [+3.1c, +10.2c]
  (bootstrap clustered by tournament), 9 of 10 tournaments positive.

Two structural, model-free drivers (GOLF_KALSHI_RESEARCH.md):
  1. Tie/dead-heat inflation — Kalshi top-N pays in full on ties;
     real top-20 markets settled ~22.7 "yes" vs the nominal 20 (+13%
     mass). Anyone pricing from sportsbook (dead-heat-reduced) odds
     underprices the whole complex.
  2. Attention — pre-tournament flow concentrates on stars, leaving the
     mid-tier board unbought. (The lone negative backtest event was
     Travelers, a heavily-watched signature event — hence the optional
     signature-event skip.)

No model input required: fair_prob = min(ask + edge_bump, cap), where
edge_bump is the CONSERVATIVE lower-CI offset (0.04), NOT the point
estimate — same convention as P-015 (qualifier_favorite_pod). When a
DataGolf feed is later wired in, replace edge_bump with the model's
P(top-N incl. ties) and keep the same net-edge gate.

Rules:
  * KXPGATOP10 / KXPGATOP20 markets, pre-tournament only
    (days_to_close in [min_days, max_days])
  * two-sided quote, spread <= max_spread
  * buy YES at ask when ask in [ask_floor, ask_cap]
  * net edge after the quadratic TAKER fee must clear min_net_edge
  * per-trade size capped by displayed ask depth (taker on a retail book)
  * optional skip of signature-event weeks (attention exception)
  * hard cap on concurrent open positions

Validation plan: paper mode. Kill if realized hit rate / net edge falls
below half the backtest baseline over a rolling window (edges from
inattention can decay).

Expected volume — NOTE the cap interaction. The backtest averaged ~93
bets/tournament (926 over 10 events), and a live 3M Open board offered
174 names clearing the band. max_open_positions (40) therefore BINDS
hard: the pod owns roughly 40% of the validated strategy, chosen by
_select_candidates. Earlier docs claiming "~30-60 bets/tournament" were
wrong and are what the cap appears to have been sized against — revisit
the cap if the paper sample proves too thin for the 8-tournament gate.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.base_pod import BasePod, ScanResult
from src.kalshi_fees import fee_per_contract
from src.kalshi_public import KalshiPublic, fnum
from src.pod_registry import register_pod

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400.0
DEFAULT_SERIES = ("KXPGATOP10", "KXPGATOP20")


def _market_prices(m: Dict[str, Any]) -> Optional[tuple]:
    """(yes_bid, yes_ask) in dollars from a /markets record, or None.

    Handles both '*_dollars' string fields and cents ints.
    """
    b = fnum(m.get("yes_bid_dollars"))
    a = fnum(m.get("yes_ask_dollars"))
    if b is None:
        raw = fnum(m.get("yes_bid"))
        b = raw / 100.0 if raw is not None else None
    if a is None:
        raw = fnum(m.get("yes_ask"))
        a = raw / 100.0 if raw is not None else None
    if b is None or a is None or b <= 0 or a >= 1 or a < b:
        return None
    return b, a


def _close_epoch(m: Dict[str, Any]) -> Optional[float]:
    """Effective tournament-end epoch.

    Kalshi sets close_time / expiration_time to a far-future conservative
    date (can_close_early=True; the market really closes when the event
    ends). occurrence_datetime carries the actual event time, so prefer it
    — same field the tennis pod (P-015) keys on. Fall back to close_time.
    """
    raw = (m.get("occurrence_datetime") or m.get("close_time")
           or m.get("expected_expiration_time"))
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _event_code(m: Dict[str, Any]) -> str:
    ev = m.get("event_ticker") or ""
    parts = ev.split("-")
    return parts[1] if len(parts) >= 2 else ev


@register_pod("P-017")
class GolfTopNPod(BasePod):
    """P-017: buy underpriced mid-tier golfers in pre-tournament top-N."""

    def __init__(
        self,
        kalshi_public: Optional[KalshiPublic] = None,
        edge_calculator=None,
        risk_manager=None,
        trade_log_path: Optional[Path] = None,
        mode: str = "paper",
        series: tuple = DEFAULT_SERIES,
        ask_floor: float = 0.08,
        ask_cap: float = 0.45,
        edge_bump: float = 0.04,
        min_net_edge: float = 0.02,
        max_spread: float = 0.06,
        min_days_to_close: float = 4.0,
        max_days_to_close: float = 10.0,
        max_open_positions: int = 40,
        depth_haircut: float = 0.5,
        skip_events: Optional[List[str]] = None,
        fair_prob_cap: float = 0.75,
        datagolf_client=None,
        model_weight: float = 0.5,
        min_net_edge_model: float = 0.03,
        _now_fn: Optional[Callable[[], datetime]] = None,
        aggregate_risk=None,
        trade_store=None,
    ):
        super().__init__(
            pod_id="P-017",
            pod_name="Golf Top-N Pre-Tournament Value",
            edge_calculator=edge_calculator,
            risk_manager=risk_manager,
            trade_log_path=trade_log_path or Path("data/pods/P-017.jsonl"),
            mode=mode,
            _now_fn=_now_fn,
            aggregate_risk=aggregate_risk,
            trade_store=trade_store,
        )
        self.client = kalshi_public or KalshiPublic()
        self.series = tuple(series)
        self.ask_floor = ask_floor
        self.ask_cap = ask_cap
        self.edge_bump = edge_bump
        self.min_net_edge = min_net_edge
        self.max_spread = max_spread
        self.min_days_to_close = min_days_to_close
        self.max_days_to_close = max_days_to_close
        self.max_open_positions = max_open_positions
        self.depth_haircut = depth_haircut
        self.skip_events = {e.upper() for e in (skip_events or [])}
        self.fair_prob_cap = fair_prob_cap
        self.datagolf = datagolf_client
        self.model_weight = model_weight
        self.min_net_edge_model = min_net_edge_model
        self._open_count = 0

    # ── BasePod interface ────────────────────────────────────────────

    def venue_name(self) -> str:
        return "kalshi"

    def scan_once(self) -> List[ScanResult]:
        results: List[ScanResult] = []
        markets: List[Dict[str, Any]] = []
        for s in self.series:
            try:
                markets.extend(self.client.open_markets(s))
            except Exception as exc:
                logger.error("P-017: discovery failed for %s: %s", s, exc)

        # Resolve ONE close per event = the earliest occurrence across its
        # markets. Kalshi leaves close_time/occurrence at a far-future
        # conservative default on most top-N markets and stamps the true
        # event date on only a few; the per-event minimum recovers it and
        # applies uniformly (all top-N of one tournament close together).
        event_close = self._resolve_event_closes(markets)

        now = self._now_fn()

        # Pass 1 — price every market with no side effects.
        #
        # The band rule qualifies far more names than max_open_positions
        # allows (a live 3M Open board offered 174 candidates against a
        # cap of 40). Placing in API discovery order would spend the whole
        # cap on whichever tickers happened to come back first — in
        # practice all top-10, none top-20, even though top-20 is the
        # stronger leg in the backtest (+8.3c vs +4.9c). Rank first so the
        # truncation is deliberate rather than incidental.
        candidates: List[tuple] = []
        for m in markets:
            try:
                close = event_close.get(m.get("event_ticker") or "")
                priced = self._price_market(m, now, close)
                if priced is not None:
                    candidates.append((m, priced))
            except Exception as exc:
                logger.error("P-017: pricing error %s: %s",
                             m.get("ticker", "?"), exc)

        candidates = self._select_candidates(candidates)

        # Pass 2 — commit in selection order until the cap binds.
        n_hits = 0
        for m, priced in candidates:
            try:
                r = self._evaluate_market(m, now, priced["close"], priced=priced)
                if r is not None:
                    results.append(r)
                    if r.action == "PLACED":
                        n_hits += 1
            except Exception as exc:
                logger.error("P-017: eval error %s: %s", m.get("ticker", "?"), exc)

        if n_hits and len(candidates) > n_hits:
            logger.info(
                "P-017: %d candidates cleared the band, placed top %d by net "
                "edge (cap=%d)", len(candidates), n_hits,
                self.max_open_positions,
            )
        logger.info("P-017: scanned %d top-N markets, %d placed",
                    len(markets), n_hits)
        return results

    @staticmethod
    def _resolve_event_closes(markets: List[Dict[str, Any]]) -> Dict[str, float]:
        """event_ticker → earliest occurrence/close epoch across its markets."""
        out: Dict[str, float] = {}
        for m in markets:
            ev = m.get("event_ticker") or ""
            ep = _close_epoch(m)
            if ep is None:
                continue
            if ev not in out or ep < out[ev]:
                out[ev] = ep
        return out

    # ── Fair value ───────────────────────────────────────────────────

    def _fair_prob(self, m: Dict[str, Any], ticker: str, bid: float,
                   ask: float) -> tuple:
        """Return (fair_prob, source, min_net_edge_gate).

        Prefer a DataGolf model probability (blended toward the market mid
        to respect the realized<<modeled shrinkage documented in the
        research); fall back to the structural ask+edge_bump when no
        confident model match exists.
        """
        if self.datagolf is not None:
            try:
                model_p = self.datagolf.prob_for(
                    str(m.get("title") or ""), ticker)
            except Exception as exc:
                logger.debug("P-017: datagolf lookup failed %s: %s", ticker, exc)
                model_p = None
            if model_p is not None:
                mid = (bid + ask) / 2.0
                blended = self.model_weight * model_p + \
                    (1.0 - self.model_weight) * mid
                fair = min(blended, self.fair_prob_cap)
                return fair, "datagolf", self.min_net_edge_model
        fair = min(ask + self.edge_bump, self.fair_prob_cap)
        return fair, "structural", self.min_net_edge

    # ── Strategy ─────────────────────────────────────────────────────

    def _price_market(self, m: Dict[str, Any], now: datetime,
                      close: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Structural gates + pricing for one market. NO side effects.

        Returns None when the market is not tradeable at all (wrong
        window, no two-sided quote, outside the band). Otherwise returns
        the pricing dict — including markets whose net edge misses the
        gate, so the caller can still log them as SKIPPED_EDGE.

        Split out from _evaluate_market so scan_once can rank the whole
        candidate set by net edge BEFORE spending the position cap.
        """
        ticker = m.get("ticker", "")
        if not ticker:
            return None

        # signature-event skip (attention exception)
        if _event_code(m) in self.skip_events:
            return None

        # pre-tournament window gate (event-level close resolved by caller)
        if close is None:
            close = _close_epoch(m)
        if close is None:
            return None
        days_to_close = (close - now.timestamp()) / SECONDS_PER_DAY
        if not (self.min_days_to_close <= days_to_close <= self.max_days_to_close):
            return None

        prices = _market_prices(m)
        if prices is None:
            return None
        bid, ask = prices
        if (ask - bid) > self.max_spread:
            return None
        if not (self.ask_floor <= ask <= self.ask_cap):
            return None

        fee = fee_per_contract(ask, maker=False)  # quadratic taker
        fair_prob, source, gate = self._fair_prob(m, ticker, bid, ask)
        return {
            "ticker": ticker, "close": close, "days_to_close": days_to_close,
            "bid": bid, "ask": ask, "fee": fee, "fair_prob": fair_prob,
            "source": source, "gate": gate,
            "net": fair_prob - ask - fee,
        }

    def _select_candidates(self, candidates: List[tuple]) -> List[tuple]:
        """Order candidates so the position cap truncates deliberately.

        The band rule qualifies far more names than max_open_positions
        allows (a live 3M Open board offered 174 against a cap of 40), so
        SOMETHING decides which ones get bought. Discovery order is the
        wrong answer — it spent the whole cap on top-10 and skipped
        top-20 entirely, despite top-20 being the stronger backtest leg.

        Ranking by net edge is ALSO wrong in structural mode. With a
        constant edge_bump, net = edge_bump - fee(ask), and the quadratic
        fee rises with ask, so net edge decreases monotonically in price:
        sorting by it is exactly "buy the cheapest names". That is a
        different, higher-variance strategy than the one that returned
        +6.8c — the backtest bought the band uniformly, not its cheap
        tail.

        So:
          * structural mode — no per-name quality signal exists (every
            name in the band carries the same thesis-implied edge), so
            take a systematic sample spread evenly across the ask range,
            stratified by series. This preserves the backtest's band and
            series composition under a cap. Deterministic, no RNG.
          * model mode (DataGolf live) — fair_prob genuinely varies by
            golfer, so net edge IS a quality signal and we rank by it.
        """
        slots = self.max_open_positions - self._open_count
        if slots <= 0 or len(candidates) <= slots:
            return candidates

        model_priced = sum(
            1 for _, p in candidates if p["source"] == "datagolf"
        )
        if model_priced > len(candidates) / 2:
            ranked = sorted(candidates, key=lambda c: c[1]["net"], reverse=True)
            logger.info(
                "P-017: %d candidates, %d slots — ranking by model net edge",
                len(candidates), slots,
            )
            return ranked

        # Systematic sample across the ask range, per series.
        by_series: Dict[str, List[tuple]] = {}
        for m, p in candidates:
            by_series.setdefault(p["ticker"].split("-")[0], []).append((m, p))

        picked: List[tuple] = []
        total = len(candidates)
        for series, group in sorted(by_series.items()):
            group.sort(key=lambda c: c[1]["ask"])
            # proportional allocation, at least one slot per series
            k = max(1, round(slots * len(group) / total))
            k = min(k, len(group))
            step = len(group) / k
            picked.extend(group[int(i * step)] for i in range(k))

        logger.info(
            "P-017: %d candidates, %d slots — systematic band sample "
            "(structural mode, no per-name signal to rank on)",
            len(candidates), slots,
        )
        return picked

    def _evaluate_market(self, m: Dict[str, Any], now: datetime,
                         close: Optional[float] = None,
                         priced: Optional[Dict[str, Any]] = None,
                         ) -> Optional[ScanResult]:
        if priced is None:
            priced = self._price_market(m, now, close)
        if priced is None:
            return None

        ticker = priced["ticker"]
        close = priced["close"]
        days_to_close = priced["days_to_close"]
        bid, ask = priced["bid"], priced["ask"]
        fee, fair_prob = priced["fee"], priced["fair_prob"]
        source, gate, net = priced["source"], priced["gate"], priced["net"]

        if net < gate:
            return self._skip(m, ask, fair_prob, "SKIPPED_EDGE",
                              f"net {net:.3f} < {gate} ({source})")

        fp = self.make_fingerprint(ticker, "YES")
        if self.is_duplicate(fp) or self.has_open_position(ticker):
            return None
        if self._open_count >= self.max_open_positions:
            return self._skip(m, ask, fair_prob, "SKIPPED_RISK",
                              f"max_open_positions {self.max_open_positions}")

        # Kelly for a binary buy at effective price (ask + fee)
        a_eff = min(ask + fee, 0.999)
        kelly = max((fair_prob - a_eff) / (1.0 - a_eff), 0.0)
        size = self.compute_position_size(kelly)

        # depth cap — taker on a retail-facing book
        ask_ct = fnum(m.get("yes_ask_size_fp")) or fnum(m.get("yes_ask_size")) or 0.0
        if ask_ct > 0:
            size = min(size, ask_ct * ask * self.depth_haircut)
        if size < 1.0:
            return self._skip(m, ask, fair_prob, "SKIPPED_RISK",
                              "size < $1 after caps")

        if self.aggregate_risk is not None and not self.check_aggregate_risk(
            "kalshi", size
        ):
            return self._skip(m, ask, fair_prob, "SKIPPED_RISK",
                              "aggregate risk guard")

        self.mark_seen(fp)
        self.mark_position_open(ticker)
        self._open_count += 1

        contracts = max(1, int(size / ask))
        ev = contracts * net

        result = ScanResult(
            fingerprint=fp,
            timestamp_utc=now.isoformat(),
            pod_id=self.pod_id,
            pod_name=self.pod_name,
            mode=self.mode,
            venue="kalshi",
            market_type="golf_topn",
            event=str(m.get("title") or ticker),
            market_id=ticker,
            side="YES",
            fair_prob=round(fair_prob, 4),
            venue_prob=ask,
            edge_pct=round((fair_prob - ask) / ask, 5) if ask else 0.0,
            ev=round(ev, 2),
            kelly_fraction=round(kelly, 5),
            position_size_usd=round(size, 2),
            action="PLACED",
            fill_price=ask,  # paper: assume fill at displayed ask
            extra={
                "series": ticker.split("-")[0],
                "event_code": _event_code(m),
                "days_to_close": round(days_to_close, 2),
                # absolute event close — the settler's stale guard needs a
                # fixed reference, not one relative to placement time
                "close_utc": datetime.fromtimestamp(
                    close, tz=timezone.utc).isoformat(),
                "bid": bid, "ask": ask, "spread": round(ask - bid, 4),
                "taker_fee": round(fee, 4),
                "edge_bump": self.edge_bump,
                "net_edge": round(net, 4),
                "fair_source": source,
                "contracts": contracts,
                # CLV/settler compatibility
                "kalshi_yes_side": "YES",
            },
        )
        self.write_log(result)
        logger.info("P-017: PLACED %s ask=%.2f size=$%.0f d2c=%.1f",
                    ticker, ask, size, days_to_close)
        return result

    def _skip(self, m: Dict[str, Any], ask: float, fair_prob: float,
              action: str, reason: str) -> ScanResult:
        ticker = m.get("ticker", "")
        r = ScanResult(
            fingerprint=self.make_fingerprint(ticker, "SKIP"),
            timestamp_utc=self._now_fn().isoformat(),
            pod_id=self.pod_id, pod_name=self.pod_name, mode=self.mode,
            venue="kalshi", market_type="golf_topn",
            event=str(m.get("title") or ticker), market_id=ticker, side="YES",
            fair_prob=round(fair_prob, 4), venue_prob=ask, edge_pct=0.0,
            ev=0.0, kelly_fraction=0.0, position_size_usd=0.0,
            action=action, skip_reason=reason,
            extra={"event_code": _event_code(m)},
        )
        self.write_log(r)
        return r

    def on_settlement(self, market_id: str) -> None:
        self.mark_position_closed(market_id)
        self._open_count = max(0, self._open_count - 1)

    # ── Construction ─────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict, **overrides) -> "GolfTopNPod":
        cfg = (config.get("pods", {}) or {}).get("P-017", {}) or {}
        strat = cfg.get("strategy", {}) or {}
        risk = cfg.get("risk", {}) or {}

        log_dir = Path(config.get("paths", {}).get("pod_logs", "data/pods"))
        log_path = overrides.get("trade_log_path") or log_dir / "P-017.jsonl"
        env = cfg.get("environment", config.get("environment", "demo"))
        mode = {"demo": "paper", "live": "live"}.get(env, "paper")

        # Optional DataGolf model feed (None if disabled / no key)
        dg = overrides.get("datagolf_client")
        if dg is None:
            try:
                from src.datagolf_client import DataGolfClient
                dg = DataGolfClient.from_config(config)
            except Exception as exc:  # never let the feed break pod construction
                logger.warning("P-017: DataGolf init skipped: %s", exc)
                dg = None

        return cls(
            kalshi_public=overrides.get("kalshi_public"),
            edge_calculator=overrides.get("edge_calculator"),
            risk_manager=overrides.get("risk_manager"),
            trade_log_path=Path(log_path),
            mode=mode,
            series=tuple(strat.get("series", DEFAULT_SERIES)),
            ask_floor=float(strat.get("ask_floor", 0.08)),
            ask_cap=float(strat.get("ask_cap", 0.45)),
            edge_bump=float(strat.get("edge_bump", 0.04)),
            min_net_edge=float(strat.get("min_net_edge", 0.02)),
            max_spread=float(strat.get("max_spread", 0.06)),
            min_days_to_close=float(strat.get("min_days_to_close", 4.0)),
            max_days_to_close=float(strat.get("max_days_to_close", 10.0)),
            max_open_positions=int(risk.get("max_open_positions", 40)),
            depth_haircut=float(risk.get("depth_haircut", 0.5)),
            skip_events=strat.get("skip_events", []),
            fair_prob_cap=float(strat.get("fair_prob_cap", 0.75)),
            datagolf_client=dg,
            model_weight=float(strat.get("model_weight", 0.5)),
            min_net_edge_model=float(strat.get("min_net_edge_model", 0.03)),
            _now_fn=overrides.get("_now_fn"),
            aggregate_risk=overrides.get("aggregate_risk"),
            trade_store=overrides.get("trade_store"),
        )
