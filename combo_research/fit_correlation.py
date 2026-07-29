#!/usr/bin/env python3
"""
combo_research/fit_correlation.py
─────────────────────────────────
Fit the leg-correlation parameters that `src/combo_copula.py` prices with.

Why this matters
────────────────
The copula currently ships PRIORS — `same_player 0.35`, `same_game 0.18`,
`same_day 0.02` — and those priors drive the whole Gate 0 correction. On live
data they imply a **+5.3¢** adjustment on same-player combos, which is 1.7× the
entire measured combo edge. A number that large, resting on a guess, is not a
finding; it is a sensitivity. This script replaces the guess with a measurement.

Method — tetrachoric correlation
────────────────────────────────
Two legs are binary outcomes with marginals ``p1``, ``p2`` and an observed joint
``p11 = P(both YES)``. Under the Gaussian copula the latent correlation ρ is the
unique solution of::

    Φ₂( Φ⁻¹(p1), Φ⁻¹(p2) ; ρ )  =  p11

so ρ is recovered by bisection on Φ₂, which is monotone in ρ. This is the
standard tetrachoric estimator and it needs only realised outcomes — no leg
prices, no candlesticks, no historical marks.

Three things that would quietly corrupt the estimate, and how each is handled:

* **Pseudo-replication.** A popular leg pair appears in thousands of combos.
  Counting each occurrence would shrink the confidence interval by orders of
  magnitude while adding no information. Pairs are therefore **deduplicated to
  unique (leg_a, leg_b) sets** before anything is counted.
* **Clustering.** One slate's legs share weather, pace and referees. CIs
  bootstrap over **event days**, never over pairs — the house standard, and the
  same correction that collapsed P-020's β from 0.300 to 0.045.
* **Selection.** Legs only enter the sample because somebody built a combo from
  them, so this measures correlation *among legs retail combines*. That is the
  right population for pricing retail flow, and the wrong one for anything else.
  Stated here so nobody generalises it later.

Usage::

    python3 fit_correlation.py --out combo_research/fitted_rho.json
    python3 fit_correlation.py --archive /var/lib/p029/archive --out ...
    python3 fit_correlation.py --max-combos 20000        # quick pass
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import logging
import math
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.combo_copula import relation  # noqa: E402

API = "https://api.elections.kalshi.com/trade-api/v2"
_NORM = NormalDist()
log = logging.getLogger("fit_rho")

MIN_PAIRS = 30          # below this a block is reported but NOT fitted
BOOT = 4000


# ──────────────────────────────────────────────────────────────────────
# bivariate normal CDF
# ──────────────────────────────────────────────────────────────────────
def _bvn_cdf(z1: float, z2: float, rho: float) -> float:
    """Φ₂(z1, z2; ρ). Exact via scipy when present, else Monte Carlo.

    scipy is available on a laptop but not on the droplet, and this script may
    run in either place, so the fallback is real rather than decorative.
    """
    try:
        from scipy.stats import multivariate_normal
        return float(multivariate_normal(
            mean=[0.0, 0.0], cov=[[1.0, rho], [rho, 1.0]]).cdf([z1, z2]))
    except Exception:
        import numpy as np
        rng = np.random.default_rng(20260729)
        n = 400_000
        a = rng.standard_normal(n)
        b = rho * a + math.sqrt(max(1e-12, 1 - rho * rho)) * rng.standard_normal(n)
        return float(np.mean((a < z1) & (b < z2)))


def tetrachoric(p1: float, p2: float, p11: float) -> Optional[float]:
    """Latent Gaussian correlation implied by two binary marginals and a joint.

    Returns None when the joint is outside the Fréchet bounds — which happens on
    thin samples and means "unidentifiable", not "zero".
    """
    eps = 1e-6
    p1 = min(max(p1, eps), 1 - eps)
    p2 = min(max(p2, eps), 1 - eps)
    lo_b, hi_b = max(0.0, p1 + p2 - 1.0), min(p1, p2)
    if not (lo_b - 1e-9 <= p11 <= hi_b + 1e-9):
        return None
    z1, z2 = _NORM.inv_cdf(p1), _NORM.inv_cdf(p2)
    lo, hi = -0.999, 0.999
    if p11 <= _bvn_cdf(z1, z2, lo):
        return lo
    if p11 >= _bvn_cdf(z1, z2, hi):
        return hi
    for _ in range(60):                       # bisection; Φ₂ is monotone in ρ
        mid = (lo + hi) / 2.0
        if _bvn_cdf(z1, z2, mid) < p11:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ──────────────────────────────────────────────────────────────────────
# data
# ──────────────────────────────────────────────────────────────────────
class Public:
    def __init__(self, min_interval: float = 0.12, timeout: float = 30.0):
        self.min_interval, self.timeout, self._last = min_interval, timeout, 0.0
        self._s = requests.Session()
        self._s.headers.update({"Accept": "application/json",
                                "User-Agent": "p029-fit-rho/1.0"})
        self.calls = 0

    def get(self, path: str, params: Optional[dict] = None,
            tries: int = 5) -> Optional[dict]:
        for attempt in range(tries):
            dt = time.monotonic() - self._last
            if dt < self.min_interval:
                time.sleep(self.min_interval - dt)
            self._last = time.monotonic()
            try:
                r = self._s.get(f"{API}{path}", params=params, timeout=self.timeout)
                self.calls += 1
            except requests.RequestException:
                time.sleep(1.5 * (2 ** attempt))
                continue
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    return None
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (2 ** attempt))
                continue
            log.warning("HTTP %s on %s", r.status_code, path)
            return None
        return None


def combos_from_archive(path: Path, limit: int) -> Iterable[dict]:
    """Read the settled-combo archive written by archive_settled_combos.py."""
    files = sorted(glob.glob(str(path / "combos_*.jsonl.gz")))
    n = 0
    for f in files:
        try:
            with gzip.open(f, "rt") as fh:
                for line in fh:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
                    n += 1
                    if n >= limit:
                        return
        except (OSError, EOFError) as exc:
            log.warning("%s ends early (%s); keeping the readable prefix",
                        os.path.basename(f), type(exc).__name__)


def combos_from_api(pub: Public, limit: int) -> Iterable[dict]:
    """Settled combos straight from Kalshi, for when no archive is to hand.

    Trap: ``status=settled`` is the query FILTER; rows come back reading
    ``finalized``. ``status=finalized`` returns HTTP 400.
    """
    n = 0
    for series in ("KXMVESPORTSMULTIGAMEEXTENDED", "KXMVECROSSCATEGORY",
                   "KXMVENBASINGLEGAME", "KXMVENFLSINGLEGAME"):
        cursor = None
        while n < limit:
            p = {"series_ticker": series, "status": "settled", "limit": 1000}
            if cursor:
                p["cursor"] = cursor
            d = pub.get("/markets", p)
            if not d:
                break
            rows = d.get("markets") or []
            if not rows:
                break
            for m in rows:
                legs = [[l.get("market_ticker"), (l.get("side") or "yes").lower()]
                        for l in (m.get("mve_selected_legs") or [])
                        if l.get("market_ticker")]
                if legs:
                    yield {"legs": legs, "settled_time": m.get("close_time")}
                    n += 1
                    if n >= limit:
                        return
            cursor = d.get("cursor")
            if not cursor:
                break


def fetch_leg_outcomes(pub: Public, tickers: List[str]) -> Dict[str, int]:
    """Settled YES/NO for each leg market.

    Batched with ``?tickers=`` (plural CSV) — the singular ``?ticker=`` is
    SILENTLY IGNORED and returns the unfiltered list with HTTP 200. Batches are
    bounded by CHARACTER budget, not count: combo-era tickers run ~47 chars and
    CloudFront 414s near 9,600.
    """
    out: Dict[str, int] = {}
    batch: List[str] = []
    budget = 0

    def flush() -> None:
        nonlocal batch, budget
        if not batch:
            return
        d = pub.get("/markets", {"tickers": ",".join(batch), "limit": 1000})
        for m in ((d or {}).get("markets") or []):
            res = (m.get("result") or "").lower()
            if res == "yes":
                out[m["ticker"]] = 1
            elif res == "no":
                out[m["ticker"]] = 0
            # `scalar` and empty are deliberately dropped: a partial payout is
            # not a binary outcome, and an empty result means not yet settled.
        batch, budget = [], 0

    for t in tickers:
        if budget + len(t) + 1 > 3500:
            flush()
        batch.append(t)
        budget += len(t) + 1
    flush()
    return out


# ──────────────────────────────────────────────────────────────────────
# fit
# ──────────────────────────────────────────────────────────────────────
def day_of(ticker: str) -> str:
    """Event day from the ticker's embedded date (``26JUL28...``)."""
    parts = ticker.split("-")
    return parts[1][:7] if len(parts) >= 2 and len(parts[1]) >= 7 else "unknown"


def fit(pairs_by_block: Dict[str, Set[Tuple[str, str]]],
        outcomes: Dict[str, int]) -> Dict[str, Any]:
    """Tetrachoric ρ per block, with a day-clustered bootstrap CI."""
    import numpy as np
    rng = np.random.default_rng(20260729)
    result: Dict[str, Any] = {}

    for block, pairs in sorted(pairs_by_block.items()):
        usable = [(a, b) for a, b in pairs
                  if a in outcomes and b in outcomes]
        if len(usable) < MIN_PAIRS:
            result[block] = {"n_pairs": len(usable), "rho": None,
                             "note": f"under the {MIN_PAIRS}-pair floor — not fitted"}
            continue

        def point(sample: List[Tuple[str, str]]) -> Optional[float]:
            if not sample:
                return None
            p1 = sum(outcomes[a] for a, _ in sample) / len(sample)
            p2 = sum(outcomes[b] for _, b in sample) / len(sample)
            p11 = sum(1 for a, b in sample
                      if outcomes[a] and outcomes[b]) / len(sample)
            return tetrachoric(p1, p2, p11)

        rho = point(usable)

        # Bootstrap over EVENT DAYS, not pairs. Resampling pairs would treat one
        # slate's legs as independent draws and produce an absurdly tight CI.
        by_day: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        for a, b in usable:
            by_day[day_of(a)].append((a, b))
        days = list(by_day)
        boots: List[float] = []
        if len(days) >= 3:
            for _ in range(BOOT):
                pick = rng.integers(0, len(days), len(days))
                sample: List[Tuple[str, str]] = []
                for i in pick:
                    sample.extend(by_day[days[i]])
                v = point(sample)
                if v is not None:
                    boots.append(v)

        p1 = sum(outcomes[a] for a, _ in usable) / len(usable)
        p2 = sum(outcomes[b] for _, b in usable) / len(usable)
        p11 = sum(1 for a, b in usable if outcomes[a] and outcomes[b]) / len(usable)
        identified = len(days) >= 3
        result[block] = {
            "n_pairs": len(usable),
            "n_days": len(days),
            "identified": identified,
            "note": (None if identified else
                     "SINGLE SLATE -- rho is confounded with that day's outcome "
                     "rate and must NOT be used; widen the sample"),
            "marginal_a": round(p1, 4),
            "marginal_b": round(p2, 4),
            "joint_observed": round(p11, 4),
            "joint_if_independent": round(p1 * p2, 4),
            "rho": None if rho is None else round(rho, 4),
            "ci95": ([round(float(np.percentile(boots, 2.5)), 4),
                      round(float(np.percentile(boots, 97.5)), 4)]
                     if len(boots) >= 100 else None),
        }
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", type=str, default=None,
                    help="settled-combo archive dir (else pull from the API)")
    ap.add_argument("--out", type=str, default="combo_research/fitted_rho.json")
    ap.add_argument("--max-combos", type=int, default=60_000)
    ap.add_argument("--max-legs", type=int, default=6,
                    help="skip combos with more legs — pair count grows as n²")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    pub = Public()
    src = (combos_from_archive(Path(a.archive), a.max_combos) if a.archive
           else combos_from_api(pub, a.max_combos))

    # Collect candidate combos first; the pair set is built only AFTER leg
    # outcomes are known, because of the selection trap below.
    candidates: List[List[str]] = []
    legs_seen: Set[str] = set()
    for c in src:
        legs = [l[0] for l in (c.get("legs") or []) if l and l[0]]
        if not (2 <= len(legs) <= a.max_legs):
            continue
        candidates.append(legs)
        legs_seen.update(legs)

    log.info("candidate combos %d | unique legs %d", len(candidates), len(legs_seen))
    log.info("fetching settled outcomes for %d legs...", len(legs_seen))
    outcomes = fetch_leg_outcomes(pub, sorted(legs_seen))
    log.info("resolved %d/%d legs (%.1f%%) in %d API calls",
             len(outcomes), len(legs_seen),
             100.0 * len(outcomes) / max(1, len(legs_seen)), pub.calls)

    # ⚠️ SELECTION TRAP — measured on live data 2026-07-29.
    # A SETTLED COMBO CAN HAVE UNSETTLED LEGS: the combo resolves NO the moment
    # any single leg fails, so the remaining legs never resolve at all. 43 of 60
    # legs sampled from settled combos were still `active` with an empty result.
    # Keeping only the resolved legs would therefore select precisely the legs
    # that DECIDED the outcome -- a sample conditioned on the very thing being
    # measured, which would bias rho upward without limit.
    # A combo is used only when EVERY one of its legs settled.
    pairs_by_block: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
    n_combos = dropped = 0
    for legs in candidates:
        if not all(t in outcomes for t in legs):
            dropped += 1
            continue
        n_combos += 1
        for i in range(len(legs)):
            for j in range(i + 1, len(legs)):
                x, y = sorted((legs[i], legs[j]))
                if x != y:
                    pairs_by_block[relation(x, y)].add((x, y))
    log.info("combos with ALL legs settled: %d (dropped %d partially-settled)",
             n_combos, dropped)
    if n_combos and dropped / (n_combos + dropped) > 0.5:
        log.warning("over half the combos were dropped as partially settled -- "
                    "the sample is too fresh. Point --archive at data that is "
                    "several days old, or the estimate will rest on one slate.")

    log.info("unique pairs %d", sum(len(v) for v in pairs_by_block.values()))
    for b, v in sorted(pairs_by_block.items(), key=lambda kv: -len(kv[1])):
        log.info("  %-12s %d unique pairs", b, len(v))

    fitted = fit(pairs_by_block, outcomes)

    print("\n=== FITTED LEG CORRELATION ===")
    print(f"{'block':<12} {'pairs':>7} {'days':>5} {'P(both)':>9} "
          f"{'indep':>8} {'rho':>8} {'95% CI':>20}")
    for block, r in fitted.items():
        if r.get("rho") is None:
            print(f"{block:<12} {r['n_pairs']:>7} {'':>5} {'':>9} {'':>8} "
                  f"{'n/a':>8}   {r.get('note','')}")
            continue
        ci = r["ci95"]
        cis = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "(too few days)"
        print(f"{block:<12} {r['n_pairs']:>7} {r['n_days']:>5} "
              f"{r['joint_observed']:>9.4f} {r['joint_if_independent']:>8.4f} "
              f"{r['rho']:>+8.3f} {cis:>20}")

    payload = {
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "n_combos": n_combos,
        "n_legs_resolved": len(outcomes),
        "blocks": fitted,
        "caveat": ("Measured among legs RETAIL CHOSE TO COMBINE — the right "
                   "population for pricing retail flow, the wrong one for "
                   "anything else. Blocks under the pair floor are unfitted; "
                   "keep the prior and say so."),
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(payload, indent=1))
    print(f"\nwritten: {a.out}")
    print("Load into the pricer with ComboCopula(rho=load_rho(path)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
