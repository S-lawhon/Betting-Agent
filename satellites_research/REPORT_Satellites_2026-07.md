# Satellite Quirk Census: Award Ties, RT Fallback/Drift, WINS Partition Scanner

**Date:** 2026-07-26 · **Prompt:** `research/prompts/PROMPT_SATELLITES_Quirk_Census.md` (Task 10)
**Mode:** paper/demo research only. No orders placed, nothing added to `pods.active`, no pod built,
no deploy, live `data/` untouched.

## Verdicts

| # | Sub-study | Verdict | Headline |
|---|---|---|---|
| **A** | Award tie regime | **KILL** | Best ex-ante trade **+0.93¢/ct against 99¢ collateral**; **24 of 25** live tie strikes have **zero bid depth**. Entire addressable market: **39,038 ct ≈ $390**. |
| **B** | RT non-partition + read-time drift | **KILL** | Fallback fired **0/19** settled events (and isn't identifiable from settlement data). Drift is real at mid (+1.3¢/ct at T−24h) but **+0.10¢/ct executable**, CI [−1.02,+1.18]¢. |
| **C** | WINS/EXACTWINS dutch-book scanner | **KILL** | 1,514 pairs → 118 mid inversions → 61 two-sided → **0 executable → 0 persisting**. Closest candidate was **1¢ short** of break-even *before* fees. |

Three clean KILLs, same root cause each time: **the mechanic is real and correctly documented, but
the $0.01 minimum tick and the bid–ask spread are each larger than the mechanic is worth.**

## 0. Method

- **Terms were read, not assumed.** 37 certified PDFs downloaded from
  `assets.kalshi.com/contract_terms/` → `satellites_research/contract_terms/*.txt`, each carrying
  its source URL. Every rule below is verbatim.
- **Catalogue:** 9,839 series → **2,662 contract-terms templates**. The template, not the series,
  carries the rule.
- **All historical prices from candlesticks** (settled LIST endpoints null bid/ask; `last_price` is
  settlement-contaminated), anchored backwards from `close_time`. 607 markets' candle histories cached.
- **Event-clustered** bootstrap CIs throughout (one film / one award category-year = one observation).
- ~1,900 Kalshi requests at ≤1.4 req/s, all cached, no 429s. Zero Odds API credits.

## 1. Study A — Award tie regimes → KILL

### 1.1 The rule splits four ways, not one

**Regime 1 — named tie strike** (`GLOBES.txt`, verbatim identical in `CRITICS`, `AMAS`, `TONYS`,
`BILLBOARDAWARDS`):

> "If there is a tie, the strike for "tie" will resolve to Yes and the strikes for the shared
> winners alone will resolve to No."

**Regime 2 — the unified `AWARDS` rulebook** (nine PDFs all opening with header `AWARDS`: `AWARDS`,
`SAGAWARDS`, `BETAWARDS`, `AWARDSCMA`, `LATINGRAMMY`, `LATINGRAMMYS`, `IHEARTRADIOMUSICAWARDS`,
`PRODUCERSGUILDAWARDS`, `FILMINDEPENDENTSPIRITAWARDS`):

> "Resolution scenarios: If there is a tie, a strike for a tie will resolve Yes and all others will
> resolve No."

**Regime 3 — silent** (`OSCARS`, `EMMYS`, `GRAMMY`, `ACMA`, `BAFTA`, `GAMEAWARDS`): zero occurrences
of "tie"/"tied". Rule 6.3 discretion.

**Regime 4 — RANKLIST, hiding in the same naming space.** `GOLDENGLOBESNOM.pdf` is not an award
template at all; it serves **RANKLIST** ("Will \<candidate\> be \<rank\> on \<list\>…"), whose tie
rule is the *golf/KXLEADER* one:

> "If multiple candidates are tied for a given rank, then their Contracts will resolve to $1/(the
> number of candidates tied), rounded down."

**The two dead-heat regimes sit one PDF apart under similar tickers.** Check the PDF header, not the
ticker — **the P-022 intuition does not transfer to award winners.**

| Regime | Templates | Series | Tradeable? |
|---|---|---|---|
| 1 — named tie strike | 5 | 122 | rule clear |
| 2 — unified AWARDS | 9 | 49 | same |
| 3 — silent | 6 | 236 | **no** — discretion |
| 4 — RANKLIST pro-rata | GOLDENGLOBESNOM + family | — | different mechanic |

All `fee_type: quadratic` → **maker fee zero**, taker 0.07·P·(1−P).

### 1.2 Listing census

| Regime | Template | Events | With tie strike | Fully settled | Ties observed |
|---|---|---|---|---|---|
| 1 | CRITICS | 2 | 2 | 0 | 0 |
| 1 | TONYS | 25 | 25 | 25 | 0 |
| 2 | BETAWARDS | 3 | 3 | 3 | 0 |
| 2 | FILMINDEPENDENTSPIRIT / LATINGRAMMYS / SAGAWARDS | 3 | 3 | 0 | 0 |
| 3 | ACMA | 2 | 2 | 1 | 0 |
| 3 | EMMYS | 16 | 16 | 0 | 0 |
| 3 | OSCARS | 7 | 7 | 0 | 0 |
| 3 | GAMEAWARDS / GRAMMY | 3 | 2 | 0 | 0 |
| | **TOTAL** | **61** | **60** | **29** | **0** |

Two findings: **Kalshi lists tie strikes on the silent templates too** (all 7 Oscars events, all 16
Emmys), and **GLOBES / AMAS / BILLBOARDAWARDS return zero markets** — Kalshi purges markets from
settled Entertainment events while keeping the event shell (`/events/KXGGAFILM-26` →
`"markets": []`). The three headline templates have no retrievable settled price history at all.

### 1.3 The anchor trap

`TONYS.txt`: *"The Last Trading Date of the Contract will be the same as the Expiration Date"*,
Expiration time **10:00 AM ET** — the morning **after** the ceremony.

| Anchor | n | median bid | median ask | Ex ante? |
|---|---|---|---|---|
| T−168h / T−72h / T−24h | 28–29 | **1.0¢** | **5.0¢** | **yes** |
| T−6h / T−1h | 29 | 0.0¢ | 1.0¢ | **no — winner already public** |

The tie collapsing to 1¢ in 29/29 events is not the market ignoring it; it is the market *knowing*
there was no tie. The honest ex-ante quote is **1¢ bid / 5¢ ask**. (Medians used deliberately — the
T−24h *means* are contaminated by stale one-lot candles printing 97–98¢ asks on empty books, exactly
the artifact this task warns about.)

### 1.4 Base rates vs the tick (modern era, 2001–26)

| Show | Tie rate | Rule permits ties? | BUY @5¢ | SELL @1¢ |
|---|---|---|---|---|
| Critics' Choice (film) | **2.70%** (15/~555) | in practice; rulebook unsourced | −2.63¢ | −1.77¢ |
| Grammys | 0.69% (17/~2,452) | yes, explicit, no tiebreak | −4.64¢ | +0.24¢ |
| BET / Oscars / SAG | 0.24–0.35% | yes, explicit | ≈−5.0¢ | +0.58–0.69¢ |
| Tonys | 0.16% (1/~625) | silent → co-winners | −5.17¢ | +0.77¢ |
| Golden Globes | **0%** (0/~684, last tie 1998) | **no — broken by nomination-ballot points** | −5.33¢ | +0.93¢ |
| Emmys | **~0%, structurally** | **no** — *"Ties in the final round of voting will be broken by referencing the tied nominations' relative voter approval in the first-round nominating ballot"* | −5.33¢ | +0.93¢ |
| AMAs / Billboard / Game Awards | ~0 (data-determined) | no published rule | −5.33¢ | +0.93¢ |

Three corrections: the **Globes anecdote is backwards for the modern era**; the **Emmys are
structurally tie-proof** (rule verified in the TV Academy rulebooks — absent from the 2020 book,
present from 2021 onward, i.e. effective from the 73rd Primetime Emmys); **Critics' Choice is the
only show clearing the 1¢ tick** — and at the observed 1¢/5¢ quote **both sides lose**.

### 1.5 Haircut invisible, capacity trivial

Favourite haircut is ≤1–2¢ at best and sub-tick everywhere else. Measured award-book overround at
T−24h on settled events: summed YES **bids** 0.29→1.02, summed YES **asks** 1.09→4.42. No sub-tick
signal survives a 10–300pp-wide band.

Across **all 25 live tie strikes**: OI **39,038 ct ≈ $390** premium at 1¢; **24 of 25 have bid
0.00 × 0 size** (only `KXOSCARSUPACTO-27-TIE` bids 2¢ × 992). **The profitable direction (fade)
cannot be taken at all** — only made, queueing behind e.g. 140,849 contracts already offered at the
tick on `KXOSCARPIC-27-TIE`.

**KILL.** The brief's direction is refuted: the tie strike is *over*priced 7–30× ex ante on every
show but Critics' Choice. The profitable side is worth ≤0.93¢/ct on 99¢ collateral, requires resting
at the minimum tick, and addresses ~$390. This is the P-016 trap with a 99:1 tail.

**Free falsification:** re-read Critics' Choice tie asks when the 2027 ceremony lists (Dec 2026 /
Jan 2027). If they quote at the 1¢ tick with real offered size, ~2.7% makes the *long* side +1.6¢/ct
— still under the 2¢ gate, but it is the one observation that could move this. Caveat: the CRITICS
template covers **film and TV** categories; the 2.7% is film-derived.

## 2. Study B — RT fallback and read-time drift → KILL

### 2.1 Rules, verbatim

`RT.txt` (207 series):

> **Underlying:** "Rotten Tomatoes' Tomatometer "All Critics" Tomatometer score for \<movie\> on
> \<date\> at 10:00 AM ET."
> **Payout Criterion:** "…If no data is available at 10:00 AM ET on the Monday following wide
> release, then the value at 10:00 AM ET on Tuesday will be used. **If no data is available a week
> after that Monday, then all markets will resolve to No.**"

**Correction 1 — `RTTV` has no such clause.** Its Payout Criterion stops at the
\<above/below/between\> sentence; Contingencies delegate to discretion (Rule 6.3(b)). The briefed
"RTTV: day 3" fallback **does not exist** — day-3 is the *Underlying read time*, not a resolution
rule. Half the claimed 238-series surface is void, and RTTV returns **zero markets** from the API.

**Correction 2 — Last Trading Time *is* the read time.** Trading closes 10:00 ET on the same Monday
the score is read (confirmed: `close_time = …T14:00:00Z` on 19/19 settled events). **There is no
close-to-settlement drift window.**

### 2.2 The ladder is not a partition — but not as assumed

Across all **523 RT markets**, **every strike is `strike_type: "greater"`** — nested *"Above X"*
binaries. No `"below X"` or `"between"` strike has ever been listed. In 19 settled events, 3–19
strikes resolved Yes each; 19/19 perfectly monotone.

So there is **no "safe low bracket"** — there are no brackets. And **an all-No settlement is not
identifiable as the fallback**: in a nested ladder it is the legitimate outcome whenever the score
falls below the lowest strike. Any census counting all-No settlements as "the fallback firing"
counts the wrong thing.

**Fallback census: 0 firings in 19 settled events.** **Can it occur?** `RT.txt` restricts listings to
films *"significantly covered by industry trade publications"*; the fallback needs a **wide-release**
film with **no Tomatometer a week later**. RT assigns a score at 5 reviews; the settled sample
carried 41–180 reviews by opening weekend. Near-unrealisable for the listed population.

### 2.3 Drift — the artifact, then the honest test

**The naive result is a trap worth recording.** Implied-median-vs-realised gives −0.49 pts at T−1h,
CI [−0.55,−0.42], 18/19 same side. Entirely mechanical: "realised" is the midpoint of
`[max(yes)+1, min(no)]`, which for an exact bracket *is* the integer score, while the interpolated
0.5-crossing of a step survival function sits at the midpoint of the two bracketing strikes —
**exactly 0.5 grid-points lower by construction.**

Per-strike vs realised 0/1 payout:

| Anchor | strikes | events | mean(payout − mid) | 95% CI |
|---|---|---|---|---|
| T−72h | 315 | 19 | +0.0156 | [−0.0107, +0.0438] |
| T−48h | 338 | 19 | +0.0142 | [−0.0066, +0.0347] |
| T−24h | 338 | 19 | +0.0130 | **[+0.0009, +0.0247]** |
| T−6h | 338 | 19 | +0.0045 | [−0.0043, +0.0132] |
| T−1h | 338 | 19 | +0.0046 | [−0.0023, +0.0129] |

A genuine small bias exists (market under-prices YES on "Above X"). But mids are not tradeable.
Buying the whole ladder **at the ask**, net of taker fee:

| Anchor | half-spread | S+ buy ladder | 95% CI | S− short ladder | 95% CI |
|---|---|---|---|---|---|
| T−72h | 1.23¢ | **−0.03¢/ct** | [−2.65,+2.73]¢ | −3.19¢ | [−6.12,−0.51]¢ |
| T−48h | 1.27¢ | −0.10¢/ct | [−2.09,+1.79]¢ | −2.98¢ | [−5.26,−0.73]¢ |
| T−24h | 0.99¢ | **+0.10¢/ct** | [−1.02,+1.18]¢ | −2.54¢ | [−3.88,−1.21]¢ |
| T−6h | 1.28¢ | −1.15¢/ct | [−2.07,−0.35]¢ | −2.09¢ | [−4.04,−0.60]¢ |
| T−1h | 1.26¢ | −1.16¢/ct | [−2.03,−0.44]¢ | −2.13¢ | [−4.26,−0.80]¢ |

The +1.3¢ mid bias is entirely consumed by the ~1.0–1.3¢ half-spread plus fee. **Nothing clears 2¢
at any anchor.**

### 2.4 Wayback corroboration — honest coverage

Attempted 10 films. **3 usable**, 1 rejected as a slug mismatch, 6 unusable (no CDX snapshots in the
release-week window, or score not extractable from archived markup). **Coverage: 3 of 10 (30%).**
Nothing inferred for films without snapshots.

| Film | archived Tomatometer path (reviews) | settled | market implied median, same timestamps |
|---|---|---|---|
| Backrooms | 81 (54) → 85 (66) → 88 (80) → 88 (126) → 89 (141) → 90 (164) | **89** | 80.8 → 83.1 → 86.0 → 87.9 → 89.3 → 89.5 |
| Evil Dead Burn | **94 (32)** → 80 (54) → 77 (83) → 73 (94) → 71 (109) → 70 (149) | **71–72** | 79.0 → 76.2 → 74.0 → 71.9 → 68.7 → 70.9 |
| Moana | 32 (41) → 36 (67) → 38 (106) → 35 (135) | **33** | 33.8 → 36.3 → 36.4 → 34.6 |

The mechanism is **real and large** — Evil Dead Burn moved **−24 points** from embargo lift to
settlement. But the market already discounts it: at the embargo-lift snapshot it priced 79.0 against
a live 94. Residuals at the earliest anchor are ±8 points and **two-signed** — exactly consistent
with the executable table.

**KILL, both mechanisms.** The fallback surface is half as large as briefed, has never fired,
wouldn't be identifiable if it had, and is near-unrealisable. The drift is real at mid and
**+0.10¢/ct executable**, CI spanning zero.

## 3. Study C — WINS/EXACTWINS scanner → KILL (scanner retained)

### 3.1 One rulebook

`NFLWINS.pdf`, `NFLEXACTWINS.pdf`, `MLBWINS.pdf` all open with header **`WINTOTAL`** and are
verbatim identical. Every strike is a deterministic function of one integer → P(≥N) monotone,
P(≥N)−P(≥N+1)=P(=N), Σ P(=N)=1, **no forecast required.**

### 3.2 EXACTWINS is not listed

All 32 `KXNFLEXACTWINS*` series exist but carry only a **2025-season event with zero markets**.
**The exact-N partition test has no live data** and could not be run.

### 3.3 Attrition table — the deliverable

Two snapshots, 2026-07-26 19:45 and 20:53 UTC, **1,718 live markets** each across 8 WINTOTAL templates.

| Filter | Definition | scan1 | scan2 |
|---|---|---|---|
| **F0 candidates** | adjacent `≥N`/`≥N+1` pairs in a team-season ladder | **1,514** | 1,514 |
| **F0′ mid inversions** | mid(≥N+1) > mid(≥N) — the naive "violation" | **118** (7.8%) | 116 |
| **F1 two-sided** | both legs bid>0 **and** ask<1 **and** non-zero size both sides | **61** | 61 |
| **F2 executable** | survives crossing the spread: bid(≥N+1) − ask(≥N) > 0 | **0** | **0** |
| **F3 depth** | ≥20 ct on every leg | vacuous | vacuous |
| **F4 net of fees** | clears taker fee both legs + ½-tick slippage | vacuous | vacuous |
| **F5 persistence** | present in both snapshots ≥1h apart | **0** | |

Per template (scan1): NFLWINS 512/31/9/0 · NCAAFWINS 341/46/31/0 · NBAWINS 282/20/15/0 · MLBWINS
182/13/3/0 · NCAAMBWINS 104/3/3/0 · NFLDIVISIONTOTALWINS 56/0/0/0 · WNBAWINS 35/5/0/0 · NFLXWINS 2/0/0/0.

Closest candidates, **gross of fees**:

| gross | buy leg | ask × size | sell leg | bid × size |
|---|---|---|---|---|
| −1.0¢ | KXNFLWINS-27PIT-1 | 0.99 × 25 | KXNFLWINS-27PIT-2 | 0.98 × 755 |
| −1.0¢ | KXNFLWINS-27BAL-4 | 0.98 × 5 | KXNFLWINS-27BAL-5 | 0.97 × 5 |
| −2.0¢ | KXNCAAFWINS-26ND-8 | 0.99 × 50 | KXNCAAFWINS-26ND-9 | 0.97 × 505 |

**112 of the 118 mid inversions recur in both snapshots (92% overlap)** — they are *stable* artifacts
of one- and five-lot resting orders on otherwise-empty books (`ask_sz = 5.00` recurs constantly), not
transient noise. **Persistent, and still not a trade.**

### 3.4 A stronger test: the league adding-up constraint

All 32 NFL team ladders are complete at unit increments 1…17, so one contract of every `≥N` leg pays
exactly *W* dollars. Summed over the league the payoff is structural: 32×17/2 = **272 wins**, less
one per tie game (0–2/season). A true 544-leg dutch-book test needing no forecast.

| Quantity | scan1 | scan2 |
|---|---|---|
| Buy all 544 legs at ask | $283.08 | $283.11 |
| Sell all 544 legs at bid | $256.87 | $256.85 |
| **Mid** | **$269.98** | **$269.98** |
| Structural payoff | 272 − ties ≈ 270.5–272 | |
| Taker fees, 544 legs | $3.97 buy / $4.02 sell | |
| **Net if bought / sold** | **−$18.05 / −$19.15** | −$18.08 / −$19.17 |

**Mid-implied total NFL wins = 269.98 against a structural 272** — a 0.7% error, inside expected tie
games, sitting mid-band in a **26-win-wide** bid/ask. The complex is collectively coherent; the
entire gap is spread.

**KILL. Capacity: $0.** `wins_scanner.py` is worth keeping as a standing check — re-run when NFL
season-wins liquidity thickens (Aug–Sep) and once `KXNFLEXACTWINS*` lists 2026 markets, since the
exact-N partition test has never touched live data. ~20 min, ~1,700 markets, cannot place an order.

## 4. Corrections to the working brief (banked)

1. The award tie rule spans **14 templates in 2 textual variants**, not 3.
2. **`GOLDENGLOBESNOM` serves the RANKLIST rulebook** — ties pay **pro-rata $1/n**, the opposite
   regime, one PDF away.
3. **Kalshi lists "tie" strikes on templates silent on ties** (Oscars, Emmys, Game Awards).
4. **`RTTV` has no all-No fallback.** The 238-series RT surface is really 207.
5. **RT events are nested "Above X" binaries, never brackets**; an all-No settlement is not evidence
   the fallback fired.
6. **RT's Last Trading Time is the read time** — no close-to-settlement drift window exists.
7. **Modern Golden Globes and Emmys break ties by rule**; their tie strikes are worth ≈0.
8. **GLOBES / AMAS / BILLBOARDAWARDS settled markets are unavailable** — Kalshi purges markets from
   old settled Entertainment events, keeping the event shell.
9. **`KXNFLEXACTWINS*` exists as series but carries no markets.**

## 5. Anomaly worth a second look

**~36,000 contracts of open interest sit long on award "tie" strikes at 1¢** (Game Awards 21,276;
Oscars Best Picture 14,899). On the base rates in §1.4 those are negative-EV positions — and they
look like **someone else running exactly the hypothesis this task just killed.**

## 6. Calendar-dated free falsifications

1. **Critics' Choice tie asks** when the 2027 ceremony lists (Dec 2026 / Jan 2027).
2. **Re-run `wins_scanner.py`** once `KXNFLEXACTWINS*` lists 2026 markets — the exact-N partition
   test has never run against live data.

**Deploy list: nothing.** This task found no config the droplet is not running.

---

*Verification note: the orchestrating session independently confirmed the contract-terms claims from
the cached extractions — `GLOBES.txt:64-66` carries the named-tie-strike clause verbatim,
`OSCARS.txt` contains zero occurrences of "tie", `RT.txt` carries the "week after that Monday"
fallback, and `GOLDENGLOBESNOM.txt` is the RANKLIST rulebook ("Will \<candidate\> be \<rank\> on
\<list\>…") with pro-rata "$1/(the number of candidates tied), rounded down".*
