# P-018 — gate #1 redesigned, PRE-REGISTERED

**Written 2026-07-28, BEFORE any new effect number was computed.**
Committed before the harness that implements it was run — check the git
history: this file lands in its own commit, ahead of the results.

---

## 0. Why the old gate #1 cannot discriminate — and the reason is worse than "the buckets are empty"

The spec's gate #1 is *"does edge rise with surprise?"*, answered by
contrasting high-surprise buckets against low ones. The committed report:

| `abs(wp_jump)` | fills | net ¢/ct |
|---|---:|---:|
| [0.00, 0.02) | **0** | — |
| [0.02, 0.06) | **0** | — |
| [0.06, 0.12) | 3,514 | +8.46 |
| [0.12, 1.01) | 2,162 | +10.13 |

Both low buckets are empty because `select_regime` only returns `FADE` when
`ev.surprise >= params.surprise_hi` (= 0.06). The gate was specified against a
population the strategy cannot generate.

**But populating those buckets would not fix it, and this is the part no
prior document states.** The fade quote is constructed in
`src/inplay_surprise.py::_fade_spec` as

```
px = mid + fade_edge_frac × (fair − mid)          # fade_edge_frac = 0.5
```

so the **model's own expected edge per contract, before any fee, is exactly**

```
E[pnl/ct | model correct] = |fair − px| = (1 − fade_edge_frac) × |fair − mid|
                          = 0.5 × |fair − mid|
```

And at event time, with `fair_before ≈ mid_before`,

```
underreaction = wp_jump − mkt_jump = (fair_now − mid_now) − (fair_before − mid_before)
              ≈ fair_now − mid_now
```

**Therefore the model-predicted edge is ≈ `0.5 × |underreaction|`, and
`|underreaction|` grows with surprise.** The surprise-bucket table is
*obliged* to slope upward whenever the win-probability model is even roughly
right — **whether or not the market overreacts to surprise at all.**

> **A monotone surprise-bucket table is arithmetic, not evidence.** Gate #1 as
> specified could not have returned KILL even with the low buckets populated.
> That is the finding, and it is why this redesign replaces the test rather
> than repairing its sample.

The mechanism claim P-018 actually makes is *the market **over**reacts to
surprise, so fading it earns **more** than the model's own disagreement would
predict*. The redesigned gate tests that claim.

---

## 1. What is under test, stated as a falsifiable proposition

> **H1 (P-018's reason to exist):** conditional on quoting the same distance
> from mid toward the same model fair value, fills taken **shortly after a
> high-surprise event** earn materially more than fills taken **at other
> times in the same markets**.

If H1 is false, the +9.09 ¢ headline is not a surprise effect. It is
"`src/mlb_win_prob` disagrees with the market and the model is right", which
is a *model-calibration* claim — one that P-016 could have made, that no gate
in this spec tests, and that does not justify a new pod.

## 2. The three tests, and exactly what each can kill

### Gate 1R-A — the PLACEBO. This is the primary kill.

**Construction.** In the same markets, on the same ticks, rest an
**identical** quote — same `_fade_spec` arithmetic, same 0.5 edge fraction,
same 240 s window, same linear size taper, same pessimistic
strictly-through fill rule, same series-aware maker fee, same settlement —
at **pseudo-events**: ticks that are

* **not** within `fade_window_s` of any real state change, and
* carry `|fair − mid| ≥ min_underreaction` (0.015), so the quote is placed at
  a comparable distance and the two arms are matched on the mechanical driver.

Everything differs from the fade arm in exactly one respect: **no surprising
event just happened.**

**Pre-registered decision:**

| result | verdict |
|---|---|
| placebo net ¢/ct ≥ **50%** of fade net ¢/ct **and** the two game-clustered 95% CIs **overlap** | **KILL.** Surprise contributes nothing identifiable; P-018 has no reason to exist over P-016. |
| the **paired difference** (fade − placebo), game-clustered, has a 95% CI **excluding 0** in the positive direction | **PASS gate #1.** |
| anything else (including placebo negative but difference CI spanning 0) | **NO DECISION** — gate #1 unresolved, pod stays inert. |

**This gate can return KILL and the KILL is cheap: it needs no new data.**

### Gate 1R-B — mechanical decomposition. Cannot kill; reframes.

Per fill, compute the model's own predicted edge `|fair − px|` at the moment
of quoting, fee-net, and report `realized − predicted` game-clustered.

* `realized ≈ predicted` → the headline is the model's belief, restated. The
  binding gate becomes **model calibration**, which nothing in this spec
  tests. Say so in the report in those words.
* `realized > predicted`, CI excluding 0 → positive evidence of overreaction
  beyond the model. This is H1's positive form.
* `realized < predicted`, CI excluding 0 → adverse selection is eating the
  model's edge, as it did to P-016 and P-017A.

**Explicitly not a kill criterion.** It is a decomposition, and dressing a
decomposition as a gate is how a rationalisation gets a number attached to it.

### Gate 1R-C — dose-response with quote distance HELD FIXED.

Stratify fills into terciles of predicted edge `|fair − px|`. **Within each
stratum**, bucket by surprise and test the slope.

| result | verdict |
|---|---|
| slope flat or negative in **every** stratum | **KILL.** The old surprise-bucket table was measuring quote distance, exactly as §0 predicts. |
| slope positive with CI excluding 0 in ≥ 2 of 3 strata | supports H1 |
| otherwise | NO DECISION |

## 3. Options considered and rejected, with reasons

**Widen `surprise_hi` in the backtest only, to populate the low buckets.**
Rejected as the *primary* gate — §0 shows the resulting table slopes upward by
arithmetic, so it cannot kill. **It is still run as a descriptive by-product**,
because "are there even events down there?" is worth knowing and is
outcome-independent. It is not a gate.

**Finer buckets across the range that exists.** Rejected alone, same reason —
it is the same confounded contrast at higher resolution. It survives *inside*
1R-C, where quote distance is controlled.

## 4. Multiplicity, declared up front

Three tests, two of which can KILL. That raises the false-KILL rate above 5%.
**Accepted deliberately**: this is a kill gate on an inert pod, the asymmetry
favours killing, and the alternative — picking the single most favourable test
after seeing three — is the failure this whole document exists to prevent.
**No test may be dropped after the fact.** All three are reported whatever
they say.

## 5. What would make this gate itself invalid

Stated now so it cannot be argued later:

* **If fewer than 30 independent game clusters carry placebo fills**, 1R-A is
  under-powered and returns NO DECISION rather than PASS. It may still return
  KILL, since KILL requires the arms to *agree*, which small samples make
  harder rather than easier.
* **If pseudo-events cannot be constructed** at comparable `|fair − mid|` —
  i.e. if non-event ticks essentially never show model/market disagreement of
  0.015 — then the placebo is not matched and 1R-A returns NO DECISION with
  that stated as the reason. The count is reported either way.
* Both arms are replayed by the **same** code path in the **same** run. A
  placebo computed by a second implementation would be measuring the
  implementation.

## 6. The prior this gate is being asked to overturn

**Maker/fade is 0 for 5 in this fund.** P-016 v1 was retired for adverse
selection; P-017M was underpowered; P-017A died last night at a **2.2% fill
rate** despite a correctly-measured edge. P-018 is a maker/fade pod with a
+9.09 ¢ headline — **larger than any of them** — produced by a replay whose
sample drops **33.9% of discovered markets, lowest-volume-first**, biased
toward exactly the liquid markets where a maker fills.

The prior is that this does not survive. The gate above is built to give it a
fair chance to.
