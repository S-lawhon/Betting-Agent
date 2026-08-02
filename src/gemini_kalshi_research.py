"""Conservative Gemini/Kalshi prediction-market research primitives.

Version one supports MLB moneylines only.  Matching is exact on event date and
the two normalized teams; ambiguous events are rejected.  Current Gemini and
Kalshi cancellation/postponement language differs, so every match remains
``terms_unverified`` and can never be labelled actionable.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from src.kalshi_fees import fee_total as kalshi_fee_total


EASTERN = ZoneInfo("America/New_York")
GEMINI_TAKER_COEF = 0.07
TERMS_STATUS = "unverified"
TERMS_REASON = (
    "Gemini and Kalshi use different postponement/cancellation resolution "
    "windows; manual contract-level equivalence approval is required."
)

_TEAM_ALIASES = {
    "arizona": "diamondbacks", "diamondbacks": "diamondbacks", "az": "diamondbacks",
    "atlanta": "braves", "braves": "braves", "atl": "braves",
    "baltimore": "orioles", "orioles": "orioles", "bal": "orioles",
    "boston": "red sox", "red sox": "red sox", "bos": "red sox",
    "chicago cubs": "cubs", "cubs": "cubs", "chc": "cubs",
    "chicago white sox": "white sox", "white sox": "white sox", "cws": "white sox",
    "cincinnati": "reds", "reds": "reds", "cin": "reds",
    "cleveland": "guardians", "guardians": "guardians", "cle": "guardians",
    "colorado": "rockies", "rockies": "rockies", "col": "rockies",
    "detroit": "tigers", "tigers": "tigers", "det": "tigers",
    "houston": "astros", "astros": "astros", "hou": "astros",
    "kansas city": "royals", "royals": "royals", "kc": "royals",
    "los angeles angels": "angels", "la angels": "angels", "angels": "angels", "laa": "angels",
    "los angeles dodgers": "dodgers", "la dodgers": "dodgers", "dodgers": "dodgers", "lad": "dodgers",
    "miami": "marlins", "marlins": "marlins", "mia": "marlins",
    "milwaukee": "brewers", "brewers": "brewers", "mil": "brewers",
    "minnesota": "twins", "twins": "twins", "min": "twins",
    "new york mets": "mets", "ny mets": "mets", "mets": "mets", "nym": "mets",
    "new york yankees": "yankees", "ny yankees": "yankees", "yankees": "yankees", "nyy": "yankees",
    "athletics": "athletics", "oakland": "athletics", "ath": "athletics", "oak": "athletics",
    "philadelphia": "phillies", "phillies": "phillies", "phi": "phillies",
    "pittsburgh": "pirates", "pirates": "pirates", "pit": "pirates",
    "san diego": "padres", "padres": "padres", "sd": "padres",
    "san francisco": "giants", "giants": "giants", "sf": "giants",
    "seattle": "mariners", "mariners": "mariners", "sea": "mariners",
    "st louis": "cardinals", "cardinals": "cardinals", "stl": "cardinals",
    "tampa bay": "rays", "rays": "rays", "tb": "rays",
    "texas": "rangers", "rangers": "rangers", "tex": "rangers",
    "toronto": "blue jays", "blue jays": "blue jays", "tor": "blue jays",
    "washington": "nationals", "nationals": "nationals", "wsh": "nationals",
}


def _clean(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    return re.sub(r"\s+", " ", text)


def normalize_mlb_team(value: Any) -> Optional[str]:
    cleaned = _clean(value)
    if cleaned in _TEAM_ALIASES:
        return _TEAM_ALIASES[cleaned]
    # Labels often include a city plus nickname or question framing.
    matches = {canonical for alias, canonical in _TEAM_ALIASES.items()
               if re.search(r"(?:^| ){}(?: |$)".format(re.escape(alias)), cleaned)}
    return next(iter(matches)) if len(matches) == 1 else None


def _float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result > 1.0 and result <= 100.0:
        result /= 100.0
    return result if 0.0 <= result <= 1.0 else None


def _first(mapping: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if mapping.get(name) is not None:
            return mapping[name]
    return None


def _iso_date(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(EASTERN).date().isoformat()


def _kalshi_date(ticker: str) -> Optional[str]:
    match = re.search(r"-(\d{2})([A-Z]{3})(\d{2})(\d{4})", ticker.upper())
    if not match:
        return None
    try:
        parsed = datetime.strptime("".join(match.groups()), "%y%b%d%H%M")
    except ValueError:
        return None
    return parsed.replace(tzinfo=EASTERN).date().isoformat()


def _contract_label(contract: Dict[str, Any]) -> Any:
    return _first(contract, "name", "title", "label", "outcome", "description",
                  "contractName", "displayName")


def normalize_gemini_events(events: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    normalized: List[Dict[str, Any]] = []
    reasons: Dict[str, int] = defaultdict(int)
    for event in events:
        sports = event.get("sportsMarket") or event.get("sports_market") or {}
        if _clean(sports.get("sport")) not in ("baseball", "mlb"):
            reasons["not_mlb"] += 1
            continue
        if _clean(sports.get("type")) not in ("moneyline", "money line", "winner"):
            reasons["not_moneyline"] += 1
            continue
        contracts = event.get("contracts") or event.get("outcomes") or []
        outcomes: Dict[str, Dict[str, Any]] = {}
        for contract in contracts:
            if not isinstance(contract, dict):
                continue
            team = normalize_mlb_team(_contract_label(contract))
            if not team or team in outcomes:
                continue
            prices = contract.get("prices") or {}
            buy = prices.get("buy") or {}
            sell = prices.get("sell") or {}
            outcomes[team] = {
                "team": team,
                "contract_id": str(_first(contract, "id", "contractId", "symbol") or ""),
                "bid": _float(_first(
                    contract, "bid", "bestBid", "bidPrice")
                    if _first(contract, "bid", "bestBid", "bidPrice") is not None
                    else _first(prices, "bestBid")
                    if _first(prices, "bestBid") is not None else sell.get("yes")),
                "ask": _float(_first(
                    contract, "ask", "bestAsk", "askPrice")
                    if _first(contract, "ask", "bestAsk", "askPrice") is not None
                    else _first(prices, "bestAsk")
                    if _first(prices, "bestAsk") is not None else buy.get("yes")),
            }
        if len(outcomes) != 2:
            reasons["not_two_resolved_outcomes"] += 1
            continue
        date = _iso_date(_first(event, "startTime", "start_time"))
        if not date:
            reasons["missing_start_date"] += 1
            continue
        normalized.append({
            "venue": "gemini", "event_id": str(_first(event, "id", "eventId") or ""),
            "title": str(_first(event, "title", "name") or ""), "date_et": date,
            "teams": sorted(outcomes), "outcomes": outcomes,
        })
    return normalized, dict(reasons)


def normalize_kalshi_events(events: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    normalized: List[Dict[str, Any]] = []
    reasons: Dict[str, int] = defaultdict(int)
    for event in events:
        event_ticker = str(event.get("event_ticker") or event.get("ticker") or "")
        if not event_ticker.startswith("KXMLBGAME-"):
            reasons["not_mlb_moneyline"] += 1
            continue
        outcomes: Dict[str, Dict[str, Any]] = {}
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            team = normalize_mlb_team(
                _first(market, "yes_sub_title", "yes_subtitle", "subtitle"))
            if not team:
                ticker = str(market.get("ticker") or "")
                team = normalize_mlb_team(ticker.rsplit("-", 1)[-1])
            if not team or team in outcomes:
                continue
            outcomes[team] = {
                "team": team, "ticker": str(market.get("ticker") or ""),
                "bid": _float(_first(market, "yes_bid_dollars", "yes_bid")),
                "ask": _float(_first(market, "yes_ask_dollars", "yes_ask")),
            }
        if len(outcomes) != 2:
            reasons["not_two_resolved_outcomes"] += 1
            continue
        date = _kalshi_date(event_ticker)
        if not date:
            reasons["missing_ticker_date"] += 1
            continue
        normalized.append({
            "venue": "kalshi", "event_id": event_ticker,
            "title": str(event.get("title") or ""), "date_et": date,
            "teams": sorted(outcomes), "outcomes": outcomes,
        })
    return normalized, dict(reasons)


def match_events(gemini: Iterable[Dict[str, Any]],
                 kalshi: Iterable[Dict[str, Any]],
                 terms_policy: Optional[Dict[str, Any]] = None
                 ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Exact-match unique event keys; reject duplicated/ambiguous keys."""
    indexes: List[Dict[Tuple[str, Tuple[str, ...]], List[Dict[str, Any]]]] = []
    for rows in (gemini, kalshi):
        index: Dict[Tuple[str, Tuple[str, ...]], List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            index[(str(row["date_et"]), tuple(sorted(row["teams"])))].append(row)
        indexes.append(index)
    keys = set(indexes[0]) & set(indexes[1])
    matches: List[Dict[str, Any]] = []
    counts = {"shared_keys": len(keys), "ambiguous_keys": 0, "matched": 0}
    for key in sorted(keys):
        left, right = indexes[0][key], indexes[1][key]
        if len(left) != 1 or len(right) != 1:
            counts["ambiguous_keys"] += 1
            continue
        policy = terms_policy or {
            "id": "gemini_kalshi_mlb_moneyline", "status": TERMS_STATUS,
            "reason": TERMS_REASON, "actionable_allowed": False,
        }
        matches.append({
            "match_key": f"{key[0]}|{'|'.join(key[1])}",
            "date_et": key[0], "teams": list(key[1]),
            "gemini": left[0], "kalshi": right[0],
            "terms_policy_id": policy.get("id"),
            "terms_equivalence": policy.get("status", TERMS_STATUS),
            "terms_reason": policy.get("reason", TERMS_REASON),
        })
    counts["matched"] = len(matches)
    return matches, counts


def gemini_fee_total(contracts: float, price: float) -> float:
    """Gemini prediction taker fee, rounded up to the next cent."""
    raw = GEMINI_TAKER_COEF * contracts * price * (1.0 - price)
    return math.ceil(raw * 100.0 - 1e-9) / 100.0


def evaluate_match(match: Dict[str, Any], *, contracts: int = 1,
                   quote_skew_seconds: float = 0.0,
                   max_quote_skew_seconds: float = 10.0) -> Dict[str, Any]:
    """Evaluate complementary YES purchases with actual rounded taker fees."""
    teams = list(match["teams"])
    paths: List[Dict[str, Any]] = []
    blockers = []
    if match.get("terms_equivalence") != "verified":
        blockers.append("terms_" + str(
            match.get("terms_equivalence") or "unverified"))
    if quote_skew_seconds > max_quote_skew_seconds:
        blockers.append("quote_skew")
    for gemini_team in teams:
        kalshi_team = teams[1] if gemini_team == teams[0] else teams[0]
        gemini_ask = match["gemini"]["outcomes"][gemini_team].get("ask")
        kalshi_ask = match["kalshi"]["outcomes"][kalshi_team].get("ask")
        path_blockers = list(blockers)
        if gemini_ask is None or kalshi_ask is None:
            path_blockers.append("missing_ask")
            net = None
            gross = None
            fees = None
        else:
            gemini_fee = gemini_fee_total(contracts, gemini_ask)
            kalshi_fee = kalshi_fee_total(
                contracts, kalshi_ask, maker=False, series_ticker="KXMLBGAME")
            gross = round(contracts * (1.0 - gemini_ask - kalshi_ask), 6)
            fees = round(gemini_fee + kalshi_fee, 6)
            net = round(gross - fees, 6)
            if net <= 0:
                path_blockers.append("no_positive_net")
        paths.append({
            "gemini_yes_team": gemini_team, "kalshi_yes_team": kalshi_team,
            "gemini_ask": gemini_ask, "kalshi_ask": kalshi_ask,
            "gross_edge_usd": gross, "fees_usd": fees, "net_edge_usd": net,
            "hypothetical_positive": isinstance(net, (int, float)) and net > 0,
            "actionable": not path_blockers,
            "blockers": sorted(set(path_blockers)),
        })
    return dict(match) | {
        "contracts": contracts, "quote_skew_seconds": round(quote_skew_seconds, 3),
        "max_quote_skew_seconds": max_quote_skew_seconds, "paths": paths,
        "best_net_edge_usd": max((p["net_edge_usd"] for p in paths
                                  if p["net_edge_usd"] is not None), default=None),
        "hypothetical_positive_paths": sum(p["hypothetical_positive"] for p in paths),
        "actionable_paths": sum(p["actionable"] for p in paths),
    }
