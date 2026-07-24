# P-025 — Music-Chart Mid-Week Edge — Phase 1 Report

**Series:** KXTOPSONG (Billboard Hot 100 #1), KXTOPALBUM (Billboard 200 #1)
**Date:** 2026-07-24 · **Author:** research (Claude) · **Mode:** paper/settled-data only, nothing placed
**Verdict: 🔴 KILL** (tradeable-from-free-data thesis refuted). Forward log left running as a cheap prospective re-check.

---

## TL;DR

The thesis needs three things to line up in the *same* market: (1) the outcome is
knowable from **free public data** days before close, (2) Kalshi's price **lags** that
knowledge, and (3) the lagging price carries **tradeable size**. Across 20 settled
chart-weeks (10 song + 10 album, May 23 – Jul 25 2026) these conditions are **mutually
exclusive across the two series**:

- **Albums** — the free signal (HITS album midweek) nails the Billboard 200 #1 **9/9
  weeks (100%)**, but Kalshi has *already* priced the winner to **≈$1.00 by Tuesday**.
  Only **1 of 10** weeks offered a triggerable gap (+5.6¢/ct on ~400 contracts → **$22**
  that week). No lag, no capacity.
- **Songs** — Kalshi genuinely lags (real 7–36¢ gaps, real volume on transition weeks),
  but the **free reproducible signal (kworb US-Spotify) is only 50–60% accurate** at
  identifying the Billboard Hot 100 #1, because the Hot 100 is multi-metric
  (radio + sales + streams) and Spotify-daily misses two of three. Worse, kworb is wrong
  on **exactly the high-gap weeks** (e.g. Jun 13: kworb led "Choosin' Texas" at 3% margin;
  the actual #1 "hate that i made you love me" was trading **0.64**). **Trading the free
  signal loses ≈3¢/ct.**

The only signal that bridges the song gap is **Talk of the Charts'** proprietary
multi-metric call — which is (a) not reproducible "free public data anyone reads",
(b) a single un-auditable account, and (c) **not re-gradeable from timestamped originals
in this harness** (calls live in X posts we cannot fetch). Per the survivorship guard we
did **not** launder ToTC's 52/52 self-audit into a verified number.

All three gate conditions fail. Details below.

---

## 1. Contract rules — verbatim (RANKLISTBILLBOARDCHARTS, read directly)

Extracted from `contract_terms/RANKLISTBILLBOARDCHARTS.pdf` (cached) and cross-checked
against each market's live `rules_primary`. The operative clauses:

**Underlying / post-close revisions ignored:**
> "The Underlying for this Contract is the official published ranking of \<candidate\> on
> \<list\> during \<time period\>. **Revisions to the Underlying made after Expiration
> will not be accounted for in determining the Expiration Value.**"

**Source — primary publisher only:**
> "The ranking must be from the primary publisher's official release, not secondary
> reporting … If \<list\> has multiple categories or subcategories, only the specific
> category named in the Contract title applies."

**Ties / dead-heat split (same mechanism as P-022):**
> "Tied rankings shall be resolved using the publisher's official tie-breaking methodology;
> if none exists, all tied entities share the highest rank among them. **If multiple
> candidates are tied for a given rank, then their Contracts will resolve to $1/(the
> number of candidates tied), rounded down.**"

*(Billboard publishes an official tiebreaker for chart ranks, so a split #1 is very rare;
none occurred in the 20-week sample — every winner settled `result=yes`, sv=$1.0000, no
`scalar`.)*

**Timing:** `Expiration time … 10:00 AM ET`; `Last Trading Date … the same as the
Expiration Date`; `Settlement Date … no later than the day after the Expiration Date`;
`Settlement Value … $1.00`; `Minimum Tick … $0.01`; `Position Accountability Level …
$25,000 per strike, per Member`.

**Observed live timing** (validated against `close_time`): for a chart dated Saturday
`chart_sat`, the Kalshi book opens ~17 days prior and **closes Sunday `chart_sat-6`
23:59 ET** (e.g. KXTOPSONG-26JUL25 closed 2026-07-19 23:59 ET). Billboard's tracking
week is **Fri `chart_sat-15` → Thu `chart_sat-9`**; the chart is announced **Tue
`chart_sat-4`**. So the book is open for ~2 days *after the tracking week ends and
before the announcement* — the window the thesis targets. Fees: series `fee_type =
quadratic` → **maker fee $0**, taker `0.07·P·(1−P)` ≈ 0.6¢ at 90¢.

---

## 2. Universe & method

- **Sample:** all settled events, enumerated live — **10 KXTOPSONG + 10 KXTOPALBUM**
  weeks (2026-05-23 … 2026-07-25). The memo's "20–30 weeks" was optimistic; the series
  launched ~May 2026. One album week (KXTOPALBUM-26JUL25) settled **all-No** — the
  Billboard 200 #1 was an *unlisted* album — and is excluded from winner-based stats.
- **Decision times** (per week, ET): **T-mid** = Wed of tracking week (`chart_sat-10`,
  20:00, HITS midweek is out); **T-close** = Fri after the tracking week
  (`chart_sat-8`, 12:00, full-week streaming known); **T-late** = Sat (`chart_sat-7`,
  18:00, book still open, outcome ~deterministic).
- **Free signal, reconstructed from timestamped/archived sources only** (survivorship
  guard; all 20/20 weeks reconstructed, 0 excluded for missing reference):
  - **Albums →** HITS Daily Double *Midweek 20* album building chart, dated-archive URLs
    `/charts/midweek-20/<Thursday>` (server-rendered rows in the Next.js RSC payload;
    ranks by "Activity" = Billboard-200-style consumption units).
  - **Songs →** kworb.net US-Spotify daily via the **Wayback Machine** (WebFetch is
    blocked from archive.org; `curl` reaches it), ranked by the **7-day** stream column.
  - **ToTC →** cited as the source's own claim only; **not** re-graded (X posts
    unfetchable here).
- **Fill realism:** entry = `yes_ask.close_dollars` at the decision hour; fillable size =
  that hour's `volume_fp` (never more); taker fee `0.07·P·(1−P)`; PnL/ct =
  `settlement_value(signal_leader) − entry − fee`. We trade the **signal leader** (ex-ante
  knowable), not the ex-post winner. Stats clustered by **chart-week**; bootstrap
  resamples weeks (n=5000).
- **Cache (no re-hitting Kalshi):** `data/events.json`, `data/candles/<ticker>.json`
  (374 strikes, hourly), `data/reference.json`, `data/reference_raw/` (HITS/kworb HTML),
  `data/trades_sim.json`.

---

## 3. Re-graded signal accuracy (my count, not ToTC's)

Graded as "did the free-data leader match the Billboard #1?" (Billboard result = Kalshi
settled winner).

| Series | Decision | All weeks | Wide-margin (≥20%) |
|---|---|---|---|
| **KXTOPSONG** (kworb Spotify) | T-mid | **5/10 = 50%** | 2/4 = 50% |
| **KXTOPSONG** | T-close | **6/10 = 60%** | 3/4 = 75% |
| **KXTOPALBUM** (HITS Activity) | T-mid | **9/9 = 100%** | 8/8 = 100% |
| **KXTOPALBUM** | T-close | **9/9 = 100%** | 8/8 = 100% |

**Gate needs ≥95% at T-close on wide-margin weeks.** Albums clear it; **songs fail badly
(75%)** — and songs are the only series with capacity. The song miss is structural:
Spotify-daily ignores radio + sales, so radio/country-driven #1s ("I Knew It, I Knew You",
"Janice STFU", "hate that i made you love me") sit at Spotify #2–3 behind the streaming
favourite "Choosin' Texas". kworb is a **weaker signal than ToTC by construction**, and
ToTC is not free-reproducible/gradeable.

---

## 4. Gap / PnL / capacity — honest fill model (trade the free-data leader)

Triggered = `(1.0 − ask) > fee + 0.03`. Per-contract cents, week-clustered CI.

| Series | Decision | n | hit% | ¢/ct | med hr-vol | cap $/wk | EV $/wk | 95% CI |
|---|---|---|---|---|---|---|---|---|
| KXTOPSONG | T-mid | 6 | 17% | **−3.5** | 0 | 6 | 0 | [−16.8, +8.1] |
| KXTOPSONG | T-close | 5 | 20% | **−2.9** | 3 | 20 | −21 | [−10.0, +2.4] |
| KXTOPSONG | T-late | 4 | 0% | **−3.2** | 1 | 1 | −1 | [−7.4, −1.1] |
| KXTOPALBUM | T-mid | 1 | 100% | +5.6 | 401 | 377 | 22 | (single week) |
| KXTOPALBUM | T-close | — | — | *no triggered trades (all priced ≈$1.00)* | | | | |

Trading the free signal is **net-negative on songs** at every decision time (you buy the
Spotify favourite on gap weeks and it settles to $0), and albums produce **one** tradeable
week in ten. **33 of the decision-hours had zero traded volume** — the "visible 7–15¢
gaps" in the settled-list `last_price` largely evaporate under the volume-cap fill model.

**Oracle ceiling** (upper bound: buy the *eventual winner*, i.e. a perfect signal, honest
fills):

| Series | Decision | tradeable weeks | ¢/ct | med hr-vol | cap $/wk | EV $/wk |
|---|---|---|---|---|---|---|
| KXTOPSONG | T-mid | 3/10 | +24.0 | 37 | 25 | 9 |
| KXTOPSONG | T-close | **2/10** | **+8.9** | 2057 | 942 | 118 |
| KXTOPALBUM | T-mid | 1/10 | +5.6 | 401 | 377 | 22 |

So **even with perfect foresight** the song market only offers a tradeable gap on ~2 weeks
in 10 (Jun 13, Jun 06), ~+9¢/ct, ~$118/wk on those weeks (~$24/wk amortized over all
weeks) — **below the $500/wk capacity gate before you even account for signal error.** The
market *does* lag; the problem is you cannot know which way from free data.

---

## 5. Contested-week split (KXTOPSONG @ T-close, triggered)

| Bucket | n | hit% | mean ¢/ct | CI |
|---|---|---|---|---|
| Wide-margin ≥20% | 1 | 0% | −1.1 | (single) |
| Contested <20% | 4 | 25% | −3.3 | [−12.2, +3.2] |

The thesis said "restrict to wide-margin weeks — that's where the edge is." In the free-data
song signal there is **no wide-margin edge**: the wide-margin kworb weeks are wide because
the *streaming* favourite is dominant, but that favourite is often **not** the Billboard #1.
The restriction that was supposed to isolate the edge instead isolates the streaming bias.

---

## 6. Forward-log seed & pick-over

`forward_log.py` (read-only, cron-able Tue/Fri) ran once (2026-07-24). Current open week
(26AUG01): HITS album leader **DAUGHTER FROM HELL** (margin 32%), kworb song leader
**Choosin' Texas** (margin 22%). For **both**, the Kalshi favourite is already **bid 0.99
with no resting ask** — you cannot even buy the discount; you would have to post a bid and
wait. That is the "corner already watched / picked over" signal the memo flagged as a KILL
condition on its own, observed live.

---

## 7. Verdict — KILL

Against the pre-registered gate:

| Gate condition | Threshold | Result | Pass? |
|---|---|---|---|
| Signal accuracy, T-close, wide-margin | ≥95% | Songs 75% / Albums 100% | ❌ (songs) |
| Net edge, week-clustered CI excl. zero | ≥3¢/ct | Songs −2.9¢/ct (CI spans 0) | ❌ |
| Capacity at honest volume caps | ≥$500/wk | ≤$22/wk (albums) / negative (songs) | ❌ |

**Both KILL triggers are met:** the visible gaps disappear under the volume-cap fill model
(albums), and the reproducible signal re-grades far below the self-audit (songs, 50–60% vs
ToTC's claimed 100%). The edge described — *read free public data, fade stan retail* — does
not survive because the free reproducible signal (Spotify) does not determine the Billboard
Hot 100, and where a strong free signal exists (albums) the market is not slow and has no
size.

**What is NOT refuted (and why the forward log stays up):** a **ToTC-based** version —
trading the song market off ToTC's multi-metric call rather than raw Spotify — was *not*
tested, because ToTC is not re-gradeable from originals in this harness. If someone logs
ToTC's timestamped calls prospectively for ~6 weeks alongside the Kalshi ask (the forward
log now captures the Kalshi side and the free-signal side), and ToTC's calls prove both
accurate *and* ahead of a lagging, size-carrying Kalshi ask, this could be revisited. On
current evidence that is a low-probability world: the live 26AUG01 week already shows the
favourites bid to 0.99 with no ask (fully picked over). **Recommend: do not build a pod.**

---

## 8. Files

- `pull_charts.py` → `data/events.json`, `data/candles/` (Kalshi, cached)
- `pull_reference.py` → `data/reference.json`, `data/reference_raw/` (HITS+kworb, cached)
- `backtest_charts.py` → `data/trades_sim.json` + the tables above
- `forward_log.py` → `data/forward_log.jsonl` (read-only prospective collector)
- `contract_terms/RANKLISTBILLBOARDCHARTS.pdf` (rules source)
- `p025_params.json` (params + gate results, machine-readable)

No pod, config, service, or live order was created. Nothing placed.
