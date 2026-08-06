# PREREG — P-029 Gate 0c (forward re-read under the recalibrated model)

**Registered:** 2026-08-05, BEFORE any window data exists. · **Author:** Claude session with Sam
**Approved by Sam:** 2026-08-05, in chat ("Blessing the pre-registration"), before the window
opened. Phase 1 onward still requires a separate explicit go from Sam per the test plan.
**Model:** `recalibrated_model.json`, sha256 `01411d863de04075a38f02b40b7e0c4a7e21463a96b42d8194e8ccd8325956af`
— FROZEN. Any model change voids this registration and requires a new one.

## Why a new gate is legitimate after a STOP

Gate 0 measured the margin against a correlation-adjusted fair value that was mechanistically
wrong three ways (side-sign bug, miscalibrated leg marginals, aggregation-biased ρ — see
`REPORT_P029_Recalibration_2026-08-05.md`). The corrected instrument reads the SAME tape at
+2.27¢ median where the old one read −2.54¢. Discipline: the in-window re-read decides nothing
(the model was fit on that tape). This registration puts the corrected instrument on data that
does not yet exist. The original Gate 0 verdict on the original instrument stands.

## Window and sample

- Combos first seen **2026-08-06T00:00Z → 2026-08-19T23:59Z** (14 days), from the live shadow
  tape on 143.198.162.120. Read no earlier than **2026-08-23** (resolve lag + settle + archive).
- Prices are recomputed OFFLINE from stored leg marks under the frozen model
  (never the stored `copula_price` — same principle as `gate0_zone_recompute.py`).
- Zone: 2–4 legs AND frozen-model price ∈ [0.10, 0.35], membership from the frozen model.
- Traded = first print exists (resolver logic unchanged).

## Decision rule — BOTH must pass for CONTINUE (into Phase 1, still $0)

1. **Margin condition (original Gate 0 statistic, original thresholds):**
   median(first_print − frozen_model_fair) over traded in-zone combos **≥ +2.0¢**, n ≥ 500.
   STOP if < +1.0¢.
2. **Realized condition (money statistic, immune to residual model bias):**
   mean(first_print − settlement) over the same rows **≥ +1.0¢** AND its day-clustered 95% CI
   excludes 0. STOP if the point estimate ≤ 0.

Both-pass → CONTINUE. Either-STOP → STOP (final; a further re-open requires new *mechanistic*
evidence, not a re-run). Otherwise (mixed/insufficient) → extend exactly ONE further week
(seen through 08-26, read ≥ 08-30), then decide with no extension beyond that.

Requiring both conditions is strictly TIGHTER than registered Gate 0 (which had only #1);
permitted under the tighten-only rule. Clustering: #2 clusters by seen-day; #1 reported with
event-cluster and day-cluster CIs, decided on the more conservative (day).

## Also recorded, not part of the decision

- Per-day medians/means, per-class residuals, and E[settle − model] on ALL settled rows in the
  window (model health check — if |bias| > 1.0¢ overall, flag for re-fit BEFORE any Phase 1).
- NFL-preseason-leg combos reported separately (regime preview, no decision weight).

## Season gate (separate, unchanged intent)

NFL/NBA regular season is a different regime and gets its OWN registration before that data is
read. The model may be re-fit for it ONLY on data preceding the season window, then frozen
again. Nothing in this document authorizes reading season data against today's thresholds.

## Operational prerequisites

- `p029-shadow.service` must stay healthy through 08-19: raise `MemoryMax` 512M → 768M
  (it has been pinned at the cap with ~220M swap since 07-31). Command for Sam:
  `sed -i 's/MemoryMax=512M/MemoryMax=768M/' /etc/systemd/system/p029-shadow.service && systemctl daemon-reload && systemctl restart p029-shadow.service`
- `p029-archive.timer` keeps running (settlement source for condition #2).
- In-zone resolver backlog stays at 0 (check `shadow.log` "due N in-zone").
