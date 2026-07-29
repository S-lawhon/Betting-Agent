"""
src/engine.py
─────────────
Engine construction and runtime loop.

Builds venue clients, shared dependencies, and the cycle callback.
Also contains the guarded loop that drives continuous scanning and
the trade log reading utilities used by the web dashboard.
"""

import difflib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.aggregate_risk import AggregateRiskGuard
from src.capital_allocator import CapitalAllocator
from src.dashboard import DashboardRenderer
from src.forecastex_client import ForecastExClient
from src.nowcast_client import NowcastClient
from src.pod_runner import CycleReport, PodRunner
from src.polymarket_client import PolymarketClient
from src.trade_store import TradeStore
from src.web_dashboard import WebDashboardServer

logger = logging.getLogger(__name__)

# ── Ensure legacy modules are importable ──────────────────────────────
# The Scanner, Settler, KalshiClient, EdgeCalculator, etc. live under
# Legacy/Kalshi Arb Project/src/ and need to be on sys.path for bare
# ``from scanner import Scanner`` imports to resolve.
import sys as _sys
_LEGACY_SRC = str(Path(__file__).resolve().parent.parent / "Legacy" / "Kalshi Arb Project" / "src")
if _LEGACY_SRC not in _sys.path:
    _sys.path.insert(0, _LEGACY_SRC)
    logger.debug("engine: added legacy path to sys.path: %s", _LEGACY_SRC)


# ── Component construction ───────────────────────────────────────────

def build_venue_clients(
    config: Dict[str, Any],
    mode_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Build venue clients from config.

    Handles missing optional dependencies (py_clob_client, IB TWS) gracefully
    — a missing client produces a warning log, not a crash.

    Args:
        config:        Merged config dict.
        mode_override: If set ("paper" or "live"), overrides per-venue env.

    Returns:
        Dict mapping venue name → client instance.
    """
    if mode_override:
        for section in ("polymarket", "interactive_brokers"):
            if section in config:
                config = dict(config)
                config[section] = dict(config[section])
                config[section]["environment"] = mode_override

    clients: Dict[str, Any] = {}

    # Polymarket
    try:
        clients["polymarket"] = PolymarketClient.from_config(config)
    except Exception as exc:
        logger.warning("build_venue_clients: Polymarket unavailable — %s", exc)

    # ForecastEx (Interactive Brokers)
    try:
        clients["forecastex"] = ForecastExClient.from_config(config)
    except Exception as exc:
        logger.warning("build_venue_clients: ForecastEx unavailable — %s", exc)

    # Kalshi (existing system module, optional)
    try:
        from kalshi_client import KalshiClient  # type: ignore  # existing system
        api_key_id  = os.environ.get("KALSHI_API_KEY_ID",  "")
        private_key = os.environ.get("KALSHI_PRIVATE_KEY", "").strip()
        email       = os.environ.get("KALSHI_EMAIL",       "")
        password    = os.environ.get("KALSHI_PASSWORD",    "")
        clients["kalshi"] = KalshiClient.from_config(
            config,
            api_key_id=api_key_id,
            private_key=private_key,
            email=email,
            password=password,
        )
        logger.info("build_venue_clients: Kalshi client loaded")
    except ImportError:
        logger.info("build_venue_clients: kalshi_client not found in existing system")
    except Exception as exc:
        logger.warning("build_venue_clients: Kalshi unavailable — %s", exc)

    return clients


# Settlers that resolve a single pod expose it as ``_pod_id``.  The
# generic Kalshi Settler predates that convention and filters on an
# explicit pod tuple, so it is mapped here.
#
# P-014 is in this tuple because it has no settler of its own and never has
# had one — it was resolved incidentally by this settler back when
# ``_open_placed_entries`` walked every pod's rows.  Scoping that walk
# (3b0daae, 2026-07-26) fixed the P-017 spurious-void bug and silently
# orphaned P-014 in the same stroke: 15 positions, zero settlements in the
# two days that followed.  Adding it back is safe in a way it is NOT for
# P-017 — the settler's ``result or "void"`` rule is roughly correct for
# KXMLBGAME, which resolves promptly, whereas Kalshi leaves golf top-N
# markets ``active`` with an empty result for ~a day post-tournament.
#
# P-014 carries ``game_time``, so its stale threshold is the 8h
# STALE_HOURS_AFTER_GAME one.  Any backlog MUST be booked before this pod
# is added, or the first cycle auto-voids it at $0.00 — see
# ``scripts/backfill_p014_settlements.py``.
_SETTLER_DEP_PODS: Dict[str, tuple] = {
    "settler": ("P-001", "P-014", ""),
}


def _settled_pod_ids(deps: Dict[str, Any]) -> set:
    """Pod IDs with a live settler among the built dependencies.

    AggregateRiskGuard uses this to decide whose paper positions to track
    on bootstrap: a pod that settles will have close_position called for
    it, so its open paper exposure is real and should count against the
    limits.  A pod with no settler would accumulate forever.
    """
    settled: set = set()
    for name, inst in deps.items():
        if inst is None or not name.endswith("settler"):
            continue
        explicit = _SETTLER_DEP_PODS.get(name)
        if explicit is not None:
            settled.update(explicit)
            continue
        pod_id = getattr(inst, "_pod_id", None) or getattr(inst, "pod_id", None)
        if pod_id:
            settled.add(pod_id)
    return settled


def build_shared_deps(
    config: Dict[str, Any],
    clients: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build shared infrastructure components.

    Attempts to import EdgeCalculator, RiskManager, and Scanner from the
    existing system.  Falls back gracefully when unavailable (individual
    pods create their own instances via from_config() in that case).

    Returns:
        Dict of shared dependency objects.
    """
    deps: Dict[str, Any] = {}

    # Scanner for P-001
    try:
        from scanner        import Scanner        # type: ignore  # existing system
        from odds_client    import OddsClient     # type: ignore
        from matcher        import Matcher        # type: ignore
        from edge_calculator import EdgeCalculator  # type: ignore
        from risk_manager   import RiskManager, Ledger  # type: ignore

        from src.constants import DEFAULT_BANKROLL
        odds_key     = os.environ.get("ODDS_API_KEY", "")
        odds_inst    = OddsClient.from_config(config, api_key=odds_key)
        matcher_inst = Matcher.from_config(config)
        edge_inst    = EdgeCalculator.from_config(config)
        bankroll     = float(config.get("risk", {}).get("initial_bankroll", DEFAULT_BANKROLL))
        ledger_inst  = Ledger(initial_bankroll=bankroll)
        risk_inst    = RiskManager.from_config(config, ledger_inst)

        kalshi_client = (clients or {}).get("kalshi")
        deps["scanner"] = Scanner.from_config(
            config, kalshi_client, odds_inst, matcher_inst, edge_inst, risk_inst
        )
        deps["odds_client"]     = odds_inst
        deps["edge_calculator"] = edge_inst
        deps["risk_manager"]    = risk_inst

        # Build Settler for automatic Kalshi settlement polling
        if kalshi_client:
            try:
                from settler import Settler  # type: ignore
                # The SAME filter must reach settle_cycle(), not just the
                # ledger rebuild below.  This settler runs first in the cycle
                # and applies `result or "void"`; unscoped, it voids positions
                # belonging to pods with a purpose-built settler that exists
                # precisely because that rule is wrong for them.  On
                # 2026-07-26 it voided 16 P-017 golf positions at $0.00
                # seconds after a restart, because Kalshi leaves top-N markets
                # `active` with an empty result for ~a day post-tournament.
                settler_inst = Settler.from_config(
                    config, kalshi_client, ledger_inst,
                    pod_ids=_SETTLER_DEP_PODS["settler"],
                )
                # Only load P-001 and legacy (no pod_id) positions into
                # the Scanner's Ledger.  Without this filter, positions
                # from P-002, P-014, etc. inflate the open-position count
                # and cause the RiskManager to block P-001 trades via
                # MAX_POSITIONS even though those positions belong to
                # other pods with their own risk management.
                settler_inst.rebuild_ledger_from_log(pod_ids=_SETTLER_DEP_PODS["settler"])
                deps["settler"] = settler_inst
                logger.info("build_shared_deps: Settler loaded — will poll Kalshi for resolved markets")
            except ImportError:
                logger.info("build_shared_deps: Settler module not available; auto-settlement disabled")
            except Exception as exc:
                logger.warning("build_shared_deps: Settler build failed: %s", exc)

        logger.info("build_shared_deps: Scanner + dependencies loaded (P-001 ready)")
    except ImportError as exc:
        logger.info("build_shared_deps: legacy scanner not available; P-001 skipped (%s)", exc)
    except Exception as exc:
        logger.warning("build_shared_deps: Scanner build failed: %s", exc)

    # Build EloModel for P-006 fair value + auto-update on settlement
    elo_model = None
    try:
        from src.elo_model import EloModel
        elo_model = EloModel.from_config(config)
        deps["elo_model"] = elo_model
        logger.info("build_shared_deps: EloModel loaded — will auto-update on settlement")
    except ImportError:
        logger.info("build_shared_deps: EloModel module not available")
    except Exception as exc:
        logger.warning("build_shared_deps: EloModel build failed: %s", exc)

    # Build PolymarketSettler for automatic P-006 settlement polling
    # Now uses Odds API /scores as primary (fast) + Gamma API as fallback (slow)
    poly_client = (clients or {}).get("polymarket")
    if poly_client:
        try:
            from src.polymarket_settler import PolymarketSettler
            poly_settler = PolymarketSettler.from_config(
                config, poly_client, elo_model=elo_model,
            )
            deps["polymarket_settler"] = poly_settler
            logger.info(
                "build_shared_deps: PolymarketSettler loaded — "
                "Odds API scores (primary) + Gamma API (fallback)%s",
                " + Elo auto-update" if elo_model else "",
            )
        except ImportError:
            logger.info("build_shared_deps: PolymarketSettler module not available")
        except Exception as exc:
            logger.warning("build_shared_deps: PolymarketSettler build failed: %s", exc)

    # Build KalshiTennisSettler for automatic P-015 settlement polling
    # (public Kalshi API — production tennis markets settle normally)
    if "P-015" in (config.get("pods", {}).get("active") or []):
        try:
            from src.kalshi_tennis_settler import KalshiTennisSettler
            deps["tennis_settler"] = KalshiTennisSettler.from_config(config)
            logger.info(
                "build_shared_deps: KalshiTennisSettler loaded — "
                "will poll Kalshi public API for P-015 results"
            )
        except ImportError:
            logger.info("build_shared_deps: KalshiTennisSettler module not available")
        except Exception as exc:
            logger.warning("build_shared_deps: KalshiTennisSettler build failed: %s", exc)

    # Build KalshiGolfSettler for automatic P-017 settlement polling.
    # Without this, P-017 positions never resolve: no P&L, no CLV, and
    # the pod's open-position counter never releases — so it goes silent
    # after its first tournament fills the cap.
    if "P-017" in (config.get("pods", {}).get("active") or []):
        try:
            from src.kalshi_golf_settler import KalshiGolfSettler
            deps["golf_settler"] = KalshiGolfSettler.from_config(config)
            logger.info(
                "build_shared_deps: KalshiGolfSettler loaded — "
                "will poll Kalshi public API for P-017 results"
            )
        except ImportError:
            logger.info("build_shared_deps: KalshiGolfSettler module not available")
        except Exception as exc:
            logger.warning("build_shared_deps: KalshiGolfSettler build failed: %s", exc)

    # Record which pods can actually settle, so the risk guard knows
    # whose paper positions will drain (see settled_pod_ids below).
    deps["settled_pod_ids"] = _settled_pod_ids(deps)

    # CrossVenueMatcher for P-002
    try:
        from src.cross_venue_matcher import CrossVenueMatcher
        deps["cross_venue_matcher"] = CrossVenueMatcher.from_config(config)
        logger.info("build_shared_deps: CrossVenueMatcher loaded (P-002 ready)")
    except Exception as exc:
        logger.info("build_shared_deps: CrossVenueMatcher unavailable — %s", exc)

    # NowcastClient
    fred_key = (
        os.environ.get("FRED_API_KEY")
        or config.get("nowcast", {}).get("fred_api_key")
    )
    try:
        from src.nowcast_http import CompositeNowcastAdapter  # type: ignore
        adapter = CompositeNowcastAdapter.from_config(config)
        deps["nowcast_client"] = NowcastClient(
            adapter=adapter,
            fred_api_key=fred_key,
        )
        logger.info(
            "build_shared_deps: NowcastClient loaded with HTTP adapters%s",
            " (FRED key set)" if fred_key else " (no FRED key — payrolls disabled)",
        )
    except Exception:
        deps["nowcast_client"] = NowcastClient.from_config(config)
        logger.info("build_shared_deps: NowcastClient using stubs (no HTTP adapters)")

    return deps


# ── Trade log readers (fallback when TradeStore unavailable) ─────────

def _compute_settlement_summary(
    log_path: Path,
    trades: Optional[List[Dict]] = None,
) -> Optional[Dict]:
    """Aggregate WIN/LOSS/VOID records from trade_log.jsonl AND settlements.jsonl.

    Reads settlement outcomes from both the main trade log (which may contain
    inline WIN/LOSS/VOID entries) and the settlement bridge log (settlements.jsonl)
    in the same directory.  Deduplicates by fingerprint so the same settlement
    is never counted twice.

    If *trades* is provided, the void count is derived from it instead of from
    the settlement records.

    Returns a dict compatible with DashboardState.settlement_summary or None.
    """
    if not log_path or not log_path.exists():
        return None
    try:
        seen_fps: dict = {}

        # 1. Read from main trade log (inline settlement entries)
        with open(log_path, "r") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                outcome = (rec.get("outcome") or "").upper()
                if outcome not in ("WIN", "LOSS", "VOID"):
                    continue
                fp = rec.get("fingerprint") or rec.get("market_ticker") or ""
                pnl = float(rec.get("pnl_usd", 0) or rec.get("pnl", 0) or 0)
                pid = rec.get("pod_id", "P-001")
                if fp:
                    seen_fps[fp] = {"outcome": outcome, "pnl": pnl, "pod_id": pid}

        # 2. Read from settlements.jsonl
        settlement_log = log_path.parent / "settlements.jsonl"
        if settlement_log.exists():
            with open(settlement_log, "r") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    fp = rec.get("fingerprint", "")
                    outcome = rec.get("outcome", "").upper()
                    pnl = float(rec.get("pnl", 0))
                    pid = rec.get("pod_id", "P-001")
                    if fp and outcome in ("WIN", "LOSS", "VOID"):
                        seen_fps[fp] = {"outcome": outcome, "pnl": pnl, "pod_id": pid}

        if not seen_fps:
            return None

        total_pnl = 0.0
        wins = 0
        losses = 0
        settlement_voids = 0
        per_pod: dict = {}
        for info in seen_fps.values():
            outcome = info["outcome"]
            pnl = info["pnl"]
            pid = info["pod_id"]
            if outcome == "WIN":
                wins += 1
                total_pnl += pnl
            elif outcome == "LOSS":
                losses += 1
                total_pnl += pnl
            elif outcome == "VOID":
                settlement_voids += 1
            per_pod[pid] = per_pod.get(pid, 0.0) + pnl

        if trades is not None:
            voids = sum(1 for t in trades if t.get("status") == "VOID")
        else:
            voids = settlement_voids

        total_settled = wins + losses + voids
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0.0
        return {
            "total_pnl": total_pnl,
            "total_settled": total_settled,
            "wins": wins,
            "losses": losses,
            "voids": voids,
            "win_rate": win_rate,
            "per_pod": {k: round(v, 2) for k, v in per_pod.items()},
        }
    except Exception:
        return None


def _resolve_bet_team(entry: Dict) -> str:
    """Determine which team/outcome is actually being bet on.

    Resolution order:
      1. ``yes_side`` field (``"home"`` / ``"away"``) set by P-001 scanner.
      2. ``extra.yes_team`` set by P-006 (actual team name).
      3. Ticker-suffix heuristic — e.g. ``...-DUC`` → fuzzy match to "Ducks".

    For events formatted as "Home Team vs. Away Team":
      - Identify which team YES resolves to (the "yes team").
      - If side == "YES", return the yes team.
      - If side == "NO",  return the *other* team.

    Falls back to raw "YES"/"NO" when nothing can be inferred.
    """
    side = (entry.get("side") or "").upper()
    yes_side = entry.get("yes_side", "")
    extra = entry.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}

    if not yes_side:
        yes_side = extra.get("yes_side", "") or extra.get("yes_team", "")

    # ── Fast path: if extra contains actual team names, use them directly ──
    extra_home = extra.get("home_team", "")
    extra_away = extra.get("away_team", "")
    if extra_home and extra_away and yes_side:
        if yes_side == "home":
            yes_team_name, no_team_name = extra_home, extra_away
        elif yes_side == "away":
            yes_team_name, no_team_name = extra_away, extra_home
        else:
            if yes_side.lower() in extra_home.lower():
                yes_team_name, no_team_name = extra_home, extra_away
            elif yes_side.lower() in extra_away.lower():
                yes_team_name, no_team_name = extra_away, extra_home
            else:
                yes_team_name, no_team_name = yes_side, ""

        if side == "YES":
            return yes_team_name
        elif side == "NO" and no_team_name:
            return no_team_name
        return side

    event = entry.get("event") or ""
    parts = re.split(r"\s+vs\.?\s+", event, maxsplit=1)

    if len(parts) != 2:
        if yes_side and yes_side not in ("home", "away") and side == "YES":
            return yes_side
        return side

    home_team = parts[0].strip()
    away_team = parts[1].strip()

    yes_team = None
    no_team = None

    if yes_side == "home":
        yes_team, no_team = home_team, away_team
    elif yes_side == "away":
        yes_team, no_team = away_team, home_team
    elif yes_side:
        yes_team = yes_side
        if yes_side.lower() in home_team.lower():
            no_team = away_team
        elif yes_side.lower() in away_team.lower():
            no_team = home_team
    else:
        # ── Fallback: infer from ticker suffix ────────────────────────────
        ticker = entry.get("market_ticker") or ""
        if "-" in ticker:
            suffix = ticker.rsplit("-", 1)[-1].upper()
            if suffix and suffix not in ("TIE", "ML"):
                def _match_score(abbr: str, team: str) -> float:
                    return difflib.SequenceMatcher(
                        None, abbr.lower(), team.lower()
                    ).ratio()
                home_score = _match_score(suffix, home_team)
                away_score = _match_score(suffix, away_team)
                for word in home_team.split():
                    if word.upper().startswith(suffix[:3]):
                        home_score += 0.3
                for word in away_team.split():
                    if word.upper().startswith(suffix[:3]):
                        away_score += 0.3
                if home_score > away_score and home_score >= 0.3:
                    yes_team, no_team = home_team, away_team
                elif away_score > home_score and away_score >= 0.3:
                    yes_team, no_team = away_team, home_team

    if yes_team is None:
        return side

    if side == "YES":
        return yes_team
    elif side == "NO" and no_team:
        return no_team

    return side


def _read_recent_placed_trades(log_path: Path, max_trades: int = 2000) -> List[Dict]:
    """Read the most recent PLACED trades from the JSONL trade log.

    Each returned entry gains a ``status`` field:
      - ``"OPEN"``    — no WIN / LOSS / VOID record exists for this trade
      - ``"WIN"``     — market settled in our favour
      - ``"LOSS"``    — market settled against us
      - ``"VOID"``    — market voided / cancelled

    Each entry also gains a ``bet_team`` field that resolves YES/NO to the
    actual team name using the ``yes_side`` field from the scanner.

    Returns newest-first, capped at *max_trades*.
    Silently returns [] if the log doesn't exist or is unreadable.
    """
    if not log_path.exists():
        return []
    try:
        with open(log_path, "r") as fh:
            lines = fh.readlines()

        # First pass: collect settlement outcomes
        resolved_by_fp: Dict[str, str] = {}
        resolved_pnl_fp: Dict[str, float] = {}
        resolved_at_fp: Dict[str, str] = {}
        resolved_by_ticker: Dict[str, str] = {}
        resolved_pnl_ticker: Dict[str, float] = {}
        resolved_at_ticker: Dict[str, str] = {}

        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            outcome = (rec.get("outcome") or rec.get("action") or "").upper()
            if outcome in ("WIN", "LOSS", "VOID"):
                fp = rec.get("fingerprint", "")
                ticker = rec.get("market_ticker") or rec.get("market_id", "")
                pnl = float(rec.get("pnl_usd", 0) or rec.get("pnl", 0) or 0)
                ts = rec.get("settled_at_utc") or rec.get("timestamp_utc", "")
                if fp:
                    resolved_by_fp[fp] = outcome
                    resolved_pnl_fp[fp] = pnl
                    resolved_at_fp[fp] = ts
                if ticker:
                    resolved_by_ticker[ticker] = outcome
                    resolved_pnl_ticker[ticker] = pnl
                    resolved_at_ticker[ticker] = ts

        # Second pass: collect PLACED entries, annotated with status
        placed: List[Dict] = []
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if entry.get("action") != "PLACED":
                continue
            if "pod_id" not in entry:
                entry["pod_id"] = "P-001"
            if "pod_name" not in entry:
                entry["pod_name"] = "Kalshi vs Sharp's Strategy"

            fp = entry.get("fingerprint", "")
            ticker = entry.get("market_ticker") or entry.get("market_id", "")

            if fp and fp in resolved_by_fp:
                entry["status"] = resolved_by_fp[fp]
                entry["pnl_usd"] = resolved_pnl_fp[fp]
                entry["settled_at_utc"] = resolved_at_fp[fp]
            elif ticker and ticker in resolved_by_ticker:
                entry["status"] = resolved_by_ticker[ticker]
                entry["pnl_usd"] = resolved_pnl_ticker[ticker]
                entry["settled_at_utc"] = resolved_at_ticker[ticker]
            else:
                entry["status"] = "OPEN"
            entry["bet_team"] = _resolve_bet_team(entry)
            if not entry.get("game_time"):
                extra = entry.get("extra") or {}
                if isinstance(extra, dict) and extra.get("game_time"):
                    entry["game_time"] = extra["game_time"]
            placed.append(entry)

        return list(reversed(placed[-max_trades:]))
    except Exception as exc:
        logger.debug("_read_recent_placed_trades: error reading %s: %s", log_path, exc)
        return []


# ── Cycle callback ───────────────────────────────────────────────────

def make_cycle_callback(
    guard: AggregateRiskGuard,
    verbose: bool = False,
    renderer: Optional["DashboardRenderer"] = None,
    allocator: Optional["CapitalAllocator"] = None,
    web_server: Optional["WebDashboardServer"] = None,
    trade_log_path: Optional[Path] = None,
    trade_store: Optional["TradeStore"] = None,
) -> Callable[[CycleReport], None]:
    """Create a callback that updates the aggregate risk guard and logs status.

    Args:
        guard:           AggregateRiskGuard to update after each cycle.
        verbose:         If True, log individual trade details at DEBUG level.
        renderer:        Optional terminal DashboardRenderer.
        allocator:       Optional CapitalAllocator.
        web_server:      Optional WebDashboardServer.
        trade_log_path:  Path to the JSONL trade log (fallback when no store).
        trade_store:     Optional TradeStore for in-memory dashboard data.

    Returns:
        Callback function matching the PodRunner callback signature.
    """
    cb_logger = logging.getLogger("main.cycle")

    def _callback(report: CycleReport) -> None:
        guard.update_post_cycle(report)

        snap = guard.snapshot()
        cb_logger.info(
            "cycle=%d pods=%d placed=%d skipped=%d errors=%d "
            "duration=%.2fs | exposure=%.1f%% daily_pnl=%.2f%s",
            report.cycle_number,
            report.pods_scanned,
            report.placed_count,
            report.skipped_count,
            report.error_count,
            report.duration_seconds,
            snap.total_exposure_pct * 100,
            snap.daily_pnl,
            " [HALTED]" if snap.halted else "",
        )

        if verbose:
            for result in report.results:
                if not result.skipped:
                    cb_logger.debug(
                        "  %s %s %s fill=%.4f size=%.2f",
                        result.pod_id,
                        result.market_id,
                        getattr(result, "action", "?"),
                        result.fill_price or 0.0,
                        result.fill_price or 0.0,
                    )

        if renderer is not None:
            try:
                print(renderer.clear_and_render(snapshot=snap, report=report))
            except Exception as exc:
                cb_logger.debug("dashboard render failed: %s", exc)

        if web_server is not None:
            try:
                perfs  = allocator.performances if allocator else None
                allocs = allocator.allocations  if allocator else None

                if trade_store is not None:
                    trades = trade_store.get_recent_placed_with_status()
                    for t in trades:
                        t["bet_team"] = _resolve_bet_team(t)
                        if not t.get("game_time"):
                            extra = t.get("extra") or {}
                            if isinstance(extra, dict) and extra.get("game_time"):
                                t["game_time"] = extra["game_time"]
                    settlement = trade_store.compute_settlement_summary(trades)
                else:
                    trades = (
                        _read_recent_placed_trades(trade_log_path)
                        if trade_log_path else []
                    )
                    settlement = _compute_settlement_summary(
                        trade_log_path, trades=trades
                    )

                web_server.update(
                    snapshot=snap,
                    report=report,
                    performances=perfs,
                    allocations=allocs,
                    trades=trades,
                    settlement_summary=settlement,
                )
            except Exception as exc:
                cb_logger.debug("web dashboard update failed: %s", exc)

    return _callback


# ── Main loop logic ──────────────────────────────────────────────────

def _run_guarded_loop(
    runner: PodRunner,
    guard: AggregateRiskGuard,
    interval: float,
    max_cycles: Optional[int],
    settler=None,
    polymarket_settler=None,
    tennis_settler=None,
    golf_settler=None,
    allocator: Optional["CapitalAllocator"] = None,
    trade_store: Optional["TradeStore"] = None,
    settlement_interval_cycles: int = 1,
) -> List[CycleReport]:
    """Run scan cycles in a guarded loop.

    Guards each cycle with check_pre_cycle().  If the guard halts, waits
    one interval and tries again on the next iteration.

    Args:
        settlement_interval_cycles:
            Run the settlement pass every Nth cycle.  1 = every cycle (the
            historical behaviour, and the default so nothing changes for a
            caller that does not set it).

            WHY THIS EXISTS.  All four settlers used to poll on EVERY cycle.
            With a 5-minute interval and ~115 open positions that is ~1,380
            Kalshi requests/hour in bursts, and it was measured (2026-07-21)
            to be the dominant source of HTTP 429s on the droplet — every
            429 cluster fell in a settler-pass minute or the one after it,
            and none fell anywhere else.

            Nothing in the book can resolve on a 5-minute boundary: golf
            Top-N sits `closed` with an empty `result` for ~a day
            (see CLAUDE.md), and tennis/MLB resolve on hour scales.  So ~11
            of every 12 passes per hour were guaranteed to find nothing.

            TRADE-OFF: settlement latency rises to N*interval, and open
            positions stay counted against pod caps for that much longer
            (P-017 derives its cap from the TradeStore).  Keep N*interval
            well below the fastest real settlement time.
    """
    reports: List[CycleReport] = []
    iteration = 0
    settle_every = max(1, int(settlement_interval_cycles or 1))

    while max_cycles is None or iteration < max_cycles:
        cycle_start = time.monotonic()
        iteration += 1

        # Always settle on the first pass so a restart reconciles immediately,
        # then every Nth cycle thereafter.
        do_settle = (iteration - 1) % settle_every == 0
        if not do_settle:
            logger.debug(
                "settlement: skipped on cycle %d (every %d cycles)",
                iteration, settle_every,
            )

        # ── Settlement: poll venues for resolved markets BEFORE scanning ──
        if do_settle and settler is not None:
            try:
                settled = settler.settle_cycle()
                if settled:
                    logger.info(
                        "settlement.kalshi: %d positions resolved this cycle", len(settled),
                    )
                    for rec in settled:
                        pod_id = rec.get("pod_id", "P-001")
                        pnl = float(rec.get("pnl_usd", 0) or 0)
                        fp = rec.get("fingerprint", "")
                        market_id = rec.get("market_id") or rec.get("market_ticker", "")
                        if allocator is not None:
                            allocator.record_settlement(pod_id, pnl, fingerprint=fp)
                        # Sync settlement to aggregate risk guard so
                        # it stops counting this as an open position.
                        if market_id:
                            guard.close_position(market_id, pnl)
                        # Sync settlement to in-memory trade store so
                        # the dashboard sees resolved (not OPEN) status.
                        if trade_store is not None:
                            try:
                                trade_store.index_only(rec)
                            except Exception:
                                pass  # settler already persisted to disk
            except Exception as exc:
                logger.warning("settlement.kalshi: settle_cycle failed: %s", exc)

        if do_settle and polymarket_settler is not None:
            try:
                settled = polymarket_settler.settle_cycle()
                if settled:
                    logger.info(
                        "settlement.polymarket: %d positions resolved this cycle", len(settled),
                    )
                    for rec in settled:
                        pod_id = rec.get("pod_id", "P-006")
                        pnl = float(rec.get("pnl_usd", 0) or rec.get("pnl", 0) or 0)
                        fp = rec.get("fingerprint", "")
                        market_id = rec.get("market_id") or rec.get("market_ticker", "")
                        if allocator is not None:
                            allocator.record_settlement(pod_id, pnl, fingerprint=fp)
                        # Sync settlement to aggregate risk guard
                        if market_id:
                            guard.close_position(market_id, pnl)
                        # Sync settlement to in-memory trade store
                        if trade_store is not None:
                            try:
                                trade_store.index_only(rec)
                            except Exception:
                                pass
            except Exception as exc:
                logger.warning("settlement.polymarket: settle_cycle failed: %s", exc)

        if do_settle and tennis_settler is not None:
            try:
                settled = tennis_settler.settle_cycle()
                if settled:
                    logger.info(
                        "settlement.tennis: %d positions resolved this cycle",
                        len(settled),
                    )
                    for rec in settled:
                        pod_id = rec.get("pod_id", "P-015")
                        pnl = float(rec.get("pnl_usd", 0) or 0)
                        fp = rec.get("fingerprint", "")
                        market_id = rec.get("market_id", "")
                        if allocator is not None:
                            allocator.record_settlement(pod_id, pnl, fingerprint=fp)
                        if market_id:
                            guard.close_position(market_id, pnl)
                            # release the pod's open-position slot
                            for pod in getattr(runner, "pods", []) or []:
                                if getattr(pod, "pod_id", "") == pod_id and \
                                        hasattr(pod, "on_settlement"):
                                    try:
                                        pod.on_settlement(market_id)
                                    except Exception:
                                        pass
                        # NOTE: no trade_store.index_only() here — the
                        # tennis settler persists through
                        # trade_store.append(), which has already indexed
                        # the record. Indexing again would double it in
                        # the in-memory lists.
            except Exception as exc:
                logger.warning("settlement.tennis: settle_cycle failed: %s", exc)

        if do_settle and golf_settler is not None:
            try:
                settled = golf_settler.settle_cycle()
                if settled:
                    logger.info(
                        "settlement.golf: %d positions resolved this cycle",
                        len(settled),
                    )
                    for rec in settled:
                        pod_id = rec.get("pod_id", "P-017")
                        pnl = float(rec.get("pnl_usd", 0) or 0)
                        fp = rec.get("fingerprint", "")
                        market_id = rec.get("market_id", "")
                        if allocator is not None:
                            allocator.record_settlement(pod_id, pnl, fingerprint=fp)
                        if market_id:
                            guard.close_position(market_id, pnl)
                            # release the pod's open-position slot
                            for pod in getattr(runner, "pods", []) or []:
                                if getattr(pod, "pod_id", "") == pod_id and \
                                        hasattr(pod, "on_settlement"):
                                    try:
                                        pod.on_settlement(market_id)
                                    except Exception:
                                        pass
                        # NOTE: no trade_store.index_only() here — unlike
                        # the settlers above, the golf settler persists
                        # through trade_store.append(), which has already
                        # indexed the record. Indexing again would double
                        # it in the in-memory lists.
            except Exception as exc:
                logger.warning("settlement.golf: settle_cycle failed: %s", exc)

        if guard.check_pre_cycle():
            try:
                report = runner.run_once()
                reports.append(report)
            except Exception as exc:
                logger.error("_run_guarded_loop: cycle %d crashed: %s", iteration, exc)
        else:
            snap = guard.snapshot()
            logger.warning(
                "_run_guarded_loop: guard halted (%s), skipping cycle %d",
                snap.halt_reason, iteration,
            )

        # Ping systemd watchdog so WatchdogSec doesn't kill us
        try:
            from src.watchdog import notify_watchdog, notify_status
            notify_watchdog()
            notify_status("cycle {} done, {} placed".format(
                iteration, report.placed_count if guard.check_pre_cycle else 0,
            ))
        except Exception:
            pass  # watchdog is best-effort

        elapsed = time.monotonic() - cycle_start
        remaining = interval - elapsed
        if remaining > 0 and (max_cycles is None or iteration < max_cycles):
            time.sleep(remaining)

    return reports
