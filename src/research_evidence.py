"""Attach measured, public evidence to a research dispatch packet.

Every model-produced disposition so far has terminated on absent evidence
rather than on falsification -- ``fees_unknown``, ``contract_terms_unavailable``,
``execution_friction_unknown``, ``no_timestamp_correct_dataset``.  The packet's
completion contract demands fees, depth, settlement rules and an eligibility
decision, and the packet shipped none of them, while the screening stage is
told not to browse.  A screen asked to price a market it cannot see can only
defer.

This module resolves the facts the repository already owns -- the generated
Kalshi fee fixture, the public market/orderbook endpoints, the filing document
links a CFTC row carries -- and hands them to the specialist as measured
observations with a timestamp.

Three rules hold everywhere in here:

* **Fail soft.** A resolver never raises into the pipeline.  A network problem
  yields ``status="unavailable"`` with a reason; triage still dispatches.
* **Measured is not derived.** These are observations, not a verdict.  Depth is
  not capacity and a spread is not an edge, so ``capacity``/``net_edge`` stay
  unknown in the scorecard and the triage score is untouched.
* **Timestamped.** Every pack records ``measured_at``, because a quote read
  yesterday is not evidence about today.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Protocol
from urllib.parse import urljoin

from src.kalshi_fees import (
    MAKER_COEF,
    TAKER_COEF,
    fee_per_contract,
    series_maker_charges_fee,
)


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# A representative quote is only worth shipping if it is a real two-sided
# market.  Below this, the "spread" is an artifact of an empty book.
MIN_QUOTE_SIZE = 1.0


def _utc_iso(value: Optional[datetime] = None) -> str:
    value = value or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _round(value: Optional[float], places: int = 4) -> Optional[float]:
    return None if value is None else round(float(value), places)


def _pack(status: str, source: str, *, facts: Optional[Mapping[str, Any]] = None,
          notes: Optional[List[str]] = None, now: Optional[datetime] = None,
          ) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "source": source,
        "measured_at": _utc_iso(now),
        "facts": dict(facts or {}),
        "notes": list(notes or []),
        "semantics": {
            "is_edge_evidence": False,
            "authorizes_execution": False,
            "capacity_measured": False,
            "note": ("Observations only. Depth is not capacity and a spread is "
                     "not an edge; both remain unknown until modelled."),
        },
    }


class EvidenceResolver(Protocol):
    name: str

    def applies_to(self, assignment: Mapping[str, Any],
                   source_item: Mapping[str, Any]) -> bool: ...

    def resolve(self, assignment: Mapping[str, Any],
                source_item: Mapping[str, Any],
                *, now: Optional[datetime] = None) -> Dict[str, Any]: ...


class KalshiEvidenceResolver:
    """Fee class, live quote and contract terms for a Kalshi series.

    The census bridge stores the series ticker in ``metadata.market_family``
    (``KXASEANSPREAD``), which is the only durable handle a census seed gives
    onto the traded product.
    """

    name = "kalshi_public+kalshi_fees"

    def __init__(self, client: Optional[Any] = None, *,
                 max_markets: int = 200) -> None:
        self._client = client
        self.max_markets = max_markets

    def _ensure_client(self):
        if self._client is None:
            from src.kalshi_public import KalshiPublic

            self._client = KalshiPublic()
        return self._client

    @staticmethod
    def series_ticker(assignment: Mapping[str, Any],
                      source_item: Mapping[str, Any]) -> str:
        metadata = source_item.get("metadata") or {}
        candidate = str(metadata.get("market_family") or "").strip().upper()
        if re.fullmatch(r"[A-Z0-9-]{2,}", candidate):
            return candidate
        return ""

    def applies_to(self, assignment: Mapping[str, Any],
                   source_item: Mapping[str, Any]) -> bool:
        venues = {str(value).lower() for value in
                  (assignment.get("venue_ids") or source_item.get("venue_ids") or [])}
        return ("kalshi" in venues
                and bool(self.series_ticker(assignment, source_item)))

    def resolve(self, assignment: Mapping[str, Any],
                source_item: Mapping[str, Any],
                *, now: Optional[datetime] = None) -> Dict[str, Any]:
        series = self.series_ticker(assignment, source_item)
        if not series:
            return _pack("not_applicable", self.name, now=now,
                         notes=["no series ticker on the source record"])
        # The fee classification is offline and always available, so it is
        # resolved before anything that can fail on the network.
        charges = series_maker_charges_fee(series)
        facts: Dict[str, Any] = {
            "series_ticker": series,
            "fees": {
                "maker_charges_fee": charges,
                "maker_rate_coefficient": MAKER_COEF if charges else 0.0,
                "taker_rate_coefficient": TAKER_COEF,
                "fee_at_50c_taker_usd": _round(fee_per_contract(0.50), 6),
                "fee_at_50c_maker_usd": _round(
                    fee_per_contract(0.50, maker=True, series_ticker=series), 6),
                "formula": "taker 0.07*P*(1-P); maker 0.0175*P*(1-P) or 0",
                "source": "src/fixtures/kalshi_series_fees.json (exact match)",
            },
        }
        notes: List[str] = []
        try:
            client = self._ensure_client()
            markets = client.open_markets(series) or []
        except Exception as exc:                      # never break triage
            logger.warning("kalshi evidence: open_markets(%s) failed: %s",
                           series, exc)
            facts["markets"] = {"error": str(exc)[:200]}
            return _pack("partial", self.name, facts=facts, now=now,
                         notes=notes + ["fee class measured; market data "
                                        "unavailable this run"])
        facts["markets"] = {
            "open_count": len(markets),
            "tickers_sample": [str(row.get("ticker") or "")
                               for row in markets[:5] if isinstance(row, Mapping)],
        }
        if not markets:
            notes.append("series has no OPEN markets right now; it may be "
                         "listed-but-dormant or between events")
            return _pack("measured", self.name, facts=facts, now=now, notes=notes)

        quote = self._representative_quote(markets, now=now)
        if quote:
            facts["representative_quote"] = quote
        else:
            notes.append("no two-sided quote of size found in the open markets "
                         "sampled; the book may be empty")
        terms = self._contract_terms(series)
        if terms:
            facts["contract_terms"] = terms
        else:
            notes.append("series metadata did not carry a contract_terms_url")
        return _pack("measured", self.name, facts=facts, now=now, notes=notes)

    def _representative_quote(self, markets, *, now) -> Optional[Dict[str, Any]]:
        """Best two-sided quote among the most active open markets.

        Sorted by volume so the sample describes where the series actually
        trades rather than whichever ticker sorted first.
        """
        client = self._ensure_client()

        def _volume(row: Mapping[str, Any]) -> float:
            try:
                return float(row.get("volume") or row.get("volume_24h") or 0)
            except (TypeError, ValueError):
                return 0.0

        ranked = sorted(
            (row for row in markets if isinstance(row, Mapping) and row.get("ticker")),
            key=lambda row: (-_volume(row), str(row.get("ticker"))))
        for row in ranked[:3]:
            ticker = str(row["ticker"])
            try:
                book = client.orderbook(ticker)
            except Exception as exc:
                logger.warning("kalshi evidence: orderbook(%s) failed: %s",
                               ticker, exc)
                continue
            if not book:
                continue
            bid, ask = book.get("yes_bid"), book.get("yes_ask")
            bid_qty = float(book.get("bid_qty") or 0)
            ask_qty = float(book.get("ask_qty") or 0)
            if bid is None or ask is None:
                continue
            if bid_qty < MIN_QUOTE_SIZE or ask_qty < MIN_QUOTE_SIZE:
                continue
            spread = float(ask) - float(bid)
            mid = (float(ask) + float(bid)) / 2.0
            return {
                "ticker": ticker,
                "observed_at": _utc_iso(now),
                "yes_bid": _round(bid),
                "yes_ask": _round(ask),
                "mid": _round(mid),
                "spread": _round(spread),
                "half_spread": _round(spread / 2.0),
                "bid_size": bid_qty,
                "ask_size": ask_qty,
                "volume": _volume(row),
                "taker_fee_at_mid_usd": _round(fee_per_contract(mid), 6),
                "round_trip_cost_at_mid_usd": _round(
                    spread / 2.0 + fee_per_contract(mid), 6),
                "note": ("One quote at one instant. It bounds the friction a "
                         "taker pays now; it is not a depth or capacity study."),
            }
        return None

    def _contract_terms(self, series: str) -> Optional[Dict[str, Any]]:
        try:
            payload = self._ensure_client().get(f"/series/{series}")
        except Exception as exc:
            logger.warning("kalshi evidence: series(%s) failed: %s", series, exc)
            return None
        row = (payload or {}).get("series") or {}
        url = str(row.get("contract_terms_url") or "").strip()
        if not url:
            return None
        return {
            "contract_terms_url": url,
            "category": str(row.get("category") or ""),
            "fee_type": str(row.get("fee_type") or ""),
            "note": ("Read the terms document itself. Ticker names group "
                     "opposite settlement regimes under similar prefixes."),
        }


class CFTCFilingEvidenceResolver:
    """Document links for a CFTC filing row.

    The collector records how many documents a filing has but kept only the
    first link, so a specialist was told a rule change exists and given no way
    to read it -- which is verbatim what the 2026-08-08 screen deferred on.
    """

    name = "cftc_filing_documents"
    DOCUMENT_SUFFIXES = (".pdf", ".doc", ".docx", ".htm", ".html")

    def __init__(self, session: Optional[Any] = None,
                 base: str = "https://www.cftc.gov") -> None:
        self._session = session
        self.base = base

    def applies_to(self, assignment: Mapping[str, Any],
                   source_item: Mapping[str, Any]) -> bool:
        return str(assignment.get("source_type")
                   or source_item.get("source_type") or "") == "regulatory_filing"

    def resolve(self, assignment: Mapping[str, Any],
                source_item: Mapping[str, Any],
                *, now: Optional[datetime] = None) -> Dict[str, Any]:
        metadata = source_item.get("metadata") or {}
        filing_url = str(source_item.get("url") or "").strip()
        facts: Dict[str, Any] = {
            "filing_url": filing_url,
            "organization": str(metadata.get("Organization") or ""),
            "status": str(metadata.get("Status") or ""),
            "filing_description": str(metadata.get("Filing Description") or ""),
            "receipt_date": str(metadata.get("Receipt Date")
                                or metadata.get("Date") or ""),
            "document_count_reported": metadata.get("Documents"),
        }
        recorded = [str(value) for value in (metadata.get("documents") or [])
                    if str(value).strip()]
        if recorded:
            facts["documents"] = recorded
            return _pack("measured", self.name, facts=facts, now=now,
                         notes=["document links recorded at collection time"])
        if not filing_url:
            return _pack("unavailable", self.name, facts=facts, now=now,
                         notes=["no filing URL on the source record"])
        links = self._fetch_documents(filing_url)
        if links is None:
            return _pack("partial", self.name, facts=facts, now=now,
                         notes=["filing metadata measured; the filing page "
                                "could not be read this run"])
        facts["documents"] = links
        notes = ["document links resolved from the filing page"]
        if not links:
            notes = ["the filing page exposed no document links; the text may "
                     "not be public yet"]
        return _pack("measured", self.name, facts=facts, now=now, notes=notes)

    def _fetch_documents(self, filing_url: str) -> Optional[List[str]]:
        try:
            if self._session is None:
                import requests

                self._session = requests.Session()
            response = self._session.get(
                filing_url, timeout=30.0,
                headers={"User-Agent": "betting-pod-shop/research-intake"})
            response.raise_for_status()
            html = response.text
        except Exception as exc:
            logger.warning("cftc evidence: %s failed: %s", filing_url, exc)
            return None
        seen: List[str] = []
        for href in re.findall(r'href=["\']([^"\']+)["\']', html):
            if not href.lower().endswith(self.DOCUMENT_SUFFIXES):
                continue
            absolute = urljoin(self.base, href)
            if absolute not in seen:
                seen.append(absolute)
        return seen[:12]


DEFAULT_RESOLVERS: tuple = (KalshiEvidenceResolver, CFTCFilingEvidenceResolver)


def build_resolvers(enabled: bool = True) -> List[EvidenceResolver]:
    return [factory() for factory in DEFAULT_RESOLVERS] if enabled else []


def resolve_evidence(
    assignment: Mapping[str, Any],
    source_item: Mapping[str, Any],
    resolvers: Optional[List[EvidenceResolver]] = None,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return the evidence pack for one packet, never raising."""
    for resolver in resolvers or []:
        try:
            if not resolver.applies_to(assignment, source_item):
                continue
            return resolver.resolve(assignment, source_item, now=now)
        except Exception as exc:                      # never break triage
            logger.warning("evidence resolver %s failed: %s",
                           getattr(resolver, "name", resolver), exc)
            return _pack("unavailable", getattr(resolver, "name", "unknown"),
                         now=now, notes=[f"resolver raised: {str(exc)[:200]}"])
    return _pack("not_applicable", "none", now=now,
                 notes=["no resolver covers this source type"])


def supplied_evidence_keys(pack: Mapping[str, Any]) -> List[str]:
    """Flat list of what a pack actually carries, for the agent contract."""
    if str(pack.get("status") or "") in {"not_applicable", "unavailable"}:
        return []
    out: List[str] = []
    facts = pack.get("facts") or {}
    for key, value in facts.items():
        if value in (None, "", [], {}):
            continue
        out.append(key)
    return sorted(out)
