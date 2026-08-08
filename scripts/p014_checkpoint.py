#!/usr/bin/env python3
"""
scripts/p014_checkpoint.py
──────────────────────────
Sanctioned reader for the P-014 (Live Game Agent) gate.

    python3 -m scripts.p014_checkpoint
    python3 -m scripts.p014_checkpoint --json

Implements `research/P014_DECISION_RULE.md`, **LOCKED 2026-07-31** by Sam
(option (a): as written, §0 contamination recorded). This script implements
§2 (admissibility), §3 (game clustering) and §4 (statistic + schedule) and
nothing else. It is the only sanctioned reader; a P&L chart, a dashboard
tile or an eyeballed jsonl tail is not a verdict.

THE STATISTIC (§3, §4)
──────────────────────
Fee-net edge in ¢/contract, clustered by GAME:

* within a game: contract-weighted mean pnl/contract across admissible fills
  (126 of 129 games carry >1 row; fills in one game settle together);
* across games: EQUAL weight per game;
* `edge = mean(x_g)`, `se = stdev(x_g)/sqrt(G)`, `z = edge/se`.

`n` (progress against the 500 threshold) is counted in ADMISSIBLE SETTLED
ROWS because that is the unit the registry declares; the statistic is
computed on games. Different units on purpose — §6 is about the gap.

PRICES AND SIDES
────────────────
P-014 writes `fill_price: null` on every row; the entry is carried in
`venue_prob`/`kalshi_prob`, which is the **YES price** (it equals the row's
`extra.kalshi_mid_price`). The price of the position actually taken is
therefore `p` for YES and `1 − p` for NO — verified against booked
`pnl_usd`: a NO win at yes-price 0.505, stake $71.43, booked +$72.87 =
stake/(1−p)·p, which only the yes-price convention reproduces.

A row missing any statistic input (price, side, event, stake — or scalar
value on a scalar row) is EXCLUDED and counted loudly, never defaulted.
The precedent is P-015's `or 0.9` fallback, which fabricated its input.

DECISION SCHEDULE (§4, locked — do not renegotiate)
───────────────────────────────────────────────────
  any n   : HARD KILL  if z ≤ −2.0
  n < 500 : NO DECISION (underpowered)
  n ≥ 500 : KILL if edge ≤ 0 · PASS if z ≥ 2.0 · else CONTINUE (one
            extension, byte-identical parameters)
  n ≥ 900 : KILL if still z < 2.0 — the extension is spent

PASS authorises more paper allocation and a written promotion proposal.
It does NOT authorise live money.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# §4: "src.kalshi_fees.fee_per_contract is the only sanctioned source."
from src.kalshi_fees import fee_per_contract  # noqa: E402

POD_ID = "P-014"
THRESHOLD = 500          # §4 — the registry's declared gate, unchanged
EXTENSION_N = 900        # §4 — 500 × 1.8, single extension, then KILL
PASS_Z = 2.0             # §4 — house bar, inherited from P-015/P-022
HARD_KILL_Z = -2.0       # §4 — final at any n

RULE_DOCUMENT = "research/P014_DECISION_RULE.md"

# Same globs as the P-015 reader, for the same reasons: rotation TRUNCATES the
# live file, so a reader that opens only `trade_log.jsonl` under-reports by
# whatever has rotated.
DEFAULT_LOGS = [
    "data/trade_logs/trade_log.jsonl",
    "data/trade_logs/trade_log.archive_*.jsonl",
    "data/trade_logs/trade_log.archive_*.jsonl.gz",
    "data/trade_logs/trade_log.[0-9]*.jsonl",
    "data/trade_logs/trade_log.[0-9]*.jsonl.gz",
    "data/trade_logs/archive/trade_log.archive_*.jsonl",
    "data/trade_logs/archive/trade_log.archive_*.jsonl.gz",
    "data/trade_logs/archive/[0-9][0-9][0-9][0-9]-[0-9][0-9].jsonl",
    "data/trade_logs/archive/[0-9][0-9][0-9][0-9]-[0-9][0-9].jsonl.gz",
    "data/pods/P-014.jsonl",
]

SETTLED = ("WIN", "LOSS")          # VOIDs excluded — no risk taken
ALL_TERMINAL = ("WIN", "LOSS", "VOID")


def _open_any(path: str):
    return (gzip.open(path, "rt", encoding="utf-8", errors="replace")
            if path.endswith(".gz")
            else open(path, "r", encoding="utf-8", errors="replace"))


def load_terminal(patterns, pod_id: str = POD_ID) -> List[Dict[str, Any]]:
    """Every terminal P-014 row (WIN/LOSS/VOID), deduplicated on fingerprint.

    Dedup matters: a rotated log can carry the same settlement twice, and n is
    measured against a pre-registered threshold, so double-counting moves the
    gate rather than merely looking untidy.
    """
    if isinstance(patterns, (str, Path)):
        patterns = [str(patterns)]
    files: List[str] = []
    for pat in patterns:
        files.extend(sorted(glob.glob(str(pat))))

    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(set(files)):
        try:
            with _open_any(path) as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw or pod_id not in raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("pod_id") != pod_id:
                        continue
                    if (rec.get("outcome") or "").upper() not in ALL_TERMINAL:
                        continue
                    key = (rec.get("fingerprint")
                           or f"{rec.get('market_id')}|{rec.get('settled_at_utc')}")
                    out[key] = rec
        except OSError:
            continue
    return list(out.values())


def entry_price(rec: Dict[str, Any]) -> Optional[float]:
    """YES price of the market at entry, or None — never defaulted.

    P-014 writes `fill_price: null` on every row and carries the entry in
    `venue_prob` / `kalshi_prob` (both the YES price). A reader that assumed
    `fill_price` would price nothing; one that substituted a constant would
    fabricate the input to the statistic.
    """
    for field in ("fill_price", "venue_prob", "kalshi_prob"):
        v = rec.get(field)
        if v is None:
            continue
        try:
            p = float(v)
        except (TypeError, ValueError):
            continue
        if 0.0 < p < 1.0:
            return p
    return None


def _taker_fee(price_taken: float, series_ticker: str) -> float:
    """§4: fee at the ACTUAL traded price, rounded up to the cent per contract."""
    per = fee_per_contract(price_taken, maker=False, series_ticker=series_ticker)
    return math.ceil(per * 100.0) / 100.0


def fill_pnl(rec: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Fee-net pnl/contract and contract count for one admissible fill.

    Returns None — with the caller counting WHY — when any input is missing.
    Position-relative accounting: `price` is the cost of the side taken,
    settlement is what that side received ($1/$0 binary, the realised value
    on a scalar), so pnl/ct = settlement − price − fee for both sides.
    """
    outcome = (rec.get("outcome") or "").upper()
    if outcome not in SETTLED:
        return None
    yes_p = entry_price(rec)
    if yes_p is None:
        return None
    side = (rec.get("side") or "").upper()
    if side not in ("YES", "NO"):
        return None
    try:
        stake = float(rec.get("position_size_usd"))
    except (TypeError, ValueError):
        return None
    if not stake > 0:
        return None

    price = yes_p if side == "YES" else 1.0 - yes_p

    # §2: result="scalar" is a partial payout at settlement_value_dollars —
    # never a void, never $0, never excluded (unless the value is unreadable,
    # in which case it is excluded LOUDLY, mirroring the golf settler).
    if (rec.get("result") or "").lower() == "scalar":
        sv = rec.get("settlement_value_dollars", rec.get("settlement_value"))
        try:
            sv = float(sv)
        except (TypeError, ValueError):
            return None
        settlement = sv if side == "YES" else 1.0 - sv
    else:
        settlement = 1.0 if outcome == "WIN" else 0.0

    ticker = rec.get("market_ticker") or rec.get("market_id") or ""
    fee = _taker_fee(price, ticker.split("-")[0])
    return {
        "pnl_ct": settlement - price - fee,     # dollars per contract, fee-net
        "contracts": stake / price,
    }


def cluster_by_game(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """§3: one game = one observation. Returns the statistic and exclusion
    counts. Rows must already be settled (WIN/LOSS) P-014 rows."""
    games: Dict[str, Dict[str, float]] = {}
    n_admissible = 0
    n_unpriced = 0
    n_excluded_other = 0   # missing side/stake/event, or scalar without value

    for rec in rows:
        pr = fill_pnl(rec)
        if pr is None:
            if entry_price(rec) is None:
                n_unpriced += 1
            else:
                n_excluded_other += 1
            continue
        event = rec.get("event")
        if not event:
            n_excluded_other += 1
            continue
        n_admissible += 1
        g = games.setdefault(str(event), {"w_pnl": 0.0, "w": 0.0})
        g["w_pnl"] += pr["pnl_ct"] * pr["contracts"]
        g["w"] += pr["contracts"]

    # x_g in ¢/contract: contract-weighted mean within the game (§3)
    x = [100.0 * g["w_pnl"] / g["w"] for g in games.values() if g["w"] > 0]
    G = len(x)
    edge = se = z = None
    if G >= 2:
        edge = statistics.fmean(x)
        sd = statistics.stdev(x)
        se = sd / math.sqrt(G)
        z = edge / se if se > 0 else None
    elif G == 1:
        edge = x[0]

    return {
        "n_admissible": n_admissible,
        "n_unpriced": n_unpriced,
        "n_excluded_other": n_excluded_other,
        "games": G,
        "edge_cents_per_contract": edge,
        "se_cents": se,
        "z": z,
    }


def decide(n: int, edge: Optional[float], z: Optional[float]) -> Dict[str, str]:
    """§4's decision schedule, verbatim. `n` = admissible settled rows."""
    if z is not None and z <= HARD_KILL_Z:
        return {"verdict": "HARD KILL",
                "reason": (f"z = {z:+.2f} ≤ {HARD_KILL_Z} at n = {n}. Final at "
                           "any n. Stop immediately — no extension, no "
                           "re-parameterisation, no fading the model. (§4, §7)")}
    if n < THRESHOLD:
        return {"verdict": "NO DECISION",
                "reason": (f"n = {n} < {THRESHOLD} — underpowered. Do not act, "
                           "do not scale, do not report a verdict. (§4)")}
    if edge is None or z is None:
        return {"verdict": "NO DECISION",
                "reason": ("n has crossed the threshold but the game-clustered "
                           "statistic could not be computed (fewer than 2 game "
                           "clusters, zero variance, or excluded inputs). "
                           "Investigate before acting — never default.")}
    if edge <= 0:
        return {"verdict": "KILL",
                "reason": (f"edge = {edge:+.2f}¢/ct ≤ 0 at n = {n} ≥ "
                           f"{THRESHOLD}. A strategy whose point estimate is "
                           "not positive after its declared sample has "
                           "failed. (§4)")}
    if z >= PASS_Z:
        return {"verdict": "PASS",
                "reason": (f"edge = {edge:+.2f}¢/ct, z = {z:+.2f} ≥ {PASS_Z} at "
                           f"n = {n}. Authorises more paper allocation and a "
                           "written promotion proposal — NOT live money; live "
                           "is a separate decision by Sam. (§4)")}
    if n >= EXTENSION_N:
        return {"verdict": "KILL",
                "reason": (f"z = {z:+.2f} < {PASS_Z} at n = {n} ≥ "
                           f"{EXTENSION_N}. The single extension is spent. "
                           "There is no second extension and no 'it was "
                           "close'. (§4, §7)")}
    return {"verdict": "CONTINUE",
            "reason": (f"edge = {edge:+.2f}¢/ct > 0 but z = {z:+.2f} < "
                       f"{PASS_Z} at n = {n} ≥ {THRESHOLD}. One extension to "
                       f"n = {EXTENSION_N}, at byte-identical parameters — any "
                       "parameter change is a new hypothesis (P-014b) with its "
                       "own registration and n reset to 0. (§7)")}


def evaluate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """§2 admissibility + §3 clustering + §4 schedule, and nothing else."""
    settled = [r for r in rows if (r.get("outcome") or "").upper() in SETTLED]
    voids = [r for r in rows if (r.get("outcome") or "").upper() == "VOID"]
    wins = sum(1 for r in settled if (r.get("outcome") or "").upper() == "WIN")

    stat = cluster_by_game(settled)
    n = stat["n_admissible"]
    verdict = decide(n, stat["edge_cents_per_contract"], stat["z"])

    days = sorted({(r.get("settled_at_utc") or "")[:10]
                   for r in rows if r.get("settled_at_utc")})
    return {
        "pod": POD_ID,
        "metric": "settled_trades",
        "threshold": THRESHOLD,
        # §4: n is counted in ADMISSIBLE settled rows. An unpriced or otherwise
        # unreadable row shrinks n; it is never papered over.
        "progress": n,
        "n_settled_excl_voids": len(settled),
        "n_voids": len(voids),
        "n_terminal_incl_voids": len(rows),
        "n_unpriced": stat["n_unpriced"],
        "n_excluded_other": stat["n_excluded_other"],
        "wins": wins,
        "losses": len(settled) - wins,
        "first_settlement_utc": days[0] if days else None,
        "last_settlement_utc": days[-1] if days else None,
        "active_days": len(days),
        # §3/§4 — the gate statistic, computed on games
        "games": stat["games"],
        "edge_cents_per_contract": stat["edge_cents_per_contract"],
        "se_cents": stat["se_cents"],
        "z": stat["z"],
        "verdict": verdict["verdict"],
        "reason": verdict["reason"],
        "rule_document": RULE_DOCUMENT,
        "locked_date": "2026-07-31",
        # Descriptive only — the booked paper P&L is not the gate statistic.
        "pnl_usd": round(sum(float(r.get("pnl_usd") or 0) for r in settled), 2),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="P-014 gate reader — implements the LOCKED "
                    "research/P014_DECISION_RULE.md (2026-07-31)")
    ap.add_argument("--log", action="append", default=[],
                    help="log path or glob (repeatable); overrides the defaults")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--unblind", action="store_true",
                    help="deprecated no-op — the statistic prints by default "
                         "since the rule locked on 2026-07-31")
    args = ap.parse_args(argv)

    rows = load_terminal(args.log or DEFAULT_LOGS)
    r = evaluate(rows)

    if args.json:
        print(json.dumps(r, indent=1))
        return 0

    print("P-014 Live Game Agent — gate reader (rule LOCKED 2026-07-31)")
    print("=" * 62)
    print(f"  admissible n   : {r['progress']} of {THRESHOLD}   "
          f"({r['wins']}W / {r['losses']}L, VOIDs excluded)")
    print(f"  incl. VOIDs    : {r['n_terminal_incl_voids']} "
          f"({r['n_voids']} void — excluded per §2, no risk taken)")
    print(f"  span           : {r['first_settlement_utc']} → "
          f"{r['last_settlement_utc']}  ({r['active_days']} active days)")
    if r["n_unpriced"]:
        print(f"  *** UNPRICED   : {r['n_unpriced']} settled row(s) carry no "
              "usable entry price — EXCLUDED, n shrunk (§2)")
    if r["n_excluded_other"]:
        print(f"  *** EXCLUDED   : {r['n_excluded_other']} settled row(s) "
              "missing side/stake/event or scalar value — EXCLUDED, n shrunk")
    print()
    print(f"  game clusters  : {r['games']}   (one game = one observation, §3)")
    if r["edge_cents_per_contract"] is not None:
        se = f"{r['se_cents']:.2f}" if r["se_cents"] is not None else "n/a"
        zs = f"{r['z']:+.2f}" if r["z"] is not None else "n/a"
        print(f"  edge (fee-net) : {r['edge_cents_per_contract']:+.2f} ¢/ct   "
              f"se = {se}   z = {zs}")
    print(f"  paper P&L      : ${r['pnl_usd']:+,.2f}   (descriptive, not the "
          "gate statistic)")
    print()
    print(f"  VERDICT: {r['verdict']}")
    print(f"  {r['reason']}")
    print()
    print(f"  Rule: {RULE_DOCUMENT} (locked 2026-07-31) · "
          "Registry: manager/registry.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
