"""
src/polymarket_settler.py
─────────────────────────
Settlement engine for Polymarket (P-006) trades.

Resolution order (fast to slow):
  1. **Odds API /scores** — check if the real-world game has completed and
     determine winner from actual scores.  This resolves trades within minutes
     of game completion, bypassing Polymarket's often-delayed market closure.
  2. **Polymarket Gamma API** — fallback: checks if Polymarket has formally
     closed and resolved the market (can lag hours/days).
  3. **Auto-void** — safety net: if a game started >8h ago (or >48h since
     placement) and neither method has resolved it, void the trade.

After each settlement, if an EloModel is attached, the model's ratings are
updated with the game result and persisted to disk.

P&L conventions (consistent with Kalshi settler and dashboard)
──────────────────────────────────────────────────────────────
  WIN  — pnl_usd = profit only (stake excluded)
  LOSS — pnl_usd = -position_size_usd
  VOID — pnl_usd = 0 (stake returned)
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import urllib.error as _urlerr
import urllib.request as _urlreq
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"


# ── P&L helpers ──────────────────────────────────────────────────────────────

def _calc_outcome_pnl(
    side: str,
    result: str,
    size_usd: float,
    venue_prob: float,
) -> Tuple[str, float]:
    """Determine outcome label and net P&L for a settled Polymarket market.

    Parameters
    ----------
    side : str
        "YES" or "NO" — our position side.
    result : str
        "yes", "no", or "void" — resolved outcome (lower-cased).
    size_usd : float
        Dollar amount staked.
    venue_prob : float
        YES price at entry (0–1).  Used to calculate payout ratio.

    Returns
    -------
    (outcome, pnl_usd)
        outcome: "WIN", "LOSS", or "VOID".
        pnl_usd: profit on win, -size_usd on loss, 0 on void.
    """
    result_l = result.strip().lower()
    side_upper = side.strip().upper()

    if result_l == "void":
        return "VOID", 0.0

    won = (result_l == "yes" and side_upper == "YES") or \
          (result_l == "no" and side_upper == "NO")

    if won:
        if side_upper == "YES":
            # YES contract: paid venue_prob per share, pays out $1
            # Profit = size * (1 - p) / p
            pnl = size_usd * (1.0 - venue_prob) / max(venue_prob, 1e-9)
        else:
            # NO contract: paid (1 - venue_prob) per share, pays out $1
            # Profit = size * p / (1 - p)
            no_prob = 1.0 - venue_prob
            pnl = size_usd * venue_prob / max(no_prob, 1e-9)
        return "WIN", round(pnl, 2)

    return "LOSS", -round(size_usd, 2)


# ── Team name matching ────────────────────────────────────────────────────────

def _fuzzy_team_match(name_a: str, name_b: str) -> float:
    """Return 0–1 similarity between two team name strings."""
    a = name_a.strip().lower()
    b = name_b.strip().lower()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ── Polymarket Settler ──────────────────────────────────────────────────────

class PolymarketSettler:
    """Polls Odds API scores + Polymarket Gamma API to settle P-006 trades.

    Parameters
    ----------
    polymarket_client : PolymarketClient
        Client with Gamma API access.
    log_path : Path
        Path to trade_log.jsonl (read and appended to).
    pod_id : str
        Pod identifier to filter trades (default "P-006").
    odds_api_key : str
        The Odds API key for /scores lookups.
    elo_model : EloModel, optional
        If provided, ratings are updated on each settlement.
    _now_fn : callable, optional
        Returns current UTC datetime.  Injectable for tests.
    """

    # Hours after GAME START before auto-voiding an unresolved position.
    STALE_HOURS_AFTER_GAME = 8

    # Fallback: hours after PLACEMENT if no game_time is available.
    STALE_HOURS_AFTER_PLACEMENT = 48

    def __init__(
        self,
        polymarket_client,
        log_path: Path,
        pod_id: str = "P-006",
        odds_api_key: str = "",
        elo_model=None,
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._client = polymarket_client
        self._log_path = log_path
        self._pod_id = pod_id
        self._odds_api_key = odds_api_key or os.environ.get("ODDS_API_KEY", "")
        self._elo_model = elo_model
        self._now = _now_fn or (lambda: datetime.now(tz=timezone.utc))
        # Cache scores per sport per cycle to avoid redundant API calls
        self._scores_cache: Dict[str, List[Dict]] = {}

    @classmethod
    def from_config(
        cls,
        config: dict,
        polymarket_client,
        elo_model=None,
        **kwargs,
    ) -> "PolymarketSettler":
        """Construct from config dict + pre-built Polymarket client."""
        log_path = Path(
            config.get("paths", {}).get(
                "trade_log", "data/trade_logs/trade_log.jsonl"
            )
        )
        pod_id = kwargs.pop("pod_id", "P-006")
        odds_api_key = os.environ.get("ODDS_API_KEY", "")
        return cls(
            polymarket_client=polymarket_client,
            log_path=log_path,
            pod_id=pod_id,
            odds_api_key=odds_api_key,
            elo_model=elo_model,
            **kwargs,
        )

    # ── Public API ───────────────────────────────────────────────────────────

    def settle_cycle(self) -> List[Dict[str, Any]]:
        """Check all open P-006 positions; write outcomes to trade log.

        Resolution order per trade:
          1. Odds API /scores (fast — minutes after game ends)
          2. Polymarket Gamma API (slow — hours/days)
          3. Auto-void for stale positions

        Returns list of WIN / LOSS / VOID records written this cycle.
        """
        open_entries = self._open_placed_entries()
        if not open_entries:
            logger.debug("polymarket_settler: no open P-006 positions")
            return []

        # Clear scores cache at the start of each cycle
        self._scores_cache.clear()

        now = self._now()
        settled: List[Dict[str, Any]] = []

        for market_id, entry in open_entries.items():
            rec = self._check_and_settle(market_id, entry)
            if rec:
                settled.append(rec)
                continue

            # ── Auto-void stale positions ─────────────────────────────
            extra = entry.get("extra") or {}
            game_time_str = extra.get("game_time", "") or entry.get("game_time", "")
            ts_str = entry.get("timestamp_utc", "")

            try:
                if game_time_str:
                    ref_time = datetime.fromisoformat(
                        game_time_str.replace("Z", "+00:00")
                    )
                    threshold = self.STALE_HOURS_AFTER_GAME
                    ref_label = "game_time"
                elif ts_str:
                    ref_time = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")
                    )
                    threshold = self.STALE_HOURS_AFTER_PLACEMENT
                    ref_label = "placed_at"
                else:
                    continue

                hours_since = (now - ref_time).total_seconds() / 3600.0
                if hours_since >= threshold:
                    logger.warning(
                        "polymarket_settler: auto-void stale trade %s "
                        "hours_since=%.1f ref=%s threshold=%d",
                        market_id[:16], hours_since, ref_label, threshold,
                    )
                    rec = self._write_settlement(market_id, entry, "void")
                    settled.append(rec)
            except (ValueError, TypeError):
                pass

        logger.info(
            "polymarket_settler: cycle done — checked=%d settled=%d",
            len(open_entries), len(settled),
        )
        return settled

    # ── Internal ─────────────────────────────────────────────────────────────

    def _open_placed_entries(self) -> Dict[str, Dict[str, Any]]:
        """Scan trade_log.jsonl for unresolved PLACED entries from this pod.

        Returns dict[market_id → placed_record].
        """
        if not self._log_path.exists():
            return {}

        placed: Dict[str, Dict[str, Any]] = {}
        resolved_ids: set = set()

        try:
            lines = self._log_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("polymarket_settler: log read error: %s", exc)
            return {}

        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = (rec.get("action") or "").upper()
            outcome = (rec.get("outcome") or "").upper()
            pod_id = rec.get("pod_id", "")

            # Skip entries not from our pod
            if pod_id != self._pod_id:
                continue

            # BANKROLL_RESET clears the slate
            if action == "BANKROLL_RESET":
                placed.clear()
                resolved_ids.clear()
                continue

            market_id = rec.get("market_id", "")
            if not market_id:
                continue

            if action == "PLACED":
                size = float(rec.get("position_size_usd") or 0)
                if size > 0:
                    placed[market_id] = rec

            if outcome in ("WIN", "LOSS", "VOID"):
                resolved_ids.add(market_id)

        return {mid: r for mid, r in placed.items() if mid not in resolved_ids}

    def _check_and_settle(
        self,
        market_id: str,
        placed_entry: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Try to resolve a trade.  Odds API scores first, then Gamma API."""

        # ── 1. Odds API /scores (primary — fast resolution) ───────────
        scores_result = self._check_scores(placed_entry)
        if scores_result is not None:
            rec = self._write_settlement(market_id, placed_entry, scores_result,
                                         resolution_source="odds_api_scores")
            return rec

        # ── 2. Polymarket Gamma API (fallback — slow resolution) ──────
        slug = (
            placed_entry.get("market_slug", "")
            or (placed_entry.get("extra") or {}).get("market_slug", "")
        )
        try:
            info = self._client.get_market_resolution(market_id, slug=slug)
        except Exception as exc:
            logger.warning(
                "polymarket_settler: API error for %s: %s", market_id, exc
            )
            return None

        if info is None:
            logger.warning("polymarket_settler: no data returned for %s", market_id)
            return None

        if not info.get("resolved", False):
            logger.info(
                "polymarket_settler: not yet resolved: %s closed=%s active=%s prices=%s",
                market_id[:16] if market_id else "?",
                info.get("closed"),
                info.get("active"),
                info.get("outcome_prices"),
            )
            return None

        result = info["result"]  # "yes", "no", or "void"
        rec = self._write_settlement(market_id, placed_entry, result,
                                     resolution_source="gamma_api")
        return rec

    # ── Odds API scores resolution ────────────────────────────────────────

    def _check_scores(self, placed_entry: Dict[str, Any]) -> Optional[str]:
        """Check The Odds API /scores endpoint for a completed game result.

        Returns "yes" or "no" if the game is completed and winner determined,
        None if the game is not yet completed or cannot be found.
        """
        extra = placed_entry.get("extra") or {}

        # Extract sport from market_type ("sports_basketball_nba" → "basketball_nba")
        market_type = placed_entry.get("market_type", "")
        sport = ""
        if market_type.startswith("sports_"):
            sport = market_type[len("sports_"):]
        if not sport:
            sport = extra.get("sport", "")
        if not sport:
            return None

        # We need home_team, away_team, and yes_team to resolve
        home_team = extra.get("home_team", "")
        away_team = extra.get("away_team", "")
        yes_team = extra.get("yes_team", "")

        if not home_team or not away_team or not yes_team:
            # Fall back to event string parsing: "Home vs Away"
            event = placed_entry.get("event", "")
            parts = event.split(" vs ", 1)
            if len(parts) == 2 and not home_team:
                home_team = parts[0].strip()
                away_team = parts[1].strip()

        if not home_team or not away_team:
            return None

        if not self._odds_api_key:
            return None

        # Fetch scores (cached per sport per cycle)
        scores_list = self._fetch_scores(sport)
        if not scores_list:
            return None

        # Find matching completed game
        for game in scores_list:
            if not game.get("completed"):
                continue

            g_home = game.get("home_team", "")
            g_away = game.get("away_team", "")

            # Match by team names (fuzzy)
            home_match = _fuzzy_team_match(home_team, g_home) > 0.6
            away_match = _fuzzy_team_match(away_team, g_away) > 0.6

            if not (home_match and away_match):
                # Try reversed (event string might have them swapped)
                home_match_r = _fuzzy_team_match(home_team, g_away) > 0.6
                away_match_r = _fuzzy_team_match(away_team, g_home) > 0.6
                if not (home_match_r and away_match_r):
                    continue

            # Also check game time if available to avoid matching wrong game
            game_time_str = extra.get("game_time", "") or placed_entry.get("game_time", "")
            if game_time_str:
                try:
                    placed_game_time = datetime.fromisoformat(
                        game_time_str.replace("Z", "+00:00")
                    )
                    game_commence = datetime.fromisoformat(
                        game.get("commence_time", "").replace("Z", "+00:00")
                    )
                    # Must be within 2 hours of each other
                    delta_hrs = abs(
                        (placed_game_time - game_commence).total_seconds()
                    ) / 3600.0
                    if delta_hrs > 2.0:
                        continue
                except (ValueError, TypeError):
                    pass  # Skip time check if parsing fails

            # Game found and completed — determine winner from scores
            scores = game.get("scores")
            if not scores or len(scores) < 2:
                continue

            # scores is [{name, score}, {name, score}]
            score_map: Dict[str, int] = {}
            for s in scores:
                try:
                    score_map[s["name"]] = int(s["score"])
                except (KeyError, ValueError, TypeError):
                    continue

            home_score = score_map.get(g_home, 0)
            away_score = score_map.get(g_away, 0)

            if home_score == away_score:
                # Tie — can't determine winner for moneyline
                logger.info(
                    "polymarket_settler: scores tie %s vs %s (%d-%d)",
                    g_home, g_away, home_score, away_score,
                )
                return None

            home_won = home_score > away_score

            # Map winner to YES/NO result.
            # yes_team tells us which team the YES contract maps to.
            # Handle both literal "home"/"away" strings (legacy trades)
            # and actual team names (new trades).
            yes_lower = yes_team.strip().lower()
            if yes_lower == "home":
                yes_is_home = True
                yes_is_away = False
            elif yes_lower == "away":
                yes_is_home = False
                yes_is_away = True
            else:
                yes_is_home = _fuzzy_team_match(yes_team, g_home) > 0.6
                yes_is_away = _fuzzy_team_match(yes_team, g_away) > 0.6

            if yes_is_home:
                result = "yes" if home_won else "no"
            elif yes_is_away:
                result = "yes" if not home_won else "no"
            else:
                # Can't determine which side YES maps to
                logger.warning(
                    "polymarket_settler: can't map yes_team=%s to home=%s/away=%s",
                    yes_team, g_home, g_away,
                )
                return None

            logger.info(
                "polymarket_settler: scores resolved %s vs %s (%d-%d) → %s "
                "(yes_team=%s)",
                g_home, g_away, home_score, away_score, result, yes_team,
            )
            return result

        return None

    def _fetch_scores(self, sport: str) -> List[Dict]:
        """Fetch completed game scores from The Odds API.

        Cached per sport per settle_cycle() to avoid redundant API calls.
        Uses daysFrom=3 to get games completed in the last 3 days.
        """
        if sport in self._scores_cache:
            return self._scores_cache[sport]

        if not self._odds_api_key:
            return []

        url = (
            f"{_ODDS_API_BASE}/sports/{sport}/scores/"
            f"?apiKey={self._odds_api_key}&daysFrom=3&dateFormat=iso"
        )

        try:
            req = _urlreq.Request(url, headers={"Accept": "application/json"})
            with _urlreq.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self._scores_cache[sport] = data if isinstance(data, list) else []
            completed_count = sum(
                1 for g in self._scores_cache[sport] if g.get("completed")
            )
            logger.info(
                "polymarket_settler: scores fetched sport=%s total=%d completed=%d",
                sport, len(self._scores_cache[sport]), completed_count,
            )
            return self._scores_cache[sport]
        except (_urlerr.URLError, OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "polymarket_settler: scores fetch error sport=%s: %s", sport, exc,
            )
            self._scores_cache[sport] = []
            return []

    # ── Settlement + Elo update ────────────────────────────────────────────

    def _write_settlement(
        self,
        market_id: str,
        placed_entry: Dict[str, Any],
        result: str,
        resolution_source: str = "unknown",
    ) -> Dict[str, Any]:
        """Write a settlement record and update Elo ratings if applicable."""
        side = placed_entry.get("side", "YES")
        size_usd = float(placed_entry.get("position_size_usd") or 0)
        venue_prob = float(
            placed_entry.get("venue_prob")
            or placed_entry.get("kalshi_prob")
            or 0.5
        )

        outcome, pnl_usd = _calc_outcome_pnl(side, result, size_usd, venue_prob)

        # Build settlement record
        now = self._now()
        record: Dict[str, Any] = {
            **placed_entry,
            "action": outcome,
            "outcome": outcome,
            "result": result,
            "pnl_usd": pnl_usd,
            "settled_at_utc": now.isoformat(),
            "timestamp_utc": now.isoformat(),
            "resolution_source": resolution_source,
        }
        self._append_log(record)

        logger.info(
            "polymarket_settler: settled %s | side=%s result=%s outcome=%s "
            "pnl=%.2f source=%s",
            market_id[:16], side, result, outcome, pnl_usd, resolution_source,
        )

        # ── Elo auto-update ───────────────────────────────────────────
        if self._elo_model is not None and result in ("yes", "no"):
            self._update_elo(placed_entry, result)

        return record

    def _update_elo(self, placed_entry: Dict[str, Any], result: str) -> None:
        """Update Elo ratings based on game result, then persist to disk."""
        extra = placed_entry.get("extra") or {}
        home_team = extra.get("home_team", "")
        away_team = extra.get("away_team", "")
        yes_team = extra.get("yes_team", "")

        if not home_team or not away_team or not yes_team:
            return

        # Extract sport from market_type
        market_type = placed_entry.get("market_type", "")
        sport = ""
        if market_type.startswith("sports_"):
            sport = market_type[len("sports_"):]
        if not sport:
            return

        # Determine who won: yes_team won if result=="yes"
        yes_won = result == "yes"
        yes_lower = yes_team.strip().lower()
        if yes_lower == "home":
            yes_is_home = True
        elif yes_lower == "away":
            yes_is_home = False
        else:
            yes_is_home = _fuzzy_team_match(yes_team, home_team) > 0.6
        if yes_is_home:
            home_won = yes_won
        else:
            home_won = not yes_won

        try:
            new_h, new_a = self._elo_model.update(
                home_team=home_team,
                away_team=away_team,
                home_won=home_won,
                sport=sport,
            )
            self._elo_model.save()
            logger.info(
                "polymarket_settler: elo updated %s %s(%.0f) vs %s(%.0f) "
                "winner=%s",
                sport, home_team, new_h, away_team, new_a,
                "home" if home_won else "away",
            )
        except Exception as exc:
            logger.warning(
                "polymarket_settler: elo update failed: %s", exc,
            )

    def _append_log(self, record: Dict[str, Any]) -> None:
        """Append one JSON line to trade_log.jsonl."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.error(
                "polymarket_settler: log write error: path=%s err=%s",
                str(self._log_path), exc,
            )
