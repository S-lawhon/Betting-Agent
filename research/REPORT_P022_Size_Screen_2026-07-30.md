# REPORT — P-022 top-of-book size screen (Task 4, 2026-07-30)

**Shipped in `297ce2b`. Live A/B on the open AIGWO26/ROC26 window: 20 of the in-band
books (62%) are refused as thin; quote count stays at 12 both ways because the
per-tournament collateral cap binds first — the screen's effect is to REDIRECT the cap's
capacity from phantom books (1–18 contracts behind the reference) to real ones.**

## What shipped

`src/round_leader_fade_maker.py`: new quotes are refused unless the top of the book the
reference price came from carries **`min_top_size` contracts (config, default 100)** —
the ask on a one-sided book, **both** sides on a two-sided one (a thick ask cannot vouch
for a 1-lot bid). Per the R5 house rule the screen is on SIZE, never spread:
`CONTROLH-2026-R` (152,862 at bid) and `KXRHOUSESEATS-27-230` (1 at bid) both passed a
"spread ≤ 2¢" test. Details:

- **Placement-gated only.** A resting quote rides through the round per the locked
  strategy; a book thinning after placement does not pull it (tested).
- Missing/unreadable quantities count as thin — absence of evidence of size is thinness.
- Refusals are logged once per episode (`type: REFUSED`, `reason: thin_book`) with the
  full book snapshot, and re-log if the book thickens and thins again.
- The window-check detector (`scripts/p022_window_check.py::screen_after_band`)
  duplicates the screen in the same position (after band, before caps), so it will not
  page `WINDOW_OPEN_CANDIDATE_NO_QUOTE` on books the pod correctly refuses. The existing
  engine/detector parity test caught this dependency and now pins it, with a thin-book
  case added.
- `config_multi_pod.yaml` `pods.P-022.quoting.min_top_size: 100`. This is a
  **tightening** of the quoted population — allowed at any time under the
  pre-registration rules; `min_top_size: 0` disables (tested).
- 7 new tests; full suite 1,947 passed / 2 skipped.

## Live funnel A/B (pod's own from_config → discover → cycle, injected clock 2026-07-30T03:00Z)

| | screen OFF | screen ON (100) |
|---|---|---|
| markets discovered | 958 | 958 |
| in window | 293 | 293 |
| quotes placed | 12 | 12 |
| thin-book refusals | — | 20 |

The 12-and-12 is the per-tournament collateral cap (§7, 5% of bankroll) binding before
the book runs out of candidates: with thin names refused, the greedy ticker-order
allocation flows the same capital to thicker names — e.g. `AIGWO26-HANGRE` sized 3 with
the screen off (cap remnant) and 5 with it on. Refused books included 14 one-sided asks
with 1–18 contracts and one **two-sided book with 116 at ask and 1 at bid**
(`AIGWO26-NELKOR`) — the exact CONTROLH/HOUSESEATS asymmetry, on a book a spread test
would have passed.

## Caveat

The 100-contract default is a judgment call sitting between the observed phantom books
(1–18) and the fixture-thick ones; it was not swept. It gates ~62% of the current
in-band population, which materially narrows where the pod can quote — if T accrues too
slowly under it, loosening is a §8-governed decision (written justification,
forward-only), not a tuning knob.

## Deploy note

Committed and pushed; **not yet deployed to 129.212.176.202** (`betting-live-maker` /
`betting-round-leader-fade` service). Deploy via `bash scripts/deploy.sh 129.212.176.202
restart` so the ARMED pod picks the screen up before its next window.
