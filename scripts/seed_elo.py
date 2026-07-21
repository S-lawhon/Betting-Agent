#!/usr/bin/env python3
"""
scripts/seed_elo.py
───────────────────
Seed the Elo model with historical game results from the ESPN API.

Pulls completed games for NBA and NHL (2025-26 season), feeds them
chronologically into the Elo model, then saves the ratings.

Usage:
    python scripts/seed_elo.py                    # Full 2025-26 season
    python scripts/seed_elo.py --start 2026-01-01 # From Jan 1 onward
    python scripts/seed_elo.py --dry-run          # Print stats, don't save

The ESPN scoreboard API is free and requires no API key.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Add project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "Legacy" / "Kalshi Arb Project" / "src"))

from elo_model import EloModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed_elo")

# ── ESPN API config ──────────────────────────────────────────────────────

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

SPORT_CONFIG = {
    "basketball_nba": {
        "espn_path": "basketball/nba",
        "season_start": date(2025, 10, 22),  # NBA 2025-26 season opens Oct 22
        "display": "NBA",
    },
    "icehockey_nhl": {
        "espn_path": "hockey/nhl",
        "season_start": date(2025, 10, 7),   # NHL 2025-26 season opens Oct 7
        "display": "NHL",
    },
}

# ESPN team names that differ from Odds API names.
# ESPN → Odds API mapping.  Only add entries where there's a mismatch.
TEAM_NAME_MAP = {
    # ESPN uses "LA Clippers", Odds API uses "Los Angeles Clippers"
    "LA Clippers": "Los Angeles Clippers",
}

HEADERS = {"User-Agent": "BettingPodShop/1.0"}


# ── ESPN fetching ────────────────────────────────────────────────────────

def fetch_scoreboard(espn_path: str, game_date: date) -> list[dict]:
    """Fetch completed games from ESPN scoreboard for one date."""
    date_str = game_date.strftime("%Y%m%d")
    url = f"{ESPN_BASE}/{espn_path}/scoreboard?dates={date_str}"

    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return []

    games = []
    for event in data.get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        status = comp.get("status", {}).get("type", {}).get("name", "")

        # Only process completed games
        if status != "STATUS_FINAL":
            continue

        competitors = comp.get("competitors", [])
        if len(competitors) != 2:
            continue

        # ESPN lists competitors as [home, away] typically, but check homeAway field
        home_comp = away_comp = None
        for c in competitors:
            if c.get("homeAway") == "home":
                home_comp = c
            elif c.get("homeAway") == "away":
                away_comp = c

        if not home_comp or not away_comp:
            continue

        home_name = home_comp["team"]["displayName"]
        away_name = away_comp["team"]["displayName"]

        # Normalize team names to match Odds API
        home_name = TEAM_NAME_MAP.get(home_name, home_name)
        away_name = TEAM_NAME_MAP.get(away_name, away_name)

        home_score = int(home_comp.get("score", 0))
        away_score = int(away_comp.get("score", 0))
        home_won = home_score > away_score

        games.append({
            "home": home_name,
            "away": away_name,
            "home_score": home_score,
            "away_score": away_score,
            "home_won": home_won,
            "date": game_date.isoformat(),
        })

    return games


def fetch_season_games(
    sport_key: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """Fetch all completed games for a sport within a date range."""
    config = SPORT_CONFIG[sport_key]
    espn_path = config["espn_path"]
    season_start = config["season_start"]

    start = start_date or season_start
    end = end_date or date.today()

    if start < season_start:
        start = season_start

    log.info(
        "Fetching %s games: %s to %s (%d days)",
        config["display"], start, end, (end - start).days,
    )

    all_games = []
    current = start
    days_fetched = 0

    while current <= end:
        games = fetch_scoreboard(espn_path, current)
        all_games.extend(games)
        days_fetched += 1

        if days_fetched % 30 == 0:
            log.info(
                "  ... %s: %d days fetched, %d games so far",
                config["display"], days_fetched, len(all_games),
            )

        current += timedelta(days=1)

        # Rate limiting: ESPN is generous but let's be respectful
        # ~0.1s delay = ~10 req/sec, well within limits
        time.sleep(0.05)

    log.info(
        "%s: fetched %d games across %d days",
        config["display"], len(all_games), days_fetched,
    )
    return all_games


# ── Seeding ──────────────────────────────────────────────────────────────

def seed_model(
    model: EloModel,
    sport_key: str,
    games: list[dict],
) -> dict:
    """Feed games into the Elo model chronologically.

    Returns stats dict with game count, team count, etc.
    """
    # Games should already be chronological (fetched day by day)
    wins = 0
    losses = 0

    for g in games:
        model.update(
            home_team=g["home"],
            away_team=g["away"],
            home_won=g["home_won"],
            sport=sport_key,
        )
        if g["home_won"]:
            wins += 1
        else:
            losses += 1

    teams = model.team_count(sport_key)
    ratings = model.get_all_ratings(sport_key)

    # Find top and bottom teams
    sorted_teams = sorted(ratings.items(), key=lambda x: x[1], reverse=True)
    top_5 = sorted_teams[:5]
    bottom_5 = sorted_teams[-5:]

    return {
        "sport": sport_key,
        "games": len(games),
        "home_wins": wins,
        "away_wins": losses,
        "home_win_pct": wins / len(games) if games else 0,
        "teams": teams,
        "top_5": top_5,
        "bottom_5": bottom_5,
        "avg_rating": sum(ratings.values()) / len(ratings) if ratings else 0,
    }


def print_stats(stats: dict) -> None:
    """Pretty-print seeding stats."""
    sport = stats["sport"]
    display = SPORT_CONFIG[sport]["display"]

    print(f"\n{'='*60}")
    print(f"  {display} Elo Ratings — {stats['games']} games, {stats['teams']} teams")
    print(f"  Home win rate: {stats['home_win_pct']:.1%}")
    print(f"  Average rating: {stats['avg_rating']:.0f}")
    print(f"{'='*60}")

    print(f"\n  Top 5:")
    for name, rating in stats["top_5"]:
        print(f"    {rating:>7.1f}  {name}")

    print(f"\n  Bottom 5:")
    for name, rating in stats["bottom_5"]:
        print(f"    {rating:>7.1f}  {name}")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Seed Elo model with ESPN historical results")
    parser.add_argument(
        "--start", type=str, default=None,
        help="Start date (YYYY-MM-DD). Default: season start.",
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="End date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--sports", type=str, nargs="+",
        default=["basketball_nba", "icehockey_nhl"],
        help="Sports to seed.",
    )
    parser.add_argument(
        "--output", type=str, default="data/elo_ratings.json",
        help="Output ratings file path.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute ratings but don't save.",
    )
    parser.add_argument(
        "--games-log", type=str, default=None,
        help="Save fetched games to JSON for debugging.",
    )
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start) if args.start else None
    end_date = date.fromisoformat(args.end) if args.end else None

    # Create fresh model (we're seeding from scratch)
    ratings_file = PROJECT_ROOT / args.output
    model = EloModel(ratings_file=ratings_file)

    all_games = {}
    all_stats = {}

    for sport in args.sports:
        if sport not in SPORT_CONFIG:
            log.warning("Unknown sport %s — skipping", sport)
            continue

        games = fetch_season_games(sport, start_date, end_date)
        all_games[sport] = games

        if not games:
            log.warning("No games found for %s", sport)
            continue

        stats = seed_model(model, sport, games)
        all_stats[sport] = stats
        print_stats(stats)

    # Save games log if requested
    if args.games_log:
        log_path = PROJECT_ROOT / args.games_log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as f:
            json.dump(all_games, f, indent=2)
        log.info("Games log saved to %s", log_path)

    # Save model
    if not args.dry_run:
        model._dirty = True  # Force save even if no updates happened after init
        model.save()
        log.info("Elo ratings saved to %s", ratings_file)

        # Print summary
        total_games = sum(s["games"] for s in all_stats.values())
        total_teams = sum(s["teams"] for s in all_stats.values())
        print(f"\n✓ Seeded {total_games} games across {total_teams} teams")
        print(f"  Ratings file: {ratings_file}")
    else:
        print("\n[DRY RUN] — ratings not saved")


if __name__ == "__main__":
    main()
