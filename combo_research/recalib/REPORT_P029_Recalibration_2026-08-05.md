# P-029 Copula Recalibration — 2026-08-05

**Trigger:** the Gate 0 settlement join showed the fitted-ρ copula overprices combos by ~2.5¢.
This session found and fixed **three mechanistic defects**, froze a recalibrated model, and
pre-registered a forward gate (`PREREG_P029_Gate0c_Forward.md`). No thresholds were changed on
the closed Gate 0; its STOP verdict on the old instrument stands as recorded.

**Data:** every shadow row with stored leg marks joined to archived combo settlements —
**1,543,618 settled combos** (2–4 legs subset used for fitting: 680,807), 2026-07-29 → 08-05,
8 seen-days; leg outcomes fetched for 31,377 unique legs (97.0% coverage).
Scripts: `extract_calib.py`, `fetch_legs.py`, `leg_calibration.py`, `leg_map.py`,
`refit_rho.py`, `patch_finalize.py` (in `combo_research/recalib/`).

## Defect 1 — leg mids are miscalibrated (favorite-longshot)

Unit = unique (leg, side), mark = median side-adjusted mid at combo creation, day-clustered by
the leg's event day (n=43,448, 12 days). Overall E[pays − mark] = **−2.34¢ ± 0.87**, with a
strong FLB shape: low-priced legs 4–8.5¢ rich, high-priced legs 3–4¢ cheap. Concentrated in
fast/thin series — MLB totals −5.6¢, spreads −5.2¢, ITF tennis −7.8¢, 15-min crypto −3.5 to
−3.9¢ — while MLB player props are nearly calibrated. Interpretation: retail builds combos at
momentum moments; the mid at that instant overstates the chased side (plus mid-of-wide-book bias
on thin series). One-sided books are NOT the cause (essentially all observations two-sided).

**Fix:** logistic map `logit(p_cal) = −0.1685 + 1.2685·logit(mid)`, fit on event days ≤ 08-01.
Out-of-sample (days > 08-01): overall gap −2.34¢ → **−0.10¢**; every decile within ~±2.6¢.

## Defect 2 — the pricer ignores leg SIDES in the correlation matrix

`src/combo_copula.py::price_combo` builds `correlation_matrix(tickers, rho)` from tickers only.
A YES/NO pair of positively correlated legs has **negatively** correlated payoffs; the model
applies +ρ, inflating every mixed-side joint. **Fix:** `corr_ij = s_i·s_j·ρ[relation]`,
s = +1 YES / −1 NO (congruence by diag(s), so PSD is preserved). **The repo patch to
`src/combo_copula.py` is still to be written** — blocked this session by a Mac Desktop
permission failure; the recalibrated pricer used for these results lives in
`recalib/refit_rho.py`.

## Defect 3 — the tetrachoric ρ fit pooled marginals across heterogeneous pairs

`fit_correlation.py` estimated ρ from block-wide pooled `p1/p2/p11`. With heterogeneous
marginals the pooled joint exceeds the product of pooled marginals even under independence, so
every block was biased UP. Refit per pure-relation class on calibrated marginals with the
sign-corrected one-factor copula (Gauss–Hermite, exact), moment-matched to settlements,
day-clustered bootstrap:

| class | old (07-29 fit) | recalibrated | 95% CI | n (pure) |
|---|---|---|---|---|
| cross | 0.107 | **0.000** | [0.00, 0.06] | 16,135 |
| same_day | 0.116 | **0.015** | [0.00, 0.20] | 362,419 |
| same_game | 0.208 | **0.379** | [0.32, 0.44] | 102,763 |
| same_player | 0.35 (prior) | **0.901** | [0.44, 0.95] | 2,059 |

Cross-slate correlation was an artifact; real correlation is concentrated within games
(and within a single player's nested props, ρ≈0.9 — hits/TB/HR on one batter overlap heavily).

## Validation — incremental attribution on held-out days (seen > 08-01)

E[settle − price], 89,851 test rows, day-clustered SE:

| model | bias |
|---|---|
| A stored copula (mids, old ρ, unsigned) | −3.14¢ ± 0.60 |
| B + side-sign fix | −1.84¢ ± 0.53 |
| C + leg calibration map | −0.76¢ ± 0.49 |
| D + refit ρ (train-day fit) | **−0.45¢ ± 0.52** |

Residual structure (D, test days): same_game **+2.16¢ ± 0.26** (model still underprices
same-game joints — single-ρ-per-class is too coarse there); price-bucket slope +1.5¢ (sub-5¢)
to −4.1¢ (60¢+) — residual combo-level FLB. In the gate zone the residuals are small
(+0.46 / −1.06¢). These are why the forward gate carries a settlement-based co-primary
condition rather than trusting the model statistic alone.

## What the recalibrated model says about the tape (NOT a gate verdict)

Margin of the winning print over frozen-model fair, traded rows, all 8 days, zone on the
recalibrated price: **median +2.27¢** (n=46,912; positive every day, +1.99…+2.88). The old
gate read −2.54¢ because its fair value was ~2.5¢ rich; the corrected instrument reads at the
CONTINUE line. The realized crosscheck on the same fills: seller edge (print − settle) =
**+1.40¢ ± 1.12** day-clustered — positive, thinner than the +3.17¢ historical tape, and not
yet significant on 7 days. The gap between +2.27 (median, vs model) and +1.40 (mean, realized)
reflects residual model bias concentrating on traded rows plus median/mean skew.

**Discipline:** this re-read is on the same tape the model was fit on, so it decides nothing.
Gate 0 (old instrument) remains STOP as recorded. Whether P-029 re-opens is decided ONLY by
the pre-registered forward window — `PREREG_P029_Gate0c_Forward.md`, model frozen at
sha256 `01411d86…` before any forward data existed.

## Caveats

- 8 summer days (MLB/WNBA/crypto), one regime; NFL preseason enters the tape this week.
- same_player CI is wide [0.44, 0.95]; small block (0.3% of rows).
- same_game +2.2¢ residual is the largest known model error; a structure-aware same_game
  model (e.g., prop-direction-aware) is the next refinement if Gate 0c extends.
- Capacity, fill rate, and RFQ competition (Phases 1–3 questions) are untouched by any of this.
