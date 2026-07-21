"""
src/constants.py
────────────────
Central location for magic numbers and defaults used across the codebase.

Every pod, risk guard, and allocator should import from here rather than
embedding raw numeric literals.
"""

# ── Bankroll & Position Sizing ──────────────────────────────────────────────
DEFAULT_BANKROLL: float = 10_000.0
"""Default bankroll when no config or ledger value is available."""

DEFAULT_MAX_BET_PCT: float = 0.03
"""Maximum position size as a fraction of bankroll (3%)."""

MIN_KELLY_FRACTION: float = 0.02
"""Minimum Kelly fraction to place a trade (2%).
Trades below this threshold are low-conviction and historically lose money
(30% win rate, −$110.58 cumulative). Raising from 0→2% filters out the
worst-performing cohort while preserving 72%+ win-rate trades.

NOTE: This was calibrated for P-001 (Kalshi, 7% fee, 7% min_edge).
For zero-fee venues like Polymarket, the edge threshold already provides
quality filtering, so pods can override via config min_kelly_fraction."""

# ── Market Filtering ────────────────────────────────────────────────────────
MIN_VALID_PRICE: float = 0.03
"""Prices below this are treated as illiquid/invalid (Polymarket)."""

MAX_VALID_PRICE: float = 0.97
"""Prices above this are treated as illiquid/invalid (Polymarket)."""

# ── Retry / Timing ──────────────────────────────────────────────────────────
HTTP_TIMEOUT_SECONDS: float = 15.0
"""Default HTTP request timeout for external API calls."""

DEDUP_WINDOW_HOURS: int = 1
"""Default deduplication window for fingerprint-based dedup."""
