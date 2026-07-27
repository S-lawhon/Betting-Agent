# P-026 — Stat-Leader Dead-Heat Fade (KXLEADER $1/n split)

**Phase 1, the $0 pre-trade test · 2026-07-26 · paper/demo only, no orders placed**
**Prompt:** `research/prompts/PROMPT_P026_Leader_Monitor.md` (Task 5)

## VERDICT: **KILL**

> **CORRECTION 2026-07-28 — the split is CONDITIONAL and this report states it
> unconditionally.** `LEAGUELEADER.pdf` reads: *"In the event of a tie where multiple participants have exactly the same <statistic> total, **and <league> does not declare a single winner through official tiebreaker procedures**, the markets for all tied <participant>s will resolve so "Yes" holders receive $1/[the number of tied <participant>s] rounded down to the nearest cent and "No" holders receive $1
> minus the Yes payout."* **The emphasised clause is missing from every
> statement of the rule in this document.** The split fires only when the league
> does not resolve the tie itself, and most leagues publish official
> tiebreakers — so `E[1/n | tie] = 0.43` used below is an **upper bound on the
> haircut**, not its expectation, and the free-money arithmetic in §"prices
> *none* of the split" is correspondingly overstated. The KILL verdict is
> unaffected (it was decided at the $0 pre-trade gate), but the effect size is.
>
> Also corrected: the split has **never been observed in the wild**.
> `KXWCGOALLEADER` (54 markets) and `KXLEADERUCLGOALS` (6) have already settled
> and **both resolved outright at $1.0000**. The claim elsewhere that "~Oct 15
> is the first-ever KXLEADER settlement" is wrong.

The $1/n dead-heat rule is real and was re-verified verbatim from the certified terms. The
*fade* it is supposed to fund is not there. Two independent reasons, either sufficient:

1. **No mispricing is demonstrable.** The prompt's headline condition — co-leader mids
   summing to ≳100¢ — fires, and **it is an artifact every time**. In each firing event,
   `SUM(mid) − SUM(bid)` (pure accumulated half-spread) accounts for 100% of the excess over
   100¢. On the only model-free statistic available (`SUM(bid) > 100¢` = strict dutch book),
   **no event clears**: the maximum anywhere is **99.0¢**, and 92.7¢ net of taker fees.
2. **Even granting the thesis in full, the edge is smaller than the spread.** Assuming the
   market prices *zero* of the split — the most favourable assumption possible — the
   theoretical haircut exceeds the half-spread on **4 of 63** quoted legs, best case anywhere
   **+0.56¢/contract**. Median MLB-wins leg: 1.83¢ max edge against a 2.82¢ half-spread.

**Headline: highest co-leader bid-sum on any KXLEADER event = 99.0¢ against a hard 100¢
ceiling** (MLB pitcher wins — the most liquid, highest-tie-rate event in the family). One
cent short of an arbitrage, on the wrong side, before fees.

Step 2 (the monitor) was **not built**: Step 1 returned KILL and the prompt's rule is not to
build past a kill.

## 1. The rule — re-verified, plus a conditional nobody recorded

`GET /series?category=Sports` (2026-07-26) returns **44 KXLEADER-family series**; **38 share
`contract_terms_url = .../LEAGUELEADER.pdf`**, all `fee_type = quadratic` (maker fee 0, taker
0.07·P·(1−P)). PDF cached at `leader_research/contract_terms/LEAGUELEADER.pdf`. Operative
clause, quoted:

> "In the event of a tie where multiple participants have exactly the same \<statistic\>
> total, **and \<league\> does not declare a single winner through official tiebreaker
> procedures**, the markets for all tied \<participant\>s will resolve so 'Yes' holders
> receive $1/[the number of tied \<participant\>s] **rounded down to the nearest cent**…"

Two details not in the brief:

- **The split is conditional on the league not breaking the tie.** Every prior write-up
  states it unconditionally. Whether MLB and the NFL declare co-leaders or apply an official
  tiebreaker per counting stat is **unverified** and is a live tail risk. It does not change
  this verdict — nothing survives even assuming the split always applies — but anyone
  reviving P-026 must verify it per stat, per league, before quoting a number.
- **Rounds down**, which favours the NO/fade side; consistent with `PGAROUNDLEADER.pdf`
  (P-022). 3-way → 0.33; 6-way → 0.16.

## 2. What the test actually is, and why the specified version is unsafe

Let `L` = the set tied at the season maximum, `n = |L|`. YES on *i* pays `floor(100/n)/100`
if `i ∈ L`, else 0. So over the **complete eligible field**:

```
Σ fair_i  =  E[ n · floor(100/n)/100 ]  ≤  $1.00
```

A clean partition identity requiring no model — strictly below 100¢ whenever `n ∤ 100`,
because the split rounds down. If instead the market ignores the split and prices each name
as though a share of the title pays a full $1, it prices `q_i = P(i ∈ L)` and
`Σ q_i = E[n]` ≈ 140¢ for MLB pitcher wins (s=0.30), ≈162¢ for NFL INT (s=0.47). 140¢ vs
≤100¢ is large and unmissable, so the test is **well posed and high-powered in principle**.
Execution fails in two ways that push in opposite directions:

**(a) Half-spread accumulation inflates the mid-sum.** `SUM(mid) = SUM(bid) + SUM(half-spread)`.
On a 20-leg book quoting 5¢ wide, that is **+50¢ of pure spread with no informational
content**. A mid-sum over 100¢ is what a wide multi-leg book *always* produces.

**(b) The strike list is not a partition**, which deflates it — see §5.

Both biases are the same order as the effect being measured. **The mid-sum statistic, run as
specified, is not diagnostic on these books.** The bid/mid/ask decomposition below is
therefore the headline output, and the only model-free claim available is:

> `SUM bid > 100¢` ⇒ strict dutch book. Anything at or below 100¢ proves nothing either way.

## 3. Data and quote quality

`leader_research/fetch_leader_books.py` (read-only, cached, ~1.2 req/s, resumes from cache).
Snapshot **2026-07-26 19:15 UTC**. Top-of-book from `/markets/{t}/orderbook`
(`orderbook_fp`, dollars; best YES ask = 1 − best NO bid); executed prints from
`/markets/trades` (30 d). MLB standings same date from `statsapi.mlb.com`. Never-fade stats
(half-sacks, steals, all yardage, all rate stats, NBA/WNBA per-game) excluded up front.

There is a **live 5-way dead heat at 12 pitcher wins** (Ashby, Burns, Gray, Griffin,
Sánchez) — precisely the setup the hypothesis was designed for.

| | legs |
|---|---:|
| listed across the 10 events | **426** |
| **genuine two-sided quote (usable mid)** | **99 (23%)** |
| discarded — no usable mid | **327 (77%)** |
| …of those, how many had *any* YES bid | **0** |

**Every one of the 327 discarded legs is an ask with no bid.** Imputing a mid from a bare
ask is fabrication. For reference, imputing them produces a "field sum" of **1151¢** on NFL
pass-INT — an event with **zero** two-sided legs. That is the failure mode the guardrail
exists to catch, and it fires hard.

## 4. Result — per event, with the discard count

All figures in cents. Correctly-split *complete* field ≤ 100.0¢.

| Event | stat, tie rate *s* | legs | **two-sided** | discarded | **SUM bid** | SUM mid | SUM ask | **half-spread** | excess >100 | naive E[n] |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| KXLEADERMLBWINS-26 | pitcher wins, 0.30 | 57 | **18** | 39 | **99.0** | 135.5 | 172.0 | **36.5** | 35.5 | 140 |
| KXLEADERMLBHR-26 | home runs, 0.07 | 31 | **5** | 26 | **87.0** | 98.0 | 109.0 | 11.0 | 0.0 | 109 |
| KXLEADERMLBRBI-26 | RBI, 0.07 | 71 | **13** | 58 | **65.0** | 89.5 | 114.0 | 24.5 | 0.0 | 109 |
| KXLEADERNFLINT-27 | interceptions, **0.47** | 24 | **2** | 22 | **4.0** | 7.0 | 10.0 | 3.0 | 0.0 | 162 |
| KXLEADERNFLPINT-27 | pass INT, **0.47** | 30 | **0** | 30 | **0.0** | 0.0 | 0.0 | 0.0 | 0.0 | 162 |
| KXLEADERNFLRUSHTDS-27 | rushing TDs, 0.20 | 25 | **15** | 10 | **61.0** | 242.0 | 423.0 | **181.0** | 142.0 | 126 |
| KXLEADERNFLRTDS-27 | receiving TDs, 0.20 | 26 | **21** | 5 | **71.0** | 157.5 | 244.0 | 86.5 | 57.5 | 126 |
| KXLEADERNFLPTDS-27 | passing TDs, 0.20 | 28 | **18** | 10 | **76.0** | 119.0 | 162.0 | 43.0 | 19.0 | 126 |
| KXLEADERMLBRUNS-26 | runs, 0.10 | 62 | **3** | 59 | **70.0** | 89.5 | 109.0 | 19.5 | 0.0 | 113 |
| KXLEADERMLBHITS-26 | hits, 0.05 | 72 | **4** | 68 | **66.0** | 74.0 | 82.0 | 8.0 | 0.0 | 107 |

### Does the ≳100¢ condition hold anywhere?

**On mids: strictly above 100¢ in 4 of 10 events — and the half-spread column equals or
exceeds the entire excess in 4 of those 4.** The cleanest demonstration is **NFL rushing
TDs**: mid-sum **242¢**, apparently a spectacular confirmation at nearly double the 126¢
naive prediction — from a book with a **22¢ median spread** (Saquon Barkley bid 3¢ / ask
41¢) whose bid-sum is **61¢**. Reporting that as evidence would have been pure fabrication,
and it is exactly what the unguarded test produces.

**On bids — the only model-free version — no.** Maximum 99.0¢, 92.7¢ net of the taker fee to
sell. No event on the board contains a dutch book.

### The prime targets have no market

NFL interceptions — 47% tie rate, the best stat in the whole thesis — has **2 of 24 legs
quoted, totalling 4¢ of bid**. NFL pass-INT: **0 of 30**. For those two the answer is not
KILL but **INCONCLUSIVE-because-untradeable**: there is nothing to fade, in July or
plausibly ever, and they cannot rescue the verdict.

## 5. Structural finding: the KXLEADER strike list is not a partition

`GET /events/KXLEADERMLBWINS-26?with_nested_markets=true` returns **exactly 57 markets, all
`active`, all created 2026-04-28**, with **no "Field"/"Other" strike**. The list has not been
maintained since April:

| Currently top-10 in MLB wins | listed? |
|---|---|
| Aaron Ashby (12) | yes |
| **Chase Burns (12)** | **no market** |
| Sonny Gray (12) | yes |
| **Foster Griffin (12)** | **no market** |
| Cristopher Sánchez (12) | yes |
| **Andre Pallante (11)** | **no market** |

**Two of the five current co-leaders cannot be traded at all.** Three consequences:

- It breaks the ≤100¢ benchmark the test leans on — the identity holds over the *complete
  eligible field*, not Kalshi's April subset. A listed-field sum below 100¢ is therefore
  ambiguous between split-aware pricing and simple field leakage. Only a sum *above* 100¢
  would have been unambiguous, and none is.
- It is a *separate* NO-side tailwind unrelated to dead heats: an unlisted player winning
  resolves every listed strike to No. **Not pursued** — different hypothesis, one
  observation, and see §7's capacity math.
- It is a standing data-integrity gotcha for anything built on KXLEADER: **the strike list is
  a stale snapshot, not the field.**

## 6. The decisive test — the edge is smaller than the spread

This stands regardless of §4, and is what makes the verdict a clean kill rather than
"inconclusive, revisit in September". Grant the thesis **completely**: assume the market
prices *none* of the split, so the whole haircut `mid · s · (1 − E[1/n])` is free money
(`E[1/n | tie] = 0.43`). Compare to the half-spread you must pay to get on. Over the 63 legs
quoted at a mid ≥ 5¢:

| Event | legs ≥5¢ | mean max edge | mean half-spread | net > 0 |
|---|---:|---:|---:|---:|
| KXLEADERMLBWINS-26 | 11 | 1.83¢ | 2.82¢ | **0 / 11** |
| KXLEADERMLBHR-26 | 4 | 0.93¢ | 2.00¢ | 1 / 4 |
| KXLEADERMLBRBI-26 | 7 | 0.40¢ | 2.00¢ | **0 / 7** |
| KXLEADERNFLRUSHTDS-27 | 15 | 1.84¢ | 12.07¢ | **0 / 15** |
| KXLEADERNFLRTDS-27 | 11 | 1.38¢ | 6.77¢ | 2 / 11 |
| KXLEADERNFLPTDS-27 | 11 | 0.97¢ | 2.91¢ | 1 / 11 |
| KXLEADERMLBRUNS-26 | 2 | 2.41¢ | 8.25¢ | **0 / 2** |
| KXLEADERMLBHITS-26 | 2 | 0.95¢ | 2.00¢ | **0 / 2** |
| **total** | **63** | | | **4 / 63** |

The four survivors in full: Schwarber (MLB HR) **+0.56¢**, Ja'Marr Chase +0.48¢, Stafford
+0.14¢, CeeDee Lamb +0.13¢.

Three things kill it:

- **The best case on the entire board is +0.56¢/ct**, on a 7%-tie stat where the estimate is
  dominated by assumption error in `s` and `E[1/n]`, and where a ±0.03 wobble in `s` flips
  the sign.
- The high-tie stats, where the haircut is genuinely large in *percentage* terms (MLB wins:
  17.1% of value), trade at **10–17¢ with 5–6¢ spreads**. 17% of 14¢ is 2.4¢ against a 3¢
  half-spread. **The haircut is real and smaller than one tick-and-a-half of friction.**
- The only way to avoid the half-spread is to rest NO bids and be a maker (fee 0 on
  `quadratic`). That is **the P-016 trap, verbatim** — killed twice, −1.29¢/ct markout at 814
  fills after earning +4.08¢ of spread. On a season-long market whose price moves only when a
  co-leader wins or loses a start, resting NO offers get taken precisely by whoever just
  watched that start. No version of this survives measured adverse selection of that
  magnitude on a ≤2.4¢ theoretical haircut.

## 7. Capacity ceiling, recorded so it is not rediscovered under pressure

- **~6–8 independent stat-seasons per year, with total within-stat correlation.** One
  season's INT race is **one** observation however many names trade. Every CI must be
  clustered at the stat-season.
- **Cap 1–2% of bankroll per stat-season** → **$10–20** on the current $1,000 paper bankroll.
  Below the minimum position size worth booking, and trivial at any bankroll the fund will
  plausibly run this year.
- **A forward gate is untestable on the fund's timescale.** At 6–8 independent observations
  per year, a 20-observation gate takes **~3 years** — disqualifying on its own. Even a
  genuine +2¢ edge could not be demonstrated before it decayed or the series changed.
- Visible fade-side depth was never the binding constraint: MLB wins shows ~1,300 contracts
  of resting YES bid (~$1,224 of NO collateral). **The correlation cap binds ~100× tighter
  than the book does.**

## 8. The ~Oct 15 free falsification — status corrected, still worth keeping

**The brief's premise is wrong: October will not be the first-ever KXLEADER settlements.**
Two LEAGUELEADER-template events have already settled (`leader_research/settled_leader_check.py`):

| Series | settled markets | result |
|---|---:|---|
| `KXWCGOALLEADER` (2026 WC golden boot) | 54 | 53 no, **1 yes** — `-26-KMBA`, `settlement_value_dollars = 1.0000` |
| `KXLEADERUCLGOALS` (2025-26 UCL top scorer) | 6 | 5 no, **1 yes** — `-26-KMBA`, `1.0000` |

Both resolved to a single outright winner, so neither produced a `scalar` print. Settlement
plumbing is confirmed working and reads cleanly, but **the $1/n split has still never been
observed in this family**, and October remains the earliest realistic chance.

**Keep it, but demote it.** It is free and it de-risks a *live* workstream — P-022's settler
must treat `result="scalar"` as a dead-heat payout rather than a void (now fixed; see
`golf_quirks_research/REPORT_P022_Settler_Scalar_Fix_2026-07-26.md`), and independent
confirmation of the round-down is worth having. But it is a 15-minute settled-data check, not
a research run. It cannot resurrect P-026: confirming the rule was never the binding
constraint — §6 is, and §6 does not depend on the rule being confirmed. Note also that
P(any MLB counting-stat leader ties in 2026) is maybe 50–60% across the ~6 tie-capable
series, so the check may return nothing even in October.

Re-run `python3 leader_research/settled_leader_check.py` after 2026-10-16 (delete
`leader_research/data/settled_*.json` first to bust the cache).

## 9. What would have to change to reopen this

A falsification bar, so a future session does not relitigate on vibes:

1. An event where **`SUM(bid)` over genuinely two-sided legs exceeds 100¢**. Nothing weaker;
   mid-sums are inadmissible on this family (§2a).
2. **NFL INT with an actual book** — say ≥10 two-sided legs at ≤3¢ spreads during December.
   Check once in-season; if coverage is still 2-of-24, the highest-tie-rate stat in the
   thesis is permanently untradeable and P-026 has no best case left.
3. A **maker-fill study** clearing the P-022 Phase-2 bar (positive net of *measured* adverse
   selection, stat-season-clustered CI excluding zero) — which needs ~3 years of
   stat-seasons to power. Practically: no.

Items 1 and 2 are cheap standing checks. Item 3 is the real gate and it is not reachable.

## Appendix — MLB pitcher wins, the one event with a real book

18 of 57 legs two-sided; 39 discarded (ask-only, all a 1¢ ask with no bid). `SUM(bid) = 99.0¢`,
`SUM(mid) = 135.5¢`, `SUM(ask) = 172.0¢`; the 36.5¢ by which the mid-sum clears the 100¢
ceiling is **exactly** the accumulated half-spread.

| Name | tier | bid | ask | mid | spread | bid qty | ask qty | trades 30d |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Yoshinobu Yamamoto | T3 | 0.14 | 0.20 | 0.170 | 0.06 | 18 | 164 | 35 |
| Aaron Ashby | T3 | 0.12 | 0.19 | 0.155 | 0.07 | 15 | 5 | 42 |
| Cristopher Sánchez | T3 | 0.11 | 0.17 | 0.140 | 0.06 | 217 | 5 | 32 |
| Chris Sale | T2 | 0.11 | 0.16 | 0.135 | 0.05 | 2 | 5 | 12 |
| Jacob Misiorowski | T2 | 0.10 | 0.15 | 0.125 | 0.05 | 5 | 5 | 51 |
| Sonny Gray | T3 | 0.07 | 0.13 | 0.100 | 0.06 | 265 | 115 | 31 |
| Cam Schlittler | T3 | 0.06 | 0.12 | 0.090 | 0.06 | 22 | 5 | 11 |
| Justin Wrobleski | T2 | 0.05 | 0.10 | 0.075 | 0.05 | 116 | 5 | 15 |
| Zack Wheeler | T3 | 0.04 | 0.10 | 0.070 | 0.06 | 20 | 82 | 23 |
| Bryan Woo | T2 | 0.04 | 0.09 | 0.065 | 0.05 | 167 | 5 | 10 |
| Paul Skenes | T2 | 0.03 | 0.08 | 0.055 | 0.05 | 10 | 5 | 8 |
| Gavin Williams | T1 | 0.04 | 0.05 | 0.045 | 0.01 | 175 | 50 | 1 |
| Michael Soroka | T2 | 0.01 | 0.06 | 0.035 | 0.05 | 50 | 5 | 0 |
| Max Fried | T1 | 0.02 | 0.03 | 0.025 | 0.01 | 57 | 50 | 4 |
| Logan Webb | T2 | 0.02 | 0.03 | 0.025 | 0.01 | 8 | 50 | 0 |
| Steven Matz | T1 | 0.01 | 0.02 | 0.015 | 0.01 | 50 | 50 | 0 |
| Jose Soriano | T1 | 0.01 | 0.02 | 0.015 | 0.01 | 50 | 50 | 0 |
| Landen Roupp | T1 | 0.01 | 0.02 | 0.015 | 0.01 | 50 | 48 | 1 |

Tiers: **T1** two-sided, ≤3¢ spread, ≥25 contracts resting both sides · **T2** two-sided,
≤5¢ · **T3** two-sided, any spread · **X** one-sided or empty → discarded, never priced.

Note the shape: three of the five current 12-win co-leaders are quoted at 14–17¢ mids with 6¢
spreads, while the other two have no market at all. Whatever those three prices are, they are
not a clean read on whether the split is priced — and they are not wide enough of fair to pay
for the spread.

---

*Verification note: the orchestrating session independently recomputed the per-event table
from `leader_research/data/split_pricing_analysis.json`, confirming max `SUM(bid)` = 99.0¢,
the 99/426 (23%) two-sided rate, and that half-spread ≥ excess in 4 of 4 events whose mid-sum
strictly exceeds 100¢. The "6 of 10" figure in the agent's draft was corrected to 4 of 10
strictly above 100¢ (MLB HR at 98.0¢ and RBI at 89.5¢ do not clear the line).*
