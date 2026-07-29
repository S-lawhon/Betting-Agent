# SPEC P-030 — Cross-Venue Lock: Kalshi ↔ ProphetX

*Drafted 2026-07-29. Status: **NOT GREEN-LIT.** Read-only falsification only. No connector, no order path, no capital.*
*Parent research: `Deep Research R6 - Cross-Venue Arb (Kalshi, ProphetX, Novig) 2026-07.md`*

---

## 1. Thesis

ProphetX became a CFTC DCM+DCO and launched nationwide 2026-06-18. It lists economically identical binary contracts to Kalshi on the same games, runs a price-time-priority CLOB, and **explicitly permits algorithmic trading for all users** (ToS §3.7 carve-out). It is six weeks old, publishes no depth data, and **is covered by zero arbitrage screens on the market**. If executable cross-venue gaps exceed the combined fee-and-tax corridor with enough frequency and depth, a two-leg lock captures them.

**This is a STRUCTURAL claim, which is the class that survives on this book** (`deepev_research_r2` house pattern: external-information and structural/mechanical edges survive; behavioral and pure-MM edges have failed on contact). It is *not* a claim that either venue is mispriced in any behavioral sense.

## 2. Economics — the corridor

```
Kalshi   fee = 0.07·P(1−P)   upfront, per contract, win or lose
                (maker 0.0175·P(1−P), but ONLY on 130/12,299 series —
                 and KXMLBGAME / KXNBAGAME / KXNHLGAME / KX*MATCH are all in that 130)
ProphetX fee = 2% of net gain, paid ONLY on the leg that wins
                → E[fee] = 0.02·q(1−q), same parabola, coefficient 2

combined E[fee]           = 0.09·P(1−P)                    = 2.25¢ at P=0.50
after-tax BE (gambling)   = [9 + 20τ]·P(1−P)               = 3.85¢ at P=0.50, τ=0.32
Rule 4.6 bust ceiling     = 0.15 × (ProphetX leg price)    = 7.50¢ at q=50¢
```

⚠️ **ProphetX's fee is stochastic and paid on the winner only. It must NOT be modeled as a per-contract cost** — `src/kalshi_fees.py`'s shape does not transfer. A ProphetX fee module needs its own signature.

**Viable window (floor vs ceiling), whole gap assumed on the ProphetX leg:**

| Kalshi P | floor | ceiling | window |
|---|---|---|---|
| 0.50 | 3.85¢ | 7.50¢ | **3.65¢** |
| 0.60 | 3.70¢ | 6.00¢ | **2.30¢** |
| 0.70 | 3.23¢ | 4.50¢ | 1.27¢ |
| 0.80 | 2.46¢ | 3.00¢ | 0.54¢ |
| 0.90 | 1.39¢ | 1.50¢ | **closed** |

**Trade mid-price only, P ∈ [0.40, 0.65].** The fee parabola points at the tails; Rule 4.6 closes them. Ceiling wins.

## 3. Universe — v1 is deliberately narrow

**IN:** MLB moneyline · NBA moneyline · NHL moneyline · tennis match winner.
Criteria: two-outcome, no push, no draw, full-game, overtime/extra-innings included on **both** venues (verified aligned).

**OUT, each for cause — not deferred for convenience:**

| Excluded | Reason |
|---|---|
| **Golf (all)** | ProphetX's certified golf spec cross-references dead-heat rules to "Rule F," which is *Player Must Start*. **Dead heats are never defined.** This book has already been burned once by assuming golf tie payouts (`golf_template_sweep_2026-07-27`). Requires written confirmation from ProphetX before reconsideration |
| **Spreads / totals** | Requires Kalshi's strike grid and ProphetX's sportsbook line grid to coincide. Unverified. Also: ProphetX **voids** an exact-line landing, Kalshi does not push |
| **Player props** | Opposite DNP conventions — Kalshi marks to last fair price, ProphetX voids and refunds |
| **P > 0.75** | Rule 4.6 window closed (§2) |
| **Novig** | Sweepstakes-only; ToS §3.7 bans bots on retail accounts; real-money DCM not launched |

## 4. Known hazards to encode

| # | Hazard | Requirement |
|---|---|---|
| H1 | **Settlement convention is opposite.** ProphetX voids + refunds at stake; Kalshi marks to *"last fair market price… in its sole discretion"* and never refunds | On a ProphetX void the Kalshi leg is **naked**. Must detect `winning_status ∈ {no_result, draw, push}` and immediately flatten or re-hedge. MLB postponement ≈2% of games |
| H2 | **Postponement windows differ** — Kalshi 48h, ProphetX **same calendar day** | Same handler as H1; this is the most common trigger |
| H3 | **Rule 4.6 busts.** Band is 15% *of price*, multiplicative. Anyone — counterparty or third party — may request, within 15 min, waivable at ProphetX's discretion. 4.6(f) accepts "automated trading malfunction," so a counterparty's bot bug can void your good fill. Final, no appeal | Never take a ProphetX fill more than `0.15 × price` from FMV. Log every fill's distance from FMV; alert on any bust |
| H4 | **`wiped` status** — all pre-match orders auto-cancelled when an event goes live | Any maker-side variant must re-arm at transition |
| H5 | **Capital-split drift.** Winner's venue receives all cash; the split is a random walk. p05 over 200 locks strands **6pp** of the book on one venue (12pp over 50). Kalshi withdrawals reportedly 3–30 days | Buffer on both venues + a rebalancing loop. Size assuming ~2× nominal capital requirement |
| H6 | **§165(d):** only 90% of wagering losses deductible from 2026. Under gambling characterization the phantom tax is **213% of edge at a 3¢ gap** (vs 9% for P-022) | **Gate 0.** See §5 |
| H7 | Kalshi position accountability **$25k per strike per member** | Per-strike cap in sizing |
| H8 | ProphetX API rate limits **unpublished** | Ask during onboarding; back off conservatively until known |

## 5. Falsification — pre-registered, read-only

**Gate 0 (runs first, costs one hour, can kill everything):** put §165(d) / §1092 characterization of CFTC event contracts to a CPA. Gambling treatment → gate 2 threshold is **3.85¢** and the published 2–4% cross-venue basis is mostly underwater. Capital treatment → **2.25¢** and it is live. *Do not spend 14 days collecting before asking a question answerable this week.*

**Instrument:** extend `scripts/collect_inplay_basis.py` — it already has this exact shape for Kalshi↔Polymarket, a systemd unit (`scripts/betting-inplay-basis.service`), Kalshi-abbreviation canonicalisation, and append-only JSONL to `data/`. Add a ProphetX read-only leg (Market Data API). **60s snapshots, 14 days, top-of-book + depth on both sides.** No `place_order` import, per the existing collector's discipline.

**Gates — all four must pass. Event-clustered CIs. No post-hoc corridor tuning.**

| # | Metric | KILL if |
|---|---|---|
| 1 | Median ProphetX top-3-level depth, in dollars, on matched markets | **< $2,000** |
| 2 | P(**executable** gross gap > 3.85¢) at P ∈ [0.40, 0.65] — crossing both spreads, snapped to each venue's grid | **< 2%** of observations |
| 3 | Median survival of a qualifying gap | **< 30s** |
| 4 | Implied capturable = frequency × depth × matched-market count | **< $5,000/day** |

Gate 2 uses the **after-tax** floor regardless of Gate 0's outcome — if Gate 0 returns capital treatment, passing at 3.85¢ simply means more margin, not a re-run at 2.25¢.

**P-020 precedent, binding here:** that study killed Polymarket-as-oracle because Kalshi was *both deeper and sharper* (Brier 0.1373 vs 0.1445; optimal blend weight on the other venue exactly **0.0**). Its §8 pre-registered the reopening condition — *measure depth dollar-for-dollar before building the connector*. Gate 1 is that condition.

**Price granularity is already cleared.** ProphetX's American-odds ladder is 0.168–0.758 pp per tick — finer than Kalshi's 1¢ grid everywhere. Kalshi is the binding grid. Not a gate.

## 6. Build cost, if it survives

**Cheap — the seams exist.** `BasePod` already carries `venue`; `@register_pod` is zero-friction; `build_venue_clients()` takes one try/except; `MultiExecutor` takes one `elif` + a `_execute_prophetx`; `aggregate_risk` is already venue-aware. Template: `src/pods/forecastex_kalshi_arb.py` + `src/forecastex_client.py` (~21KB, dual-venue, emits `venue="multi"`).

**Expensive — fees and settlement.** `src/kalshi_fees.py` has no venue abstraction and ProphetX's fee is a different mathematical object (§2). `src/settlement_bridge.py`, the per-venue settlers, `trade_log_schema.py` (defaults `venue: "kalshi"`), and `base_pod.write_log()`'s Kalshi-settler shims all assume one venue. Estimate 2–4 weeks, dominated by settlement.

## 7. Prerequisites (free, do now, independent of the gates)

- Request ProphetX API access — Zendesk form or `marketmaking@prophetexchange.com`. **Sandbox with demo funds** + first-party Python scaffold at `github.com/betprophet1/mm-api-integration-guide`
- Email `developers@novig.com` — LP/EMM criteria + post-DCM fee schedule
- Gate 0 (§5)

## 8. Note: two adjacent opportunities rank above this one

Recorded here so the queue does not lose them — both use the same ProphetX integration and neither requires the arb to work.

1. **Fee routing.** ProphetX taker costs `0.02·P(1−P)` vs Kalshi's `0.07·P(1−P)` — **exactly 3.5× cheaper at every price**. Porting an already-validated *taker* edge (P-001) to ProphetX cuts fee load 72% with no new edge thesis. Higher expected value and far lower risk than P-030. Rule: **take on ProphetX, make on Kalshi's 12,169 fee-free series.**
2. **Designated market making.** ProphetX added Rulebook Chapter 8 (market makers, negotiated rebates) on **2026-07-27**, has no published designated MMs, and raised $35M on 2026-07-28 explicitly to buy liquidity. Separately, its **Make Volume Fee Rebate Program** (up to 80% marginal, open to all participants on objective thresholds) nominally **expires ~2026-08-29** — do not underwrite on it, but note the door is open now and may not stay open.
