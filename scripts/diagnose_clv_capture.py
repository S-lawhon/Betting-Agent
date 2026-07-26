#!/usr/bin/env python3
"""P-001 CLV capture diagnostic — why does a settled bet not produce a CLV row?

Replays the candidate-selection logic of `scripts/clv_settlement.py` over the
active trade log PLUS **all** archives (not just CLV_ARCHIVE_LOOKBACK), and
prints a failure-reason histogram by sport and by date.

Three things this answers that the production job cannot:

1. **Which of close_fair()'s five silent None paths fired.** Uses
   `src.clv_close.close_fair`, which returns (result, reason).
2. **What never reached close_fair() at all.** The daily job filters on
   `'MLB' in market_ticker`, so every tennis / NBA / NHL / NFL settled bet is
   dropped before pricing. Those are counted as EXCLUDED_NON_MLB, because they
   are the dominant term in the capture rate and are invisible in the job's own
   output.
3. **Whether the Kalshi ticker names the game that was actually priced.** Each
   trade row carries `game_time` (the Odds API commence_time of the event the
   pod priced from) while `market_ticker` encodes the Kalshi market's own ET
   start. When those disagree by more than a few hours the bet was placed on a
   *different game between the same teams*, and its CLV row compares the
   entry price of one market against the closing line of another. Reported as
   TICKER_GAME_MISMATCH — an audit flag, orthogonal to capture.

Odds API historical calls are metered. Every response is cached to
`--cache-dir` keyed by (sport, snapshot timestamp) and re-read from there, and
the run prints the x-requests-remaining header before and after so spend is
auditable. `--max-api-calls` hard-caps live calls; 0 means cache-only.

Read-only: it never writes to data/trade_logs/.
"""
from __future__ import annotations

import argparse
import collections
import glob
import gzip
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

ROOT = os.environ.get("CLV_ROOT", "/opt/betting-pod-shop")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.clv_close import close_fair, historical_url, norm  # noqa: E402
from src.et_time import parse_mlb_ticker_start  # noqa: E402
from src.mlb_teams import teams_from_mlb_ticker  # noqa: E402

MISMATCH_TOLERANCE_S = 3 * 3600


# ── inputs ────────────────────────────────────────────────────────────────────

def _read_key(root: str) -> str:
    m = re.search(r'ODDS_API_KEY=([^\n\r"\']+)', open(f"{root}/.env").read())
    if not m:
        raise SystemExit("no ODDS_API_KEY in .env")
    return m.group(1).strip()


def iter_trade_rows(root: str):
    """Every line of every archive plus the active log. Archives first so the
    active log's newer duplicates win."""
    for path in sorted(glob.glob(f"{root}/data/trade_logs/trade_log.archive_*.jsonl.gz")):
        with gzip.open(path, "rt", errors="replace") as fh:
            for ln in fh:
                yield ln
    with open(f"{root}/data/trade_logs/trade_log.jsonl", errors="replace") as fh:
        for ln in fh:
            yield ln


def load_candidates(args):
    """Settled P-001 bets, deduped by fingerprint.

    `--rows` accepts a pre-extracted compact file (fields fp/act/tk/ev/gt/...),
    which is how this is run off-box against a mirrored corpus without copying
    130MB of gzipped logs.
    """
    out = {}
    if args.rows:
        for ln in open(args.rows):
            r = json.loads(ln)
            if r.get("act") not in ("WIN", "LOSS"):
                continue
            out[r["fp"]] = dict(fingerprint=r["fp"], market_ticker=r.get("tk"),
                                event=r.get("ev"), side=r.get("side"),
                                yes_side=r.get("ys"), fill_price=r.get("fill"),
                                kalshi_prob=r.get("kp"), outcome=r.get("out"),
                                settled_at_utc=r.get("sa"), game_time=r.get("gt"))
        return out
    for ln in iter_trade_rows(args.root):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("pod_id") != "P-001" or r.get("action") not in ("WIN", "LOSS"):
            continue
        ex = r.get("extra") or {}
        fp = r.get("fingerprint")
        if not fp:
            continue
        out[fp] = dict(fingerprint=fp,
                       market_ticker=r.get("market_ticker") or r.get("market_id"),
                       event=r.get("event"), side=r.get("side"),
                       yes_side=r.get("yes_side") or ex.get("yes_side"),
                       fill_price=r.get("fill_price"),
                       kalshi_prob=r.get("kalshi_prob"),
                       outcome=r.get("outcome"),
                       settled_at_utc=r.get("settled_at_utc"),
                       game_time=r.get("game_time") or ex.get("game_time"))
    return out


# ── metered fetch with a disk cache ───────────────────────────────────────────

class BudgetExhausted(BaseException):
    """Raised on a cache miss once --max-api-calls is spent.

    Subclasses BaseException on purpose: `close_fair` catches `Exception` to
    turn transport failures into API_ERROR, and a budget stop must NOT be
    laundered into that bucket — it would look like a real capture failure.
    """


class CachedFetcher:
    def __init__(self, cache_dir, max_calls):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.max_calls = max_calls
        self.live_calls = 0
        self.cache_hits = 0
        self.remaining_before = None
        self.remaining_after = None

    def _path(self, url):
        # the api key is in the URL; key the cache on the rest only
        scrubbed = re.sub(r"apiKey=[^&]+", "apiKey=X", url)
        return os.path.join(self.dir, hashlib.sha1(scrubbed.encode()).hexdigest() + ".json")

    def __call__(self, url):
        p = self._path(url)
        if os.path.exists(p):
            self.cache_hits += 1
            with open(p) as fh:
                return json.load(fh)
        if self.live_calls >= self.max_calls:
            raise BudgetExhausted("api call budget exhausted (cache miss)")
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.load(r)
            rem = r.headers.get("x-requests-remaining")
        self.live_calls += 1
        if self.remaining_before is None:
            self.remaining_before = rem
        self.remaining_after = rem
        with open(p, "w") as fh:
            json.dump(body, fh)
        return body


# ── classification ────────────────────────────────────────────────────────────

def sport_of(ticker: str) -> str:
    return (ticker or "").split("-")[0] or "UNKNOWN"


def ticker_game_mismatch_hours(row):
    """|ticker-encoded start − priced game_time| in hours, or None if either
    side is unavailable. Only meaningful for tickers that encode a time
    (KXMLBGAME); date-only tickers return None."""
    st = parse_mlb_ticker_start(row.get("market_ticker") or "")
    gt = row.get("game_time")
    if st is None or not gt:
        return None
    try:
        g = datetime.fromisoformat(str(gt).replace("Z", "+00:00"))
    except ValueError:
        return None
    if g.tzinfo is None:
        return None
    return (g - st).total_seconds() / 3600.0


def _names_for(row, mode):
    """Normalised team names for the closing-line lookup.

    `event` is the legacy behaviour (the Odds API game the pod priced);
    `ticker` is the fix (the market actually traded). Run both to measure what
    the fix recovers — the replay is cache-backed, so the comparison is free.
    """
    if mode in ("ticker", "ticker_then_event"):
        t = teams_from_mlb_ticker(row.get("market_ticker") or "")
        if t:
            return [norm(t[0]), norm(t[1])]
        if mode == "ticker":
            return None
    parts = (row.get("event") or "").split(" vs ")
    if len(parts) != 2:
        return None
    return [norm(x) for x in parts]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", default="event",
                    choices=("event", "ticker", "ticker_then_event"),
                    help="where team names come from (default: event = legacy behaviour)")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--rows", help="pre-extracted compact candidate file")
    ap.add_argument("--clv-log", default=None,
                    help="clv_log.jsonl to treat as already-captured")
    ap.add_argument("--cache-dir", default="/tmp/clv_diag_cache")
    ap.add_argument("--max-api-calls", type=int, default=0,
                    help="hard cap on live Odds API historical calls (0 = cache only)")
    ap.add_argument("--price-captured", action="store_true",
                    help="also re-price bets already in clv_log (costs credits)")
    ap.add_argument("--out", default=None, help="write per-bet classification JSONL here")
    args = ap.parse_args()

    key = _read_key(args.root)
    clv_path = args.clv_log or f"{args.root}/data/trade_logs/clv_log.jsonl"
    captured = set()
    if os.path.exists(clv_path):
        for ln in open(clv_path):
            try:
                captured.add(json.loads(ln)["fingerprint"])
            except Exception:
                pass

    rows = load_candidates(args)
    fetch = CachedFetcher(args.cache_dir, args.max_api_calls)

    by_reason = collections.Counter()
    by_sport = collections.defaultdict(collections.Counter)
    by_month = collections.defaultdict(collections.Counter)
    mismatch = collections.Counter()
    detail = []

    # group MLB candidates by game, exactly as the production job does
    games = collections.defaultdict(list)
    for r in rows.values():
        tk = r.get("market_ticker") or ""
        if "MLB" not in tk.upper():
            reason = "EXCLUDED_NON_MLB"
        elif r["fingerprint"] in captured and not args.price_captured:
            reason = "ALREADY_CAPTURED"
        else:
            games["-".join(tk.split("-")[:2])].append(r)
            continue
        _tally(r, reason, by_reason, by_sport, by_month, mismatch, detail)

    for gid, rs in sorted(games.items()):
        r0 = rs[0]
        expected = parse_mlb_ticker_start(r0["market_ticker"])
        if expected is None:
            reason = "UNPARSEABLE_TICKER"
        else:
            names = _names_for(r0, args.names)
            if names is None:
                reason = "EVENT_SPLIT"
            else:
                try:
                    _, reason = close_fair(expected, names, key, fetch=fetch)
                except BudgetExhausted:
                    reason = "NOT_PRICED_BUDGET"
        for r in rs:
            _tally(r, reason, by_reason, by_sport, by_month, mismatch, detail)

    # ── report ────────────────────────────────────────────────────────────────
    n = sum(by_reason.values())
    print(f"P-001 settled bets examined: {n}\n")
    print("failure-reason histogram (per settled bet)")
    for k, v in by_reason.most_common():
        print(f"  {v:6d}  {v/n:6.1%}  {k}")

    print("\nby sport (series prefix)")
    order = [k for k, _ in by_reason.most_common()]
    for s in sorted(by_sport, key=lambda s: -sum(by_sport[s].values())):
        tot = sum(by_sport[s].values())
        bits = " ".join(f"{k}={by_sport[s][k]}" for k in order if by_sport[s][k])
        print(f"  {s:24s} n={tot:5d}  {bits}")

    print("\nby settlement month")
    for m in sorted(by_month):
        tot = sum(by_month[m].values())
        bits = " ".join(f"{k}={by_month[m][k]}" for k in order if by_month[m][k])
        print(f"  {m}  n={tot:5d}  {bits}")

    print("\nticker-vs-priced-game audit (KXMLBGAME only; |delta| > 3h = the pod "
          "priced a different game than the ticker it traded)")
    for k, v in mismatch.most_common():
        print(f"  {v:6d}  {k}")

    print(f"\nodds api: live calls={fetch.live_calls} cache hits={fetch.cache_hits} "
          f"x-requests-remaining before={fetch.remaining_before} after={fetch.remaining_after}")

    if args.out:
        with open(args.out, "w") as fh:
            for d in detail:
                fh.write(json.dumps(d) + "\n")
        print(f"per-bet detail -> {args.out}")


def _tally(r, reason, by_reason, by_sport, by_month, mismatch, detail):
    by_reason[reason] += 1
    by_sport[sport_of(r.get("market_ticker"))][reason] += 1
    by_month[(r.get("settled_at_utc") or "????-??")[:7]][reason] += 1
    dh = ticker_game_mismatch_hours(r)
    if dh is not None:
        mismatch["ticker_matches_priced_game" if abs(dh) <= 3
                 else "TICKER_GAME_MISMATCH"] += 1
    detail.append(dict(fingerprint=r["fingerprint"], reason=reason,
                       market_ticker=r.get("market_ticker"), event=r.get("event"),
                       settled_at=r.get("settled_at_utc"), game_time=r.get("game_time"),
                       ticker_game_delta_h=None if dh is None else round(dh, 2)))


if __name__ == "__main__":
    main()
