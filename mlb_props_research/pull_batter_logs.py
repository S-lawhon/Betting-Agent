"""Phase 3 — per-batter game lines from MLB StatsAPI.

One boxscore call per game. For every batter who appeared: identity (id, name,
jersey), lineup slot, AB/H/PA, the opposing starting pitcher, park (home team),
and date. This is the training data for the hits-prop model.

Output: data/batter_logs_<season>.jsonl
Usage:  python pull_batter_logs.py 2024 2025 2026
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


def season_games(season):
    sched = get("/schedule", {
        "sportId": 1, "season": season, "gameTypes": "R",
        "startDate": f"{season}-03-01", "endDate": f"{season}-11-15",
        "fields": "dates,date,games,gamePk,status,detailedState",
    })
    out = []
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("detailedState") == "Final":
                out.append((g["gamePk"], d["date"]))
    return out


def starter_id(team_box):
    """First listed pitcher with gamesStarted=1."""
    for pid in team_box.get("pitchers", [])[:3]:
        p = team_box["players"].get(f"ID{pid}", {})
        if p.get("stats", {}).get("pitching", {}).get("gamesStarted"):
            return pid
    return None


def extract(pk, date):
    """Returns (batter_rows, pitcher_rows) from one boxscore call."""
    box = get(f"/game/{pk}/boxscore")
    rows, prows = [], []
    teams = box.get("teams", {})
    if "home" not in teams or "away" not in teams:
        return rows, prows
    home_abbr = teams["home"]["team"].get("abbreviation")
    starters = {"home": starter_id(teams["home"]),
                "away": starter_id(teams["away"])}
    for side in ("home", "away"):
        opp = "away" if side == "home" else "home"
        team = teams[side]
        abbr = team["team"].get("abbreviation")
        for pid in team.get("batters", []):
            p = team["players"].get(f"ID{pid}", {})
            bat = p.get("stats", {}).get("batting", {})
            if not bat:
                continue
            order = p.get("battingOrder")
            rows.append({
                "pk": pk, "date": date, "side": side,
                "team": abbr, "opp_team": teams[opp]["team"].get("abbreviation"),
                "park": home_abbr,
                "pid": pid,
                "name": p.get("person", {}).get("fullName"),
                "jersey": p.get("jerseyNumber"),
                "order": int(order) if order else None,
                "starter_slot": bool(order and str(order).endswith("00")),
                "ab": bat.get("atBats", 0),
                "h": bat.get("hits", 0),
                "pa": bat.get("plateAppearances", 0),
                "opp_sp": starters[opp],
            })
        for pid in team.get("pitchers", []):
            p = team["players"].get(f"ID{pid}", {})
            pit = p.get("stats", {}).get("pitching", {})
            if not pit:
                continue
            prows.append({
                "pk": pk, "date": date, "team": abbr, "pid": pid,
                "name": p.get("person", {}).get("fullName"),
                "gs": bool(pit.get("gamesStarted")),
                "bf": pit.get("battersFaced", 0),
                "h": pit.get("hits", 0),
                "so": pit.get("strikeOuts", 0),
                "bb": pit.get("baseOnBalls", 0),
            })
    return rows, prows


def main():
    seasons = [int(a) for a in sys.argv[1:]] or [2024, 2025, 2026]
    for season in seasons:
        out_path = OUT_DIR / f"batter_logs_{season}.jsonl"
        done = set()
        if out_path.exists():
            with open(out_path) as f:
                done = {json.loads(l)["pk"] for l in f if l.strip()}
        games = season_games(season)
        print(f"[{season}] {len(games)} games, {len(done)} already pulled",
              flush=True)
        n = 0
        pit_path = OUT_DIR / f"pitcher_logs_{season}.jsonl"
        with open(out_path, "a") as f, open(pit_path, "a") as pf:
            for pk, date in games:
                if pk in done:
                    continue
                try:
                    brows, prows = extract(pk, date)
                    for row in brows:
                        f.write(json.dumps(row) + "\n")
                    for row in prows:
                        pf.write(json.dumps(row) + "\n")
                except Exception as e:
                    print(f"[{season}] pk {pk} failed: {e}", flush=True)
                    continue
                n += 1
                if n % 250 == 0:
                    print(f"[{season}] {n} games", flush=True)
                time.sleep(0.12)
        print(f"[{season}] done (+{n})", flush=True)


if __name__ == "__main__":
    main()
