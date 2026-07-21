"""Weather fair-value pipeline v0 (Build 2).

Ensemble (GEFS 31 + ECMWF 51 members, Open-Meteo) -> distribution of the CLI
daily high per station -> bracket probabilities -> compare to live Kalshi
quotes -> edge sheet.

Settlement target: NWS Climatological Report (Daily) integer high, local
calendar day. Bracket semantics (verified from settled markets):
  -B{x.5} : high in {x-0.5, x+0.5}   (two integers, e.g. B89.5 -> {89,90})
  -B{x}   : high in {x-1, x, x+1}? (not observed; parse floor/cap from API)
  -T{x}   : high >= x+1              (strike_type 'greater', floor x)
  low-tail leg uses strike_type 'less'.
We always read floor_strike/cap_strike from the market object rather than
parsing tickers.

Dispersion: raw ensembles are underdispersed for a point station; each member
is smeared with N(bias, sigma). Defaults sigma=1.8F, bias=0 until the archive
(data/weather_archive/) accumulates enough days to fit per-station values
against IEM CLI truth.

Usage:
  /usr/bin/python3 src/weather_pipeline.py            # today's edge sheet
  /usr/bin/python3 src/weather_pipeline.py --date=2026-07-20
"""
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc
import fees

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ARCHIVE_DIR = DATA_DIR / "weather_archive"

# series -> (IEM station, lat, lon, IANA tz)
STATIONS = {
    "KXHIGHNY":   ("KNYC", 40.783, -73.967, "America/New_York"),
    "KXLOWTNYC":  ("KNYC", 40.783, -73.967, "America/New_York"),
    "KXHIGHLAX":  ("KLAX", 33.938, -118.389, "America/Los_Angeles"),
    "KXHIGHCHI":  ("KMDW", 41.786, -87.752, "America/Chicago"),
    "KXHIGHMIA":  ("KMIA", 25.788, -80.317, "America/New_York"),
    "KXHIGHAUS":  ("KAUS", 30.183, -97.680, "America/Chicago"),
    "KXHIGHPHIL": ("KPHL", 39.868, -75.231, "America/New_York"),
    "KXHIGHTLV":  ("KLAS", 36.072, -115.163, "America/Los_Angeles"),
    "KXHIGHTPHX": ("KPHX", 33.428, -112.004, "America/Phoenix"),
    "KXHIGHTSFO": ("KSFO", 37.620, -122.365, "America/Los_Angeles"),
    # verified 45/45 vs settlements (src/verify_stations.py, 2026-07-19);
    # note DC=National not Dulles, Houston=Hobby not IAH, Dallas=DFW
    "KXHIGHTATL":  ("KATL", 33.630, -84.442, "America/New_York"),
    "KXHIGHDEN":   ("KDEN", 39.847, -104.656, "America/Denver"),
    "KXHIGHTSEA":  ("KSEA", 47.444, -122.314, "America/Los_Angeles"),
    "KXHIGHTOKC":  ("KOKC", 35.389, -97.600, "America/Chicago"),
    "KXHIGHTDC":   ("KDCA", 38.848, -77.034, "America/New_York"),
    "KXHIGHTDAL":  ("KDFW", 32.898, -97.019, "America/Chicago"),
    "KXHIGHTBOS":  ("KBOS", 42.361, -71.010, "America/New_York"),
    "KXHIGHTHOU":  ("KHOU", 29.646, -95.277, "America/Chicago"),
    "KXHIGHTSATX": ("KSAT", 29.544, -98.484, "America/Chicago"),
    "KXHIGHTMIN":  ("KMSP", 44.883, -93.229, "America/Chicago"),
}

SIGMA_DEFAULT = 1.8   # deg F smear per member; refit from archive when ready
_session = requests.Session()
_session.headers.update({"User-Agent": "weather-fv/0.1"})


def load_station_params():
    p = DATA_DIR / "weather_station_params.json"
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {}


def fetch_det_daily_max(lat, lon, target_day):
    """Current deterministic forecast daily max (same models as the bias fit,
    so the station bias from weather_calibrate applies to it directly)."""
    r = _session.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max",
        "models": "gfs_seamless,ecmwf_ifs025",
        "forecast_days": 4,
        "temperature_unit": "fahrenheit", "timezone": "auto",
    }, timeout=45)
    r.raise_for_status()
    d = r.json().get("daily", {})
    times = d.get("time", [])
    if target_day.isoformat() not in times:
        return None
    i = times.index(target_day.isoformat())
    vals = [d[c][i] for c in d if c.startswith("temperature_2m_max")
            and d[c][i] is not None]
    return float(sum(vals) / len(vals)) if vals else None


def fetch_ensemble(lat, lon):
    """Hourly temperature members for 3 forecast days, both models."""
    r = _session.get("https://ensemble-api.open-meteo.com/v1/ensemble", params={
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m",
        "models": "gfs025,ecmwf_ifs025",
        "forecast_days": 3,
        "temperature_unit": "fahrenheit",
        "timezone": "UTC",
    }, timeout=45)
    r.raise_for_status()
    return r.json()


def member_daily_max(payload, target_day, tz):
    """Per-member max temp over target local calendar day. Returns list."""
    out = []
    for block in (payload if isinstance(payload, list) else [payload]):
        hourly = block.get("hourly", {})
        times = pd.to_datetime(hourly.get("time", []), utc=True).tz_convert(tz)
        day_mask = times.date == target_day
        if not day_mask.any():
            continue
        for key, vals in hourly.items():
            if not key.startswith("temperature_2m"):
                continue
            s = pd.Series(vals)[day_mask]
            if s.notna().any():
                out.append(float(s.max()))
    return out


def prob_table(members, sigma=SIGMA_DEFAULT, bias=0.0):
    """P(high == k) for integer k via Gaussian-smeared ensemble mixture."""
    if not members:
        return {}
    lo = int(min(members) - 8)
    hi = int(max(members) + 8)
    probs = {}
    phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))
    for k in range(lo, hi + 1):
        p = 0.0
        for m in members:
            mu = m + bias
            p += phi((k + 0.5 - mu) / sigma) - phi((k - 0.5 - mu) / sigma)
        probs[k] = p / len(members)
    return probs


KERNEL_H = 0.8  # deg F bandwidth over empirical errors


def prob_table_empirical(center, errors, h=KERNEL_H):
    """P(high == k) from bias-corrected det forecast + empirical error sample.

    Mixture over historical (truth - forecast) errors with a narrow Gaussian
    kernel: keeps the tight day-of core AND the real fat tails (busted
    forecasts) that a fitted Gaussian underweights. `errors` are raw
    (truth - forecast); center must already include the bias correction, so
    each error is de-meaned before use.
    """
    if not errors:
        return {}
    mean_e = sum(errors) / len(errors)
    mus = [center + (e - mean_e) for e in errors]
    lo, hi = int(min(mus) - 5), int(max(mus) + 5)
    phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))
    probs = {}
    for k in range(lo, hi + 1):
        p = sum(phi((k + 0.5 - mu) / h) - phi((k - 0.5 - mu) / h) for mu in mus)
        probs[k] = p / len(mus)
    return probs


def bracket_prob(probs, floor, cap, strike_type):
    """P(bracket) from integer pmf. floor/cap from the market object."""
    if strike_type == "between":
        return sum(p for k, p in probs.items() if floor <= k <= cap)
    if strike_type in ("greater", "greater_or_equal"):
        thr = floor if strike_type == "greater_or_equal" else floor + 1
        return sum(p for k, p in probs.items() if k >= thr)
    if strike_type in ("less", "less_or_equal"):
        thr = cap if cap is not None else floor
        cut = thr if strike_type == "less_or_equal" else thr - 1
        return sum(p for k, p in probs.items() if k <= cut)
    return float("nan")


def date_suffix(d):
    return d.strftime("%y%b%d").upper()


def live_markets(series_ticker, d):
    ev = f"{series_ticker}-{date_suffix(d)}"
    try:
        data = kc.get("/markets", {"event_ticker": ev, "limit": 20})
    except Exception:
        return []
    return data.get("markets", [])


def run(target_day):
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for series, (iem, lat, lon, tz) in STATIONS.items():
        try:
            payload = fetch_ensemble(lat, lon)
        except Exception as e:
            print(f"[warn] ensemble {series}: {e}", flush=True)
            continue
        # archive raw ensemble for future bias/sigma fitting
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M")
        with open(ARCHIVE_DIR / f"ens_{iem}_{stamp}.json", "w") as f:
            json.dump(payload, f)
        members = member_daily_max(payload, target_day, tz)
        if len(members) < 20:
            print(f"[warn] {series}: only {len(members)} members for "
                  f"{target_day}", flush=True)
            continue
        # location: bias-corrected deterministic forecast (grid-consistent
        # with the calibration fit); dispersion: empirical error mixture when
        # available (v1), else recentred-ensemble Gaussian smear (v0).
        sp = load_station_params().get(series) or {}
        det = fetch_det_daily_max(lat, lon, target_day)
        ens_med = float(pd.Series(members).median())
        if det is not None and sp.get("errors"):
            center = det + sp["bias"]
            probs = prob_table_empirical(center, sp["errors"])
        else:
            if det is not None:
                center = det + sp.get("bias", 0.0)
                members = [center + (m - ens_med) for m in members]
            sigma = max(SIGMA_DEFAULT, sp.get("resid_sigma", 0.0))
            probs = prob_table(members, sigma=sigma)
        agg = "min" if series.startswith("KXLOW") else "max"
        if agg == "min":  # low-temp series: recompute on daily min
            continue  # v0: highs only; lows need member_daily_min
        mkts = live_markets(series, target_day)
        for m in mkts:
            fair = bracket_prob(probs, m.get("floor_strike"),
                                m.get("cap_strike"), m.get("strike_type"))
            bid, ask = kc.fnum(m.get("yes_bid_dollars")), kc.fnum(m.get("yes_ask_dollars"))
            rows.append({
                "series": series, "ticker": m["ticker"],
                "sub": m.get("yes_sub_title"),
                "fair": round(fair, 3),
                "bid": bid, "ask": ask,
                "ens_median": round(pd.Series(members).median(), 1),
                "n_members": len(members),
                # maker edge: post inside the spread at fair +/- required edge
                "buy_edge": round(fair - ask - fees.fee_per_contract(ask or 0.5, series), 3)
                    if ask not in (None, 0, 1) else None,
                "sell_edge": round(bid - fair - fees.fee_per_contract(bid or 0.5, series), 3)
                    if bid not in (None, 0, 1) else None,
            })
        time.sleep(0.4)
    df = pd.DataFrame(rows)
    out = DATA_DIR / f"weather_fair_{target_day.isoformat()}.csv"
    df.to_csv(out, index=False)
    print(df.to_string(index=False))
    print(f"\n[done] -> {out}")
    return df


if __name__ == "__main__":
    d = date.today()
    for a in sys.argv[1:]:
        if a.startswith("--date"):
            d = date.fromisoformat(a.split("=")[1])
    run(d)
