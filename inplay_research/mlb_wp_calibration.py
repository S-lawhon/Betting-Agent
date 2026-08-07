#!/usr/bin/env python3
"""Forward, outcome-blind gate for ``src.mlb_win_prob``.

The governing rule is ``research/MLB_WIN_PROB_CALIBRATION_RULE.md``.  Arm the
study before collecting its sample, use ``status`` for outcome-free progress,
and call ``evaluate`` only after the manifest's read-not-before time.
"""
from __future__ import annotations

import argparse
import bisect
import glob
import gzip
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.et_time import parse_mlb_ticker_start  # noqa: E402
from src.mlb_teams import split_codes  # noqa: E402
from src.mlb_win_prob import MlbGameSnapshot, home_win_probability  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RULE_PATH = ROOT / "research" / "MLB_WIN_PROB_CALIBRATION_RULE.md"
MODEL_PATH = ROOT / "src" / "mlb_win_prob.py"
DEFAULT_DATA_DIR = ROOT / "data" / "book_capture"
DEFAULT_STATE_DIR = ROOT / "data" / "research" / "mlb_wp_calibration"
DEFAULT_MANIFEST = DEFAULT_STATE_DIR / "manifest.json"
DEFAULT_RESULT = DEFAULT_STATE_DIR / "result.json"

CHECKPOINTS_S = (30 * 60, 60 * 60, 90 * 60, 120 * 60)
CHECKPOINT_TOLERANCE_S = 90
PREGAME_MIN_LEAD_S = 5 * 60
PREGAME_MAX_LEAD_S = 6 * 60 * 60
STATE_LAGS_S = (30, 60)
MIN_CHECKPOINTS = 2
FIRST_LOOK_GAMES = 60
FINAL_LOOK_GAMES = 120
BOOTSTRAP_REPS = 5000
BOOTSTRAP_SEED = 20260807
MIN_BLIND_DAYS = 7
_DOUBLEHEADER_RE = re.compile(r"G\d$")


class CalibrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class TickerIdentity:
    ticker: str
    event_ticker: str
    away_code: str
    home_code: str
    target_code: str
    scheduled_start: datetime

    @property
    def target_is_home(self) -> bool:
        return self.target_code == self.home_code


@dataclass(frozen=True)
class PricePoint:
    epoch: float
    home_mid: float
    target_is_home: bool


@dataclass
class EventTape:
    identity: TickerIdentity
    prices: list[PricePoint]


@dataclass(frozen=True)
class ScheduledGame:
    game_pk: int
    away_code: str
    home_code: str
    start_epoch: float
    is_final: bool
    detailed_state: str


class StateTimeline:
    def __init__(self, states: Sequence[tuple[float, MlbGameSnapshot]],
                 final_home_won: bool):
        ordered = sorted(states, key=lambda row: row[0])
        self._epochs = [row[0] for row in ordered]
        self._states = [row[1] for row in ordered]
        self.final_home_won = final_home_won

    def state_at(self, epoch: float) -> Optional[MlbGameSnapshot]:
        idx = bisect.bisect_right(self._epochs, epoch) - 1
        return self._states[idx] if idx >= 0 else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise CalibrationError(f"timestamp lacks timezone: {value}")
    return dt.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise CalibrationError(f"refusing to replace immutable file: {path}")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    if exclusive and path.exists():
        tmp.unlink(missing_ok=True)
        raise CalibrationError(f"refusing to replace immutable file: {path}")
    os.replace(tmp, path)


def parse_ticker(ticker: str) -> Optional[TickerIdentity]:
    parts = str(ticker or "").upper().split("-")
    if len(parts) != 3 or parts[0] != "KXMLBGAME":
        return None
    start = parse_mlb_ticker_start(ticker)
    if start is None:
        return None
    blob = _DOUBLEHEADER_RE.sub("", parts[1][11:])
    codes = split_codes(blob)
    if codes is None or parts[2] not in codes:
        return None
    away, home = codes
    return TickerIdentity(
        ticker=ticker,
        event_ticker=f"KXMLBGAME-{parts[1]}",
        away_code=away,
        home_code=home,
        target_code=parts[2],
        scheduled_start=start,
    )


def orient_home_mid(identity: TickerIdentity, mid: Any) -> Optional[float]:
    try:
        value = float(mid)
    except (TypeError, ValueError):
        return None
    if not 0.0 < value < 1.0:
        return None
    return value if identity.target_is_home else 1.0 - value


def _open_capture(path: str):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def capture_files(data_dir: Path) -> list[str]:
    return sorted(
        glob.glob(str(data_dir / "book_capture_*.jsonl"))
        + glob.glob(str(data_dir / "book_capture_*.jsonl.gz"))
    )


def load_event_tapes(data_dir: Path, sample_start: datetime) -> tuple[dict[str, EventTape], Counter]:
    tapes: dict[str, EventTape] = {}
    exclusions: Counter = Counter()
    for path in capture_files(data_dir):
        try:
            with _open_capture(path) as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        exclusions["invalid_json"] += 1
                        continue
                    if row.get("type") != "BOOK":
                        continue
                    ident = parse_ticker(row.get("ticker", ""))
                    if ident is None:
                        exclusions["unparseable_ticker"] += 1
                        continue
                    if ident.scheduled_start < sample_start:
                        continue
                    home_mid = orient_home_mid(ident, row.get("mid"))
                    try:
                        epoch = float(row.get("epoch"))
                    except (TypeError, ValueError):
                        exclusions["invalid_epoch"] += 1
                        continue
                    if home_mid is None:
                        exclusions["invalid_mid"] += 1
                        continue
                    tape = tapes.setdefault(
                        ident.event_ticker, EventTape(identity=ident, prices=[]))
                    tape.prices.append(PricePoint(epoch, home_mid, ident.target_is_home))
        except OSError:
            exclusions["unreadable_file"] += 1
    for tape in tapes.values():
        tape.prices.sort(key=lambda point: (point.epoch, point.target_is_home))
    return tapes, exclusions


def pregame_anchor(tape: EventTape) -> Optional[PricePoint]:
    start = tape.identity.scheduled_start.timestamp()
    candidates = [
        point for point in tape.prices
        if start - PREGAME_MAX_LEAD_S <= point.epoch <= start - PREGAME_MIN_LEAD_S
    ]
    return max(candidates, key=lambda point: (point.epoch, point.target_is_home),
               default=None)


def checkpoint_prices(tape: EventTape) -> list[tuple[int, PricePoint]]:
    start = tape.identity.scheduled_start.timestamp()
    selected: list[tuple[int, PricePoint]] = []
    for offset in CHECKPOINTS_S:
        target = start + offset
        candidates = [
            point for point in tape.prices
            if abs(point.epoch - target) <= CHECKPOINT_TOLERANCE_S
        ]
        if not candidates:
            continue
        home = [point for point in candidates if point.target_is_home]
        pool = home or candidates
        point = min(pool, key=lambda item: (abs(item.epoch - target), item.epoch))
        selected.append((offset, point))
    return selected


def status_payload(manifest: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    verify_manifest(manifest)
    tapes, exclusions = load_event_tapes(data_dir, _parse_iso(manifest["sample_start_utc"]))
    anchored = 0
    eligible = 0
    checkpoint_counts: Counter = Counter()
    for tape in tapes.values():
        anchor = pregame_anchor(tape)
        points = checkpoint_prices(tape)
        if anchor is not None:
            anchored += 1
        checkpoint_counts[len(points)] += 1
        if anchor is not None and len(points) >= MIN_CHECKPOINTS:
            eligible += 1
    return {
        "schema_version": 1,
        "status": "COLLECTING",
        "outcomes_read": False,
        "as_of_utc": _iso(_utc_now()),
        "sample_start_utc": manifest["sample_start_utc"],
        "read_not_before_utc": manifest["read_not_before_utc"],
        "events_seen": len(tapes),
        "events_with_pregame_anchor": anchored,
        "events_book_eligible": eligible,
        "first_look_games": FIRST_LOOK_GAMES,
        "checkpoint_count_distribution": dict(sorted(checkpoint_counts.items())),
        "row_exclusions": dict(exclusions),
    }


def arm(manifest_path: Path, now: Optional[datetime] = None) -> dict[str, Any]:
    armed = (now or _utc_now()).astimezone(timezone.utc)
    payload = {
        "schema_version": 1,
        "status": "ARMED",
        "armed_at_utc": _iso(armed),
        "sample_start_utc": _iso(armed),
        "read_not_before_utc": _iso(armed + timedelta(days=MIN_BLIND_DAYS)),
        "rule_path": str(RULE_PATH.relative_to(ROOT)),
        "rule_sha256": _sha256(RULE_PATH),
        "model_path": str(MODEL_PATH.relative_to(ROOT)),
        "model_sha256": _sha256(MODEL_PATH),
        "checkpoints_seconds": list(CHECKPOINTS_S),
        "state_lags_seconds": list(STATE_LAGS_S),
        "first_look_games": FIRST_LOOK_GAMES,
        "final_look_games": FINAL_LOOK_GAMES,
    }
    _atomic_json(manifest_path, payload, exclusive=True)
    return payload


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        with path.open() as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"cannot read manifest {path}: {exc}") from exc
    verify_manifest(payload)
    return payload


def verify_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("status") != "ARMED":
        raise CalibrationError("manifest is not ARMED")
    expected = {
        "rule_sha256": _sha256(RULE_PATH),
        "model_sha256": _sha256(MODEL_PATH),
        "checkpoints_seconds": list(CHECKPOINTS_S),
        "state_lags_seconds": list(STATE_LAGS_S),
        "first_look_games": FIRST_LOOK_GAMES,
        "final_look_games": FINAL_LOOK_GAMES,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise CalibrationError(f"manifest mismatch for {key}")


def _stats_code(team: dict[str, Any]) -> str:
    return str(team.get("abbreviation") or "").upper()


def fetch_schedule(session: requests.Session, date_str: str) -> list[ScheduledGame]:
    response = session.get(
        "https://statsapi.mlb.com/api/v1/schedule",
        params={"sportId": 1, "date": date_str, "hydrate": "team"},
        timeout=20,
    )
    response.raise_for_status()
    games: list[ScheduledGame] = []
    for day in response.json().get("dates", []):
        for row in day.get("games", []):
            try:
                start = _parse_iso(row["gameDate"]).timestamp()
                away = _stats_code(row["teams"]["away"]["team"])
                home = _stats_code(row["teams"]["home"]["team"])
                status = row.get("status") or {}
                games.append(ScheduledGame(
                    game_pk=int(row["gamePk"]), away_code=away, home_code=home,
                    start_epoch=start,
                    is_final=status.get("abstractGameState") == "Final",
                    detailed_state=str(status.get("detailedState") or ""),
                ))
            except (KeyError, TypeError, ValueError, CalibrationError):
                continue
    return games


def match_scheduled_game(identity: TickerIdentity,
                         games: Sequence[ScheduledGame]) -> Optional[ScheduledGame]:
    exact = [game for game in games
             if game.away_code == identity.away_code and game.home_code == identity.home_code]
    if not exact:
        return None
    target = identity.scheduled_start.timestamp()
    game = min(exact, key=lambda item: abs(item.start_epoch - target))
    return game if abs(game.start_epoch - target) <= 3 * 60 * 60 else None


def _play_snapshot(play: dict[str, Any]) -> Optional[tuple[float, MlbGameSnapshot]]:
    about = play.get("about") or {}
    result = play.get("result") or {}
    count = play.get("count") or {}
    matchup = play.get("matchup") or {}
    try:
        epoch = _parse_iso(about.get("endTime") or about.get("startTime")).timestamp()
        inning = int(about.get("inning") or 1)
        is_top = about.get("halfInning") == "top"
        outs = int(count.get("outs") or 0)
        home_score = int(result.get("homeScore") or about.get("homeScore") or 0)
        away_score = int(result.get("awayScore") or about.get("awayScore") or 0)
    except (TypeError, ValueError, CalibrationError):
        return None
    on_first = bool(matchup.get("postOnFirst"))
    on_second = bool(matchup.get("postOnSecond"))
    on_third = bool(matchup.get("postOnThird"))
    if not (on_first or on_second or on_third):
        for runner in play.get("runners") or []:
            end = (runner.get("movement") or {}).get("end")
            on_first |= end == "1B"
            on_second |= end == "2B"
            on_third |= end == "3B"
    if outs >= 3:
        if not is_top:
            inning += 1
        is_top = not is_top
        outs = 0
        on_first = on_second = on_third = False
    snap = MlbGameSnapshot(
        home_score=home_score, away_score=away_score, inning=inning,
        is_top=is_top, outs=outs, on_first=on_first, on_second=on_second,
        on_third=on_third, is_final=False,
    )
    return epoch, snap


def fetch_timeline(session: requests.Session, game: ScheduledGame) -> Optional[StateTimeline]:
    if not game.is_final or any(word in game.detailed_state.lower()
                                for word in ("postponed", "cancelled", "suspended")):
        return None
    response = session.get(
        f"https://statsapi.mlb.com/api/v1.1/game/{game.game_pk}/feed/live",
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()
    linescore = ((data.get("liveData") or {}).get("linescore") or {})
    teams = linescore.get("teams") or {}
    try:
        home_score = int((teams.get("home") or {})["runs"])
        away_score = int((teams.get("away") or {})["runs"])
    except (KeyError, TypeError, ValueError):
        return None
    if home_score == away_score:
        return None
    states = []
    plays = (((data.get("liveData") or {}).get("plays") or {}).get("allPlays") or [])
    for play in plays:
        parsed = _play_snapshot(play)
        if parsed is not None:
            states.append(parsed)
    return StateTimeline(states, home_score > away_score) if states else None


def cluster_bootstrap(values: Sequence[float], reps: int = BOOTSTRAP_REPS,
                      seed: int = BOOTSTRAP_SEED) -> dict[str, Optional[float]]:
    if not values:
        return {"mean": None, "lo": None, "hi": None, "se": None}
    mean = sum(values) / len(values)
    if len(values) < 2:
        return {"mean": mean, "lo": None, "hi": None, "se": None}
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    center = sum(means) / len(means)
    se = (sum((value - center) ** 2 for value in means) / (len(means) - 1)) ** 0.5
    return {
        "mean": mean,
        "lo": means[int(0.025 * len(means))],
        "hi": means[int(0.975 * len(means))],
        "se": se,
    }


def _score_game(tape: EventTape, timeline: StateTimeline, anchor: PricePoint,
                points: Sequence[tuple[int, PricePoint]], lag_s: int) -> Optional[dict[str, Any]]:
    outcome = 1.0 if timeline.final_home_won else 0.0
    rows = []
    for offset, point in points:
        state = timeline.state_at(point.epoch - lag_s)
        if state is None:
            continue
        model = home_win_probability(state, anchor.home_mid).home_wp
        market_loss = (point.home_mid - outcome) ** 2
        model_loss = (model - outcome) ** 2
        rows.append({
            "offset_seconds": offset,
            "market_home_mid": point.home_mid,
            "model_home_probability": model,
            "market_brier": market_loss,
            "model_brier": model_loss,
            "delta": market_loss - model_loss,
        })
    if len(rows) < MIN_CHECKPOINTS:
        return None
    return {
        "event_ticker": tape.identity.event_ticker,
        "scheduled_start_utc": _iso(tape.identity.scheduled_start),
        "pregame_home_mid": anchor.home_mid,
        "outcome_home_win": bool(timeline.final_home_won),
        "n_checkpoints": len(rows),
        "market_brier": sum(row["market_brier"] for row in rows) / len(rows),
        "model_brier": sum(row["model_brier"] for row in rows) / len(rows),
        "delta": sum(row["delta"] for row in rows) / len(rows),
        "checkpoints": rows,
    }


def _summarize(games: Sequence[dict[str, Any]]) -> dict[str, Any]:
    deltas = [game["delta"] for game in games]
    stats = cluster_bootstrap(deltas)
    stats.update({
        "n_games": len(games),
        "n_checkpoints": sum(game["n_checkpoints"] for game in games),
        "market_brier": sum(game["market_brier"] for game in games) / len(games),
        "model_brier": sum(game["model_brier"] for game in games) / len(games),
    })
    return stats


def verdict_for(summaries: dict[str, dict[str, Any]], look_size: int) -> str:
    primary = summaries[str(STATE_LAGS_S[0])]
    robust = summaries[str(STATE_LAGS_S[1])]
    passes = all(
        summary["mean"] is not None and summary["mean"] > 0
        and summary["lo"] is not None and summary["lo"] > 0
        for summary in (primary, robust)
    )
    if passes:
        return "PASS"
    if look_size == FIRST_LOOK_GAMES and primary["mean"] > 0:
        return "NO_DECISION"
    return "KILL"


def _load_existing_result(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"cannot read existing result {path}: {exc}") from exc


def evaluate(manifest: dict[str, Any], data_dir: Path, result_path: Path,
             now: Optional[datetime] = None, session: Optional[requests.Session] = None
             ) -> dict[str, Any]:
    verify_manifest(manifest)
    current = (now or _utc_now()).astimezone(timezone.utc)
    if current < _parse_iso(manifest["read_not_before_utc"]):
        raise CalibrationError("outcomes are still blind: read-not-before has not arrived")
    prior = _load_existing_result(result_path)
    if prior and prior.get("verdict") != "NO_DECISION":
        raise CalibrationError(f"terminal result already exists: {prior.get('verdict')}")
    look_size = FINAL_LOOK_GAMES if prior else FIRST_LOOK_GAMES
    if prior and prior.get("look_size") != FIRST_LOOK_GAMES:
        raise CalibrationError("existing result is not the sanctioned first look")

    tapes, row_exclusions = load_event_tapes(
        data_dir, _parse_iso(manifest["sample_start_utc"]))
    candidates = []
    exclusions: Counter = Counter(row_exclusions)
    for tape in tapes.values():
        anchor = pregame_anchor(tape)
        points = checkpoint_prices(tape)
        if anchor is None:
            exclusions["no_pregame_anchor"] += 1
        elif len(points) < MIN_CHECKPOINTS:
            exclusions["too_few_book_checkpoints"] += 1
        else:
            candidates.append((tape, anchor, points))
    candidates.sort(key=lambda row: (row[0].identity.scheduled_start,
                                     row[0].identity.event_ticker))
    if len(candidates) < look_size:
        raise CalibrationError(
            f"only {len(candidates)} book-eligible games; need {look_size} before reading outcomes")

    sess = session or requests.Session()
    schedules: dict[str, list[ScheduledGame]] = {}
    scored: dict[int, list[dict[str, Any]]] = {lag: [] for lag in STATE_LAGS_S}
    for tape, anchor, points in candidates:
        date_str = tape.identity.scheduled_start.date().isoformat()
        if date_str not in schedules:
            schedules[date_str] = fetch_schedule(sess, date_str)
        game = match_scheduled_game(tape.identity, schedules[date_str])
        if game is None:
            exclusions["schedule_no_match"] += 1
            continue
        try:
            timeline = fetch_timeline(sess, game)
        except requests.RequestException:
            exclusions["statsapi_error"] += 1
            continue
        if timeline is None:
            exclusions["not_admissible_final"] += 1
            continue
        per_lag = {
            lag: _score_game(tape, timeline, anchor, points, lag)
            for lag in STATE_LAGS_S
        }
        if any(value is None for value in per_lag.values()):
            exclusions["too_few_state_checkpoints"] += 1
            continue
        for lag, value in per_lag.items():
            scored[lag].append(value)
        if len(scored[STATE_LAGS_S[0]]) >= look_size:
            break

    if len(scored[STATE_LAGS_S[0]]) < look_size:
        raise CalibrationError(
            f"only {len(scored[STATE_LAGS_S[0]])} fully admissible completed games; "
            f"need {look_size}; no effect result written")
    for lag in STATE_LAGS_S:
        scored[lag] = scored[lag][:look_size]
    summaries = {str(lag): _summarize(scored[lag]) for lag in STATE_LAGS_S}
    verdict = verdict_for(summaries, look_size)
    payload = {
        "schema_version": 1,
        "evaluated_at_utc": _iso(current),
        "manifest_rule_sha256": manifest["rule_sha256"],
        "manifest_model_sha256": manifest["model_sha256"],
        "look_size": look_size,
        "verdict": verdict,
        "summaries_by_state_lag_seconds": summaries,
        "exclusions": dict(exclusions),
        "games_by_state_lag_seconds": {str(lag): scored[lag] for lag in STATE_LAGS_S},
        "interpretation": (
            "Model calibration only; no execution edge and no authority to revive P-016 or P-018."
        ),
    }
    _atomic_json(result_path, payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("arm", "status", "evaluate"))
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "arm":
            payload = arm(args.manifest)
        else:
            manifest = load_manifest(args.manifest)
            if args.command == "status":
                payload = status_payload(manifest, args.data_dir)
            else:
                payload = evaluate(manifest, args.data_dir, args.result)
    except CalibrationError as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
