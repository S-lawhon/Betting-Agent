# Golf Settlement-Quirk Falsifications — P-022 & P-023

**Betting Pod Shop · Phase-1 research (settled data only)**
**Prepared 2026-07-23**

Shared settled-data harness testing two adjacent extensions of the one
validated structural mechanism in this fund — P-017's golf tie-inflation.
Both ask the same question the house law now demands: *the rule makes the
price wrong (verified from certified contract terms), but has the closing
price already fixed it?*

- **P-022 — Round-Leader Dead-Heat Fade** (sell YES / make NO on R1/R2/R3
  leader markets, whose ties SPLIT the payout $1/n).
- **P-023 — Golf Quirk Basket** (buy boundary YES on LIV/DPW/LPGA top-N and
  make-cut, where ties/MDF/post-cut-WD inflate the YES count above nominal).

> **VERDICTS (Phase 1):**
> - **P-022 (round-leader dead-heat fade) → ADVANCE** to a Phase-2 maker-fill
>   study with collateral caps. The dead-heat mechanic is real (37% payout
>   haircut for names that lead) and the pre-round **5–10¢ band leaves a
>   +4 to +6¢/ct net fade edge, tournament-clustered CI excluding zero at all
>   three anchors**. The binding open question is fillability (a thin-book
>   maker fade, the same constraint that left P-017's fade leg underpowered),
>   not whether the edge exists.
> - **P-023 (golf quirk basket) → MIXED: KILL the broad top-N basket,
>   ADVANCE make-cut (PGA), MARGINAL LIV top-N & DPW make-cut.** The
>   inflation is real everywhere, but on the liquid surfaces the closing
>   price already embeds it: buying boundary YES on PGA top-N (the P-017
>   control) and on every round-based top-N is **efficient-to-negative**.
>   The only unpriced residual is make-the-cut — **PGA make-cut +4.8¢/ct
>   (10 tournaments, CI [+2.3, +7.7])** clears the bar; LIV top-N (+18¢) and
>   DPW make-cut (+45¢) point the same way but rest on 2–4 tournaments.
>
> ADVANCE = closing prices leave >2¢/ct net edge with an event-clustered CI
> excluding zero. MARGINAL = widen history and re-run. KILL = the price
> already embeds the mechanic. **Nothing was built beyond this research
> folder; nothing was placed live.**

---

## 1. Verified rules (quoted from certified contract terms)

All target series were enumerated live from `GET /series?category=Sports`
(2026-07-23); every one is `fee_type = quadratic` → **zero maker fee**
(`src/kalshi_fees.py`, `series_maker_charges_fee` returns False for these
prefixes). Each series' `contract_terms_url` was fetched and read; the
operative clauses are quoted below (PDFs cached under
`golf_quirks_research/contract_terms/`).

### 1a. Round-leader dead-heat — `PGAROUNDLEADER.pdf` (ACHIEVEMENTS template)

Shared by **all** round-leader series (KXPGAR1/R2/R3LEAD, KXLIVR1/R2/R3LEAD,
KXLPGAR1/R2/R3LEAD, KXDPWORLDTOURR1/R2/R3LEAD, KXCHAMPTOURR1LEAD — all
`contract_terms_url = .../PGAROUNDLEADER.pdf`). Operative clause:

> "If multiple participants are reported as the result of \<achievement>,
> then the markets for those participants will resolve so 'Yes' holders
> receive **$1/[the number of participants declared as the result of
> \<achievement>] rounded down to the nearest cent** and 'No' holders receive
> $1 minus the Yes payout. For example, if two participants are the reported
> result of \<achievement>, both 'Yes' and 'No' holders for each of those
> participants shall receive $0.50 per share."

So a tie for the round lead pays YES only $1/n, and **rounds down** — the
rounding always favors the NO holder (the fade side). Withdrawal before the
round resolves **No** for that participant; a whole-event cancellation pays
YES the last traded price (not a void).

### 1b. Top-N finish — `GOLFFINISH.pdf`

Shared by KXPGATOP5/10/20/40, KXLIVTOP5/10, and the round-based
KXPGAR1/R2/R3TOP5/10/20. Two inflation clauses:

> "**Tie Handling:** In the event of a tie for a finishing position, the tied
> position counts as the position itself. For example, if five players tie
> for 8th place, all five players are considered to have finished in 8th
> place... the market resolves to Yes because 8th place is within the top 10."

> "**Boundary Ties:** If \<player> is tied for a position at the boundary of
> \<count>, the market resolves to Yes. For example, if \<count> is 10 and
> \<player> finishes tied for 10th place with three other players, the market
> resolves to Yes."

Ties therefore pay YES **in full ($1)** — an event can settle more than N
markets YES. Note a key asymmetry vs the round-leader family: here
"Withdrawal Before Tee-Off... resolves to the last fair price," so a
`result="scalar"` on a top-N market is a **withdrawal refund, not a
dead-heat split**. The harness treats P-023 scalar rows as voids, never as
YES inflation.

### 1c. Make-the-cut — `GOLFCUT.pdf`

Shared by KXPGAMAKECUT, KXDPWORLDTOURMAKECUT. Three stacked YES-inflators,
all in the certified terms:

> "**Cut Line Ties:** If \<player>'s score is exactly on the official cut
> line and \<player> is among those who qualify to continue under the
> policies of the governing body... (e.g., 'top 65 **and ties**'), \<player>
> shall be considered to have made the cut."

> "**'Made Cut, Did Not Finish' (MDF):** ...a player who made the initial cut
> but was eliminated by a secondary cut shall be considered to have 'made the
> cut'..."

> "**Withdrawals After Making the Cut:** If \<player> makes the cut and
> subsequently withdraws... the market will still resolve to Yes..."
> (and identically for **Disqualification After Making Cut** → Yes.)

So the number of markets settling YES on a make-cut event exceeds the nominal
cut (typically "top 65 and ties"), because cut-line ties, MDF players, and
post-cut WD/DQ all pay YES.

---

## 2. Methodology

**Data model — the realized payout is recorded per market.** On a settled
market the field `settlement_value_dollars` IS the actual per-contract YES
payout: `1.0000` (yes), `0.0000` (no), and for a round-leader dead-heat the
API reports `result="scalar"` with `settlement_value_dollars = $1/n rounded
down` (0.50 = 2-way, 0.20 = 5-way, 0.16 = 6-way). No inference from counts is
needed — the split is read directly. (Verified live 2026-07-23; the per-ticker
`GET /markets/{t}` nulls this, so we read it from the settled-list record.)

**Clustering.** Every statistic is bootstrap-resampled clustering by EVENT
(tournament) — within-event outcomes are strongly correlated (one leaderboard
determines them all). 5 000 resamples, event-level 95% CI. This is the house
discipline (P-017, P-019, P-021).

**Fees.** All target series are `quadratic` → maker fee 0. P-022 fades as a
maker (rest a YES ask / NO bid); P-023 buys boundary YES as a maker (rest a
YES bid). Edges below are net of the (zero) maker fee; the taker alternative
would additionally pay `0.07·P·(1−P)`.

**The closing-price contamination problem (critical).** Round-leader and
round-based top-N markets **trade through the determining round**: pre-round
a cheap name sits at 1–5¢ as an illiquid lottery ticket (bid 0, ask ~2–9¢),
then in the final ~3–5 hours the price discovers toward the settlement value
(a name that ties drifts to ~0.50; one that busts drifts to ~0.00). The
literal "last candlestick close before expiration" therefore **mechanically
equals the settlement value** — using it would make every market look
perfectly efficient and produce a spurious KILL. (Measured: literal-close
mean price ≈ realized value to within a fraction of a cent — reported in §3.)

The only tradeable, information-clean price is the **pre-event anchor**: the
latest quote at least H hours before close, before the round's price
discovery. For round markets we report H = 12h (pure pre-round), 6h
(early-round) and 3h (mid-round, increasingly info-contaminated), because the
"4–10¢ mid-tier names" the P-022 thesis targets barely exist pre-round —
leaders price as 1–2¢ longshots until the round reveals who is contending.
For full-tournament top-N and make-cut we anchor at H = 48h (before R1). The
reference price at an anchor is the last trade if present, else the two-sided
mid, else the ask.

**Sources.** Kalshi public API `https://api.elections.kalshi.com/trade-api/v2`
(settled-market list + candlesticks, pulled 2026-07-23, cached to
`golf_quirks_research/data/`). Contract PDFs under
`https://assets.kalshi.com/contract_terms/`. Harness:
`pull_settled.py` (cache) + `backtest_quirks.py` (analysis).

---

## 3. P-022 — Round-Leader Dead-Heat Fade

**Data:** 13 round-leader series (PGA/LIV/LPGA/DPW/Champions R1/R2/R3),
24 195 settled markets total; 3 079 round-leader markets carry candlesticks.
Split classification is per **Kalshi event** (one round of one tournament);
the price gate clusters CIs per **tournament**.

### 3.1 The split is frequent, and it materially haircuts a leader's payout

Of 69 classifiable round-leader events (round × tournament), **21 settled as
a dead-heat scalar split and 48 as an outright leader → 30% split**. Split
frequency by tour:

| Tour | split | outright | split share |
|------|------:|---------:|------------:|
| PGA | 10 | 23 | 0.30 |
| DP World | 4 | 10 | 0.29 |
| LPGA | 3 | 12 | 0.20 |
| LIV | 3 | 3 | 0.50 |
| Champions | 1 | 0 | 1.00 |

Distribution of the tie size *n* (number sharing the lead), across all
events: n=1 (outright) ×48, n=2 ×12, n=3 ×4, n=4 ×2, n=5 ×1, n=6 ×2.

**Conditional value.** Across the 109 markets that actually led (result yes
or scalar), the realized per-contract YES payout averaged
**E[payout | led] = $0.632 — a 36.8% haircut versus the $1 a naive "will X
lead" YES implies.** This is the mechanism, measured, and it matches the
research memo's ~64¢ figure exactly. It is the mirror image of P-017: golf
ties *inflate* top-N YES payouts (pay $1 in full) and *deflate* leader YES
payouts (pay $1/n).

### 3.2 The literal "closing price" is contaminated — pre-round anchor required

Round-leader markets trade **through** the determining round: a cheap name
sits at 1–5¢ pre-round, then in the final ~3–5 hours the price discovers to
its settlement value. Measured over 3 068 markets, the **literal last-candle
price (0.047) ≈ the realized settlement value (0.022)** — the "close" has
mechanically absorbed the outcome. Using it would declare every market
efficiently priced (a spurious KILL). All gate numbers below therefore use a
**pre-round anchor** (latest real trade / tight two-sided mid ≥ H hours before
close). We also rejected bare one-sided asks: illiquid lottery books park
phantom sells (e.g. a lone 0.98 ask on a name trading at 0.01), and an early
version that trusted them fabricated a false +50¢ edge — the gate uses only
executed trades and narrow two-sided quotes.

### 3.3 The gate — sell YES (maker, fee 0), edge = anchor price − realized value

Per price bucket, at three anchors (12h = pre-round, 6h = early-round,
3h = mid-round). `outr` = # that led outright (the tail); `worst` = worst
single-contract P&L.

**Anchor 12h (pre-round):**

| bucket | n | tourn | mean price | mean value | fade edge/ct | 95% CI | outr | worst |
|--------|--:|------:|-----------:|-----------:|-------------:|--------|-----:|------:|
| 0–5¢ | 2264 | 21 | 0.013 | 0.007 | **+0.006** | [+0.003, +0.008] | 7 | −0.99 |
| **5–10¢** | 138 | 19 | 0.066 | 0.026 | **+0.040** | **[+0.018, +0.059]** | 3 | −0.94 |
| 10–20¢ | 66 | 17 | 0.133 | 0.108 | +0.025 | [−0.046, +0.081] | 6 | −0.89 |
| 20–100¢ | 75 | 21 | 0.516 | 0.258 | +0.258 | [+0.134, +0.353] | 14 | −0.77 |

**Robustness across anchors** (5–10¢ band): 6h → +0.049 [+0.027, +0.067];
3h → +0.064 [+0.050, +0.070]. The edge is stable and the CI excludes zero at
every anchor. Per-event, **16 of 19 tournaments** show a positive fade in the
5–10¢ band.

**Reading it:**
- **0–5¢:** +0.6¢ edge — real (CI excludes zero) but far below the 2¢ bar.
  Pre-round, ~90% of leader names live here as 1–2¢ longshots; there is
  almost nothing to fade profitably.
- **5–10¢ — the thesis's target band — is the finding:** names retail buys at
  ~6.6¢ realize only ~2.6¢ → a **+4¢/ct** fade, rising to +6¢ intra-round as
  the dead names decay. This clears the ADVANCE bar (>2¢, CI excludes zero,
  19 tournaments).
- **20–100¢:** a *second*, larger signal (+26¢) — mid-round "leaders" bid to
  ~52¢ that realize ~26¢ — but it is a high-variance fade of genuinely
  contended markets (14–36 outright leads), not the cheap-lottery thesis, and
  is reported as a separate Phase-2 candidate, not the headline.

**Capacity (indicative).** The 5–10¢ faded markets carry large lifetime
volume (~822k contracts/tournament), but that includes in-round flow after the
price left the band; the *pre-round* tradeable slice at 5–10¢ is a fraction of
it. Sizing is a Phase-2 fill question.

**Tail.** Selling a 6–7¢ YES that leads outright loses ~93¢ (`worst` −0.94).
The mean is positive but the per-bet distribution is P-019-shaped. Any Phase-2
spec **must** carry per-event and per-tournament collateral caps (P-019 spec
§4).

---

## 4. P-023 — Golf Quirk Basket

### 4.1 Inflation is real and quantified (the count test) — but this is only half the law

YES settlements vs nominal N, per series (full metadata; robust). Round-based
top-N inflate most; make-cut runs ~+11% over "top 65 and ties":

| series | nominal N | events | mean YES | YES/N |
|--------|----------:|-------:|---------:|------:|
| KXLIVTOP5 | 5 | 2 | 6.50 | **1.30** |
| KXLIVTOP10 | 10 | 2 | 10.50 | 1.05 |
| KXPGATOP5 | 5 | 12 | 5.67 | 1.13 |
| KXPGATOP10 | 10 | 12 | 11.50 | 1.15 |
| KXPGATOP20 | 20 | 12 | 23.08 | 1.15 |
| KXPGATOP40 | 40 | 4 | 42.75 | 1.07 |
| KXPGAR1TOP5 | 5 | 11 | 7.18 | **1.44** |
| KXPGAR1TOP10 | 10 | 11 | 14.64 | **1.46** |
| KXPGAR2TOP10 | 10 | 11 | 12.27 | 1.23 |
| KXPGAR3TOP10 | 10 | 11 | 11.09 | 1.11 |
| KXPGAMAKECUT | ~65 | 10 | 72.20 | 1.11 |
| KXDPWORLDTOURMAKECUT | ~65 | 5 | 71.80 | 1.10 |

LIV top-5 = 6.5 YES vs 5 (**+30%**, exact match to the memo); PGA top-N
+13–15% (matching P-017's +13%); make-cut ~72 YES (memo's 67–82 range). The
mechanic is confirmed. **But the count only proves the price *could* be wrong
— the gate is whether the closing price already embeds it.**

### 4.2 The gate — buy boundary YES (maker, fee 0), edge = realized − anchor price

> ## ⚠ CORRECTION 2026-07-28 — two anchor errors in this section
>
> Found by exact replication of the cells below. **Both were uncorrected until
> now, and P-023c's conclusions were drawn against them.**
>
> 1. **The round-based top-N cells are anchored at H = 12h, not 48h.** The four
>    `round-topn R1TOP5 / R1TOP10 / R2TOP10 / R3TOP10` rows in the table below
>    were computed at a 12-hour anchor. The "48h" in this paragraph describes
>    the full-tournament and make-cut rows only.
> 2. **The full-tournament "48h, before R1" anchor is NOT pre-event for most of
>    the sample.** Replication puts it **after R1 for 58–71% of the control
>    cohort.** It is a mid-tournament price for the majority of rows, not a
>    pre-tournament one — the description in this paragraph is wrong about what
>    the number measures.
>
> **Any figure derived from these cells inherits the error**, including the
> −3.2¢ control reading quoted immediately below and every comparison drawn
> against it. The direction and size of the bias are *not* established here;
> the make-cut precedent is that a stale anchor is simply **wrong**, not
> conservatively wrong (a median-68h-old anchor manufactured +9.5¢/ct with a
> CI excluding zero), and P-023c later found the bias runs the opposite way on
> its own cohort. Do not attempt to correct these numbers by subtracting an
> offset — they need re-running at a stated, verified anchor.

Pre-tournament anchor (48h, before R1 — the make-cut anchors land at 57–64h,
genuinely pre-cut). Bands: top-N 8–45¢; make-cut 40–60¢. CIs clustered by
tournament.

| group | n | tourn | mean price | realized YES | buy edge/ct | 95% CI | verdict |
|-------|--:|------:|-----------:|-------------:|------------:|--------|---------|
| **CONTROL** PGA top-10/20 | 1627 | 12 | 0.199 | 0.167 | **−0.032** | [−0.051, −0.011] | priced |
| PGA top-5 | 351 | 12 | 0.159 | 0.122 | −0.037 | [−0.053, −0.022] | priced |
| PGA top-40 | 190 | 3 | 0.290 | 0.305 | +0.015 | [−0.028, +0.078] | ~zero |
| round-topn R1TOP5 | 224 | 10 | 0.133 | 0.085 | −0.048 | [−0.077, −0.018] | over-priced |
| round-topn R1TOP10 | 375 | 10 | 0.205 | 0.139 | −0.066 | [−0.107, −0.029] | over-priced |
| round-topn R2TOP10 | 125 | 9 | 0.255 | 0.232 | −0.023 | [−0.085, +0.047] | ~zero |
| round-topn R3TOP10 | 82 | 9 | 0.272 | 0.159 | −0.114 | [−0.179, −0.042] | over-priced |
| LIV top-N | 57 | **2** | 0.203 | 0.386 | **+0.183** | [+0.085, +0.328] | thin+ |
| make-cut DPW | 45 | **4** | 0.507 | 0.956 | **+0.449** | [+0.295, +0.557] | thin+ |
| **make-cut PGA** | 568 | **10** | 0.512 | 0.560 | **+0.048** | **[+0.023, +0.077]** | **unpriced** |

**Reading it (the control comparison the task asked for):**
- **The control settles the top-N basket.** Buying boundary YES on PGA
  top-10/20 — where P-017 already trades — is **−3.2¢ at this 48h/last-trade
  anchor**. This is *consistent* with P-017: P-017's validated +6.8¢ lives in
  the earlier, ask-based Wednesday window (4–10 days out); by 48h out on
  transacted prices the top-N inflation is already priced. Every round-based
  top-N is priced-to-**over-priced** (−5¢ to −16¢) — retail over-buys the
  boundary on the thin round markets. **The broad "buy boundary YES basket"
  thesis is refuted on top-N.**
- **Make-cut is the exception.** Bubble names at ~51¢ make the cut ~56% of the
  time on PGA — a **+4.8¢/ct** residual, 10 tournaments, 8 of 10 positive, CI
  excluding zero. The cut-line-ties / MDF / post-cut-WD inflation is only
  partially priced here. DP World make-cut points much stronger (+45¢) but 4
  tournaments (one, BMIO26, supplies 36 of 45 markets); LIV top-N (+18¢) rests
  on 2 tournaments. Both are directionally strong and thin.

---

## 5. Verdicts & next steps

**P-022 — Round-Leader Dead-Heat Fade → ADVANCE** (to a Phase-2 maker-fill
study, not to a pod). Both halves of the house law are present: the rulebook
*guarantees* the leader YES is overvalued (37% haircut, verified in the PDF
and in settled payouts), and the thin weekly leader markets with retail
lottery demand are why it persists. The 5–10¢ pre-round band clears the gate
(+4 to +6¢/ct net, tournament-clustered CI excluding zero at 12h/6h/3h). Two
conditions on Phase 2, both mandatory:
1. **Fill realism.** This is a maker fade in thin books — exactly what left
   P-017's fade leg "promising but underpowered." Phase 2 must replay tick
   prints (as `backtest_golf.py` leg B did) to confirm the edge survives
   pessimistic through-fills before any pod is written.
2. **Collateral caps.** Tail is −0.94 per contract on an outright leader.
   Carry per-event and per-tournament collateral caps per the P-019 spec §4;
   size on collateral-at-risk, not contract count.

**P-023 — Golf Quirk Basket → MIXED (no single verdict is honest):**
- **KILL** the top-N basket — PGA top-N (the P-017 control) and all
  round-based top-N are priced-to-over-priced at the boundary. The inflation
  is real but already in the closing price.
- **ADVANCE** make-the-cut on the PGA book — +4.8¢/ct, 10 tournaments, CI
  excludes zero, pre-tournament anchor, symmetric (non-tail) risk. This is the
  one clean survivor; a focused make-cut Phase 2 (extend history, add DPW/LPGA
  make-cut, replay maker fills) is warranted.
- **MARGINAL** LIV top-N (+18¢/2 tourn) and DPW make-cut (+45¢/4 tourn) —
  same direction, far too few tournaments. Widen history and re-run before
  acting; do not size on 2–4 events.

**Cross-cutting caution.** Both surviving edges (P-022 5–10¢ fade, P-023 PGA
make-cut) are **modest and maker-execution-bound**. The settled-data gate
proves the price is wrong; it does not prove you can transact against it at
size. That is the Phase-2 question for both, and it is where thin-book golf
edges have died before.

Params: `golf_quirks_research/p022_p023_params.json`.
Harness: `pull_settled.py` (cache) + `backtest_quirks.py` (analysis);
raw pulls under `data/`; contract PDFs under `contract_terms/`.
