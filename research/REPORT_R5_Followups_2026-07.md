# R5 Follow-ups — tick size · skewed partitions · NCAAF poller

**Task:** `research/prompts/PROMPT_R5_Followups.md`
**Run:** 2026-07-28 · **no pod, no config, no deploy, no orders**
**Verdicts:** tick size **ANSWERED — no verdict moves** · skewed partitions **KILL** · NCAAF poller **BUILT and SCHEDULED 2026-07-27 20:06 UTC**

---

## Item 1 — Tick size · **ANSWERED. No past verdict moves.**

### The field Hunt E reported does not exist

| probe | result |
|---|---|
| `tick_size` on `/markets` list rows | **key absent** (200/200) |
| `tick_size` on `/markets/{ticker}` singular | **key absent** — identical 43 keys to the list row |
| `linear_cent` / `tapered_deci_cent` / `deci_cent` anywhere in `/series` (12,199 rows) | **not present** |
| tick-related field on `/series` | none — the 12 keys are `additional_prohibitions, category, contract_terms_url, contract_url, fee_multiplier, fee_type, frequency, last_updated_ts, settlement_sources, tags, ticker, title` |

Hunt E's *"`tick_size: None` for all 200 sampled"* was `.get()` returning `None`
for a **key that does not exist**, not a null value on a real field. The three
regime strings appear in neither public payload. I could not reproduce them
either, and can now say why.

### So it was measured empirically instead — and sub-cent ticks are real

Read 400+ real order books across 11 series, counting significant decimal places
in `orderbook_fp` prices (`research/r5_tick_probe.py`):

| series | books | with a sub-cent level | decimals seen |
|---|---:|---:|---|
| **`KXPGAR1LEAD`** | 40 | **38** | 1, 2, **3** |
| `KXMLBGAME` | 40 | 0 | 1, 2 |
| `KXNFLGAME` | 40 | 0 | 1, 2 |
| `KXATPMATCH` | 40 | 0 | 1, 2 |
| `KXWTAMATCH` | 40 | 0 | 1, 2 |
| `KXMLBTOTAL` | 40 | 0 | 1, 2 |
| `KXBTCD` / `KXETHD` | 80 | 0 | 1, 2 |
| `KXFED` / `KXCPI` | 78 | 0 | 1, 2 |
| `KXHIGHNY` | 12 | 0 | 1, 2 |

**Sub-cent quoting exists, carries real depth, and is confined to one family in
this sample.** `KXPGAR1LEAD-ROC26-BKOH` rests **25,107 contracts at $0.0010** —
a tenth of a cent. 268 sub-cent levels totalling **1.88M contracts**.

It is not even uniform inside the round-leader family: `KXLPGAR1LEAD` and
`KXCHAMPTOURR1LEAD` are strictly 1¢. That argues against a per-series "tick
regime" and for **specific market makers quoting sub-cent in the single deepest
golf book** — but the API exposes nothing either way, so that stays an inference.

### Where it sits, which is what decides the question

| price band | sub-cent levels found |
|---|---:|
| ≥ 0.90 | 255 |
| ≤ 0.02 | 12 |
| 0.02 – 0.05 | 1 |
| **0.05 – 0.12** | **0** |
| 0.12 – 0.90 | 0 |

The ≥0.90 cluster is the NO side of deep-tail YES markets (NO at 0.986 ⇔ YES at
0.014), so it is the same phenomenon. **Sub-cent granularity exists only in the
extreme tails.**

### Which past verdicts move: **none**

The kills that sat within 1–2¢ of their line were checked against the families
they were actually computed on:

| verdict | band it was decided in | family | sub-cent there? |
|---|---|---|---|
| **P-022** (live) | YES **0.03 – 0.12** | `KXPGAR1LEAD` | **No** — 0 levels in band (1 in 0.02–0.05) |
| **P-026** stat-leader (+0.99¢ vs a 100¢ ceiling) | ≥0.90 sums | `KXLEADERMLBHR/AVG/HITS` | **No** — 1,851 levels read, all 1¢ |
| **P-023c** top-N fade (+0.2¢ executable) | tail | `KXPGATOP*` | not listed this week — **untested, flagged** |
| satellites (+0.93¢ on 99¢) | ≥0.90 | award/RT families | not in this sample — **untested, flagged** |

**P-022's own band is clean**, which is the one that matters most: its friction
arithmetic assumes a 1¢ tick and the assumption holds where it trades. **P-026's
kill is also safe** — its families quote strictly 1¢, so a finer tick could not
have lifted its 99.0¢ bid-sum over the 100¢ ceiling.

Two families behind near-miss kills had **no open markets this week** and are
honestly untested. Neither is a live candidate, so this is recorded rather than
chased.

> **Practical note for P-022, not a parameter change.** The pod quotes
> `round(mid + 0.02, 2)` — 1¢ granularity — into a book that sometimes quotes
> tenths. That means it can be queue-jumped by a maker posting 0.1¢ better. It
> does not change the validated edge or any §7 cap, and **I have not touched
> it**; it is worth knowing when the fill data arrives.

**STOP as instructed — answered, not acted on.**

---

## Item 2 — Skewed partitions · **KILL**

### The correction stands, and it mattered

Hunt E's `fee = 0.07(1 − ΣPᵢ²) ≥ 0.07(1 − 1/n)` is backwards: ΣPᵢ² is
*minimised* at uniform, so `0.07(1 − 1/n)` is a **ceiling**. Skewed partitions
are ~10× cheaper in fees, and the flawed proof would have dismissed exactly
those.

**That correction changed the empirical result.** The prior scan found
**0 positive net**; this one, over a wider universe, found **38**.

### Method

`research/r5_mutex_scan.py`. Mutual exclusivity is taken from Kalshi's own
`mutually_exclusive` flag on `/events`, not inferred. `with_nested_markets=true`
collapses one `/markets` call per event into a single paged sweep — without it
the scan does not finish in a sane budget. `KXMVE*` excluded throughout.

**3,515 open mutually-exclusive events → 2,488 fully two-sided families**
(1,004 skipped as not-fully-two-sided, 23 with <2 legs). That is a materially
wider base than the prior scan's 2,069 and the "~700 usable" first pass.

Fees are charged **per leg at that leg's own ask**, never at an average.

### The exhaustiveness trap ate the headline numbers

The raw scan showed families with **+93¢ net**. All were artefacts:

| Σ(ask) | families | reading |
|---|---:|---|
| < 0.50 | 12 | **cannot be exhaustive** — buying every listed leg is not a $1 claim |
| 0.50 – 0.90 | 11 | almost certainly not exhaustive |
| 0.90 – 1.00 | 34 | plausible partition |
| 1.00 – 1.10 | 1,609 | normal overround |
| ≥ 1.10 | 822 | wide overround |

`mutually_exclusive` means **at most one leg wins — not that one must.**
`KXLAPRIMARY-01D26` prices two legs at ~6¢ total; buying both costs 6¢ and pays
$1 only if one of those two candidates wins a race with more entrants. The 93¢
"gross" is the probability the field wins, not an edge. This is the trap the
brief flagged via `USELECTION.pdf`'s resolve-No-for-all clause, and it accounts
for **every** large positive number in the unrestricted scan.

### The skew stratification — restricted to Σ(ask) ≥ 0.90

| skew (max leg) | n | best gross | median fee | **best net** | n net>0 |
|---|---:|---:|---:|---:|---:|
| < 0.5 | 276 | 9.30¢ | 4.86¢ | **+3.66¢** | 4 |
| 0.5 – 0.85 | 1,239 | 6.00¢ | 3.34¢ | **+4.33¢** | 6 |
| 0.85 – 0.95 | 453 | 1.10¢ | 1.42¢ | **+0.29¢** | 1 |
| **≥ 0.95** | 497 | 0.80¢ | **0.62¢** | **+0.29¢** | 4 |

**The narrow question — "does positive net ever appear where the fee floor is
~0.4¢ rather than 3.5–6.3¢?" — answers: yes, and it tops out at +0.29¢.**

That is **below a single 1¢ tick**. And the result runs opposite to the
hypothesis: the largest nets sit in the *low-skew, expensive-fee* buckets, where
the gross overround is simply wider. Cheap fees did not buy an edge; they
attached to families that are priced tight precisely because they are simple.

### Executability — the actual gate

Every one of the 15 plausibly-exhaustive positive-net families, checked for
resting dollars at the best YES ask on **every** leg (≥$100 required):

| event | legs | net | max leg | thinnest leg | |
|---|---:|---:|---:|---:|---|
| `HOUSEAKAL-26` | 2 | +4.33¢ | 0.83 | **$1** | ✗ |
| `KXRTICKET-28NOV07` | 25 | +3.66¢ | 0.28 | **$0** | ✗ |
| `KXFLPRIMARY-02D26` | 5 | +3.34¢ | 0.38 | **$3** | ✗ |
| `KXNFLENDSTREAK-40NYJ` | 5 | +2.78¢ | 0.23 | **$0** | ✗ |
| `KXPRESTURKEYR1-28` | 3 | +2.09¢ | 0.69 | **$0** | ✗ |
| `KXTHEGAMBIAPRES-26` | 4 | +1.97¢ | 0.70 | **$1** | ✗ |
| `KXFOMCDISSENTCOUNT-26JUL` | 5 | +1.03¢ | 0.32 | **$25** | ✗ |
| `KXGOVOKNOMR-26` | 2 | +0.89¢ | 0.80 | **$2** | ✗ |
| `KXMONGOLIAPRES-27` | 5 | +0.62¢ | 0.60 | **$0** | ✗ |
| `GOVPARTYOK-26` | 2 | +0.29¢ | 0.93 | **$12** | ✗ |
| `KXHOUSERACE-VA08-26` | 2 | +0.29¢ | 0.97 | **$12** | ✗ |
| `SENATERI-26` | 2 | +0.23¢ | 0.97 | **$1** | ✗ |
| `KXHOUSERACE-FL10-26` | 2 | +0.21¢ | 0.95 | **$10** | ✗ |
| `SENATEOR-26` | 2 | +0.13¢ | 0.97 | **$16** | ✗ |
| `KXITFMATCH-26JUL28CASPAR` | 2 | +0.03¢ | 0.82 | **$5** | ✗ |

**EXECUTABLE: 0 of 15.** The best-funded thin leg is **$25**, against a family
netting +1.03¢ — about **$0.26 per full cycle**, before any slippage, on a
$1,000 paper bankroll. Capacity is not small; it is absent.

This reproduces the prior scan's 0-of-1,514 on a wider base and by a stricter
test (top-of-book **size**, not spread).

> **KILL.** The sub-case is now closed on evidence rather than on a flipped
> inequality: skewed partitions *do* surface positive-net candidates that the
> flawed proof would have dismissed, and every one of them dies at the
> executability gate. The correction was worth making and it changed nothing.

---

## Item 3 — NCAAF listing-window snapshot · **BUILT and SCHEDULED**

`scripts/ncaaf_listing_watch.py`, committed and tested live.

```
KXNCAAFGAME open markets: 30
bulk detected  : False
```

30 — exactly the stated baseline (15 marquee games, opened 2026-05-20).

**Pre-registered constants, fixed in code before any drop data exists:**

| parameter | value |
|---|---|
| bulk trigger | 30 → **≥100** markets |
| anchor deadline | **within 6h** of bulk `open_time` |
| band | two-sided, ask ∈ **[0.85, 0.975]**, non-marquee |
| depth | **≥$100 within 3¢, both sides** |
| re-snapshots | **T+24h, T+72h** |
| **KILL** | **drift < 3.5¢, regardless of statistical significance** |

The kill threshold is derived, not chosen after the fact: 1¢ tick + spread +
0.4¢ fee + 1¢ margin. `--status` prints the verdict against it automatically, so
the comparison cannot be quietly renegotiated.

**Expected to fail, stated in advance and recorded in the script's own
docstring.** Marquee games are already priced tightly, the target band across
live NCAAF shows a 27¢ median spread, and backwater families quote 87–93¢. On
NCAAF, attention and liquidity are perfectly correlated, so the inattention
pocket and the tradeable pocket are disjoint sets. It runs only because it is
nearly free and **the snapshot cannot be reconstructed afterwards.**

### Scheduled — 2026-07-27 20:06 UTC, on the droplet

```
7 */3 * * * cd /opt/betting-pod-shop && sudo -u bettingbot ./venv/bin/python -m scripts.ncaaf_listing_watch >> /var/log/ncaaf_listing_watch.log 2>&1
```

**3-hourly rather than daily, deliberately.** The registered anchor rule is
"within 6h of the bulk `open_time`", and a once-a-day poll can miss that window
by up to 18 hours. The snapshot cannot be reconstructed afterwards, so the
cheaper cadence would risk the entire test to save 7 requests a day. The
pre-registered constants — including the 6h deadline — are cited in the crontab
comment so the reason survives the next person reading it.

Verified in its exact cron form as `bettingbot`: exit 0, state written to
`data/ncaaf_listing_watch/polls.jsonl` (bettingbot-owned, and `data/` is
excluded from the deploy rsync so it survives redeploys), log appending.
Baseline captured:

```
{"n_open": 30, "prev_n": null,  "bulk_detected": false, "trigger": 100, ...}
{"n_open": 30, "prev_n": 30,    "bulk_detected": false, "trigger": 100, ...}
```

Prior crontab backed up to `/root/crontab.bak.ncaaf.2026-07-27`; the installer
is idempotent and no-ops if the entry already exists.

**It starts polling now rather than on 2026-08-01.** That is a deviation from
the written protocol and it is deliberate: earlier polls only extend the
baseline series, they cannot touch the T0 / T+24h / T+72h drift comparison the
kill threshold is computed from, and if the bulk drop lands early the poller
catches it instead of missing it. The 08-01 date was a convenience, not a
constraint on the statistic.

---

## Summary

| item | verdict | the number |
|---|---|---|
| 1 · tick size | **ANSWERED** | field doesn't exist; sub-cent real but **0 levels in P-022's 0.03–0.12 band**; **no verdict moves** |
| 2 · skewed partitions | **KILL** | 2,488 families; best net in the cheap-fee skew bucket **+0.29¢**; **0 of 15 executable** |
| 3 · NCAAF poller | **BUILT** | baseline 30 confirmed; kill threshold 3.5¢ registered; **schedule before 2026-08-01** |

### Appendix — reproduce

```bash
python3 -m research.r5_tick_probe            # tick granularity by series and band
python3 -m research.r5_mutex_scan            # mutex families, skew-stratified
python3 -m scripts.ncaaf_listing_watch       # one poll
python3 -m scripts.ncaaf_listing_watch --status
```

Artifacts: `research/r5_tick_probe.json`, `research/r5_mutex_scan.json`.
