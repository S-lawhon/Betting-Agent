# Claude Code Task — R5 Follow-ups: the three cheap items the hunt produced

> Background: `Deep Research R5 - Five-Way Hunt 2026-07.md` (repo root). Five hunts, five KILLs, zero candidates. These are the only three live threads, all bounded. Do them in order; each has its own STOP.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper/demo; **no real orders, ever**). Public Kalshi API needs no auth: `https://api.elections.kalshi.com/trade-api/v2`.

**Data-shape traps — respect these or every count is wrong:**
- **84.8% of open markets are `KXMVE*` parlay dust** (362,671 of 425,682; 0.1% two-sided). EXCLUDE always.
- Only ~2,863 of 12,187 series have any open market.
- `status=settled` is the *filter*; returned rows read `status="finalized"`. Combined filters (`finalized,closed`) 400 silently.
- List `yes_bid_dollars`/`yes_ask_dollars` validated accurate (56/60) — screen on those, confirm via `/markets/{t}/orderbook` (`orderbook_fp.{yes_dollars,no_dollars}`).
- **Two-sided quotes only.** One-sided = INSUFFICIENT DATA, never an edge.

---

## Item 1 — Settle the tick-size question (~10 minutes, do first)

Hunt E reported non-1¢ tick regimes: `linear_cent` 77.3%, `tapered_deci_cent` 19.9%, `deci_cent` 2.8%, with **890 two-sided markets quoting spreads below 1¢**, and CONTROL/LLM1/PRESPERSON contract terms stating *"Minimum Tick $0.001"*.

**I could not reproduce it** — `/markets?limit=200&status=open` returned `tick_size: None` for all 200 sampled, and the field is absent from `/series` payloads entirely.

Resolve it definitively: find where the tick regime is actually exposed (try `/markets/{ticker}` singular, the orderbook payload, and the contract-terms PDFs for CONTROL/LLM1/PRESPERSON), and verify empirically by checking whether any real two-sided book quotes a sub-1¢ spread.

**Why it matters:** every friction calculation this fund makes assumes a 1¢ tick. A deci-cent tick in a deep book changes the arithmetic for every candidate we have ever screened out on a 1–2¢ margin — several kills sat that close. Report which if any past verdicts would move.

**STOP at a written answer.** Do not act on it.

---

## Item 2 — Re-scan the skewed-partition sub-case (bounded, ~2 hours)

**A correction first.** Hunt E declared the mutex/partition trade "structurally impossible" via `fee = 0.07(1 − ΣPᵢ²) ≥ 0.07(1 − 1/n)`. **That inequality is backwards** — verified numerically. ΣPᵢ² is *minimised* at uniform, so `0.07(1 − 1/n)` is a **CEILING**, not a floor:

| Partition | Total fee |
|---|---|
| n=2 uniform [.5,.5] | 3.50¢ |
| n=10 uniform | 6.30¢ |
| **n=2 skewed [.97,.03]** | **0.41¢** |
| **n=10 skewed [.97,.003×9]** | **0.41¢** |

The empirical result stands (2,069 fully two-sided mutex families, 33 positive gross, **0 positive net**, best −0.065¢) — but "impossible" is not established, and the **skewed** case is exactly what the flawed proof would have dismissed while being ~10× cheaper in fees.

Your job:
1. Rebuild the mutex-family scan over the full non-MVE open universe. **Do not trust a small sample** — a first pass here yielded only 700 usable markets after dust removal and settled nothing.
2. Stratify by skew (`max leg` bucketed: <0.5, 0.5–0.85, ≥0.85, ≥0.95) and report gross, fee and **net** per bucket. The question is narrow: *does positive net ever appear where the fee floor is ~0.4¢ rather than 3.5–6.3¢?*
3. **Executability is the gate, not the price relationship.** A prior scan found 0 of 1,514 ladder pairs executable. Every candidate must show real top-of-book size on **every** leg — inside one family, one leg had 152,862 contracts and another had **1**, and both passed "spread ≤2¢". **Screen on top-of-book size, not spread.**
4. Verify the family genuinely partitions before treating Σ > $1 as an overround — read the contract terms. `GPUMON.pdf` ladders are *nested* (`strike_type='greater'`), not buckets, and `USELECTION.pdf` has a special-election clause resolving **No for all**.

**Gate: KILL** unless a skew bucket shows positive net with verified size on every leg. Expect KILL — the point is to close the sub-case honestly rather than on a flipped inequality.

---

## Item 3 — The NCAAF listing-window snapshot (pre-registered; scheduled, not immediate)

The only scheduled test the hunt produced. Week 1 is **2026-08-29**; `KXNCAAFGAME` currently lists 30 markets (15 marquee games, opened 2026-05-20). The bulk drop should land **~2026-08-10 to 08-25**.

**Pre-registered protocol — do not renegotiate after seeing data:**
1. Poll `/markets?series_ticker=KXNCAAFGAME&status=open` **daily from 2026-08-01**. Trigger = count jumping from 30 to several hundred.
2. **Within 6 hours of the bulk `open_time`**, snapshot bid/ask for every new market. Anchor contemporaneity is the whole point — a 68h-stale anchor manufactured a +9.5¢ artifact with a CI excluding zero once already.
3. Restrict to two-sided books, ask ∈ [0.85, 0.975], on **non-marquee** games (Group of 5 / FCS tune-ups) — the genuine inattention analogue.
4. Confirm each finalist on `/markets/{t}/orderbook`: ≥$100 within 3¢ **both sides**.
5. Re-snapshot at **T+24h and T+72h**. The drift *is* the alpha.
6. **KILL threshold, registered now: if drift < 3.5¢ the hypothesis is dead regardless of statistical significance.** Required edge = 1¢ tick + spread + 0.4¢ fee + 1¢ margin.

**Stated in advance: this is expected to fail.** The already-listed marquee games are priced tightly, the target band across live NCAAF shows a **27¢ median spread**, and the backwater families quote 87–93¢. On NCAAF, attention and liquidity are perfectly correlated — so the inattention pocket and the tradeable pocket are disjoint sets. It runs only because it is nearly free and the snapshot cannot be reconstructed afterwards.

Set this up as a scheduled check now; do not sit and wait on it.

---

## Definition of done
`research/REPORT_R5_Followups_2026-07.md` with: the tick-size answer and which past verdicts it would move; the skew-stratified mutex table with an explicit KILL or the one surviving construct; and the NCAAF poller committed and scheduled with its threshold written down. **No pod, no config, no deploy, no orders.**
