"""
src/kalshi_private.py
─────────────────────
Authenticated Kalshi client — RSA-PSS request signing, portfolio, orders,
RFQ/quotes, multivariate (combo) market creation, and the `communications`
WebSocket feed.

Why this module exists
──────────────────────
Until now the stack had **no authenticated Kalshi path at all**.  `kalshi_public`
is read-only by construction, and every pod runs `paper`/`demo`.  That is why
P-022 can be "ARMED" and still have no way to place its quotes.  This module is
the missing piece, built once, for every pod that eventually goes live.

Safety posture — read this before using it
──────────────────────────────────────────
This client can spend real money.  It is therefore **closed by default**:

  * ``allow_orders`` defaults to **False**.  Every order-placing method raises
    ``OrdersDisabled`` unless the caller passes ``allow_orders=True`` explicitly.
  * A ``LossGuard`` can be attached and is consulted **before every order**.  It
    computes realised loss from ``/portfolio/fills`` and ``/portfolio/settlements``
    — never from an in-process counter, which resets on restart.  That reset is
    exactly how P-014 wrote ``fill_price: null`` on 356 rows for four months.
  * ``dry_run`` logs the exact request that *would* be sent and returns a stub.
  * Key material is never logged, never repr'd, and never returned.

Credentials
───────────
``KALSHI_API_KEY_ID`` plus **one** of:

  * ``KALSHI_PRIVATE_KEY_PATH``  (preferred — a path cannot be echoed by a
    careless ``sed`` over ``.env``; an inline PEM can, and once was)
  * ``KALSHI_PRIVATE_KEY``       (inline PEM; ``env_loader`` handles the
    multi-line form)

Use a **read-only** key for research phases.  A read-scoped key cannot trade even
if it leaks.

Dependencies
────────────
``cryptography`` (required).  ``websocket-client`` is needed only for
:class:`CommunicationsFeed` and is imported lazily::

    pip install websocket-client

Usage::

    from src.kalshi_private import KalshiPrivate, LossGuard

    k = KalshiPrivate.from_env()                  # read-only, orders disabled
    print(k.get_balance())

    guard = LossGuard(k, max_total_loss=500.0, max_daily_loss=100.0)
    k = KalshiPrivate.from_env(allow_orders=True, loss_guard=guard)
    k.place_order(ticker="KXMVE...", action="sell", side="yes",
                  count=5, yes_price_dollars="0.0630")
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

BASE_HOST = "https://api.elections.kalshi.com"
BASE_PATH = "/trade-api/v2"
WS_COMMUNICATIONS = "wss://external-api-ws.kalshi.com/communications"

# Rate-limit token costs (docs.kalshi.com/getting_started/rate_limits).
# Default is 10; quote create/cancel are explicitly discounted to 2.
TOKENS_DEFAULT = 10
TOKENS_QUOTE = 2

# Tier ceilings, tokens/sec (write bucket).  Basic holds ~1s of budget.
TIERS = {"basic": 100, "advanced": 300, "premier": 1000,
         "paragon": 2000, "prime": 4000}


class KalshiAuthError(RuntimeError):
    """Credentials missing, malformed, or rejected."""


class OrdersDisabled(RuntimeError):
    """An order was attempted on a client that was not opened for trading."""


class LossHalt(RuntimeError):
    """The loss guard has halted trading.  Requires manual intervention."""


class KalshiReadError(RuntimeError):
    """A read that must not be allowed to look like an empty result failed.

    ``paginate`` normally swallows a failed page and returns what it has, which
    is right for market data and **wrong** for the loss guard: an expired key or
    a 403 would arrive as ``[]`` and read as "no losses".  Pass ``strict=True``
    to turn a failed page into this exception so the caller can fail closed.
    """


# ──────────────────────────────────────────────────────────────────────
# price helpers — combos quote on a 0.1c grid, not the usual 1c
# ──────────────────────────────────────────────────────────────────────
def fnum(x: Any) -> Optional[float]:
    """Parse Kalshi's stringified numerics ('0.4700') to float."""
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def to_fixed(price: float, step: float = 0.01) -> str:
    """Snap a probability to a market's price grid and render it fixed-point.

    Combo (KXMVE) markets use ``price_level_structure: "deci_cent"`` with
    ``step = 0.0010`` — ten times finer than a standard Kalshi market.  Sending
    an off-grid price returns ``invalid_parameters``, so callers must snap
    rather than round to whole cents.

    Args:
        price: Probability in [0, 1].
        step: Grid step in dollars, from ``price_ranges`` on ``GET /markets/{t}``.

    Returns:
        Fixed-point dollar string, e.g. ``"0.0630"``.
    """
    if not (0.0 <= price <= 1.0):
        raise ValueError(f"price {price!r} outside [0, 1]")
    d = Decimal(str(price))
    s = Decimal(str(step))
    snapped = (d / s).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * s
    return f"{snapped:.4f}"


def grid_step(market: Dict[str, Any]) -> float:
    """Read a market's tick from ``price_ranges``, defaulting to 1c.

    Do not infer the step from ``tick_size`` — ``GET /markets`` returns
    ``tick_size: None`` even for markets that quote sub-cent.
    """
    for rng in (market.get("price_ranges") or []):
        step = fnum(rng.get("step"))
        if step:
            return step
    return 0.01


# ──────────────────────────────────────────────────────────────────────
# auth
# ──────────────────────────────────────────────────────────────────────
class KalshiAuth:
    """Holds the API key ID and private key, and signs requests.

    The signed string is ``timestamp_ms + METHOD + path``, where *path*
    includes ``/trade-api/v2`` and **excludes** the query string.  Signature is
    RSA-PSS / MGF1-SHA256, salt length = digest length, base64-encoded.
    """

    def __init__(self, key_id: str, private_key_pem: bytes):
        try:
            from cryptography.hazmat.primitives import serialization
        except ImportError as exc:                       # pragma: no cover
            raise KalshiAuthError(
                "cryptography is required for authenticated Kalshi access; "
                "pip install cryptography") from exc

        if not key_id:
            raise KalshiAuthError("KALSHI_API_KEY_ID is empty")
        self.key_id = key_id
        try:
            self._key = serialization.load_pem_private_key(
                private_key_pem, password=None)
        except (ValueError, TypeError) as exc:
            # Deliberately does not echo the key material.
            raise KalshiAuthError(
                f"could not parse the Kalshi private key ({exc.__class__.__name__}); "
                "check KALSHI_PRIVATE_KEY_PATH points at a PEM file") from exc

    # -- never leak key material through repr/str -----------------------
    def __repr__(self) -> str:
        return f"<KalshiAuth key_id={self.key_id[:8]}… private_key=<redacted>>"

    __str__ = __repr__

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "KalshiAuth":
        env = env if env is not None else os.environ
        key_id = (env.get("KALSHI_API_KEY_ID") or "").strip()

        path = (env.get("KALSHI_PRIVATE_KEY_PATH") or "").strip()
        if path:
            p = Path(path).expanduser()
            if not p.is_file():
                raise KalshiAuthError(
                    f"KALSHI_PRIVATE_KEY_PATH does not exist: {p}")
            pem = p.read_bytes()
        else:
            inline = env.get("KALSHI_PRIVATE_KEY") or ""
            if not inline.strip():
                raise KalshiAuthError(
                    "no Kalshi private key: set KALSHI_PRIVATE_KEY_PATH "
                    "(preferred) or KALSHI_PRIVATE_KEY")
            logger.warning(
                "using inline KALSHI_PRIVATE_KEY; prefer "
                "KALSHI_PRIVATE_KEY_PATH so the key cannot be echoed by a "
                "careless read of .env")
            pem = inline.encode()
        return cls(key_id, pem)

    def sign(self, method: str, path: str,
             timestamp_ms: Optional[int] = None) -> Dict[str, str]:
        """Build the three auth headers for one request.

        Args:
            method: HTTP verb, upper-case.
            path: Full path including ``/trade-api/v2``, **without** query.
            timestamp_ms: Override for tests; defaults to now.
        """
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
        msg = f"{ts}{method.upper()}{path}".encode()
        sig = self._key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(ts),
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }


# ──────────────────────────────────────────────────────────────────────
# rate limiting
# ──────────────────────────────────────────────────────────────────────
class TokenBucket:
    """Token bucket sized to a Kalshi rate tier.

    Kalshi returns 429 with **no retry headers**, so the only safe strategy is
    to stay under the ceiling locally and back off blindly when we miss.
    """

    def __init__(self, tokens_per_s: float, burst: Optional[float] = None):
        self.rate = float(tokens_per_s)
        self.capacity = float(burst if burst is not None else tokens_per_s * 2)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, cost: float = TOKENS_DEFAULT) -> float:
        """Block until *cost* tokens are available.  Returns seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity,
                                   self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return waited
                deficit = cost - self._tokens
                sleep_for = min(deficit / self.rate, 1.0)
            time.sleep(sleep_for)
            waited += sleep_for


# ──────────────────────────────────────────────────────────────────────
# loss guard
# ──────────────────────────────────────────────────────────────────────
@dataclass
class LossState:
    """Snapshot of the guard's view of the account.

    ``total_realised`` is the **net** realised loss — the figure the halt is
    compared against, and the one a human means by "a $500 budget".
    ``gross_loss`` is the sum of the losing settlements alone, kept for
    visibility: for a maker that wins small and often, gross runs far ahead of
    net, and halting on it would stop a profitable book.
    """
    total_realised: float = 0.0
    today_realised: float = 0.0
    net_pnl: float = 0.0
    gross_loss: float = 0.0
    day: str = ""
    halted: bool = False
    reason: str = ""
    rows_seen: int = 0
    rows_readable: int = 0


def settlement_pnl(row: Dict[str, Any]) -> Optional[float]:
    """Realised P&L in dollars for one settlement row, or None if unreadable.

    **Kalshi returns no P&L field.**  Verified against a live account on
    2026-07-29: a settlement carries only ``event_ticker``, ``ticker``,
    ``market_result``, ``yes_count_fp``, ``no_count_fp``,
    ``yes_total_cost_dollars``, ``no_total_cost_dollars``, ``fee_cost``,
    ``revenue``, ``value`` and ``settled_time``.  There is no
    ``realized_pnl_dollars``.  P&L must therefore be *derived*::

        pnl = revenue/100 - yes_total_cost - no_total_cost - fee_cost

    Unit trap: ``revenue`` and ``value`` are **integer cents**, while the
    ``*_cost_dollars`` fields are fixed-point **dollar** strings.  ``value`` is
    the per-contract settlement value (max 100 = $1.00), *not* a total — do not
    sum it.

    Fees are subtracted because a loss guard must err toward halting early.

    The documented ``realized_pnl_dollars`` / ``realized_pnl`` fields are still
    tried first, so this keeps working if Kalshi adds them later.
    """
    v = fnum(row.get("realized_pnl_dollars"))
    if v is not None:
        return v
    if row.get("realized_pnl") is not None:
        try:
            return float(row["realized_pnl"]) / 100.0
        except (TypeError, ValueError):
            pass

    revenue = row.get("revenue")
    if revenue is None:
        return None
    try:
        pnl = float(revenue) / 100.0
    except (TypeError, ValueError):
        return None
    for field in ("yes_total_cost_dollars", "no_total_cost_dollars", "fee_cost"):
        c = fnum(row.get(field))
        if c:
            pnl -= c
    return pnl


class LossGuard:
    """Hard kill switch, computed from the exchange rather than from memory.

    Two thresholds, both authorised for P-029 testing on 2026-07-28:

      * ``max_total_loss`` — permanent halt; requires a manual ``reset()``.
      * ``max_daily_loss`` — 24h pause, clears at UTC midnight.

    The realised figure is recomputed from ``/portfolio/settlements`` (closed
    P&L) so that a process restart cannot silently reset the budget.  An
    in-process counter would — and that class of bug has already cost this
    project four months of bad data once.

    **Fails closed.**  If the exchange cannot be read, the guard halts rather
    than assuming the budget is intact.
    """

    def __init__(self, client: "KalshiPrivate",
                 max_total_loss: float = 500.0,
                 max_daily_loss: float = 100.0,
                 refresh_s: float = 60.0,
                 since: Optional[str] = None):
        """
        Args:
            since: ISO-8601 instant (e.g. ``"2026-08-01T00:00:00Z"``) or date
                (``"2026-08-01"``).  Only settlements at or after it count
                toward the budget.  **Set this.**  An account with prior trading
                history will otherwise spend the whole budget on losses that
                predate the experiment — this account carried 830 settlements
                before P-029 placed its first order, which would have halted the
                run instantly and looked like a mystery.
        """
        if max_total_loss <= 0 or max_daily_loss <= 0:
            raise ValueError("loss limits must be positive")
        self.client = client
        self.max_total_loss = float(max_total_loss)
        self.max_daily_loss = float(max_daily_loss)
        self.refresh_s = refresh_s
        self.since = since[:10] if (since and len(since) >= 10) else None
        self._min_ts: Optional[int] = None
        if since:
            try:
                s = since if len(since) > 10 else since + "T00:00:00Z"
                self._min_ts = int(datetime.fromisoformat(
                    s.replace("Z", "+00:00")).timestamp())
            except ValueError:
                logger.warning("could not parse since=%r as a timestamp; "
                               "falling back to client-side date filtering", since)
        else:
            logger.warning(
                "LossGuard has no `since` — every settlement in the account's "
                "history counts toward the budget. Set since= to the start of "
                "the experiment.")
        if getattr(client, "subaccount", None) is None:
            logger.warning(
                "LossGuard's client has no subaccount set — manual trading in "
                "the primary subaccount will count toward this budget and can "
                "trip the halt. Point the client at a dedicated subaccount.")
        self.state = LossState()
        # None, not 0.0.  `time.monotonic()` is not guaranteed to be large at
        # process start — on a freshly booted box, or any platform where it
        # counts from process start, `monotonic() - 0.0 < refresh_s` is true and
        # the FIRST check would silently skip its refresh and report "no loss".
        # That is the restart-resets-the-budget failure this class exists to
        # prevent, reintroduced by a sentinel value.
        self._last_refresh: Optional[float] = None
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Clear a halt.  Deliberately manual — a halt means something is wrong."""
        with self._lock:
            self.state = LossState()
            self._last_refresh = 0.0
        logger.warning("LossGuard reset by operator")

    def _refresh(self, force: bool = False) -> None:
        if (not force and self._last_refresh is not None
                and (time.monotonic() - self._last_refresh) < self.refresh_s):
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        query: Dict[str, Any] = {"limit": 1000}
        if self._min_ts is not None:
            # Filter SERVER-side. Scoping by subaccount + min_ts means manual
            # trading in the primary subaccount can neither trip this halt nor
            # hide the pod's own losses inside it.
            query["min_ts"] = self._min_ts
        try:
            # strict=True so a 401/403/5xx raises instead of arriving as an
            # empty list.  Without it a failed read is indistinguishable from a
            # clean account and the guard reports $0.00 — the same blindness as
            # the missing-P&L-field bug, one layer further out.
            rows = self.client.get_settlements(strict=True, **query)
        except Exception as exc:                     # fail closed, loudly
            with self._lock:
                self.state.halted = True
                self.state.reason = f"could not read settlements: {exc}"
            logger.error("LossGuard failing closed: %s", exc)
            return

        net = day_net = gross = 0.0
        seen = readable = 0
        for r in rows:
            ts = str(r.get("settled_time") or r.get("created_time") or "")[:10]
            if self.since and ts and ts < self.since:
                continue                       # predates the experiment
            seen += 1
            pnl = settlement_pnl(r)
            if pnl is None:
                continue
            readable += 1
            net += pnl
            if pnl < 0:
                gross -= pnl
            if ts == today:
                day_net += pnl

        # Halt on NET loss.  Summing only the losing legs would stop a book that
        # is winning: a maker collecting 3c on 94% of contracts and paying out on
        # 6% accrues gross losses continuously while net climbs.  Measured on a
        # synthetic +$200-net book, gross reached $600 and tripped a $500
        # "budget" that had not actually been spent.
        total = max(0.0, -net)
        day_loss = max(0.0, -day_net)

        with self._lock:
            self.state.total_realised = total
            self.state.today_realised = day_loss
            self.state.net_pnl = net
            self.state.gross_loss = gross
            self.state.day = today
            self.state.rows_seen = seen
            self.state.rows_readable = readable
            # A guard that can read nothing must not report "no loss".  This is
            # the failure that made it silently blind on a live account: it read
            # `realized_pnl_dollars`, which Kalshi does not return.
            if seen > 0 and readable == 0:
                self.state.halted = True
                self.state.reason = (
                    f"could not derive P&L from any of {seen} settlements — "
                    "the guard is blind and must not be trusted; check the "
                    "settlement schema with scripts/inspect_settlements.py")
            elif total >= self.max_total_loss:
                self.state.halted = True
                self.state.reason = (
                    f"cumulative realised loss ${total:,.2f} >= "
                    f"${self.max_total_loss:,.2f} — permanent halt")
            elif day_loss >= self.max_daily_loss:
                self.state.halted = True
                self.state.reason = (
                    f"daily realised loss ${day_loss:,.2f} >= "
                    f"${self.max_daily_loss:,.2f} — paused until UTC midnight")
            self._last_refresh = time.monotonic()

    def check(self) -> None:
        """Raise :class:`LossHalt` if trading must stop.  Called before every order."""
        self._refresh()
        with self._lock:
            if self.state.halted:
                raise LossHalt(self.state.reason)

    def snapshot(self) -> LossState:
        self._refresh()
        with self._lock:
            return LossState(**self.state.__dict__)


# ──────────────────────────────────────────────────────────────────────
# the client
# ──────────────────────────────────────────────────────────────────────
class KalshiPrivate:
    """Authenticated Kalshi client.

    Conforms to ``src.protocols.KalshiClientProtocol`` for ``get_market``,
    ``get_markets`` and ``place_order``.
    """

    def __init__(self, auth: KalshiAuth, *,
                 allow_orders: bool = False,
                 dry_run: bool = False,
                 loss_guard: Optional[LossGuard] = None,
                 subaccount: Optional[int] = None,
                 tier: str = "basic",
                 timeout: float = 15.0,
                 user_agent: str = "betting-pod-shop/kalshi-private"):
        """
        Args:
            subaccount: Kalshi subaccount 0–63 (0 = primary).  When set, it is
                applied to every portfolio read *and* to every order, so the
                pod's capital and P&L are partitioned from anything else in the
                account.

                This is the sanctioned way to isolate an algo from manual
                trading — Kalshi permits only **one account per person**, so a
                second account is not an option.  Subaccounts are an **API-only**
                feature: they are not exposed in the Kalshi web or mobile app,
                which is precisely what makes them safe here — manual GUI
                trading cannot reach a numbered subaccount.
        """
        if subaccount is not None and not (0 <= int(subaccount) <= 63):
            raise ValueError("subaccount must be 0–63")
        self.auth = auth
        self.allow_orders = bool(allow_orders)
        self.dry_run = bool(dry_run)
        self.loss_guard = loss_guard
        self.subaccount = None if subaccount is None else int(subaccount)
        self.timeout = timeout
        self.bucket = TokenBucket(TIERS.get(tier, TIERS["basic"]))
        self._s = requests.Session()
        self._s.headers.update({"Accept": "application/json",
                                "Content-Type": "application/json",
                                "User-Agent": user_agent})
        self.calls = 0
        self.status_log: List[Tuple[str, str, int]] = []
        if allow_orders:
            logger.warning(
                "KalshiPrivate opened WITH ORDERS ENABLED%s",
                " (dry_run)" if dry_run else " — this client can spend real money")

    def __repr__(self) -> str:
        return (f"<KalshiPrivate key={self.auth.key_id[:8]}… "
                f"orders={'on' if self.allow_orders else 'OFF'} "
                f"dry_run={self.dry_run}>")

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None, **kw) -> "KalshiPrivate":
        return cls(KalshiAuth.from_env(env), **kw)

    # -- transport ------------------------------------------------------
    def request(self, method: str, path: str, *,
                params: Optional[dict] = None,
                body: Optional[dict] = None,
                cost: int = TOKENS_DEFAULT,
                retries: int = 4) -> Optional[dict]:
        """Signed request against ``/trade-api/v2``.

        Args:
            method: HTTP verb.
            path: Path *relative to* ``/trade-api/v2`` (leading slash), e.g.
                ``"/portfolio/balance"``.
            params: Query parameters.  Excluded from the signature.
            body: JSON body for POST/PUT.
            cost: Rate-limit token cost (2 for quote create/cancel).
        """
        full = f"{BASE_PATH}{path}"
        url = f"{BASE_HOST}{full}"
        method = method.upper()

        for attempt in range(retries + 1):
            self.bucket.acquire(cost)
            headers = self.auth.sign(method, full)
            try:
                r = self._s.request(method, url, params=params,
                                    data=json.dumps(body) if body is not None else None,
                                    headers=headers, timeout=self.timeout)
                self.calls += 1
            except requests.RequestException as exc:
                if attempt < retries:
                    time.sleep(1.5 * (2 ** attempt))
                    continue
                logger.warning("kalshi_private %s %s transport error: %s",
                               method, path, exc)
                return None

            if r.status_code in (200, 201, 204):
                if r.status_code == 204 or not r.content:
                    return {}
                try:
                    return r.json()
                except ValueError:
                    logger.warning("non-JSON %s from %s %s",
                                   r.status_code, method, path)
                    return None

            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                # No retry headers are returned; back off blindly.
                time.sleep(1.5 * (2 ** attempt))
                continue

            self.status_log.append((method, path, r.status_code))
            if r.status_code in (401, 403):
                logger.error(
                    "kalshi_private %s %s -> HTTP %s. Check the key is active "
                    "and has the needed scope (a read-only key cannot trade).",
                    method, path, r.status_code)
            else:
                logger.warning("kalshi_private %s %s -> HTTP %s: %s",
                               method, path, r.status_code, r.text[:300])
            return None
        return None

    def get(self, path: str, params: Optional[dict] = None,
            cost: int = TOKENS_DEFAULT) -> Optional[dict]:
        return self.request("GET", path, params=params, cost=cost)

    def _scoped(self, params: Optional[dict] = None) -> dict:
        """Add the subaccount to a portfolio query, unless already specified.

        Every portfolio read (`balance`, `positions`, `fills`, `settlements`)
        accepts `subaccount` — verified against the OpenAPI spec.  Applying it
        centrally is what keeps the loss guard and the edge measurement from
        silently including manual trading.
        """
        p = dict(params or {})
        if self.subaccount is not None and "subaccount" not in p:
            p["subaccount"] = self.subaccount
        return p

    def paginate(self, path: str, key: str,
                 params: Optional[dict] = None,
                 max_pages: int = 200,
                 strict: bool = False) -> List[dict]:
        """Follow Kalshi's cursor pagination and return the flattened list.

        Args:
            strict: Raise :class:`KalshiReadError` if a page fails, rather than
                returning the rows gathered so far.  Callers that treat an empty
                result as meaningful — the loss guard above all — must set this,
                because ``request`` returns ``None`` on 401/403/4xx/persistent
                5xx and a silent ``[]`` then reads as "nothing here".
        """
        out: List[dict] = []
        cursor = None
        for _ in range(max_pages):
            p = dict(params or {})
            if cursor:
                p["cursor"] = cursor
            d = self.get(path, p)
            if d is None:                       # the request itself failed
                if strict:
                    raise KalshiReadError(
                        f"GET {path} failed; last statuses: {self.status_log[-3:]}")
                break
            if not d:                           # 204 / empty body — end of data
                break
            out.extend(d.get(key) or [])
            cursor = d.get("cursor")
            if not cursor:
                break
        return out

    # -- portfolio ------------------------------------------------------
    def get_balance(self) -> Optional[float]:
        """Balance in dollars, for this client's subaccount if one is set."""
        d = self.get("/portfolio/balance", self._scoped())
        if not d:
            return None
        v = fnum(d.get("balance_dollars"))
        if v is None and d.get("balance") is not None:
            v = d["balance"] / 100.0
        return v

    def get_positions(self, **params) -> List[dict]:
        return self.paginate("/portfolio/positions", "market_positions",
                             self._scoped(params))

    def get_fills(self, **params) -> List[dict]:
        return self.paginate("/portfolio/fills", "fills", self._scoped(params))

    def get_settlements(self, strict: bool = False, **params) -> List[dict]:
        """Settlements.  Supports `ticker`, `event_ticker`, `min_ts`, `max_ts`
        and `subaccount` — prefer `min_ts` over client-side date filtering.

        Args:
            strict: Raise :class:`KalshiReadError` instead of returning ``[]``
                when the read fails.  The loss guard sets this.
        """
        return self.paginate("/portfolio/settlements", "settlements",
                             self._scoped(params), strict=strict)

    def get_orders(self, **params) -> List[dict]:
        return self.paginate("/portfolio/orders", "orders", self._scoped(params))

    def transfer_subaccount(self, *, from_subaccount: int, to_subaccount: int,
                            amount_dollars: float,
                            client_transfer_id: Optional[str] = None
                            ) -> Optional[dict]:
        """Move cash between subaccounts.

        Idempotent on ``client_transfer_id`` — reusing an id will not move the
        money twice, so a retry after a timeout is safe.

        Args:
            amount_dollars: Converted to whole cents for the API.
        """
        self._assert_can_order()
        for n in (from_subaccount, to_subaccount):
            if not (0 <= int(n) <= 63):
                raise ValueError("subaccount must be 0–63")
        if from_subaccount == to_subaccount:
            raise ValueError("from_subaccount and to_subaccount are the same")
        cents = int(round(amount_dollars * 100))
        if cents <= 0:
            raise ValueError("amount must be positive")
        body = {"client_transfer_id": client_transfer_id or str(uuid.uuid4()),
                "from_subaccount": int(from_subaccount),
                "to_subaccount": int(to_subaccount),
                "amount_cents": cents}
        if self.dry_run:
            logger.info("DRY RUN transfer %s", json.dumps(body))
            return {"dry_run": True, "transfer": body}
        return self.request("POST", "/portfolio/subaccounts/transfer", body=body)

    # -- market data (authenticated view) --------------------------------
    def get_market(self, ticker: str) -> Optional[Dict[str, Any]]:
        d = self.get(f"/markets/{ticker}")
        return (d or {}).get("market")

    def get_markets(self, **params) -> List[Dict[str, Any]]:
        return self.paginate("/markets", "markets", params)

    # -- orders ----------------------------------------------------------
    def _assert_can_order(self) -> None:
        if not self.allow_orders:
            raise OrdersDisabled(
                "this client was opened read-only; construct with "
                "allow_orders=True to place orders")
        if self.loss_guard is not None:
            self.loss_guard.check()

    def place_order(self, *, ticker: str, side: str,
                    yes_price: Optional[int] = None,
                    no_price: Optional[int] = None,
                    count: int = 1,
                    action: str = "buy",
                    order_type: str = "limit",
                    yes_price_dollars: Optional[str] = None,
                    no_price_dollars: Optional[str] = None,
                    post_only: bool = False,
                    client_order_id: Optional[str] = None,
                    expiration_ts: Optional[int] = None) -> Optional[dict]:
        """Place an order.

        Signature is protocol-compatible with ``KalshiClientProtocol``; the
        extra keyword arguments are additive.

        Prefer ``yes_price_dollars`` / ``no_price_dollars`` for **combo (KXMVE)
        markets** — they quote on a 0.1c grid that whole-cent integers cannot
        express.  Use :func:`to_fixed` with :func:`grid_step` to snap.

        Args:
            ticker: Market ticker.
            side: ``"yes"`` or ``"no"``.
            yes_price: Limit price in whole cents (1–99).
            no_price: Limit price in whole cents (1–99).
            count: Contracts.
            action: ``"buy"`` or ``"sell"``.
            order_type: ``"limit"`` or ``"market"``.
            post_only: Reject rather than cross the spread.
            client_order_id: Idempotency key; generated if omitted.

        Raises:
            OrdersDisabled: Client not opened for trading.
            LossHalt: The loss guard has halted trading.
        """
        self._assert_can_order()
        side = side.lower()
        action = action.lower()
        if side not in ("yes", "no"):
            raise ValueError(f"side must be yes|no, got {side!r}")
        if action not in ("buy", "sell"):
            raise ValueError(f"action must be buy|sell, got {action!r}")
        if count <= 0:
            raise ValueError("count must be positive")
        if order_type == "limit" and not any(
                x is not None for x in
                (yes_price, no_price, yes_price_dollars, no_price_dollars)):
            raise ValueError("a limit order needs a price")

        body: Dict[str, Any] = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": int(count),
            "type": order_type,
            "client_order_id": client_order_id or str(uuid.uuid4()),
        }
        if yes_price is not None:
            body["yes_price"] = int(yes_price)
        if no_price is not None:
            body["no_price"] = int(no_price)
        if yes_price_dollars is not None:
            body["yes_price_dollars"] = yes_price_dollars
        if no_price_dollars is not None:
            body["no_price_dollars"] = no_price_dollars
        if post_only:
            body["post_only"] = True
        if expiration_ts is not None:
            body["expiration_ts"] = int(expiration_ts)
        if self.subaccount is not None:
            body["subaccount"] = self.subaccount

        if self.dry_run:
            logger.info("DRY RUN place_order %s", json.dumps(body))
            return {"dry_run": True, "order": body}
        return self.request("POST", "/portfolio/orders", body=body)

    def cancel_order(self, order_id: str) -> Optional[dict]:
        self._assert_can_order()
        if self.dry_run:
            logger.info("DRY RUN cancel_order %s", order_id)
            return {"dry_run": True, "order_id": order_id}
        return self.request("DELETE", f"/portfolio/orders/{order_id}")

    # -- multivariate / combos -------------------------------------------
    def get_collections(self, **params) -> List[dict]:
        """All multivariate event collections.

        Note the response key is ``multivariate_contracts``, not ``collections``.
        Only the rolling ``-R`` collections are ever live, and they are re-opened
        daily — resolve them per cycle, never cache to disk.
        """
        return self.paginate("/multivariate_event_collections",
                             "multivariate_contracts", params)

    def create_combo_market(self, collection_ticker: str,
                            selected_markets: List[Dict[str, str]]
                            ) -> Optional[dict]:
        """Materialise a combo market from a collection.

        A combo market does **not** pre-exist — this must be called before the
        combo can be quoted or RFQ'd, and ``CreateRFQ`` has no leg fields, so
        this is the only way to obtain a combo ticker.

        Args:
            collection_ticker: e.g. ``"KXMVECROSSCATEGORY-R"``.
            selected_markets: ``[{"market_ticker":…, "event_ticker":…,
                "side": "yes"|"no"}, …]``.

        Returns:
            ``{"event_ticker":…, "market_ticker":…}``.

        Note:
            Kalshi caps creations at **5,000 per week**.
        """
        return self.request("POST",
                            f"/multivariate_event_collections/{collection_ticker}",
                            body={"selected_markets": selected_markets})

    # -- RFQ / quotes -----------------------------------------------------
    def get_rfqs(self, **params) -> List[dict]:
        return self.paginate("/communications/rfqs", "rfqs", params)

    def create_rfq(self, market_ticker: str, *,
                   contracts: Optional[int] = None,
                   target_cost_dollars: Optional[str] = None,
                   rest_remainder: bool = False) -> Optional[dict]:
        """Request a quote.  Size by contracts **or** by target cost, not both.

        Only one open RFQ per market ticker is allowed — a second returns
        ``409 Conflict``.
        """
        self._assert_can_order()
        body: Dict[str, Any] = {"market_ticker": market_ticker,
                                "rest_remainder": rest_remainder}
        if contracts is not None:
            body["contracts"] = int(contracts)
        if target_cost_dollars is not None:
            body["target_cost_dollars"] = target_cost_dollars
        if self.subaccount is not None:
            body["subaccount"] = self.subaccount
        if self.dry_run:
            logger.info("DRY RUN create_rfq %s", json.dumps(body))
            return {"dry_run": True, "rfq": body}
        return self.request("POST", "/communications/rfqs", body=body)

    def delete_rfq(self, rfq_id: str) -> Optional[dict]:
        self._assert_can_order()
        return self.request("DELETE", f"/communications/rfqs/{rfq_id}")

    def create_quote(self, rfq_id: str, *,
                     yes_bid: str, no_bid: str,
                     rest_remainder: bool = False,
                     post_only: bool = False) -> Optional[dict]:
        """Respond to an RFQ with a two-sided quote.

        Either side may be ``"0"`` to decline it, but not both, and the quote is
        rejected if ``yes_bid + no_bid > $1``.  A new quote on the same RFQ
        **replaces** this maker's previous one.  Quotes are for the full RFQ
        size — the quoter does not choose size.

        Prices must sit on the market's grid (0.1c for combos).
        """
        self._assert_can_order()
        y, n = Decimal(str(yes_bid)), Decimal(str(no_bid))
        if y <= 0 and n <= 0:
            raise ValueError("at least one side of a quote must be non-zero")
        if y + n > Decimal("1"):
            raise ValueError(
                f"yes_bid + no_bid = {y + n} > $1; the exchange would reject this")
        body: Dict[str, Any] = {"rfq_id": rfq_id, "yes_bid": str(yes_bid),
                                "no_bid": str(no_bid),
                                "rest_remainder": rest_remainder}
        if post_only:
            body["post_only"] = True
        if self.subaccount is not None:
            body["subaccount"] = self.subaccount
        if self.dry_run:
            logger.info("DRY RUN create_quote %s", json.dumps(body))
            return {"dry_run": True, "quote": body}
        return self.request("POST", "/communications/quotes", body=body,
                            cost=TOKENS_QUOTE)

    def accept_quote(self, quote_id: str, accepted_side: str) -> Optional[dict]:
        self._assert_can_order()
        if accepted_side.lower() not in ("yes", "no"):
            raise ValueError("accepted_side must be yes|no")
        return self.request("PUT", f"/communications/quotes/{quote_id}/accept",
                            body={"accepted_side": accepted_side.lower()})

    def confirm_quote(self, quote_id: str) -> Optional[dict]:
        """Confirm an accepted quote.

        **Latency-critical.**  Combos are High Volatility Markets: the docs say
        3s to confirm, the CFTC-filed rulebook says 2s — they disagree, so
        assume 2s.  Once confirmed, neither side can opt out.

        The confirm decision must be *pre-computed at quote time*; this call
        should be a lookup, not a calculation.  It deliberately does not consult
        the loss guard, because a network round-trip inside the confirm window
        would blow the budget — the guard runs at :meth:`create_quote` instead.
        """
        if not self.allow_orders:
            raise OrdersDisabled("client opened read-only")
        return self.request("PUT", f"/communications/quotes/{quote_id}/confirm",
                            cost=TOKENS_QUOTE)

    def delete_quote(self, quote_id: str) -> Optional[dict]:
        if not self.allow_orders:
            raise OrdersDisabled("client opened read-only")
        return self.request("DELETE", f"/communications/quotes/{quote_id}",
                            cost=TOKENS_QUOTE)

    # -- diagnostics -------------------------------------------------------
    def whoami(self) -> Dict[str, Any]:
        """Cheap credential check.  Returns what the key can actually see."""
        bal = self.get_balance()
        ok = bal is not None
        return {"key_id": self.auth.key_id[:8] + "…",
                "authenticated": ok,
                "balance_dollars": bal,
                "orders_enabled": self.allow_orders,
                "dry_run": self.dry_run,
                "loss_guard": (self.loss_guard.snapshot().__dict__
                               if self.loss_guard else None)}


# ──────────────────────────────────────────────────────────────────────
# communications WebSocket
# ──────────────────────────────────────────────────────────────────────
class CommunicationsFeed:
    """Consumer for the ``communications`` channel (RFQs and quotes).

    ``rfq_created`` and ``rfq_deleted`` are broadcast to **all** subscribers and
    carry ``mve_selected_legs`` in the message body — so a quoter learns the
    full leg set with no follow-up REST call.  ``quote_created``,
    ``quote_accepted`` and ``quote_executed`` go only to the involved parties.

    Supports Kalshi's sharding (``shard_factor`` 1–100, ``shard_key``) so the
    RFQ firehose can be split across processes.

    Requires ``websocket-client`` (``pip install websocket-client``).

    Example::

        feed = CommunicationsFeed(auth, on_message=handle)
        feed.run_forever()          # blocking; reconnects with backoff
    """

    def __init__(self, auth: KalshiAuth,
                 on_message: Callable[[str, dict], None],
                 *, shard_factor: Optional[int] = None,
                 shard_key: Optional[int] = None,
                 url: str = WS_COMMUNICATIONS,
                 max_backoff: float = 60.0):
        if shard_factor is not None:
            if not (1 <= shard_factor <= 100):
                raise ValueError("shard_factor must be in [1, 100]")
            if shard_key is None or not (0 <= shard_key < shard_factor):
                raise ValueError("shard_key must satisfy 0 <= key < shard_factor")
        self.auth = auth
        self.on_message = on_message
        self.shard_factor = shard_factor
        self.shard_key = shard_key
        self.url = url
        self.max_backoff = max_backoff
        self._stop = threading.Event()
        self.messages = 0
        self.reconnects = 0

    def stop(self) -> None:
        self._stop.set()

    def _headers(self) -> List[str]:
        # The WS handshake is a GET against the connection path.
        from urllib.parse import urlparse
        path = urlparse(self.url).path or "/communications"
        h = self.auth.sign("GET", path)
        return [f"{k}: {v}" for k, v in h.items()]

    def _subscribe_frame(self) -> str:
        params: Dict[str, Any] = {"channels": ["communications"]}
        if self.shard_factor is not None:
            params["shard_factor"] = self.shard_factor
            params["shard_key"] = self.shard_key
        return json.dumps({"id": 1, "cmd": "subscribe", "params": params})

    def run_forever(self) -> None:
        """Connect, subscribe and dispatch until :meth:`stop`.

        Reconnects with exponential backoff.  A dropped feed means missed RFQs,
        and a missed RFQ cannot be recovered — so a disconnect is logged at
        ERROR, not DEBUG.
        """
        try:
            import websocket                      # websocket-client
        except ImportError as exc:                # pragma: no cover
            raise RuntimeError(
                "CommunicationsFeed needs websocket-client; "
                "pip install websocket-client") from exc

        backoff = 1.0
        while not self._stop.is_set():
            ws = None
            try:
                ws = websocket.create_connection(
                    self.url, header=self._headers(), timeout=30)
                ws.send(self._subscribe_frame())
                logger.info("communications feed connected (shard %s/%s)",
                            self.shard_key, self.shard_factor)
                backoff = 1.0
                while not self._stop.is_set():
                    raw = ws.recv()
                    if not raw:
                        continue
                    self.messages += 1
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        logger.warning("non-JSON frame on communications feed")
                        continue
                    kind = msg.get("type") or msg.get("cmd") or "unknown"
                    try:
                        self.on_message(kind, msg)
                    except Exception:
                        # A bad handler must never kill the feed.
                        logger.exception("communications handler raised on %s", kind)
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.reconnects += 1
                logger.error("communications feed dropped (%s); reconnecting in "
                             "%.0fs — RFQs during this gap are lost", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(self.max_backoff, backoff * 2)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
        logger.info("communications feed stopped after %d messages, %d reconnects",
                    self.messages, self.reconnects)
