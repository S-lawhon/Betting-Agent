"""Pull per-game outcome data from MLB StatsAPI (Phase 1, Q1 foundation).

For each final regular-season game: final/F5 linescore, winner, starter
strikeouts (both sides), team HR/hit counts, YRFI. One boxscore call per game.

Output: data/gamelogs_<season>.jsonl
Usage:  python pull_mlb_gamelogs.py 2024 2025 2026
"""
import json
import sys
import time
from pathlib import Path

import requests

BASE = "https://statsapi.mlb.com/api/v1"
OUT_DIR = Path(__file__).resolve().parent / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({"User-Agent": "mlb-props-research/0.1"})


def get(path, params=None, retries=3):
    for attempt in range(retries + 1):
        try:
            r = session.get(f"{BASE}{path}", params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
        except requests.exceptions.RequestException:
            if attempt >= retries:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"unreachable {path}")


def season_game_pks(season):
    sched = get("/schedule", {
        "sportId": 1, "season": season, "gameTypes": "R",
        "startDate": f"{season}-03-01", "endDate": f"{season}-11-15",
        "fields": "dates,date,games,gamePk,status,detailedState,gameType",
    })
    pks = []
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("detailedState") == "Final":
                pks.append((g["gamePk"], d["date"]))
    return pks


def extract(pk, date):
    line = get(f"/game/{pk}/linescore")
    box = get(f"/game/{pk}/boxscore")
    innings = line.get("innings", [])
    if len(innings) < 5:
        return None
    rec = {"pk": pk, "date": date}
    for side in ("home", "away"):
        team = box["teams"][side]
        rec[f"{side}_team"] = team["team"].get("abbreviation")
        rec[f"{side}_runs"] = line["teams"][side].get("runs")
        rec[f"{side}_f5"] = sum(i.get(side, {}).get("runs", 0) or 0
                                for i in innings[:5])
        rec[f"{side}_i1"] = innings[0].get(side, {}).get("runs", 0) or 0
        rec[f"{side}_hr"] = team["teamStats"]["batting"].get("homeRuns")
        rec[f"{side}_hits"] = team["teamStats"]["batting"].get("hits")
        # starter = first listed pitcher; verify with gamesStarted flag
        starter_ks = None
        starter_outs = None
        for pid in team.get("pitchers", [])[:1]:
            p = team["players"].get(f"ID{pid}", {})
            st = p.get("stats", {}).get("pitching", {})
            if st.get("gamesStarted"):
                starter_ks = st.get("strikeOuts")
                ip = st.get("inningsPitched", "0.0")
                try:
                    whole, frac = str(ip).split(".")
                    starter_outs = int(whole) * 3 + int(frac)
                except ValueError:
                    starter_outs = None
        rec[f"{side}_sp_ks"] = starter_ks
        rec[f"{side}_sp_outs"] = starter_outs
    return rec


def main():
    seasons = [int(a) for a in sys.argv[1:]] or [2024, 2025, 2026]
    for season in seasons:
        out_path = OUT_DIR / f"gamelogs_{season}.jsonl"
        done = set()
        if out_path.exists():
            with open(out_path) as f:
                done = {json.loads(l)["pk"] for l in f if l.strip()}
        pks = season_game_pks(season)
        print(f"[{season}] {len(pks)} final games, {len(done)} already pulled",
              flush=True)
        n = 0
        with open(out_path, "a") as f:
            for pk, date in pks:
                if pk in done:
                    continue
                try:
                    rec = extract(pk, date)
                except Exception as e:
                    print(f"[{season}] pk {pk} failed: {e}", flush=True)
                    continue
                if rec:
                    f.write(json.dumps(rec) + "\n")
                n += 1
                if n % 250 == 0:
                    print(f"[{season}] {n} games pulled", flush=True)
                time.sleep(0.15)
        print(f"[{season}] done (+{n})", flush=True)


if __name__ == "__main__":
    main()
