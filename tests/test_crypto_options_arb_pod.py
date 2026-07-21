"""
tests/test_crypto_options_arb_pod.py
────────────────────────────────────
Tests for P-013 CryptoOptionsArbPod.

  ✓ pod_id, pod_name, venue_name
  ✓ scan_once: signal found → PLACED
  ✓ scan_once: no Kalshi contracts → empty
  ✓ scan_once: no Deribit chains → empty
  ✓ scan_once: edge below threshold → no signals
  ✓ scan_once: net edge below threshold (after fees) → no signals
  ✓ scan_once: dedup prevents double placement
  ✓ scan_once: max_contracts_per_cycle caps placements
  ✓ _extract_strike: ticker format
  ✓ _extract_strike: title with dollar sign
  ✓ _extract_strike: title with $Xk format
  ✓ _evaluate_contract: YES side when Kalshi underprices
  ✓ _evaluate_contract: NO side when Kalshi overprices
  ✓ fee calculation: maker vs taker
  ✓ from_config wiring
  ✓ extra fields contain pricing metadata
"""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.base_pod import ScanResult
from src.deribit_client import OptionInstrument, OptionsChain
from src.crypto_options_pricer import CryptoOptionsPricer, BinaryProbEstimate
from src.pods.crypto_options_arb import (
    CryptoOptionsArbPod,
    KalshiCryptoContract,
    CryptoArbSignal,
    KALSHI_MAKER_FEE_MULT,
    KALSHI_TAKER_FEE_MULT,
)

_NOW = datetime(2026, 3, 26, 12, 0, 0, tzinfo=timezone.utc)


# ── Mock Clients ──────────────────────────────────────────────────────

class _MockKalshi:
    """Mock Kalshi client returning BTC crypto markets."""

    def __init__(self, markets=None):
        self._markets = markets if markets is not None else []
        self.orders = []

    def list_markets(self, status="open", limit=200, cursor=None, series_ticker=None):
        if series_ticker:
            filtered = [
                m for m in self._markets
                if m.get("ticker", "").upper().startswith(series_ticker.upper())
            ]
            return {"markets": filtered, "cursor": None}
        return {"markets": self._markets, "cursor": None}

    def get_market(self, ticker):
        for m in self._markets:
            if m["ticker"] == ticker:
                return m
        return None

    def place_order(self, ticker, side, yes_price=None, no_price=None, count=1):
        self.orders.append({
            "ticker": ticker, "side": side,
            "yes_price": yes_price, "no_price": no_price, "count": count,
        })
        class _R:
            order_id = f"PAPER-K-{ticker}-{side}"
            fill_price = (yes_price or no_price or 50) / 100.0
        return _R()


class _MockDeribit:
    """Mock Deribit client returning pre-built options chain."""

    def __init__(self, chains=None, index_price=87000):
        self._chains = chains or []
        self._index_price = index_price

    def get_index_price(self, currency="BTC"):
        return self._index_price

    def get_all_chains(self, currency="BTC"):
        return self._chains


class _MockPricer:
    """Mock pricer returning configurable probabilities."""

    def __init__(self, prob=0.20, confidence=0.85, method="ensemble"):
        self._prob = prob
        self._confidence = confidence
        self._method = method

    def estimate_probability(self, chain, strike, time_to_expiry_years=None):
        return BinaryProbEstimate(
            strike=strike,
            expiry_str=chain.expiry_str,
            underlying_price=chain.underlying_price,
            probability=self._prob,
            method=self._method,
            confidence=self._confidence,
            spread_prob=self._prob,
            bsm_prob=self._prob,
        )

    def estimate_probability_below(self, chain, strike, time_to_expiry_years=None):
        est = self.estimate_probability(chain, strike, time_to_expiry_years)
        return BinaryProbEstimate(
            strike=est.strike,
            expiry_str=est.expiry_str,
            underlying_price=est.underlying_price,
            probability=1.0 - est.probability,
            method=est.method,
            confidence=est.confidence,
        )


class _MockEdge:
    pass


class _MockRisk:
    max_bet_pct = 0.03
    max_position_usd = 300.0
    class _L:
        bankroll = 10000.0
    ledger = _L()


# ── Fixtures ──────────────────────────────────────────────────────────

def _btc_chain():
    """Minimal BTC options chain for testing."""
    return OptionsChain(
        underlying="BTC",
        expiry_str="28MAR26",
        expiry_ts=1743120000,
        underlying_price=87000,
        instruments=[
            OptionInstrument(
                "BTC-28MAR26-90000-C", "BTC", 90000, 1743120000, "28MAR26",
                "call", 3000, 0.65, 2850, 3150, 500, 100, 0.40, 0.00001, 50, -10, 87000,
            ),
            OptionInstrument(
                "BTC-28MAR26-100000-C", "BTC", 100000, 1743120000, "28MAR26",
                "call", 700, 0.75, 630, 770, 800, 200, 0.15, 0.000008, 40, -8, 87000,
            ),
        ],
    )


def _kalshi_market(ticker="KXBTCM-26MAR28-B100000", title="Bitcoin above $100,000?",
                   yes_bid=25, yes_ask=29, expiry="2026-03-28",
                   floor_strike=None, strike_type="greater"):
    """Create a mock Kalshi market dict with both legacy and v2 fields."""
    # Extract strike from ticker for floor_strike default
    import re
    if floor_strike is None:
        m = re.search(r"-[TB](\d+(?:\.\d+)?)", ticker)
        floor_strike = float(m.group(1)) if m else 100000
    return {
        "ticker": ticker,
        "title": title,
        # Legacy integer cents
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        # v2 dollar decimals
        "yes_bid_dollars": f"{yes_bid / 100.0:.4f}" if yes_bid is not None else None,
        "yes_ask_dollars": f"{yes_ask / 100.0:.4f}" if yes_ask is not None else None,
        "expiration_time": f"{expiry}T16:00:00Z",
        "close_time": f"{expiry}T16:00:00Z",
        "status": "open",
        "strike_type": strike_type,
        "floor_strike": floor_strike,
    }


def _make_pod(
    kalshi_markets=None,
    deribit_chains=None,
    pricer_prob=0.20,
    pricer_confidence=0.85,
    min_edge_pct=0.07,
    min_net_edge_pct=0.03,
    max_contracts=5,
    max_plausible_edge=0.20,
):
    """Build a P-013 pod with mocked dependencies."""
    kalshi = _MockKalshi(kalshi_markets or [])
    deribit = _MockDeribit(
        deribit_chains if deribit_chains is not None else [_btc_chain()],
        index_price=87000,
    )
    pricer = _MockPricer(prob=pricer_prob, confidence=pricer_confidence)

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        log_path = Path(f.name)

    pod = CryptoOptionsArbPod(
        kalshi_client=kalshi,
        deribit_client=deribit,
        pricer=pricer,
        edge_calculator=_MockEdge(),
        risk_manager=_MockRisk(),
        min_edge_pct=min_edge_pct,
        min_net_edge_pct=min_net_edge_pct,
        max_contracts_per_cycle=max_contracts,
        kelly_multiplier=0.25,
        use_maker_orders=True,
        max_plausible_edge=max_plausible_edge,
        trade_log_path=log_path,
        mode="paper",
        _now_fn=lambda: _NOW,
        min_kelly_fraction=0.005,  # Low floor for testing (same as P-006/P-002)
    )
    return pod, kalshi


# ── Tests ─────────────────────────────────────────────────────────────

class TestPodIdentity(unittest.TestCase):

    def test_pod_id(self):
        pod, _ = _make_pod()
        self.assertEqual(pod.pod_id, "P-013")

    def test_pod_name(self):
        pod, _ = _make_pod()
        self.assertEqual(pod.pod_name, "Kalshi-Deribit Crypto Options Arb")

    def test_venue_name(self):
        pod, _ = _make_pod()
        self.assertEqual(pod.venue_name(), "kalshi")


class TestScanOnce(unittest.TestCase):

    def test_signal_found_and_placed(self):
        """Kalshi YES=0.27, Deribit fair=0.20 → buy NO at 0.73 → edge."""
        markets = [_kalshi_market()]  # YES bid/ask = 25/29 → mid = 0.27
        pod, kalshi = _make_pod(
            kalshi_markets=markets,
            pricer_prob=0.20,  # Deribit says 20%
            min_edge_pct=0.05,
            min_net_edge_pct=0.03,
        )

        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 1)
        self.assertEqual(placed[0].pod_id, "P-013")
        self.assertEqual(placed[0].venue, "kalshi")
        self.assertEqual(placed[0].market_type, "crypto_options_arb")

    def test_no_kalshi_contracts(self):
        """No matching Kalshi crypto contracts → empty."""
        pod, _ = _make_pod(kalshi_markets=[])
        results = pod.scan_once()
        self.assertEqual(results, [])

    def test_no_deribit_chains(self):
        """No Deribit data → no PLACED results (may have skips)."""
        markets = [_kalshi_market()]
        pod, _ = _make_pod(kalshi_markets=markets, deribit_chains=[])
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 0)

    def test_edge_below_threshold(self):
        """Kalshi YES=0.27, Deribit fair=0.25 → edge=2% < 7% threshold → skip."""
        markets = [_kalshi_market()]
        pod, _ = _make_pod(
            kalshi_markets=markets,
            pricer_prob=0.25,  # Only 2% edge
            min_edge_pct=0.07,
        )
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 0)

    def test_dedup_prevents_repeat(self):
        """Same contract should not be placed twice in same hour."""
        markets = [_kalshi_market()]
        pod, _ = _make_pod(
            kalshi_markets=markets,
            pricer_prob=0.15,
            min_edge_pct=0.05,
            min_net_edge_pct=0.03,
        )
        r1 = pod.scan_once()
        r2 = pod.scan_once()
        placed_1 = [r for r in r1 if r.action == "PLACED"]
        placed_2 = [r for r in r2 if r.action == "PLACED"]
        self.assertEqual(len(placed_1), 1)
        self.assertEqual(len(placed_2), 0)  # Deduped

    def test_max_contracts_caps(self):
        """Respect max_contracts_per_cycle limit."""
        markets = [
            _kalshi_market(f"KXBTCM-26MAR28-B{s}", f"BTC above ${s}?", 20, 24)
            for s in [90000, 95000, 100000, 105000, 110000]
        ]
        pod, _ = _make_pod(
            kalshi_markets=markets,
            pricer_prob=0.10,  # Big edge
            min_edge_pct=0.02,
            min_net_edge_pct=0.01,
            max_contracts=2,
        )
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertLessEqual(len(placed), 2)


class TestExtractStrike(unittest.TestCase):

    def test_ticker_format_b_prefix(self):
        strike = CryptoOptionsArbPod._extract_strike("KXBTC-26MAR28-B87750", "")
        self.assertEqual(strike, 87750)

    def test_ticker_format_t_prefix(self):
        strike = CryptoOptionsArbPod._extract_strike("KXBTCD-26MAR28-T87750", "")
        self.assertEqual(strike, 87750)

    def test_title_dollar_format(self):
        strike = CryptoOptionsArbPod._extract_strike("", "Bitcoin above $87,750 on March 28?")
        self.assertEqual(strike, 87750)

    def test_title_k_format(self):
        strike = CryptoOptionsArbPod._extract_strike("", "Will Bitcoin cross $100k before July?")
        self.assertEqual(strike, 100000)

    def test_no_match(self):
        strike = CryptoOptionsArbPod._extract_strike("UNKNOWN", "no price here")
        self.assertIsNone(strike)


class TestEvaluateContract(unittest.TestCase):

    def test_buy_no_when_kalshi_overprices_yes(self):
        """Kalshi YES=0.27, Deribit fair=0.20 → buy NO (Kalshi overprices YES)."""
        markets = [_kalshi_market()]
        pod, _ = _make_pod(
            kalshi_markets=markets,
            pricer_prob=0.20,
            min_edge_pct=0.05,
            min_net_edge_pct=0.03,
        )
        # Manually test _evaluate_contract
        pod._refresh_deribit_data()
        contract = pod._parse_kalshi_contract(markets[0])
        self.assertIsNotNone(contract)
        signal = pod._evaluate_contract(contract)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "NO")
        self.assertGreater(signal.edge_pct, 0.05)

    def test_buy_yes_when_kalshi_underprices_yes(self):
        """Kalshi YES=0.10, Deribit fair=0.20 → buy YES."""
        markets = [_kalshi_market(yes_bid=8, yes_ask=12)]  # mid = 0.10
        pod, _ = _make_pod(
            kalshi_markets=markets,
            pricer_prob=0.20,
            min_edge_pct=0.05,
            min_net_edge_pct=0.03,
        )
        pod._refresh_deribit_data()
        contract = pod._parse_kalshi_contract(markets[0])
        signal = pod._evaluate_contract(contract)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "YES")


class TestFeeCalculation(unittest.TestCase):

    def test_maker_fee_at_mid_prob(self):
        """Maker fee at P=0.50: 0.0175 × 0.5 × 0.5 = 0.004375."""
        fee = KALSHI_MAKER_FEE_MULT * 0.5 * (1 - 0.5)
        self.assertAlmostEqual(fee, 0.004375, places=5)

    def test_maker_fee_at_low_prob(self):
        """Maker fee at P=0.14: 0.0175 × 0.14 × 0.86 ≈ 0.0021."""
        fee = KALSHI_MAKER_FEE_MULT * 0.14 * (1 - 0.14)
        self.assertAlmostEqual(fee, 0.00211, places=4)

    def test_taker_fee_higher(self):
        """Taker fee is 4× maker fee."""
        maker = KALSHI_MAKER_FEE_MULT * 0.5 * 0.5
        taker = KALSHI_TAKER_FEE_MULT * 0.5 * 0.5
        self.assertAlmostEqual(taker / maker, 4.0, places=1)


class TestExtraFields(unittest.TestCase):

    def test_extra_contains_pricing_metadata(self):
        """PLACED results should contain pricing method details in extra."""
        markets = [_kalshi_market()]
        pod, _ = _make_pod(
            kalshi_markets=markets,
            pricer_prob=0.15,
            min_edge_pct=0.05,
            min_net_edge_pct=0.03,
        )
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 1)
        extra = placed[0].extra
        self.assertIn("arb_type", extra)
        self.assertEqual(extra["arb_type"], "crypto_options_directional")
        self.assertIn("deribit_fair_prob", extra)
        self.assertIn("kalshi_prob", extra)
        self.assertIn("net_edge_pct", extra)
        self.assertIn("pricing_method", extra)
        self.assertIn("btc_spot", extra)
        self.assertIn("strike", extra)


class TestLiquidityFilter(unittest.TestCase):
    """Tests for the liquidity and edge sanity filters added to prevent
    phantom signals from illiquid contracts (bid=0, ask=1 → mid=0.50)."""

    def test_illiquid_contract_bid_zero_filtered(self):
        """Contract with yes_bid=0, yes_ask=100 should be rejected at parse."""
        market = _kalshi_market(yes_bid=0, yes_ask=100)
        pod, _ = _make_pod(kalshi_markets=[market])
        contract = pod._parse_kalshi_contract(market)
        self.assertIsNone(contract)  # Filtered out — bid=0

    def test_wide_spread_filtered(self):
        """Contract with spread > 0.50 should be rejected at parse."""
        # bid=10 (0.10), ask=70 (0.70) → spread = 0.60 > 0.50
        market = _kalshi_market(yes_bid=10, yes_ask=70)
        pod, _ = _make_pod(kalshi_markets=[market])
        contract = pod._parse_kalshi_contract(market)
        self.assertIsNone(contract)  # Filtered out — spread too wide

    def test_tight_spread_passes(self):
        """Contract with tight spread should pass liquidity filter."""
        # bid=25 (0.25), ask=29 (0.29) → spread = 0.04
        market = _kalshi_market(yes_bid=25, yes_ask=29)
        pod, _ = _make_pod(kalshi_markets=[market])
        contract = pod._parse_kalshi_contract(market)
        self.assertIsNotNone(contract)

    def test_moderate_spread_passes(self):
        """Contract with spread < 0.50 should pass."""
        # bid=20 (0.20), ask=60 (0.60) → spread = 0.40 < 0.50
        market = _kalshi_market(yes_bid=20, yes_ask=60)
        pod, _ = _make_pod(kalshi_markets=[market])
        contract = pod._parse_kalshi_contract(market)
        self.assertIsNotNone(contract)

    def test_implausible_edge_rejected(self):
        """Edge > 20% should be rejected as data quality issue."""
        # Kalshi mid = 0.27, Deribit fair = 0.70 → edge = 43% > 20%
        markets = [_kalshi_market(yes_bid=25, yes_ask=29)]  # mid=0.27
        pod, _ = _make_pod(
            kalshi_markets=markets,
            pricer_prob=0.70,  # 43% edge — implausible
            min_edge_pct=0.05,
            min_net_edge_pct=0.03,
            max_plausible_edge=0.20,
        )
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 0)

    def test_plausible_edge_accepted(self):
        """Edge of ~12% should pass sanity check."""
        # Kalshi mid = 0.27, Deribit fair = 0.15 → edge = 12%
        markets = [_kalshi_market(yes_bid=25, yes_ask=29)]  # mid=0.27
        pod, _ = _make_pod(
            kalshi_markets=markets,
            pricer_prob=0.15,  # 12% edge — plausible
            min_edge_pct=0.05,
            min_net_edge_pct=0.03,
        )
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 1)

    def test_scan_zero_signals_from_illiquid_markets(self):
        """Full scan with illiquid markets should produce zero placements."""
        markets = [
            _kalshi_market(f"KXBTCM-26MAR28-B{s}", f"BTC above ${s}?", 0, 100)
            for s in [80000, 85000, 90000, 95000, 100000]
        ]
        pod, _ = _make_pod(
            kalshi_markets=markets,
            pricer_prob=0.05,
            min_edge_pct=0.02,
            min_net_edge_pct=0.01,
        )
        results = pod.scan_once()
        placed = [r for r in results if r.action == "PLACED"]
        self.assertEqual(len(placed), 0)


class TestFromConfig(unittest.TestCase):

    def test_basic_wiring(self):
        """from_config builds pod with correct settings."""
        config = {
            "pods": {
                "P-013": {
                    "environment": "demo",
                    "min_edge_pct": 0.10,
                    "min_net_edge_pct": 0.07,
                    "max_contracts_per_cycle": 3,
                    "kelly_multiplier": 0.20,
                    "use_maker_orders": True,
                }
            },
            "paths": {"pod_logs": "/tmp/pods"},
            "deribit": {"environment": "testnet"},
        }
        pod = CryptoOptionsArbPod.from_config(
            config,
            kalshi_client=_MockKalshi(),
            edge_calculator=_MockEdge(),
            risk_manager=_MockRisk(),
        )
        self.assertEqual(pod.pod_id, "P-013")
        self.assertEqual(pod.min_edge_pct, 0.10)
        self.assertEqual(pod.max_contracts_per_cycle, 3)
        self.assertEqual(pod.mode, "paper")


if __name__ == "__main__":
    unittest.main()
