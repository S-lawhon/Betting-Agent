# PROMPT — P-022 pre-flight and silent-failure alarm

**Run this FIRST and ALONE. The window opens 2026-07-29T15:30Z.**

## Why this exists

P-022 is the fund's only validated edge and has never emitted a quote. Three fixes have shipped; two were declared verified and failed live. The deployed state (2026-07-27 17:53 UTC) is the best it has ever been — detector `WAITING`, 351 markets, 0 unresolved — but **that is a state check, not a placement.** Nothing in the system has ever demonstrated the path from "window open" to "order placed".

**The failure mode is silence. Silence is also correct behaviour between tournaments.** An observer cannot distinguish them, which is why every previous failure cost days.

## Hard constraints

- **Do not change any P-022 parameter.** Band `(0.03, 0.12)`, offset `+0.02`, window `[12h, 24h]`, caps 0.5%/5%/15%, 13 series. Verify byte-identical before and after everything you do. Any change resets T to 0.
- **Do not "fix" a no-quote by widening anything.** If the dry-run shows no eligible market, that is a finding to report, not a bug to tune away.
- **Standing deploy exception, narrow:** you may deploy changes whose entire diff surface is observation — new log lines, a new alarm script, a crontab entry, a read-only checker. Anything that touches `discover()`, `_cycle_book()`, `_close_epoch()`, sizing, or the risk guard is **Sam's call and must not ship tonight.**

## Part A — resolver ground truth against tomorrow

For each of the three listed tournaments (ROC26, AIGWO26, POI26):

1. Pull `product_metadata.competition` / `competition_scope` from the live event payload.
2. Record what `GolfScheduleResolver` returns **right now**, and which of its three precedence paths produced it (this round's tee times +4.0h / R1 tee times + (n−1)d +1.0h / per-tour day offset).
3. Independently fetch tomorrow's actual tee times from the tour's own site (PGA Tour / R&A / DP World as applicable) and record the real first tee and expected round end.
4. Report the signed error. **The resolver is designed to fail early and did so on 72 of 72 settled events; an error that is late for any of the three is a stop-the-line finding** — report it and do not attempt a same-night fix.

Also report, per tournament, the exact UTC instant the `[12h, 24h]` window opens and closes under the resolver's current answer.

## Part B — dry-run the placement decision at simulated wall-clock

Do not wait for 15:30Z. Drive the pod's own decision path with an injected clock:

- Pick the earliest window-open instant from Part A. Run the pod's real `discover()` → eligibility → quote-construction path with `now` set to `window_open + 1 minute`, in **dry-run** (no order submission, paper or otherwise).
- Report, as a funnel with counts at every stage: markets discovered → resolved close → inside `[12h, 24h]` → inside band `(0.03, 0.12)` → passes size/depth screen → passes caps → **would place**.
- **The number that matters is the last one.** If it is zero, report which stage zeroed it, with the concrete market tickers and their prices at that stage.
- Use the pod's own code paths. **Do not reimplement the window arithmetic** — last night's P-001 lesson is that a hand-rolled reader produces a second, subtly different number for a quantity meant to be unambiguous.

Note the known calibration fact and do not treat it as a defect: the conservative bias puts the first quote at a true H of ~25.6h (tee times) or ~29.6h (day offset), i.e. **above the nominal 24h edge**. That is decision item 7 for Sam, not tonight's fix.

## Part C — the alarm (the actual deliverable)

Extend `scripts/p022_window_check.py` — **preserving its deliberate independence from the pod's window arithmetic** — so that at every `*/30` run it emits, and alarms on, the distinction the system currently cannot make:

| state | meaning | alarm |
|---|---|---|
| `NO_WINDOW` | no market has a resolved close within `[12h, 24h]` of now | no |
| `WINDOW_OPEN_NO_CANDIDATE` | window open, but nothing in band / size / caps | **no**, but log the funnel |
| `WINDOW_OPEN_CANDIDATE_NO_QUOTE` | window open, ≥1 market passes every screen, **and the pod has written no quote** | **YES — this is the failure that has cost five days** |
| `QUOTED` | ≥1 quote written since the window opened | no |
| `CLOSE_REF_PLACEHOLDER` | resolver degraded / no real close | **YES** (existing behaviour, keep) |

Requirements:

- Alarm surfaces through the existing `manager/alert.py` path so it reaches Sam, not just `status.jsonl`.
- **A checker failure is a failure, never a skip** — the same rule the fee fixture now follows.
- Crontab it `*/30` as `bettingbot` if it is not already; back up the previous crontab.
- Tests for each of the five states, including the one that matters most: window open + candidate present + no quote.

## Stop rule

**STOP at the report.** Do not change pod behaviour. Do not reset `t_start_utc` (that is Task 2).

## Deliverable

`research/REPORT_P022_PreFlight_2026-07-29.md` and, at the top of the run summary, a single sentence answering: **is P-022 armed to quote at 15:30Z, and what will tell us within 30 minutes if it is not?**
