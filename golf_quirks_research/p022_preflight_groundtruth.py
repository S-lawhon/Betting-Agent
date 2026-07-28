#!/usr/bin/env python3
"""Part A — resolver ground truth for the three listed tournaments.

Read-only.  Uses the pod's OWN resolver (imported, never reimplemented) and
then asks independent questions about the answer.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config
from src.round_leader_fade_maker import RoundLeaderFadeMakerPod
from src.golf_schedule import TOUR_LEAGUE, LAG_DAY_H, LAG_TEE_H, LAG_R1_ANCHOR_H, tour_of, round_of

cfg = load_config()
pod = RoundLeaderFadeMakerPod.from_config(cfg)
now = pod._now()
print(f"# now = {datetime.fromtimestamp(now, timezone.utc).isoformat()}")
print(f"# params: band={pod.mid_band} offset={pod.quote_offset} "
      f"window=[{pod.no_new_quote_h},{pod.fade_start_h}] "
      f"caps={pod.pct_per_name}/{pod.pct_per_tournament}/{pod.pct_total} "
      f"bankroll={pod.bankroll} series={len(pod.series)}")

markets = []
for s in pod.series:
    try:
        markets.extend(pod.kalshi.open_markets(s))
    except Exception as exc:
        print(f"  WARN {s}: {exc}", file=sys.stderr)

events = {}
for m in markets:
    ev = m.get("event_ticker") or ""
    if not ev:
        continue
    e = events.setdefault(ev, {"n": 0, "open_time": m.get("open_time"),
                               "kalshi_close_time": m.get("close_time"),
                               "expiration_time": m.get("expiration_time")})
    e["n"] += 1

out = []
for ev in sorted(events):
    meta = pod._product_metadata(ev)
    rc = pod.resolve_event_close(ev)
    series = ev.split("-")[0]
    row = {
        "event_ticker": ev,
        "n_markets": events[ev]["n"],
        "competition": meta.get("competition"),
        "competition_scope": meta.get("competition_scope"),
        "series": series,
        "tour": tour_of(series),
        "espn_league": TOUR_LEAGUE.get(tour_of(series) or ""),
        "round": round_of(series, meta.get("competition_scope")),
        "kalshi_open_time": events[ev]["open_time"],
        "kalshi_close_time": events[ev]["kalshi_close_time"],
        "kalshi_expiration_time": events[ev]["expiration_time"],
    }
    if rc is None:
        row["resolved"] = None
        row["unresolved_reason"] = pod.unresolved_events.get(ev)
    else:
        row.update({
            "resolved": rc.close_iso,
            "source": rc.source,
            "lag_h": rc.lag_h,
            "espn_event_id": rc.espn_event_id,
            "espn_name": rc.espn_name,
            "hours_to_close": round((rc.close_epoch - now) / 3600.0, 2),
            "window_open_utc": datetime.fromtimestamp(
                rc.close_epoch - pod.fade_start_h * 3600.0,
                timezone.utc).isoformat().replace("+00:00", "Z"),
            "window_close_utc": datetime.fromtimestamp(
                rc.close_epoch - pod.no_new_quote_h * 3600.0,
                timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        # raw ESPN facts behind the answer
        league = TOUR_LEAGUE[rc.tour]
        sb = pod.schedule._scoreboard(league, 2026)
        for e in sb:
            if str(e.get("id")) == rc.espn_event_id:
                row["espn_start_date"] = e.get("date")
                row["espn_end_date"] = e.get("endDate")
                comps = (e.get("competitions") or [{}])[0]
                venue = (comps.get("venue") or {})
                row["espn_venue"] = venue.get("fullName")
                row["espn_venue_addr"] = venue.get("address")
                break
        tees = pod.schedule._tee_times(league, rc.espn_event_id)
        row["espn_tee_times_by_round"] = tees
    out.append(row)

print(json.dumps(out, indent=1, default=str))
