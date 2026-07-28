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

## 2.1 The same cells on the widened cache (n = 404) — added 2026-07-28

The table above is the **published** result and rests on a frozen 364-market
universe (`published_universe_364.json`). The 2026-07-28 widening run added 40
markets / 3 tournaments to `data/leader_trades/`, so a run over the *whole
cache* no longer reproduces it. **That is a different sample, not a different
harness**: pinned to the 364, the untouched harness still reproduces every cell
exactly (`backtest_fade_fills.py --validate`, PASS as of 2026-07-28).

Recorded here because P-022 is **GREEN-LIT and armed live**, so a movement in
its measured edge on a larger sample is worth knowing even when the added block
is too small to decide anything.

| H (h) | offset | net ¢/ct **n=364** | net ¢/ct **n=404** | Δ | 95% CI n=364 | 95% CI n=404 | filled 364→404 | tourn (+) 364→404 |
|------:|-------:|---:|---:|---:|---|---|---|---|
| 12 | 0.00 | +2.1 | **+1.6** | −0.5 | [+0.8, +3.5] | [−0.1, +3.1] | 211→232 | 19 (16) → 22 (16) |
| **12** | **0.02** | **+3.4** | **+2.6** | **−0.8** | **[+1.7, +5.1]** | **[+0.1, +4.5]** | 171→183 | 19 (16) → 22 (17) |
| 12 | 0.04 | +4.7 | **+3.7** | −1.0 | [+2.7, +6.7] | [+0.9, +5.9] | 147→155 | 19 (16) → 22 (18) |
| 6 | 0.00 | +0.5 | **−0.7** | −1.2 | [−1.9, +3.0] | [−3.7, +1.9] | 112→123 | 15 (—) → 17 (9) |
| 6 | 0.02 | +1.7 | **+0.2** | −1.5 | [−1.0, +4.6] | [−3.6, +3.2] | 97→104 | 15 (—) → 17 (10) |
| 24 | 0.00 | +2.4 | **+1.9** | −0.5 | [+0.0, +4.3] | [−0.4, +3.8] | 172→197 | 19 (16) → 22 (16) |
| 24 | 0.02 | +3.5 | **+2.9** | −0.6 | [+0.5, +5.9] | [−0.4, +5.3] | 138→155 | 19 (16) → 22 (17) |

Robustness cells (§3) at n=404, headline H=12 / +0.02:
**17 of 22 tournaments positive** (published 16 of 19); **leave-one-out drop
USO26 → +2.15¢** (published +3.04¢), all 22 jackknives still positive, min
**+2.15¢**.

**Reading it, without overreading it:**

- **Every one of the seven cells moved DOWN**, by 0.5–1.5¢. That uniformity is
  not seven independent draws — the same 3 added tournaments
  (`ISPHWSO26`, `ISHSO26`, `KIN26` / `3MO26`, −12.33¢/ct as a block) sit in all
  seven, so this is one adverse draw seen seven times, not seven confirmations.
- **The qualitative shape is unchanged**: offset still monotonically helps
  (+1.6 → +2.6 → +3.7), H=6 is still the collapse, H=12/24 still the tradeable
  window, and the fill-vs-posted adverse-selection gap is still there
  (E[settle] filled 0.051 vs posted 0.021 at the headline).
- **What did change is the confidence, not the sign.** At n=364 four cells had a
  CI strictly above zero; at n=404 only three do (12/+0.02, 12/+0.04, and
  12/0.00 is now marginal at −0.1). The headline's lower bound went **+1.7 →
  +0.1**. The leave-one-out margin shrank correspondingly (+3.04¢ → +2.15¢).
- **The two H=6 cells now straddle or sit below zero.** §2's "post early"
  conclusion is *strengthened* by this, not weakened — H=6 was already the
  marginal row and is now the negative one.

**Not acted on.** No parameter, band, offset, window or gate changed on the
strength of this table; the live pod's quoting parameters are unchanged. The
one consequence that was already taken is the decision threshold: sizing
against +2.57¢ instead of +3.4¢ raised **T = 14 → 24** (`P022_DECISION_RULE.md`
Amendment 1, made at T = 0). Full analysis of the added block, including its
CI of [−38.5, +7.0]¢/ct and the INCONCLUSIVE verdict, is in
`REPORT_P022_Widened_2026-07.md`.

> **Reproduce both rows:**
> ```
> python3 golf_quirks_research/backtest_fade_fills.py --validate            # n=364, must PASS
> python3 golf_quirks_research/backtest_fade_fills.py --universe all        # n=404
> ```
> `--validate` is pinned to the 364 tickers and is **independent of cache
> growth** — that is deliberate. A reproduction test that reads whatever is in
> `data/` is a cache fingerprint, not a reproduction test, and it broke the
> moment the cache legitimately grew.

---

## 2.2 Is that decay? No — it is two markets — added 2026-07-28

§2.1 records the movement and correctly declines to act on it. This section
answers the question §2.1 leaves open, because "armed live and the edge just
dropped 0.8¢" is the kind of thing that gets acted on by the next reader if
nobody decomposes it.

**The added block is a real out-of-sample window.** All 40 added markets sit in
**4 tournaments absent from the published universe** (`3MO26` +15, `KIN26` +11,
`ISPHWSO26` +10, `ISHSO26` +4), closing **2026-07-23 → 07-25**, strictly after
the published block's 05-18 → 07-18. None of the 364 published tickers is
missing. So this is a time extension, not a band widening — a genuine forward
test, and it came back negative. (§2.1 says "3 tournaments"; that is the count
that *produces fills* at the headline cell. Four were added.)

**And it is 5.3% of the sample, carried by two fills.** At H=12/+0.02 the added
block is 12 filled markets and 228 contracts. Every dollar of its −12.33¢/ct
comes from two markets where the faded longshot actually won:

| ticker | sold YES @ | settled | contracts | P&L |
|---|---:|---:|---:|---:|
| `KXLIVR1LEAD-KIN26-LHER` | 0.06 | **$1.0000** | 25 | **−$23.50** |
| `KXPGAR3LEAD-3MO26-JKOI` | 0.10 | **$1.0000** | 25 | **−$22.50** |
| *the other ten fills* | — | $0.0000 | 178 | **+$17.85** |

The other ten fills are **all positive**. Remove these two and the added block
is profitable. `KIN26` is a three-market tournament, and one of the two losses
is inside it — which is why it aggregates to −38.49¢/ct and why the block CI
runs to [−38.49, +7.00].

**Against the strategy's own base rate this is unremarkable.** The edge exists
because faded names win far less often than their price implies. In the
published block they win **6 of 171 filled markets = 3.5%** at a mean quote of
**7.5¢**. The added block ran **2 of 12 = 16.7%** at a 9.6¢ mean quote. Under
the published rate:

> **P(≥ 2 winners in 12 fills) = 0.064.** Expected winners: 0.4.

Uncommon, not extraordinary — and observed **post-hoc**, because we went
looking at the stretch that looked bad. It is not significant at any
conventional threshold.

**Three further reasons not to call it decay:**

- The added block's CI **[−38.49, +7.00] contains the published +3.41¢**. The
  hypothesis "same edge" cannot be rejected.
- §2.1's "one adverse draw seen seven times" is **confirmed**: the seven cells
  draw from a union of 31 added markets and are *nested subsets* of each other
  (the 12 filled at 12/+0.02 are a subset of the 21 filled at 12/+0.00). Seven
  views of one draw.
- Three calendar days cannot speak to decay regardless of what they show.

**A tournament-clustered test is the wrong instrument here, and it lies.** A
permutation test resampling 3 tournaments from the published 19 puts
P(draw ≤ −12.33¢/ct) at **0.000** — which reads as decisive and is an artifact.
`KIN26` has three filled markets; one −$23.50 fill drags its *tournament*
average to −38.49¢, a value nothing in the published tournament distribution
(worst: `AND26` at −11.9¢) can reproduce by construction. Tournament-level
aggregation manufactured an impossible observation out of a single market. The
market-level binomial above (P=0.064) is the right test and points the other
way. **Clustering by tournament is correct for the CI on a large sample and
actively misleading on a 3-tournament block where tournaments hold 3–5
markets.**

**What this block IS evidence for — the tail, not decay.** These two fills are
precisely the failure mode §5 already names: losses concentrate where a faded
name actually leads. At a 6¢ quote a winner costs **15.7× the credit**, so 2 of
183 filled markets moved the headline 0.84¢. That is a *sizing and per-name-cap*
finding, and the response to it has already been taken (T = 14 → 24,
`P022_DECISION_RULE.md` Amendment 1) — not an edge-decay finding.

**Verdict: NOT DECAY, and this sample could not have demonstrated decay either
way.** Nothing changed. The forward gate remains the instrument that decides
P-022, which is exactly what it is for.

> **Reproduce:**
> ```
> python3 golf_quirks_research/analyze_p022_added_block.py
> ```

---

## 3. Robustness

Per-tournament (H=12, offset +0.02): **16 of 19 tournaments positive**. The 3
negatives are precisely the tournaments where faded names led — AND26 (−11.9¢,
had multiple names lead), THCCBN26 (−3.4¢), COPC26 (−0.5¢). The winners are
broad and modest (+1.5¢ to +9.0¢).

**Leave-one-out:** removing the single largest positive contributor (USO26)
moves the overall from +3.4¢ to +3.0¢ — the edge is *not* a one-tournament
artifact. Every jackknife stays comfortably positive.

(On the widened n=404 cache both cells weaken but hold: 17 of 22 positive,
drop-USO26 → +2.15¢, all 22 jackknives positive. See §2.1.)

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
