"""
src/pods/kalshi_moneyline.py
────────────────────────────
P-001: Kalshi Moneyline Value pod.

Wraps the existing Scanner via delegation — preserving all 88
existing Scanner tests — while exposing the BasePod interface
for multi-pod orchestration.

scan_once() calls Scanner.scan_once(), then converts each dict
entry to a ScanResult.  The Scanner's own trade-log writing and
fingerprint dedup continue to operate internally; this wrapper
adds the ScanResult layer on top.
"""

from pathlib import Path
from typing import List, Optional

from src.base_pod import BasePod, ScanResult


def _clv_net_edge(entry, maker):
    """Net edge per contract (after Kalshi fee) for CLV observability; None if
    fair_prob/price unavailable."""
    fp = entry.get("fair_prob")
    price = entry.get("fill_price")
    if price is None:
        price = entry.get("kalshi_prob")
    if fp is None or price is None:
        return None
    try:
        from src.kalshi_fees import net_edge
        return round(net_edge(float(fp), float(price), maker=maker), 4)
    except Exception:
        return None
from src.pod_registry import register_pod


@register_pod("P-001")
class KalshiMoneylinePod(BasePod):
    """P-001: Existing Kalshi moneyline scanner as a BasePod.

    Constructor takes a pre-built Scanner instance and wraps it.
    All scanning logic is delegated to Scanner.scan_once().
    """

    def __init__(
        self,
        scanner,
        edge_calculator=None,
        risk_manager=None,
        trade_log_path=None,
        mode: str = "paper",
        _now_fn=None,
        trade_store=None,
        clv_gate=None,
    ):
        # Pull defaults from scanner if not provided
        ec = edge_calculator or getattr(scanner, "_edge", None) or getattr(scanner, "edge_calculator", None)
        rm = risk_manager or getattr(scanner, "_risk", None) or getattr(scanner, "risk_manager", None)
        log = trade_log_path or getattr(scanner, "_log_path", None) or Path("/tmp/p001_trade_log.jsonl")
        md = mode or getattr(scanner, "_mode", "paper")

        super().__init__(
            pod_id="P-001",
            pod_name="Kalshi vs Sharp's Strategy",
            edge_calculator=ec,
            risk_manager=rm,
            trade_log_path=log,
            mode=md,
            _now_fn=_now_fn,
            trade_store=trade_store,
        )
        self._scanner = scanner
        self._clv_gate = clv_gate or {}

    def _passes_clv_gate(self, entry) -> bool:
        """True if a PLACED candidate meets the validated MLB rule: underdog
        side (price < max_entry_price) AND positive net edge vs the de-vigged
        sharp fair, using the maker fee. Blocks when inputs are missing."""
        g = self._clv_gate
        fp = entry.get("fair_prob")
        price = entry.get("fill_price")
        if price is None:
            price = entry.get("kalshi_prob")
        if fp is None or price is None:
            return False
        if float(price) >= float(g.get("max_entry_price", 0.50)):
            return False
        from src.kalshi_fees import net_edge
        return net_edge(float(fp), float(price), maker=bool(g.get("maker", True))) > float(g.get("min_net_edge", 0.0))

    def scan_once(self) -> List[ScanResult]:
        """Delegate to Scanner.scan_once(), convert dicts → ScanResults."""
        raw_entries = self._scanner.scan_once()
        results = []
        for entry in raw_entries:
            # Skip if we already hold an open position on this market
            ticker = entry.get("market_ticker", "")
            if ticker and entry.get("action") == "PLACED" and self.has_open_position(ticker):
                continue
            result = ScanResult(
                fingerprint=entry["fingerprint"],
                timestamp_utc=entry["timestamp_utc"],
                pod_id=self.pod_id,
                pod_name=self.pod_name,
                mode=entry.get("mode", self.mode),
                venue="kalshi",
                market_type="sports_{}".format(entry.get("sport", "unknown")),
                event=entry.get("event", ""),
                market_id=entry.get("market_ticker", ""),
                side=entry.get("side", ""),
                fair_prob=entry.get("fair_prob", 0.0),
                venue_prob=entry.get("kalshi_prob", 0.0),
                edge_pct=entry.get("edge_pct", 0.0),
                ev=entry.get("ev", 0.0),
                kelly_fraction=entry.get("kelly_fraction", 0.0),
                position_size_usd=entry.get("position_size_usd", 0.0),
                action=entry.get("action", ""),
                order_id=entry.get("order_id"),
                fill_price=entry.get("fill_price"),
                skip_reason=entry.get("skip_reason"),
                extra={
                    k: v
                    for k, v in {
                        "yes_side": entry.get("yes_side"),
                        "home_team": entry.get("home_team"),
                        "away_team": entry.get("away_team"),
                        "game_time": entry.get("game_time"),
                        "sharp_fair_entry": entry.get("fair_prob"),
                        "entry_price": (entry.get("fill_price")
                                        if entry.get("fill_price") is not None
                                        else entry.get("kalshi_prob")),
                        "net_edge_maker": _clv_net_edge(entry, True),
                        "net_edge_taker": _clv_net_edge(entry, False),
                    }.items()
                    if v is not None
                },
            )
            if (self._clv_gate.get("enabled") and result.action == "PLACED"
                    and not self._passes_clv_gate(entry)):
                result.action = "SKIPPED_CLV_GATE"
                result.skip_reason = "clv_gate: not underdog+net-edge-maker"
            if result.action == "PLACED" and result.market_id:
                self.mark_position_open(result.market_id)
            results.append(result)
        return results

    def venue_name(self) -> str:
        return "kalshi"

    @classmethod
    def from_config(cls, config: dict, **overrides):
        """Build from config.  Requires scanner, edge_calculator, risk_manager
        to be passed via overrides (or pre-built externally).

        Typical usage:
            scanner = Scanner.from_config(config, kalshi, odds, matcher, edge, risk)
            pod = KalshiMoneylinePod.from_config(config, scanner=scanner)
        """
        scanner = overrides.get("scanner")
        if scanner is None:
            raise ValueError(
                "KalshiMoneylinePod.from_config requires 'scanner' in overrides"
            )
        mode_map = {"demo": "paper", "live": "live"}
        env = config.get("environment", "demo")
        mode = mode_map.get(env, "paper")

        return cls(
            scanner=scanner,
            edge_calculator=overrides.get("edge_calculator"),
            risk_manager=overrides.get("risk_manager"),
            trade_log_path=overrides.get("trade_log_path"),
            mode=mode,
            _now_fn=overrides.get("_now_fn"),
            trade_store=overrides.get("trade_store"),
            clv_gate=(config.get("pods", {}).get("P-001", {}) or {}).get("clv_gate"),
        )
