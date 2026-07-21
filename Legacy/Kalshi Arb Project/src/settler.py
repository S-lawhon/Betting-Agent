"""
src/settler.py
──────────────
Settlement engine: resolves open paper positions using real game results.

How it works
────────────
  rebuild_ledger_from_log()
      Called once at startup.  Reads all unresolved PLACED entries from the
      trade log and opens matching positions in the Ledger so the RiskManager
      has accurate exposure / open-position counts after a restart.

  settle_cycle() → List[dict]
      Called each scan cycle (before executor.run_cycle()).
      1. Scans trade_log.jsonl for PLACED entries with no matching
         WIN / LOSS / VOID record.
      2. For each open position, checks The Odds API /scores endpoint
         for completed game results (primary), or the Kalshi API as
         fallback for non-sports markets.
      3. If the game is completed, determines winner from scores,
         calculates P&L, closes the Ledger position, and appends a
         WIN / LOSS / VOID record to trade_log.jsonl.
      4. Returns the list of settlement records written this cycle.

  Note: The Kalshi demo API never settles sports markets, so the
  Odds API /scores endpoint is used as the primary resolution source
  for paper trading.  This gives us real-world results regardless
  of Kalshi's settlement timing.

P&L conventions  (consistent with serve_dashboard.py _collect_data)
────────────────────────────────────────────────────────────────────
  WIN  — pnl_usd = profit only (stake excluded)
         Dashboard restores stake + pnl so: bankroll += size + pnl
  LOSS — pnl_usd = -position_size_usd
         bankroll += size + (-size) = 0  (full loss)
  VOID — pnl_usd = 0
         bankroll += size + 0 = size  (stake returned)
"""

from __future__ import annotations

import difflib
import json
import os
import re
import urllib.error as _urlerr
import urllib.request as _urlreq
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from kalshi_client import KalshiAPIError, KalshiClient
from logger_setup import get_logger
from risk_manager import Ledger, Position

log = get_logger(__name__)

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"


# ── P&L helpers ───────────────────────────────────────────────────────────────

def _calc_outcome_pnl(
    side: str,
    result: str,
    size_usd: float,
    kalshi_prob: float,
) -> Tuple[str, float]:
    """
    Determine outcome label and net P&L for a settled market.

    Parameters
    ----------
    side : str
        ``"YES"`` or ``"NO"`` — our position side.
    result : str
        ``"yes"``, ``"no"``, or ``"void"`` — market result (lower-cased).
    size_usd : float
        Dollar amount staked (cost of the position).
    kalshi_prob : float
        YES mid-price at entry (0–1).  Used to infer per-dollar payout ratio.

    Returns
    -------
    (outcome, pnl_usd)
        outcome is ``"WIN"``, ``"LOSS"``, or ``"VOID"``.
        pnl_usd is the *profit* only on a win, ``-size_usd`` on a loss,
        or ``0`` on a void — matching the dashboard's bookkeeping model.
    """
    result_l   = result.strip().lower()
    side_upper = side.strip().upper()

    if result_l == "void":
        return "VOID", 0.0

    won = (result_l == "yes" and side_upper == "YES") or \
          (result_l == "no"  and side_upper == "NO")

    if won:
        if side_upper == "YES":
            # YES contract: paid kalshi_prob per dollar of stake.
            # Profit = size * (1 - p) / p
            pnl = size_usd * (1.0 - kalshi_prob) / max(kalshi_prob, 1e-9)
        else:
            # NO contract: paid (1 - kalshi_prob) per dollar of stake.
            # Profit = size * p / (1 - p)
            no_prob = 1.0 - kalshi_prob
            pnl = size_usd * kalshi_prob / max(no_prob, 1e-9)
        return "WIN", round(pnl, 2)

    # Lost — entire stake forfeit
    return "LOSS", -round(size_usd, 2)


# ── Team name matching ────────────────────────────────────────────────────────

def _fuzzy_team_match(name_a: str, name_b: str) -> float:
    """Return 0–1 similarity between two team name strings."""
    a = name_a.strip().lower()
    b = name_b.strip().lower()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ── Settler ───────────────────────────────────────────────────────────────────

class Settler:
    """
    Resolves open paper positions using real game results.

    Primary resolution: The Odds API /scores endpoint (real game results).
    Fallback: Kalshi API get_market (for non-sports or when scores unavailable).

    Parameters
    ----------
    kalshi : KalshiClient
        Authenticated Kalshi API client (fallback for non-sports markets).
    ledger : Ledger
        In-memory ledger (positions closed here on settlement).
    log_path : Path
        Path to ``trade_log.jsonl`` (read and appended to).
    odds_api_key : str
        The Odds API key for /scores lookups.
    _now_fn : callable, optional
        Returns current UTC datetime.  Injectable for tests.
    """

    def __init__(
        self,
        kalshi: KalshiClient,
        ledger: Ledger,
        log_path: Path,
        odds_api_key: str = "",
        _now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._kalshi       = kalshi
        self._ledger       = ledger
        self._log_path     = log_path
        self._odds_api_key = odds_api_key or os.environ.get("ODDS_API_KEY", "")
        self._now           = _now_fn or (lambda: datetime.now(tz=timezone.utc))
        # Cache scores per sport per cycle to avoid redundant API calls
        self._scores_cache: Dict[str, List[Dict]] = {}

    @classmethod
    def from_config(
        cls,
        config: dict,
        kalshi: KalshiClient,
        ledger: Ledger,
        **kwargs,
    ) -> "Settler":
        """Construct from a loaded config.yaml dict + pre-built components."""
        log_path = Path(
            config.get("paths", {}).get(
                "trade_log", "data/trade_logs/trade_log.jsonl"
            )
        )
        odds_api_key = os.environ.get("ODDS_API_KEY", "")
        return cls(
            kalshi=kalshi, ledger=ledger, log_path=log_path,
            odds_api_key=odds_api_key, **kwargs,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def rebuild_ledger_from_log(self, pod_ids: Optional[tuple] = None) -> int:
        """
        Reopen Ledger positions for unresolved PLACED entries in the log.

        Called once at startup so the RiskManager has accurate open-position
        counts and exposure even after a process restart.

        Only Kalshi-venue positions are added to the Ledger for exposure
        tracking.  Non-Kalshi positions (e.g. Polymarket arb legs) are
        hedged against a Kalshi leg and carry near-zero net risk — counting
        both legs would double-count exposure.  settle_cycle() still sees
        all venues for auto-void purposes.

        Parameters
        ----------
        pod_ids : tuple of str, optional
            If provided, only load positions whose ``pod_id`` field matches
            one of these values.  Use ``""`` to match legacy entries that
            have no ``pod_id``.  When ``None`` (default), all pods are
            loaded (preserving backward compatibility).

            Example: ``pod_ids=("P-001", "")`` loads only P-001 and legacy
            entries, preventing other pods' positions from inflating this
            Ledger's open-position count.

        Returns
        -------
        int : number of positions reopened.
        """
        open_entries = self._open_placed_entries()
        count = 0
        skipped_other_pod = 0

        for _key, entry in open_entries.items():
            ticker      = entry.get("market_ticker") or entry.get("market_id") or ""
            size_usd    = float(entry.get("position_size_usd") or 0)
            kalshi_prob = float(entry.get("kalshi_prob") or 0.5)
            side        = entry.get("side", "YES")
            ts_str      = entry.get("timestamp_utc", "")

            if size_usd <= 0:
                continue

            # Only count Kalshi-venue positions in the Ledger.
            # Polymarket arb legs are hedged — adding them would
            # double-count exposure and block all trading.
            venue = (entry.get("venue") or "").lower()
            is_kalshi = venue == "kalshi" or venue == "" or ticker.startswith("KX")
            if not is_kalshi:
                continue

            # Pod-aware filtering: when pod_ids is set, only load
            # positions belonging to the specified pods.  This prevents
            # cross-pod contamination where (e.g.) P-002 arb legs
            # inflate P-001's RiskManager open-position count.
            if pod_ids is not None:
                entry_pod = entry.get("pod_id") or ""
                if entry_pod not in pod_ids:
                    skipped_other_pod += 1
                    continue

            try:
                opened_at = datetime.fromisoformat(
                    ts_str.replace("Z", "+00:00")
                ) if ts_str else self._now()
            except ValueError:
                opened_at = self._now()

            pos = Position.new(
                market_ticker=ticker,
                side=side,
                size_usd=size_usd,
                kalshi_price=kalshi_prob,
                opened_at=opened_at,
            )
            self._ledger.open_position(pos)
            count += 1

        if count or skipped_other_pod:
            log.info("settler_rebuilt_ledger", positions=count,
                     skipped_other_pod=skipped_other_pod,
                     pod_filter=list(pod_ids) if pod_ids else "all")
        else:
            log.debug("settler_rebuilt_ledger", positions=0)

        return count

    # Hours after GAME START before auto-voiding an unresolved position.
    # Most sports games complete within 4-5 hours; 8h is generous.
    STALE_HOURS_AFTER_GAME = 8

    # Fallback: hours after PLACEMENT if no game_time is available.
    # Positions are often placed 20+ hours before game start, so 48h
    # avoids voiding during the actual game.
    STALE_HOURS_AFTER_PLACEMENT = 48

    def settle_cycle(self) -> List[Dict[str, Any]]:
        """
        Check all open positions for completed games; write outcomes to trade log.

        Returns
        -------
        List of WIN / LOSS / VOID records written this cycle (empty if none
        have settled yet or no open positions exist).
        """
        open_entries = self._open_placed_entries()
        if not open_entries:
            log.info("settler_no_open_positions")
            return []

        log.info("settler_checking", open_count=len(open_entries),
                 tickers=list(open_entries.keys())[:5])

        # Clear scores cache for this cycle
        self._scores_cache.clear()

        now = self._now()
        settled: List[Dict[str, Any]] = []

        # Cache resolved market results per ticker to avoid duplicate API
        # calls when multiple trades exist for the same market.
        # Values: "yes", "no", "void", or None (not resolved yet).
        ticker_result_cache: Dict[str, Optional[str]] = {}

        for _key, entry in open_entries.items():
            # Extract actual ticker from the entry (key may be a fingerprint)
            ticker = entry.get("market_ticker") or entry.get("market_id") or ""
            if not ticker:
                continue

            # Check if we already resolved this ticker in this cycle
            if ticker in ticker_result_cache:
                cached_result = ticker_result_cache[ticker]
                if cached_result is not None:
                    rec = self._settle_position(ticker, entry, cached_result)
                    settled.append(rec)
                    continue
                # cached_result is None → market not resolved, fall through
                # to stale check
            else:
                rec = self._check_and_settle(ticker, entry)
                if rec:
                    # Cache the result for other entries on the same ticker
                    ticker_result_cache[ticker] = rec.get("result", "void")
                    settled.append(rec)
                    continue
                else:
                    ticker_result_cache[ticker] = None  # not resolved

            # ── Auto-void stale positions ──────────────────────────────
            # Use game_time (actual game start) when available; this
            # prevents voiding positions whose game hasn't started yet.
            # Fall back to placement time with a longer window.
            game_time_str = entry.get("game_time", "")
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
                    log.warning(
                        "settler_auto_void_stale",
                        ticker=ticker,
                        hours_since=round(hours_since, 1),
                        ref=ref_label,
                        ref_time=game_time_str or ts_str,
                        threshold=threshold,
                    )
                    rec = self._settle_position(ticker, entry, "void")
                    settled.append(rec)
            except (ValueError, TypeError):
                pass

        log.info(
            "settler_cycle_done",
            checked=len(open_entries),
            settled=len(settled),
        )
        return settled

    # ── Internal ──────────────────────────────────────────────────────────────

    def _open_placed_entries(self) -> Dict[str, Dict[str, Any]]:
        """
        Scan trade_log.jsonl and return unresolved PLACED entries.

        Resolution is tracked by *fingerprint* — each trade has a unique
        fingerprint, and a settlement record resolves only the specific
        trade it was written for.  This supports multiple pods (or the
        same pod) placing trades on the same market/ticker.

        For legacy entries without fingerprints, falls back to
        ticker-based resolution.

        Returns
        -------
        dict[fingerprint_or_ticker, placed_record]
            Keyed by fingerprint when available, ticker otherwise.
        """
        if not self._log_path.exists():
            return {}

        placed: Dict[str, Dict[str, Any]]  = {}
        resolved_fingerprints: set[str]    = set()
        resolved_tickers: set[str]         = set()

        try:
            lines = self._log_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            log.warning("settler_log_read_error", error=str(exc))
            return {}

        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action  = (rec.get("action")  or "").upper()
            outcome = (rec.get("outcome") or "").upper()
            ticker  = rec.get("market_ticker") or rec.get("market_id") or ""
            fp      = rec.get("fingerprint", "")

            # A BANKROLL_RESET wipes the slate: positions before the reset
            # should not be re-opened or re-checked.
            if action == "BANKROLL_RESET":
                placed.clear()
                resolved_fingerprints.clear()
                resolved_tickers.clear()
                continue

            if not ticker:
                continue

            if action == "PLACED":
                size = float(rec.get("position_size_usd") or 0)
                if size > 0 and ticker:
                    # Key by fingerprint (unique per trade) when available,
                    # fall back to ticker for legacy entries.
                    key = fp if fp else ticker
                    placed[key] = rec

            if outcome in ("WIN", "LOSS", "VOID"):
                if fp:
                    resolved_fingerprints.add(fp)
                else:
                    # Legacy: no fingerprint, resolve by ticker
                    resolved_tickers.add(ticker)

        result = {}
        for key, rec in placed.items():
            fp = rec.get("fingerprint", "")
            ticker = rec.get("market_ticker") or rec.get("market_id") or ""
            # Check if resolved by fingerprint or by legacy ticker match
            if fp and fp in resolved_fingerprints:
                continue
            if not fp and ticker in resolved_tickers:
                continue
            result[key] = rec
        return result

    def _check_and_settle(
        self,
        ticker: str,
        placed_entry: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Check if a game has completed and settle the position.

        Resolution order:
          1. Odds API /scores — check if the real-world game has completed
             and determine winner from actual scores.
          2. Kalshi API get_market — fallback for non-sports or when the
             Odds API doesn't have the result.

        Parameters
        ----------
        ticker : str
            Kalshi market ticker.
        placed_entry : dict
            The original PLACED record from the trade log.

        Returns
        -------
        Settlement record dict if the game has completed, else None.
        """
        # ── 0. Non-Kalshi venues (Polymarket, etc.) ──────────────────────
        # These can't be resolved via Kalshi API — fall through to
        # auto-void in the caller's stale-position check.
        venue = (placed_entry.get("venue") or "").lower()
        is_kalshi = venue == "kalshi" or venue == "" or ticker.startswith("KX")
        if not is_kalshi:
            return None

        # ── 1. Try Odds API scores (primary for sports) ──────────────────
        sport = placed_entry.get("sport", "")
        if not sport:
            # P-014 stores sport inside extra or market_type ("live_baseball_mlb")
            extra = placed_entry.get("extra") or {}
            sport = extra.get("sport", "")
        if not sport:
            mtype = placed_entry.get("market_type", "")
            if mtype.startswith("live_"):
                sport = mtype[5:]  # "live_baseball_mlb" → "baseball_mlb"
        if sport and self._odds_api_key:
            scores_result = self._check_scores(placed_entry, sport)
            if scores_result is not None:
                return self._settle_position(ticker, placed_entry, scores_result)

        # ── 2. Kalshi API fallback ───────────────────────────────────────
        market = None
        try:
            data   = self._kalshi.get_market(ticker)
            market = data.get("market") or data
            log.info("settler_api_raw", ticker=ticker,
                     status=market.get("status"), result=market.get("result"))
        except KalshiAPIError as exc:
            log.info("settler_api_error", ticker=ticker,
                     status=getattr(exc, "status_code", "?"), error=str(exc))
        except Exception as exc:
            log.warning("settler_fetch_error", ticker=ticker, error=str(exc))

        status = (market.get("status") or "").lower() if market else ""

        if status not in ("settled", "finalized"):
            log.info("settler_not_settled", ticker=ticker, status=status,
                     result=(market.get("result", "") if market else ""))
            return None

        kalshi_result = (market.get("result") or "").lower()
        if not kalshi_result:
            log.warning("settler_no_result", ticker=ticker, market_status=status)
            return None

        return self._settle_position(ticker, placed_entry, kalshi_result)

    def _check_scores(
        self,
        placed_entry: Dict[str, Any],
        sport: str,
    ) -> Optional[str]:
        """
        Check The Odds API /scores endpoint for a completed game result.

        Returns
        -------
        ``"yes"`` or ``"no"`` if the game is completed and winner determined,
        ``None`` if the game is not yet completed or cannot be found.
        """
        # Parse home/away from event string.
        # Supports multiple formats:
        #   Legacy:  "Home Team vs Away Team"
        #   P-014:   "Away Team @ Home Team (LIVE)"
        event = placed_entry.get("event", "")

        # Try "Away @ Home (LIVE)" format first (P-014 live games)
        at_match = re.match(r"^(.+?)\s+@\s+(.+?)(?:\s*\(LIVE\))?\s*$", event)
        if at_match:
            away_team = at_match.group(1).strip()
            home_team = at_match.group(2).strip()
        else:
            # Legacy "Home vs Away" format
            parts = re.split(r"\s+vs\.?\s+", event, maxsplit=1)
            if len(parts) != 2:
                return None
            home_team = parts[0].strip()
            away_team = parts[1].strip()

        yes_side = placed_entry.get("yes_side", "")
        if not yes_side:
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
                # Try reversed (event string might be away vs home)
                home_match_r = _fuzzy_team_match(home_team, g_away) > 0.6
                away_match_r = _fuzzy_team_match(away_team, g_home) > 0.6
                if not (home_match_r and away_match_r):
                    continue

            # Also check game time if available to avoid matching wrong game
            game_time_str = placed_entry.get("game_time", "")
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
                log.info("settler_scores_tie", game_event=event,
                         home_score=home_score, away_score=away_score)
                return None

            home_won = home_score > away_score

            # Map winner to YES/NO result
            # yes_side tells us which team YES resolves to
            if yes_side == "home":
                result = "yes" if home_won else "no"
            elif yes_side == "away":
                result = "yes" if not home_won else "no"
            else:
                return None

            log.info(
                "settler_scores_resolved",
                game_event=event,
                home_team=g_home, away_team=g_away,
                home_score=home_score, away_score=away_score,
                yes_side=yes_side, result=result,
            )
            return result

        return None

    def _fetch_scores(self, sport: str) -> List[Dict]:
        """
        Fetch completed game scores from The Odds API.

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
            log.info("settler_scores_fetched", sport=sport,
                     total=len(self._scores_cache[sport]),
                     completed=completed_count)
            return self._scores_cache[sport]
        except (_urlerr.URLError, OSError, json.JSONDecodeError) as exc:
            log.warning("settler_scores_fetch_error", sport=sport,
                        error=str(exc))
            self._scores_cache[sport] = []
            return []

    def _settle_position(
        self,
        ticker: str,
        placed_entry: Dict[str, Any],
        result: str,
    ) -> Dict[str, Any]:
        """
        Write a settlement record for a resolved position.

        Parameters
        ----------
        ticker : str
            Market ticker.
        placed_entry : dict
            Original PLACED record.
        result : str
            ``"yes"``, ``"no"``, or ``"void"`` — the market result.

        Returns
        -------
        Settlement record dict.
        """
        side        = placed_entry.get("side", "YES")
        size_usd    = float(placed_entry.get("position_size_usd") or 0)
        kalshi_prob = float(
            placed_entry.get("kalshi_prob")
            or placed_entry.get("venue_prob")
            or 0.5
        )

        outcome, pnl_usd = _calc_outcome_pnl(side, result, size_usd, kalshi_prob)

        # Close in-memory ledger position (best-effort)
        closed = self._ledger.close_by_ticker(ticker, pnl_usd)
        if closed is None:
            log.debug(
                "settler_ledger_miss", ticker=ticker,
                hint="position not found in ledger; bankroll may be stale",
            )

        now = self._now()
        record: Dict[str, Any] = {
            **placed_entry,
            "action":         outcome,      # WIN | LOSS | VOID
            "outcome":        outcome,      # mirrors action for dashboard compat
            "result":         result,       # "yes" | "no" | "void"
            "pnl_usd":        pnl_usd,
            "settled_at_utc": now.isoformat(),
            "timestamp_utc":  now.isoformat(),   # settlement date for daily P&L
        }
        self._append_log(record)

        log.info(
            "settler_settled",
            ticker=ticker,
            side=side,
            result=result,
            outcome=outcome,
            pnl_usd=pnl_usd,
        )
        return record

    def _append_log(self, record: Dict[str, Any]) -> None:
        """Append one JSON line to trade_log.jsonl."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.error(
                "settler_log_write_error",
                path=str(self._log_path),
                error=str(exc),
            )
