# P-016: Is +5m markout a valid proxy for settlement economics?

**Date:** 2026-07-20 (analysis run against data through 2026-07-21T04:01Z)
**Open question:** `markout-vs-settlement` (manager/registry.yaml, P-016 block)
**Status:** ANSWERED — and the answer is *"the sample cannot answer it yet, and the
trigger that fired was mis-specified."*
**Action taken:** none. Read-only analysis. Gate, pod, config, and registry unchanged.

---

## 1. The question

From `manager/registry.yaml`:

> Is +5m markout the right adaptation target at all? It is the gate metric, but for
> a maker holding to SETTLEMENT, realized settlement P&L is ground truth and markout
> is a proxy assuming we could exit at mid. If the two diverge materially once
> settlements accumulate, the GATE METRIC itself needs revisiting — that is
> explicitly a human decision.
>
> **trigger:** Surface once >=100 settled maker positions exist to compare.

The trigger fired at 107 settled positions. This document compares the two.

---

## 2. Headline finding

**The trigger counted the wrong thing.** 107 settled *positions* is not 107
independent observations of settlement. Settlement P&L for every fill in a game is a
deterministic function of a *single binary outcome* (home won / home lost). The 107
settled positions come from **5 games**. The effective sample size on the settlement
side is **5**, not 107.

Everything below follows from that. The point estimates are suggestive; none of them
are distinguishable from zero, and the fill-level and game-level estimates of the key
quantity **disagree in sign** — which is exactly the signature of unmodelled
clustering.

**Recommendation: keep +5m markout as the gate metric. Do not change it now.
Re-surface this question at >=40 distinct settled games, not >=100 positions.**

---

## 3. Data and methodology

### Sources (read-only; nothing written to the droplet)
- `/opt/betting-pod-shop/data/trade_logs/maker_fills.jsonl` (VPS 129.212.176.202),
  copied locally for analysis. 1,610 records.
- Record types written by `src/pods/live_maker_pod.py`:
  - `FILL` — the fill itself (414 records; 296 non-shadow)
  - `MARKOUT` — one per fill per horizon in `MARKOUT_HORIZONS = (60, 300, 900)`s,
    field `markout_per_contract = sign * (mid_at_horizon - fill_price)`, **gross of fees**
  - `SETTLE` — `pnl_usd = (sign * (result - fill_price) - maker_fee) * qty`,
    **already net of the maker fee**
- Fee model: `src/kalshi_fees.py`, general maker rate `0.0175 * P * (1-P)`
  (P-016 passes no `series_ticker`, so it takes the general path — confirmed in
  `_settle()`, which calls `fee_per_contract(fill.price, maker=True)`).

### Normalisation
Both legs are expressed as **cents per contract, net of the maker fee**, matching the
gate convention in `scripts/maker_report.py` (`markout_per_contract - fee`):

- `markout_pc = markout_per_contract - fee_per_contract(price, maker=True)`
- `settle_pc  = pnl_usd / qty`  (fee already deducted at settle time)

### Sample construction
Non-shadow fills that have **both** a `SETTLE` record and a +5m `MARKOUT`.
All 107 settled non-shadow positions have a +5m markout, so the paired sample is the
full settled set: **n = 107 fills across 5 games**.

### Exclusion window
`exclude_before: 2026-07-20T01:26:00Z` (first-night anchor contamination). Verified:
**0 fills in the current log predate it.** The contaminated data was archived to
`data/trade_logs/contaminated_2026-07-20/` and the live log starts clean at
2026-07-21T01:27:17Z. No contamination leaked into this analysis.

### Bootstrap
Per CLAUDE.md convention, **cluster bootstrap resampling whole games** (20,000 reps,
seed 20260720), not fills. A fill-level bootstrap would be wrong here and would
understate the CI by roughly the square root of the design effect.

---

## 4. Results

### 4.1 Per-game breakdown (the whole sample)

| game_pk | n fills | result | mean markout | mean settle | diff (S−M) | qty | $ markout | $ settle |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 824328 | 54 | home lost | −1.99c | +0.79c | +2.78c | 439.2 | −$19.94 | +$17.60 |
| 824087 | 25 | home won  | +3.17c | +5.79c | +2.62c | 158.0 | +$2.48  | +$8.13 |
| 822874 | 19 | home lost | −3.70c | −9.36c | −5.66c | 167.0 | −$1.57  | −$6.69 |
| 824167 |  5 | home won  | +2.95c | +3.65c | +0.70c | 100.0 | +$2.95  | +$3.65 |
| 823522 |  4 | home won  | −1.09c | −2.09c | −1.00c |  60.0 | −$0.36  | −$0.66 |
| **TOTAL** | **107** | | **−0.82c** | **+0.18c** | **+1.00c** | 924.2 | **−$16.44** | **+$22.03** |

One game (824328) supplies 54 of 107 fills — half the sample — and it is also the
single largest contributor to the markout/settlement divergence.

### 4.2 Central estimates (cents per contract, fee-adjusted)

| Quantity | Fill-level | Qty-weighted | Game-level |
|---|---:|---:|---:|
| mean +5m markout | −0.823c (sd 11.54) | −1.779c | +0.07c |
| mean settlement P&L | +0.182c (sd 35.68) | +2.384c | −0.27c |
| **mean difference (S − M)** | **+1.005c** (sd 32.70) | +4.163c | **−0.11c** |

**The fill-level and game-level estimates of the difference have opposite signs**
(+1.00c vs −0.11c). That is not a subtle discrepancy — it means the apparent
divergence is driven entirely by *which games happened to have many fills*, not by a
systematic gap between the two metrics.

### 4.3 Confidence intervals

Cluster bootstrap, resampling the 5 games:

| Quantity | Point | 95% CI | P(>0) |
|---|---:|---|---:|
| mean markout (fee-adj) | −0.823c | [−2.712, +2.872] | 0.264 |
| mean settlement P&L | +0.182c | [−6.947, +4.966] | 0.544 |
| **mean difference (S − M)** | **+1.005c** | **[−4.304, +2.675]** | 0.721 |
| Pearson r | +0.409 | [+0.237, +0.956] | — |
| OLS slope b | +1.264 | [+0.942, +1.792] | — |

Game-level paired t (n=5 games, 4 df), which does not rely on bootstrap validity at
tiny cluster counts: mean diff **−0.11c**, se 1.55c, **t = −0.07**, 95% CI
**[−4.41, +4.19]c**.

**Caveat on the bootstrap itself:** a cluster bootstrap with 5 clusters is not
trustworthy. The conventional floor is ~20–30 clusters; below that the resampling
distribution is dominated by which of 5 games are drawn and coverage is unreliable.
The bootstrap CI above is reported for completeness and is, if anything,
**too narrow**. The t-interval is the more honest of the two, and it is ±4.3c on a
question whose economically material scale is ~1–2c.

### 4.4 Association — is markout informative at all?

| Horizon | n | mean markout | mean settle | diff | Pearson r | sign agreement | OLS slope |
|---|---:|---:|---:|---:|---:|---:|---:|
| +1m  | 107 | −1.43c | +0.18c | +1.61c | +0.145 | 51.4% | +0.629 |
| **+5m**  | 107 | **−0.82c** | **+0.18c** | **+1.00c** | **+0.409** | **74.8%** | **+1.264** |
| +15m | 107 | −1.52c | +0.18c | +1.71c | +0.432 | 74.8% | +0.915 |

- Spearman at +5m: **+0.447**.
- Sign agreement 74.8% against a **50.0% chance baseline**
  (P(markout>0)=0.505, P(settle>0)=0.495).
- OLS `settle = a + b*markout`: **b = +1.264, CI [+0.942, +1.792]**; intercept
  +1.222c. An unbiased predictor implies b=1, a=0. **b=1 sits comfortably inside the
  CI**, so there is no evidence of scale bias; the intercept is the location question
  and is not separable from the mean-difference test above.
- **r² = 0.167** — the +5m markout explains ~17% of the variance in settlement P&L.

So markout is *directionally* informative (the sign agreement and rank correlation are
the most robust things in this dataset), but it is a low-r² predictor of any
individual fill's fate. That is expected and not a defect: settlement is a single
Bernoulli draw per game, most of whose variance is irreducible.

### 4.5 The decomposition that makes this interpretable

By construction, `settle_pc − markout_pc` is exactly **the P&L of holding the position
from the +5-minute mid to settlement**. So the question "do markout and settlement
diverge?" is *identically* the question **"are Kalshi live MLB mids a martingale?"**

- If mids are efficient, E[settle − markout] = 0 and markout is an **unbiased**
  (and lower-variance) estimator of settlement economics.
- If mids are biased, the two diverge by exactly the size of that bias.

Measured: **+1.005c per contract, sd 32.70c, CI spanning [−4.3, +2.7]c.** This is a
test of Kalshi live-mid efficiency with 5 games of data. It has no power.

This also clarifies *what each metric measures*, which is worth stating regardless of
sample size:

- **+5m markout = adverse selection cost.** Did the market move against me right after
  I got filled? This is the maker-skill / quote-placement question.
- **Settlement P&L = adverse selection + (mid vs. true probability) exposure.** It
  bundles the maker's execution quality with a directional bet on the market being
  mispriced.

P-016 is a *market-making* pilot, not a directional-alpha pilot. The gate question
("can this maker earn its spread without being picked off?") maps onto markout more
cleanly than onto settlement. If settlement is systematically better than markout,
that is evidence of a *separate* alpha source (mids are biased), which would deserve
its own pod, not a redefinition of the maker's gate.

---

## 5. Adversarial checks

### 5.1 Sample size — explicitly inadequate
**n=107 is too small to distinguish the hypotheses. Stated plainly.**

- 107 fills, **5 game clusters**. The settlement side has 5 independent draws.
- The estimated ICC of the difference is slightly negative (−0.035), which naively
  gives a design effect < 1 and an "effective n" of 370. **That number is an
  artifact and should be ignored** — with 5 clusters the ICC estimate is essentially
  unidentified, and a negative ICC here reflects that the maker takes both sides
  within a game (intra-game fills partly offset), not that clustering is absent. The
  binding constraint is the 5 independent *outcomes*, and no variance formula changes
  that.
- CI half-width on the difference: **±3.5c (bootstrap) / ±4.3c (game-level t)**.
  Minimum reliably detectable effect at ~80% power: **~5c**. The question at issue is
  whether the two metrics differ by ~1–2c. **We are roughly 3–5x short in resolution,
  which translates to needing on the order of 10–25x the number of games.**

### 5.2 Scope concentration — one night, one slate
The entire clean sample spans **2026-07-21T01:27Z to 04:01Z — a single 2.5-hour
window on one MLB slate.** Every conclusion is conditioned on one evening's market
conditions, one set of pitchers, and one anchor-cache state. There is no
across-night variation in this dataset at all.

Note also the outcome imbalance: 3 of 5 games were home wins. P-016 quotes home-YES.
With 5 coin flips this is unremarkable, but it means the settlement leg is not
outcome-balanced and its +0.18c mean carries a directional accident.

### 5.3 Selection effect — settled positions are a biased subset (and it flatters settlement)
This is a real effect and it cuts against a naive reading:

| Group | n | mean fee-adj +5m markout |
|---|---:|---:|
| Settled (the paired sample) | 107 | **−0.823c** |
| Has +5m markout, not yet settled | 156 | **−2.250c** |
| All real fills with a +5m markout | 263 | **−1.669c** |

Settled positions have markouts **1.4c better** than the still-open ones. Games that
finished early in the window are systematically the ones where the maker's quotes
looked better. **Therefore: comparing the settlement result (+0.18c, measured on the
good subset) against the headline gate number (−1.32c, measured on all fills) is
apples-to-oranges and manufactures roughly 1.4c of spurious divergence.** The
like-for-like comparison is the within-paired-sample one: **−0.82c vs +0.18c**, a gap
of 1.0c, which is what §4 reports and which is not significant.

Any future reading of this comparison must use the paired subset. Do not put the
all-fills markout next to the settled-only P&L.

### 5.4 Reconciliation with the reported gate figures
The task brief cites ~282 clean fills and −1.32c over 253 measured fills, from
`manager/state/status.json` collected at 03:55Z. This analysis reads the log at
04:01Z and sees **296 non-shadow fills, 263 with a +5m markout, mean −1.669c**
unweighted (−2.633c qty-weighted, dollar total −$62.81). The log is live and grew
between the two reads; the difference is consistent with 6 extra minutes of fills plus
an unweighted-vs-weighted convention difference. **This does not change any conclusion
— the gate is negative on both readings and the settled subset is not representative
of either.**

### 5.5 Shadow fills
296 of 414 fill records are non-shadow. All analysis above excludes shadow fills on
both legs. Shadow quotes are also logged separately in `data/shadow_maker/quotes.jsonl`
and were not used.

---

## 6. Answer to the question

**Is +5m markout a valid proxy for this strategy's economics?**

On the evidence available: **no material divergence has been demonstrated, and none
could have been at this sample size.** Specifically:

1. **No detectable bias.** Mean difference +1.005c per contract at the fill level,
   −0.11c at the game level (signs disagree), CI [−4.3, +2.7]c. The null of "markout
   is unbiased for settlement" is nowhere near rejected. The OLS slope of +1.26
   [0.94, 1.79] is consistent with an unbiased 1:1 relationship.
2. **Markout is genuinely informative about direction.** 74.8% sign agreement against
   a 50% baseline, Spearman +0.45, stable across the +5m and +15m horizons. The +1m
   horizon is visibly noisier (r=+0.145, 51.4% sign agreement) — **+5m is the better
   of the three choices**, and this is the one finding here that is reasonably robust.
3. **Markout is the variance-efficient estimator, by a lot.** Per-fill sd is 11.54c
   for markout vs 35.68c for settlement — a **3.09x ratio, ~9.6x in variance**.
   To detect a 1c effect at 80% power requires ~1,045 fills on markout versus ~9,981
   fills on settlement — and the settlement figure is a *per-fill* count that ignores
   clustering, so the true requirement is that many fills' worth of **independent
   games**. Settlement is not a practical gate metric at this pilot's scale. It would
   take months to years of fills to gate on realized P&L.

**Direction of the point estimate, stated with the appropriate hedge:** settlement is
running ~1c/contract *better* than fee-adjusted markout at the fill level. If that
were real, the current gate would be *conservative* — it would be killing a strategy
that is nearer break-even than the gate suggests. **But the game-level estimate flips
the sign, the CI is 8 cents wide, and §5.3 shows ~1.4c of the apparent gap is
selection. Do not act on the +1c.**

**What this implies for the 500-fill gate currently reading −1.32c:** nothing yet. The
settlement data neither rescues nor condemns the pod. It would be a specific error to
argue "settlement is positive (+$22), so keep the pod despite the negative markout" —
that +$22 rests on 5 coin flips, three of which landed home. Equally, the gate's −1.32c
is itself only ~half-sampled and is not yet a verdict. **Let the pre-registered gate
run to 500 fills on its pre-registered metric.** Changing a gate metric mid-flight
because an underpowered secondary metric looks friendlier is precisely the failure mode
pre-registration exists to prevent.

---

## 7. Recommendations (for human decision — nothing here has been actioned)

1. **Keep +5m markout as the P-016 gate metric.** It is unbiased as far as we can
   tell, directionally informative, and ~10x more sample-efficient than settlement.
   No change to `manager/registry.yaml`, the pod, or its config.

2. **Fix the trigger, not the metric.** The `markout-vs-settlement` trigger
   (">=100 settled maker positions") counts the wrong unit — settlement outcomes are
   one per *game*, not one per position. Suggest restating as
   **">=40 distinct settled games"**, which would give ~±1c resolution on the
   difference and make the cluster bootstrap valid. At the observed rate (5 games/night
   of clean running) that is roughly 8 clean slates.

3. **Re-run this analysis then, using the paired subset only.** The script logic is
   reproduced in §3; the essential discipline is (a) pair on fills that have *both*
   legs, (b) cluster by `game_pk`, (c) never compare all-fills markout to settled-only
   P&L (§5.3).

4. **Track the difference as a diagnostic, not a gate.** `mean(settle − markout)` is a
   direct running test of whether Kalshi live MLB mids are a martingale. If it
   stabilises materially away from zero at adequate n, that is a **separate alpha
   discovery** (mid mispricing) deserving its own pod and its own gate — not a reason
   to redefine the maker's success criterion.

5. **Do not use the settlement number in the go/no-go conversation at 500 fills**
   unless it has by then cleared ~40 games. Reporting both metrics side by side
   invites exactly the selection error in §5.3.

---

## 8. Reproduction

Analysis was run read-only against a local copy of
`root@129.212.176.202:/opt/betting-pod-shop/data/trade_logs/maker_fills.jsonl`.
Nothing was written to the droplet; no pod, config, gate, or registry file was
modified. Fee handling uses `src.kalshi_fees.fee_per_contract(price, maker=True)`
with no `series_ticker`, matching P-016's own settle path in
`src/pods/live_maker_pod.py::_settle`.
