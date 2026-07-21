"""Fit per-station bias/sigma: ensemble daily max vs IEM CLI settlement truth.

Uses Open-Meteo ensemble `past_days` (stitched short-lead archive) as the
forecast proxy and IEM CLI reports as truth. Writes
data/weather_station_params.json consumed by weather_pipeline.

Caveat: past_days series ~= 0-1 day lead forecasts; bias at longer leads may
differ. Good enough to correct the gross station offsets (marine layer, urban
heat); refit from the live archive as it accumulates.
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from weather_pipeline import STATIONS, member_daily_max, _session

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PAST_DAYS = 60


def fetch_past(lat, lon):
    """Archived deterministic forecast daily-max (ensemble member archive only
    reaches ~4 days back, so bias is fit on the deterministic runs)."""
    end = date.today() - pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=PAST_DAYS)
    r = _session.get("https://historical-forecast-api.open-meteo.com/v1/forecast",
                     params={
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max",
        "models": "gfs_seamless,ecmwf_ifs025",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "temperature_unit": "fahrenheit", "timezone": "auto",
    }, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_cli(iem_station, year):
    r = _session.get("https://mesonet.agron.iastate.edu/json/cli.py",
                     params={"station": iem_station, "year": year}, timeout=45)
    r.raise_for_status()
    return {row["valid"]: row.get("high")
            for row in r.json().get("results", [])
            if row.get("high") is not None}


def main():
    params = {}
    seen_iem = {}
    for series, (iem, lat, lon, tz) in STATIONS.items():
        if series.startswith("KXLOW"):
            continue
        if iem not in seen_iem:
            try:
                payload = fetch_past(lat, lon)
                cli = fetch_cli(iem, date.today().year)
            except Exception as e:
                print(f"[warn] {series}: {e}", flush=True)
                continue
            daily = payload.get("daily", {})
            times = daily.get("time", [])
            model_cols = [k for k in daily if k.startswith("temperature_2m_max")]
            fc = {}
            for i, d_iso in enumerate(times):
                vals = [daily[c][i] for c in model_cols
                        if daily[c][i] is not None]
                if vals:
                    fc[d_iso] = float(np.mean(vals))
            errs = [truth - fc[d_iso] for d_iso, truth in cli.items()
                    if d_iso in fc]
            if len(errs) < 20:
                print(f"[warn] {iem}: only {len(errs)} matched days", flush=True)
                continue
            errs = np.array(errs)
            seen_iem[iem] = {
                "bias": round(float(np.mean(errs)), 2),
                "resid_sigma": round(float(np.std(errs)), 2),
                "n_days": len(errs),
                # full empirical error sample (truth - forecast), for the
                # kernel-mixture pmf in the pipeline
                "errors": [round(float(e), 1) for e in errs],
            }
            print(f"{iem}: bias {seen_iem[iem]['bias']:+.1f}F  "
                  f"resid sigma {seen_iem[iem]['resid_sigma']:.1f}F  "
                  f"(n={len(errs)})", flush=True)
        params[series] = seen_iem.get(iem)
    with open(DATA_DIR / "weather_station_params.json", "w") as f:
        json.dump(params, f, indent=1)
    print(f"[done] -> weather_station_params.json")


if __name__ == "__main__":
    main()
