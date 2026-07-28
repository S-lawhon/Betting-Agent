#!/usr/bin/env python3
"""Part B — drive P-022's REAL decision path at an injected wall-clock.

No order submission of any kind (this client cannot place orders at all).
Logs are redirected to the scratchpad so the live quote/fill logs are
untouched.

The funnel is observed from the pod's own execution:
  * discovered      -> kalshi.open_markets (wrapped counter)
  * resolved close  -> len(engine.books) after engine.discover()
  * in window       -> engine.no_new_quote_h <= (b.close_epoch - now)/3600
                       <= engine.fade_start_h, read off engine attributes and
                       engine book state (the same expression scripts/
                       p022_window_check.assess uses)
  * priced / in band-> every engine._mid() call the pod actually made
  * would place     -> QUOTE rows the pod actually wrote
  * refusals        -> PULL rows with the pod's own reason string
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config
from src.round_leader_fade_maker import RoundLeaderFadeMakerPod

SCRATCH = Path(__file__).resolve().parent / "preflight_dryrun"


def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def run(label: str, t_iso: str, only_events: set) -> dict:
    T = epoch(t_iso)
    cfg = load_config()
    logdir = SCRATCH / label
    logdir.mkdir(parents=True, exist_ok=True)
    eng = RoundLeaderFadeMakerPod.from_config(
        cfg, _now_fn=lambda: T, log_dir=logdir,
        kill_file=Path(logdir / "NEVER_EXISTS"))

    # verify the locked parameters are untouched
    params = {
        "mid_band": list(eng.mid_band), "quote_offset": eng.quote_offset,
        "fade_start_h": eng.fade_start_h, "no_new_quote_h": eng.no_new_quote_h,
        "pct_per_name": eng.pct_per_name,
        "pct_per_tournament": eng.pct_per_tournament,
        "pct_total": eng.pct_total, "bankroll": eng.bankroll,
        "max_contracts_per_name": eng.max_contracts_per_name,
        "n_series": len(eng.series),
        "risk_guard": type(eng.risk_guard).__name__ if eng.risk_guard else None,
    }

    n_listed = {"n": 0}
    real_open = eng.kalshi.open_markets

    def counting_open(series):
        ms = real_open(series)
        n_listed["n"] += len(ms)
        return ms
    eng.kalshi.open_markets = counting_open

    n_new = eng.discover()
    n_resolved_books = len(eng.books)

    hrs = {tk: (b.close_epoch - T) / 3600.0 for tk, b in eng.books.items()}
    in_window = {tk for tk, h in hrs.items()
                 if eng.no_new_quote_h <= h <= eng.fade_start_h}
    # Prune to the in-window books ONLY to bound orderbook calls; books are
    # processed independently and the pruned ones hold zero collateral, so no
    # cap or decision for a surviving book changes.
    for tk in list(eng.books):
        if tk not in in_window:
            del eng.books[tk]

    mids = {}
    real_mid = eng._mid

    def recording_mid(ticker):
        m = real_mid(ticker)
        mids[ticker] = m
        return m
    eng._mid = recording_mid

    eng.cycle()

    lo, hi = eng.mid_band
    priced = {t: m for t, m in mids.items() if m is not None}
    in_band = {t: m for t, m in priced.items() if lo <= m <= hi}

    quotes, pulls = [], []
    qlog = logdir / "round_leader_fade_quotes.jsonl"
    if qlog.exists():
        for raw in qlog.read_text().splitlines():
            if not raw.strip():
                continue
            r = json.loads(raw)
            (quotes if r.get("type") == "QUOTE" else pulls).append(r)

    ev_of = {}
    for tk in in_window:
        ev_of[tk] = tk.rsplit("-", 1)[0]

    return {
        "label": label, "clock_utc": t_iso, "params": params,
        "funnel": {
            "1_markets_discovered": n_listed["n"],
            "2_resolved_close": n_resolved_books,
            "3_inside_window_12_24h": len(in_window),
            "4_priced_two_sided_or_ask": len(priced),
            "5_inside_band": len(in_band),
            "6_would_place": len(quotes),
        },
        "in_window_events": sorted({ev_of[t] for t in in_window}),
        "hours_to_close_by_event": {
            ev: round(min(h for tk, h in hrs.items()
                          if tk.rsplit("-", 1)[0] == ev), 3)
            for ev in sorted({tk.rsplit("-", 1)[0] for tk in in_window})
        },
        "mid_distribution": {
            "n_none": len(mids) - len(priced),
            "n_below_band": sum(1 for m in priced.values() if m < lo),
            "n_in_band": len(in_band),
            "n_above_band": sum(1 for m in priced.values() if m > hi),
        },
        "in_band_sample": sorted(in_band.items(), key=lambda kv: -kv[1])[:15],
        "quotes": [{k: q.get(k) for k in
                    ("ticker", "event", "ask", "mid", "size",
                     "hours_to_close", "close_source")} for q in quotes],
        "quote_totals": {
            "n": len(quotes),
            "contracts": sum(q.get("size") or 0 for q in quotes),
            "collateral_usd": round(sum((1 - (q.get("ask") or 0)) * (q.get("size") or 0)
                                        for q in quotes), 2),
        },
        "pull_reasons": {r: sum(1 for p in pulls if p.get("reason") == r)
                         for r in sorted({p.get("reason") for p in pulls})},
        "breaches": sorted(eng.breached_events),
        "total_collateral_after": round(eng.total_collateral(), 2),
    }


if __name__ == "__main__":
    out = []
    for label, t_iso in (
        ("A_aigwo_r1_window_open", "2026-07-29T15:31:00Z"),
        ("B_roc_r1_window_open", "2026-07-29T18:31:00Z"),
    ):
        try:
            out.append(run(label, t_iso, set()))
        except Exception as exc:            # noqa: BLE001
            import traceback
            traceback.print_exc()
            out.append({"label": label, "error": str(exc)})
        print(json.dumps(out[-1], indent=1, default=str))
    (SCRATCH / "funnel.json").write_text(json.dumps(out, indent=1, default=str))
