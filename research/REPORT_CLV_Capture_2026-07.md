# P-001 CLV Capture Diagnostic — why `close_fair()` returned nothing for 10 of 14

**Date:** 2026-07-26 · **Prompt:** `research/prompts/PROMPT_P001_CLV_Capture_Diagnostic.md` (Task 4)
**Verdict: FIXED (capture) / KILL (the existing 650-row corpus as gate evidence)**
Paper only. No orders. **No deploy.** Live `data/` untouched — recovered rows went to a new file.

## Headline

Three things are true at once, and only the first was being asked about.

1. **CLV capture is not broken.** Corpus-wide, **650 of 671** settled P-001 MLB bets have a
   CLV row: **96.9%**. Not 29%. The "10 of 14" was a *same-day* miss that self-heals; the
   residual after five months is 21 bets. Quota and HTTP are not involved at all — **4.87M of
   ~5M credits remain** and the full replay produced **zero `API_ERROR`**.

2. **The 200-row gate is not unreachable. It is already met — and that is the danger.**
   `clv_log.jsonl` holds **650 rows** (3.25× the gate) reading **+1.39¢/ct net-maker,
   day-clustered CI [+0.39, +2.49]** — almost exactly the +1.4pp target the gate was written
   against. Read literally today, **P-001 passes.**

3. **It should not pass, because 86% of those trades were selected using a different game's
   information.** P-001's Kalshi↔Odds-API matcher broke fuzzy-score ties by list order. Every
   game of an MLB series carries the same two team names and therefore the same fuzzy score,
   so the pod routinely computed an edge from **Wednesday's** game and placed the bet on
   **Thursday's** Kalshi market.

| cohort | n | day-clusters | mean CLV net-maker | day-clustered CI |
|---|---|---|---|---|
| all captured rows | 650 | 57 | **+1.39¢** | [+0.39, +2.49] |
| ticker **==** priced game | 105 | 26 | **+7.65¢** | [+3.46, +12.05] |
| ticker **!=** priced game | 545 | 54 | **+0.19¢** | [−0.26, +0.66] |

The +1.4pp headline is a blend of a small correctly-matched cohort and a large cohort of
effectively random trades. **The metric definition is fine and correctly computed; the
population it is computed over is not the population the hypothesis is about.**

## ⚠️ Where the bug is visible — a correction worth carrying forward

**The defect cannot be detected from `clv_log.jsonl` alone.** Independent verification found
that the CLV log's own `commence` field agrees with the ticker-encoded start time in **650 of
650 rows (100%)**. Settlement re-derives `commence` by looking up the game near the *ticker's*
time, so the CLV row is anchored to the correct game and looks perfectly healthy in isolation.

The mismatch only appears when you join to the **trade log's `game_time`** — the Odds API event
that actually produced the edge at placement time. There: **628 of 734 P-001 MLB PLACED rows
(85.6%)** sit 17–43 h from their own ticker's game. Examples:

```
KXMLBGAME-26JUL231310MINCLE-MIN   priced 2026-07-22T22:41Z   Δ 18.5h
KXMLBGAME-26JUL251805NYYPHI-NYY   priced 2026-07-24T22:46Z   Δ 23.3h
KXMLBGAME-26JUL261335CHCPIT-PIT   priced 2026-07-24T22:41Z   Δ 42.9h
```

So the correct statement is not "the CLV rows measure the wrong market" — it is **"the CLV
rows correctly measure trades that were selected by a broken process."** The consequence for
the gate is identical, but anyone re-running this must join the two logs; auditing `clv_log`
on its own will show a clean bill of health.

The corroborating signature *is* visible in `clv_log`: **543 of 650 rows (83.5%) have
`settled_at` earlier than their own ticker's game start** — settlement resolved on the
wrong (earlier) game.

## 1. The five silent `None` paths, now distinguishable

`src/clv_close.py` returns `(result, reason)` over 8 stable tags.
`scripts/diagnose_clv_capture.py` replays candidate selection over the active log **plus all
nine archives** (1,360,929 rows), caching every Odds API response to disk.

**Failure-reason histogram — all 1,494 settled P-001 bets, Apr 3 → Jul 26:**

| n | share | reason |
|---|---|---|
| 823 | 55.1% | `EXCLUDED_NON_MLB` — never reached `close_fair()` |
| 650 | 43.5% | `ALREADY_CAPTURED` |
| 10 | 0.7% | `TIME_GT_3H` |
| 10 | 0.7% | `NO_TEAM_MATCH` |
| 1 | 0.1% | `NO_PINNACLE` |
| **0** | **0%** | **`API_ERROR` — quota/HTTP is not a cause** |
| 0 | 0% | `SNAPSHOT_EMPTY`, `PINN_NO_H2H`, `PINN_NAME_MISMATCH` |

**By sport:** KXMLBGAME 671 (650 captured); KXWTAMATCH 209, KXATPSETWINNER 162, KXATPMATCH
146, KXNHLGAME 126, KXNBAGAME 123, KXNBA2HWINNER 55, KXNCAAMBGAME 2 — all excluded. Non-MLB
bets are **not** dying at the name-match path; they are dropped by `if 'MLB' in market_ticker`
before pricing. So the gate *is* denominated against MLB volume alone (45% of settled flow) —
a real re-scope input, but not a bug and not the capture problem.

**By month:** residual misses are front-loaded (Apr 16, May 2, Jul 3). The Jul-21
archive-lookback repair plus the Jul-22 backfill already drained the backlog. No accumulating
deficit.

### Why "10 of 14" on 2026-07-21

Same-day capture is genuinely poor, then self-heals. Live from `/var/log/clv_settlement.log`:

```
Jul 23  28 new settled bets, 21 games, wrote  7
Jul 24  21 new settled bets, 15 games, wrote  0
Jul 25  35 new settled bets, 29 games, wrote  8
Jul 26  34 new settled bets, 25 games, wrote 13
```

…against a corpus-wide 96.9%. The job re-reads 10 archives and dedupes by fingerprint, so a
miss is retried every morning until it lands.

The cause is **the same bug as above**. Because the pod trades a market whose ticker points
~21 h into the future (median `settled_at − commence` = **−20.9 h** over 650 captured rows),
`close_fair()` asks for a snapshot that does not exist yet. Verified today: a request for
`T+6h` is clamped to the newest snapshot, so the right game is absent or >3 h away and the row
defers. **The low same-day capture rate that opened this investigation is a downstream symptom
of the matcher bug, not an independent problem.**

## 2. The real finding: the pod prices one game and trades another

`Legacy/Kalshi Arb Project/src/matcher.py` selected with `if score > best_score` — first
maximal event wins. Series games produce identical labels, so the winner was whichever the
Odds API returned first. The guard that should have caught it is
`time_window_minutes: 43200` — **30 days** (`Legacy/Kalshi Arb Project/config.yaml:106`) — fed
`close_time`, the market's *closing* stamp, not its start.

**Worked example, live log 2026-07-26 02:05 UTC:**

| field | value |
|---|---|
| `market_ticker` | `KXMLBGAME-26JUL261215CLETB-TB` → Sat Jul 26, 12:15 ET |
| `game_time` (priced event) | `2026-07-25T22:11:00Z` → Fri Jul 25, 18:11 ET |

Both markets exist on Kalshi, confirmed live today (`-26JUL241910CLETB`, `-26JUL251810CLETB`,
`-26JUL261215CLETB`). The pod computed its edge from Friday's sharp line and bought Saturday's
contract.

**Scale (671 settled MLB bets):** same game (≤3 h) 105 (15.7%) · +1 day 374 · +2 days 174 ·
≥3 days / −1 day 18. (Independently recomputed over 734 PLACED rows: 106 same, 628 mismatched
= 85.6%.)

The same signature appears outside MLB (NBA 63% of rows date-mismatched, NHL 39%, tennis
39–74%), but tennis `commence_time` is a synthetic scan-time stamp and NBA/NHL/NFL tickers
encode date only — **only the MLB number is established.** Spot checks surfaced a second,
rarer defect there: outright wrong-team matches (`KXNBAGAME-…OKCLAC` → a Lakers event;
`KXNHLGAME-…OTTNJ` → an Islanders event; `KXNFLGAME-…MINNYG` → a Cowboys event). Four MLB rows
show it too. Not quantified; flagged.

**Consequences:** CLV rows are internally consistent but off-target (+0.19¢/ct). **Settlement
is on the wrong game** — 543 of 650 captured rows have `settled_at` before their own market's
commence, so P&L / win-rate / outcome for those bets describe a different game (paper only).
**P-001's edge is untested in both directions.**

## 3. What was fixed (on disk, not deployed)

| file | change |
|---|---|
| `src/clv_close.py` *(new)* | `close_fair()` → `(result, reason)`, 8 tags; injectable `fetch` so replays cost no credits |
| `src/mlb_teams.py` *(new)* | Kalshi MLB code → Odds API name; `teams_from_mlb_ticker()`, doubleheader `G1/G2` suffix. 537/541 distinct tickers parse before the suffix fix, 541/541 after |
| `scripts/clv_settlement.py` | instrumented `close_fair`; per-run reason histogram; **team names from the ticker**, falling back to `event` |
| `scripts/diagnose_clv_capture.py` *(new)* | full-corpus replay, disk cache, reason/sport/month histograms, ticker-vs-priced-game audit, credit accounting, `--max-api-calls` hard cap |
| `Legacy/Kalshi Arb Project/src/matcher.py` | ties within `score_tie_epsilon` (1.0) broken by **start-time proximity**; ticker-encoded start preferred over `close_time`; new `ticker_time_window_minutes` (720 = 12 h) applied only when the start came from the ticker, so date-only tickers keep the legacy wide window |
| `tests/test_clv_capture.py` *(new)* | 19 regression tests: all 8 reasons, ticker→team parsing, series disambiguation both directions, wrong-day-only rejection, doubleheader still matches, date-only tickers unaffected |

**Test results (real):** `python3 -m pytest tests/ -q` → **1469 passed, 0 failed**. Legacy
suite `Legacy/Kalshi Arb Project/tests/` → 773 passed / 42 failed, and the **failure set is
byte-identical to the pre-change baseline** (diffed against a scratch copy with
`git show HEAD:…matcher.py` restored) — all 42 are pre-existing local-Python-3.9 environment
failures; the droplet runs 3.12.

**Recovery, replayed from cache at zero credits** (`--names event` vs `--names ticker_then_event`):

| reason | legacy | fixed |
|---|---|---|
| `OK` (recovered) | 0 | **4** |
| `NO_TEAM_MATCH` | 10 | 7 |
| `TIME_GT_3H` | 10 | 9 |
| `NO_PINNACLE` | 1 | 1 |

**4 of 21 residual bets (19%) recovered → capture 96.9% → 97.5%.** Exactly the rows whose
`event` named teams absent from their own ticker (e.g. `KXMLBGAME-26JUL201910BALBOS-BOS`
labelled "Houston Astros vs Baltimore Orioles"). Written to
**`research/clv_recovered_ticker_names_2026-07-26.jsonl`**; live `clv_log.jsonl` untouched.
**Honest caveat: all four are themselves ticker≠game rows, so they belong to the inadmissible
cohort and should not count toward a re-scoped gate.**

**The residual 17, each inspected against its cached snapshot:** 9 `TIME_GT_3H` — the ticker's
game does not exist in the Odds API at that time; nearest same-teams game is 18–22 h away.
Widening the window would price a *different game*, i.e. the same defect. **Do not widen.**
7 `NO_TEAM_MATCH` — genuine Odds API coverage holes; one (`26JUL182008LADNYY`) exists in
neighbouring snapshots but had dropped out of the T−4 min one, recoverable by a T−15/60 min
retry (cheap, not implemented, changes the anchor). 1 `NO_PINNACLE` — game present with 6
books, no Pinnacle.

**On the Pinnacle→sharp-consensus fallback: don't.** It changes what "closing fair" means — a
material change to a pre-registered gate metric — to buy **1 row in 671 (0.15%)**. Proposed
and declined, not applied.

## 4. Gate reachability — the decision for Sam

Measured: MLB settled **5.2/day** (trailing 14 d) to **8.0/day** (July MTD); capture 96.9% →
97.5%; non-MLB 55% of flow structurally excluded; **admissible rows (ticker == priced game)
105 of 650 to date, only 16 of them in July ≈ 0.6/day.**

| scenario | rows today | rate | 200 reached |
|---|---|---|---|
| **A. Gate as literally written** | **650** | 5–8/day | **already met, ~2026-07-22.** Reads +1.39¢/ct → spurious PASS |
| **B. Admissible-only, matcher NOT fixed** | 105 | ~0.6/day | ~158 more days → **~Jan 2027**, except MLB stops in late Sept → **stalls ~160 rows in October, resumes April 2027** |
| **C. Matcher fixed, keep 105 legacy admissible** | 105 | 5–8/day | **~mid-August 2026** (12–18 days) |
| **D. Matcher fixed, clean forward sample only** | 0 | 5–8/day | **late Aug – early Sept 2026** (25–38 days), inside the season |
| E. Also generalise past MLB | — | ×2.2 | ~2 weeks faster, changes the population |

**So the honest answer is not "years away". It is worse: the gate is already satisfied on
paper, by rows that do not test the hypothesis, at almost exactly the target number.**
Scenario B — tightening admissibility but leaving the matcher alone — *is* the "never / 2027"
outcome.

### Proposed re-scope, for Sam's approval (NOT applied)

1. **Do not read the current 650 rows as gate progress.** Freeze P-001 at NO DECISION until
   the matcher fix ships.
2. **Add an admissibility rule, leaving the metric untouched:** a row counts only if
   `|ticker-encoded start − priced game_time| ≤ 3 h`. A filter on *which trades are eligible*,
   not a change to "de-vigged Pinnacle close, net of maker fee". Mechanically checkable from
   fields already logged — **but note it requires the trade log's `game_time`, not
   `clv_log.commence`** (see the correction above).
3. **Choose the basis — recommend D** (clean forward sample, n=200, post-fix only). C is
   ~2 weeks faster but mixes two matcher regimes, and the 105 legacy admissible rows are
   non-randomly selected (they are the cases where the odds list happened to contain one game
   for that pair; 83 of 105 are April).
4. **Keep the job MLB-only.** The +1.4pp was measured on MLB; generalising changes the
   population mid-test.
5. Optional: lower n to 150 for a September verdict. Not recommended — at 5–8/day that is
   about a week's difference.

**Caution on what the admissible cohort shows.** Filtered to admissible rows *and* the pod's
own underdog rule (`entry < 0.50`): **+13.42¢/ct, n=73, 21 day-clusters, CI [+7.15, +18.79]**
— ~10× the target. **Hypothesis-generating only**: small n, 79% April, and the cohort is
selected by the matcher accidentally getting it right (which correlates with thin slates and
single-game pairings). A reason to run the forward test properly, not a result.

## 5. Odds API spend

Remaining at start **4,867,826** → at end **4,867,664** = **162 credits**. 15 historical
snapshot calls @10 (the 15 uncaptured MLB games), one future-timestamp probe, two zero-cost
`/sports` quota reads. Both the legacy and fixed replays ran entirely from cache at **zero**
additional cost. Quota exhaustion was never plausible: 132 k of ~5 M used lifetime.

## 6. Deploy list (nothing shipped)

1. `Legacy/Kalshi Arb Project/src/matcher.py` — tie-break + ticker-time window. **Changes live
   P-001 trading behaviour; gating item for any P-001 verdict.**
2. `scripts/clv_settlement.py`, `src/clv_close.py`, `src/mlb_teams.py` — safe, additive,
   idempotent.
3. Not applied: `matcher.time_window_minutes: 43200` remains the loose fallback for date-only
   tickers; the new 12 h ticker window covers MLB regardless, but 43200 should be revisited for
   NBA/NHL back-to-backs.
4. Merge (or not) `research/clv_recovered_ticker_names_2026-07-26.jsonl` into `clv_log.jsonl`
   — safe (fingerprint dedup), but inadmissible under the proposed re-scope.

## 7. Follow-ups for separate tasks

- **Wrong-team matches outside MLB** (LAC↔LAL, NJ↔NYI, MIN/NYG↔DAL). `_both_teams_present` did
  not catch these. Not quantified.
- **P-001's settler settles the wrong game** for the mismatched cohort — a distinct defect
  downstream of the matcher. Any P&L or win-rate read on P-001 before the fix is invalid.

---

*Verification note: the orchestrating session independently recomputed the headline numbers
directly against the droplet's live logs, read-only. Confirmed: 650 clv_log rows; all-rows
mean +1.39¢; ticker==priced n=105 at **+7.65¢**, day-clustered CI [+3.46, +12.05];
ticker!=priced n=545 at **+0.19¢**, CI [−0.26, +0.66]; 650/650 rows joined to a trade-log
`game_time`; 628/734 (85.6%) of PLACED rows mismatched; 543/650 (83.5%) settled before their
own ticker's game start. The one correction to the agent's draft is locational and is recorded
in the box above — the mismatch is invisible in `clv_log` alone, where `commence` agrees with
the ticker 650/650.*
