# P-016 Recursive Review — Design & Authority Rules

**Status:** Tier 1 implemented 2026-07-20. Tiers 2–3 designed, not built.
**Governs:** `scripts/maker_diagnostics.py`, the scheduled review agent, and any
future adaptation layer on the P-016 Live Maker pilot.

---

## The problem this design exists to prevent

If the system tunes its parameters on the same fills it later uses to judge
itself, the pre-registered gate (≥500 fills, positive fee-adjusted +5m markout,
robust ex-best-day) becomes meaningless. This is not hypothetical: the tennis
reality-check study (Applied Economics 2018) showed 40 individually-profitable
betting rules whose edge vanished entirely once data-snooping was corrected.
An optimizer is a rule-search machine — point it at the pilot's own fills and it
will always find a flattering configuration.

**Core rule: measurement, adaptation, and judgment are separated. The fills
that drive adaptation are never the fills that decide the gate.**

---

## Three tiers of authority

### Tier 1 — Diagnostics (fully automatic, changes nothing)
`scripts/maker_diagnostics.py`. Measures and reports only:

1. Coverage / health — books anchored, quote uptime, widths, pulls
2. Fill rate by quote distance from mid
3. **Adverse selection by seconds-since-observed-state-change** — the pick-off
   signature of our StatsAPI feed lag. This is the single most important panel:
   markouts concentrated negative at low staleness means we are being picked off
   on stale quotes after game events.
4. Leverage calibration — markouts by |dWP/drun| bucket (is `sens_mult` right?)
5. Directional bias — markouts by side and price bucket (is the FLB skew right?)
6. Model calibration — fair-value buckets vs realized settlement
7. Guardrail cost — shadow/counterfactual fills vs real

Zero risk: nothing acts on the output.

### Tier 2 — Theory-driven recalibration (automatic, tightly bounded) — NOT YET BUILT
Only parameters with a *principled* online update rule, never a fitted one:

- **Quote width vs. measured adverse selection.** Standard market-making theory
  (Glosten–Milgrom): a maker widens where informed flow is concentrated. If the
  staleness panel shows negative markouts in the 0–30s bucket, the principled
  response is a post-event quote-suppression window or a widening multiplier
  there. This is the maker doing its job, not strategy search.
- **Model calibration correction.** If fair values show a persistent one-sided
  gap vs realized outcomes, correcting the intercept/slope is recalibration.

Bounds: every auto-adjustable parameter gets a hard `[min, max]` in config, and
every change is logged with before/after and a revert path.

### Tier 3 — Strategy search (proposal only, human-approved)
Anything that changes the strategy's identity: inning gate, FLB coefficient,
quote size, which markets, sizing regime, and — always — any move toward real
money. These are *proposed* automatically and committed manually.

---

## Asymmetric authority (the governing principle)

> The system may become **more conservative** on its own authority.
> It may become **more aggressive** only within pre-registered bounds,
> and never by its own strategy search.

Automatic without approval: widening quotes, suppressing post-event windows,
cutting size, pulling quotes, halting.
Requires approval: tightening spreads, extending the inning gate, raising size,
relaxing inventory caps, going live.

Rationale: the failure mode of excess conservatism is a small opportunity cost.
The failure mode of excess aggression is a real, compounding loss — and in an
adaptive system, one that can feed on itself.

---

## Champion / challenger, not sequential tuning

Because paper fills are *simulated from the public trade tape*, additional
configurations cost essentially nothing — the same prints can be evaluated
against several quote sets at once.

**Therefore: run challengers in parallel on the same games, never tune
sequentially over time.** Sequential tuning confounds parameter changes with the
game mix and market conditions of different weeks; parallel arms are like-for-like
on identical tape.

- **Champion** stays frozen and is the *only* arm the gate ever judges.
- **Challengers** accumulate independent fill records.
- Promotion requires a pre-registered margin, the challenger's own fill count,
  and multiple-comparison correction on the number of arms run.

This advantage vanishes once real money is involved. Exploit it now.

**Arm-count budget (critical):** arms are limited by fill scarcity, not
imagination. At ~50 fills/week, an 8-arm grid gives each arm ~6 fills — worthless,
and needs an 8-way correction on top. Rule: **few arms, each a coarse
theory-motivated contrast** ("wide vs narrow base", "with vs without post-event
suppression"), never a fine grid sweep. Size arm count to the measured fill rate.

---

## Cadence: fill-triggered, not calendar-triggered

Fills are the scarce resource; days are not. Full reviews fire when champion
fills cross a multiple of 150 (`--milestone`), with a daily calendar backstop so
a dead or broken pilot still reports. Implemented via the diagnostics state file
`data/trade_logs/.maker_diag_state.json`.

---

## Where the LLM review agent fits

Good at: reading the decomposition and generating hypotheses — *"adverse
selection is concentrated in fills within 90s of a scoring play; propose a
post-event suppression window."* That is pattern-noticing over a structured
diagnostic, which is a genuine strength.

Bad at: committing parameter changes unattended. That reintroduces rule search
with worse discipline, since each run is tempted to fiddle.

**Therefore the scheduled agent writes proposals to
`live_agent_research/proposals/` and never edits config.** Proposal → human
review → manual commit with logged before/after.

---

## Sequence

1. **Now:** Tier 1 diagnostics + shadow logging + scheduled proposal agent. ✅
2. **After ~1 week of fills:** read the actual fill rate. If fills are too sparse
   (< ~50/wk), the honest conclusion is that this pilot cannot support *any*
   optimization layer — fix fill rate (quote width, market selection) before
   building machinery on an empty tape.
3. **Then:** size the challenger arm count to the measured rate; build Tier 2
   within bounds; keep champion frozen for the gate.

---

## Open question deliberately left unresolved

Whether the +5m markout horizon is the right adaptation target at all. It is the
gate metric, but for a maker holding to settlement, realized settlement P&L is
the ground truth and markout is a proxy that assumes we could exit at mid. If
the two diverge materially once settlements accumulate, the gate metric — not
just the parameters — needs revisiting, and that is a human decision.

---

## Addendum: first-night findings (2026-07-20)

The review layer paid for itself within an hour of going live. Recorded here
because each is a reusable lesson, not just a fixed bug.

1. **Anchor contamination on restart.** Restarting mid-game re-anchored every
   live book to the *current* mid, which the model then double-counted by
   re-applying the score — 8–17pp systematic divergence and 484 one-way fills in
   45 minutes. Fixed with a persisted anchor cache plus `implied_pregame_prob()`
   (bisection inversion) for games first seen in progress.

2. **The first hypothesis was wrong, and measuring caught it.** The initial
   read was that model variance (σ) was too low. `scripts/calibrate_wp_model.py`
   refuted it: with *true* pregame anchors, extremeness ≈ 0 at σ=0.90 and the
   model is mildly **timid** at the current 1.05. σ was left unchanged — n=11 is
   nowhere near enough to justify moving a constant. This is the whole design
   thesis in miniature: **calibrate before adjusting, and let data kill your
   hypothesis.**

3. **Market-anchored quoting is structural, not cosmetic.** Quoting around pure
   model fair means that whenever the model diverges, *both* quotes land on one
   side of the book — you stop making markets and start accumulating directional
   risk at the touch. Quotes now center on `mid + clamp(fair − mid, ±max_tilt)`.

4. **Guard against your own model, not just the market.** `max_divergence`
   pulls quotes when |fair − mid| > 15pp: a gap that large means the model is
   the likely suspect.

5. **Compare −EV guards to the *effective* valuation.** The clamp-through-fair
   guard must test against the market-anchored center, not raw model fair —
   testing against model fair silently suppresses a whole side whenever model
   and market disagree, quietly recreating the one-sided quoting of (3).
