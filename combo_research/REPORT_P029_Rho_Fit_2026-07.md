# REPORT — P-029 leg-correlation fit, and what it does to Gate 0

**Verdict up front: the fitted ρ SHRINKS the Gate 0 in-zone margin from +2.87¢ to +1.85¢ —
below the +2.0¢ CONTINUE line, inside the "extend one week" band.** The priors understated
correlation everywhere it was identifiable, exactly as the confounded single-day run hinted.
Not a STOP: the read is on n=26 traded in-zone rows against a gate that requires n≥500, and
the STOP line is +1.0¢. But the +3.17¢ historical edge now has to survive a correction that
measurement made *larger*, not smaller.

**Date:** 2026-07-29 · **Fitter:** `fit_correlation.py` @ `01ce818` · **Input:** the VPS
settled-combo archive, 8 days (2026-07-22 → 07-29), 7,647,318 rows · **Output:**
`combo_research/fitted_rho.json` (committed)

---

## 1. The fit

Tetrachoric ρ per relation block, pairs deduplicated to unique (leg_a, leg_b) sets,
bootstrap clustered by event day (4,000 draws). 104,353 candidate combos → **103,019 with
every leg settled** (1,334 dropped as partially settled — the decided-leg selection trap the
fitter exists to refuse); 6,281/6,356 legs (98.8%) resolved binary.

| block | pairs | days | P(both) obs | indep | ρ | 95% CI | prior | status |
|---|---|---|---|---|---|---|---|---|
| cross | 16,430 | 9 | 0.2662 | 0.2493 | **+0.107** | [+0.040, +0.144] | 0.00 | fitted |
| same_day | 88,151 | 7 | 0.2627 | 0.2442 | **+0.116** | [+0.099, +0.384] | 0.02 | fitted |
| same_game | 28,731 | 5 | 0.2760 | 0.2427 | **+0.208** | [+0.084, +0.221] | 0.18 | fitted |
| same_player | 431 | **2** | 0.2947 | 0.2429 | (+0.323) | — | 0.35 | **UNIDENTIFIED — keeps prior** |

Three observations:

- **Every identified block came in ABOVE its prior.** The big movers are `same_day`
  (0.02 → 0.116, ~6×) and `cross` (0.00 → 0.107). "Unrelated" legs retail combines are not
  unrelated: same-sport outcome rates share a slate-wide tilt (favourites-day vs dogs-day)
  even across days — remember the selection caveat below.
- **`same_player` is unfitted by rule**, not by pair count: 431 pairs cleared the 30-pair
  floor easily, but they sit on only 2 event days, so ρ is confounded with those slates'
  outcome rates. The prior 0.35 stands. Its computed-but-unidentified +0.323 sitting next to
  the 0.35 prior is weak comfort, nothing more.
- The confounded single-day numbers the run queue quoted (`same_day +0.355`,
  `same_game +0.688`) were indeed inflated 2–3× — the multi-day fit landed well below them,
  which is the confounding washing out, and is why the fitter refuses single-slate fits.

**Selection caveat (stated in the JSON itself):** this is correlation *among legs retail
chose to combine* — the right population for pricing retail combo flow, the wrong one for
anything else.

## 2. A bug the fit exposed: `load_rho` trusted unidentified numbers

`load_rho()` kept the prior only when `rho` was `null`. `same_player` ships a **computed but
`identified: false`** ρ — which would have been loaded silently. Fixed in `01ce818`:
unidentified blocks keep their prior too, pinned by tests against the committed
`fitted_rho.json`. A second latent bug in the same commit family: `shadow_public.py` built
the correlation matrix without passing the copula's ρ, so fitted values would have been
silently discarded in favour of `DEFAULT_RHO` (fixed in `187f093`).

## 3. What it does to Gate 0 (the decision-relevant number)

All 85 traded first-print rows re-priced offline from stored discovery-time leg marks
(`/tmp/margin_ab.py` output, 2026-07-29):

| basis | in-zone median (n=26) | all (n=85) | frac>0 in-zone |
|---|---|---|---|
| independence | +4.17¢ | +1.69¢ | 73% |
| copula, prior ρ | +2.87¢ | +0.91¢ | 73% |
| **copula, fitted ρ** | **+1.85¢** | **−0.93¢** | 69% |

- Correlation eats **~2.3¢ of the +4.17¢ independence margin** in-zone — more than half.
- Under fitted ρ the **whole-universe** median goes *negative* (−0.93¢): outside the target
  zone the winning quoter is on average selling *below* the correlation-adjusted joint.
  Consistent with the zone-targeting rules from the July report.
- By relation, in-zone medians hide a split: `same_game` −0.35¢, `same_day` −1.60¢ on the
  all-rows cut. The n is small everywhere; do not over-read the slices.

**Against the pre-registered thresholds (CONTINUE ≥ +2.0¢ @ n≥500 · STOP < +1.0¢):**
+1.85¢ at n=26 — not decidable, in the extend band, and the margin *fell* when the prior
became a measurement. Plainly: **if this number holds as n grows, Gate 0 does not clear on
2026-08-04 and the correct outcome is "extend one week", not CONTINUE.**

## 4. The backfill (Task 2)

The droplet's `combo` table predated the copula columns entirely — `copula_price` and
`leg_relation` did not exist. `backfill_copula.py` (`187f093` + retry fix `a68b791`):

- **Additive idempotent migration** (`ALTER TABLE ADD COLUMN`), never touches existing data.
- Re-prices from stored `leg_marks_json` — exactly equivalent to the live path, both use
  discovery-time mids. Rows with a value are untouched without `--force`.
- `model_price` and `in_zone` are deliberately NOT rewritten: re-deriving them retroactively
  would re-define the Gate 0 sample. The gate analysis reads `copula_price` directly.
- **Operational trap found live:** the shadow logger holds write transactions across
  rate-limited API calls — the DB stays locked for *minutes*, past any `busy_timeout`. The
  first run died on batch 2. Batches now wait out the logger (30-min patience per batch).
  Ran as transient unit `p029-backfill.service`; the logger was **never restarted**.

## 5. Population-definition note for the 2026-08-04 gate read

Rows logged before the copula landed have `in_zone` computed against the independence
model; once the logger restarts on the new code, new rows compute it against the fitted
copula. The stored `n_legs` + prices + raw marks make zone membership fully re-derivable
offline — **the gate read should recompute zone membership uniformly across the whole tape**
rather than trusting the stored flag across the boundary.

## 6. Files

- `combo_research/fitted_rho.json` — the fit (committed)
- `combo_research/fit_correlation.py` — the fitter (16 tests)
- `combo_research/backfill_copula.py` — migration + offline re-pricing
- `src/combo_copula.py` — pricer; `load_rho` now respects `identified: false`
- `tests/test_combo_copula.py` — incl. pins against the committed fitted file
