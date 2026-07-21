# Open Questions — 2026-07-20

Three research questions from `manager/registry.yaml` (R-LIVE-AGENT `open_questions`,
~lines 418–430) and `live_agent_research/REPORT_Live_InPlay_Agent_2026-07-19.md:127`.

Answered with live-API and repo evidence, not recollection.

---

## Q1 — `mlb-maker-fee`: are MLB series on the Kalshi maker-fee list?

**ANSWER: Split. The MLB *game* (moneyline) series DOES charge maker fees — so the
conservative 0.0175 assumption P-016 already uses is CORRECT and there is no
0.44¢/contract to recover. But every MLB *prop/derivative* series charges ZERO maker
fee, and `src/kalshi_fees.py` misclassifies all of them as fee-charging.**

**Confidence: HIGH** for the classification (direct API field, unambiguous).

### Evidence — live Kalshi API (ground truth)

`GET https://api.elections.kalshi.com/trade-api/v2/series/?category=Sports&limit=200`
returns a per-series `fee_type` field. Queried 2026-07-20. 2,964 sports series:
2,856 `quadratic` (maker = 0) vs 108 `quadratic_with_maker_fees` (maker = 0.0175).

Every MLB series the repo references:

| Series | `fee_type` | Maker fee |
|---|---|---|
| `KXMLBGAME` (moneyline — **what P-016 trades**) | `quadratic_with_maker_fees` | **0.0175** |
| `KXMLB` (World Series) | `quadratic_with_maker_fees` | 0.0175 |
| `KXMLBAL` / `KXMLBNL` (LCS) | `quadratic_with_maker_fees` | 0.0175 |
| `KXMLBASGAME` (All-Star Game) | `quadratic_with_maker_fees` | 0.0175 |
| `KXMLBHIT` (hits) | `quadratic` | **0** |
| `KXMLBKS` (strikeouts) | `quadratic` | **0** |
| `KXMLBTOTAL` (game total) | `quadratic` | **0** |
| `KXMLBSPREAD` (run line) | `quadratic` | **0** |
| `KXMLBTEAMTOTAL` | `quadratic` | **0** |
| `KXMLBTB` / `KXMLBHR` / `KXMLBHRR` / `KXMLBSB` | `quadratic` | **0** |
| `KXMLBRFI` (run in 1st) / `KXMLBF5` (first 5) | `quadratic` | **0** |

The organising principle across all sports: **core game/outcome markets charge maker
fees; player-prop and derivative markets do not.** Note the cross-sport asymmetry —
`KXNBASPREAD`, `KXNBATOTAL`, `KXNFLSPREAD`, `KXNFLTOTAL`, `KXNCAAFSPREAD` are all
`quadratic_with_maker_fees`, but `KXMLBSPREAD` and `KXMLBTOTAL` are **not**. MLB
spread/total are free to make; the NBA/NFL equivalents are not. Do not generalise a
fee assumption from one sport to another.

### Evidence — published fee schedule (corroboration)

Kalshi's fee schedule PDF (`https://kalshi.com/docs/kalshi-fee-schedule.pdf`,
"Fee Schedule for July 2026 — 7.7.26 Update") was rate-limited (HTTP 429) on direct
fetch. Secondary sources confirm the **coefficients**:

- Taker = `0.07 × C × (1−C)` per contract → max **1.75¢** at C = 50¢
- Maker = **25% of the taker fee** = `0.0175 × C × (1−C)` → max **0.44¢** at C = 50¢
  — [pm.wiki](https://pm.wiki/learn/kalshi-fees-explained)
- [help.kalshi.com/en/articles/13823805-fees](https://help.kalshi.com/en/articles/13823805-fees)
  confirms maker fees apply to resting orders, charged only on execution, none on cancel.

This confirms the registry's "0.44¢/contract" figure is exactly the maker fee at P=0.50.
**However, no published document enumerates *which series* are subject to maker fees.**
The help centre and the aggregators all defer to the PDF, and the PDF's public summaries
cover coefficients and volume tiers (the bps tiers surfaced in search are the
**perpetual-futures** schedule, not prediction markets) — not a per-series list.

### Which source I trusted, and why

**The API.** The `fee_type` field is the value the matching engine actually bills
against, it is per-series and unambiguous, and it requires no interpretation. The
published docs are not in conflict — they are simply *silent* on series membership, so
there is nothing to reconcile. **No API/doc disagreement was found.**

### Does `src/kalshi_fees.py` classify MLB correctly?

**Partially. Correct for the moneyline; WRONG for all MLB props.**

`series_maker_charges_fee()` defaults to `True` for any series that is neither an
explicitly-listed golf maker-fee prefix nor an explicitly-listed zero-maker golf prop
prefix. The zero-maker allow-list contains **only golf** families. Verified by running
the function against API truth:

| Series | code says | API says | |
|---|---|---|---|
| `KXMLBGAME` | True | True | OK |
| `KXMLB` | True | True | OK |
| `KXMLBHIT`, `KXMLBKS`, `KXMLBTOTAL`, `KXMLBSPREAD`, `KXMLBTEAMTOTAL`, `KXMLBTB`, `KXMLBHR`, `KXMLBRFI`, `KXMLBF5` | True | **False** | **MISMATCH** |

Magnitude: `fee_per_contract(0.50, maker=True)` = **0.438¢/contract** charged where
Kalshi charges nothing.

**P-016 is UNAFFECTED.** `src/pods/live_maker_pod.py:487` calls
`fee_per_contract(fill.price, maker=True)` with no `series_ticker`, falling back to the
general 0.0175 rate — which is the *correct* rate for `KXMLBGAME`. The running gate
sample is booking the right fee.

> **FLAGGED FOR HUMAN DECISION — NOT CHANGED.** Per instruction, the fee logic was left
> untouched: P-016 is mid-gate and altering its fee model would contaminate the sample.
> The defect is latent, not active — it only bites when an MLB **props** strategy calls
> `fee_per_contract(..., maker=True)`. `mlb_props_research/PHASE1_REPORT.md:59` already
> models "prop maker fees = $0" independently, so the research numbers are right and the
> shared helper is the thing that disagrees with them. Fix before any props maker pod
> ships, by extending the zero-maker allow-list beyond golf.

### Implication for the project

- **P-016 economics do not improve.** The 0.44¢/contract upside in the registry question
  does not exist for the moneyline maker. Close `mlb-maker-fee` as answered-negative and
  keep the current gate baseline.
- **The 0.44¢ IS real for MLB props** — and props (batter props / team totals) are
  precisely where `mlb_props_phase1` located the maker edge. That edge is fee-free on
  Kalshi today, which strengthens the props maker thesis relative to the moneyline one.
- Kalshi could extend maker fees to prop series at any time; `PHASE1_REPORT.md:104`
  already names this as a risk. The API check above is cheap and worth re-running
  periodically as a monitor.

---

## Q2 — `polymarket-us-api`: does the US-regulated app expose a full trading API?

**ANSWER: YES. Polymarket US exposes a fully documented order-placement API. Critically,
it is a COMPLETELY SEPARATE STACK from the offshore Polygon CLOB — different docs,
different host, different auth, different SDK. None of the repo's existing Polymarket
order-placement code is reusable against it.**

**Confidence: HIGH** on API existence, auth model, and CFTC status (official docs + CFTC
filings fetched directly). **LOW** on the excluded-state list and on whether the ToS
permits automated trading — see "What remains genuinely unclear".

### The two stacks are not the same product

| | International (offshore) | **Polymarket US** |
|---|---|---|
| Entity | Polymarket (offshore) | **QCX LLC d/b/a Polymarket US** (CFTC DCM/DCO) |
| Docs | `docs.polymarket.com` | **`docs.polymarket.us`** |
| API host | CLOB on Polygon | **`api.polymarket.us`** (auth), `gateway.polymarket.us` (public) |
| Auth | Wallet / EIP-712 signing | **Ed25519 API key, `X-PM-*` headers** |
| SDK | `py-clob-client` | **separate official Python/TS SDKs** |
| Settlement | USDC on Polygon | **USD via FCMs** |
| US order entry | **Close-only — blocked** | Permitted (eligible states) |

They share a brand and essentially nothing else technically.

### 1. Order placement — DOCUMENTED AND CONFIRMED

- `POST /v1/orders` at base `https://api.polymarket.us`
  — [docs.polymarket.us/api-reference/orders/create-order](https://docs.polymarket.us/api-reference/orders/create-order)
- Params: `marketSlug`, `type` (limit/market), `price`, `quantity`, `tif`
  (DAY/GTC/GTD/IOC/FOK), `intent` / `outcomeSide`+`action`, `synchronousExecution`
- Rate limit 20 req/s per API key
  — [orders/overview](https://docs.polymarket.us/api-reference/orders/overview)
- **Only the long side (YES) is directly tradable; NO is synthetic exposure via the long
  side.** This is a real modelling difference from Kalshi's YES/NO symmetry and would
  need handling in any executor.
- Also documented: official Python and TypeScript SDKs, WebSocket streaming (public
  market data + private order/position updates), gRPC streaming, RFQ/combos, OpenAPI +
  AsyncAPI specs.

### 2. Auth / KYC — CONFIRMED

[docs.polymarket.us/api-reference/authentication](https://docs.polymarket.us/api-reference/authentication):
create account in the app → **complete identity verification (required before trading or
API access)** → sign in at `polymarket.us/developer` → generate API key (Key ID + Secret,
secret shown once). Signing is **Ed25519** over timestamp + method + path, via
`X-PM-Access-Key`, `X-PM-Timestamp` (must be within **30 s** of server time),
`X-PM-Signature`. No wallet, no EIP-712, no Polygon key.

### 3. Geographic / regulatory restrictions

- **CONFIRMED:** the *international* API explicitly blocks US order entry. The United
  States is listed under "Regulatory-Restricted Jurisdictions (Close-Only on Frontend and
  API)" — close existing positions, cannot open new ones, **on both frontend and API**.
  [docs.polymarket.com/api-reference/geoblock](https://docs.polymarket.com/api-reference/geoblock)
  **The offshore `py-clob-client` path is a dead end for a US-based fund.**
- **NOT CONFIRMED:** the excluded-state list. Secondary sources converge on roughly AZ,
  IL, MA, MD, MI, MT, NV, OH being unavailable, plus NY complications and TN
  cease-and-desist activity — but no authoritative list was found on `polymarket.us` or
  in the docs, and `polymarket.us/tos` is JS-rendered and did not yield text. **Treat as
  unverified; confirm in-app during KYC.**

### 4. CFTC status — CONFIRMED

**QCX LLC, operating under the assumed name "Polymarket US," designated as a DCM on
2025-07-09** — [cftc.gov filing 49571](https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/49571).
Acquisition of QCX/QC Clearing for $112M:
[PR Newswire](https://www.prnewswire.com/news-releases/polymarket-acquires-cftc-licensed-exchange-and-clearinghouse-qcex-for-112-million-302509626.html).

Regulatory status **broadens** rather than restricts API access — it is the reason a
compliant US API exists at all. The cost is mandatory KYC (gov ID, SSN, proof of address)
and USD/FCM settlement instead of pseudonymous wallet trading.

An **institutional tier** also exists (FIX API, FIXT.1.1/FIX50SP2, gRPC, AWS PrivateLink,
100 req/s, onboarding via `onboarding@qcex.com`) — aimed at FCMs/IBs/ISVs and almost
certainly overkill at this fund's position sizes. The retail Ed25519 REST/WS path is the
right target.

### What remains genuinely unclear

1. **The authoritative excluded-state list.** Only secondary/affiliate sources found.
2. **Whether the US ToS permits automated/algorithmic trading.** Official SDKs, a
   developer portal, FIX, and market-maker incentive programs make API trading plainly
   *intended*, but the contractual language was not verified.

Both need either the in-app KYC flow or a rendered read of `polymarket.us/tos` — neither
is resolvable from static public sources.

> **Source-quality warning.** Several high-ranking search results
> (`polymarketexchange.com`, `docs.polymarketexchange.com`, `agentbets.ai`, `quantvps.com`,
> `tradingvps.io`) are unofficial third-party mirrors / SEO sites. Nothing above relies on
> them. A mirrored doc site under a lookalike domain is exactly the shape of a phishing
> surface for API credentials — **do not generate or paste Polymarket API keys anywhere
> but `polymarket.us/developer`.**

### Implication for the project

- **Resolves the "critical open item" at `REPORT_Live_InPlay_Agent_2026-07-19.md:127`
  affirmatively.** Polymarket execution is no longer blocked on API availability in
  principle. It is blocked on KYC onboarding, which is a human task and cannot be done by
  an agent.
- **`src/polymarket_client.py`'s `place_order()` / `cancel_order()` target the offshore
  CLOB and are unusable from the US** (close-only geoblock). Any future Polymarket
  execution needs a new client against `api.polymarket.us` with Ed25519 signing. Budget
  for a rewrite, not an adaptation.
- The YES-only trading model and USD/FCM settlement both differ enough from Kalshi that
  cross-venue position and risk accounting would need real work — this is not a
  drop-in second venue.
- Nothing here should be actioned without a human decision: account creation and KYC
  require the user personally.

---

## Q3 — `inplay-basis`: minimal Kalshi-vs-Polymarket in-play basis collector

**ANSWER: The gating blocker is GONE — Polymarket now lists live MLB regular-season
markets, and both venues have tradeable MLB game markets today. The repo already has
essentially every client needed. Estimated effort: ~1 day to build, then passive
collection.**

**Confidence: HIGH** on the blocker being cleared (direct API observation).
**MEDIUM** on the study being *informative*, because Polymarket's MLB books are thin —
see the liquidity caveat, which is the real risk to this question.

### (a) The stale blocker — RESOLVED

Two places in the repo say this is blocked:

- `PROJECT_STATUS.md:593` — "MLB regular season started March 20 but Polymarket's
  `series_id=3` still returns stale spring training markets."
- `manager/registry.yaml:211` — "`baseball_mlb` sport_override disabled pending
  Polymarket regular-season markets."

**Both are now STALE.** Queried 2026-07-21T04:08Z:

`GET https://gamma-api.polymarket.com/events?closed=false&series_id=3&order=startDate`
returns **100 open events**, with start dates running **2026-05-04 through 2026-07-20**
— i.e. current-day regular-season games, not spring training. Sample of the most recent:

```
2026-07-20T13:06Z  Cleveland Guardians vs. Tampa Bay Rays
2026-07-20T13:08Z  Los Angeles Dodgers vs. New York Mets
2026-07-20T13:08Z  Houston Astros vs. Chicago White Sox
2026-07-20T04:47Z  Athletics vs. Arizona Diamondbacks - Player Props
```

Polymarket is additionally listing **Player Props** and **First 5 Innings** events per
game, so the market set is richer than plain moneyline.

Kalshi side confirmed live in parallel: `GET /markets?series_ticker=KXMLBGAME&status=open`
returns open markets (e.g. `KXMLBGAME-26JUL231840KCDET-KC`). Both venues are live on the
same games, and `PROJECT_STATUS.md:593` already notes team-name formats match between
venues, so matching should work.

**Action item independent of this study:** update `PROJECT_STATUS.md:593` and
`registry.yaml:211`, and re-evaluate whether P-006's `baseball_mlb` sport_override can be
re-enabled. That is a separate decision from the basis study but is unblocked by the same
finding.

### Liquidity caveat — the real risk

Polymarket's MLB books exist but are **thin, with a wide inside market.** Sampled 8 MLB
moneyline markets via `https://clob.polymarket.com/book?token_id=...`:

| Event | liquidity | best bid | best ask | spread | depth (b/a) |
|---|---|---|---|---|---|
| Yankees vs. Phillies | $881 | 0.19 | 0.59 | 0.40 | 16/22 |
| Mariners vs. Rangers | $814 | 0.28 | 0.68 | 0.40 | 20/19 |
| Reds vs. Cardinals | $701 | 0.15 | 0.49 | 0.34 | 14/21 |
| Astros vs. White Sox | $879 | 0.16 | 0.54 | 0.38 | 14/22 |

A near-uniform ~40¢ top-of-book spread across unrelated games, on ~$700–900 of
liquidity, means **the book is layered but the inside is nearly meaningless.** First 5
Innings markets are worse (spreads 9¢–93¢).

Design consequence: **do not measure basis at top-of-book.** Top-of-book basis would be
dominated by Polymarket's own spread rather than by genuine cross-venue disagreement.
Record midpoint and depth-weighted price, and log the spread alongside every observation
so thin quotes can be filtered out in analysis rather than silently polluting it.

### (b) Minimal collector spec

Everything needed already exists — **no new client code.**

- `src/polymarket_client.py` — `PolymarketClient` with `get_sport_events(series_id=3)`,
  `get_book(token_id)`, `get_midpoint()`, `get_price()`, `find_series_id()`. Gamma +
  CLOB hosts already wired. **Note: this client also has `place_order()` / `cancel_order()`
  — the collector must use read paths ONLY.**
- `src/kalshi_public.py` — `KalshiPublic.open_markets(series_ticker)` and
  `.orderbook(ticker)`, already handling the dollars/sub-penny `orderbook_fp` quirk.
- `src/cross_venue_matcher.py` — `CrossVenueMatcher.match_all()`,
  `normalise_market_title()`, city→team resolution. Built for exactly this pairing.

**Proposed: `scripts/collect_inplay_basis.py`** (standalone, like `run_golf_maker.py` —
NOT a pod, NOT in `pods.active`).

1. On start and every ~10 min, refresh the matched pair set: Kalshi `KXMLBGAME` open
   markets × Polymarket `series_id=3` events, paired via `CrossVenueMatcher`.
2. Every 30–60 s for each matched pair, snapshot both books and append one row.
3. Only sample while a game is actually in play (between Kalshi `open_time` and close) —
   that in-play window is the whole point of the question.
4. Append-only JSONL under `data/inplay_basis/YYYY-MM-DD.jsonl`.

Row schema (deliberately raw — store inputs, compute basis in analysis):

```
ts_utc, game_id, kalshi_ticker, pm_token_id, team,
k_yes_bid, k_yes_ask, k_mid, k_depth_usd,
pm_bid, pm_ask, pm_mid, pm_liquidity_usd,
basis_mid            # pm_mid - k_mid
basis_tradeable      # pm_bid - k_yes_ask  and  k_yes_bid - pm_ask
game_state           # inning/score if cheaply available, else null
```

Guardrails: read-only (no order paths); throttle via the clients' existing rate limiting;
never let a collector failure touch the live engine — separate process, separate log.

> **Cross-link to Q2 — matters for what this study can lead to.** Read-only collection
> against the offshore Gamma/CLOB hosts works fine from the US and was demonstrated above,
> so the collector as specified is unaffected. But per Q2, the offshore API is
> **close-only for US users**, so if the basis study finds a tradeable edge, it could NOT
> be executed through `PolymarketClient.place_order()`. Execution would require the
> separate `api.polymarket.us` stack, which requires KYC onboarding plus a new Ed25519
> client. **Treat this as a measurement study whose positive outcome triggers a
> meaningful build + onboarding project, not a quick path to a live cross-venue trade.**

Analysis after ~2–3 weeks: is `basis_mid` persistently signed and larger than combined
fees + half-spreads? Per `REPORT_Live_InPlay_Agent_2026-07-19.md:127`, if the pre-game
fee-corridor result breaks down in-play, that is a second strategy.

### (c) Effort estimate

| Task | Estimate |
|---|---|
| `collect_inplay_basis.py` reusing the three existing clients | 4–6 h |
| Verify `CrossVenueMatcher` on live MLB pairs (untested for MLB — the override has been off) | 1–2 h |
| systemd unit + log rotation, mirroring `betting-live-maker` | 1 h |
| Passive collection | 2–3 weeks wall-clock, no attention |
| Analysis notebook | 3–4 h |

**~1 day of build, ~half a day of analysis, 2–3 weeks of waiting.** Cheap. The main
uncertainty is not effort, it is whether Polymarket MLB liquidity is deep enough for the
measured basis to mean anything — which the collector will itself answer within days,
since it logs spread and depth on every row. Recommend building it and re-assessing after
one week of data rather than committing to the full three weeks up front.

---
