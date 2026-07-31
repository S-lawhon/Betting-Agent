# P-014 — Pre-Registered Decision Rule

**Written 2026-07-29 at n = 331 of 500, BLIND. LOCKED 2026-07-31 by Sam,
option (a) — see §9. §2–§8 are frozen.**

`scripts/p014_checkpoint.py` is the **only sanctioned reader** of P-014's
results. A P&L chart, a dashboard tile, an eyeballed jsonl tail, or a
per-market breakdown is not a verdict. If the checkpoint has not printed it, it
has not happened.

This document exists because P-013 lost **$2,094** while its criteria were
still being decided after the fact. P-015 and P-022 each have a rule locked
before results existed. P-014 has been trading since **2026-03-29** with none,
and is the first gate in this fund's history with a real projected resolution
date (**2026-10-23**). A rule written at n = 331 is far more defensible than
one written at n = 480; one written after unblinding is worthless.

---

## 0. BLINDNESS ATTESTATION — read this before anything else

### What I deliberately read

Placement-side properties only: `n` (331 settled, 345 terminal, 14 VOID, 0
unpriced), first/last settlement dates, active days, sport and venue
composition, `market_type`, `side`, `event` and `game_time` structure,
`position_size_usd`, and the source code of `scripts/p014_checkpoint.py`.

### What I did NOT read

`--unblind` was **never run**. I did not read P&L, edge, realised win rate at
the sanctioned reader's own definition, CLV, calibration, entry prices, or any
per-trade result field (`outcome`, `pnl_usd`, `result`, `settlement_*`).

### ⚠️ CONTAMINATION — one incident, disclosed in full

While counting the composition of P-014's rows I aggregated the **`action`**
field, not realising it *encodes the outcome*. The output printed:

```
actions: [('PLACED', 210), ('DATA_COLLECTION', 114), ('LOSS', 99),
          ('WIN', 96), ('VOID', 4)]
```

**So I have seen a raw win/loss count — 96 W / 99 L — on a 523-row,
own-deduplication view of the logs.** `action` was added to the forbidden list
immediately and every subsequent query dropped it at the parser.

**What this does and does not compromise, stated so Sam can judge rather than
take my word:**

* It is **not** the gate statistic. The statistic below is a fee-net,
  game-clustered mean edge in cents per contract. A raw win/loss count carries
  almost no information about it: P-014 is a calibration play that takes both
  sides (`YES` 205 / `NO` 204) at a wide range of prices, so wins at cheap
  prices and losses at expensive ones — or the reverse — are consistent with
  any edge whatsoever. I never saw a price.
* It is **not** the sanctioned reader's number either. My view deduplicates on
  `(market_id, timestamp_utc)` and covers a different row population from the
  checkpoint's fingerprint-based one (which reports 331 settled, not 195).
* It **is** an outcome, and it could in principle anchor a threshold.

**The defence is in how the thresholds were chosen, and it is checkable.**
Every number in §4 is either inherited from an existing locked document
(`z ≥ 2.0` PASS, `z ≤ −2.0` hard kill, from P-015 and P-022 §4) or derived
from an outcome-independent measurement (§3 clustering, §6 power). **No
threshold in this document is a function of any outcome.** A reviewer can
verify that by checking that removing the contaminated observation changes
nothing: the numbers come from the house pattern and from the row-per-game
ratio, and would be identical had I never run that query.

**Sam's call, and it should be made explicitly:** accept this rule as written,
or have §4's thresholds re-derived by a session that has seen nothing. My
recommendation is to accept it and record the incident, because the
alternative — delaying the rule further while n climbs toward 500 — trades a
small, disclosed, mechanically-checkable contamination for a large and growing
one. **This document is not locked until Sam answers §9.**

---

## 1. The claim being tested

From `manager/registry.yaml`:

> *"INCONCLUSIVE — not significant but excellently calibrated. Hold, do not
> scale."*
> Gate question: *"Does the calibration edge become statistically significant
> with more n?"*

P-014 is the live in-play game agent: it prices in-play MLB and NBA moneylines
against a win-probability model and takes the side the market misprices. It is
a **"we have better information" hypothesis**, and the standing prior on that
category in this operation is **0 for 7**. That prior is not a reason to write
a lenient rule; it is a reason to write the rule now, while it costs nothing.

**Prior, stated honestly.** There is no backtest with a pre-registered
effect size behind P-014 — unlike P-022, whose T = 14 was sized against a
measured +3.4¢/ct. That absence is itself a finding, and §6 confronts it:
without a target effect, the threshold of 500 was never sized against
anything.

---

## 2. Admissibility — which rows count

A row counts toward `n` and toward the statistic if and only if:

1. `pod_id == "P-014"`, and
2. its `outcome` is `WIN` or `LOSS`, and
3. `entry_price(row)` returns a real price in `(0, 1)`.

**Explicitly excluded, each for a reason this fund has already paid for:**

* **VOIDs are excluded from both `n` and the statistic.** No risk was taken.
  They are reported alongside (`n_voids`, currently 14 of 345 = 4.1%) so the
  exclusion is visible rather than silent. This matches every other pod here.
* **A row with no readable price is EXCLUDED, never defaulted.** P-014 writes
  `fill_price: null` on every row and carries the entry in
  `venue_prob` / `kalshi_prob`; `entry_price()` tries all three and returns
  `None` otherwise. **It must never substitute a constant.** The precedent is
  P-015's `or 0.9` fallback, which fabricated a breakeven price for any row
  missing one and therefore fabricated the statistic's input. Currently
  `n_unpriced = 0`; if that ever becomes non-zero the checkpoint must report
  it and shrink `n`, not paper over it.
* **`DATA_COLLECTION` and `PLACED` rows are not settlements.** They are
  telemetry and open positions respectively.
* **No sub-slicing, ever.** The verdict is computed on every admissible P-014
  row. *"It works if you drop NBA"*, *"if you exclude April"*, *"if you only
  count favourites"* is not a result — it is a new hypothesis needing its own
  registration, under its own pod ID. The checkpoint prints the all-in number
  first and must refuse to filter.

**`result="scalar"` — pre-registered now even though P-014 has never seen
one.** A scalar settlement is a **partial payout at
`settlement_value_dollars`, not a void and never $0**. If a P-014 market ever
settles scalar it is booked at its realised value, counts as `WIN` or `LOSS`
on the sign of realised P&L, and is **never excluded**. This cost the golf
settler a whole class of outcomes until 2026-07-26 and it is written here so
that P-014 cannot repeat it.

**Anchor contemporaneity.** P-014's edge is defined against the model's fair
value *at the moment of the fill*. A row whose `venue_prob` / `kalshi_prob`
was not captured contemporaneously with `timestamp_utc` is inadmissible. The
P-001 precedent is 86% of bets priced off a different day's game; the in-play
version of that mistake is a model probability computed from a game state the
fill did not face. **If contemporaneity cannot be established from the row,
the row is excluded** — the same rule as a missing price.

---

## 3. Unit of observation: the GAME

**One game = one observation. Always. No exceptions.**

This is the single most consequential choice in the document, so here is the
measurement rather than an assertion. Across P-014's full placement
population — 523 rows, outcome fields dropped at the parser:

```
distinct `event` values (games) : 129
rows per game                   : median 4, p90 6, max 20
games with more than one row    : 126 of 129
sports                          : live_baseball_mlb 425, live_basketball_nba 98
sides                           : YES 205, NO 204
```

**126 of 129 games carry more than one row.** Within one game every P-014
position is decided by the same scoreboard: one comeback, one bullpen
collapse, one fourth-quarter run moves every fill in that game together, and
a pod that quotes both `YES` and `NO` in the same game is holding two legs of
the same outcome. Treating each row as independent would understate the
standard error by roughly `sqrt(4)` — a factor of two on the z-score, which
is the difference between PASS and NO DECISION.

This is not hypothetical. **P-017M produced a phantom +9.1¢/ct that corrected
to +3.34¢ once tournament weighting was fixed**, and P-022's rule hard-codes
the same lesson.

Concretely:

* **Within a game**, the per-game statistic is the **contract-weighted** mean
  net ¢/contract across every admissible fill in that game.
* **Across games**, each game enters with **equal weight**. A game with 20
  fills and a game with 1 count the same.
* A game is one `event` value. MLB and NBA are pooled — splitting them is a
  sub-slice under §2, and neither has enough games alone.

---

## 4. The statistic and the decision schedule

For each admissible fill:

```
settlement_value = 1.00                      if outcome == WIN
                 = 0.00                      if outcome == LOSS
                 = settlement_value_dollars  if result == "scalar"

fee = ceil(0.07 * P * (1 - P) * 100) / 100   # P = entry_price(row), the
                                             # ACTUAL traded price, never a
                                             # mean and never an average rate
pnl_per_contract = (settlement_value - entry_price) * direction - fee
```

`fee` is the Kalshi **taker** fee — P-014 crosses the spread; it does not
make. It peaks at 1.75¢/ct at P = 0.50 and collapses to 0.33¢ at P = 0.95, so
computing it at a mean price rather than the traded price misstates it by more
than most plausible edges. `src.kalshi_fees.fee_per_contract` is the only
sanctioned source, called **with the series ticker**.

Then, with `G` games:

```
x_g  = contract-weighted mean pnl_per_contract within game g   (¢/ct)
edge = mean(x_g)                    # EQUAL weight per game
se   = stdev(x_g) / sqrt(G)
z    = edge / se
```

### Decision schedule

| condition | rule |
|---|---|
| **any n** | **HARD KILL** if `z ≤ −2.0`. Stop immediately. No extension, no re-parameterisation, no "let's try the other side." |
| **n < 500** | **NO DECISION** — underpowered. Do not act, do not scale, do not report a verdict. |
| **n ≥ 500** | **KILL** if `edge ≤ 0`. |
| **n ≥ 500** | **PASS** if `edge > 0` and `z ≥ 2.0`. |
| **n ≥ 500** | **CONTINUE — single extension** if `edge > 0` and `z < 2.0`. See §7. |
| **n ≥ 900** | **KILL** if still `z < 2.0`. The extension is spent. |

`n` is counted in **admissible settled rows**, because that is what the
registry's threshold of 500 already declares and changing the counted unit
would be moving the gate. **The statistic is computed on games.** These are
different units on purpose, and §6 is about the gap between them.

**Why these numbers, given the contamination in §0:**

* **`z ≥ 2.0` for PASS and `z ≤ −2.0` for HARD KILL** are inherited verbatim
  from `P015_DECISION_RULE.md` and `P022_DECISION_RULE.md` §4. Using the house
  bar rather than inventing one is the whole point — it is the number that was
  chosen before any of these pods had results.
* **`edge ≤ 0` ⇒ KILL at n = 500** needs no calibration: a strategy whose
  point estimate is not positive after its declared sample has failed.
* **500** is the registry's existing threshold, unchanged. This document does
  not move it. §6 says what it buys, which is less than it looks.
* **900** is `500 × 1.8`, matching P-022's single extension ratio
  (`14 → 40` is 2.9×; P-016's was one extension at unchanged parameters). It
  is deliberately not round and was not chosen after seeing a forward result.

**PASS authorises more paper allocation and a written promotion proposal. It
does not authorise live money.** Live is a separate decision by Sam against
the fund's live-promotion bar, and nothing here grants it.

---

## 5. The reader

```bash
python3 -m scripts.p014_checkpoint          # progress + verdict
python3 -m scripts.p014_checkpoint --json   # machine-readable
```

`scripts/p014_checkpoint.py` implements §2, §3 and §4 and nothing else. It is
the only sanctioned reader (this file is its `rule_document`).

**Requirements on the reader, each of which has already failed somewhere in
this repo:**

1. **It must return `None` — never a stale fallback — when it cannot read.**
   A projection built on a stale progress number is worse than no projection,
   because it looks like measurement.
2. **It must read the file the pod actually writes.** P-015's reader pointed
   at `data/pods/P-015.jsonl`, *which does not exist*, and reported 0 against
   5 real settlements. P-014's reader globs `data/trade_logs/trade_log*.jsonl`
   plus `.gz` and `archive/`, **because rotation TRUNCATES the live file** and
   a reader that opens only `trade_log.jsonl` under-reports by whatever has
   rotated. Verified 2026-07-29 on the droplet: those globs return
   **331 settled / 345 terminal / 14 VOID / 0 unpriced**, over 41 active days
   from 2026-03-29 to 2026-07-26.
3. **It must not print a verdict this document does not define.** Until this
   file is locked (§0), the reader correctly returns
   `NO DECISION — no pre-registered rule exists`.
4. **The economics stay behind `--unblind`** until the rule is locked, so they
   cannot leak into the daily brief and anchor anything.

**Sanity check performed, without breaking blindness:** the reader runs
end-to-end on the droplet and emits `progress`, `threshold`, `metric`,
`n_terminal_incl_voids`, `n_voids`, `n_unpriced`, `active_days` and
`rule_document`. The output *shape* was asserted; `wins`, `losses` and every
`--unblind` field were filtered out of the query before display.

Once this document is locked, the reader must be updated to implement §3 and
§4 — **it currently does not compute a game-clustered statistic at all.** That
is a code change to make against a locked rule, not a rule to write against
existing code.

---

## 6. Power — and the uncomfortable part

**n = 500 rows is not 500 observations.** At the measured 4.05 rows per game,
500 admissible settled rows is roughly **123 game clusters**, and today's 331
is roughly **82**. The gate's threshold is stated in a unit that overstates the
sample by a factor of four.

What `G = 123` detects, one-sided at the critical `z = 2.0`, as a function of
the between-game SD `sigma`:

```
detectable edge at 90% power  =  (2.0 + 1.2816) * sigma / sqrt(G)
```

| between-game SD | G = 82 (today) | **G = 123 (n = 500)** | G = 222 (n = 900) |
|---|---:|---:|---:|
| 10 ¢/ct | 3.6 ¢ | **3.0 ¢** | 2.2 ¢ |
| 20 ¢/ct | 7.2 ¢ | **5.9 ¢** | 4.4 ¢ |
| 30 ¢/ct | 10.9 ¢ | **8.9 ¢** | 6.6 ¢ |

**`sigma` is unknown and cannot be estimated without unblinding**, which is
the price of writing this rule honestly. But the range is bounded by the
instrument: a single in-play moneyline fill resolves to $1 or $0 against an
entry between roughly 0.1 and 0.9, so per-fill dispersion is on the order of
**40–50¢**, and game-level averaging over ~4 correlated fills reduces it
little — correlated fills do not diversify. **A between-game SD of 20–40¢ is
the realistic band**, which puts the detectable edge at **n = 500 somewhere
around 6–12 ¢/ct.**

**Say it plainly, now, while it costs nothing: that is a very large edge for a
market-taking in-play strategy, and n = 500 is probably underpowered for any
edge P-014 actually has.** A 2–3 ¢/ct true edge — which would be a genuinely
good result for this pod — would need **G ≈ 500–2,000 games**, i.e. **n ≈
2,000–8,000 rows**, which at 41 active days for 331 rows is **years**.

Three things follow, and they are for Sam, not for me:

1. **The 500 threshold was never sized against a target effect.** No backtest
   with a pre-registered effect size exists for P-014. That is the actual gap,
   and no choice of threshold repairs it.
2. **A KILL at n = 500 is well-powered; a NO-DECISION is not informative.**
   The rule above is therefore honest in one direction and weak in the other,
   and §7 is what stops that weakness from becoming an indefinite hold.
3. **Reducing `sigma` is worth more than increasing `n`.** Fixed stake sizing,
   a narrower entry-price band, or one fill per game would each cut the
   between-game SD far faster than the calendar can add games. **All three are
   parameter changes and would reset this gate under a new pod ID.** They are
   listed as options for a P-014b, not as adjustments.

---

## 7. What happens at n = 500 if the verdict is NO DECISION

Pre-registered now, so it cannot be renegotiated in the moment:

* `edge > 0` and `z < 2.0` at n = 500 ⇒ **CONTINUE, one extension, to
  n = 900, at byte-identical parameters** — same model, same entry band, same
  sizing, same sports, same venue. Any parameter change is a **new hypothesis
  (P-014b)** with its own registration and its own counter reset to n = 0.
* At **n = 900**: `z ≥ 2.0` ⇒ PASS, otherwise **KILL**. There is no second
  extension and no "it was close, give it another month."
* **`edge ≤ 0` at n = 500 is a KILL, not a continuation.** A pod that is not
  even pointing the right way after its declared sample does not get more
  calendar.
* **Silence is not confirmation.** If P-014 never reaches n = 500, the verdict
  stays NO DECISION and it stays in paper indefinitely. An unfalsifiable
  strategy is not promoted by default.
* **A HARD KILL is final.** `z ≤ −2.0` stops the pod at any n. It does not
  trigger "let's fade our own model."

At the realised rate — 331 admissible rows in 41 active days from 2026-03-29
— n = 500 projects to **2026-10-23** and n = 900 to roughly **2027-04**.
**The extension is nearly two more quarters.** Sam should decide whether that
is worth holding capital and attention against, *before* the marginal result
arrives and makes it feel like a small ask.

---

## 8. Anti-rationalisation clauses

1. **No mid-flight parameter changes.** Model, entry band, sizing, sports,
   venue. Any change resets `n` to 0 under a new pod ID.
2. **No cherry-picking sub-slices** (§2). The checkpoint prints the all-in
   number first and refuses to filter.
3. **Scalar settlements are counted at their realised value** (§2), never as
   voids and never at $0.
4. **Silence is not confirmation** (§7).
5. **No CLV substitute.** P-014's claim is a settlement edge from better
   in-play information. If a CLV argument is later imported to promote it
   early, that is a different hypothesis and needs its own registration.
6. **The unit is the game, not the fill** (§3), even when the fill count is
   the more flattering denominator.
7. **A HARD KILL is final** (§7).

---

## 9. Status: LOCKED 2026-07-31

> **DECISION (Sam): (a) — lock this document as written, recording the §0
> contamination.**
>
> Given by Sam on 2026-07-31 in a Claude Code session ("Lock it as written —
> option (a)"), after reading §0 and §9 in full. Recorded by the session.
> The options as presented were:
> *(a) Lock this document as written, recording the §0 contamination.*
> *(b) Have §4's thresholds re-derived by a session that has seen nothing,
>     then lock.*
> *(c) Something else.*

The three follow-ups were executed the same day, in order:

1. `rule_status` in `manager/registry.yaml` now points at this file, and the
   gate block carries `locked: true, locked_date: 2026-07-31` plus a
   machine-readable transcription of §4's schedule;
2. `scripts/p014_checkpoint.py` implements §2's admissibility, §3's game
   clustering and §4's schedule, and prints the gate statistic openly —
   `--unblind` is no longer a gate, because the rule now exists;
3. the lock date is recorded here. **§2–§8 are frozen and must never be
   edited again.**
