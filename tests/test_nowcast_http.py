"""
tests/test_nowcast_http.py
──────────────────────────
Tests for src/nowcast_http.py — real HTTP adapters for NowcastClient.

All HTTP is mocked via the injectable _http_get parameter — no network
calls are made during testing.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.nowcast_http import (
    ClevelandFedAdapter,
    AtlantaFedAdapter,
    FredAdapter,
    CompositeNowcastAdapter,
    _try_float,
    _parse_float_any,
    _pick_any,
)
from src.nowcast_client import NowcastReading

# ── Mock HTTP helper ─────────────────────────────────────────────────

def _ok(body: bytes):
    """Return a mock HTTP get that returns status 200 + body."""
    return lambda url: (200, body)


def _fail(exc):
    """Return a mock HTTP get that raises an exception."""
    def _raise(url):
        raise exc
    return _raise


# ── Sample data ──────────────────────────────────────────────────────

_CLEVELAND_CSV = b"""date,cpi_yoy,ci_lower_90,ci_upper_90
2025-12-01,3.2,2.8,3.6
2026-01-01,3.4,3.0,3.8
"""

_ATLANTA_CSV = b"""date_of_release,gdpnow
2025-12-15,2.1
2026-01-20,2.5
"""

_FRED_JSON = json.dumps({
    "observations": [
        {"date": "2026-01-01", "value": "158200"},
        {"date": "2025-12-01", "value": "158000"},
    ]
}).encode("utf-8")

_FRED_JSON_MISSING = json.dumps({
    "observations": [
        {"date": "2026-01-01", "value": "."},   # FRED "missing" sentinel
        {"date": "2025-12-01", "value": "157900"},
    ]
}).encode("utf-8")


# ── TestClevelandFedAdapter ───────────────────────────────────────────

class TestClevelandFedAdapter(unittest.TestCase):

    def test_returns_reading_on_success(self):
        adapter = ClevelandFedAdapter(_http_get=_ok(_CLEVELAND_CSV))
        r = adapter.get_reading("cleveland_fed_cpi")
        self.assertIsInstance(r, NowcastReading)
        self.assertEqual(r.source, "cleveland_fed_cpi")
        self.assertEqual(r.indicator, "cpi_yoy")

    def test_value_from_last_row(self):
        adapter = ClevelandFedAdapter(_http_get=_ok(_CLEVELAND_CSV))
        r = adapter.get_reading("cleveland_fed_cpi")
        self.assertAlmostEqual(r.value, 3.4)

    def test_confidence_interval_parsed(self):
        adapter = ClevelandFedAdapter(_http_get=_ok(_CLEVELAND_CSV))
        r = adapter.get_reading("cleveland_fed_cpi")
        self.assertAlmostEqual(r.lower_bound, 3.0)
        self.assertAlmostEqual(r.upper_bound, 3.8)

    def test_unit_is_percent(self):
        adapter = ClevelandFedAdapter(_http_get=_ok(_CLEVELAND_CSV))
        r = adapter.get_reading("cleveland_fed_cpi")
        self.assertEqual(r.unit, "percent")

    def test_wrong_source_returns_none(self):
        adapter = ClevelandFedAdapter(_http_get=_ok(_CLEVELAND_CSV))
        r = adapter.get_reading("atlanta_fed_gdp")
        self.assertIsNone(r)

    def test_http_error_returns_none(self):
        adapter = ClevelandFedAdapter(_http_get=_fail(ConnectionError("timeout")))
        r = adapter.get_reading("cleveland_fed_cpi")
        self.assertIsNone(r)

    def test_non_200_returns_none(self):
        adapter = ClevelandFedAdapter(_http_get=lambda url: (404, b"Not found"))
        r = adapter.get_reading("cleveland_fed_cpi")
        self.assertIsNone(r)

    def test_empty_csv_returns_none(self):
        adapter = ClevelandFedAdapter(_http_get=_ok(b"date,cpi_yoy\n"))
        r = adapter.get_reading("cleveland_fed_cpi")
        self.assertIsNone(r)


# ── TestAtlantaFedAdapter ─────────────────────────────────────────────

class TestAtlantaFedAdapter(unittest.TestCase):

    def test_returns_reading_on_success(self):
        adapter = AtlantaFedAdapter(_http_get=_ok(_ATLANTA_CSV))
        r = adapter.get_reading("atlanta_fed_gdp")
        self.assertIsInstance(r, NowcastReading)
        self.assertEqual(r.source, "atlanta_fed_gdp")
        self.assertEqual(r.indicator, "gdp_quarterly")

    def test_value_from_last_row(self):
        adapter = AtlantaFedAdapter(_http_get=_ok(_ATLANTA_CSV))
        r = adapter.get_reading("atlanta_fed_gdp")
        self.assertAlmostEqual(r.value, 2.5)

    def test_unit_is_percent(self):
        adapter = AtlantaFedAdapter(_http_get=_ok(_ATLANTA_CSV))
        r = adapter.get_reading("atlanta_fed_gdp")
        self.assertEqual(r.unit, "percent")

    def test_wrong_source_returns_none(self):
        adapter = AtlantaFedAdapter(_http_get=_ok(_ATLANTA_CSV))
        r = adapter.get_reading("cleveland_fed_cpi")
        self.assertIsNone(r)

    def test_http_error_returns_none(self):
        adapter = AtlantaFedAdapter(_http_get=_fail(OSError("connection refused")))
        r = adapter.get_reading("atlanta_fed_gdp")
        self.assertIsNone(r)

    def test_non_200_returns_none(self):
        adapter = AtlantaFedAdapter(_http_get=lambda url: (503, b""))
        r = adapter.get_reading("atlanta_fed_gdp")
        self.assertIsNone(r)


# ── TestFredAdapter ───────────────────────────────────────────────────

class TestFredAdapter(unittest.TestCase):

    def test_returns_reading_with_api_key(self):
        adapter = FredAdapter(api_key="testkey", _http_get=_ok(_FRED_JSON))
        r = adapter.get_reading("fred_payrolls")
        self.assertIsInstance(r, NowcastReading)
        self.assertEqual(r.source, "fred_payrolls")
        self.assertEqual(r.indicator, "payrolls")

    def test_value_from_latest_observation(self):
        adapter = FredAdapter(api_key="testkey", _http_get=_ok(_FRED_JSON))
        r = adapter.get_reading("fred_payrolls")
        self.assertAlmostEqual(r.value, 158200.0)

    def test_unit_is_thousands(self):
        adapter = FredAdapter(api_key="testkey", _http_get=_ok(_FRED_JSON))
        r = adapter.get_reading("fred_payrolls")
        self.assertEqual(r.unit, "thousands")

    def test_no_api_key_returns_none(self):
        """Without an API key, FRED returns None without making a request."""
        called = []
        def track(url):
            called.append(url)
            return (200, _FRED_JSON)

        adapter = FredAdapter(api_key=None, _http_get=track)
        r = adapter.get_reading("fred_payrolls")
        self.assertIsNone(r)
        self.assertEqual(len(called), 0)  # No request made

    def test_missing_value_falls_back_to_previous(self):
        """FRED '.' sentinel triggers fallback to previous observation."""
        adapter = FredAdapter(api_key="testkey", _http_get=_ok(_FRED_JSON_MISSING))
        r = adapter.get_reading("fred_payrolls")
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r.value, 157900.0)

    def test_wrong_source_returns_none(self):
        adapter = FredAdapter(api_key="testkey", _http_get=_ok(_FRED_JSON))
        r = adapter.get_reading("atlanta_fed_gdp")
        self.assertIsNone(r)

    def test_http_error_returns_none(self):
        adapter = FredAdapter(api_key="k", _http_get=_fail(RuntimeError("timeout")))
        r = adapter.get_reading("fred_payrolls")
        self.assertIsNone(r)


# ── TestCompositeNowcastAdapter ───────────────────────────────────────

class TestCompositeNowcastAdapter(unittest.TestCase):

    def _make_composite(self):
        clev = ClevelandFedAdapter(_http_get=_ok(_CLEVELAND_CSV))
        atl = AtlantaFedAdapter(_http_get=_ok(_ATLANTA_CSV))
        fred = FredAdapter(api_key="k", _http_get=_ok(_FRED_JSON))
        return CompositeNowcastAdapter({
            "cleveland_fed_cpi": clev,
            "atlanta_fed_gdp": atl,
            "fred_payrolls": fred,
        })

    def test_dispatches_to_cleveland(self):
        comp = self._make_composite()
        r = comp.get_reading("cleveland_fed_cpi")
        self.assertIsInstance(r, NowcastReading)
        self.assertEqual(r.source, "cleveland_fed_cpi")

    def test_dispatches_to_atlanta(self):
        comp = self._make_composite()
        r = comp.get_reading("atlanta_fed_gdp")
        self.assertEqual(r.source, "atlanta_fed_gdp")

    def test_dispatches_to_fred(self):
        comp = self._make_composite()
        r = comp.get_reading("fred_payrolls")
        self.assertEqual(r.source, "fred_payrolls")

    def test_unknown_source_returns_none(self):
        comp = self._make_composite()
        r = comp.get_reading("nonexistent_source")
        self.assertIsNone(r)

    def test_from_config_no_fred_key(self):
        """from_config with no FRED key should produce a working composite."""
        comp = CompositeNowcastAdapter.from_config({})
        self.assertIn("cleveland_fed_cpi", comp._adapters)
        self.assertIn("atlanta_fed_gdp", comp._adapters)
        self.assertIn("fred_payrolls", comp._adapters)

    def test_register_adds_adapter(self):
        comp = CompositeNowcastAdapter({})
        mock_adapter = ClevelandFedAdapter(_http_get=_ok(_CLEVELAND_CSV))
        comp.register("cleveland_fed_cpi", mock_adapter)
        r = comp.get_reading("cleveland_fed_cpi")
        self.assertIsNotNone(r)


# ── TestHelpers ────────────────────────────────────────────────────────

class TestHelpers(unittest.TestCase):

    def test_try_float_valid(self):
        self.assertEqual(_try_float("3.14"), 3.14)
        self.assertEqual(_try_float(3.14), 3.14)
        self.assertEqual(_try_float("  2.5  "), 2.5)

    def test_try_float_invalid(self):
        self.assertIsNone(_try_float("n/a"))
        self.assertIsNone(_try_float("."))
        self.assertIsNone(_try_float(""))
        self.assertIsNone(_try_float(None))

    def test_parse_float_any_first_match(self):
        row = {"date": "2026-01-01", "cpi_yoy": "3.4", "other": "1.0"}
        self.assertAlmostEqual(_parse_float_any(row, ["cpi_yoy", "estimate"]), 3.4)

    def test_parse_float_any_fallback(self):
        row = {"date": "2026-01-01", "estimate": "2.7"}
        self.assertAlmostEqual(_parse_float_any(row, ["cpi_yoy", "estimate"]), 2.7)

    def test_parse_float_any_no_match(self):
        row = {"date": "2026-01-01"}
        self.assertIsNone(_parse_float_any(row, ["cpi_yoy", "estimate"]))

    def test_pick_any_returns_first_match(self):
        row = {"date": "2026-01-15", "other": "x"}
        self.assertEqual(_pick_any(row, ["date", "as_of"]), "2026-01-15")


if __name__ == "__main__":
    unittest.main()
