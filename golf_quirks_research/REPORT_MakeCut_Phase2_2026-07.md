# P-023 — PGA Make-Cut Phase 2 (maker fill replay)

**Date:** 2026-07-26 · **Prompt:** `research/prompts/PROMPT_P023_MakeCut_Phase2.md` (Task 7)
**Paper/demo only. No pod built, no config touched, nothing deployed, no order placed.**

## Harness validation: **PASS** (every published cell)

The P-022/P-023 harness `.py` files were lost 2026-07-25. Rebuilt from the surviving reports,
then validated against P-022's published Phase-2 numbers **before** being pointed at make-cut.

Reproduce with `python3 golf_quirks_research/backtest_fade_fills.py --validate`.

- **Universe reconstruction is exact.** Selecting round-leader markets whose 12h anchor sits
  in [0.03, 0.12], with anchor = the latest candle carrying an executed close *or* a
  two-sided quote ≤ **0.10** wide, returns **exactly the 364 cached tickers — 0 missing, 0
  extra**. `max_spread=0.10` is the only free parameter and it is pinned by that exact match,
  not fitted to a result.
- **Every cell of the published Phase-2 table matches, including all counts:**

| H | off | posted | filled | contracts | net ¢/ct | 95% CI | tourn | E[s\|post] | E[s\|fill] | |
|--:|--:|--:|--:|--:|--:|---|--:|--:|--:|---|
| 12 | 0.00 | 364/**364** | 211/**211** | 5160/**5160** | +2.1/**+2.1** | [+0.8,+3.5]/**[+0.8,+3.5]** | 19/**19** | .018/**.018** | .032/**.032** | PASS |
| 12 | 0.02 | 364/**364** | 171/**171** | 4061/**4061** | +3.4/**+3.4** | [+1.7,+5.1]/**[+1.7,+5.1]** | 19/**19** | .018/**.018** | .041/**.041** | PASS |
| 12 | 0.04 | 364/**364** | 147/**147** | 3506/**3506** | +4.7/**+4.7** | [+2.6,+6.8]/**[+2.7,+6.7]** | 19/**19** | .018/**.018** | .048/**.048** | PASS |
| 6 | 0.00 | 342/**342** | 112/**112** | 2765/**2765** | +0.5/**+0.5** | [−1.8,+3.0]/**[−1.9,+3.0]** | 15/**15** | .020/**.020** | .060/**.060** | PASS |
| 6 | 0.02 | 364/**364** | 97/**97** | 2385/**2385** | +1.7/**+1.7** | [−1.0,+4.5]/**[−1.0,+4.6]** | 15/**15** | .018/**.018** | .070/**.070** | PASS |
| 24 | 0.00 | 272/**272** | 172/**172** | 4234/**4234** | +2.4/**+2.4** | [+0.1,+4.3]/**[+0.0,+4.3]** | 19/**19** | .017/**.017** | .028/**.028** | PASS |
| 24 | 0.02 | 274/**274** | 138/**138** | 3323/**3323** | +3.5/**+3.5** | [+0.5,+5.9]/**[+0.5,+5.9]** | 19/**19** | .017/**.017** | .035/**.035** | PASS |

*(rebuilt / **published**. Three CI endpoints differ by 0.1¢ — bootstrap RNG noise, inside a
0.3¢ tolerance.)*

Also reproduced: **16 of 19 tournaments positive**; **leave-one-out dropping USO26 →
+3.04¢** (published +3.0¢), all 19 jackknives positive.

**The strongest evidence it is the *same* computation** is that the rebuild reproduces two
**incidental fingerprints** nothing in the report explains: posted falls to **342** at
H=6/off 0.00 and to **272** at H=24/off 0.00, while off +0.02 posts 364/274. Both fall out of
cent-rounding the quote — an anchor decayed below half a cent rounds to 0.00 and drops out at
offset 0, then reappears at +0.02. Nobody could fit those by targeting the headline.

Phase-1 gate rows reproduce too: PGA make-cut n=**570** (pub. 568), 10 tournaments, mean px
**0.512**, realized **0.560**, edge **+0.048**, CI **[+0.023,+0.079]** (pub. [+0.023,+0.077]);
DPW n=**45**, +**0.449**, CI **[+0.295,+0.557]** — exact.

## Make-cut Phase 2: **KILL**

**PGA make-cut, maker route.** Rest a YES bid at `anchor − 0.02`, fill only on
`taker_side="no"` prints strictly through, 25 ct/name, 566 markets / 10 tournaments / 13,993
prints:

> **−13.8¢/ct, tournament-clustered CI [−23.9, −4.1], 1 of 10 tournaments positive,
> 0 of 10 leave-one-out jackknives positive** (LOO band [−17.7, −10.5]¢).

**Adverse selection does not shave the edge here — it flips the sign.** If every posted quote
had filled at its price, the strategy earns **+6.7¢/ct** (Phase 1's average, restated on the
traded population). It actually earns −13.8¢. The **20.5¢ gap** is fully explained by
**E[settlement | filled] = 0.361 vs E[settlement | posted] = 0.558**.

A resting bid on a market that trades through its own determining period is a one-way option
written to the informed side: the price only comes back down through your bid when the player
is playing his way *out* of the cut, and it never comes back down at all on the ones who make
it.

| setting | filled | fill% | cts | net ¢/ct | 95% CI | +tourn | E[s\|fill] |
|---|--:|--:|--:|--:|---|--:|--:|
| **H48, −0.02 (headline)** | 253 | 45% | 4915 | **−13.8** | **[−23.9, −4.1]** | **1/10** | **0.361** |
| H48, −0.00 | 292 | 52% | 5888 | −10.7 | [−19.5, −3.6] | 1/10 | 0.412 |
| H48, −0.04 | 235 | 42% | 4403 | −16.8 | [−27.0, −6.2] | 2/10 | 0.313 |
| H24, −0.02 | 236 | 42% | 4571 | −16.5 | [−27.8, −5.2] | 1/10 | 0.337 |
| H48 −0.02, at-or-through (friendlier) | 272 | 48% | 5395 | −11.2 | [−21.2, −2.1] | 1/10 | 0.387 |
| H48 −0.02, cancel at R1 tee-off | 71 | 13% | 1271 | +4.2 | [−6.7, +19.6] | 6/10 | 0.538 |
| H48 −0.02, cancel at end of R1 | 124 | 22% | 2098 | +2.0 | [−9.8, +14.0] | 6/10 | 0.518 |
| H48 −0.00, cancel at R1 tee-off | 138 | 24% | 2572 | +3.2 | [−5.1, +17.3] | 7/10 | 0.550 |

Per-tournament (¢/ct): CHSC26 −0.1, COPC26 −38.7, GESO26 −6.8, ISC26 −43.5, JODC26 −10.9,
RBBCAN26 −8.2, THCCBN26 −18.8, THMTPBW26 −4.2, THOC26 −25.4, USO26 +2.2.

The cancel-early rows are the only positive cells and **none clears the gate** — every CI
straddles zero, and they are post-hoc (1 favourable cell of 15).

**DP World make-cut → NOT EXECUTABLE.** All **99** lifetime prints across the 43-market
universe are `taker_side="yes"`. Not one seller ever hit a bid. **Zero maker fills in all 15
configurations.** Phase 1's +45¢ is real as a settled average but describes a book a maker
cannot transact in.

**LIV top-N → KILL.** −15.2¢/ct, both tournaments negative, both jackknives negative,
**E[settle | filled] = 0.021 vs 0.400 posted** — the most extreme adverse selection measured
in this fund.

## The finding you most need to see: a stale-anchor artifact nearly produced a false ADVANCE

P-022 posts at `T = close − H` using the latest clean candle at or before `T`. On leader
markets those coincide (median staleness **1.0 h**). On make-cut they do not — these books
only print during tour business hours, so the "48h anchor" is on median a **68h-old price,
20.3 h stale, for 100% of markets**. Quoting a 20-hour-old price is not an execution model
anyone could run, so the headline posts the quote *at the moment its anchor was observed*.

| variant | filled | cts | net ¢/ct | 95% CI | tourn |
|---|--:|--:|--:|---|--:|
| stale-`T` post, cancel at tee-off | 55 | 1017 | **+9.5** | **[+2.1, +30.3]** | 9 |
| contemporaneous post, cancel at tee-off | 71 | 1271 | +4.2 | [−6.7, +19.6] | 10 |

The stale version is **the only cell in the entire study whose CI excludes zero on the
positive side**. Had the harness been rebuilt to the literal recipe without checking anchor
staleness, this study would have reported a **+9.5¢ "ADVANCE" on 55 fills**. Both variants are
retained in the grid (`_staleT` rows) rather than assumed away.

**Recommendation: make the anchor-contemporaneity check standard in every future maker
replay.** It is a one-line diagnostic and it changed a verdict.

## Is make-cut dead? The taker leg, stated honestly

This kills the **maker** route; it does not test P-017's existing `makecut_yes` taker leg.
Reference (buy YES at the anchor-candle ask, net of 0.07·P·(1−P)), added to the script as
`taker_reference()`:

| cohort | n | tourn | mean ask | spread | net ¢/ct | 95% CI | +tourn |
|---|--:|--:|--:|--:|--:|---|--:|
| PGA make-cut | 556 | 10 | 0.528 | 0.039 | **+1.5** | **[−1.0, +4.3]** | 7/10 |
| DPW make-cut | 37 | 2 | 0.543 | 0.081 | +41.3 | [+40.4, +51.3] | 2/2 |
| LIV top-N | 49 | 2 | 0.209 | 0.070 | +16.7 | [+7.4, +35.9] | 2/2 |

**PGA make-cut as a taker is +1.5¢/ct with a CI spanning zero — below the +2¢ bar.** Phase 1's
+4.8¢ was quoted against the anchor (mid/last trade); the ~1.7¢ walk to the ask plus the
~1.75¢ fee accounts for the gap almost exactly. The inflation is real and still not fully in
the mid — it is simply smaller than the cost of touching it from either side. Same shape as
P-021, P-024 and P-023a.

## Capacity — moot, but recorded

Headline config over 10 tournaments: **$2,452 collateral-at-risk, 4,915 contracts, −$677
(−27.6% on collateral)**. The one positive config (cancel at tee-off): $630 collateral,
**+$53 = +8.4% over ten tournaments** — ~$5/tournament at a cap 5× any sane live size, on a
statistically insignificant estimate. PGA make-cut carries ~25 prints per market over its
whole life. Worst single name −$14.50. Not a capacity story under any reading.

## Verdict table

| cohort | verdict | headline |
|---|---|---|
| **PGA make-cut (maker)** | **KILL** | −13.8¢/ct, CI [−23.9,−4.1], 1/10 tourn, 0/10 jackknives |
| **PGA make-cut (taker)** | **MARGINAL — no action** | +1.5¢/ct, CI [−1.0,+4.3], 7/10 |
| **DPW make-cut** | **NOT EXECUTABLE** | zero maker fills; 99/99 prints are buys |
| **LIV top-N** | **KILL** | −15.2¢/ct, 0/2, E[s\|fill] 0.021 |

**Do not build a P-023 pod.** P-022 is unaffected and remains GREEN-LIT — its numbers were
independently reproduced here from a from-scratch harness, which is the strongest evidence
the fund has for any of its own results.

## Method notes for the record

Universe: 48h anchor in [0.40,0.60] (make-cut), [0.08,0.45] (LIV top-N). Fills strictly
through, 25 ct/name research cap. **Tournament-clustered contract-weighted bootstrap**, 5,000
resamples, never per-contract. Anchors use executed prints or two-sided quotes ≤0.10 wide only
— never bare asks. **Settlement read directly from `settlement_value_dollars`, NOT through
`src/kalshi_golf_settler.py`** (which at the time of the run still voided `result="scalar"`;
2 of 566 PGA make-cut markets are scalar — see
`REPORT_P022_Settler_Scalar_Fix_2026-07-26.md`, since fixed). Maker fee 0, **verified live**
2026-07-26 (`fee_type=quadratic` for all four series), not assumed. Effective sample: late-May
→ mid-July 2026, 10 PGA tournaments.

## Needs Sam's decision / attention

1. **Kalshi trade history reaches back to at least 2026-05-20, not ~1 month.** Probing one
   market per tournament on 2026-07-26 returned prints for all 15 golf tournaments in the
   cache — over two months. `archive/README.md` has been corrected. **This is the cheapest
   open lead in the folder:** P-022's 19 tournaments and the make-cut taker's 10 can be
   widened *backwards* for minutes of API budget rather than months of calendar.
2. **`src/kalshi_fees.py` has drifted a third time** — `KXDPWORLDTOURMAKECUT`, `KXLIVTOP5`,
   `KXLIVTOP10` are missing from `_SERIES_MAKER_FEE`, so the code charges a maker fee on
   series Kalshi bills at zero. No effect on this report (fees verified live per-series
   instead). Task 3 independently found the `*LEAD` half of the same drift.
3. **Adopt the anchor-contemporaneity check as standard** for maker replays.

---

*Verification note: the orchestrating session independently re-ran
`backtest_fade_fills.py --validate` and confirmed `HARNESS VALIDATION: PASS` — 364/364
universe, all seven cells, 16-of-19 positive, LOO-USO26 +0.0304.*
