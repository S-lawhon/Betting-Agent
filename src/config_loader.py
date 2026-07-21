"""
src/config_loader.py
────────────────────
Config loading and merging.

Handles YAML loading, deep merging of base + multi-pod configs,
pod filtering, and live sport discovery enrichment.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

# ── Config loading requires PyYAML ───────────────────────────────────
try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _yaml = None  # type: ignore
    _YAML_AVAILABLE = False

logger = logging.getLogger(__name__)

DEFAULT_MULTI_CONFIG = "config_multi_pod.yaml"


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge *overlay* into *base*.

    - Dict values are merged recursively (overlay keys win on leaf conflicts).
    - All other types (including lists) are replaced by the overlay value.

    Returns a new dict; neither input is mutated.
    """
    merged = dict(base)
    for key, val in overlay.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(val, dict)
        ):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def load_config(
    multi_path: str = DEFAULT_MULTI_CONFIG,
    base_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Load and merge config files.

    Loads base config first (if provided), then deep-merges the multi-pod
    config on top.  Multi-pod values win on leaf-level key conflicts;
    nested dicts are merged recursively so base-only keys survive.

    Args:
        multi_path: Path to config_multi_pod.yaml.
        base_path:  Optional path to the existing config.yaml.

    Returns:
        Merged config dict.

    Raises:
        FileNotFoundError: If multi_path doesn't exist and no base given.
        RuntimeError: If PyYAML is not installed.
    """
    if not _YAML_AVAILABLE:
        raise RuntimeError(
            "PyYAML is required to load config files.  "
            "Install with: pip install pyyaml"
        )

    config: Dict[str, Any] = {}

    if base_path:
        bp = Path(base_path)
        if bp.exists():
            with open(bp) as fh:
                base = _yaml.safe_load(fh) or {}
            config = _deep_merge(config, base)

    mp = Path(multi_path)
    if mp.exists():
        with open(mp) as fh:
            multi = _yaml.safe_load(fh) or {}
        config = _deep_merge(config, multi)
    elif not config:
        raise FileNotFoundError(
            "Config not found: {}.  "
            "Pass --config to specify a path.".format(multi_path)
        )

    return config


def filter_pods(config: Dict[str, Any], pod_filter: Optional[str]) -> Dict[str, Any]:
    """Return a copy of config with the active pod list narrowed to pod_filter.

    If pod_filter is None, returns config unchanged.
    Unknown pod IDs in the filter string are silently ignored.

    Args:
        config:     Merged config dict.
        pod_filter: Comma-separated pod IDs, e.g. "P-001,P-006".

    Returns:
        Config dict with updated pods.active list.
    """
    if not pod_filter:
        return config

    requested = [p.strip() for p in pod_filter.split(",") if p.strip()]
    current_active = config.get("pods", {}).get("active") or []
    narrowed = [p for p in requested if p in current_active]

    config = dict(config)
    config["pods"] = dict(config.get("pods", {}))
    config["pods"]["active"] = narrowed
    return config


def enrich_sports_with_active_tennis(config: Dict[str, Any]) -> Dict[str, Any]:
    """Query The Odds API /v4/sports and add any active Tennis keys to config.

    This replaces the need to maintain a static list of per-tournament sport
    keys (which rotate every week).  Any key in the ``"Tennis"`` group that is
    currently marked ``active`` by the API is merged into
    ``config.odds_api.sports``.  The existing static entries are kept so the
    config works even when the network call fails.

    Failures (network error, missing API key, bad JSON) are logged as warnings
    and the config is returned unmodified — the scanner will still run with
    whatever sports were already listed.
    """
    import urllib.request as _urlreq
    import urllib.error  as _urlerr

    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        logger.debug("enrich_sports: ODDS_API_KEY not set, skipping tennis discovery")
        return config

    url = f"https://api.the-odds-api.com/v4/sports/?apiKey={api_key}"
    try:
        with _urlreq.urlopen(url, timeout=10) as resp:
            sports_list = json.loads(resp.read().decode("utf-8"))
    except (_urlerr.URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("enrich_sports: could not fetch active sports — %s", exc)
        return config

    active_tennis = [
        s["key"] for s in sports_list
        if s.get("group", "").lower() == "tennis" and s.get("active")
    ]

    if not active_tennis:
        logger.info("enrich_sports: no active tennis tournaments found")
        return config

    config = dict(config)
    config["odds_api"] = dict(config.get("odds_api", {}))
    existing = list(config["odds_api"].get("sports", []))
    added = [k for k in active_tennis if k not in existing]
    config["odds_api"]["sports"] = existing + added

    logger.info(
        "enrich_sports: active tennis keys — %s%s",
        ", ".join(active_tennis),
        f"  (added: {', '.join(added)})" if added else "  (all already listed)",
    )
    return config


# Keep the old private name as an alias for backward compatibility
_enrich_sports_with_active_tennis = enrich_sports_with_active_tennis
