"""
src/learner.py
──────────────
Phase 10: rolling probability calibration + logistic regression learner.

Workflow
────────
  1. After each resolved trade, call ``learner.update(...)`` to add the
     observed outcome to the rolling window.
  2. Call ``learner.train()`` to fit a logistic regression model on the
     current window (no-op until min_trades_for_adjustment records exist).
  3. ``learner.adjust_prob(fair_prob, edge_pct, sport)`` returns a
     calibrated probability shifted by at most ±max_adjustment_pct.
  4. Persist state between runs via ``save()`` / ``load()``.

Calibration strategy
────────────────────
  Below min_trades
    Global bias only: ``bias = mean(actual_wins) - mean(fair_probs)``.
    The fair_prob is shifted by the clamped bias.

  At / above min_trades
    Logistic regression on features ``[fair_prob, edge_pct]`` (implemented
    with numpy gradient descent — no scikit-learn required).  The
    calibration delta (``logistic_prob - fair_prob``) is clamped to
    ``±max_adjustment_pct`` before being applied.

  Per-sport
    A sport-level bias is tracked separately and applied when the
    sport has ≥ 10 resolved trades and no logistic model is available.

State file (JSON)
─────────────────
  {
    "version": 1,
    "records": [
      {"fair_prob": 0.60, "kalshi_prob": 0.55, "edge_pct": 0.05,
       "ev": 0.02, "sport": "nfl", "outcome": 1},
      ...
    ],
    "logistic_weights": [w_fair, w_edge, bias],   // null until trained
    "last_trained_n": 0,
    "last_updated": "2024-01-15T18:00:00Z"
  }
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from logger_setup import get_logger

log = get_logger(__name__)

_RECORD_FIELDS = ("fair_prob", "kalshi_prob", "edge_pct", "ev", "sport", "outcome")
_MIN_SPORT_RECORDS = 10      # minimum resolved trades for a per-sport bias
_DEFAULT_MAX_RECORDS = 500   # rolling window cap
_LR_RATE = 0.1               # logistic gradient-descent learning rate
_LR_ITERS = 300              # gradient-descent iterations


# ── Numeric helpers ────────────────────────────────────────────────────────────

def _sigmoid(z: np.ndarray) -> np.ndarray:
    """Numerically stable sigmoid."""
    return 1.0 / (1.0 + np.exp(-np.clip(z, -500.0, 500.0)))


def _fit_logistic(
    X: np.ndarray,   # (n, p) feature matrix
    y: np.ndarray,   # (n,) binary labels 0 / 1
    lr: float = _LR_RATE,
    n_iter: int = _LR_ITERS,
) -> np.ndarray:
    """
    Fit binary logistic regression via batch gradient descent.

    Returns weights of shape ``(p + 1,)``; the last element is the bias term.
    """
    n, p = X.shape
    X_aug = np.column_stack([X, np.ones(n)])
    w = np.zeros(p + 1)
    for _ in range(n_iter):
        pred = _sigmoid(X_aug @ w)
        grad = X_aug.T @ (pred - y) / n
        w -= lr * grad
    return w


def _global_bias(records: List[Dict]) -> float:
    """
    Compute mean(actual_wins) - mean(fair_probs) over resolved records.
    Returns 0.0 if the list is empty.
    """
    if not records:
        return 0.0
    outcomes  = [r["outcome"] for r in records]
    fair_probs = [r["fair_prob"] for r in records]
    return float(np.mean(outcomes)) - float(np.mean(fair_probs))


def _sport_bias(records: List[Dict], sport: str) -> Optional[float]:
    """Per-sport bias, or None if fewer than _MIN_SPORT_RECORDS records."""
    sp_recs = [r for r in records if r.get("sport") == sport]
    if len(sp_recs) < _MIN_SPORT_RECORDS:
        return None
    return float(np.mean([r["outcome"] for r in sp_recs])) - \
           float(np.mean([r["fair_prob"] for r in sp_recs]))


# ── Main class ─────────────────────────────────────────────────────────────────

class Learner:
    """
    Rolling probability calibrator with optional logistic regression.

    Parameters
    ----------
    min_trades : int
        Minimum resolved trades before logistic regression is trained.
    max_adjustment_pct : float
        Maximum absolute shift applied to fair_prob (e.g. 0.03 = ±3%).
    max_records : int
        Rolling-window capacity (oldest records evicted when exceeded).
    model_state_path : Path, optional
        JSON file to persist state between runs.
    enabled : bool
        If False, ``adjust_prob`` returns fair_prob unchanged.
    _now_fn : callable, optional
        Injects current UTC time (override in tests).
    """

    def __init__(
        self,
        min_trades: int = 200,
        max_adjustment_pct: float = 0.03,
        max_records: int = _DEFAULT_MAX_RECORDS,
        model_state_path: Optional[Path] = None,
        enabled: bool = True,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._min_trades = min_trades
        self._max_adj = max_adjustment_pct
        self._max_records = max_records
        self._path = model_state_path
        self._enabled = enabled
        self._now_fn = _now_fn or (lambda: datetime.now(tz=timezone.utc))

        self._records: List[Dict[str, Any]] = []
        self._logistic_w: Optional[np.ndarray] = None
        self._last_trained_n: int = 0

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict, **kwargs) -> "Learner":
        """Construct from config.yaml dict; optionally load persisted state."""
        lc = config.get("learner", {})
        enabled  = lc.get("enabled", False)
        min_tr   = lc.get("min_trades_for_adjustment", 200)
        max_adj  = lc.get("max_adjustment_pct", 0.03)
        state_p  = lc.get("model_state_path", "data/model_state.json")

        learner = cls(
            min_trades=min_tr,
            max_adjustment_pct=max_adj,
            model_state_path=Path(state_p),
            enabled=enabled,
            **kwargs,
        )
        if learner._path and learner._path.exists():
            learner.load()
        return learner

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def n_resolved(self) -> int:
        """Number of WIN or LOSS records in the rolling window."""
        return len(self._records)

    def update(
        self,
        fair_prob: float,
        kalshi_prob: float,
        edge_pct: float,
        ev: float,
        sport: Optional[str],
        outcome: str,          # "WIN" | "LOSS"
    ) -> None:
        """
        Append one resolved trade to the rolling window.
        VOID / PENDING / unrecognised outcomes are silently ignored.
        """
        outcome_upper = (outcome or "").upper()
        if outcome_upper not in ("WIN", "LOSS"):
            return
        record = {
            "fair_prob":   float(fair_prob),
            "kalshi_prob": float(kalshi_prob),
            "edge_pct":    float(edge_pct),
            "ev":          float(ev),
            "sport":       sport or "unknown",
            "outcome":     1 if outcome_upper == "WIN" else 0,
        }
        self._records.append(record)
        # Evict oldest if over capacity
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records:]

    def train(self) -> bool:
        """
        Fit the logistic model on current records.

        Returns True if training ran, False if skipped (insufficient data).
        """
        if self.n_resolved < self._min_trades:
            log.info(
                "learner_train_skipped",
                n_resolved=self.n_resolved,
                min_trades=self._min_trades,
            )
            return False

        X = np.array([[r["fair_prob"], r["edge_pct"]] for r in self._records])
        y = np.array([r["outcome"]                    for r in self._records], dtype=float)
        self._logistic_w = _fit_logistic(X, y)
        self._last_trained_n = self.n_resolved
        log.info(
            "learner_trained",
            n_records=self.n_resolved,
            weights=self._logistic_w.tolist(),
        )
        return True

    def adjust_prob(
        self,
        fair_prob: float,
        edge_pct: float = 0.0,
        sport: Optional[str] = None,
    ) -> float:
        """
        Return a calibrated probability for the given fair_prob.

        If the learner is disabled or has no records, returns fair_prob.
        Adjustment is capped at ±max_adjustment_pct and the result is
        clamped to [0.01, 0.99].
        """
        if not self._enabled or not self._records:
            return fair_prob

        if self._logistic_w is not None:
            # Logistic calibration
            x = np.array([fair_prob, edge_pct])
            x_aug = np.append(x, 1.0)
            logistic_prob = float(_sigmoid(x_aug @ self._logistic_w))
            delta = logistic_prob - fair_prob
        elif sport is not None:
            # Per-sport bias if available, else global
            sp_bias = _sport_bias(self._records, sport)
            delta = sp_bias if sp_bias is not None else _global_bias(self._records)
        else:
            delta = _global_bias(self._records)

        clamped = max(-self._max_adj, min(self._max_adj, delta))
        result  = fair_prob + clamped
        return max(0.01, min(0.99, result))

    def stats(self) -> Dict[str, Any]:
        """Return a summary dict for logging / dashboard display."""
        if not self._records:
            return {"n_resolved": 0, "trained": False, "global_bias": 0.0}

        bias = _global_bias(self._records)
        sp_set = {r["sport"] for r in self._records}
        sport_biases = {
            sp: round(b, 4)
            for sp in sp_set
            if (b := _sport_bias(self._records, sp)) is not None
        }
        return {
            "n_resolved":       self.n_resolved,
            "trained":          self._logistic_w is not None,
            "last_trained_n":   self._last_trained_n,
            "global_bias":      round(bias, 4),
            "by_sport":         sport_biases,
            "logistic_weights": (self._logistic_w.tolist()
                                 if self._logistic_w is not None else None),
        }

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> bool:
        """
        Persist state to JSON.  Uses ``self._path`` if path is None.
        Returns True on success, False on error.
        """
        target = path or self._path
        if target is None:
            log.warning("learner_save_no_path")
            return False
        state = {
            "version":         1,
            "records":         self._records,
            "logistic_weights": (self._logistic_w.tolist()
                                 if self._logistic_w is not None else None),
            "last_trained_n":  self._last_trained_n,
            "last_updated":    self._now_fn().isoformat(),
        }
        try:
            target = Path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(state, indent=2), encoding="utf-8")
            log.info("learner_saved", path=str(target), n=self.n_resolved)
            return True
        except OSError as exc:
            log.warning("learner_save_error", path=str(target), error=str(exc))
            return False

    def load(self, path: Optional[Path] = None) -> bool:
        """
        Load state from JSON.  Uses ``self._path`` if path is None.
        Returns True on success, False if missing or unreadable.
        """
        target = path or self._path
        if target is None or not Path(target).exists():
            return False
        try:
            raw = json.loads(Path(target).read_text(encoding="utf-8"))
            self._records = raw.get("records", [])
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records:]
            w = raw.get("logistic_weights")
            self._logistic_w = np.array(w) if w is not None else None
            self._last_trained_n = raw.get("last_trained_n", 0)
            log.info("learner_loaded", path=str(target), n=self.n_resolved)
            return True
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.warning("learner_load_error", path=str(target), error=str(exc))
            return False
