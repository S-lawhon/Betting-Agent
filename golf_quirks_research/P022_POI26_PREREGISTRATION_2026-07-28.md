# P-022 — PRE-REGISTRATION: `KXCHAMPTOURR1LEAD-POI26` is excluded from T

**Written 2026-07-28, before the window opens at `2026-07-30T16:00Z`.**
Filed alongside `P022_WIDENING_PREDECLARATION.md`, and for the same reason:
a decision made after the result is known is not a decision.

## The registration

> **The Portugal Invitational R1 round-leader event
> (`KXCHAMPTOURR1LEAD-POI26`) is EXCLUDED from T — it contributes no
> observation, favourable or unfavourable — unless every QUOTE row it
> produces carries `close_source` other than `tour_day_offset`.**
>
> If the resolver upgrades to `tee_times` or `r1_tee_anchor` before the
> window opens, the exclusion does **not** apply and the tournament counts
> normally.

This is checkable from the log without reconstruction: `close_source` is
written on every QUOTE row (`src/round_leader_fade_maker.py`), and the
window detector records it per event in `status.jsonl` every 15 minutes.

## Why

`LAG_DAY_H["KXCHAMPTOUR"] = 12.0` — the constant that produces POI26's
close reference on the coarse path — is calibrated on **n = 1**, and that
single observation is a **US** event. POI26 is the first PGA TOUR Champions
event ever staged in Europe (The Els Club Vilamoura, Algarve, WEST = UTC+1),
so the one calibration point is not merely thin, it is in a different clock
frame. Champions events are also 54 holes with a 78-player field, so the
round is *shorter* than the 12.5 h median span the lags were fitted against —
which pushes the error in the dangerous direction (predicting the close
LATE puts quotes inside a live round, which §7 excludes anyway).

Confirmed by measurement on 2026-07-28, not assumed:

* **ESPN publishes nothing.** `/champions-tour/scoreboard?dates=20260731`
  returns event `401832063`, `STATUS_SCHEDULED`, **0 competitors** — so
  `tee_times` and `r1_tee_anchor` cannot run and the resolver falls to
  `tour_day_offset`.
* **No tee sheet exists anywhere.** The tournament's own site publishes the
  dates ("31st July to 2nd August, 2026") and a field of 78, and no times of
  play. Web search across the tour, the venue and the golf press returns the
  field, the purse and the venue — no first tee, no broadcast window.
* **Every other listed event HAS upgraded.** Live `close_source` census
  across all 958 open round-leader books today: `tee_times` 586,
  `r1_tee_anchor` 293, `tour_day_offset` **79** — and the 79 are exactly
  POI26's markets. The mitigation the 07-29 pre-flight was relying on has
  fired for AIGWO26 and ROC26 and **not** for the one event that needed it.

## What this costs, stated up front

One tournament out of a T = 24 gate, at a moment when the gate has
**zero** observations. Under the measured cadence of 3.16 golf event codes
per week that is roughly two days of gate progress.

**That is the correct price.** A tournament quoted off an uncalibrated
constant cannot be evidence either way: if it settles favourably, the
result is unusable because the quotes may have been placed inside the live
round; if it settles unfavourably, the same. Deciding this after the fact
is fitting the sample to the result — the thing §8.2 exists to forbid.

## What would reverse it

Any of these, observed **before** `2026-07-30T16:00Z`:

1. `close_source` reads `tee_times` or `r1_tee_anchor` for
   `KXCHAMPTOURR1LEAD-POI26` in `status.jsonl` or on the QUOTE rows;
2. a published R1 tee sheet that independently confirms the resolved close
   is EARLY (the resolver is validated one-sided early on 72 of 72 settled
   events; being early is the safe direction);
3. Sam overrides this registration explicitly, in writing, before the window.

## Not done, deliberately

**No code change.** The pod is not made to skip POI26, and `LAG_DAY_H` is
not touched. Suppressing the quotes would destroy the only calibration
observation this event can produce: letting it quote and then discarding it
from T yields a measured `lag_h` for `KXCHAMPTOUR` in a European frame,
which takes that constant from n = 1 to n = 2 — the exclusion is from the
**gate**, not from the **data**.
