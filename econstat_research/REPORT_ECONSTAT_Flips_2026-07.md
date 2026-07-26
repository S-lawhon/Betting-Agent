# P-027 — ECONSTAT Prelim-vs-Final Settlement Flips

**Date:** 2026-07-26 · **Prompt:** `research/prompts/PROMPT_P027_ECONSTAT_Flips.md` (Task 9)
Paper/demo, read-only public endpoints. No orders, no pod, no deploy, no live `data/` mutation.

## Verdict: **KILL.** The mechanism does not exist.

**Headline: 0 bracket flips in 19 settled release-events in the qualifying set (0 in 164
family-wide). Not "rare" — structurally impossible, because Kalshi lists the preliminary print
as its own `<econ stat>` and settles on it the same morning.**

## 1. The hypothesis, and where it breaks

Half the premise is quoted correctly. Half is false.

**Verified verbatim** — `ECONSTAT.pdf`, Payout Criterion (fetched 2026-07-26;
`ECONSTATTE.pdf` is byte-for-byte the same text):

> "Unless explicitly stated otherwise, only the first non-preliminary release of the relevant
> \<econ stat\> will be used to resolve the market, and subsequent revisions will not affect
> resolution."

and Last Trading Date: *"one (1) minute prior to the expected release of \<econ stat\>."*

**The break is in the definition of `<econ stat>`**, two pages earlier in the same PDF:

> "\<econ stat\> refers to a particular economic statistic **specified by the Exchange**.
> Specification of \<econ stat\> may match the format and terminology of the relevant Source
> Agency."

Kalshi specifies the preliminary print **as the statistic itself**. The flash is not a
provisional estimate of the underlying — it *is* the underlying. So "the first non-preliminary
release of \[Michigan consumer sentiment **prel**\]" is the prelim release. The clause is an
anti-revision clause, exactly as its own worked example says ("A revised CPI value falls within
the range… but the first release did not" → resolves No). **It never opens a prelim→final gap.**

Visible in the certified per-market `rules_primary` that actually governs each market:

| Series | `rules_primary` (verbatim) |
|---|---|
| `KXUSMICHCSP` | "If US Michigan consumer sentiment **prel** for June 2026 is above 56…" |
| `KXFRCPIPREL` | "If France inflation rate YoY **prel** for May 2026 is above 3.1…" |
| `KXITCPIPREL` | "If Italy inflation rate YoY **prel** for May 2026 is above 3.9…" |
| `KXDECPIPREL` | "If Germany inflation rate YoY **prel** for May 2026 is above 3.8…" |
| `KXEZCPIYOYF` | "If Euro area inflation rate YoY **flash** for May 2026 is above 4.1…" |
| `KXDEGDPQOQF` | "If Germany GDP growth rate QoQ **flash** for Q2 2026 is above 0.9…" |
| `KXITGDPYOYA` | "If Italy GDP growth rate YoY **adv** for Q2 2026 is above 2.0…" |
| `KXFRGDPYOYP` | "If France GDP growth rate YoY **prel** for Q2 2026 is above 2.2…" |
| `KXUMICHOVR` | "If any University of Michigan Consumer Sentiment Index **final** monthly release… above 65.0…" |

Run as an exhaustive screen, not a spot check: regex for
`prel|prelim|flash|advance|adv|revised|revision|final|second estimate|third estimate` over
`rules_primary` + `title` of **all 3,156 cached markets in all 197 series**. Every market whose
underlying has a labelled preliminary names that preliminary in its own rules text. **Not one
market in the family trades on a flash and settles on a later final.** The only series naming a
*final* print (`KXUMICHOVR`) says "final" and settles on the final — no mismatch in the other
direction either.

## 2. Which series qualify

Census: `GET /series/?category=…` across 9 categories → 4,106 series → filter
`contract_terms_url` ending `ECONSTAT.pdf`/`ECONSTATTE.pdf` → **197 series**. (R4 said 148+45;
same family, count drifted.)

**All 197 are `fee_type: quadratic`** — verified against live `/series` metadata, not assumed.
Taker = 0.07·P·(1−P); **maker = 0** for the whole family.

**13 series** name a preliminary/flash/advance print: `KXUSMICHCSP` · `KXDECPIPREL`
`KXFRCPIPREL` `KXITCPIPREL` `KXEZCPIYOYF` · `KXDEGDPQOQF` `KXDEGDPYOYF` `KXESGDPQOQF`
`KXEZGDPYOYF` `KXEZGDPQOQF` `KXFRGDPQOQP` `KXFRGDPYOYP` `KXITGDPQOQA` `KXITGDPYOYA`.

Two premise assumptions are wrong on the facts:

1. **There are no S&P Global flash PMI series on Kalshi.** Sweeping all 4,106 series for "PMI"
   returns five: `KXISMPMI`, `KXISMSERVICES` (own templates), `KXUSISMSERV`, `KXCHNBSPMI`,
   `KXCAIVEYPMI`. ISM, China NBS and Ivey each publish **one** print. Half the hypothesised
   qualifying set is not listed at all.
2. The proposed title screen would have **missed** two live cases where Kalshi's title carries
   no label but the source's first print is officially an "Advance Report": `KXUSDURABLE`
   (Census Advance Durable Goods) and `KXUSRETAIL` (Census Advance Monthly Retail). Both settle
   on the advance — §3.

## 3. The universal diagnostic: settlement lag

A prelim→final gap has an unavoidable signature: if a market closes on the flash and waits for
a later print, `settlement_ts − close_time` must span days or weeks. Over **302 events / 197
series / 164 with a settlement timestamp**:

| lag | events | share |
|---|---|---|
| < 2 h | 103 | 62.8% |
| 2–24 h | 56 | 34.1% |
| 1–3 d | 3 | 1.8% |
| 3–10 d | 1 | 0.6% |
| > 10 d | 1 | 0.6% |
| negative | 0 | 0.0% |

**96.9% settle within 24 h of close.** All 5 events ≥24 h are *non*-preliminary series whose
reference source publishes on a lag — `KX30YUSTW`, `KXFISHMEAL`, `KXAMSAVO`, `KXJETFUEL`,
`KXPRIMESPEND`. None is a prelim→final case.

**In the 13 qualifying series, across 19 settled release-events, the maximum settlement lag is
7.15 hours.**

| Market | close (UTC) | settled (UTC) | lag | `expiration_value` |
|---|---|---|---|---|
| `KXUSMICHCSP-26JUN12` | 06-12 13:59 | 06-12 14:36 | 0.63 h | **48.9** |
| `KXEZCPIYOYF-26MAY20` | 05-20 08:59 | 05-20 12:53 | 3.90 h | 3% |
| `KXFRCPIPREL-26MAY29` | 05-29 06:44 | 05-29 13:33 | 6.82 h | 2.4% |
| `KXITCPIPREL-26MAY29` | 05-29 08:59 | 05-29 13:33 | 4.57 h | 3.2% |
| `KXDECPIPREL-26MAY29` | 05-29 11:59 | 05-29 13:45 | 1.78 h | 2.6% |
| `KXUSDURABLE-26MAY28` | 05-28 12:29 | 05-28 13:32 | 1.06 h | 7.9% (Census **advance**) |
| `KXUSRETAIL-26JUL16` | 07-16 12:25 | 07-16 13:25 | 1.01 h | 0.2 (Census **advance**) |
| `KXUKGDPMOM-26JUN12` | 06-12 05:59 | 06-12 13:36 | 7.63 h | −0.1% (ONS first estimate) |

## 4. The natural experiment — and it lands against the hypothesis

June 2026 UMich is the ideal test: the revision was large **and** it crossed a listed strike.

- **Preliminary, Fri 12 Jun 2026 10:00 ET: 48.9.**
- **Final, Fri 26 Jun 2026: 49.5.** (UMich official ICS history, cached as
  `data/umich_ics_final.csv`; prelim corroborated by CollisionWeek 15 Jun, Advisor Perspectives
  26 Jun, Qz 26 Jun.)

`KXUSMICHCSP-26JUN12-T49.0` ("above 49.0") straddles the revision. Hypothesised rule → **Yes**
(49.5). Actual rule → **No** (48.9).

**Kalshi settled it `no`, `expiration_value = "48.9"`, at 14:36 UTC on 12 June — 37 minutes
after the preliminary release and two weeks before the final.**

Pre-close prices from 1-minute candlesticks over the 6 h before Last Trading Time, using only
the **last genuine two-sided quote** (bid>0, ask<1, spread ≤25¢) and the **last executed
print**; the post-halt candle (book collapses to 0.00/1.00) discarded:

| | value |
|---|---|
| last two-sided quote | **0.12 / 0.15** |
| last executed print | **0.12** |
| volume / open interest | 693 / 688 |

The hypothesised fade lifts the 15¢ ask and **loses**: −15.0¢/ct gross, **−15.9¢/ct net** of
the 0.89¢ taker fee. The 12–15¢ quote was not a mispricing of flip risk — it was a correct
12–15% probability that the *prelim* would print above 49.0, and the prelim printed 48.9.

Full strike ladder (all 15 strikes; every result matches the prelim):

| strike | result | vol | OI | last 2-sided | last print |
|---|---|---|---|---|---|
| 42 | yes | 16 | 11 | 0.92/0.99 | — |
| 43 | yes | 173 | 147 | 0.85/0.97 | 0.97 |
| 44 | yes | 1,770 | 1,527 | 0.77/0.91 | 0.91 |
| 45 | yes | 1,099 | 989 | 0.58/0.60 | 0.60 |
| 46 | yes | 474 | 276 | 0.46/0.52 | 0.52 |
| 47 | yes | 1,265 | 946 | 0.08/0.32 | 0.32 |
| 48 | yes | 1,001 | 930 | 0.19/0.24 | 0.24 |
| **49** | **no** | **693** | **688** | **0.12/0.15** | **0.12** |
| 50 | no | 35 | 20 | 0.01/0.17 | — |
| 51 | no | 58 | 35 | 0.02/0.10 | 0.02 |
| 52 | no | 35 | 25 | 0.02/0.08 | — |
| 53–56 | no | 5–75 | 5–75 | none (one-sided) | — |

**Three corroborating settlements**, May 2026 European CPI (all prelim = final):

| Series | Kalshi `expiration_value` | Prelim | Final | Source |
|---|---|---|---|---|
| `KXFRCPIPREL-26MAY29` | 2.4% | 2.4% (29 May) | 2.4% (12 Jun) | INSEE Informations rapides 136/140 |
| `KXITCPIPREL-26MAY29` | 3.2% | 3.2% (29 May) | 3.2% (16 Jun, "confirming the flash estimate") | Istat, *Consumer prices – May 2026* |
| `KXDECPIPREL-26MAY29` | 2.6% | 2.6% (29 May) | 2.6% (12 Jun) | Destatis PE26_182_611 / PE26_199_611 |

A second, independent weakness in the premise: **European flash HICP/CPI is confirmed unchanged
at the headline YoY level in the overwhelming majority of months.** Even if Kalshi settled on
the final, the qualifying set's revision variance sits almost entirely in the single UMich
series.

## 5. Flip rate, release-clustered

Clustering by release event — every strike inside one release shares the revision and is
perfectly correlated.

| Population | settled release-events | flips | rate | 95% upper bound (rule of three, event-clustered) |
|---|---|---|---|---|
| 13 qualifying prelim/flash series | 19 | **0** | **0.0%** | 15.8% |
| All 197 ECONSTAT series | 164 | **0** | **0.0%** | 1.8% |

The statistical bound is the weaker argument and is not load-bearing. The load-bearing result is
structural: a flip requires the settlement print to differ from the print the market closed on,
and here **the two are the same print by construction** — confirmed in the certified rules text
of all 3,156 markets and in 164/164 settlement timestamps.

**Gate:** ADVANCE required flip rate ≥5% on near-boundary strikes. Observed 0%, and 0 is not a
small-sample artefact. **Fails at step 3. KILL.**

## 6. Which bucket does the residue fall in?

- **Settlement-mechanic (3 for 3):** *nothing left.* There is no mechanical rule to exploit. The
  gap the hypothesis needed does not exist in the terms, in the rules text, or in one of 164
  settlements.
- **"We have better information" (0 for 6):** the only surviving trade is *forecast the
  revision* — buy the flash-priced strike you think the flash itself will print. That is pure
  forecasting against a market which, on the one observation where it mattered, priced the
  prelim correctly to within a tenth of an index point.

Per the edge law, an edge needs both a reason the price is wrong and a reason nobody fixed it.
**There is no reason the price is wrong. KILL.**

## 7. Liquidity and capacity (recorded for the file only)

Within the 13 qualifying series — 264 traded settled markets, 1-min candles, 6 h pre-close:

| metric | value |
|---|---|
| markets with a **genuine two-sided quote** (bid>0, ask<1, spread ≤25¢) | **102 / 264 = 38.6%** |
| markets with an executed print in the window | 136 / 264 = 51.5% |
| spread on the two-sided subset | median **6¢**, p75 10¢, max 25¢ |
| per-market lifetime volume | median 459, max 46,896, total 314,520 |
| total volume, 29 qualifying events | 332,081 contracts |

**Fewer than 4 in 10 of these markets ever showed a genuine two-sided quote in the final six
hours.** A study using bare asks or mids on one-sided books would have fabricated an edge on
~61% of its observations. That risk was real here, not theoretical.

**Family-wide, ECONSTAT is not thin:** 302 events, **11,311,013 contracts total**, median
6,213/event, max 2,058,877 (`KXECONSTATCPI-26JUN` alone did 674,394). R4's "$13–55 depth" is
true of the European satellites and badly understates the US headline series. *Positive residue
for any future econ hypothesis — the liquidity is there; this edge is not.*

## 8. Reusable facts (bank regardless of verdict)

1. **Settled ECONSTAT markets have `status: "finalized"`, not `"settled"`.** A filter on
   `status == "settled"` returns **zero** rows across all 197 series. The first pass hit this and
   reported "0 settled" for every series. Same family as the golf `status="closed"` trap, and
   equally silent.
2. **`expiration_value` is populated on finalized markets and carries the realised statistic
   verbatim** — `"48.9"`, `"172K"`, `"-0.1%"`, `"Exactly 0.5%"`. Any future econ study can read
   the settlement print straight off the market payload.
3. **`settlement_ts` uses a variable number of fractional-second digits.** Python 3.9's
   `fromisoformat` accepts only 3 or 6 and raises. The naive parse silently dropped **164 → 144**
   settled events (a 12% hole) with no error. Normaliser in `settle_lag_census.iso()`.
4. **`settlement_ts − close_time` is a cheap, general settlement-mechanic probe** — answers "did
   this market settle on the release it closed for?" in one field, any series, no external data.
   **Recommend it as the first-pass screen for every future settlement-quirk hypothesis.**
5. **Candlesticks reach far past the trade-history window.**
   `GET /series/{s}/markets/{t}/candlesticks?start_ts&end_ts&period_interval=1` returns
   per-minute `yes_bid`/`yes_ask`/`volume`/`open_interest` — this is how the June 12 book was
   reconstructed on July 26. **The post-close candle always shows bid 0.00 / ask 1.00** (halted
   book) and must be dropped or it fabricates a 100¢ spread.
6. **Fee-table gap (flagged, no code change made):** all 197 ECONSTAT series are `quadratic` →
   maker-free. `src/kalshi_fees.py::_SERIES_MAKER_FEE` has no ECONSTAT entry, so
   `series_maker_charges_fee()` falls through to conservative `True` and would over-charge a
   maker fee on any future econ pod. Harmless for gating (stricter), wrong for sizing.
7. **Listing-hygiene artefact, not an edge:** a few duplicate/mislisted events were force-closed
   off-schedule and settled ~30 min later on a stale value (`KXUSMICHCSP-26MAY22` closed 19 May
   22:50Z, settled 23:20Z, `expiration_value` 48.2 against a May final of 44.8;
   `KXFRCPIPREL-26JUN12` and `KXITCPIPREL-26JUN16` force-closed 29 May with the 29 May values).
   Volumes trivial (53–142 contracts). This is **mis-settlement risk pointing at the trader** —
   unpredictable and the wrong sign.

## 9. Do not re-test without new information

Re-open P-027 only if Kalshi lists a series whose `rules_primary` names an **unlabelled**
statistic while its `close_time` sits one minute before that statistic's **flash** release —
i.e. `settlement_ts − close_time` running into days. The §3 diagnostic detects that in one field.

## Appendix — reproduction

```bash
cd ~/Desktop/Betting\ Fund\ Project
python3 econstat_research/census_series.py       # 4,106 series -> 197 ECONSTAT
python3 -m econstat_research.settle_lag_census   # 302 events, settlement lag
python3 econstat_research/analyze_lag.py         # diagnostic table (§3)
python3 econstat_research/price_test.py          # two-sided quotes + prints (§7)
```

All pulls cached under `econstat_research/data/`. Scripts are cache-first and resume on restart;
throttled 0.6–1.0 s/call against the shared 2 req/s budget. **No 429s observed.** Read-only
endpoints only.

---

*Verification note: the orchestrating session independently queried Kalshi for the natural
experiment. `KXUSMICHCSP-26JUN12-T49.0` → `status=finalized, result=no, expiration_value='48.9',
close=2026-06-12T13:59:00Z, settlement_ts=2026-06-12T14:36:46.940636Z` — settled **37 minutes
after close** and two weeks before the 49.5 final. The adjacent `-T48.0` settles `yes` on the
same 48.9. Both the `status="finalized"` gotcha and the structural argument are confirmed.*
