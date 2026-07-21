"""
src/datagolf_client.py
──────────────────────
Optional DataGolf model feed for P-017. Supplies per-player finish
probabilities (win / top-5 / top-10 / top-20 / make-cut) to use as
`fair_prob` in the golf top-N pod, replacing the structural edge_bump
when a match is found.

Requires a DataGolf **Scratch Plus** membership + API key (env
DATAGOLF_API_KEY, or config datagolf.api_key). Endpoints (docs verified
2026-07-20):
  pre-tournament : https://feeds.datagolf.com/preds/pre-tournament
  in-play        : https://feeds.datagolf.com/preds/in-play
params: tour, odds_format=percent, dead_heat, file_format=json, key.

DEAD-HEAT: we request dead_heat=no → the TRUE probability of finishing in
the top-N counting ties as full qualifiers. That matches Kalshi's payout
(full $1 on ties) and is precisely the mass that sportsbook dead-heat odds
understate — the structural edge documented in GOLF_KALSHI_RESEARCH.md.

Read-only; no betting. Degrades gracefully: any network/parse failure or
missing key leaves the client "unavailable" and the pod falls back to its
structural edge_bump.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Dict, Optional

import requests

from src.fuzzy_utils import token_sort_ratio

logger = logging.getLogger(__name__)

BASE = "https://feeds.datagolf.com"

# Kalshi series prefix → DataGolf probability field
SERIES_TO_FIELD = {
    "KXPGATOP5": "top_5",
    "KXPGATOP10": "top_10",
    "KXPGATOP20": "top_20",
    "KXPGATOP40": "top_40",   # only if add_position requested; else absent
    "KXPGAMAKECUT": "make_cut",
    "KXPGAWIN": "win",
}

# Kalshi tour hints from series/event → DataGolf `tour` param
_DEFAULT_TOUR = "pga"


def _to_prob(x: Any) -> Optional[float]:
    """DataGolf percent format may return 0-1 or 0-100; normalize to 0-1."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    if v > 1.5:          # percent (e.g. 45.0) → 0.45
        v = v / 100.0
    return min(v, 1.0)


def _norm_name(name: str) -> str:
    """'Scheffler, Scottie' → 'scottie scheffler'; strip punctuation."""
    n = (name or "").strip()
    if "," in n:
        last, first = n.split(",", 1)
        n = f"{first.strip()} {last.strip()}"
    n = re.sub(r"[^\w\s]", "", n).lower()
    return re.sub(r"\s+", " ", n).strip()


def player_from_title(title: str) -> Optional[str]:
    """Extract the player name from a Kalshi top-N market title.

    Titles look like: '<Tournament>: Will <Player> finish top 20?' or
    '<Tournament>: Will <Player> make the cut?'. Falls back to None.
    """
    if not title:
        return None
    m = re.search(r"will\s+(.+?)\s+(?:finish|make|record|win|be)\b",
                  title, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Secondary: text after a colon, before a '?'
    if ":" in title:
        tail = title.split(":", 1)[1]
        tail = re.sub(r"\?.*$", "", tail).strip()
        return tail or None
    return None


class DataGolfClient:
    """Cached DataGolf predictions with fuzzy player matching."""

    def __init__(self, api_key: Optional[str] = None, tour: str = _DEFAULT_TOUR,
                 cache_ttl: float = 900.0, match_threshold: float = 80.0,
                 timeout: float = 15.0, _now_fn=None):
        self.api_key = api_key or os.environ.get("DATAGOLF_API_KEY", "")
        self.tour = tour
        self.cache_ttl = cache_ttl
        self.match_threshold = match_threshold
        self.timeout = timeout
        self._now = _now_fn or time.time
        self._session = requests.Session()
        self._lock = threading.Lock()
        # cache: kind → (fetched_epoch, {norm_name: record})
        self._cache: Dict[str, tuple] = {}

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    # ── Fetch ────────────────────────────────────────────────────────

    def _fetch(self, kind: str) -> Dict[str, Dict[str, Any]]:
        """kind: 'pre' or 'in_play'. Returns {norm_name: {field: prob}}."""
        now = self._now()
        with self._lock:
            cached = self._cache.get(kind)
            if cached and now - cached[0] < self.cache_ttl:
                return cached[1]
        if not self.api_key:
            return {}
        path = "/preds/pre-tournament" if kind == "pre" else "/preds/in-play"
        params = {
            "tour": self.tour, "odds_format": "percent",
            "dead_heat": "no",       # true P(top-N incl. ties) — matches Kalshi
            "file_format": "json", "key": self.api_key,
        }
        try:
            r = self._session.get(f"{BASE}{path}", params=params,
                                  timeout=self.timeout)
            if r.status_code != 200:
                logger.warning("DataGolf %s -> HTTP %s", path, r.status_code)
                return {}
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("DataGolf %s failed: %s", path, exc)
            return {}

        index = self._parse(data)
        with self._lock:
            self._cache[kind] = (now, index)
        return index

    @staticmethod
    def _parse(data: Any) -> Dict[str, Dict[str, Any]]:
        """Pull the player-record list from either response shape and index
        it by normalized name. DataGolf nests records under a model key
        (e.g. 'baseline') or returns a bare list."""
        records = None
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            for key in ("baseline", "players", "data", "baseline_history_fit"):
                v = data.get(key)
                if isinstance(v, list) and v:
                    records = v
                    break
            if records is None:  # first list-valued field
                for v in data.values():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        records = v
                        break
        if not records:
            return {}
        index: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            if not isinstance(rec, dict):
                continue
            name = rec.get("player_name") or rec.get("name")
            if not name:
                continue
            probs = {}
            for field in ("win", "top_5", "top_10", "top_20", "top_40",
                          "make_cut"):
                if field in rec:
                    p = _to_prob(rec[field])
                    if p is not None:
                        probs[field] = p
            if probs:
                probs["_dg_id"] = rec.get("dg_id")
                index[_norm_name(name)] = probs
        return index

    # ── Query ────────────────────────────────────────────────────────

    def prob_for(self, title: str, series_ticker: str,
                 kind: str = "pre") -> Optional[float]:
        """Model probability for the top-N implied by `series_ticker`, for the
        player named in `title`. None if unavailable / no confident match."""
        field = SERIES_TO_FIELD.get(series_ticker.split("-")[0])
        if field is None:
            return None
        index = self._fetch(kind)
        if not index:
            return None
        who = player_from_title(title)
        if not who:
            return None
        target = _norm_name(who)
        if target in index:
            return index[target].get(field)
        # fuzzy fallback
        best_name, best_score = None, 0.0
        for name in index:
            s = token_sort_ratio(target, name)
            if s > best_score:
                best_name, best_score = name, s
        if best_name is not None and best_score >= self.match_threshold:
            return index[best_name].get(field)
        return None

    @classmethod
    def from_config(cls, config: dict) -> Optional["DataGolfClient"]:
        """Build from config.datagolf if enabled, else None."""
        cfg = (config.get("datagolf") or {})
        if not cfg.get("enabled"):
            return None
        key = cfg.get("api_key") or os.environ.get("DATAGOLF_API_KEY", "")
        if not key:
            logger.warning("DataGolf enabled but no api_key/DATAGOLF_API_KEY; "
                           "P-017 will use structural edge_bump only.")
            return None
        return cls(
            api_key=key,
            tour=cfg.get("tour", _DEFAULT_TOUR),
            cache_ttl=float(cfg.get("cache_ttl", 900.0)),
            match_threshold=float(cfg.get("match_threshold", 80.0)),
        )
