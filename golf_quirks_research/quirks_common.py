"""
golf_quirks_research/quirks_common.py
─────────────────────────────────────
Shared primitives for the P-022 / P-023 golf settlement-quirk harnesses.

REBUILT 2026-07-26. The original `pull_trades.py` / `backtest_fade_fills.py`
were written 2026-07-23 and lost on 2026-07-25 (never committed to git). This
module is a reconstruction from the two surviving reports:

  REPORT_Golf_Quirks_2026-07.md            (Phase 1 — settled data, §2 method)
  REPORT_Golf_Quirks_Phase2_P022_2026-07.md (Phase 2 — tick replay, §1 method)

The reconstruction is *validated*, not merely plausible: with the constants
below (`ANCHOR_MAX_SPREAD = 0.10`, cent-rounded quotes, contract-weighted
tournament-clustered bootstrap at seed 12345) it reproduces
  - the 364-ticker P-022 universe exactly (0 missing / 0 extra vs the cache),
  - every cell of the published Phase-2 fill table, and
  - the published Phase-1 make-cut gate rows (PGA and DP World).
See `backtest_fade_fills.py --validate` and the REPORT for the side-by-side.

House methodology encoded here (all of it load-bearing — see CLAUDE.md and
RESEARCH_TESTING_BRIEF §7):
  * Bootstrap CIs cluster by TOURNAMENT, never per contract (per-contract
    resampling is how P-017M produced a phantom +9.1c).
  * Anchors use executed prints or TIGHT TWO-SIDED quotes only. A bare ask on
    an empty book is rejected — trusting them fabricated a false +50c edge in
    an early Phase-1 draft.
  * `last_price` / the literal final candle are settlement-contaminated on any
    market that trades through its own determining period. Never used.
  * Realized payout is `settlement_value_dollars`, read directly, so a
    `result="scalar"` row books its true $1/n (leader) or refund (top-N) value
    rather than being coerced to 0/1.
"""
from __future__ import annotations

import json
import math
import os
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CANDLE_DIR = os.path.join(DATA, "candles")
SETTLED_META = os.path.join(DATA, "settled_meta.jsonl")

# Max bid/ask width accepted for a two-sided anchor quote. 0.10 is the value
# that reproduces the published 364-market P-022 universe exactly.
ANCHOR_MAX_SPREAD = 0.10

# Bootstrap settings — fixed so results are reproducible run to run.
N_BOOT = 5000
BOOT_SEED = 12345

LEADER_SERIES = (
    "KXPGAR1LEAD", "KXPGAR2LEAD", "KXPGAR3LEAD",
    "KXDPWORLDTOURR1LEAD", "KXDPWORLDTOURR2LEAD", "KXDPWORLDTOURR3LEAD",
    "KXLPGAR1LEAD", "KXLPGAR2LEAD", "KXLPGAR3LEAD",
    "KXLIVR1LEAD", "KXLIVR2LEAD", "KXLIVR3LEAD",
    "KXCHAMPTOURR1LEAD",
)
MAKECUT_SERIES_PGA = ("KXPGAMAKECUT",)
MAKECUT_SERIES_DPW = ("KXDPWORLDTOURMAKECUT",)
LIV_TOPN_SERIES = ("KXLIVTOP5", "KXLIVTOP10")
# P-023c cohorts. The published P-023a gate anchors the full-tournament book
# at 48h (pre-R1) and the round-based book at 12h (pre-round) — verified by
# exact replication of all seven published cells in backtest_topn_fade.py.
FULL_TOPN_SERIES = ("KXPGATOP5", "KXPGATOP10", "KXPGATOP20", "KXPGATOP40")
ROUND_TOPN_SERIES = ("KXPGAR1TOP5", "KXPGAR1TOP10",
                     "KXPGAR2TOP10", "KXPGAR3TOP10")


# ── scalars ──────────────────────────────────────────────────────────────

def fnum(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def iso_epoch(s: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError, TypeError):
        return None


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def series_of(ticker: str) -> str:
    return ticker.split("-")[0]


def tournament_of(event_ticker: str) -> str:
    """'USO26' from 'KXPGAMAKECUT-USO26' — the bootstrap cluster key."""
    parts = (event_ticker or "").split("-")
    return parts[1] if len(parts) >= 2 else event_ticker


# ── candle cache ─────────────────────────────────────────────────────────

def candle_path(ticker: str) -> str:
    return os.path.join(CANDLE_DIR, f"{ticker}.json")


def load_candle_rec(ticker: str) -> Optional[Dict[str, Any]]:
    p = candle_path(ticker)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as fh:
            rec = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
    return rec if rec.get("candles") else None


def iter_candle_recs(series: Sequence[str]) -> Iterable[Dict[str, Any]]:
    """Yield every cached candle record belonging to `series`."""
    want = set(series)
    try:
        names = sorted(os.listdir(CANDLE_DIR))
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        ticker = name[:-5]
        if series_of(ticker) not in want:
            continue
        rec = load_candle_rec(ticker)
        if rec is not None:
            yield rec


def candle_price(c: Dict[str, Any],
                 max_spread: float = ANCHOR_MAX_SPREAD) -> Optional[float]:
    """Information-clean reference price for ONE candle, or None.

    Preference order (Phase-1 §2 / §3.2):
      1. the candle's executed close price, if the bar actually traded;
      2. the mid of a TIGHT two-sided quote (bid > 0, ask < 1, ask >= bid,
         width <= max_spread).
    A one-sided book is rejected outright — illiquid golf props park phantom
    asks (a lone 0.98 sell on a name trading at 0.01) and using them
    fabricates edge.
    """
    traded = (c.get("price") or {}).get("close_dollars")
    if traded is not None and (fnum(c.get("volume_fp")) or 0.0) > 0:
        px = fnum(traded)
        if px is not None:
            return px
    bid = fnum((c.get("yes_bid") or {}).get("close_dollars"))
    ask = fnum((c.get("yes_ask") or {}).get("close_dollars"))
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask >= 1 or ask < bid or (ask - bid) > max_spread:
        return None
    return (ask + bid) / 2.0


def anchor_at(rec: Dict[str, Any], hours_before_close: float,
              max_spread: float = ANCHOR_MAX_SPREAD
              ) -> Optional[Tuple[float, float]]:
    """(candle_end_epoch, price) of the LATEST clean candle at or before
    T = close - H hours. None if the market has no clean price that early.

    This is the pre-event anchor. It exists because the literal close is
    mechanically equal to the settlement value on any market that trades
    through its determining round — anchoring there manufactures efficiency.
    """
    close = iso_epoch(rec.get("close_time"))
    if close is None:
        return None
    target = close - hours_before_close * 3600.0
    best: Optional[Tuple[float, float]] = None
    for c in rec.get("candles") or []:
        end = c.get("end_period_ts")
        if end is None or end > target:
            continue
        px = candle_price(c, max_spread)
        if px is None:
            continue
        if best is None or end > best[0]:
            best = (float(end), px)
    return best


def build_universe(series: Sequence[str], hours_before_close: float,
                   band: Tuple[float, float],
                   max_spread: float = ANCHOR_MAX_SPREAD
                   ) -> List[Dict[str, Any]]:
    """Markets whose H-hour pre-close anchor sits inside `band`, with the
    anchor and realized settlement value attached. Sorted by ticker."""
    lo, hi = band
    out: List[Dict[str, Any]] = []
    for rec in iter_candle_recs(series):
        a = anchor_at(rec, hours_before_close, max_spread)
        if a is None:
            continue
        end, px = a
        if not (lo <= px <= hi):
            continue
        close = iso_epoch(rec["close_time"])
        out.append({
            "ticker": rec["ticker"],
            "event": rec.get("event", ""),
            "tournament": tournament_of(rec.get("event", "")),
            "series": series_of(rec["ticker"]),
            "close_time": rec["close_time"],
            "close_epoch": close,
            "anchor_epoch": end,
            "anchor_price": px,
            "anchor_lag_h": (close - end) / 3600.0 if close else None,
            "result": rec.get("result"),
            "settlement_value_dollars": rec.get("settlement_value_dollars"),
        })
    out.sort(key=lambda r: r["ticker"])
    return out


# ── trade cache ──────────────────────────────────────────────────────────

def load_trade_recs(trade_dir: str) -> List[Dict[str, Any]]:
    """Every cached tick file in `trade_dir`, with its candle record attached
    as `_candles`. Markets with no candle history are dropped (no anchor)."""
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(trade_dir):
        return out
    for name in sorted(os.listdir(trade_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(trade_dir, name)) as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        if not rec.get("trades"):
            continue
        crec = load_candle_rec(rec["ticker"])
        if crec is None:
            continue
        rec["_candles"] = crec
        rec["tournament"] = tournament_of(rec.get("event", ""))
        out.append(rec)
    return out


def settlement_value(rec: Dict[str, Any]) -> Optional[float]:
    """Realized per-contract YES payout in dollars.

    `settlement_value_dollars` is authoritative and already correct for
    `result="scalar"` (leader dead-heat $1/n, top-N withdrawal refund), which
    is why this harness reads it directly instead of going through
    `src/kalshi_golf_settler.py` — that settler still voids scalar rows.
    """
    sv = fnum(rec.get("settlement_value_dollars"))
    if sv is not None:
        return sv
    res = rec.get("result")
    if res == "yes":
        return 1.0
    if res == "no":
        return 0.0
    return None


# ── clustered bootstrap ──────────────────────────────────────────────────

def bootstrap_weighted(entries: Sequence[Tuple[str, float, float]],
                       n_boot: int = N_BOOT,
                       seed: int = BOOT_SEED
                       ) -> Tuple[float, float, float]:
    """Contract-weighted mean and 95% CI, resampling TOURNAMENTS with
    replacement.

    `entries` are (tournament, pnl_per_contract, contracts). Each tournament
    is ONE observation: within a tournament every name is decided by the same
    leaderboard, so per-contract resampling would understate the CI by an
    order of magnitude. This is the P-017M lesson, hard-coded.
    """
    if not entries:
        return 0.0, float("nan"), float("nan")
    by: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for ev, pnl, w in entries:
        by[ev].append((pnl, w))
    keys = list(by.keys())
    total_w = sum(w for _, _, w in entries)
    if total_w <= 0:
        return 0.0, float("nan"), float("nan")
    mean = sum(p * w for _, p, w in entries) / total_w
    if len(keys) < 2:
        return mean, float("nan"), float("nan")
    rng = random.Random(seed)
    means: List[float] = []
    for _ in range(n_boot):
        num = den = 0.0
        for _ in range(len(keys)):
            for p, w in by[keys[rng.randrange(len(keys))]]:
                num += p * w
                den += w
        if den > 0:
            means.append(num / den)
    means.sort()
    return mean, means[int(0.025 * len(means))], means[int(0.975 * len(means))]


def bootstrap_unweighted(entries: Sequence[Tuple[str, float]],
                         n_boot: int = N_BOOT,
                         seed: int = BOOT_SEED
                         ) -> Tuple[float, float, float]:
    """Per-market mean and 95% CI, clustered by tournament. Used for the
    settled-data (Phase-1) gate where every market is one equal-weight bet."""
    if not entries:
        return 0.0, float("nan"), float("nan")
    by: Dict[str, List[float]] = defaultdict(list)
    for ev, v in entries:
        by[ev].append(v)
    keys = list(by.keys())
    mean = sum(v for _, v in entries) / len(entries)
    if len(keys) < 2:
        return mean, float("nan"), float("nan")
    rng = random.Random(seed)
    means: List[float] = []
    for _ in range(n_boot):
        pool: List[float] = []
        for _ in range(len(keys)):
            pool.extend(by[keys[rng.randrange(len(keys))]])
        if pool:
            means.append(sum(pool) / len(pool))
    means.sort()
    return mean, means[int(0.025 * len(means))], means[int(0.975 * len(means))]


def per_tournament(entries: Sequence[Tuple[str, float, float]]
                   ) -> Dict[str, Dict[str, float]]:
    num: Dict[str, float] = defaultdict(float)
    den: Dict[str, float] = defaultdict(float)
    for ev, pnl, w in entries:
        num[ev] += pnl * w
        den[ev] += w
    return {ev: {"contracts": round(den[ev], 1),
                 "net_per_contract": round(num[ev] / den[ev], 4)}
            for ev in sorted(den) if den[ev] > 0}


def leave_one_out(entries: Sequence[Tuple[str, float, float]]
                  ) -> Dict[str, float]:
    """Contract-weighted mean with each tournament removed in turn."""
    keys = sorted({ev for ev, _, _ in entries})
    out: Dict[str, float] = {}
    for k in keys:
        sub = [e for e in entries if e[0] != k]
        w = sum(x[2] for x in sub)
        out[k] = round(sum(p * c for _, p, c in sub) / w, 4) if w > 0 else float("nan")
    return out


# ── maker-fill replay ────────────────────────────────────────────────────

def replay(recs: Sequence[Dict[str, Any]], side: str, hours: float,
           offset: float, quote_size: float = 25.0,
           window_end_h: float = 0.0, strict: bool = True,
           max_spread: float = ANCHOR_MAX_SPREAD,
           round_quote: bool = True,
           post_at_anchor: bool = False) -> Dict[str, Any]:
    """Rest ONE maker quote per market and replay the public tape against it.

    side = "sell_yes"  (P-022 fade)  — rest a YES ask at anchor + offset.
                                       Fills on `taker_side="yes"` prints
                                       (a buyer lifting asks) whose price is
                                       strictly THROUGH the quote.
                                       PnL/ct = quote_px - settlement_value.
    side = "buy_yes"   (P-023 make-cut) — rest a YES bid at anchor - offset.
                                       Fills on `taker_side="no"` prints
                                       (a seller hitting bids) whose price is
                                       strictly THROUGH the quote.
                                       PnL/ct = settlement_value - quote_px.

    "Strictly through" is the pessimistic convention: a print exactly AT our
    price might have been someone else's resting order ahead of us in queue,
    so we refuse to claim it. `strict=False` reports the friendlier
    at-or-through variant for comparison.

    The quote is posted at T = close - `hours` and lives until
    close - `window_end_h`; per-market fills are capped at `quote_size`
    contracts. Maker fee is ZERO — every series here is Kalshi `quadratic`
    (verified live from /series metadata; see src/kalshi_fees.py).

    Quotes are rounded to the cent, which is why a market whose anchor has
    decayed below half a cent drops out of the posted count at offset 0 but
    reappears at offset +0.02. That quirk is present in the published P-022
    table and is reproduced here.
    """
    if side not in ("sell_yes", "buy_yes"):
        raise ValueError(f"bad side {side!r}")
    entries: List[Tuple[str, float, float]] = []   # (tourn, pnl/ct, contracts)
    posted = 0
    posted_sv: List[float] = []
    posted_q: List[float] = []
    posted_tourns: set = set()
    filled_markets = 0
    contracts = 0.0
    sv_fill_num = 0.0
    q_fill_num = 0.0
    collateral = 0.0            # dollars at risk on the filled contracts
    pnl_usd = 0.0
    worst_market = None
    skipped_no_anchor = 0

    for rec in recs:
        a = anchor_at(rec["_candles"], hours, max_spread)
        if a is None:
            skipped_no_anchor += 1
            continue
        anchor_px = a[1]
        raw_q = anchor_px + offset if side == "sell_yes" else anchor_px - offset
        quote_px = round(raw_q, 2) if round_quote else raw_q
        if quote_px <= 0.0 or quote_px >= 1.0:
            continue
        sv = settlement_value(rec)
        if sv is None:
            continue
        close = iso_epoch(rec.get("close_time"))
        if close is None:
            continue
        # The quote goes live at T = close - `hours` by default, which is the
        # published P-022 convention. But the anchor is the latest CLEAN
        # candle at or before T, and on quiet books it can predate T by many
        # hours — median 20h on make-cut, where the market only prints during
        # tour business hours. Quoting a 20h-stale price is not an execution
        # model anyone could run. `post_at_anchor` instead goes live at the
        # moment the anchor was observed, so the quote is contemporaneous
        # with the price it is derived from. (On P-022's leader universe the
        # median staleness is 1h, so the two agree there.)
        t_start = a[0] if post_at_anchor else close - hours * 3600.0
        t_end = close - window_end_h * 3600.0
        if t_end <= t_start:
            continue

        posted += 1
        posted_sv.append(sv)
        posted_q.append(quote_px)
        tourn = rec.get("tournament") or tournament_of(rec.get("event", ""))
        posted_tourns.add(tourn)

        want_taker = "yes" if side == "sell_yes" else "no"
        remaining = quote_size
        filled = 0.0
        for t in rec["trades"]:
            e = t.get("epoch")
            px = t.get("yes_price")
            cnt = t.get("count") or 0.0
            if e is None or px is None or cnt <= 0:
                continue
            if e < t_start or e > t_end:
                continue
            if t.get("taker_side") != want_taker:
                continue
            if side == "sell_yes":
                through = px > quote_px + 1e-9 if strict else px >= quote_px - 1e-9
            else:
                through = px < quote_px - 1e-9 if strict else px <= quote_px + 1e-9
            if not through:
                continue
            take = min(remaining, cnt)
            if take <= 0:
                continue
            pnl = (quote_px - sv) if side == "sell_yes" else (sv - quote_px)
            entries.append((tourn, pnl, take))
            filled += take
            remaining -= take
            if remaining <= 1e-9:
                break

        if filled > 0:
            filled_markets += 1
            contracts += filled
            sv_fill_num += sv * filled
            q_fill_num += quote_px * filled
            pnl = (quote_px - sv) if side == "sell_yes" else (sv - quote_px)
            pnl_usd += pnl * filled
            # collateral at risk: what a fill can lose in the worst case
            risk = (1.0 - quote_px) if side == "sell_yes" else quote_px
            collateral += risk * filled
            if worst_market is None or pnl * filled < worst_market[1]:
                worst_market = (rec["ticker"], round(pnl * filled, 2),
                                round(quote_px, 4), round(sv, 4), round(filled, 1))

    mean, lo, hi = bootstrap_weighted(entries)
    pt = per_tournament(entries)
    return {
        "side": side, "hours": hours, "offset": offset,
        "quote_size": quote_size, "window_end_h": window_end_h,
        "strict_through": strict, "post_at_anchor": post_at_anchor,
        "posted_markets": posted,
        "posted_tournaments": len(posted_tourns),
        "filled_markets": filled_markets,
        "fill_rate": round(filled_markets / posted, 4) if posted else None,
        "contracts_filled": round(contracts, 1),
        "net_per_contract": r4(mean),
        "ci95_lo": r4(lo), "ci95_hi": r4(hi),
        "n_fill_tournaments": len(pt),
        "e_settle_posted": r4(sum(posted_sv) / len(posted_sv)) if posted_sv else None,
        "e_settle_filled": r4(sv_fill_num / contracts) if contracts else None,
        "mean_quote_posted": r4(sum(posted_q) / len(posted_q)) if posted_q else None,
        "mean_quote_filled": r4(q_fill_num / contracts) if contracts else None,
        # What the quote WOULD have earned if every posted market filled at
        # the quote — i.e. the Phase-1 settled-average edge, restated on the
        # same population. The gap to net_per_contract IS adverse selection.
        "naive_edge_if_all_filled": r4(
            ((sum(posted_sv) / len(posted_sv)) - (sum(posted_q) / len(posted_q)))
            * (1.0 if side == "buy_yes" else -1.0)) if posted_sv else None,
        "pnl_usd": round(pnl_usd, 2),
        "collateral_usd": round(collateral, 2),
        "return_on_collateral": r4(pnl_usd / collateral) if collateral else None,
        "tournaments_positive": sum(1 for v in pt.values()
                                    if v["net_per_contract"] > 0),
        "per_tournament": pt,
        "worst_market": worst_market,
        "skipped_no_anchor": skipped_no_anchor,
        "maker_fee": 0.0,
    }


def r4(x: Optional[float]) -> Optional[float]:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(x, 4)
