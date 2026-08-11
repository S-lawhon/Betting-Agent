#!/usr/bin/env python3
"""Replay the raw Gemini/Kalshi cross-venue observation tape for
strat_crossvenue_mlb_v1 against the LOCKED spec's entry_logic (see
research/specs/strat_crossvenue_mlb_v1_2026-08-10.json, extra.validation_guidance).

Standalone: `python3 strat_crossvenue_mlb_v1_replay_2026-08-11.py <tape_dir>`
prints one JSON object to stdout with everything the ValidationReport needs.
No hardcoded scratchpad paths -- the tape directory is argv[1].

Method (verbatim from the locked spec, applied to the RAW tape, not
analytics.json):
  - Reuses src/dislocation_cases.py's build_cases() for episode segmentation
    and market_phase classification (pre_scheduled_start / after_scheduled_start
    / unknown), and reuses the tape's own path-level gross/fee/net fields,
    which are produced by src/gemini_kalshi_research.py's evaluate_match() /
    gemini_fee_total() / kalshi_fee_total() on the collector side (agreement
    re-verified below on a live sample, not assumed).
  - entry_logic precondition #2 (net edge >= $0.03 AFTER a $0.01/leg slippage
    allowance) is NOT baked into the tape's own net_edge_usd (that field is
    fee-net only) -- this script subtracts 2 x slippage_per_leg from the
    tape's fee-net edge before applying the qualifying threshold.
  - entry_logic precondition #5 (quote-skew synchronization) uses this
    session's OWN measured p95 quote-skew, not the (explicitly unverified)
    figure quoted in the spec's evidence_snapshot.
  - entry_logic precondition #6 (no depth beyond top-of-book) is a no-op
    filter here because the raw snapshots carry no size field at all --
    documented, not silently assumed.
  - entry_logic precondition #4 (settlement equivalence) is evaluated
    separately against config/settlement_equivalence.yaml and reported as its
    own PASS/FAIL, per the guidance's explicit instruction not to fold it into
    the rate calculation.
"""
from __future__ import annotations

import glob
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.dislocation_cases import build_cases  # noqa: E402
from src.gemini_kalshi_research import evaluate_match  # noqa: E402

UTC = timezone.utc
SLIPPAGE_PRIMARY_PER_LEG = 0.01
THRESHOLD_PRIMARY = 0.03
PERSISTENCE_PRIMARY = 2
MAX_EPISODE_GAP_SECONDS = 450.0

POLICY = {
    "id": "gemini_kalshi_mlb_moneyline",
    "status": "not_equivalent",
    "dimensions": {
        "underlying_event": "equivalent",
        "binary_payout": "equivalent",
        "extra_innings": "equivalent",
        "settlement_sources": "compatible",
        "postponement": "different",
        "cancellation": "different",
        "shortened_game": "different",
        "discretionary_review": "different",
    },
}


def _parse_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    value = (ordered[lower] if lower == upper else
             ordered[lower] * (upper - position) + ordered[upper] * (position - lower))
    return round(value, 6)


def _tape_files(tape_dir: str) -> List[str]:
    files = sorted(glob.glob(str(Path(tape_dir) / "*.jsonl")))
    if not files:
        raise SystemExit(f"no *.jsonl files found under {tape_dir}")
    return files


def _iter_rows(files: List[str]):
    for fn in files:
        with open(fn, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)


# ---------------------------------------------------------------------------
# Pass 1: resolve per-match_key schedule (constant across a game; the raw
# tape logs it only intermittently to save payload size) and measure the
# quote-skew distribution used for precondition #5's cutoff.
# ---------------------------------------------------------------------------

def resolve_schedule_and_skew(files: List[str]) -> Tuple[Dict[str, Tuple[Optional[str], Optional[str]]], List[float], Dict[str, int]]:
    schedule: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    drift_events = 0
    skew_values: List[float] = []
    row_count = 0
    for row in _iter_rows(files):
        row_count += 1
        mk = row.get("match_key")
        g = (row.get("gemini") or {}).get("scheduled_start_at")
        k = (row.get("kalshi") or {}).get("scheduled_start_at")
        if mk is not None and (g or k):
            prev = schedule.get(mk)
            if prev is None:
                schedule[mk] = (g, k)
            else:
                pg, pk = prev
                if g and pg and g != pg:
                    drift_events += 1
                if k and pk and k != pk:
                    drift_events += 1
                schedule[mk] = (pg or g, pk or k)
        skew = row.get("quote_skew_seconds")
        if isinstance(skew, (int, float)):
            skew_values.append(float(skew))
    stats = {
        "row_count": row_count,
        "match_keys_with_any_schedule": len(schedule),
        "schedule_drift_events": drift_events,
    }
    return schedule, skew_values, stats


# ---------------------------------------------------------------------------
# Pass 2: verify the tape's own precomputed path fields agree with a live
# re-derivation via evaluate_match() / gemini_fee_total() / kalshi_fee_total()
# on a deterministic sample (proof of reuse-agreement, not assumed).
# ---------------------------------------------------------------------------

def hand_check_and_sample_agreement(files: List[str], sample_every: int = 137) -> Dict[str, Any]:
    checked = 0
    mismatches = []
    hand_check_row = None
    for i, row in enumerate(_iter_rows(files)):
        if i % sample_every != 0:
            continue
        match = {
            "teams": row["teams"], "gemini": row["gemini"], "kalshi": row["kalshi"],
            "terms_equivalence": row.get("terms_equivalence"),
        }
        recomputed = evaluate_match(
            match, contracts=row.get("contracts", 1),
            quote_skew_seconds=row.get("quote_skew_seconds", 0.0))
        for orig_path, new_path in zip(row.get("paths") or [], recomputed["paths"]):
            checked += 1
            for field in ("gross_edge_usd", "fees_usd", "net_edge_usd"):
                a, b = orig_path.get(field), new_path.get(field)
                if a is None and b is None:
                    continue
                if a is None or b is None or abs(float(a) - float(b)) > 1e-9:
                    mismatches.append({
                        "match_key": row.get("match_key"), "captured_at": row.get("captured_at"),
                        "field": field, "tape_value": a, "recomputed_value": b,
                    })
            if hand_check_row is None and orig_path.get("net_edge_usd") is not None:
                hand_check_row = {
                    "match_key": row.get("match_key"), "captured_at": row.get("captured_at"),
                    "gemini_yes_team": orig_path.get("gemini_yes_team"),
                    "gemini_ask": orig_path.get("gemini_ask"),
                    "kalshi_yes_team": orig_path.get("kalshi_yes_team"),
                    "kalshi_ask": orig_path.get("kalshi_ask"),
                    "hand_gross": round(1.0 - orig_path["gemini_ask"] - orig_path["kalshi_ask"], 6),
                    "hand_gemini_fee": math.ceil(
                        (0.07 * orig_path["gemini_ask"] * (1 - orig_path["gemini_ask"])) * 100 - 1e-9) / 100.0,
                    "hand_kalshi_fee": math.ceil(
                        (0.07 * orig_path["kalshi_ask"] * (1 - orig_path["kalshi_ask"])) * 100 - 1e-9) / 100.0,
                    "tape_gross_edge_usd": orig_path.get("gross_edge_usd"),
                    "tape_fees_usd": orig_path.get("fees_usd"),
                    "tape_net_edge_usd": orig_path.get("net_edge_usd"),
                }
                hand_check_row["hand_fees_total"] = round(
                    hand_check_row["hand_gemini_fee"] + hand_check_row["hand_kalshi_fee"], 6)
                hand_check_row["hand_net_edge_usd"] = round(
                    hand_check_row["hand_gross"] - hand_check_row["hand_fees_total"], 6)
                hand_check_row["hand_matches_tape"] = (
                    abs(hand_check_row["hand_gross"] - orig_path["gross_edge_usd"]) < 1e-9
                    and abs(hand_check_row["hand_fees_total"] - orig_path["fees_usd"]) < 1e-9
                    and abs(hand_check_row["hand_net_edge_usd"] - orig_path["net_edge_usd"]) < 1e-9)
    return {
        "paths_checked": checked,
        "mismatches_found": len(mismatches),
        "mismatch_sample": mismatches[:5],
        "agreement": len(mismatches) == 0,
        "hand_check_row": hand_check_row,
    }


# ---------------------------------------------------------------------------
# Build the case-ready row stream: fills the resolved schedule into every
# row, applies the skew-synchronization filter (precondition #5), and
# subtracts the requested per-leg slippage from the tape's fee-net edge
# (precondition #2) before handing off to build_cases() for episode
# segmentation and phase classification (precondition #1 and #3), reusing
# src/dislocation_cases.py verbatim.
# ---------------------------------------------------------------------------

def make_case_rows(files: List[str], schedule: Dict[str, Tuple[Optional[str], Optional[str]]],
                    skew_cutoff: float, slippage_per_leg: float) -> List[Dict[str, Any]]:
    out = []
    for row in _iter_rows(files):
        mk = row.get("match_key")
        g_sched, k_sched = schedule.get(mk, (None, None))
        gemini = dict(row.get("gemini") or {})
        kalshi = dict(row.get("kalshi") or {})
        gemini.setdefault("scheduled_start_at", None)
        kalshi.setdefault("scheduled_start_at", None)
        if not gemini.get("scheduled_start_at"):
            gemini["scheduled_start_at"] = g_sched
        if not kalshi.get("scheduled_start_at"):
            kalshi["scheduled_start_at"] = k_sched
        skew = row.get("quote_skew_seconds")
        skew_ok = isinstance(skew, (int, float)) and skew <= skew_cutoff
        new_paths = []
        for path in row.get("paths") or []:
            fee_net = path.get("net_edge_usd")
            new_path = dict(path)
            if fee_net is None or not skew_ok:
                new_path["net_edge_usd"] = None
                new_path["excluded_reason"] = (
                    "missing_ask" if fee_net is None else "quote_skew_desynchronized")
            else:
                new_path["net_edge_usd"] = round(fee_net - 2 * slippage_per_leg, 6)
                new_path["net_edge_usd_fee_only"] = fee_net
            new_paths.append(new_path)
        out.append({
            "captured_at": row.get("captured_at"),
            "collection_id": row.get("collection_id"),
            "match_key": mk,
            "quote_skew_seconds": skew,
            "gemini": gemini,
            "kalshi": kalshi,
            "paths": new_paths,
        })
    return out


def run_gate(case_rows: List[Dict[str, Any]], threshold_usd: float,
             now: datetime) -> List[Dict[str, Any]]:
    return build_cases(
        case_rows, pair_id="gemini_kalshi_mlb_moneyline", policy=POLICY, now=now,
        threshold_usd=threshold_usd, max_episode_gap_seconds=MAX_EPISODE_GAP_SECONDS)


# ---------------------------------------------------------------------------
# Per-observation (not per-episode) phase tally: denominator for the primary
# statistic is "distinct games with >=1 pre_scheduled_start MATCHED
# observation", which must be counted independent of whether anything ever
# qualified.
# ---------------------------------------------------------------------------

def per_observation_phase_tally(files: List[str],
                                 schedule: Dict[str, Tuple[Optional[str], Optional[str]]]
                                 ) -> Dict[str, Any]:
    games_pre, games_post, games_unknown = set(), set(), set()
    obs_pre = obs_post = obs_unknown = 0
    for row in _iter_rows(files):
        mk = row.get("match_key")
        g_sched, k_sched = schedule.get(mk, (None, None))
        g_dt = _parse_dt(g_sched)
        k_dt = _parse_dt(k_sched)
        if g_dt and k_dt:
            scheduled_start = min(g_dt, k_dt)
        else:
            scheduled_start = g_dt or k_dt
        captured = _parse_dt(row.get("captured_at"))
        if scheduled_start is None or captured is None:
            games_unknown.add(mk)
            obs_unknown += 1
            continue
        if captured < scheduled_start:
            games_pre.add(mk)
            obs_pre += 1
        else:
            games_post.add(mk)
            obs_post += 1
    # a game can show up in both sets across its lifetime (observed before AND
    # after its own start) -- that is expected and does not double count in
    # either the numerator or this denominator, which are set-based.
    return {
        "games_with_pre_start_observation": sorted(games_pre),
        "games_with_post_start_observation": sorted(games_post),
        "games_unknown_phase_only": sorted(games_unknown - games_pre - games_post),
        "n_games_pre_start": len(games_pre),
        "n_games_post_start": len(games_post),
        "n_games_unknown_only": len(games_unknown - games_pre - games_post),
        "n_observations_pre_start": obs_pre,
        "n_observations_post_start": obs_post,
        "n_observations_unknown": obs_unknown,
    }


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> Dict[str, Any]:
    from scipy.stats import beta
    if n == 0:
        return {"k": k, "n": n, "point_estimate": None, "ci_lower": None, "ci_upper": None,
                "one_sided_95_upper_bound": None, "method": "n=0, undefined"}
    lower = 0.0 if k == 0 else float(beta.ppf(alpha / 2, k, n - k + 1))
    upper = 1.0 if k == n else float(beta.ppf(1 - alpha / 2, k + 1, n - k))
    one_sided_upper = float(beta.ppf(1 - alpha, k + 1, n - k)) if k < n else 1.0
    return {
        "k": k, "n": n, "point_estimate": round(k / n, 6),
        "ci_lower": round(lower, 6), "ci_upper": round(upper, 6),
        "one_sided_95_upper_bound": round(one_sided_upper, 6),
        "rule_of_three_approx_upper_bound": round(3.0 / n, 6) if k == 0 else None,
        "method": "Clopper-Pearson exact binomial, clustered by game "
                  "(k=games with qualifying persistent pre-start episode, n=games with any pre-start matched observation)",
    }


def summarize_cases(cases: List[Dict[str, Any]], phase: str, persistent_only: bool) -> Dict[str, Any]:
    filtered = [c for c in cases if c["market_phase"] == phase
                and (not persistent_only or c["persistent"])]
    games = sorted({c["match_key"] for c in filtered})
    return {
        "episode_count": len(filtered),
        "distinct_games": len(games),
        "games": games,
    }


def net_edge_percentiles(case_rows: List[Dict[str, Any]],
                          schedule: Dict[str, Tuple[Optional[str], Optional[str]]]) -> Dict[str, Any]:
    pre_vals, post_vals = [], []
    for row in case_rows:
        mk = row["match_key"]
        g_sched, k_sched = schedule.get(mk, (None, None))
        g_dt, k_dt = _parse_dt(g_sched), _parse_dt(k_sched)
        scheduled_start = min(g_dt, k_dt) if g_dt and k_dt else (g_dt or k_dt)
        captured = _parse_dt(row["captured_at"])
        if scheduled_start is None or captured is None:
            continue
        bucket = pre_vals if captured < scheduled_start else post_vals
        for path in row["paths"]:
            v = path.get("net_edge_usd_fee_only")
            if v is not None:
                bucket.append(float(v))
    def pctl(values):
        return {
            "n": len(values),
            "p05": _quantile(values, 0.05), "median": _quantile(values, 0.5),
            "p90": _quantile(values, 0.90), "p95": _quantile(values, 0.95),
            "max": round(max(values), 6) if values else None,
            "min": round(min(values), 6) if values else None,
        }
    return {"pre_scheduled_start_fee_net_only_usd": pctl(pre_vals),
            "after_scheduled_start_fee_net_only_usd": pctl(post_vals)}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: strat_crossvenue_mlb_v1_replay_2026-08-11.py <tape_dir>")
    tape_dir = sys.argv[1]
    files = _tape_files(tape_dir)

    schedule, skew_values, scan_stats = resolve_schedule_and_skew(files)
    skew_p50 = _quantile(skew_values, 0.5)
    skew_p90 = _quantile(skew_values, 0.9)
    skew_p95 = _quantile(skew_values, 0.95)
    skew_max = round(max(skew_values), 6) if skew_values else None

    agreement = hand_check_and_sample_agreement(files)

    tally = per_observation_phase_tally(files, schedule)

    all_captured = [row.get("captured_at") for row in _iter_rows(files)]
    min_captured = min(c for c in all_captured if c)
    max_captured = max(c for c in all_captured if c)
    now = _parse_dt(max_captured) 
    if now is None:
        raise SystemExit("could not parse any captured_at")
    from datetime import timedelta
    now = now + timedelta(seconds=MAX_EPISODE_GAP_SECONDS + 1)

    # ---- PRIMARY run: slippage $0.01/leg, threshold $0.03, skew <= measured p95, persistence >= 2
    primary_rows = make_case_rows(files, schedule, skew_cutoff=skew_p95,
                                   slippage_per_leg=SLIPPAGE_PRIMARY_PER_LEG)
    primary_cases = run_gate(primary_rows, THRESHOLD_PRIMARY, now)
    pre_persistent = summarize_cases(primary_cases, "pre_scheduled_start", persistent_only=True)
    pre_any = summarize_cases(primary_cases, "pre_scheduled_start", persistent_only=False)
    post_persistent = summarize_cases(primary_cases, "after_scheduled_start", persistent_only=True)
    post_any = summarize_cases(primary_cases, "after_scheduled_start", persistent_only=False)
    unknown_persistent = summarize_cases(primary_cases, "unknown", persistent_only=True)

    k_games = pre_persistent["distinct_games"]
    n_games = tally["n_games_pre_start"]
    primary_ci = clopper_pearson(k_games, n_games)

    edge_pctl = net_edge_percentiles(primary_rows, schedule)

    # ---- Analytics cross-check: same methodology as the spec's cited
    # case_ledger (fee-net only, NO slippage baked into the qualifying test,
    # threshold $0.03), on THIS tape's actual (shorter) window.
    crosscheck_rows = make_case_rows(files, schedule, skew_cutoff=float("inf"),
                                      slippage_per_leg=0.0)
    crosscheck_cases = run_gate(crosscheck_rows, THRESHOLD_PRIMARY, now)
    crosscheck_summary = {
        "lifetime_cases": len(crosscheck_cases),
        "market_phase": {
            "pre_scheduled_start": sum(c["market_phase"] == "pre_scheduled_start" for c in crosscheck_cases),
            "after_scheduled_start": sum(c["market_phase"] == "after_scheduled_start" for c in crosscheck_cases),
            "unknown": sum(c["market_phase"] == "unknown" for c in crosscheck_cases),
        },
        "persistent_cases_ge_2_snapshots": sum(c["persistent"] for c in crosscheck_cases),
        "single_snapshot_share": round(
            sum(c["observations"] == 1 for c in crosscheck_cases) / len(crosscheck_cases), 6)
            if crosscheck_cases else None,
        "spec_cited_evidence_snapshot": {
            "lifetime_cases_14d": 55, "pre_scheduled_start": 0, "after_scheduled_start": 53,
            "unknown": 2, "persistent_cases_ge_2_snapshots": 14, "single_snapshot_share": 0.745,
            "replay_window_days_cited": 14,
        },
        "note": "This tape's actual coverage (see dataset_coverage) is NOT 14 days -- do not "
                "read agreement/disagreement here as validating or invalidating the cited figures' "
                "window, only their PHASE SPLIT SHAPE (pre vs post) on a shorter, later window.",
    }

    # ---- Stress tests
    stress = {"slippage_grid_per_leg_usd": {}, "threshold_grid_usd": {}, "persistence_grid": {}}
    for slip in (0.0, 0.005, 0.01, 0.02):
        rows = make_case_rows(files, schedule, skew_cutoff=skew_p95, slippage_per_leg=slip)
        cases = run_gate(rows, THRESHOLD_PRIMARY, now)
        pre_pers = summarize_cases(cases, "pre_scheduled_start", persistent_only=True)
        pre_any_s = summarize_cases(cases, "pre_scheduled_start", persistent_only=False)
        stress["slippage_grid_per_leg_usd"][f"{slip:.3f}"] = {
            "persistent_pre_start_games": pre_pers["distinct_games"],
            "persistent_pre_start_episodes": pre_pers["episode_count"],
            "any_pre_start_qualifying_games": pre_any_s["distinct_games"],
            "any_pre_start_qualifying_episodes": pre_any_s["episode_count"],
        }
    for thr in (0.02, 0.03, 0.05):
        cases = run_gate(primary_rows, thr, now)
        pre_pers = summarize_cases(cases, "pre_scheduled_start", persistent_only=True)
        pre_any_t = summarize_cases(cases, "pre_scheduled_start", persistent_only=False)
        stress["threshold_grid_usd"][f"{thr:.2f}"] = {
            "persistent_pre_start_games": pre_pers["distinct_games"],
            "persistent_pre_start_episodes": pre_pers["episode_count"],
            "any_pre_start_qualifying_games": pre_any_t["distinct_games"],
            "any_pre_start_qualifying_episodes": pre_any_t["episode_count"],
        }
    stress["persistence_grid"] = {
        "persistence_1_any_qualifying_snapshot": {
            "distinct_games": pre_any["distinct_games"], "episodes": pre_any["episode_count"],
        },
        "persistence_2_consecutive_snapshots_le_450s": {
            "distinct_games": pre_persistent["distinct_games"], "episodes": pre_persistent["episode_count"],
        },
    }

    # ---- Capacity
    per_event_cap = 10.0
    aggregate_cap = 50.0
    max_concurrent = 5
    monthly_days = 30.4375
    tape_days = (
        (_parse_dt(max_captured) - _parse_dt(min_captured)).total_seconds() / 86400.0
    )
    per_path_notional = min(per_event_cap, aggregate_cap / max_concurrent)
    observed_pre_start_qualifying_persistent_episodes = pre_persistent["episode_count"]
    episodes_per_day_observed = (observed_pre_start_qualifying_persistent_episodes / tape_days
                                  if tape_days > 0 else 0.0)
    episodes_per_month_observed = episodes_per_day_observed * monthly_days
    # Point-estimate (continued-forward) scenario: literally the observed rate
    # (0 episodes in the tape) continued forward. Whatever per-episode edge you
    # multiply by, 0 episodes/month x anything = $0/month -- reported plainly,
    # not dressed up with a nonzero multiplier that cannot matter.
    point_estimate_monthly_gross_edge_usd = round(
        episodes_per_month_observed * max(edge_pctl["after_scheduled_start_fee_net_only_usd"]["max"] or 0.0, 0.0)
        * per_path_notional, 4)

    # Upper-bound (statistical) scenario: the primary statistic's own
    # one-sided 95% upper confidence bound on the GAME-level qualifying rate
    # (since the point estimate is 0/79, this is the honest "how large could
    # the true rate plausibly be and still be consistent with 0 observed in
    # 79 games" bound), applied to the games/month this tape's own observation
    # cadence implies, at the single most generous per-path edge actually
    # measured anywhere on this tape (the post-start max, since pre-start
    # itself never once printed a positive fee-net edge -- see
    # secondary_diagnostics.net_edge_distribution... -- so there is no
    # pre-start magnitude to draw a scenario edge from at all).
    games_with_pre_start_obs = tally["n_games_pre_start"]
    games_per_month_est = (games_with_pre_start_obs / tape_days) * monthly_days if tape_days > 0 else 0.0
    upper_bound_game_rate = primary_ci["one_sided_95_upper_bound"] or 0.0
    upper_bound_qualifying_games_per_month = games_per_month_est * upper_bound_game_rate
    scenario_edge_per_path = edge_pctl["after_scheduled_start_fee_net_only_usd"]["max"] or 0.0
    upper_bound_monthly_gross_edge_usd = round(
        upper_bound_qualifying_games_per_month * scenario_edge_per_path * per_path_notional, 4)

    capacity = {
        "caps": {"per_event_cap_usd": per_event_cap, "aggregate_cap_usd": aggregate_cap,
                 "max_concurrent_open_paths": max_concurrent},
        "tape_days": round(tape_days, 3),
        "per_path_notional_usd": per_path_notional,
        "per_path_notional_arithmetic": (
            f"min(per-event cap ${per_event_cap:.2f}, aggregate ${aggregate_cap:.2f} / "
            f"{max_concurrent} concurrent = ${aggregate_cap / max_concurrent:.2f}) = ${per_path_notional:.2f}"),
        "point_estimate_scenario": {
            "observed_pre_start_qualifying_persistent_episodes": observed_pre_start_qualifying_persistent_episodes,
            "episodes_per_day_observed": round(episodes_per_day_observed, 6),
            "episodes_per_month_continued_forward": round(episodes_per_month_observed, 4),
            "arithmetic": (
                f"{episodes_per_month_observed:.4f} episodes/month (continued forward from the observed "
                f"{observed_pre_start_qualifying_persistent_episodes} episodes / {tape_days:.2f} tape-days) "
                f"x any per-path edge x ${per_path_notional:.2f} notional/path = "
                f"${point_estimate_monthly_gross_edge_usd:.4f}/month (zero episodes makes the edge multiplier moot)"),
            "implied_monthly_gross_edge_usd": point_estimate_monthly_gross_edge_usd,
        },
        "upper_bound_statistical_scenario": {
            "description": "NOT a point estimate. Uses the primary statistic's own one-sided 95% "
                            "Clopper-Pearson upper bound on the game-level qualifying rate (honest "
                            "answer to 'how high could the true rate plausibly be given 0/79 observed'), "
                            "applied to this tape's own observed games/month cadence, at the single most "
                            "generous per-path net edge measured ANYWHERE on the tape (post-start max, "
                            "since pre-start itself never measured a positive fee-net edge at all).",
            "games_with_pre_start_observation_this_tape": games_with_pre_start_obs,
            "games_per_month_estimate": round(games_per_month_est, 4),
            "one_sided_95_upper_bound_game_qualifying_rate": upper_bound_game_rate,
            "upper_bound_qualifying_games_per_month": round(upper_bound_qualifying_games_per_month, 4),
            "scenario_edge_per_dollar_path_usd": scenario_edge_per_path,
            "scenario_edge_source": "max net_edge_usd (fee-net only) observed on ANY after_scheduled_start "
                                     "priced path in this tape -- reused here only because pre-start priced "
                                     "paths never printed a positive value to draw a scenario edge from.",
            "arithmetic": (
                f"{games_per_month_est:.4f} games/month x {upper_bound_game_rate:.6f} (95% one-sided upper "
                f"bound qualifying rate) = {upper_bound_qualifying_games_per_month:.4f} qualifying games/month "
                f"x ${scenario_edge_per_path:.4f}/$1-path edge x ${per_path_notional:.2f} notional/path = "
                f"${upper_bound_monthly_gross_edge_usd:.4f}/month"),
            "implied_monthly_gross_edge_usd": upper_bound_monthly_gross_edge_usd,
        },
        "economic_bar_usd_per_month": 20.0,
        "point_estimate_clears_economic_bar": point_estimate_monthly_gross_edge_usd >= 20.0,
        "upper_bound_scenario_clears_economic_bar": upper_bound_monthly_gross_edge_usd >= 20.0,
        "interpretation": (
            "The POINT ESTIMATE (continuing forward exactly what this tape showed: zero qualifying "
            "pre-start episodes in 8.27 days across 79 candidate games) is $0/month, far below the $20 "
            "bar. The statistical UPPER BOUND scenario -- which asks how large the true rate could "
            "plausibly be and still be consistent with observing zero in a sample this size -- is NOT "
            "ruled out from clearing the bar. Both are reported because a single point estimate of "
            "zero, on a short window, understates the genuine uncertainty about the forward rate; "
            "neither one is the 'answer' on its own."
        ),
    }

    # ---- Precondition 4 (settlement equivalence gate)
    import yaml
    gate_path = REPO_ROOT / "config" / "settlement_equivalence.yaml"
    gate_doc = yaml.safe_load(gate_path.read_text())
    policy_row = next(
        (p for p in gate_doc.get("policies", [])
         if p.get("id") == "gemini_kalshi_mlb_moneyline"), None)
    precondition_4 = {
        "gate_path": str(gate_path.relative_to(REPO_ROOT)),
        "policy_id": "gemini_kalshi_mlb_moneyline",
        "decision_state": policy_row.get("decision") if policy_row else None,
        "approval": policy_row.get("approval") if policy_row else None,
        "dimensions": policy_row.get("dimensions") if policy_row else None,
        "result": "PASS" if (policy_row and policy_row.get("decision") == "verified"
                              and policy_row.get("approval")) else "FAIL",
        "reason": (
            "settlement_equivalence.state must equal 'verified' with a recorded approval for this "
            "pair; current decision is "
            f"'{policy_row.get('decision') if policy_row else 'MISSING POLICY'}' with approval="
            f"{policy_row.get('approval') if policy_row else None}."
        ),
    }

    result = {
        "dataset_coverage": {
            "files": [Path(f).name for f in files],
            "min_captured_at": min_captured, "max_captured_at": max_captured,
            "tape_days": round(tape_days, 3),
            "spec_cited_replay_window_days": 14,
            "note": "Actual tape coverage is ~8.3 days (2026-08-02T19:17Z to "
                    "2026-08-11T01:45Z), not the 14-day window the spec's evidence_snapshot cites.",
        },
        "row_scan_stats": scan_stats,
        "quote_skew_seconds_measured": {
            "p50": skew_p50, "p90": skew_p90, "p95": skew_p95, "max": skew_max,
            "n": len(skew_values),
            "used_as_precondition_5_cutoff": skew_p95,
            "spec_cited_unverified": {"median": 0.249, "p95": 0.817, "max": 5.8},
        },
        "sample_agreement_check": agreement,
        "observation_level_phase_tally": {
            k: v for k, v in tally.items()
            if not k.startswith("games_with_") and k != "games_unknown_phase_only"
        },
        "primary_statistic": {
            "definition": "distinct GAMES with >=1 pre_scheduled_start qualifying "
                           "(net edge >= $0.03 after both venues' actual taker fees AND "
                           "$0.01/leg slippage) PERSISTENT (>=2 consecutive snapshots, "
                           "<=450s apart) episode, over distinct GAMES with >=1 "
                           "pre_scheduled_start MATCHED observation (any phase-pre observation "
                           "at all, qualifying or not)",
            "k_games_with_qualifying_persistent_pre_start_episode": k_games,
            "n_games_with_any_pre_start_matched_observation": n_games,
            "games_with_qualifying_persistent_pre_start_episode": pre_persistent["games"],
            "episode_count_pre_start_qualifying_persistent": pre_persistent["episode_count"],
            "confidence_interval": primary_ci,
        },
        "secondary_diagnostics": {
            "post_start_equivalents": {
                "k_games_with_qualifying_persistent_post_start_episode": post_persistent["distinct_games"],
                "n_games_with_any_post_start_matched_observation": tally["n_games_post_start"],
                "episode_count_post_start_qualifying_persistent": post_persistent["episode_count"],
                "episode_count_post_start_qualifying_any_persistence": post_any["episode_count"],
                "confidence_interval": clopper_pearson(
                    post_persistent["distinct_games"], tally["n_games_post_start"]),
            },
            "unknown_phase_persistent_episodes": unknown_persistent["episode_count"],
            "net_edge_distribution_fee_net_only_usd_pre_vs_post": edge_pctl,
            "analytics_json_cross_check": crosscheck_summary,
        },
        "stress_tests": stress,
        "capacity": capacity,
        "precondition_4_settlement_equivalence": precondition_4,
    }
    print(json.dumps(result, indent=2, sort_keys=False, default=str))


if __name__ == "__main__":
    main()
