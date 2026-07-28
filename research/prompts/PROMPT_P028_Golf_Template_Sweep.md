# Claude Code Task — P-028: Sweep the UNTESTED Golf Templates (the six-clause filter points here)

> R5 hunted five new *categories* and found nothing. It never looked deeper into the one domain that works. There are **~10 untested golf templates**, and two of them carry the exact mechanics we have already validated — verified verbatim from the contract terms below.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper/demo; **no real orders, ever**). Public API needs no auth: `https://api.elections.kalshi.com/trade-api/v2`.

**Why golf and why now.** The R5 six-clause survivor profile says an edge must be: LARGE mechanic (≥5¢) · single-leg · hold-to-settlement · **priced in the tails** (the fee `0.07·P·(1−P)` peaks at 1.75¢/ct at P=0.50 and collapses to 0.33¢ at P=0.95) · recurring events · real top-of-book size. **Golf satisfies all six, and is 2-for-3 on tested templates** (P-017 top-N ✓, P-022 round-leader ✓, P-023 make-cut ✗). It also has the highest event cadence available to us — 13 series across PGA/LIV/LPGA/DPW/Champions, ~4–5 tournaments/week.

**Tested and closed:** `GOLFFINISH`/`PGATOPX` (P-017), `PGAROUNDLEADER` (P-022), `GOLFCUT` (P-023 KILL), round/full top-N over-priced fade (P-023c KILL).

## The two live leads — rules already verified VERBATIM

### Lead 1 — `GOLFCATEGORYLEADER.pdf` — the P-022 mechanic in an untested wrapper
Series: **`KXGOLFCAT`, `KXPGACAT`, `KXPGAPLAYERCAT`, `KXPGAWINNERWITHOUT`** (all `fee_type: quadratic` = maker-free).

> "Tie among multiple `<golfer category>` members: If two or more members of `<golfer category>` complete `<time period>` with an identical `<score>` that is the best `<score>` within `<golfer category>`, the markets for all tied `<entity>`s will resolve so that "Yes" holders receive **$1/N** … All non-tied `<entity>`s within `<golfer category>` resolve to No."
>
> "If all members of `<golfer category>` miss the cut, the `<entity>` with the best `<score>` at the time of the cut wins the category, with ties resolved on a $1/N basis as described above."

This is **structurally identical to P-022** (dead-heat $1/N split, YES over-valued), but on a *small* field — the docs give examples like "European players" and "a discrete enumerated set of five named golfers." **The tie rate over a small category should differ materially from the full-field round-leader rate (30%), and that rate is the entire edge.** Measure it; do not assume it is higher or lower.

Note the second clause is a genuine second mechanic: an all-miss-the-cut category resolves on *cut-time* scores, which is a different and probably-unpriced state.

### Lead 2 — `GOLFH2H.pdf` — a tie-pays-ZERO regime
Series: **`KXPGAH2H`, `KXLIVH2H`, `KXDPWTH2H`** (maker-free).

> "Tie (identical total stroke count): If both `<golfer 1>` and `<golfer 2>` complete the same number of rounds and finish with identical total stroke counts, "Yes" holders shall receive **$0**."
>
> "Playoff holes or strokes are not included in the total stroke count for tournament-scope matchups."

Both sides lose on a tie, and playoff resolution is explicitly excluded, so a regulation tie *stays* a tie. Therefore **fair(A) + fair(B) = 1 − P(tie)**, strictly below $1. If the book prices the pair near $1.00 — the naive retail assumption — the tie mass is free.

**Two cautions that decide this one:**
- Identical 72-hole totals between two named golfers are **common**, but you must measure the rate from settled data, not assume it.
- **Fee placement is decisive.** Evenly-matched H2H sits at P≈0.50, the exact maximum of the fee parabola (1.75¢/ct). Selling one side single-leg nets `P(tie) − 1.75¢`; selling both legs doubles the fee. **Report the edge as a function of the pair's price skew** — the mechanic may only clear in lopsided matchups, which is precisely the tail-priced region clause #3 asks for.

## Also untested, lower prior — census but don't over-invest
`GOLFLEADMARGIN` (`KXPGAWINMARGIN`, `KXPGASTROKEMARGIN`) — a margin of 0 *is* a tie; check how it resolves. `GOLFROUNDBIRDIES`, `GOLFROUNDSCORE`, `GOLFLOWROUNDSCORE` (a statistic-threshold family; my grep found no $1/N clause, playoffs excluded from "full tournament"), `GOLFCOUNTRY`, `GOLFMILESTONES`, `PGARETURN`, `KXPGANEXTWIN`. Read each PDF, record the tie/void regime in a single table, and screen out anything without a ≥5¢ mechanic before spending time on it.

## Method — the harness already exists and is validated
`golf_quirks_research/pull_trades.py` + `backtest_fade_fills.py` were rebuilt from scratch on 2026-07-26 and reproduce P-022's Phase-2 cells exactly (`--validate`). Reuse them; do not write a third harness.

**Phase 1 (settled data) — do this alone, then STOP:**
1. Verify each rule VERBATIM against the PDF. Never infer a rule from a ticker name.
2. Pull settled markets per family. **Kalshi history reaches back to at least 2026-05-20**, not ~1 month — take the full window. `status=settled` is the *filter*; rows return reading `"finalized"`. `settlement_value_dollars` IS the realised payout; **`result="scalar"` = the dead-heat split, NOT a void.**
3. Measure the actual tie/dead-heat rate per family, **tournament-clustered**, with CIs.
4. Compare against pre-event anchors — **check anchor contemporaneity** (a "48h anchor" that was really a median 68h-old price manufactured a +9.5¢ artifact with a CI excluding zero once already; stale is *wrong*, not conservative). Two-sided quotes only; bare asks fabricate edges.
5. Apply the friction screen with fees computed **at the actual traded price**, not an average: `0.07·P·(1−P)`. Report edge net of that, by price band.
6. **Screen on top-of-book size, not spread** — inside one family one leg had 152,862 contracts and another had 1, both at ≤2¢ spreads. State capacity in dollars against a $1,000 paper bankroll.

**Phase 2 (fill realism) only if Phase 1 clears ≥5¢ gross:** reuse the P-022 tick replay, strictly-through fills, adverse-selection diagnostic (E[settlement | filled] vs E[settlement | posted]), tournament-clustered bootstrap, leave-one-out.

## Gate
- **KILL** if the tie rate is already priced at the anchor, if the mechanic is under the fee-inclusive bar, or if capacity is trivial.
- **ADVANCE to Phase 2** only on ≥5¢ gross with a tournament-clustered CI excluding zero.
- **ADVANCE to spec** only if Phase 2 clears ≥+2¢/ct net.

## Honest prior
P-023 and P-023c were also golf and both died. Two of three golf templates working does not make the fourth work. The reason to run this ahead of any new category is that the mechanics are **already verified in the documents** and the harness already exists — not that the result is likely positive.

## Definition of done
`golf_quirks_research/REPORT_P028_Template_Sweep_2026-07.md` with: the full golf-template table (family · tie/void regime quoted verbatim · live series · settled n); measured tie rates with tournament-clustered CIs; friction arithmetic at the real traded price; capacity in dollars; an explicit KILL / ADVANCE per family. **No pod, no config, no service, no deploy, no orders.** Commit artifacts as you go.
