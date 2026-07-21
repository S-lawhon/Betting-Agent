"""
src/compat.py
─────────────
Legacy adapter layer — single-import access to all public symbols.

As the codebase was refactored from a monolithic ``main.py`` into
focused modules (``config_loader``, ``engine``, ``cli``,
``pod_registry``, ``pnl_calculator``, ``protocols``, etc.), external
code and notebooks that imported from ``src.main`` or used old symbol
names still need to work.

This module provides:

  1. **Re-exports**: every public symbol from every refactored module,
     so ``from src.compat import AggregateRiskGuard`` works regardless
     of which submodule actually defines it.

  2. **Deprecated aliases**: old names that were renamed during refactoring,
     with import-time deprecation warnings.

Usage:
    # Modern — prefer direct imports:
    from src.aggregate_risk import AggregateRiskGuard

    # Legacy — still works via compat:
    from src.compat import AggregateRiskGuard

    # Migration check:
    from src.compat import check_deprecated_imports
    check_deprecated_imports()
"""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional, Type

# ── Re-exports from refactored modules ───────────────────────────────

# Base pod
from src.base_pod import BasePod, ScanResult

# Pod registry
from src.pod_registry import (
    register_pod,
    discover_pods,
    get_pod_class,
    list_registered_pods,
)

# Config loading (extracted from main.py)
from src.config_loader import (
    load_config,
    filter_pods,
)

# Engine (extracted from main.py)
from src.engine import (
    build_venue_clients,
    build_shared_deps,
    make_cycle_callback,
)

# CLI (extracted from main.py)
from src.cli import (
    setup_logging,
)

# Pod runner
from src.pod_runner import PodRunner, CycleReport

# Multi executor
from src.multi_executor import MultiExecutor, MultiExecutionResult

# Risk
from src.aggregate_risk import AggregateRiskGuard, RiskSnapshot

# Capital allocation
from src.capital_allocator import (
    CapitalAllocator,
    PodAllocation,
    PodPerformance,
)

# Constants
from src.constants import (
    DEFAULT_BANKROLL,
    DEFAULT_MAX_BET_PCT,
    MIN_VALID_PRICE,
    MAX_VALID_PRICE,
    HTTP_TIMEOUT_SECONDS,
    DEDUP_WINDOW_HOURS,
)

# Trade log schema
from src.trade_log_schema import TradeLogSchema

# P&L calculator
from src.pnl_calculator import PnLCalculator, VenueFees, PnLResult, VENUE_FEES

# Protocols
from src.protocols import (
    EdgeCalculatorProtocol,
    RiskManagerProtocol,
    KalshiClientProtocol,
    PolymarketClientProtocol,
    ForecastExClientProtocol,
    AggregateRiskProtocol,
    TradeStoreProtocol,
    EdgeResult,
)

# Dashboard
from src.web_dashboard import WebDashboardServer, DashboardState


# ── Deprecated aliases ───────────────────────────────────────────────
# Old names that were used before refactoring.  Import still works
# but emits a DeprecationWarning.

_DEPRECATED_ALIASES: Dict[str, str] = {
    # old name → new name (both importable from this module)
    "RiskGuard": "AggregateRiskGuard",
    "RunReport": "CycleReport",
    "ExecutionResult": "MultiExecutionResult",
}


def __getattr__(name: str) -> Any:
    if name in _DEPRECATED_ALIASES:
        new_name = _DEPRECATED_ALIASES[name]
        warnings.warn(
            f"src.compat.{name} is deprecated, use {new_name} instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return globals()[new_name]
    raise AttributeError(f"module 'src.compat' has no attribute {name!r}")


def check_deprecated_imports() -> List[str]:
    """Return list of deprecated aliases that are still importable.

    Useful for migration audits — run this in a notebook or script
    to see which old names still exist.
    """
    return sorted(_DEPRECATED_ALIASES.keys())
