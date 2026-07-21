"""
tests/test_nowcast_client.py
────────────────────────────
Tests for NowcastClient and NowcastReading.

  ✓ NowcastReading: fields, defaults
  ✓ get_reading with adapter returns data
  ✓ get_reading without adapter returns None (stub)
  ✓ get_all_readings aggregates from all enabled sources
  ✓ unknown source returns None
  ✓ adapter error handled gracefully
  ✓ from_config
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.nowcast_client import NowcastClient, NowcastReading


# ── Fixtures ────────────────────────────────────────────────────────

_CPI_READING = NowcastReading(
    source="cleveland_fed_cpi",
    indicator="cpi_yoy",
    value=3.2,
    lower_bound=2.8,
    upper_bound=3.6,
    as_of_date="2024-06-01",
    unit="percent",
)

_GDP_READING = NowcastReading(
    source="atlanta_fed_gdp",
    indicator="gdp_quarterly",
    value=2.1,
    lower_bound=1.5,
    upper_bound=2.7,
    as_of_date="2024-06-01",
)


class _MockAdapter:
    def __init__(self, readings=None, raise_on=None):
        self._readings = readings or {}
        self._raise_on = raise_on

    def get_reading(self, source):
        if self._raise_on and source in self._raise_on:
            raise RuntimeError("fetch error")
        return self._readings.get(source)


# ── Tests ───────────────────────────────────────────────────────────

class TestNowcastReading(unittest.TestCase):

    def test_fields(self):
        r = _CPI_READING
        self.assertEqual(r.source, "cleveland_fed_cpi")
        self.assertEqual(r.indicator, "cpi_yoy")
        self.assertAlmostEqual(r.value, 3.2)
        self.assertAlmostEqual(r.lower_bound, 2.8)
        self.assertAlmostEqual(r.upper_bound, 3.6)

    def test_defaults(self):
        r = NowcastReading(source="test", indicator="x", value=1.0)
        self.assertIsNone(r.lower_bound)
        self.assertIsNone(r.upper_bound)
        self.assertIsNone(r.as_of_date)
        self.assertEqual(r.unit, "percent")
        self.assertEqual(r.extra, {})

    def test_frozen(self):
        with self.assertRaises(AttributeError):
            _CPI_READING.value = 99.9


class TestGetReading(unittest.TestCase):

    def test_adapter_returns_reading(self):
        adapter = _MockAdapter(readings={"cleveland_fed_cpi": _CPI_READING})
        client = NowcastClient(adapter=adapter)
        result = client.get_reading("cleveland_fed_cpi")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.value, 3.2)

    def test_adapter_missing_source(self):
        adapter = _MockAdapter(readings={})
        client = NowcastClient(adapter=adapter)
        result = client.get_reading("cleveland_fed_cpi")
        self.assertIsNone(result)

    def test_no_adapter_stub_returns_none(self):
        # Without adapter, production stubs return None
        client = NowcastClient()
        result = client.get_reading("cleveland_fed_cpi")
        self.assertIsNone(result)

    def test_unknown_source_returns_none(self):
        client = NowcastClient()
        result = client.get_reading("unknown_source")
        self.assertIsNone(result)


class TestGetAllReadings(unittest.TestCase):

    def test_returns_all_enabled(self):
        adapter = _MockAdapter(readings={
            "cleveland_fed_cpi": _CPI_READING,
            "atlanta_fed_gdp": _GDP_READING,
        })
        client = NowcastClient(
            adapter=adapter,
            enabled_sources=["cleveland_fed_cpi", "atlanta_fed_gdp"],
        )
        readings = client.get_all_readings()
        self.assertEqual(len(readings), 2)
        self.assertIn("cleveland_fed_cpi", readings)
        self.assertIn("atlanta_fed_gdp", readings)

    def test_skips_missing(self):
        adapter = _MockAdapter(readings={"cleveland_fed_cpi": _CPI_READING})
        client = NowcastClient(
            adapter=adapter,
            enabled_sources=["cleveland_fed_cpi", "atlanta_fed_gdp"],
        )
        readings = client.get_all_readings()
        self.assertEqual(len(readings), 1)

    def test_empty_when_no_data(self):
        adapter = _MockAdapter(readings={})
        client = NowcastClient(adapter=adapter)
        readings = client.get_all_readings()
        self.assertEqual(len(readings), 0)


class TestErrorHandling(unittest.TestCase):

    def test_adapter_error_returns_none(self):
        adapter = _MockAdapter(raise_on={"cleveland_fed_cpi"})
        client = NowcastClient(adapter=adapter)
        # Should not raise
        result = client.get_reading("cleveland_fed_cpi")
        self.assertIsNone(result)


class TestFromConfig(unittest.TestCase):

    def test_defaults(self):
        client = NowcastClient.from_config({})
        self.assertIsNone(client._fred_api_key)
        self.assertEqual(len(client._enabled_sources), 3)

    def test_reads_config(self):
        cfg = {
            "pods": {"P-012": {"data_sources": ["cleveland_fed_cpi"]}},
            "fred": {"api_key": "test-key"},
        }
        client = NowcastClient.from_config(cfg)
        self.assertEqual(client._fred_api_key, "test-key")
        self.assertEqual(client._enabled_sources, ["cleveland_fed_cpi"])


if __name__ == "__main__":
    unittest.main()
