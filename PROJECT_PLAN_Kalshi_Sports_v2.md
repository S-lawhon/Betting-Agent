# Kalshi Sports Betting — Project Plan v2 (Focused Rebuild)

*Created 2026-07-18. Supersedes the broad multi-venue "pod shop" direction. Keep prior notes/infra; this narrows the mission.*

---

## 0. Strategic reset — the lesson learned

The v1 system spread across venues (Kalshi, Polymarket, Deribit) and strategy types (arb, promos, consensus, crypto options). The edge analysis showed the diffusion cost us: only the **Kalshi + sharp-line** approaches (P-001 moneyline, P-014 live) were well-calibrated; the cross-venue/crypto pods either couldn't be trusted (paper arb) or lost money (P-013: −$2,094, significant negative edge).

**New mission:** Kalshi-only. Per-sport predictive models. **Hybrid** approach — build our own model, but benchmark/calibrate it against sharp bookmaker closing lines (Pinnacle/Circa). Prove edge via **Closing Line Value (CLV)** in paper before risking real money.

---

## 1. Which sports, in what order (research verdict)

**Prioritization = mispricing opportunity × validation speed × data availability × modelability, gated by seasonality (mid-July 2026).**

| Priority | Sport | Why | When |
|---|---|---|---|
| **1st — NOW** | **MLB** | Only major league **in season now** → immediate live CLV data. Highest volume (~2,430 games/season) → fastest statistical validation. Deep free public data (pitcher/park/lineup). Tight 1–2¢ Kalshi spreads on popular games. | Build immediately |
| **2nd** | **NBA** | Deepest Kalshi liquidity + tight spreads; ~1,230 games/season (good validation speed); actively studied (documented live underreaction). | Pre-build over summer, deploy at Oct 2026 tip-off |
| **3rd** | **NFL** | Extremely liquid per game, but only ~272 games/season → **slow** CLV accumulation; each game high-variance. | Pre-build over summer, deploy Sep 2026 |

**Explicitly deprioritize / avoid first:** niche & thin-liquidity markets (tennis beyond majors, minor soccer, golf, MMA props, chess/darts/cricket/esports, low-profile player props) and early-season markets. Wide spreads, poor fills, and — critically — **CLV itself becomes an unreliable signal** where no sharp, efficient closing line exists. Our entire validation method depends on a trustworthy sharp close, so only trade sports where Pinnacle/Circa lines are liquid via the-odds-api.

---

## 2. ⚠️ The caveat that must shape everything: mispricing ≠ net edge

The strongest (academic) sources deliver a warning we must design around:

- **Kalshi mispricings are real but do NOT survive trading costs as simple arbitrage.** Whelan's ~300k-contract study documents a large favorite-longshot bias (cheap <10¢ contracts systematically overpriced). An arXiv NBA study (1,438 games) shows live prices underreact (~0.64-for-1 drift) — but **explicitly finds executable returns go negative once bid-ask costs are imposed.**
- **Kalshi fees peak exactly where our contracts live.** Taker fee = `round_up(0.07 × C × P × (1−P))`, a parabola maxing at **~1.75¢/contract at P=0.50** — i.e., competitive game moneylines. Maker fee is ¼ of that (`0.0175 ×`). (Do **not** assume a flat "1%" — that claim was refuted.)
- **Implication:** the engine must model the **full price-dependent fee + live bid-ask spread** and gate on *net* edge. A midpoint-only model will systematically overstate edge and repeat the v1 paper-arb mistake. Prefer **maker/limit orders** (¼ fee) and near-50¢ discipline.

**Design consequence:** we may find that *no* major-sport model clears the fee+spread hurdle on ~50¢ games. That is an acceptable, valuable finding — the plan is built to discover it fast and cheaply (paper), not to assume success.

---

## 3. The validation engine — CLV harness (build FIRST, before any model)

CLV vs a **de-vigged Pinnacle/Circa closing line** is the accepted, fastest proxy for genuine edge:

- **~10× variance reduction vs P&L** (even-money P&L SD ≈ 1.0 vs CLV SD ≈ 0.1). Significance in **as few as ~50 bets** for a large edge, vs several thousand for P&L. (Sample size scales with edge magnitude — no fixed "1,000-bet" rule; that was refuted.)
- Beating the no-vig close by X% ≈ X% expected value (holds cleanly in the ~35–65% probability band; degrades at the extremes due to favorite-longshot bias).
- **This replaces raw P&L as our north-star metric.**

**What to log on every paper bet** (new fields on the trade record):
- Kalshi entry price + side, timestamp
- De-vigged sharp fair prob **at moment of execution** (Pinnacle/Circa via the-odds-api)
- De-vigged sharp fair prob **at close** (line at k/first-pitch)
- Modeled fee + half-spread paid → **net CLV** and **gross CLV**
- Our model's fair prob (to compare model-vs-sharp separately from sharp-vs-Kalshi)

This lets us decompose: is edge coming from our model, or just from Kalshi lagging the sharp line?

---

## 4. Architecture rebuild — keep / strip / build

**✅ KEEP (reusable, venue-agnostic core)**
- `pods/kalshi_moneyline.py` (P-001) — pure Kalshi + sharp; the template for per-sport pods
- `pods/live_game_pod.py` (P-014) — Kalshi + sharp consensus; hold for a later live phase
- `elo_model.py` — foundation for team/tennis Elo
- `edge_calculator.py` (now-correct Kelly), `capital_allocator.py`, `aggregate_risk.py`
- `trade_store.py` + `trade_log_schema.py` (extend for CLV fields), `live_odds_poller.py` (sharp odds)
- Engine: `base_pod.py`, `pod_runner.py`, `main.py`, `config_loader.py`, `web_dashboard.py`
- Salvage the **sharp-consensus fair-prob logic** from `polymarket_consensus.py` (P-006) — keep the math, cut the Polymarket leg

**🗑️ STRIP (multi-venue cruft that caused the "too broad" problem)**
- Clients: `polymarket_client/matcher/settler.py`, `cross_venue_matcher.py`, `deribit_client.py`, `forecastex_client.py`, `nowcast_client.py`
- Pods: P-002 (cross-venue arb), P-013 (crypto — already KILL), P-004 (ForecastEx), P-009 (signup bonus), P-010 (odds boost), P-012 (macro nowcast — not sports)

**🔨 BUILD (the new per-sport layer)**
- A `SportModel` interface: features → calibrated fair probability, one implementation per sport, feeding the existing edge/Kelly/risk pipeline
- A **net-edge gate**: `net_edge = model_prob − kalshi_price − fee(P) − half_spread`; only bet when positive
- The **CLV harness** from §3 (the single highest-leverage new component)
- Per-sport calibration reports (model vs sharp, sharp vs Kalshi)

---

## 5. Sequenced roadmap (with exit criteria)

**Phase 0 — Strip & harden (days)**
- Disable all non-Kalshi-sports pods in `config_multi_pod.yaml`; keep P-001 running
- Remove/retire the stripped clients & pods; confirm engine boots Kalshi-only
- Extend the trade-log schema with CLV fields; build the CLV harness + net-edge gate
- **Exit:** Kalshi-only engine running P-001, logging net/gross CLV vs Pinnacle on every paper bet

**Phase 1 — MLB model + validation (now → end of MLB regular season)**
- Acquire data (see §6); build MLB fair-prob model (pitcher/park/lineup/bullpen)
- Run paper on MLB game-outcome contracts; accumulate CLV
- **Exit / go-no-go:** statistically significant **positive net CLV** vs de-vigged Pinnacle over the sport's sample (target a few hundred bets), AND simulated net-of-fee edge > threshold. Only then consider small real money.

**Phase 2 — NBA & NFL pre-build (summer) → deploy at season open**
- Reuse the MLB scaffolding; build NBA (pace/efficiency/rest) and NFL (EPA/rest) models
- Deploy in paper at NFL (Sep) and NBA (Oct) openings; run the same CLV gate

**Phase 3 — Graduate validated sport(s) to small real money**
- Only sports that cleared the Phase-1/2 CLV + net-edge gates
- Start small; keep CLV monitoring live; scale only with sustained positive net CLV

---

## 6. Data to acquire, per sport

- **MLB (now):** the-odds-api Pinnacle/Circa MLB lines; Baseball Savant **Statcast park factors** (free); probable pitchers + lineups (MLB StatsAPI); pitcher metrics (FIP/xFIP/SIERA), bullpen, injuries
- **NBA:** the-odds-api NBA lines; pace/efficiency (eFG%, PIE), rest/back-to-backs, injuries/lineups
- **NFL:** the-odds-api NFL lines; EPA/efficiency (nflverse), rest/travel, injuries
- **Cross-cutting:** verify the-odds-api gives **timely** Pinnacle/Circa closing lines with acceptable latency vs Kalshi execution (an open question flagged below)

---

## 7. Open questions to resolve early (from the research)

1. **MLB per-game Kalshi liquidity** vs flagship NFL/NBA — does MLB's volume advantage get offset by worse fills? (Measure real spreads in Phase 1.)
2. **How tightly does Kalshi track de-vigged Pinnacle per sport?** The single most important thing to measure early — efficient close = reliable CLV; inefficient = meaningless CLV.
3. **Net realistic edge after the full fee formula + spread** on near-50¢ games — does any major-sport model clear it at all?
4. **the-odds-api granularity/timeliness** for Pinnacle/Circa closing lines at the moment of Kalshi execution.
5. **Regulatory (time-sensitive):** a CFTC proposal could restrict injury/prop markets — keep game-outcome focus, monitor.

---

## 8. Success metric (single sentence)

**A sport is "proven" when it shows statistically-significant positive *net* CLV (after the actual fee formula + spread) against the de-vigged sharp close, over a sport-appropriate sample — measured in paper, before one real dollar is risked.**

---

### Key sources
- Kalshi fee formula — help.kalshi.com/en/articles/13823805-fees (primary)
- Favorite-longshot bias / no-arbitrage — Whelan, karlwhelan.com (~300k contracts); arXiv 2606.07811 (NBA live, 1,438 games)
- CLV as fast edge proxy — Buchdahl via pinnacleoddsdropper.com; corroborated by DataGolf/20k-bet studies
- MLB park factors — baseballsavant.mlb.com/leaderboard/statcast-park-factors

*Caveat: the MLB-first ordering is a defensible synthesis of seasonality + volume + spreads + data availability, not a single cited ranking.*
