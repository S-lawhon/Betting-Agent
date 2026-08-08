"""Venue-agnostic source collection and ranked research intake.

Collectors produce compact, provenance-rich SourceItems.  The intake ledger
deduplicates them and creates research assignments, never OpportunityCards or
strategy-runtime requests.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urljoin

import requests

from src.research_eligibility import EligibilityRegistry


SCHEMA_VERSION = 1

TERMINAL_MARKET_STATUSES = {
    "CANCELED", "CANCELLED", "CLOSED", "EXPIRED", "FINAL", "INACTIVE",
    "MARKET_STATUS_CLOSED", "MARKET_STATUS_RESOLVED", "RESOLVED", "SETTLED",
}

TERMINAL_REGULATORY_STATUSES = {
    "DENIED", "REJECTED", "REVOKED", "TERMINATED", "VACATED", "WITHDRAWN",
}

SOCIAL_MARKET_TERMS = {
    "betting", "event contract", "forecast", "forecastex", "kalshi",
    "market making", "odds", "polymarket", "prediction market", "prophetx",
    "sportsbook",
}

SOCIAL_MECHANISM_TERMS = {
    "api", "arbitrage", "calibration", "data", "depth", "execution", "fee",
    "latency", "liquidity", "mechanism", "mispricing", "model", "pricing",
    "rebate", "rule", "settlement", "slippage", "spread", "timestamp",
}

# A CFTC filing table puts the venue/product code in ``Organization`` and the
# economically meaningful text in one of these columns, in this order.
REGULATORY_DESCRIPTION_FIELDS = ("Filing Description", "Product Name",
                                 "Description")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


@dataclass(frozen=True)
class SourceItem:
    id: str
    source_type: str
    source_name: str
    external_id: str
    title: str
    url: str
    published_at: Optional[str]
    retrieved_at: str
    summary: str
    content_hash: str
    topics: List[str] = field(default_factory=list)
    venue_ids: List[str] = field(default_factory=list)
    product_family: str = "*"
    reliability: str = "unrated"
    rights: str = "metadata_and_link"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        source_type: str,
        source_name: str,
        external_id: str,
        title: str,
        url: str,
        retrieved_at: str,
        published_at: Optional[str] = None,
        summary: str = "",
        topics: Sequence[str] = (),
        venue_ids: Sequence[str] = (),
        product_family: str = "*",
        reliability: str = "unrated",
        rights: str = "metadata_and_link",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "SourceItem":
        substance = {
            "title": title.strip(), "summary": summary.strip(),
            "published_at": published_at, "metadata": dict(metadata or {}),
        }
        content_hash = _hash(substance)
        identity = external_id or url or content_hash
        item_id = f"src_{_hash([source_type, source_name, identity])[:20]}"
        return cls(
            id=item_id, source_type=source_type, source_name=source_name,
            external_id=str(identity), title=title.strip(), url=url,
            published_at=published_at, retrieved_at=retrieved_at,
            summary=summary.strip(), content_hash=content_hash,
            topics=sorted(set(str(topic) for topic in topics if topic)),
            venue_ids=sorted(set(str(venue) for venue in venue_ids if venue)),
            product_family=product_family, reliability=reliability,
            rights=rights, metadata=dict(metadata or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchAssignment:
    id: str
    source_item_id: str
    created_at: str
    score: int
    lane: str
    title: str
    source_type: str
    source_name: str
    attribution_key: str
    venue_ids: List[str]
    product_family: str
    legal_decisions: List[Dict[str, Any]]
    assignment: Dict[str, Any]
    may_enter_strategy_registry: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _lane(item: SourceItem) -> str:
    if item.source_type == "venue_market":
        family = item.product_family.lower()
        if family in {"sports", "weather", "science", "technology"}:
            return "information_latency"
        if family in {"crypto", "economics", "financials", "companies"}:
            return "cross_venue"
        return "new_products"
    return {
        "regulatory_filing": "market_structure",
        "venue_market": "new_products",
        "paper": "literature",
        "social": "social_signal",
        "official_data": "information_latency",
        "practitioner": "practitioner_research",
    }.get(item.source_type, "general_research")


def _source_score(item: SourceItem) -> int:
    base = {
        "regulatory_filing": 80,
        "venue_market": 70,
        "official_data": 65,
        "paper": 55,
        "practitioner": 45,
        "social": 35,
    }.get(item.source_type, 30)
    if item.source_type == "regulatory_filing" and "products" in item.topics:
        # An official product filing proves existence and terms provenance. It
        # does not by itself supply a mechanism, edge, or executable capacity.
        base = 45
    if item.source_type == "regulatory_filing" and "organizations" in item.topics:
        # Registration/application metadata is valuable universe discovery,
        # but it contains no market mechanism, executable product, or edge.
        base = 55
    if item.reliability == "primary":
        base += 10
    if item.url:
        base += 3
    if item.source_type == "venue_market":
        raw_activity = _first(item.metadata, "volume", "volumeNum", "liquidity")
        try:
            activity = max(0.0, float(raw_activity))
            base += min(7, int(math.log10(activity + 1)))
        except (TypeError, ValueError):
            pass
    return min(100, base)


def _published_epoch(value: Optional[str]) -> float:
    if not value:
        return 0.0
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            else:
                return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def market_family_key(item: SourceItem) -> str:
    """Return a deterministic research-family key, not a trade identifier."""
    text = "{} {} {}".format(
        item.title, item.summary, item.metadata.get("slug") or "").lower()
    source = re.sub(r"[^a-z0-9]+", "_", item.source_name.lower()).strip("_")
    if any(term in text for term in ("first inning", "yrfi", "nrfi", "kxmlbrfi")):
        return "mlb_first_inning_run"
    if "home run" in text or re.search(r"\bmlbhr\b", text):
        return "mlb_player_home_run"
    census_family = item.metadata.get("market_family")
    if census_family:
        return "{}:census:{}".format(source, str(census_family).lower())
    if item.source_type == "venue_market" and re.search(r"\bvs\.?\b", text):
        return "{}:{}:matchup".format(source, item.product_family.lower())
    if item.source_type == "venue_market":
        return "{}:{}".format(source, item.product_family.lower())
    if item.source_type in {"paper", "social", "practitioner"}:
        return "{}:{}".format(source, item.external_id)
    normalized = re.sub(r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", "date", text)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    tokens = [token for token in normalized.split() if len(token) > 1][:12]
    fallback = "_".join(tokens) or item.external_id or item.id
    return "{}:{}:{}".format(source, item.source_type, fallback)


def regulatory_filing_description_field(
        metadata: Optional[Mapping[str, Any]]) -> str:
    """Return which metadata column carries this filing's description."""
    for key in REGULATORY_DESCRIPTION_FIELDS:
        if str((metadata or {}).get(key) or "").strip():
            return key
    return ""


def regulatory_filing_description(
        metadata: Optional[Mapping[str, Any]]) -> str:
    """Return the mechanism-bearing text a regulatory filing row carries."""
    field_name = regulatory_filing_description_field(metadata)
    return str((metadata or {}).get(field_name) or "").strip() if field_name else ""


def is_opaque_regulatory_title(
    title: Any, metadata: Optional[Mapping[str, Any]] = None,
) -> bool:
    """True when a regulatory title is a bare venue or product code.

    ``COIN``, ``CFE`` and ``MLBEXTRAS1`` name a filer or a ticker; they do not
    name a mechanism, so dispatching one asks a specialist to invent context.
    """
    text = str(title or "").strip()
    if not text:
        return True
    if " " in text:
        return False
    organization = str((metadata or {}).get("Organization") or "").strip()
    if organization and text.casefold() == organization.casefold():
        return True
    return bool(len(text) < 40 and re.fullmatch(r"[A-Z0-9_-]+", text))


def normalize_regulatory_title(
    title: Any, metadata: Optional[Mapping[str, Any]] = None,
) -> str:
    """Promote a filing description over an opaque organization/product code.

    This is DISPLAY-ONLY.  ``SourceItem.content_hash`` covers the title, and the
    intake ledger deduplicates on that hash, so writing the result back into a
    persisted source record would make an already-ingested filing look new.
    Legacy CFTC batches (collected before the collector preferred the
    description column) are corrected by calling this at read time instead.
    """
    text = str(title or "").strip()
    if not is_opaque_regulatory_title(text, metadata):
        return text
    description = regulatory_filing_description(metadata)
    if description and description.casefold() != text.casefold():
        return description
    return text


def research_display_title(
    source_type: Any, title: Any, metadata: Optional[Mapping[str, Any]] = None,
) -> str:
    """Return the title research tooling should show for a source record."""
    if str(source_type or "") == "regulatory_filing":
        return normalize_regulatory_title(title, metadata)
    return str(title or "").strip()


def _memory_match(item: SourceItem,
                  rules: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    text = "{} {} {}".format(item.title, item.summary, item.external_id).lower()
    for rule in rules:
        terms = [str(term).lower() for term in rule.get("match_any") or [] if term]
        if terms and any(term in text for term in terms):
            return rule
    return None


def quality_rejection_reason(
    item: SourceItem,
    *,
    now: datetime,
    memory_rules: Sequence[Mapping[str, Any]] = (),
) -> Optional[str]:
    """Hard rejects for facts that make a research packet non-actionable."""
    status = str(
        item.metadata.get("status") or item.metadata.get("Status") or ""
    ).strip().upper()
    if item.source_type == "venue_market":
        if status in TERMINAL_MARKET_STATUSES:
            return "terminal_market"
        end_at = _parse_datetime(item.metadata.get("end_at"))
        if end_at and end_at <= now:
            return "expired_market"
    if item.source_type == "social":
        text = "{} {}".format(item.title, item.summary).strip().lower()
        if text.startswith("@"):
            return "low_signal_social_reply"
        if not any(term in text for term in SOCIAL_MARKET_TERMS):
            return "social_missing_market_context"
        if not any(term in text for term in SOCIAL_MECHANISM_TERMS):
            return "social_missing_testable_mechanism"
    if item.source_type == "regulatory_filing":
        if status in TERMINAL_REGULATORY_STATUSES:
            return "terminal_regulatory_filing"
        # Judge the title research tooling will actually show.  A legacy batch
        # titled with the filer code is not opaque when the filing description
        # is sitting in its own metadata.
        if is_opaque_regulatory_title(
                normalize_regulatory_title(item.title, item.metadata),
                item.metadata):
            return "opaque_product_code"
    memory = _memory_match(item, memory_rules)
    if memory and str(memory.get("status") or "").lower() in {
            "existing", "killed", "retired", "validated", "paper", "live"}:
        return "prior_research_{}".format(
            str(memory.get("status") or "existing").lower())
    return None


def quality_rejection_from_mapping(
    payload: Mapping[str, Any],
    *,
    now: datetime,
    memory_rules: Sequence[Mapping[str, Any]] = (),
) -> Optional[str]:
    """Apply the SourceItem quality gate to a persisted source payload."""
    try:
        allowed = SourceItem.__dataclass_fields__
        item = SourceItem(**{
            key: value for key, value in dict(payload).items()
            if key in allowed
        })
    except (TypeError, ValueError):
        return None
    return quality_rejection_reason(item, now=now, memory_rules=memory_rules)


def _diverse(items: Sequence[Tuple[SourceItem, int]], limit: int) -> List[Tuple[SourceItem, int]]:
    if len(items) <= limit:
        return list(items)
    cap = max(1, math.ceil(limit * 0.40))
    counts: Dict[str, int] = {}
    selected: List[Tuple[SourceItem, int]] = []
    deferred: List[Tuple[SourceItem, int]] = []
    for item, score in items:
        lane = _lane(item)
        if counts.get(lane, 0) < cap:
            selected.append((item, score))
            counts[lane] = counts.get(lane, 0) + 1
        else:
            deferred.append((item, score))
        if len(selected) == limit:
            return selected
    selected.extend(deferred[:limit - len(selected)])
    return selected


def build_intake(
    items: Sequence[SourceItem],
    *,
    eligibility: EligibilityRegistry,
    previous_ledger: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
    max_assignments: int = 50,
    memory_rules: Sequence[Mapping[str, Any]] = (),
    family_cooldown_days: int = 30,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[ResearchAssignment]]:
    now = now or datetime.now(timezone.utc)
    created_at = _utc_iso(now)
    previous = dict(previous_ledger or {})
    seen_ids = set(previous.get("source_item_ids") or [])
    seen_hashes = set(previous.get("content_hashes") or [])
    batch_ids: set = set()
    batch_hashes: set = set()
    unique: List[SourceItem] = []
    duplicates = 0
    prohibited = 0
    quality_reasons: Counter = Counter()
    quality_examples: List[Dict[str, str]] = []
    for item in items:
        if (item.id in seen_ids or item.content_hash in seen_hashes
                or item.id in batch_ids or item.content_hash in batch_hashes):
            duplicates += 1
            continue
        batch_ids.add(item.id)
        batch_hashes.add(item.content_hash)
        rejection = quality_rejection_reason(
            item, now=now, memory_rules=memory_rules)
        if rejection:
            quality_reasons[rejection] += 1
            if len(quality_examples) < 25:
                quality_examples.append({
                    "source_item_id": item.id,
                    "source_name": item.source_name,
                    "reason": rejection,
                    "title": item.title[:180],
                })
            seen_ids.add(item.id)
            seen_hashes.add(item.content_hash)
            continue
        decisions = [eligibility.decision(venue, item.product_family, as_of=now.date())
                     for venue in item.venue_ids]
        if decisions and not all(decision.research_allowed for decision in decisions):
            prohibited += 1
            continue
        unique.append(item)

    ranked_all = sorted(
        ((item, _source_score(item)) for item in unique),
        key=lambda pair: (-pair[1], -_published_epoch(pair[0].published_at), pair[0].id),
    )
    family_history = {
        str(key): str(value) for key, value in
        (previous.get("family_last_assigned") or {}).items()
    }
    family_cutoff = now - timedelta(days=max(0, family_cooldown_days))
    batch_families: set = set()
    ranked: List[Tuple[SourceItem, int]] = []
    for item, score in ranked_all:
        family = market_family_key(item)
        last = _parse_datetime(family_history.get(family))
        reason = None
        if family in batch_families:
            reason = "duplicate_market_family"
        elif last and last >= family_cutoff:
            reason = "market_family_cooldown"
        if reason:
            quality_reasons[reason] += 1
            if len(quality_examples) < 25:
                quality_examples.append({
                    "source_item_id": item.id, "source_name": item.source_name,
                    "reason": reason, "title": item.title[:180],
                })
            seen_ids.add(item.id)
            seen_hashes.add(item.content_hash)
            continue
        batch_families.add(family)
        ranked.append((item, score))
    selected = _diverse(ranked, max_assignments)
    for item, _ in selected:
        seen_ids.add(item.id)
        seen_hashes.add(item.content_hash)
        family_history[market_family_key(item)] = created_at
    assignments: List[ResearchAssignment] = []
    for item, score in selected:
        decisions = [eligibility.decision(venue, item.product_family, as_of=now.date())
                     .to_dict() for venue in item.venue_ids]
        if item.source_type == "social" and item.metadata.get("username"):
            attribution_key = f"{item.source_name}:@{item.metadata['username']}"
        elif item.source_type == "regulatory_filing" and item.metadata.get("Organization"):
            attribution_key = f"{item.source_name}:{item.metadata['Organization']}"
        else:
            attribution_key = item.source_name
        assignments.append(ResearchAssignment(
            id=f"assignment_{_hash([item.id, created_at])[:20]}",
            source_item_id=item.id,
            created_at=created_at,
            score=score,
            lane=_lane(item),
            title=research_display_title(
                item.source_type, item.title, item.metadata),
            source_type=item.source_type,
            source_name=item.source_name,
            attribution_key=attribution_key,
            venue_ids=item.venue_ids,
            product_family=item.product_family,
            legal_decisions=decisions,
            assignment={
                "agent": {
                    "paper": "literature-scout",
                    "social": "social-scout",
                    "regulatory_filing": "strategy-scout",
                    "venue_market": "strategy-scout",
                }.get(item.source_type, "strategy-scout"),
                "instruction": (
                    "Extract and try to falsify an economically plausible mechanism. "
                    "This source is an idea lead, not evidence of edge."
                ),
                "market_family_key": market_family_key(item),
                "quality_flags": (
                    ["official_listing_is_not_edge_evidence"]
                    if (item.source_type == "regulatory_filing"
                        and ({"products", "organizations"} & set(item.topics)))
                    else []),
            },
        ))

    ledger = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": created_at,
        "source_item_ids": sorted(seen_ids),
        "content_hashes": sorted(seen_hashes),
        "family_last_assigned": dict(sorted(family_history.items())),
        "counts_by_type": {
            kind: sum(item.source_type == kind for item in unique)
            for kind in sorted({item.source_type for item, _ in selected})
        },
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": created_at,
        "received": len(items),
        "new_unique": len(unique),
        "duplicates": duplicates,
        "research_prohibited": prohibited,
        "quality_rejected": sum(quality_reasons.values()),
        "quality_rejection_reasons": dict(sorted(quality_reasons.items())),
        "quality_rejection_examples": quality_examples,
        "assignments": len(assignments),
        "backlog_deferred": len(ranked) - len(assignments),
        "lane_counts": {
            lane: sum(assignment.lane == lane for assignment in assignments)
            for lane in sorted({assignment.lane for assignment in assignments})
        },
        "received_by_type": {
            kind: sum(item.source_type == kind for item in items)
            for kind in sorted({item.source_type for item in items})
        },
        "received_by_source": {
            name: sum(item.source_name == name for item in items)
            for name in sorted({item.source_name for item in items})
        },
        "safety": {
            "creates_opportunity_cards": False,
            "mutates_strategy_registry": False,
            "execution_requires_separate_approval": True,
        },
    }
    return ledger, manifest, assignments


def market_census_items(payload: Mapping[str, Any], *, retrieved_at: str) -> List[SourceItem]:
    """Bridge existing Kalshi census seeds into the unified intake."""
    run_id = str(payload.get("run_id") or "unknown")
    items: List[SourceItem] = []
    for seed in payload.get("seeds") or []:
        if not isinstance(seed, Mapping):
            continue
        seed_id = str(seed.get("id") or "")
        family = str(seed.get("market_family") or "")
        if not seed_id or not family:
            continue
        evidence = dict(seed.get("evidence") or {})
        items.append(SourceItem.create(
            source_type="venue_market", source_name="Kalshi market census",
            external_id=seed_id,
            title=str(seed.get("title") or family),
            url=str(evidence.get("contract_terms_url") or ""),
            published_at=seed.get("generated_at"), retrieved_at=retrieved_at,
            summary=f"Kalshi census seed {seed_id} from {run_id}",
            topics=[str(seed.get("lane") or "new_products")],
            venue_ids=["kalshi"],
            product_family=str(evidence.get("category") or "*").lower(),
            reliability="primary",
            metadata={
                "census_run_id": run_id, "candidate_seed_id": seed_id,
                "market_family": family,
                "rank": seed.get("rank"), "score": seed.get("score"),
                "reason_codes": seed.get("reason_codes") or [],
            },
        ))
    return items


class FeedCollector:
    """Minimal RSS/Atom metadata collector; does not archive copyrighted text."""

    def __init__(self, source_name: str, source_type: str = "paper"):
        self.source_name = source_name
        self.source_type = source_type

    def parse(self, xml_text: str, *, retrieved_at: str) -> List[SourceItem]:
        root = ET.fromstring(xml_text)
        entries = root.findall(".//item")
        atom = False
        if not entries:
            entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
            atom = True
        out: List[SourceItem] = []
        for entry in entries:
            if atom:
                ns = "{http://www.w3.org/2005/Atom}"
                title = entry.findtext(f"{ns}title") or "Untitled"
                external_id = entry.findtext(f"{ns}id") or ""
                published = (entry.findtext(f"{ns}published")
                             or entry.findtext(f"{ns}updated"))
                summary = entry.findtext(f"{ns}summary") or ""
                link_node = entry.find(f"{ns}link")
                url = link_node.get("href", "") if link_node is not None else ""
            else:
                title = entry.findtext("title") or "Untitled"
                external_id = entry.findtext("guid") or ""
                published = entry.findtext("pubDate")
                summary = entry.findtext("description") or ""
                url = entry.findtext("link") or ""
            out.append(SourceItem.create(
                source_type=self.source_type, source_name=self.source_name,
                external_id=external_id, title=title, url=url,
                published_at=published, retrieved_at=retrieved_at,
                summary=summary, reliability="primary",
            ))
        return out

    def fetch(self, url: str, *, timeout: float = 30.0,
              now: Optional[datetime] = None) -> List[SourceItem]:
        response = requests.get(url, timeout=timeout,
                                headers={"User-Agent": "betting-pod-shop/research-intake"})
        response.raise_for_status()
        return self.parse(response.text, retrieved_at=_utc_iso(
            now or datetime.now(timezone.utc)))


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables: List[List[List[Dict[str, Any]]]] = []
        self._table: Optional[List[List[Dict[str, Any]]]] = None
        self._row: Optional[List[Dict[str, Any]]] = None
        self._cell: Optional[Dict[str, Any]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = {"text": "", "header": tag == "th", "href": ""}
        elif tag == "a" and self._cell is not None:
            self._cell["href"] = attrs_dict.get("href") or ""

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._cell["text"] = " ".join(self._cell["text"].split())
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


class CFTCCollector:
    BASE = "https://www.cftc.gov"
    PATHS = {
        "products": "/IndustryOversight/IndustryFilings/TradingOrganizationProducts",
        "rules": "/IndustryOversight/IndustryFilings/TradingOrganizationRules",
        "organizations": "/IndustryOversight/IndustryFilings/TradingOrganizations",
    }
    VENUE_IDS = {
        "KEX": "kalshi",
        "QCEX": "polymarket_us",
        "QCX": "polymarket_us",
        "FEX": "forecastex",
        "CME": "cme_event_contracts",
        "COMEX": "cme_event_contracts",
        "CBOT": "cme_event_contracts",
        "NYMEX": "cme_event_contracts",
        "PROPHETX": "prophetx",
    }

    def parse(self, html_text: str, *, kind: str, retrieved_at: str) -> List[SourceItem]:
        parser = _TableParser()
        parser.feed(html_text)
        output: List[SourceItem] = []
        for table in parser.tables:
            if len(table) < 2:
                continue
            headers = [cell["text"] for cell in table[0]]
            if not any(name in headers for name in ("Organization", "Product Name")):
                continue
            for cells in table[1:]:
                values = {headers[i]: cell["text"] for i, cell in enumerate(cells)
                          if i < len(headers)}
                links = [cell["href"] for cell in cells if cell.get("href")]
                # The CFTC rules table commonly puts only the venue code in
                # ``Organization`` while the economically meaningful text is
                # in ``Filing Description``.  Dispatching the code ("CFE",
                # "MIAX") asks a model to invent a mechanism from no context.
                # Route through the shared rule so a freshly collected filing
                # and a legacy one already on disk resolve to the same title.
                title = normalize_regulatory_title(
                    _first(values, *REGULATORY_DESCRIPTION_FIELDS,
                           "Organization") or "CFTC filing", values)
                organization = str(values.get("Organization") or "CFTC")
                venue_id = self.VENUE_IDS.get(organization.upper())
                published = _first(values, "Date", "Official Receipt Date", "Receipt Date")
                url = urljoin(self.BASE, links[0]) if links else urljoin(
                    self.BASE, self.PATHS[kind])
                # Keep EVERY document link, not just the first. A rules row
                # routinely carries two, and dropping the rest left a
                # specialist told a rule change exists with no way to read it.
                documents = []
                for href in links:
                    absolute = urljoin(self.BASE, href)
                    if absolute not in documents:
                        documents.append(absolute)
                if documents:
                    values = dict(values, documents=documents)
                output.append(SourceItem.create(
                    source_type="regulatory_filing", source_name="CFTC",
                    external_id=url or _hash(values), title=str(title), url=url,
                    published_at=str(published) if published else None,
                    retrieved_at=retrieved_at,
                    summary=(
                        str(title) if str(title).startswith(f"{organization}:")
                        else f"{organization}: {title}"
                    ),
                    topics=[kind], venue_ids=[venue_id] if venue_id else [],
                    product_family=str(values.get("Category") or "*").lower(),
                    reliability="primary", metadata=values,
                ))
        return output

    def fetch(self, kind: str, *, timeout: float = 30.0,
              now: Optional[datetime] = None) -> List[SourceItem]:
        if kind not in self.PATHS:
            raise ValueError(f"unknown CFTC collection kind: {kind}")
        response = requests.get(
            urljoin(self.BASE, self.PATHS[kind]), timeout=timeout,
            headers={"User-Agent": "betting-pod-shop/research-intake"},
        )
        response.raise_for_status()
        return self.parse(response.text, kind=kind, retrieved_at=_utc_iso(
            now or datetime.now(timezone.utc)))


class PolymarketUSCollector:
    """Public, read-only Polymarket US market discovery."""

    def __init__(self, session: Optional[Any] = None,
                 base_url: str = "https://gateway.polymarket.us"):
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.truncated = False

    def collect(self, *, limit: int = 100, max_pages: int = 10,
                now: Optional[datetime] = None) -> List[SourceItem]:
        retrieved_at = _utc_iso(now or datetime.now(timezone.utc))
        out: List[SourceItem] = []
        self.truncated = False
        for page in range(max_pages):
            response = self.session.get(
                f"{self.base_url}/v1/markets",
                params={"active": "true", "limit": limit, "offset": page * limit},
                timeout=30.0,
                headers={"User-Agent": "betting-pod-shop/research-intake"},
            )
            response.raise_for_status()
            body = response.json()
            rows = body.get("markets") or body.get("data") or []
            if isinstance(rows, Mapping):
                rows = list(rows.values())
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                slug = str(_first(row, "slug", "marketSlug", "id") or "")
                if not slug:
                    continue
                title = str(_first(row, "question", "title", "name") or slug)
                category = str(_first(row, "category", "categorySlug") or "unknown")
                url = f"https://polymarket.us/market/{slug}"
                out.append(SourceItem.create(
                    source_type="venue_market", source_name="Polymarket US",
                    external_id=slug, title=title, url=url,
                    published_at=_first(row, "createdAt", "created_at", "startDate"),
                    retrieved_at=retrieved_at, summary=title,
                    topics=[category], venue_ids=["polymarket_us"],
                    product_family=category.lower(), reliability="primary",
                    metadata={
                        "slug": slug,
                        "category": category,
                        "end_at": _first(row, "endDate", "end_date", "closeTime"),
                        "status": _first(row, "status", "state"),
                        "volume": _first(row, "volumeNum", "volume", "volume24hr"),
                        "liquidity": _first(row, "liquidityNum", "liquidity"),
                    },
                ))
            if len(rows) < limit:
                break
            if page + 1 < max_pages:
                time.sleep(0.06)
        else:
            self.truncated = bool(out)
        return out


class XRecentSearchCollector:
    """Explicit opt-in X recent-search collector; never called by default."""

    URL = "https://api.x.com/2/tweets/search/recent"

    def __init__(self, bearer_token: str, session: Optional[Any] = None):
        if not bearer_token:
            raise ValueError("X bearer token is required")
        self.bearer_token = bearer_token
        self.session = session or requests.Session()

    def collect(self, query: str, *, max_results: int = 100,
                now: Optional[datetime] = None) -> List[SourceItem]:
        if not 10 <= max_results <= 100:
            raise ValueError("X max_results must be between 10 and 100")
        response = self.session.get(
            self.URL,
            params={
                "query": query, "max_results": max_results,
                "tweet.fields": "author_id,created_at,lang,public_metrics",
                "expansions": "author_id", "user.fields": "username,name",
            },
            headers={"Authorization": f"Bearer {self.bearer_token}"},
            timeout=30.0,
        )
        response.raise_for_status()
        body = response.json()
        users = {str(user.get("id")): user for user in
                 (body.get("includes", {}).get("users") or [])}
        retrieved_at = _utc_iso(now or datetime.now(timezone.utc))
        out: List[SourceItem] = []
        for post in body.get("data") or []:
            post_id = str(post.get("id") or "")
            if not post_id:
                continue
            user = users.get(str(post.get("author_id")), {})
            username = str(user.get("username") or post.get("author_id") or "unknown")
            text = str(post.get("text") or "")
            out.append(SourceItem.create(
                source_type="social", source_name="X",
                external_id=post_id, title=text[:180] or f"Post {post_id}",
                url=f"https://x.com/{username}/status/{post_id}",
                published_at=post.get("created_at"), retrieved_at=retrieved_at,
                summary=text[:500], topics=[query], reliability="unrated",
                rights="post_id_link_and_short_excerpt",
                metadata={
                    "author_id": post.get("author_id"), "username": username,
                    "metrics": post.get("public_metrics") or {},
                },
            ))
        return out
