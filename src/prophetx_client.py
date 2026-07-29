"""ProphetX READ-ONLY market-data client (P-030 cross-venue basis measurement).

ProphetX became a CFTC DCM + DCO and launched nationwide 2026-06-18. Unlike
every sportsbook in `Venue Landscape - Research Notes.md`, its Terms of Use
AFFIRMATIVELY PERMIT algorithmic trading, verbatim (ToS s3.7, eff. 2026-06-10):

    "You will not use any software-assisted methods or techniques (including
     but not limited to 'bots' designed to trade automatically) ... EXCEPT
     PROGRAMMATIC TRADING VIA THE PROPHETX API, WHICH IS AVAILABLE TO ALL
     USERS, or as otherwise expressly permitted by ProphetX."
    (followed by: "In our sole discretion, we may revoke access ... at any time.")

READ-ONLY BY CONSTRUCTION
-------------------------
This module deliberately implements NO order path. There is no submit_order,
no cancel_order, no balance mutation -- the same discipline as
scripts/collect_inplay_basis.py, which never imports place_order. P-030 is
NOT green-lit; only measurement is authorized. `tests/test_prophetx_client.py`
asserts the absence of those symbols so the constraint cannot rot.

TWO THINGS TO KNOW BEFORE YOU TRUST OUTPUT FROM THIS FILE
---------------------------------------------------------
1. **RESPONSE SHAPES ARE UNVERIFIED.** Every ProphetX data endpoint returns 401
   unauthenticated (confirmed on both sandbox and production 2026-07-29), so
   the field names below are taken from the published OpenAPI spec and the
   first-party integration guide, NOT from an observed live response. The
   parsers are deliberately tolerant and every one of them records what it
   could not understand. Run `--dump-shapes` on the first authenticated call
   and reconcile before trusting a single measured basis.

2. **RATE LIMITS ARE UNPUBLISHED.** Nothing in ProphetX's docs states them.
   The default throttle here is deliberately slow (4 req/s). Ask during API
   onboarding; do not raise this on a guess.

CREDENTIALS: request access via the Zendesk form at
prophethelp.zendesk.com/hc/en-us/requests/new or marketmaking@prophetexchange.com,
then generate tokens in-app under Menu -> API integration. A SANDBOX WITH DEMO
FUNDS is available and is the right place to reconcile shapes.

Env: PROPHETX_ACCESS_KEY, PROPHETX_SECRET_KEY, PROPHETX_ENV=sandbox|production.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from src.prophetx_ladder import VALID_ODDS, american_to_prob

logger = logging.getLogger(__name__)

BASES = {
    "sandbox": "https://api-ss-sandbox.betprophet.co/partner",
    "production": "https://cash.api.prophetx.co/partner",
}

# Access token lifetime is 20 minutes per the docs; refresh early.
_TOKEN_TTL = 20 * 60
_TOKEN_SKEW = 120


class ProphetXAuthError(RuntimeError):
    """Raised when credentials are absent or rejected. Callers should treat
    this as 'venue unavailable', never retry in a tight loop."""


class ProphetXClient:
    """Throttled, read-only. Construct with from_config() or from env."""

    def __init__(self, access_key: Optional[str] = None,
                 secret_key: Optional[str] = None,
                 env: str = "sandbox", min_interval: float = 0.25,
                 timeout: float = 15.0, dump_shapes_path: Optional[str] = None):
        if env not in BASES:
            raise ValueError(f"env must be one of {sorted(BASES)}, got {env!r}")
        self._base = BASES[env]
        self._env = env
        self._access_key = access_key or os.getenv("PROPHETX_ACCESS_KEY") or ""
        self._secret_key = secret_key or os.getenv("PROPHETX_SECRET_KEY") or ""
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "betting-pod-shop/P-030-crossvenue"})
        self._min_interval = min_interval
        self._timeout = timeout
        self._last_ts = 0.0
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_exp = 0.0
        self._dump_path = dump_shapes_path
        self._dumped: set = set()

    @classmethod
    def from_config(cls, config: dict, **overrides):
        cfg = dict((config or {}).get("prophetx") or {})
        cfg.update(overrides)
        return cls(access_key=cfg.get("access_key"),
                   secret_key=cfg.get("secret_key"),
                   env=cfg.get("environment", "sandbox"),
                   min_interval=float(cfg.get("min_interval", 0.25)),
                   timeout=float(cfg.get("timeout", 15.0)),
                   dump_shapes_path=cfg.get("dump_shapes_path"))

    # ── plumbing ────────────────────────────────────────────────────

    @property
    def has_credentials(self) -> bool:
        return bool(self._access_key and self._secret_key)

    def _throttle(self) -> None:
        with self._lock:
            slot = max(self._last_ts + self._min_interval, time.time())
            self._last_ts = slot
        delay = slot - time.time()
        if delay > 0:
            time.sleep(delay)

    def _login(self) -> None:
        if not self.has_credentials:
            raise ProphetXAuthError(
                "PROPHETX_ACCESS_KEY / PROPHETX_SECRET_KEY are not set. "
                "Request API access via prophethelp.zendesk.com or "
                "marketmaking@prophetexchange.com, then generate a token "
                "in-app under Menu -> API integration.")
        self._throttle()
        try:
            r = self._session.post(
                f"{self._base}/auth/login",
                json={"access_key": self._access_key,
                      "secret_key": self._secret_key},
                timeout=self._timeout)
        except requests.RequestException as exc:
            raise ProphetXAuthError(f"login transport error: {exc}") from exc
        if r.status_code != 200:
            raise ProphetXAuthError(
                f"login -> HTTP {r.status_code}: {r.text[:200]}")
        body = r.json() if r.content else {}
        data = body.get("data") or body
        tok = (data.get("access_token") or data.get("accessToken")
               or data.get("token"))
        if not tok:
            raise ProphetXAuthError(
                f"login succeeded but no access_token in response: "
                f"{sorted(data)[:12]}")
        self._token = tok
        self._refresh_token = (data.get("refresh_token")
                               or data.get("refreshToken"))
        exp = data.get("access_expire_time") or data.get("expires_in")
        try:
            # docs are ambiguous: absolute epoch vs relative seconds
            exp = float(exp)
            self._token_exp = exp if exp > 1e9 else time.time() + exp
        except (TypeError, ValueError):
            self._token_exp = time.time() + _TOKEN_TTL
        logger.info("prophetx: authenticated (%s), token valid ~%ds",
                    self._env, int(self._token_exp - time.time()))

    def _auth_header(self) -> Dict[str, str]:
        if self._token is None or time.time() >= self._token_exp - _TOKEN_SKEW:
            self._login()
        return {"Authorization": f"Bearer {self._token}"}

    def _record_shape(self, path: str, body: Any) -> None:
        """Append one observed response skeleton per endpoint, once. This is
        how the UNVERIFIED shapes above get reconciled on the first real run."""
        if not self._dump_path or path in self._dumped:
            return
        self._dumped.add(path)

        def skel(v, d=0):
            if d > 3:
                return "..."
            if isinstance(v, dict):
                return {k: skel(x, d + 1) for k, x in list(v.items())[:25]}
            if isinstance(v, list):
                return [skel(v[0], d + 1), f"...x{len(v)}"] if v else []
            return type(v).__name__
        try:
            os.makedirs(os.path.dirname(self._dump_path) or ".", exist_ok=True)
            with open(self._dump_path, "a") as fh:
                fh.write(json.dumps({"path": path, "shape": skel(body)}) + "\n")
        except OSError:
            logger.debug("shape dump failed", exc_info=True)

    def get(self, path: str, params: Optional[dict] = None,
            retries: int = 3) -> Optional[dict]:
        for attempt in range(retries + 1):
            try:
                headers = self._auth_header()
            except ProphetXAuthError:
                raise
            self._throttle()
            try:
                resp = self._session.get(f"{self._base}{path}", params=params,
                                         headers=headers, timeout=self._timeout)
            except requests.RequestException as exc:
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                logger.warning("prophetx %s failed: %s", path, exc)
                return None
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except ValueError:
                    return None
                self._record_shape(path, body)
                return body
            if resp.status_code == 401 and attempt < retries:
                self._token = None          # force re-login once
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                # Rate limits are UNPUBLISHED. Back off hard on 429 rather than
                # probing for the ceiling on a live regulated venue.
                time.sleep(8 * (attempt + 1) if resp.status_code == 429
                           else 2 ** attempt)
                continue
            logger.warning("prophetx %s -> HTTP %s", path, resp.status_code)
            return None
        return None

    @staticmethod
    def _unwrap(body: Optional[dict], *keys) -> list:
        """ProphetX wraps payloads in {"data": ...} and the inner key varies by
        endpoint. Try each candidate, then fall back to any list found."""
        if not body:
            return []
        data = body.get("data", body)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in keys:
                v = data.get(k)
                if isinstance(v, list):
                    return v
            for v in data.values():
                if isinstance(v, list):
                    return v
        return []

    # ── read-only market data ───────────────────────────────────────

    def price_ladder(self) -> List[int]:
        """Valid American-odds ticks. Prefers the live endpoint; falls back to
        the pinned first-party ladder if unavailable. A silently drifted ladder
        would push orders to invalid ticks, so mismatches are logged loudly."""
        try:
            body = self.get("/v4/mm/get_price_ladder")
        except ProphetXAuthError:
            return list(VALID_ODDS)
        live = [int(x) for x in self._unwrap(body, "ladder", "odds")
                if isinstance(x, (int, float))]
        if not live:
            return list(VALID_ODDS)
        if sorted(live) != sorted(VALID_ODDS):
            logger.warning(
                "prophetx: LIVE LADDER DIFFERS from pinned constant "
                "(live=%d ticks, pinned=%d). Update src/prophetx_ladder.py.",
                len(live), len(VALID_ODDS))
        return live

    def tournaments(self) -> List[dict]:
        return self._unwrap(self.get("/v4/mm/get_tournaments"),
                            "tournaments", "data")

    def sport_events(self, tournament_id: Optional[str] = None) -> List[dict]:
        params = {"tournament_id": tournament_id} if tournament_id else None
        return self._unwrap(self.get("/v4/mm/get_sport_events", params),
                            "sport_events", "events")

    def markets_for_event(self, event_id: str) -> List[dict]:
        body = self.get("/v4/mm/get_markets", {"event_id": event_id})
        return self._unwrap(body, "markets", "market")

    def moneyline_book(self, market: dict) -> Dict[str, Any]:
        """Normalize ONE ProphetX market dict into a venue-neutral two-sided
        book keyed by selection name.

        Returns {name: {"bid_odds", "ask_odds", "bid_prob", "ask_prob",
                        "bid_size", "ask_size"}} plus "_unparsed" listing any
        field it could not interpret.

        ⚠️ SHAPE UNVERIFIED (see module docstring). ProphetX is a back/lay-style
        exchange: what is displayed as a "bid" on a selection is economically a
        resting offer to take the OTHER side. Do not assume the sign convention
        matches Kalshi's yes_bid/yes_ask until reconciled against --dump-shapes.
        """
        out: Dict[str, Any] = {"_unparsed": []}
        selections = (market.get("selections") or market.get("outcomes")
                      or market.get("lines") or [])
        if isinstance(selections, dict):
            selections = list(selections.values())
        for sel in selections:
            if not isinstance(sel, dict):
                out["_unparsed"].append(repr(sel)[:80])
                continue
            name = (sel.get("name") or sel.get("selection_name")
                    or sel.get("display_name") or sel.get("competitor"))
            if not name:
                out["_unparsed"].append(sorted(sel)[:8])
                continue
            rec: Dict[str, Any] = {}
            for side, keys in (("bid", ("bid_odds", "back_odds", "odds", "best_bid")),
                               ("ask", ("ask_odds", "lay_odds", "best_ask"))):
                odds = next((sel[k] for k in keys
                             if isinstance(sel.get(k), (int, float))), None)
                rec[f"{side}_odds"] = odds
                try:
                    rec[f"{side}_prob"] = american_to_prob(odds) if odds else None
                except ValueError:
                    rec[f"{side}_prob"] = None
            for side, keys in (("bid", ("bid_size", "back_size", "stake", "size")),
                               ("ask", ("ask_size", "lay_size", "liability"))):
                rec[f"{side}_size"] = next(
                    (float(sel[k]) for k in keys
                     if isinstance(sel.get(k), (int, float))), None)
            out[str(name)] = rec
        return out

    def probe(self) -> dict:
        """Cheap liveness + credential check. Never raises; returns a dict the
        collector can log. Use this to confirm onboarding worked."""
        result = {"env": self._env, "base": self._base,
                  "has_credentials": self.has_credentials,
                  "authenticated": False, "ladder_ticks": None, "error": None}
        if not self.has_credentials:
            # price_ladder() falls back to the pinned constant and swallows the
            # auth error, so probe must report the blocker itself -- otherwise a
            # credential-less run looks healthy.
            result["error"] = (
                "no credentials: set PROPHETX_ACCESS_KEY / PROPHETX_SECRET_KEY. "
                "Request access via prophethelp.zendesk.com/hc/en-us/requests/new "
                "or marketmaking@prophetexchange.com.")
            result["ladder_ticks"] = len(VALID_ODDS)
            return result
        try:
            ladder = self.price_ladder()
            result["ladder_ticks"] = len(ladder)
            result["authenticated"] = self._token is not None
        except ProphetXAuthError as exc:
            result["error"] = str(exc)
        except Exception as exc:                    # never kill the collector
            result["error"] = f"{type(exc).__name__}: {exc}"
        return result
