# P-022 Phase 2 — Round-Leader Dead-Heat Fade: Maker-Fill Realism

**Betting Pod Shop · Phase-2 research (tick prints)**
**Prepared 2026-07-23**

Phase 1 ([REPORT_Golf_Quirks_2026-07.md](REPORT_Golf_Quirks_2026-07.md))
established that the round-leader dead-heat is real (37% payout haircut) and
that 5–10¢ leader names show a settled-data **average** edge of +4–6¢/ct when
you sell YES. It flagged the one thing settled averages cannot answer: this is
a **maker** fade, and a maker only fills when a real buyer lifts the ask —
disproportionately on names ticking *up* toward leading. That adverse
selection is what killed P-016 and left P-017's fade leg underpowered. Phase 2
replays actual tick prints to measure whether the edge survives it.

> **VERDICT: GREEN-LIGHT (qualified) → proceed to a paper pod with mandatory
> collateral caps.** The edge survives pessimistic through-fills *and* adverse
> selection: selling YES early (12–24h pre-round) at a small positive offset
> yields **~+3¢/ct net** (offset +0.02: +3.4¢, tournament-clustered CI
> [+1.7, +5.1]; range +2.1¢ at offset 0 to +4.7¢ at offset +0.04), **16 of 19
> tournaments positive**, robust to leave-one-out. The two caveats are size,
> not sign: **capacity is small** (~$140 P&L / ~$3.8k collateral over a month
> of 19 tournaments at 25-ct/name caps) and the **tail is real** (losses
> concentrate in tournaments where a faded name leads), so per-strike and
> per-tournament collateral caps are non-negotiable.
>
> **Per the agreed scope, no pod, config, or service was built. Nothing is
> live. This report is the gate.**

---

## 1. Method

- **Universe:** the 364 round-leader markets whose 12h pre-round anchor price
  sat in [0.03, 0.12] — the cheap lottery names the fade would quote on. Of
  these, only **10 actually led** (354 no, 5 scalar, 5 yes); the fade is
  betting that population stays cheap.
- **Data:** full public trade prints per market (`/markets/trades`), cached to
  `data/leader_trades/`. Kalshi's trade history reaches back ~1 month, so the
  effective sample is recent events (late-June → July 2026); this is the main
  limitation and shrinks with time as more events settle.
- **Fill model (pessimistic, mirrors `backtest_golf.py` leg B):** at decision
  time T = close − H hours, rest a YES ask at `quote_px = anchor_price +
  offset`. Fill **only** on a print with `taker_side="yes"` (a buyer lifting
  asks) at `yes_price` **strictly through** `quote_px`, up to a per-name size
  cap (25 contracts). Sold YES at `quote_px`; maker fee 0 (quadratic series);
  PnL/contract = `quote_px − settlement_value`. CIs bootstrap-clustered by
  **tournament**.
- **Adverse-selection diagnostic:** E[settlement | filled] (contract-weighted)
  vs E[settlement | posted]. Equal → fills are representative; filled ≫ posted
  → the fade is selling exactly the names that pay.

---

## 2. Adverse selection is real — and the edge survives it

Fill replay, sell YES, maker fee 0, strictly-through fills:

| H (h) | offset | posted | filled | fill rate | contracts | net ¢/ct | 95% CI | tourn | E[settle] posted | E[settle] filled |
|------:|-------:|-------:|-------:|----------:|----------:|---------:|--------|------:|-----:|-----:|
| 12 | 0.00 | 364 | 211 | 0.58 | 5160 | **+2.1** | [+0.8, +3.5] | 19 | 0.018 | 0.032 |
| 12 | 0.02 | 364 | 171 | 0.47 | 4061 | **+3.4** | [+1.7, +5.1] | 19 | 0.018 | 0.041 |
| 12 | 0.04 | 364 | 147 | 0.40 | 3506 | **+4.7** | [+2.7, +6.7] | 19 | 0.018 | 0.048 |
| 6 | 0.00 | 342 | 112 | 0.33 | 2765 | +0.5 | [−1.9, +3.0] | 15 | 0.020 | 0.060 |
| 6 | 0.02 | 364 | 97 | 0.27 | 2385 | +1.7 | [−1.0, +4.6] | 15 | 0.018 | 0.070 |
| 24 | 0.00 | 272 | 172 | 0.63 | 4234 | +2.4 | [+0.0, +4.3] | 19 | 0.017 | 0.028 |
| 24 | 0.02 | 274 | 138 | 0.50 | 3323 | +3.5 | [+0.5, +5.9] | 19 | 0.017 | 0.035 |

(`>=`-fill variant at H=12/offset 0: +2.5¢, CI [+1.3, +3.7], 67% fill — same
story, less pessimistic.)

**Reading it:**
- **Adverse selection is present and measurable.** At H=12 the names I actually
  fill realize **3.2¢** vs **1.8¢** for the posted population — filling ~doubles
  the realized value. All 10 leaders in the universe got me filled (their prices
  rose, generating the buy-throughs I sold into). This is exactly the mechanism
  Phase 1 warned about, and it *does* shave the naive +4¢ edge.
- **But it does not flip the sign.** The ~350 cheap names that bust still
  outweigh the 10 that lead: net **+2 to +5¢/ct**, CI excluding zero at H=12
  and H=24 across offsets. The premium collected on the many losers is larger
  than the blowups on the few leaders.
- **Post early.** H=6 (round underway) collapses to marginal — by then the
  through-fills are almost purely the eventual leaders (E[filled] 0.06–0.08,
  3–4× the posted rate) and the CI includes zero. The tradeable window is the
  pre-round evening (H≈12–24), before the round prices in who is contending.
- **A small positive offset helps.** Posting +2–4¢ above the touch collects
  more per fill and, net of the lower fill rate, raises ¢/ct — the extra
  premium beats the extra adverse selection.

---

## 3. Robustness

Per-tournament (H=12, offset +0.02): **16 of 19 tournaments positive**. The 3
negatives are precisely the tournaments where faded names led — AND26 (−11.9¢,
had multiple names lead), THCCBN26 (−3.4¢), COPC26 (−0.5¢). The winners are
broad and modest (+1.5¢ to +9.0¢).

**Leave-one-out:** removing the single largest positive contributor (USO26)
moves the overall from +3.4¢ to +3.0¢ — the edge is *not* a one-tournament
artifact. Every jackknife stays comfortably positive.

---

## 4. Economics & capacity (the binding constraint)

At a 25-contract/name cap, H=12, offset +0.02, over the ~1 month / 19
tournaments of available tick history:

- **P&L ≈ +$138** on **~$3,755 of collateral-at-risk deployed** →
  **~3.7% return on collateral per month** (paper, gross of slippage beyond the
  through-fill assumption).
- Mean tournament: 214 contracts filled, ~$198 collateral, +$7.3 P&L.
- **This is a small-capacity edge.** Scaling `quote_size` raises dollars but
  fills a smaller fraction of large prints and pulls in more adverse selection;
  it does not scale linearly. Realistic sizing is tens-to-low-hundreds of
  contracts per name. Treat P-022 as a modest, steady paper-CLV contributor,
  not a capacity play.

---

## 5. The tail, and the collateral caps it forces

Selling a 6¢ YES that leads outright loses ~94¢. Observed:
- **Worst single name: −$23.50** (25 ct sold ~6¢, settled $1). This is the
  per-strike blow-up, bounded only by the per-name size cap.
- **Worst tournament: AND26 −$8.17** (3 faded names led — within a tournament
  the names are correlated: one hot early wave, and ties mean several lead at
  once). Worst-tournament collateral deployed was $557.

**Mandatory caps for the Phase-2 spec (P-019 §4 pattern), sized on
collateral-at-risk, not contract count:**

1. **Per-strike (per-name) cap.** Hard-cap contracts per leader name so the
   max single-name loss `(1 − quote_px) × size` is ≤ ~0.5% of bankroll. At a
   $1,000 paper bankroll and ~6¢ quotes, that is ~5 contracts/name (the 25-ct
   figure above is the *research* cap, not a live sizing recommendation).
2. **Per-tournament collateral cap.** Cap total collateral-at-risk across all
   faded names in one tournament (all rounds) at ≤ ~5% of bankroll — the AND26
   case shows the within-tournament correlation that a per-name cap alone does
   not contain.
3. **Aggregate cap.** Total open fade collateral across all live tournaments
   ≤ ~15% of bankroll, and register it with `AggregateRiskGuard` via
   `reserve_trade` on approval (the golf reservation path already exists).
4. **Post early / expire.** Only quote in the H≈12–24h pre-round window; cancel
   unfilled quotes before the round's price discovery (the H=6 result shows
   late fills are adversely selected to marginal).

---

## 6. Recommendation

**Proceed to build a P-022 paper pod** — sell YES / make NO on 5–10¢
round-leader names, quote early, with the caps above — but scoped as a **modest
paper-CLV validator, not a capacity strategy.** Before writing it:

- The edge is proven on ~1 month of tick history (19 tournaments). **Re-run
  this replay as more events settle** to tighten the CI; the mechanism (rulebook
  dead-heat + longshot overpricing) is structural and will not decay, but the
  sample is still young.
- Reuse the existing golf settler (`KalshiGolfSettler`) — note it currently
  treats `result="scalar"` as a withdrawal void, which is correct for top-N but
  **wrong for round-leader**, where scalar is the $1/n dead-heat payout. A
  P-022 pod needs a settler that books scalar at `settlement_value_dollars`.
  (Flagged here so it is not missed at build time.)

Harness: `pull_trades.py` (tick cache) + `backtest_fade_fills.py` (replay);
results in `fade_fill_results.json`. Awaiting review before any pod is written.
