# P-022 — Widening the Phase-2 Backtest

**Task:** `research/prompts/PROMPT_P022_Widen_Backtest.md`
**Pre-declaration:** `P022_WIDENING_PREDECLARATION.md` (committed before any pull; amended §9 before any effect number)
**Approved by Sam:** 2026-07-28 · **Run:** 2026-07-28
**No parameter, gate or pod changed. T remains 0.**

---

## 0. Verdict — **INCONCLUSIVE**, and the added block is negative

Both of my pre-declared conditions fired, which is a flaw in the
pre-declaration and I am not going to use the softer one to bury the harder one.

| declared condition | fired? | on what |
|---|---|---|
| **INCONCLUSIVE** — fewer than 6 clean tournaments added | **YES** | only **3** added tournaments produced a fill |
| **WEAKENED** — added block mean ≤ 0 | **YES** | added block **−12.33 ¢/ct** |
| WEAKENED — pooled below +2.1¢/ct | no | pooled **+2.57 ¢/ct** |
| STRENGTHENED | no | — |

**Primary verdict: INCONCLUSIVE.** Three tournaments, 12 fills and 228
contracts cannot decide anything, and the added block's CI is
**[−38.5, +7.0] ¢/ct** — it overlaps the original's CI completely and contains
zero comfortably.

**But the added block is negative, and that is stated here as prominently as a
positive result would have been**, exactly as §6 required. −12.33¢/ct is not
evidence the edge is gone; it is a small, noisy, adverse draw. It is also not
nothing.

**The honest one-line summary: the widening did not tighten the CI on the only
edge in the fund — it loosened it.** Pooled went from **+3.41¢ [+1.7, +5.1]** to
**+2.57¢ [+0.1, +4.5]**. The point estimate still clears the +2.1¢ line; the
lower bound moved from comfortably positive to almost touching zero.

---

## 1. The task's premise was wrong — the backward window is empty

The brief's motivating claim: *"Kalshi trade history reaches back to at least
2026-05-20, not the ~1 month we assumed — the sample can be widened backwards
for minutes of API budget."*

**Measured: Kalshi lists 78 settled round-leader events and ZERO of them close
before the study's own start.** The binding constraint was never the
*trade-history* horizon. It is the **listing** horizon: round-leader markets did
not exist before mid-May 2026. There is nothing behind the study to widen into.

Per-series earliest retrievable print (`measure_trade_horizon.py`), which is
where the "~1 month is wrong" claim came from and which is true but irrelevant:

| series | earliest print | | series | earliest print |
|---|---|---|---|---|
| KXPGAR1LEAD | 2026-05-22 | | KXDPWORLDTOURR2LEAD | 2026-05-23 |
| KXPGAR2LEAD | 2026-05-22 | | KXDPWORLDTOURR1LEAD | 2026-05-26 |
| KXPGAR3LEAD | 2026-05-23 | | KXDPWORLDTOURR3LEAD | 2026-05-30 |
| KXLIVR1LEAD | 2026-05-27 | | KXLPGAR1LEAD | 2026-06-04 |
| KXLIVR2LEAD | 2026-05-29 | | KXLPGAR2LEAD | 2026-06-05 |
| KXLIVR3LEAD | 2026-05-30 | | KXLPGAR3LEAD | 2026-06-05 |
| KXCHAMPTOURR1LEAD | 2026-07-01 | | | |

Those dates are **bounded by our own cache**, not by the API — and the cache
already starts at the first event that ever existed.

### A second finding, which is why the caches are committed

**Tick history is already rolling off *inside* the existing sample.** Probing
the oldest cached tournaments, several now return **no prints at all**:

```
KXPGAR1LEAD-THCCBN26            0/6 markets return prints
KXPGAR3LEAD-THCCBN26            0/6
KXDPWORLDTOURR1LEAD-AUAOPBKT26  0/6
KXLIVR3LEAD-LIGK26              0/6
```

**The committed cache is now the only copy of part of the Phase-2 sample.** If
it is ever lost, those tournaments cannot be re-pulled and the published cells
become unreproducible.

---

## 2. What was run instead (amendment §9, committed before any effect number)

| block | tournaments | postable markets | fills |
|---|---|---:|---:|
| **ORIGINAL** | 19 | 364 | 171 |
| **GAP** — inside the study's span, missed by the original pull | 4 → **0 usable** | **0** | 0 |
| **FORWARD** — settled after the cache ends | 4 → **3 with fills** | 40 | 12 |

**The GAP block contributed nothing.** `SOO26`, `DOWC26`, `MEILCFSG26` and
`USSOC26` are all in the settled universe, but **not one of their markets has an
H−12h anchor inside the (0.03, 0.12) band** — they are thin events (SOO26 total
volume 1,912 contracts across 154 markets) where no name was priced in the fade
band with a clean two-sided anchor. They were pulled, and they are reported as
dropped rather than quietly omitted.

So the real addition is **3 tournaments**, against a declared INCONCLUSIVE floor
of 6.

---

## 3. Results — old and new separable, as §5 required

Headline cell throughout: **H = 12h, offset +0.02**, frozen.

### Unfiltered (the standard the ORIGINAL study was run at)

| block | markets | posted | filled | contracts | net ¢/ct | 95% CI | tourn (+) |
|---|---:|---:|---:|---:|---:|---|---:|
| **ORIGINAL** | 364 | 364 | 171 | 4,061 | **+3.41** | [+1.7, +5.1] | 19 (16) |
| **GAP** | 0 | — | — | — | — | — | 0 |
| **FORWARD / ADDED** | 40 | 40 | 12 | 228 | **−12.33** | **[−38.5, +7.0]** | 3 (1) |
| **POOLED** | 404 | 404 | 183 | 4,289 | **+2.57** | [+0.1, +4.5] | 22 (17) |

### With anchors staler than 6h dropped (the rule declared in §4)

| block | markets | filled | contracts | net ¢/ct | 95% CI | tourn (+) |
|---|---:|---:|---:|---:|---|---:|
| **ORIGINAL** | 272 | 160 | 3,795 | **+4.12** | [+2.1, +6.2] | 18 (16) |
| **FORWARD / ADDED** | 35 | 12 | 228 | **−12.33** | [−38.5, +7.0] | 3 (1) |
| **POOLED** | 307 | 172 | 4,023 | **+3.19** | [+0.3, +5.4] | 21 (17) |

97 of 404 markets dropped as stale. The added block is unchanged by the filter —
its 5 stale markets produced no fills.

---

## 4. Anchor contemporaneity — and a problem with the original sample

Staleness = `anchor_lag_h − 12h`: how much older than the intended H−12h mark
the last usable candle actually is.

| block | n | median | p90 | max | stale > 6h |
|---|---:|---:|---:|---:|---:|
| **ORIGINAL** | 364 | 0.98h | 40.13h | 249.17h | **92 / 364 (25%)** |
| **FORWARD** | 40 | 0.58h | 6.21h | 34.24h | 5 / 40 (13%) |

> **The added block's anchors are FRESHER than the original study's.** Whatever
> is wrong with the added block, it is not staleness — which removes the most
> obvious benign explanation for its negative sign.

> **A quarter of the ORIGINAL sample is staler than the threshold I declared for
> the new one.** The original study applied no contemporaneity filter at all.
> That is why every block above is reported **both ways**: filtering only the
> new block would have compared two different standards and flattered the old
> one. Filtering both *raises* the original to +4.12¢ and the pooled to +3.19¢ —
> so the filter is not what is driving the weakening.

---

## 5. Was the harness changed? **No — and `--validate` now fails anyway**

`--validate` reads the entire cache, so adding 40 markets necessarily breaks a
check that asserts `universe == 364`:

```
FAIL  universe: 404 cached markets (published 364)
```

**That is a cache-size artifact, not a harness change**, and the distinction is
provable. Restricted to the original 364 markets, the untouched harness
reproduces the published cell exactly:

| | this run (ORIGINAL block) | published |
|---|---|---|
| posted / filled | 364 / 171 | 364 / 171 |
| contracts | 4,060.6 | 4,061 |
| net ¢/ct | **+3.41** | **+3.4** |
| 95% CI | [+1.65, +5.13] | [+1.7, +5.1] |
| tournaments (positive) | 19 (16) | 19 (16) |
| leave-one-out | all positive, min +3.04¢ | drop USO26 → +0.030 |

`backtest_fade_fills.py` was **not edited** in this run.

> **This is a flaw in my own pre-declaration.** §7 said *"`--validate` must still
> reproduce the original published cells afterwards. If it does not, the harness
> changed and the run is void."* That test cannot survive its own task: widening
> the cache necessarily breaks a whole-cache validator, so the condition as
> written would void every successful widening. `published_universe_364.json`
> now pins the exact 364 tickers so the published universe can always be
> restored and the guarantee re-established.

---

## 6. What this means for the gate — reported, not acted on

**T = 14 stays exactly as it is.** The forward gate is untouched, T is still 0,
and nothing here is forward evidence — the live pod has never quoted, so none of
these tournaments could be confused with gate progress.

The power calculation behind T = 14 was sized against **+3.4¢/ct**. The pooled
estimate is now **+2.57¢ [+0.1, +4.5]**. If the true effect is nearer +2.6¢ than
+3.4¢, **T = 14 is underpowered for it** — the sample size needed scales roughly
as the inverse square of the effect, so ~14 → ~24 tournaments for the same
power.

**I am not re-deriving the threshold.** §1 of the pre-declaration forbids it and
the reasoning holds: a wider backtest changes the prior, not the gate. This is
flagged for Sam exactly as §1 said it would be.

## 7. The impulse I was told to report rather than act on

§1: *"If the wider data makes me want to re-optimise the band, the offset or the
window, stop and report the impulse instead of acting on it."*

It did, twice, and I acted on neither:

1. **The added block's 3 tournaments are all LPGA/Champions-heavy
   (`ISPHWSO26`, `ISHSO26`, `KIN26`) plus `3MO26`.** The temptation is to check
   whether the edge is PGA-only. That is a subgroup search on a 3-tournament
   adverse draw and it is exactly how a fitted result gets manufactured.
2. **The >6h staleness filter raises the original to +4.12¢.** The temptation is
   to adopt the filtered number as the headline. It is a filter I chose, applied
   after seeing that it helps — reported in both forms above, adopted in
   neither.

---

## 8. Data handling (§7)

* `golf_quirks_research/archive/candles_widened_20260728.tar.gz` (11 MB)
* `golf_quirks_research/archive/leader_trades_widened_20260728.tar.gz` (1.4 MB)
* `golf_quirks_research/published_universe_364.json` — the exact published universe

**`pull_candles.py` had to be written because the original was never
committed** — 11,349 candle records existed with nothing in the repo that
generated them, the same 2026-07-25 loss `CLAUDE.md` warns about. Without it
`pull_trades.py --mode leader` reported "0 to pull" for eight genuinely missing
tournaments, purely because they had no candles.

> **A bug in that puller, found and fixed mid-run.** A local DNS outage caused
> 28 fetch failures, and the first version wrote `"candles": []` for each —
> indistinguishable from a genuinely candle-less market, and permanently
> skipped on resume because the file existed. A transient network blip would
> have silently truncated the sample. `fetch_candles` now returns `None` on
> transport failure and the caller writes **nothing**, so a resume re-fetches.
> All 28 poisoned records were identified from the log, deleted, and re-pulled —
> **all 28 returned real candles**, confirming they were network failures and
> not empty markets.

---

## Appendix — reproduce

```bash
python3 -m golf_quirks_research.measure_trade_horizon    # per-series history horizon
python3 -m golf_quirks_research.widen_probe             # is anything older listed?
python3 -m golf_quirks_research.pull_candles --events <codes>
python3 golf_quirks_research/pull_trades.py --mode leader
python3 -m golf_quirks_research.widen_analysis          # the tables above
```

Artifacts: `widen_probe.json`, `widen_candidates.json`, `widen_results.json`,
`trade_horizon.json`, `published_universe_364.json`.
