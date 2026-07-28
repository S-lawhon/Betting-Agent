# P-018 — Pre-Registered Decision Rule

**Status: NOT LOCKED. Written 2026-07-28, blind (see §10).**
**P-018 is INERT and this document authorises nothing.**

Inherits its architecture and its two hard thresholds verbatim from
`golf_quirks_research/P022_DECISION_RULE.md` and `research/P014_DECISION_RULE.md`.
Every threshold below is marked **[inherited]** or **[outcome-independent]**,
and there is no third category.

---

## 1. The claim being tested

> In-play MLB prediction markets **overreact** to surprising game events.
> Resting a one-sided maker quote part-way from the market mid toward a model
> fair value, in the seconds after a high-surprise event, earns a positive
> fee-net edge **that is attributable to the surprise** and not to the model's
> standing disagreement with the market.

The second clause is load-bearing and is the whole content of
`P018_GATE1_REDESIGN.md`. A pod that earns because `src/mlb_win_prob` is
well-calibrated is **not** P-018; it is P-016 with a different quote price,
and P-016 v1 was retired on 2026-07-21.

## 2. Unit of observation: the GAME — not the market, and not the fill

**[outcome-independent]** Measured 2026-07-28 from the capture files, by
counting ticker strings; no outcome was read.

| unit | count | usable as an independent observation? |
|---|---:|---|
| faded fills | 5,676 | **No.** Many per quote, per event, per game. |
| game-days (date × market) | 677 | **No.** Not a unit of anything; a market spans days only through a UTC boundary. |
| **markets (tickers)** | **240** | **No — and this is a live defect.** |
| **games** | **124** | **Yes. This is the unit.** |

**The committed backtest clusters on the wrong unit.**
`backtest_inplay_fade.bootstrap_ci` keys `by_game` on `f["ticker"]`. A
`KXMLBGAME` ticker is one *team*, not one *game*: **116 of the 124 games in
the capture contribute two tickers**, and those two markets resolve on the
same event with **perfectly anti-correlated** outcomes. Clustering on ticker
therefore counts up to **240 clusters where there are 124**.

**CORRECTED 2026-07-28, after measurement — my own prediction here was wrong
and is left visible rather than rewritten.** This section originally predicted
the CI would **widen** by ≈ √(240/124) ≈ 1.39×, to roughly [+3.9, +14.5].
Measured on the identical 5,676 fills, it **narrowed by about 7%**:

| clustering | 95% CI | width |
|---|---|---:|
| ticker (published) | [+5.33, +13.10] | 7.77 ¢ |
| **game (correct)** | **[+5.53, +12.75]** | **7.22 ¢** |

The √(n/n′) reasoning assumed within-cluster observations are positively
correlated or independent. They are **anti-correlated**: the two markets of a
game resolve on the same event with opposite outcomes, so merging them into
one cluster lets their P&L partially offset and *reduces* between-cluster
variance.

**The unit correction stands** — 240 tickers are 124 games and two markets of
one game are one observation — **but it makes the published CI slightly
conservative, not anti-conservative.** See
`research/REPORT_P018_Gate1_2026-07-28.md` §6.

> **RULE:** the cluster is the **game**, keyed on the ticker's matchup
> segment (`ticker.split("-")[1]`), never the ticker. Any verdict computed
> on ticker clusters is void.

**Effective n:** at most **124**, and only games carrying fills count.

## 3. Test statistic

**[inherited from P-022 §3 / P-014]**

* Net **¢/contract**, **fee-net at the actual traded price**, using
  `src.kalshi_fees.fee_per_contract(price, maker=True, series_ticker=...)`.
  Never a hard-coded rate. `KXMLBGAME` is `quadratic_with_maker_fees` and
  **does** charge — this is not a maker-free family like the golf leaders.
* Game-clustered bootstrap, 5,000 resamples, seeded, resampling **games**
  with replacement.
* `z = mean / SE`, SE from the clustered bootstrap.
* **The all-in number is computed and reported first**, before any split.

## 4. PASS / KILL / NO DECISION

**[inherited]** — the `z` thresholds are taken verbatim from P-022 §4 and
P-014 §4, which were fixed before either pod had an observation.

| verdict | condition |
|---|---|
| **PASS** | `z ≥ 2.0` **and** gate #1 (redesigned) returns PASS **and** T ≥ 20 games |
| **HARD KILL** | `z ≤ −2.0` at any T ≥ 10 games — final, no re-parameterisation |
| **KILL** | gate #1 (redesigned) returns KILL, at any T |
| **NO DECISION** | anything else. The pod stays inert. |

**Gate #1 can kill on its own and `z` cannot rescue it.** That ordering is
deliberate: a large `z` with a dead mechanism is precisely the
P-021/P-024/P-025 shape — a real number measuring the wrong thing.

**T = 20 games** **[outcome-independent]** — see §7.

## 5. Admissibility

**[inherited from P-022 §8.3 / the Kalshi-scalar correction]**

* `result="scalar"` is a **partial payout at `settlement_value_dollars`**,
  never a void, never $0. `KXMLBGAME` should not produce scalars, but the rule
  binds anyway: booking one at zero deletes a loss tail.
* A fill with a **missing or unparseable price is EXCLUDED, never defaulted**.
  P-014 wrote `null` fill prices for four months and the backfill only worked
  because nothing had defaulted them to zero.
* A fill in a market whose **settlement is unknown** is excluded, not assumed.
* **Both tickers of the same game are admissible**, but they are one cluster.
* **Coverage bias is an admissibility fact, not a footnote** — see §6.

## 6. The coverage caveat, in the rule where it cannot be skipped

**33.9% of discovered in-play markets are DROPPED from capture, and the drop
is LOWEST-VOLUME-FIRST.** The limiter is the exchange — 128 cumulative 429s,
the daemon self-throttling to 0.6–1.2 req/s — **not** the config, so it cannot
be fixed by collecting harder. Source: the daemon's own 581 DISCOVERY records,
measured 2026-07-28; stated in `src/inplay_surprise.py`'s own docstring.

> **RULE:** every verdict produced under this document is a verdict about the
> **liquid third-to-two-thirds of the in-play book**, and must say so in its
> own words. The dropped markets are exactly the thin ones where a resting
> maker is hardest to fill and most exposed to adverse selection — so the
> bias runs in the **favourable** direction and a PASS here does **not**
> license live sizing across the full book.

This is not a hedge. **P-017A died on fill rate**, and this sample
systematically over-represents the markets where fills happen.

## 7. Power — what this gate can actually detect

**[outcome-independent]** Derived from the sample's dispersion, not its
direction — the table below would be identical if the headline were −9.09 ¢.
**Revised 2026-07-28** from measured clustered SEs after the §2 correction;
the original projection was too pessimistic.

The clustered SE scales as `s / √G` over `G` games. **Measured** on the
game-clustered bootstrap (not projected — see the §2 correction, where a
projection of exactly this kind was wrong): at **91 games carrying fills** the
half-width is **3.6 ¢**, i.e. **SE ≈ 1.85 ¢**. Scaling as `1/√G`:

| G games | SE (¢) | detectable at z ≥ 2 (¢) |
|---:|---:|---:|
| 20 | ≈ 3.9 | ≈ 7.9 |
| 50 | ≈ 2.5 | ≈ 5.0 |
| **91** (observed) | ≈ **1.85** | ≈ **3.7** |
| 124 (all games) | ≈ 1.6 | ≈ 3.2 |

**Read this honestly: at the observed 91 games the gate detects ≈ +3.7 ¢ and
no smaller.** It is adequate against +9.09 ¢ and **inadequate against anything
below ≈ +3.7 ¢** — and +1 to +3 ¢ is where several edges this fund has
measured have lived (P-022 +3.4 ¢, P-023c +0.2 ¢). Better than the ≈ +5.6 ¢
this section first projected, and still not fine-grained.

> **Therefore: a NO DECISION from this gate is the expected outcome for a
> real-but-modest edge, and must never be read as evidence of absence.**
> T = 20 is set as the *minimum* for a PASS, not as sufficient power; a PASS
> at T = 20 requires ≈ 7.9 ¢, which would itself be grounds for suspicion.

## 8. The sanctioned reader

**[inherited from `docs/GATE_INSTRUMENTATION_STANDARD.md`]**

* **One reader**, and no verdict may be quoted from anything else.
* It **returns `None` when it cannot read** — never 0, never "healthy". A
  missing file is *unmeasured*, not *passing*. This is check 1 of the
  standard and the exact defect P-014 and P-016 both carried.
* It must be **pointed at the file the pod actually writes**, verified by a
  **round-trip test**: write a row through the pod's own writer, read it back
  through the reader, assert the statistic. P-016's `source: maker_fills`
  is unresolvable *because nobody ever round-tripped it*.
* It must emit `progress` and `threshold` so `manager/throughput.py` derives
  the date rather than a human typing one.

**The reader does not exist yet.** Until it does, P-018's `blocked_on` is
`backtest`, and this rule is not operable. Writing it is a prerequisite of
any verdict, not a follow-up.

## 9. Anti-rationalisation

**[inherited verbatim from P-022 §8]**

1. **No mid-flight parameter changes.** `surprise_hi`, `surprise_lo`,
   `min_underreaction`, `fade_window_s`, `fade_edge_frac`, `fade_size`. Any
   change resets T to 0 under a new pod ID.
2. **No cherry-picking sub-slices.** Not "it works if you drop the blowouts",
   not "if you only count late innings". The all-in number is the number.
3. **Scalar settlements counted at realised value.**
4. **Silence is not confirmation.** No PASS by default, ever.
5. **A HARD KILL is final.** `z ≤ −2.0` does not trigger "try
   `fade_edge_frac = 0.3`".
6. **No CLV substitute.** Realised settlement only.
7. **Gate #1 cannot be re-redesigned after seeing its result.**
   `P018_GATE1_REDESIGN.md` is committed ahead of the numbers; if its three
   tests disagree, all three are reported and the pod stays inert. Choosing
   the favourable one afterwards is the failure this fund has already made.

## 10. Blindness statement — and one disclosed exposure

`--unblind` was never run. No new per-trade outcome was computed while any
threshold above was drafted. Every threshold is **[inherited]** from a locked
document written before P-018 had data, or **[outcome-independent]** —
derived from ticker strings, event counts, and the arithmetic of the quote
construction.

**Disclosed exposure, in full:** the committed
`inplay_research/REPORT_InPlay_Fade_2026-07.md` is in the repository and I
have read it. **I have seen the +9.09 ¢ headline, its CI, the two populated
surprise buckets and the window-decay table.** The task prompt reproduces
them, so this was unavoidable and is not a slip.

**Why the rule survives it, mechanically and checkably:**

* `z ≥ 2.0` and `z ≤ −2.0` are **quoted from P-022 §4 / P-014 §4**, written
  before either pod had an observation. They were not chosen here.
* The **unit of observation (124 games)** comes from counting ticker strings.
* The **power table** is derived from the published CI's *width* — a
  dispersion, not a direction. It would be identical if the headline were
  −9.09 ¢.
* **T = 20** is a minimum-cluster count, not tuned to any effect.
* The **gate #1 redesign** is derived from reading `_fade_spec`'s arithmetic,
  not from any result.

The one place the exposure could bite is §7's honesty about power — knowing
the headline is +9 ¢ makes "adequate against +9, inadequate below +5" easy to
write. **It cuts against the pod**, which is the safe direction, and the
arithmetic is shown so it can be checked.

## 11. What is authorised right now

**Nothing.** P-018 is inert on four independent counts: not in `src/pods/`,
no `@register_pod`, `enabled: false` in `config_multi_pod.yaml`, absent from
`pods.active`. No engine, no runner, no systemd unit.

This document does not change that and does not ask to. It exists so that
when gate #1 runs, the line it is judged against was drawn first.

**§12 for Sam:** accept this rule as written, or have the thresholds
re-derived by a session that has not read the backtest report. My
recommendation is **accept** — the exposure is disclosed, every threshold is
traceable to a document written before the data existed, and the alternative
costs a week during which the headline sits unadjudicated and someone may act
on it.
