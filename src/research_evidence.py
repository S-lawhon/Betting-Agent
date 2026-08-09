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
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence
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
    """Filing documents for a CFTC rule/product row.

    A rules row carries a document COUNT and a single link, and that link is
    the filing detail page, not the filing. The text lives in PDFs on that
    page. Without them a specialist is told a rule change exists and given no
    way to read it -- verbatim what the 2026-08-08 screen deferred on.

    Extensions are matched narrowly on purpose. Accepting ``.htm``/``.html``
    matched cftc.gov's own navigation chrome (``/About/index.htm``,
    ``/Contact/index.htm``) and returned twelve nav links and zero filings,
    which is worse than returning nothing: it would have filled the model's
    context with noise while still answering none of its questions.
    """

    name = "cftc_filing_documents"
    DOCUMENT_PATTERN = re.compile(r"\.(pdf|docx?|xlsx?)(?:\?|#|$)", re.I)
    ANCHOR_PATTERN = re.compile(
        r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
    TAG_PATTERN = re.compile(r"<[^>]+>")

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
        if not filing_url:
            return _pack("unavailable", self.name, facts=facts, now=now,
                         notes=["no filing URL on the source record"])
        documents = self._fetch_documents(filing_url)
        if documents is None:
            return _pack("partial", self.name, facts=facts, now=now,
                         notes=["filing metadata measured; the filing page "
                                "could not be read this run"])
        facts["documents"] = documents
        if not documents:
            return _pack("measured", self.name, facts=facts, now=now, notes=[
                "the filing page exposed no document files; the text may not "
                "be public yet"])
        notes = ["document files resolved from the filing page; titles are the "
                 "filer's own and distinguish the rule text from procedural "
                 "attachments such as a FOIA request"]
        # The row states how many documents the filing has. A disagreement
        # means the page changed shape and the extraction is incomplete --
        # say so rather than letting a partial read look authoritative.
        try:
            reported = int(str(facts.get("document_count_reported") or "").strip())
        except (TypeError, ValueError):
            reported = None
        if reported is not None and reported != len(documents):
            notes.append(
                f"the filing row reports {reported} documents but "
                f"{len(documents)} were extracted; treat this list as partial")
        return _pack("measured", self.name, facts=facts, now=now, notes=notes)

    def _fetch_documents(
            self, filing_url: str) -> Optional[List[Dict[str, str]]]:
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
        documents: List[Dict[str, str]] = []
        seen: set = set()
        for href, inner in self.ANCHOR_PATTERN.findall(html):
            if not self.DOCUMENT_PATTERN.search(href):
                continue
            absolute = urljoin(self.base, href)
            if absolute in seen:
                continue
            seen.add(absolute)
            title = " ".join(self.TAG_PATTERN.sub(" ", inner).split())
            documents.append({"url": absolute, "title": title[:200]})
            if len(documents) >= 12:
                break
        return documents


class ArxivEvidenceResolver:
    """Version, replication artifact and full text for an arXiv paper.

    The RSS collector stores an abstract and nothing else, so a scout is asked
    whether a mechanism is testable without being told whether the authors
    published code, whether the paper has since been replaced, or where the
    full text is.  All three come free from arXiv's public API.
    """

    name = "arxiv_api"
    # HTTPS directly: the http:// form 301s, and the redirect hop was enough
    # to push a cold request past a 30s budget.
    API = "https://export.arxiv.org/api/query"
    # Measured 28s on a cold request, then sub-second. arXiv also asks callers
    # to leave ~3s between requests, which a handful of papers a day makes
    # free to honour.
    TIMEOUT_SECONDS = 60.0
    MIN_INTERVAL_SECONDS = 3.0
    ATOM = "{http://www.w3.org/2005/Atom}"
    ARXIV = "{http://arxiv.org/schemas/atom}"
    ID_PATTERN = re.compile(r"(\d{4}\.\d{4,5})(?:v(\d+))?")
    CODE_PATTERN = re.compile(
        r"https?://(?:www\.)?(?:github\.com|gitlab\.com|zenodo\.org|"
        r"osf\.io|codeocean\.com|huggingface\.co)/[^\s,;)\]]+", re.I)

    def __init__(self, session: Optional[Any] = None, sleep=None) -> None:
        self._session = session
        self._sleep = sleep if sleep is not None else time.sleep
        self._last_call = 0.0

    @classmethod
    def arxiv_id(cls, source_item: Mapping[str, Any]) -> tuple:
        """Return ``(bare_id, ingested_version)`` for an arXiv record."""
        for field in ("external_id", "url"):
            match = cls.ID_PATTERN.search(str(source_item.get(field) or ""))
            if match:
                return match.group(1), int(match.group(2) or 0)
        return "", 0

    def applies_to(self, assignment: Mapping[str, Any],
                   source_item: Mapping[str, Any]) -> bool:
        source_type = str(assignment.get("source_type")
                          or source_item.get("source_type") or "")
        if source_type != "paper":
            return False
        return bool(self.arxiv_id(source_item)[0])

    def resolve(self, assignment: Mapping[str, Any],
                source_item: Mapping[str, Any],
                *, now: Optional[datetime] = None) -> Dict[str, Any]:
        paper_id, ingested_version = self.arxiv_id(source_item)
        if not paper_id:
            return _pack("not_applicable", self.name, now=now,
                         notes=["no arXiv identifier on the source record"])
        facts: Dict[str, Any] = {
            "arxiv_id": paper_id,
            "abstract_url": f"https://arxiv.org/abs/{paper_id}",
            "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
            "ingested_version": ingested_version or None,
        }
        entry = self._fetch(paper_id)
        if entry is None:
            return _pack("partial", self.name, facts=facts, now=now, notes=[
                "identifier and full-text location resolved; arXiv metadata "
                "was unavailable this run"])
        facts.update(entry)
        notes: List[str] = []
        latest = int(facts.get("latest_version") or 0)
        if ingested_version and latest and latest > ingested_version:
            notes.append(
                f"this record was ingested at v{ingested_version} but arXiv is "
                f"now at v{latest}; read the current version, its conclusions "
                f"may have moved")
        if facts.get("code_links"):
            notes.append("a replication artifact is linked; reproducing the "
                         "result is a test, not a research project")
        else:
            notes.append("no replication artifact found in the abstract or "
                         "author comment; any decisive test must be rebuilt "
                         "from the paper")
        return _pack("measured", self.name, facts=facts, now=now, notes=notes)

    def _fetch(self, paper_id: str) -> Optional[Dict[str, Any]]:
        try:
            if self._session is None:
                import requests

                self._session = requests.Session()
            elapsed = time.monotonic() - self._last_call
            if self._last_call and elapsed < self.MIN_INTERVAL_SECONDS:
                self._sleep(self.MIN_INTERVAL_SECONDS - elapsed)
            self._last_call = time.monotonic()
            response = self._session.get(
                self.API, params={"id_list": paper_id, "max_results": 1},
                timeout=self.TIMEOUT_SECONDS,
                headers={"User-Agent": "betting-pod-shop/research-intake"})
            response.raise_for_status()
            root = ET.fromstring(response.text)
        except Exception as exc:
            logger.warning("arxiv evidence: %s failed: %s", paper_id, exc)
            return None
        entry = root.find(f"{self.ATOM}entry")
        if entry is None:
            return None

        def text(tag: str) -> str:
            node = entry.find(tag)
            return " ".join((node.text or "").split()) if node is not None else ""

        abstract = text(f"{self.ATOM}summary")
        comment = text(f"{self.ARXIV}comment")
        latest_version = 0
        canonical = text(f"{self.ATOM}id")
        match = self.ID_PATTERN.search(canonical)
        if match and match.group(2):
            latest_version = int(match.group(2))
        code_links: List[str] = []
        for candidate in self.CODE_PATTERN.findall(f"{abstract} {comment}"):
            if candidate not in code_links:
                code_links.append(candidate)
        return {
            "title": text(f"{self.ATOM}title"),
            "abstract": abstract,
            "author_comment": comment,
            "journal_ref": text(f"{self.ARXIV}journal_ref"),
            "doi": text(f"{self.ARXIV}doi"),
            "published": text(f"{self.ATOM}published"),
            "updated": text(f"{self.ATOM}updated"),
            "latest_version": latest_version or None,
            "categories": sorted({
                node.get("term") for node in entry.findall(f"{self.ATOM}category")
                if node.get("term")}),
            "code_links": code_links,
        }


class SocialEvidenceResolver:
    """Resolve a social lead against the venues we are allowed to research.

    Deliberately makes NO network call.  These records carry
    ``rights="post_id_link_and_short_excerpt"``, and the X budget is capped, so
    re-fetching a post to enrich it would spend a scarce, rights-limited
    resource to learn nothing new.  Everything below is derived from the text
    already in hand plus the venue registry.

    The decisive fact is usually venue coverage: a post promoting a Solana
    prediction market names no venue this fund can research, which a specialist
    can act on in seconds instead of writing up a mechanism for a venue that
    was never reachable.
    """

    name = "social_venue_registry"
    # Written-form aliases for the ids in config/research_venues.yaml.
    VENUE_ALIASES = {
        "kalshi": ("kalshi",),
        "polymarket_us": ("polymarket us", "polymarket.us"),
        "polymarket_international": ("polymarket international",),
        "forecastex": ("forecastex", "forecast ex"),
        "cme_event_contracts": ("cme", "comex", "cbot", "nymex"),
        "prophetx": ("prophetx", "prophet x"),
        "novig": ("novig",),
        "underdog_predict": ("underdog",),
        "texas_parimutuel": ("texas parimutuel",),
    }
    # "Polymarket" unqualified spans a research-allowed venue and a prohibited
    # one, so it is never silently resolved to either.
    AMBIGUOUS = {"polymarket": ("polymarket_us", "polymarket_international")}
    CALL_TO_ACTION = re.compile(
        r"\b(check it|check out|sign up|join|try it|trade now|download|"
        r"get started|link in bio)\b", re.I)
    MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_]{2,30})")
    URL_PATTERN = re.compile(r"https?://([^\s/]+)")

    def __init__(self, registry: Optional[Any] = None) -> None:
        self.registry = registry

    def applies_to(self, assignment: Mapping[str, Any],
                   source_item: Mapping[str, Any]) -> bool:
        return str(assignment.get("source_type")
                   or source_item.get("source_type") or "") == "social"

    def resolve(self, assignment: Mapping[str, Any],
                source_item: Mapping[str, Any],
                *, now: Optional[datetime] = None) -> Dict[str, Any]:
        metadata = source_item.get("metadata") or {}
        text = "{} {}".format(
            source_item.get("title") or "", source_item.get("summary") or "")
        lowered = text.lower()

        matched: List[str] = []
        ambiguous: List[str] = []
        for venue_id, aliases in self.VENUE_ALIASES.items():
            if any(alias in lowered for alias in aliases):
                matched.append(venue_id)
        for term, candidates in self.AMBIGUOUS.items():
            if term in lowered and not any(c in matched for c in candidates):
                ambiguous.append(term)

        author = str(metadata.get("username") or "")
        mentions = [name for name in self.MENTION_PATTERN.findall(text)
                    if name.lower() != author.lower()]
        hosts = sorted({host.lower()
                        for host in self.URL_PATTERN.findall(text)})
        markers: List[str] = []
        if hosts:
            markers.append("contains_outbound_link")
        if mentions:
            markers.append("mentions_other_account")
        if self.CALL_TO_ACTION.search(text):
            markers.append("call_to_action")

        facts: Dict[str, Any] = {
            "post_url": str(source_item.get("url") or ""),
            "author": author,
            "registered_venues_named": sorted(matched),
            "venue_decisions": self._decisions(matched, now=now),
            "ambiguous_venue_terms": sorted(ambiguous),
            "mentioned_accounts": sorted(set(mentions)),
            "linked_hosts": hosts,
            "promotional_markers": markers,
            "public_metrics": dict(metadata.get("metrics") or {}),
            "metrics_note": (
                "Audience size, not truth. Engagement is explicitly not edge "
                "evidence; it says how many people saw a claim, never whether "
                "the claim holds."),
        }
        notes: List[str] = []
        if not matched and not ambiguous:
            notes.append("this post names no venue in the research registry; "
                         "any mechanism in it may not be reachable from any "
                         "venue this fund can trade or study")
        if ambiguous:
            notes.append("'polymarket' is named without a US/international "
                         "qualifier and those differ in eligibility; resolve "
                         "which before relying on it")
        if "t.co" in hosts:
            notes.append("t.co is a shortener; the destination was NOT "
                         "resolved and could be any venue")
        if len(markers) >= 2:
            notes.append("this reads as promotional rather than analytical; "
                         "markers are listed, not scored -- judge the claim")
        return _pack("measured", self.name, facts=facts, now=now, notes=notes)

    def _decisions(self, venue_ids: Sequence[str], *,
                   now: Optional[datetime]) -> List[Dict[str, Any]]:
        if self.registry is None or not venue_ids:
            return []
        as_of = (now or datetime.now(timezone.utc)).date()
        out: List[Dict[str, Any]] = []
        for venue_id in sorted(venue_ids):
            try:
                decision = self.registry.decision(venue_id, "*", as_of=as_of)
            except Exception as exc:
                logger.warning("social evidence: %s decision failed: %s",
                               venue_id, exc)
                continue
            out.append({
                "venue_id": venue_id,
                "status": decision.status,
                "research_allowed": decision.research_allowed,
                "execution_allowed": decision.execution_allowed,
                "stale": decision.stale,
            })
        return out


def _load_venue_registry() -> Optional[Any]:
    """Load the venue registry, or None if it is unreadable."""
    try:
        from src.research_eligibility import EligibilityRegistry

        return EligibilityRegistry.load(
            Path(__file__).resolve().parent.parent
            / "config" / "research_venues.yaml")
    except Exception as exc:                          # never break triage
        logger.warning("evidence: venue registry unavailable: %s", exc)
        return None


DEFAULT_RESOLVERS: tuple = (
    KalshiEvidenceResolver, CFTCFilingEvidenceResolver, ArxivEvidenceResolver)


def build_resolvers(enabled: bool = True) -> List[EvidenceResolver]:
    if not enabled:
        return []
    resolvers: List[EvidenceResolver] = [
        factory() for factory in DEFAULT_RESOLVERS]
    resolvers.append(SocialEvidenceResolver(_load_venue_registry()))
    return resolvers


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
