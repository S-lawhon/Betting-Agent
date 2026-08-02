"""Deterministic market-universe census and strategy-scout seed ranking.

This module deliberately stops at *research assignments*.  A high-ranking
seed is not an OpportunityCard and cannot enter the strategy registry until a
scout has researched and falsified it.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence


SCHEMA_VERSION = 1
MAKER_FEE_TYPE = "quadratic_with_maker_fees"
SERIES_FIELDS = (
    "ticker", "title", "category", "frequency", "fee_type",
    "fee_multiplier", "contract_terms_url", "contract_url",
    "settlement_sources", "additional_prohibitions", "tags",
)


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def normalize_series(payload: Any) -> List[Dict[str, Any]]:
    """Accept a Kalshi API response, a ticker-keyed cache, or a plain list."""
    if isinstance(payload, Mapping) and isinstance(payload.get("series"), list):
        rows = payload["series"]
    elif isinstance(payload, Mapping):
        rows = list(payload.values())
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("series input must be a list, ticker map, or API response")

    normalized: Dict[str, Dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        row = {key: raw.get(key) for key in SERIES_FIELDS}
        row["ticker"] = ticker
        row["fingerprint"] = _hash({key: row.get(key) for key in SERIES_FIELDS})
        normalized[ticker] = row
    return [normalized[ticker] for ticker in sorted(normalized)]


def load_snapshot_series(snapshot: Optional[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not snapshot:
        return {}
    rows = snapshot.get("series", [])
    return {str(row["ticker"]): dict(row) for row in rows if row.get("ticker")}


def _changed_fields(current: Mapping[str, Any], old: Mapping[str, Any]) -> List[str]:
    return [key for key in SERIES_FIELDS if current.get(key) != old.get(key)]


def _lane(row: Mapping[str, Any], reasons: Sequence[str]) -> str:
    if "contract_terms_changed" in reasons or "settlement_sources_changed" in reasons:
        return "contract_mechanics"
    if "fee_regime_changed" in reasons or row.get("fee_type") == MAKER_FEE_TYPE:
        return "liquidity_and_maker"
    category = str(row.get("category") or "").lower()
    if category in {"crypto", "economics", "financials", "companies"}:
        return "cross_venue"
    if category in {"sports", "weather", "science and technology"}:
        return "information_latency"
    if str(row.get("frequency") or "").lower() not in {"", "one_off"}:
        return "recurring_products"
    return "new_products"


def _diverse_shortlist(candidates: Sequence[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Cap any one lane at 40%, then backfill if breadth is unavailable."""
    if len(candidates) <= limit:
        return list(candidates)
    per_lane = max(1, math.ceil(limit * 0.40))
    counts: Dict[str, int] = {}
    chosen: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []
    for item in candidates:
        lane = item["lane"]
        if counts.get(lane, 0) < per_lane:
            chosen.append(item)
            counts[lane] = counts.get(lane, 0) + 1
        else:
            deferred.append(item)
        if len(chosen) == limit:
            return chosen
    chosen.extend(deferred[:limit - len(chosen)])
    return chosen


def _known_opportunity_ids(ticker: str, opportunities: Sequence[Mapping[str, Any]]) -> List[str]:
    needle = ticker.lower()
    matches = []
    for card in opportunities:
        haystack = _canonical(card).lower()
        if needle in haystack:
            matches.append(str(card.get("id") or card.get("opportunity_id") or "unknown"))
    return sorted(set(matches))


def _due_rechecks(
    ticker: str,
    opportunities: Sequence[Mapping[str, Any]],
    now: datetime,
    already_seeded: Sequence[str],
) -> List[str]:
    due: List[str] = []
    for card in opportunities:
        if str(card.get("market_family") or "").upper() != ticker:
            continue
        value = card.get("recheck_after")
        if not value and isinstance(card.get("extra"), Mapping):
            value = card["extra"].get("recheck_after")
        if not value:
            continue
        token = f"{card.get('id', 'unknown')}:{value}"
        try:
            due_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if due_at <= now and token not in already_seeded:
            due.append(token)
    return sorted(due)


@dataclass(frozen=True)
class CandidateSeed:
    id: str
    run_id: str
    generated_at: str
    rank: int
    score: int
    lane: str
    market_family: str
    title: str
    reason_codes: List[str]
    changed_fields: List[str]
    evidence: Dict[str, Any]
    possible_duplicates: List[str] = field(default_factory=list)
    assignment: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CensusResult:
    snapshot: Dict[str, Any]
    ledger: Dict[str, Any]
    manifest: Dict[str, Any]
    seeds: List[CandidateSeed]


def _score(row: Mapping[str, Any], reasons: Sequence[str], duplicates: Sequence[str]) -> int:
    weights = {
        "new_series": 40,
        "contract_terms_changed": 35,
        "settlement_sources_changed": 30,
        "fee_regime_changed": 30,
        "other_metadata_changed": 10,
        "full_universe_review": 5,
        "scheduled_recheck": 60,
    }
    score = sum(weights.get(reason, 0) for reason in reasons)
    if row.get("fee_type") != MAKER_FEE_TYPE:
        score += 8
    if str(row.get("frequency") or "").lower() not in {"", "one_off"}:
        score += 8
    if row.get("contract_terms_url"):
        score += 4
    score -= min(30, 15 * len(duplicates))
    return max(0, min(100, score))


def build_census(
    raw_series: Any,
    *,
    previous_snapshot: Optional[Mapping[str, Any]] = None,
    previous_ledger: Optional[Mapping[str, Any]] = None,
    opportunities: Sequence[Mapping[str, Any]] = (),
    now: Optional[datetime] = None,
    full: bool = False,
    max_seeds: int = 25,
) -> CensusResult:
    """Build a reproducible snapshot, coverage ledger, and ranked scout inbox."""
    now = now or datetime.now(timezone.utc)
    generated_at = utc_iso(now)
    rows = normalize_series(raw_series)
    prior = load_snapshot_series(previous_snapshot)
    run_id = f"census_{now.strftime('%Y%m%dT%H%M%SZ')}_{_hash(rows)[:8]}"

    candidates: List[Dict[str, Any]] = []
    changed_counts: Dict[str, int] = {
        "new": 0, "changed": 0, "unchanged": 0, "missing": 0,
    }
    current_tickers = {row["ticker"] for row in rows}
    changed_counts["missing"] = len(set(prior) - current_tickers)

    for row in rows:
        old = prior.get(row["ticker"])
        old_coverage = dict((previous_ledger or {}).get("series", {})).get(
            row["ticker"], {}
        )
        due_rechecks = _due_rechecks(
            row["ticker"], opportunities, now,
            old_coverage.get("rechecks_seeded", []),
        )
        changed = [] if old is None else _changed_fields(row, old)
        reasons: List[str] = []
        if old is None:
            reasons.append("new_series")
            changed_counts["new"] += 1
        elif changed:
            changed_counts["changed"] += 1
            if "contract_terms_url" in changed or "contract_url" in changed:
                reasons.append("contract_terms_changed")
            if "settlement_sources" in changed:
                reasons.append("settlement_sources_changed")
            if "fee_type" in changed or "fee_multiplier" in changed:
                reasons.append("fee_regime_changed")
            if not reasons or any(key not in {
                "contract_terms_url", "contract_url", "settlement_sources",
                "fee_type", "fee_multiplier",
            } for key in changed):
                reasons.append("other_metadata_changed")
        else:
            changed_counts["unchanged"] += 1
            if full:
                reasons.append("full_universe_review")

        if due_rechecks:
            reasons.append("scheduled_recheck")

        if not reasons:
            continue
        duplicates = _known_opportunity_ids(row["ticker"], opportunities)
        coverage_bonus = 20 if full and not old_coverage.get("times_seeded") else 0
        candidates.append({
            "row": row,
            "changed": changed,
            "reasons": reasons,
            "duplicates": duplicates,
            "due_rechecks": due_rechecks,
            "score": min(100, _score(row, reasons, duplicates) + coverage_bonus),
            "lane": _lane(row, reasons),
        })

    candidates.sort(key=lambda item: (-item["score"], item["row"]["ticker"]))
    shortlisted = _diverse_shortlist(candidates, max_seeds)
    seeds: List[CandidateSeed] = []
    for rank, item in enumerate(shortlisted, start=1):
        row = item["row"]
        lane = item["lane"]
        seed_id = f"seed_{_hash([run_id, row['ticker'], item['reasons']])[:16]}"
        seeds.append(CandidateSeed(
            id=seed_id,
            run_id=run_id,
            generated_at=generated_at,
            rank=rank,
            score=item["score"],
            lane=lane,
            market_family=row["ticker"],
            title=str(row.get("title") or row["ticker"]),
            reason_codes=list(item["reasons"]),
            changed_fields=list(item["changed"]),
            evidence={
                "category": row.get("category"),
                "frequency": row.get("frequency"),
                "fee_type": row.get("fee_type"),
                "fee_multiplier": row.get("fee_multiplier"),
                "contract_terms_url": row.get("contract_terms_url"),
                "settlement_sources": row.get("settlement_sources") or [],
                "series_fingerprint": row["fingerprint"],
                "due_rechecks": item["due_rechecks"],
            },
            possible_duplicates=list(item["duplicates"]),
            assignment={
                "agent": "strategy-scout",
                "instruction": (
                    "Research and falsify this seed. Return one OpportunityCard "
                    "only if a testable net edge remains; otherwise return a rejection."
                ),
                "may_enter_strategy_registry": False,
            },
        ))

    old_entries = dict((previous_ledger or {}).get("series", {}))
    ledger_entries: Dict[str, Any] = {}
    seed_by_ticker = {seed.market_family: seed for seed in seeds}
    for row in rows:
        old_entry = old_entries.get(row["ticker"], {})
        seed = seed_by_ticker.get(row["ticker"])
        seeded_rechecks = list(old_entry.get("rechecks_seeded", []))
        if seed:
            seeded_rechecks.extend(seed.evidence.get("due_rechecks", []))
        ledger_entries[row["ticker"]] = {
            "first_seen_at": old_entry.get("first_seen_at", generated_at),
            "last_seen_at": generated_at,
            "last_fingerprint": row["fingerprint"],
            "last_census_run": run_id,
            "times_seeded": int(old_entry.get("times_seeded", 0)) + int(seed is not None),
            "last_seed_id": seed.id if seed else old_entry.get("last_seed_id"),
            "lane": _lane(row, []),
            "rechecks_seeded": sorted(set(seeded_rechecks)),
        }
    for ticker in sorted(set(old_entries) - current_tickers):
        ledger_entries[ticker] = dict(old_entries[ticker]) | {
            "missing_since": old_entries[ticker].get("missing_since", generated_at),
            "last_census_run": run_id,
        }

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "source_count": len(rows),
        "universe_hash": _hash(rows),
        "series": rows,
    }
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": generated_at,
        "last_run_id": run_id,
        "series": ledger_entries,
    }
    fee_type_counts = {
        str(fee_type): sum(row.get("fee_type") == fee_type for row in rows)
        for fee_type in sorted({row.get("fee_type") for row in rows}, key=str)
    }
    anomalies: List[str] = []
    unknown_fee_types = sorted(
        key for key in fee_type_counts
        if key not in {"quadratic", MAKER_FEE_TYPE, "None"}
    )
    if unknown_fee_types:
        anomalies.append(f"unknown_fee_types:{','.join(unknown_fee_types)}")
    if prior and len(rows) < 0.8 * len(prior):
        anomalies.append("source_count_collapse")
    if prior and changed_counts["missing"] > 0.05 * len(prior):
        anomalies.append("large_removal_wave")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "mode": "full" if full else "delta",
        "source_count": len(rows),
        "previous_source_count": len(prior),
        "changes": changed_counts,
        "candidate_count": len(candidates),
        "seed_count": len(seeds),
        "seed_ids": [seed.id for seed in seeds],
        "lane_counts": {
            lane: sum(seed.lane == lane for seed in seeds)
            for lane in sorted({seed.lane for seed in seeds})
        },
        "fee_type_counts": fee_type_counts,
        "anomalies": anomalies,
        "safety": {
            "creates_opportunity_cards": False,
            "mutates_strategy_registry": False,
            "requires_scout_review": True,
        },
    }
    return CensusResult(snapshot=snapshot, ledger=ledger, manifest=manifest, seeds=seeds)


def seeds_payload(result: CensusResult) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": result.manifest["run_id"],
        "generated_at": result.manifest["generated_at"],
        "kind": "strategy_scout_inbox",
        "seeds": [asdict(seed) for seed in result.seeds],
    }
