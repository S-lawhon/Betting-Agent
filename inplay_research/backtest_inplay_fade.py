#!/usr/bin/env python3
"""
inplay_research/backtest_inplay_fade.py
───────────────────────────────────────
P-018 replay backtest — the KILL GATE (spec §5, §8 gate #1) that decides
whether the surprise gate is real BEFORE any pod is built.

What it does (when data exists):
  1. Load captured Kalshi in-play prints (``data/book_capture/*.jsonl``,
     BOOK records: mid, spread, trades[]).
  2. For each game market, reconstruct the model win-prob PATH by joining
     MLB StatsAPI historical play-by-play (state at each tick timestamp)
     and running ``src.mlb_win_prob`` — the SAME model the live pod uses.
  3. Detect events (state_key changes), compute surprise / underreaction
     via ``src.inplay_surprise``, route to a regime, and rest fade quotes.
  4. Simulate PESSIMISTIC through-fills against the real prints in the
     fade window; apply the series-aware maker fee; mark to settlement.
  5. Emit REPORT tables: a per-fill net ¢/contract with a GAME-CLUSTERED
     bootstrap CI (a game is one observation — within-game outcomes
     correlate), a SURPRISE-BUCKET table (does edge rise with surprise?),
     and a WINDOW-DECAY table (does edge die by ~6 min?).  Persist tuned
     params to ``p018_params.json``.

THE CHEAPEST KILL (spec §5): if the surprise-bucket table is flat
(high-surprise ≈ low-surprise), the gate is noise — stop, do not ship the
pod.  This harness prints that verdict; it does not decide it for you.

HONEST-DATA DISCIPLINE: this project's whole method is paper-first and
un-faked.  If there aren't enough captured game-days to run a meaningful
backtest, this exits with a PREREQUISITE-NOT-MET summary and writes the
REPORT's status as BLOCKED rather than fabricating a sample.  As of the
last run, book_capture had ~1 day of coverage (commit 71cca0f shipped it
"not yet deployed") — the daemon must accrue ~2–3 weeks first.

Usage:
    python3 inplay_research/backtest_inplay_fade.py \
        [--data-dir data/book_capture] [--series KXMLBGAME] \
        [--min-game-days 10] [--no-network]
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.inplay_surprise import (  # noqa: E402
    Regime, SurpriseParams, SurpriseState, build_quote, fills_through,
    select_regime, settle_pnl, update_on_event,
)
from src.mlb_win_prob import MlbGameSnapshot, home_win_probability  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(HERE, "REPORT_InPlay_Fade_2026-07.md")
PARAMS_PATH = os.path.join(HERE, "p018_params.json")
SURPRISE_BUCKETS = [(0.0, 0.02), (0.02, 0.06), (0.06, 0.12), (0.12, 1.01)]
DECAY_BUCKETS_S = [(0, 60), (60, 120), (120, 240), (240, 360), (360, 1e9)]


# ══════════════════════════════════════════════════════════════════════
# Load captured ticks
# ══════════════════════════════════════════════════════════════════════

def _open(path: str):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def load_book_ticks(data_dir: str) -> List[Dict[str, Any]]:
    """Read every book_capture BOOK record (skip DISCOVERY), oldest-first."""
    files = sorted(glob.glob(os.path.join(data_dir, "book_capture_*.jsonl"))
                   + glob.glob(os.path.join(data_dir, "book_capture_*.jsonl.gz")))
    ticks: List[Dict[str, Any]] = []
    for fp in files:
        try:
            with _open(fp) as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "BOOK":
                        ticks.append(rec)
        except OSError:
            continue
    ticks.sort(key=lambda r: r.get("epoch") or 0.0)
    return ticks


def assess_data(ticks: List[Dict[str, Any]], series: str) -> Dict[str, Any]:
    """Coverage summary + a sufficiency verdict.  A 'game-day' is a distinct
    (UTC date, market ticker) — the cluster unit the CI will use."""
    game_days = set()
    tickers = set()
    with_trades = 0
    for t in ticks:
        if series and not (t.get("series") == series
                           or str(t.get("ticker", "")).startswith(series)):
            continue
        day = str(t.get("ts_utc", ""))[:10]
        tk = t.get("ticker")
        if day and tk:
            game_days.add((day, tk))
            tickers.add(tk)
        if t.get("n_trades"):
            with_trades += 1
    return {
        "n_ticks": len(ticks),
        "n_series_ticks": sum(
            1 for t in ticks
            if not series or t.get("series") == series
            or str(t.get("ticker", "")).startswith(series)),
        "n_game_days": len(game_days),
        "n_tickers": len(tickers),
        "n_ticks_with_trades": with_trades,
        "game_days": sorted(game_days),
    }


# ══════════════════════════════════════════════════════════════════════
# Game-state reconstruction (StatsAPI historical play-by-play)
# ══════════════════════════════════════════════════════════════════════

def _iso_epoch(s: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError, TypeError):
        return None


class MlbStateTimeline:
    """State-at-timestamp reconstructed from a game's historical PBP.

    Each MLB play carries an ISO end time and the resulting base/out/score
    state; we build an ascending (epoch, snapshot) timeline and answer
    ``state_at(epoch)`` by taking the last play that had completed.

    NOTE (needs live validation once ticks exist): StatsAPI play timestamps
    and Kalshi print timestamps are on independent clocks with feed lag on
    both sides.  The join here is best-effort as-of; the backtest REPORT
    must sanity-check that reconstructed WP paths track the mid before any
    verdict is trusted.  This is why P-018 is gated behind real data, not
    shipped on this reconstruction alone.
    """

    def __init__(self, plays: List[Tuple[float, MlbGameSnapshot]],
                 final_home_won: Optional[bool]):
        self.plays = sorted(plays, key=lambda p: p[0])
        self.final_home_won = final_home_won

    @classmethod
    def from_statsapi(cls, game_pk: int, session=None) -> Optional["MlbStateTimeline"]:
        import requests
        sess = session or requests.Session()
        try:
            r = sess.get(
                f"https://statsapi.mlb.com/api/v1/game/{game_pk}/playByPlay",
                timeout=15)
            if r.status_code != 200:
                return None
            data = r.json()
        except Exception:
            return None
        plays: List[Tuple[float, MlbGameSnapshot]] = []
        all_plays = (data.get("allPlays") or [])
        final_home = None
        for pl in all_plays:
            about = pl.get("about") or {}
            result = pl.get("result") or {}
            count = pl.get("count") or {}
            matchup = pl.get("matchup") or {}
            inning = int(about.get("inning") or 1)
            is_top = (about.get("halfInning") == "top")
            outs = int(count.get("outs") or 0)
            home_score = int(result.get("homeScore")
                             or about.get("homeScore") or 0)
            away_score = int(result.get("awayScore")
                             or about.get("awayScore") or 0)
            # Base state after the play, from post-play runner positions.
            on1 = on2 = on3 = False
            for runner in (pl.get("runners") or []):
                mv = (runner.get("movement") or {})
                end = mv.get("end")
                if end == "1B":
                    on1 = True
                elif end == "2B":
                    on2 = True
                elif end == "3B":
                    on3 = True
            end_ts = _iso_epoch(about.get("endTime") or about.get("startTime"))
            if end_ts is None:
                continue
            snap = MlbGameSnapshot(
                home_score=home_score, away_score=away_score, inning=inning,
                is_top=is_top, outs=outs % 3, on_first=on1, on_second=on2,
                on_third=on3, is_final=False)
            plays.append((end_ts, snap))
            final_home = home_score > away_score
        if not plays:
            return None
        return cls(plays, final_home)

    def state_at(self, epoch: float) -> Optional[MlbGameSnapshot]:
        prev = None
        for ts, snap in self.plays:
            if ts <= epoch:
                prev = snap
            else:
                break
        return prev


def resolve_game_pk(ticker: str, session=None) -> Optional[int]:
    """Parse the ET date out of a KXMLBGAME ticker and match the schedule.

    Ticker: ``KXMLBGAME-<YY><MON><DD><HHMM><AWAY><HOME>-<HOME>``.
    Returns the StatsAPI gamePk whose away+home codes both appear, or None.
    """
    import requests
    sess = session or requests.Session()
    try:
        frag = ticker.split("-")[1]
    except IndexError:
        return None
    datecode = frag[:7]                                   # e.g. 26JUL20
    try:
        d = datetime.strptime(datecode, "%y%b%d")
    except ValueError:
        return None
    date_str = d.strftime("%Y-%m-%d")
    try:
        r = sess.get("https://statsapi.mlb.com/api/v1/schedule",
                     params={"sportId": 1, "date": date_str, "hydrate": "team"},
                     timeout=15)
        data = r.json()
    except Exception:
        return None
    up = ticker.upper()
    for day in data.get("dates", []):
        for g in day.get("games", []):
            try:
                away = g["teams"]["away"]["team"]["abbreviation"].upper()
                home = g["teams"]["home"]["team"]["abbreviation"].upper()
            except (KeyError, TypeError):
                continue
            if away in up and home in up:
                return g["gamePk"]
    return None


# ══════════════════════════════════════════════════════════════════════
# Replay one market
# ══════════════════════════════════════════════════════════════════════

def replay_market(ticker: str, ticks: List[Dict[str, Any]],
                  timeline: MlbStateTimeline, params: SurpriseParams,
                  series: str) -> List[Dict[str, Any]]:
    """Walk one market's ticks; return per-fill records (net of fee)."""
    st = SurpriseState()
    pregame = None
    fills: List[Dict[str, Any]] = []
    open_quote = None          # (side, price, size, event_ts, surprise, underr)
    result = (1.0 if timeline.final_home_won else 0.0)

    for t in ticks:
        epoch = t.get("epoch")
        mid = t.get("mid")
        if epoch is None:
            continue
        snap = timeline.state_at(epoch)
        if snap is None:
            continue
        if pregame is None:
            pregame = mid if mid is not None else 0.54
        fv = home_win_probability(snap, pregame)
        state_key = (f"{snap.away_score}-{snap.home_score}|i{snap.inning}"
                     f"{'T' if snap.is_top else 'B'}|o{snap.outs}"
                     f"|b{int(snap.on_first)}{int(snap.on_second)}{int(snap.on_third)}")

        # 1) fills against this tick's prints, for any resting fade quote
        if open_quote is not None:
            side, px, size, ev_ts, surp, underr = open_quote
            for pr in (t.get("trades") or []):
                filled = fills_through(side, px, size,
                                       pr.get("yes_price"), pr.get("count") or 0)
                if filled > 0:
                    fills.append({
                        "ticker": ticker, "side": "buy" if side == "bid" else "sell",
                        "price": px, "qty": filled,
                        "surprise": surp, "underreaction": underr,
                        "secs_since_event": (pr.get("epoch") or epoch) - ev_ts,
                        "pnl": settle_pnl("buy" if side == "bid" else "sell",
                                          px, result, filled, series),
                    })
                    size -= filled
            open_quote = (side, px, size, ev_ts, surp, underr) if size > 0 else None

        # 2) event detection + regime + (re)quote
        ev = update_on_event(st, state_key, fv.home_wp, mid, now=epoch)
        killed = False
        regime = select_regime(st, now=epoch, params=params, killed=killed,
                               event_risk=False)
        if regime is Regime.FADE and st.last_event is not None:
            secs = epoch - (st.event_ts or epoch)
            spec = build_quote(Regime.FADE, fv.home_wp, mid, fv.sensitivity,
                               params, secs_since_event=secs,
                               underreaction=st.last_event.underreaction)
            if spec.bid is not None:
                open_quote = ("bid", spec.bid, spec.bid_size, st.event_ts,
                              st.last_event.surprise, st.last_event.underreaction)
            elif spec.ask is not None:
                open_quote = ("ask", spec.ask, spec.ask_size, st.event_ts,
                              st.last_event.surprise, st.last_event.underreaction)
        elif regime is not Regime.FADE:
            open_quote = None   # only the fade leg is evaluated in the backtest
    return fills


# ══════════════════════════════════════════════════════════════════════
# Stats
# ══════════════════════════════════════════════════════════════════════

def bootstrap_ci(per: List[Tuple[str, float]], n_boot: int = 5000
                 ) -> Tuple[float, float, float]:
    """Mean + 95% CI, resampling GAMES with replacement (within-game
    outcomes correlate → cluster bootstrap, per spec §8 gate #4)."""
    if not per:
        return 0.0, float("nan"), float("nan")
    by_game: Dict[str, List[float]] = defaultdict(list)
    for g, v in per:
        by_game[g].append(v)
    games = list(by_game.keys())
    mean = sum(v for _, v in per) / len(per)
    if len(games) < 2:
        return mean, float("nan"), float("nan")
    rng = random.Random(12345)
    means = []
    for _ in range(n_boot):
        pool: List[float] = []
        for _ in range(len(games)):
            pool.extend(by_game[games[rng.randrange(len(games))]])
        if pool:
            means.append(sum(pool) / len(pool))
    means.sort()
    return mean, means[int(0.025 * len(means))], means[int(0.975 * len(means))]


def bucketize(fills: List[Dict[str, Any]], key: str,
              buckets) -> Dict[Tuple[float, float], List[Dict[str, Any]]]:
    out: Dict[Tuple[float, float], List[Dict[str, Any]]] = {b: [] for b in buckets}
    for f in fills:
        v = abs(f.get(key, 0.0)) if key == "underreaction" else f.get(key, 0.0)
        for lo, hi in buckets:
            if lo <= v < hi:
                out[(lo, hi)].append(f)
                break
    return out


# ══════════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════════

def _cents(x: float) -> str:
    return f"{100.0 * x:+.2f}¢"


def write_blocked_report(cov: Dict[str, Any], min_game_days: int) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# P-018 In-Play Fade Maker — Backtest REPORT",
        "",
        f"*Status as of {ts}: **BLOCKED — PREREQUISITE NOT MET.***",
        "",
        "## The kill gate cannot run yet — there is not enough captured "
        "in-play data",
        "",
        "P-018's whole reason to exist over P-016 is the surprise gate, and "
        "the pre-registered cheapest kill (spec §5, §8 gate #1) is: replay "
        "captured in-play prints, bucket faded fills by surprise, and check "
        "whether edge actually rises with surprise. That replay needs weeks "
        "of Kalshi in-play tick history. It does not exist yet.",
        "",
        "### Coverage found",
        "",
        f"- BOOK ticks on disk: **{cov['n_ticks']}** "
        f"({cov['n_series_ticks']} for the target series)",
        f"- Distinct game-days (date × market): **{cov['n_game_days']}** "
        f"(need ≥ {min_game_days} to run; spec asks ~2–3 weeks)",
        f"- Distinct markets: **{cov['n_tickers']}**",
        f"- Ticks carrying trade prints: **{cov['n_ticks_with_trades']}**",
        "",
        "### Why (root cause, not a bug)",
        "",
        "`src/book_capture.py` shipped 'not yet deployed' (commit 71cca0f); "
        "the local capture file is a single DISCOVERY record with zero BOOK "
        "ticks. The daemon must run on the VPS and accrue history first — "
        "every night it does not run is calibration data lost forever (its "
        "own docstring). The signal half (`src/mlb_win_prob`) and the whole "
        "gate (`src/inplay_surprise`, unit-tested) are ready and waiting on "
        "this data.",
        "",
        "### To unblock",
        "",
        "1. Deploy + verify `betting-book-capture.service` on the VPS "
        "(`KXMLBGAME`, 60s cadence).",
        "2. Let it collect ~2–3 weeks of live MLB in-play ticks.",
        "3. Re-run this harness; it will reconstruct WP paths from StatsAPI "
        "PBP, replay fades, and emit the surprise-bucket + window-decay "
        "tables and the game-clustered CI.",
        "",
        "**No pod ships before this REPORT shows the surprise-bucket table is "
        "NOT flat.** (spec §8 gate #1)",
        "",
        "---",
        "*Generated by `inplay_research/backtest_inplay_fade.py`. The gate "
        "logic it exercises lives in `src/inplay_surprise.py` and is covered "
        "by `tests/test_inplay_fade_maker.py`.*",
    ]
    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def write_results_report(fills: List[Dict[str, Any]], cov: Dict[str, Any],
                         params: SurpriseParams) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    per_game = [(f["ticker"], f["pnl"] / max(f["qty"], 1e-9)) for f in fills]
    mean, lo, hi = bootstrap_ci(per_game)

    lines = [
        "# P-018 In-Play Fade Maker — Backtest REPORT",
        "",
        f"*Status as of {ts}: results below. Read gate #1 before shipping.*",
        "",
        f"- Faded fills: **{len(fills)}** across **{cov['n_tickers']}** markets",
        f"- Headline net edge: **{_cents(mean)}/contract** "
        f"(95% CI game-clustered [{_cents(lo)}, {_cents(hi)}])",
        "",
        "## Gate #1 — surprise-bucket table (does edge rise with surprise?)",
        "",
        "| abs(wp_jump) bucket | fills | net ¢/ct | 95% CI |",
        "|---|---|---|---|",
    ]
    for (blo, bhi), fs in bucketize(fills, "surprise", SURPRISE_BUCKETS).items():
        per = [(f["ticker"], f["pnl"] / max(f["qty"], 1e-9)) for f in fs]
        m, l, h = bootstrap_ci(per)
        lines.append(f"| [{blo:.2f}, {bhi:.2f}) | {len(fs)} | "
                     f"{_cents(m) if fs else '—'} | "
                     f"[{_cents(l)}, {_cents(h)}] |" if fs
                     else f"| [{blo:.2f}, {bhi:.2f}) | 0 | — | — |")
    lines += [
        "",
        "**Verdict rule:** if the high-surprise buckets are not clearly above "
        "the low-surprise buckets, the gate is noise — KILL P-018 (spec §5).",
        "",
        "## Window-decay table (does the edge die by ~6 min?)",
        "",
        "| secs since event | fills | net ¢/ct |",
        "|---|---|---|",
    ]
    for (dlo, dhi), fs in bucketize(fills, "secs_since_event", DECAY_BUCKETS_S).items():
        per = [(f["ticker"], f["pnl"] / max(f["qty"], 1e-9)) for f in fs]
        m, _, _ = bootstrap_ci(per)
        hi_lbl = "∞" if dhi > 1e8 else str(int(dhi))
        lines.append(f"| {int(dlo)}–{hi_lbl}s | {len(fs)} | "
                     f"{_cents(m) if fs else '—'} |")
    lines += [
        "",
        "---",
        "*Generated by `inplay_research/backtest_inplay_fade.py`; gate logic "
        "in `src/inplay_surprise.py`.*",
    ]
    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def write_params(params: SurpriseParams, note: str) -> None:
    payload = {
        "_note": note,
        "_status": "priors — NOT backtest-validated until the REPORT shows a "
                   "non-flat surprise-bucket table",
        "quoting": {
            "surprise_hi": params.surprise_hi,
            "surprise_lo": params.surprise_lo,
            "min_underreaction": params.min_underreaction,
            "fade_window_s": params.fade_window_s,
            "fade_size": params.fade_size,
            "expected_widen": params.expected_widen,
            "base_half_width": params.base_half_width,
            "max_half_width": params.max_half_width,
            "flb_skew_coef": params.flb_skew_coef,
            "inv_skew_coef": params.inv_skew_coef,
            "max_quote_px": params.max_quote_px,
            "min_quote_px": params.min_quote_px,
            "fade_edge_frac": params.fade_edge_frac,
        },
    }
    with open(PARAMS_PATH, "w") as fh:
        json.dump(payload, fh, indent=2)


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/book_capture")
    ap.add_argument("--series", default="KXMLBGAME")
    ap.add_argument("--min-game-days", type=int, default=10)
    ap.add_argument("--no-network", action="store_true",
                    help="skip StatsAPI reconstruction (coverage report only)")
    args = ap.parse_args()

    params = SurpriseParams()
    ticks = load_book_ticks(args.data_dir)
    cov = assess_data(ticks, args.series)

    print(f"P-018 backtest: {cov['n_ticks']} BOOK ticks, "
          f"{cov['n_game_days']} game-days, {cov['n_tickers']} markets, "
          f"{cov['n_ticks_with_trades']} tick(s) with prints.")

    if cov["n_game_days"] < args.min_game_days or cov["n_ticks_with_trades"] == 0:
        print(f"PREREQUISITE NOT MET: need >= {args.min_game_days} game-days "
              f"with prints; have {cov['n_game_days']}. Writing BLOCKED report.")
        write_blocked_report(cov, args.min_game_days)
        write_params(params, "priors; backtest blocked on data (see REPORT)")
        print(f"Wrote {REPORT_PATH}\nWrote {PARAMS_PATH}")
        return 0

    if args.no_network:
        print("--no-network set but data is sufficient; StatsAPI needed to "
              "reconstruct state. Re-run without --no-network.")
        return 0

    # Group ticks by market and replay each.
    by_ticker: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in ticks:
        if t.get("series") == args.series or str(
                t.get("ticker", "")).startswith(args.series):
            by_ticker[t["ticker"]].append(t)

    all_fills: List[Dict[str, Any]] = []
    for ticker, tk_ticks in by_ticker.items():
        game_pk = resolve_game_pk(ticker)
        if game_pk is None:
            print(f"  skip {ticker}: could not resolve gamePk")
            continue
        timeline = MlbStateTimeline.from_statsapi(game_pk)
        if timeline is None or timeline.final_home_won is None:
            print(f"  skip {ticker}: no settled PBP timeline")
            continue
        fills = replay_market(ticker, tk_ticks, timeline, params, args.series)
        all_fills.extend(fills)
        print(f"  {ticker}: {len(fills)} faded fills")

    write_results_report(all_fills, cov, params)
    write_params(params, f"backtest over {cov['n_game_days']} game-days")
    print(f"Wrote {REPORT_PATH}\nWrote {PARAMS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
