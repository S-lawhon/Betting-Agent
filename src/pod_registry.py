"""
src/pod_registry.py
───────────────────
Decorator-based pod auto-discovery.

Pods register themselves by decorating their class with ``@register_pod``:

    @register_pod("P-006")
    class PolymarketConsensusPod(BasePod):
        ...

At import time the decorator records the mapping.  ``discover_pods()``
triggers the import of all modules in ``src/pods/`` so decorators fire,
then returns the complete registry dict.

The old hardcoded ``POD_REGISTRY`` in ``pod_runner.py`` is kept as a
fallback — if a pod module fails to import (missing dependency), the
hardcoded entry still works.
"""

import importlib
import logging
import pkgutil
from typing import Dict, Optional, Tuple, Type

logger = logging.getLogger(__name__)

# Module-level registry: pod_id → class
_REGISTRY: Dict[str, Type] = {}

# Legacy fallback: pod_id → (module_path, class_name)
_LEGACY_REGISTRY: Dict[str, Tuple[str, str]] = {
    "P-001": ("src.pods.kalshi_moneyline", "KalshiMoneylinePod"),
    "P-002": ("src.pods.cross_venue_arb", "CrossVenueArbPod"),
    "P-004": ("src.pods.forecastex_kalshi_arb", "ForecastExKalshiArbPod"),
    "P-006": ("src.pods.polymarket_consensus", "PolymarketConsensusPod"),
    "P-009": ("src.pods.signup_bonus_pod", "SignupBonusPod"),
    "P-010": ("src.pods.boost_scanner_pod", "BoostScannerPod"),
    "P-012": ("src.pods.macro_nowcast", "MacroNowcastPod"),
    "P-013": ("src.pods.crypto_options_arb", "CryptoOptionsArbPod"),
    "P-014": ("src.pods.live_game_pod", "LiveGamePod"),
    "P-015": ("src.pods.qualifier_favorite_pod", "QualifierFavoritePod"),
}


def register_pod(pod_id: str):
    """Class decorator that registers a pod class under the given pod_id.

    Usage:
        @register_pod("P-006")
        class PolymarketConsensusPod(BasePod):
            ...
    """
    def decorator(cls):
        if pod_id in _REGISTRY:
            logger.debug(
                "pod_registry: overwriting %s (%s → %s)",
                pod_id, _REGISTRY[pod_id].__name__, cls.__name__,
            )
        _REGISTRY[pod_id] = cls
        cls._pod_registry_id = pod_id
        return cls
    return decorator


def discover_pods() -> Dict[str, Type]:
    """Import all modules in src/pods/ to trigger @register_pod decorators.

    Returns the complete registry (auto-discovered + legacy fallback).
    """
    try:
        import src.pods as pods_pkg
        for importer, modname, ispkg in pkgutil.iter_modules(pods_pkg.__path__):
            full_name = "src.pods.{}".format(modname)
            try:
                importlib.import_module(full_name)
            except Exception as exc:
                logger.debug("pod_registry: failed to import %s: %s", full_name, exc)
    except ImportError:
        logger.debug("pod_registry: src.pods package not found")

    return dict(_REGISTRY)


def get_pod_class(pod_id: str) -> Optional[Type]:
    """Look up a pod class by ID.

    Checks the decorator registry first, falls back to legacy import.
    """
    # Try decorator registry
    if pod_id in _REGISTRY:
        return _REGISTRY[pod_id]

    # Fall back to legacy registry
    if pod_id in _LEGACY_REGISTRY:
        module_path, class_name = _LEGACY_REGISTRY[pod_id]
        try:
            module = importlib.import_module(module_path)
            return getattr(module, class_name)
        except (ImportError, AttributeError) as exc:
            logger.warning("pod_registry: legacy import failed for %s: %s", pod_id, exc)
            return None

    return None


def list_registered_pods() -> Dict[str, str]:
    """Return {pod_id: class_name} for all registered pods."""
    result = {}
    for pid, cls in _REGISTRY.items():
        result[pid] = cls.__name__
    for pid, (mod, cls_name) in _LEGACY_REGISTRY.items():
        if pid not in result:
            result[pid] = cls_name + " (legacy)"
    return result
