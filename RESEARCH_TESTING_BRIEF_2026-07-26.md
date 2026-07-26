# Research & Testing Status Brief

**Betting Pod Shop — Kalshi Fund Project**
**Prepared:** Sunday, July 26, 2026
**Prepared from:** `manager/registry.yaml`, project memory (R1–R4 research rounds + night-run outcomes), git log through `f90cfb2`, and the research report set on disk.

> **Standing invariant:** everything is paper/demo. No real money has ever been deployed. No pod is `tier: production`. Nothing here proposes changing that.

---

## 1. Where we actually are

Over the last five weeks the operation has run **27 numbered hypotheses (P-001 → P-027)** plus a satellite watchlist through an increasingly disciplined kill-first testing process. The headline:

| | Count | Which |
|---|---|---|
| **Validated & running in paper** | 4 | P-001, P-014, P-015, P-017 |
| **Validated, not yet built** | 1 | **P-022** (green-lit, awaiting your approval) |
| **Advanced Phase 1, Phase 2 owed** | 1 | P-023 PGA make-cut |
| **Never tested — queued** | 4 + satellites | P-020, P-026, P-027, P-023 flip-side |
| **Blocked on data accumulation** | 3 | P-018, MLB props execution test, EV-Map Builds 2/3 |
| **Killed on evidence** | 8 | P-013, P-016 (v1+v2), P-019, P-021, P-024, P-025, P-015b, EV-Map Build 1 |
| **Shelved (unexecutable, not refuted)** | 3 | P-002, P-006, P-017M |

**The single most important fact:** in five weeks of testing, exactly **two** new hypotheses have survived a full gate — P-017 (golf top-N tie inflation, now in forward validation) and **P-022** (round-leader dead-heat fade, green-lit July 24 and still not built). Everything else either died on contact with real Kalshi prices or is still waiting for its test.

That hit rate is the process working, not failing. But it means our forward pipeline is thin, and the one validated thing we have is sitting idle.

---

## 2. The edge law we've earned

Every kill has sharpened the same rule, and it should govern what we test next:

> **An edge needs BOTH (a) a reason the price is wrong AND (b) a reason nobody has fixed it.**

- **Behavioral / "the crowd is irrational" stories: 0 for 4.** P-016, P-019, P-025, EV-Map Build 1 all died. Kalshi books are better calibrated than the academic literature assumes.
- **Pure market-making: 0 for 2.** P-016 v1 and v2. Adverse selection ate the spread every time (−1.29¢/ct markout at 814 fills).
- **External sharp reference alone: 0 for 3** where Kalshi is liquid. P-021 proved Kalshi's MLB totals are *as sharp as Pinnacle* (Brier 0.2477 vs 0.2484). P-024 confirmed the thin corners are equally sharp. P-025's free signal wasn't accurate enough.
- **Settlement / structural mechanics: 3 for 3.** P-015 (tennis qualifier inattention), P-017 (golf top-N "and ties" inflation), P-022 (round-leader $1/n dead-heat split). These are written into certified contract terms — no probability disagreement required, and they don't decay.

**Implication for the research queue:** weight it heavily toward settlement-mechanic hypotheses (P-026, P-027, satellites) and treat any new "we have better information" idea as guilty until proven innocent.

---

## 3. Live paper pods — testing still owed

These four are accumulating forward evidence. All are `blocked_on: time` — nothing is owed by you; they need calendar, not decisions.

| Pod | Hypothesis | Gate | Progress | Test lands |
|---|---|---|---|---|
| **P-001** Kalshi Moneyline Value | Underdog realized ROI follows the measured +1.4pp net-maker CLV | 200 CLV forward rows | ~29–81 rows (last recorded) | Unknown — capture rate is the constraint |
| **P-014** Live Game Agent | Does the calibration edge reach significance with more n? | 500 settled trades | accumulating | Unknown |
| **P-015** Tennis Qualifier Favorite | Does +4.06¢/ct (n=238, CI [+1.44,+6.34]) replicate forward? | 120 trades (locked rule) | **0** | First volume **Aug 17–21** (US Open quals); 120-trade checkpoint **~Jan 2027**; 240 **~Jul 2027** |
| **P-017** Golf Top-N taker | Does forward net CLV hold above half the +6.92¢/ct backtest? | 8 tournaments | **1 of 8** | ~Q4 2026 at PGA cadence |

### Specific tests / checks still needed on the live set

1. **P-001 CLV capture rate — unresolved.** The settlement cron was fixed July 21 (it had been structurally guaranteed to find zero rows on log-rotation days). But of 14 newly-found settled bets, only 4 produced records — `close_fair()` returned nothing for 10 games. **Cause unknown** (Odds API historical depth, quota, or team-name normalization). Until this is diagnosed, the 200-row gate may be unreachable on any sensible timeline. *This is the highest-value unglamorous test we can run.*
2. **P-017 settler verification.** Top-N markets sit `status="closed"` with an empty result for ~a day post-tournament; first real settlement signal was expected **~July 28**. If PLACED rows keep climbing and settled stays 0 past that date, the settler isn't wired in — and the failure mode is silent. **Check this week.**
3. **P-017 correlated-exposure cap — verify in production.** July 25's halt (one tournament's all-miss lost −$171 paper, 16% of bankroll on one event, tripping the 5% daily limit) produced `max_event_exposure_pct: 0.08`, committed as `287cf89`. Needs a deploy + a live event to confirm it binds.
4. **P-015 needs nothing until Aug 17** — and per the locked decision rule, silence is not confirmation. If volume never reaches 120 the verdict stays NO DECISION and the pod stays in paper forever.

---

## 4. Validated but unbuilt — the live decision

### P-022 · Round-Leader Dead-Heat Fade — **GREEN-LIT, awaiting your approval**

The first fully-validated new edge since P-017, and the only one in the pipeline.

- **Phase 1 (settled data):** round-leader ties split payout $1/n — verified verbatim in `PGAROUNDLEADER.pdf`. 30% of golf rounds tie → a 37% conditional payout haircut. Fading the 5–10¢ band earns **+4–6¢/ct**, CI excludes zero at 12/6/3h anchors.
- **Phase 2 (tick replay — the hard test):** survives pessimistic through-fills *and* measured adverse selection. **+3.4¢/ct at offset +0.02**, tournament-clustered CI **[+1.7, +5.1]**, **16 of 19 tournaments positive**, robust to leave-one-out.
- **Constraints, all sign-neutral:** capacity is SMALL (~$140 P&L on ~$3.8k collateral over 19 tournaments ≈ 3.7%/mo). Tail is real — losses concentrate where a faded name actually leads.

**Testing still required before it can trade even in paper:**

1. **Pre-register the forward gate** (n tournaments, metric, kill rule) *before* the first fill — the P-013 lesson, non-negotiable.
2. **Fix `KalshiGolfSettler`** — it currently treats `result="scalar"` as a withdrawal void. Scalar *is* the $1/n dead-heat payout. **Every P-022 number depends on settling this correctly**, and the same bug silently distorts P-017's tie accounting. This is a code test, not a research test, and it's the gating item.
3. **Wire the mandatory caps:** per-name ≤0.5% bankroll, per-tournament ≤5%, aggregate ≤15%, quote only at H≈12–24h pre-round.
4. **Sample caveat to carry forward:** Kalshi's trade history reaches back only ~1 month, so Phase 2's effective sample is late-June → July 2026. The forward test is the real replication.

---

## 5. Hypotheses that have never been tested

This is the actual answer to "what testing do we still need to do."

### Tier 1 — testable now, no new data required

| # | Hypothesis | Test design | Cost | Status |
|---|---|---|---|---|
| **P-020** | Polymarket → Kalshi cross-venue signal on politics/world markets | ~100–200 settled pairs, event-clustered, corridor-sensitivity table | ~1 day, existing data | **Queued 5 separate times, never run.** Prompt exists at `research/prompts/PROMPT_P020_CrossVenue.md` |
| **P-023b** | PGA make-cut advance (+4.8¢/ct Phase 1, 10 tournaments, CI [+2.3,+7.7]) | Phase-2 maker fill replay, same design as P-022 | ~1 day **+ harness rebuild** | Prompt exists. **Harness `.py` files were lost** — must be regenerated from the restored reports (data caches intact) |
| **P-026** | Stat-leader dead-heat tie fade (KXLEADER $1/n rule verified live) | **$0 pre-trade test available today:** sum co-leaders' mids; ≳100¢ = market ignoring the split | Free | Never run. Tie rates computed over 30 seasons: NFL INT 47%, MLB pitcher wins 30%, NFL rush/rec TDs 20% |
| **P-027** | ECONSTAT prelim-vs-final flips — 148 series settle on the first *non-preliminary* release but close on the flash print (UMich, flash PMI qualify; CPI/NFP do not) | Settled-data flip study | ~1 day | Never run. Prompt exists |
| **Satellites** | Award-tie regime (GLOBES/CRITICS/AMAS verified: tied winner pays **zero**); RT non-partition + Monday-10AM read-time drift; WINS/EXACTWINS partition dutch-book scanner | Census + scanner | ~1 day | Never run. Prompt exists |
| **P-023c** | **Unexplored flip-side:** round-based top-N came back **−5 to −16¢** and PGA top-N control **−3.2¢** — i.e. systematically *over*-priced. That's a fade candidate nobody has looked at | Re-aim the P-022 harness at the negative cohort | ~half day | Never even specced |

### Tier 2 — falsification lands on a calendar date

- **P-026 free falsification: ~Oct 15, 2026.** First KXLEADER MLB-wins settlements. A tie resolving `scalar 0.50/0.33` confirms the split rule empirically at zero cost.
- **Tail risk to design around:** only ~6–8 independent stat-seasons per year, with total within-stat correlation. Cap at 1–2% bankroll per stat-season.

### Tier 3 — blocked on data accumulation

| Workstream | Blocked on | Unblocks |
|---|---|---|
| **P-018** In-play surprise-gated fade maker | `betting-book-capture` has **0 in-play ticks**; gate logic + tests already built | ~2–3 weeks of capture from July 21 → **~Aug 4** |
| **MLB props execution test** | 27 game-days of order-book snapshots; 3 collected as of last check | **~Aug 17.** Explicitly do NOT run the underpowered 2-day version — it would relaunder a known error |
| **EV-Map Build 2** (weather maker) | 30 days live across ≥10 cities; Mac cron jobs **silently skip whenever the Mac sleeps** | Ongoing — every skipped week is calibration data lost forever (90-day API horizon) |
| **EV-Map Build 3** | Polymarket leg of the basis capture was never built (Kalshi leg live since July 21) | Buildable now; 2 weeks of capture after that |

---

## 6. Killed — do not re-test without new information

Recording these so nobody spends a week re-deriving a dead answer.

| # | Verdict | Why it died |
|---|---|---|
| P-013 Kalshi-Deribit crypto options | **KILLED** | −$2,094, per-bet CI [−51%,−11%], backwards calibration + Kelly denominator bug. This pod is why P-015's rule is locked in advance |
| P-016 Live In-Play Maker (v1) | **KILLED** | 814 fills, +5m markout −1.29¢/ct. Spread capture was +4.08¢ — it earned the spread and lost more than all of it to adverse selection |
| P-016 v2 | **REJECTED same day** | Founding premise (loss concentrated in ±15s post-event window) did not reproduce. Loss is diffuse; 62% is >60s from any state change |
| P-019 Longshot maker harvest | **KILLED** | 5,192 event-clustered settled contracts. Kalshi longshots are well calibrated; the only real mispricing is a 0–3¢ dying tail a maker can't capture |
| P-021 MLB totals vs sharp consensus | **KILLED** | Kalshi pregame is as sharp as Pinnacle. *Positive residue:* Pinnacle leads Kalshi's own drift (b=+0.91, t=+3.21) — too small for taker fees, and maker capture is the P-016 trap |
| P-024 MLB F5/RFI thin corners | **KILLED** | Thin corners as sharp as the headline. Brier tied, gap→outcome insignificant. The P-021 lead-lag does **not** amplify in the corners. No Pinnacle F5 reference exists at pregame horizons (0/86 snapshots) |
| P-025 Music-chart mid-week edge | **KILLED** | Conditions are mutually exclusive across series: ALBUMS has an accurate free signal (9/9) but Kalshi is already ~$1.00 by Tuesday; SONGS has real 7–36¢ gaps but the free signal is only 50–60% accurate → −2.9¢/ct. Even the oracle ceiling (~$24/wk) is below the $500 gate |
| P-015b Lower-tour tennis | **DROPPED** | Challenger −1.98¢, ITF −2.33¢, both negative point estimates. Worse: it would have supplied 79% of volume and swamped the P-015 signal we're actually trying to measure |
| EV-Map Build 1 (MLB totals fade) | **KILLED** | +60%/trade was a close-anchoring selection artifact |
| P-023a Top-N basket | **KILLED** | Inflation is real but already priced at the 48h anchor |

**Shelved, explicitly NOT refuted:** P-002 and P-006 (no Polymarket execution access — P-006 was the *most trustworthy* edge in the lifetime analysis, CI [+5.4%, +21.2%]); P-017M golf fade maker (its +9.1¢ was a contract-weighting bug; corrected +3.34¢ is below baseline, but 6 events against an 8–10 event gate is stopping early on unfavorable data).

---

## 7. Methodology debts that affect *all* future tests

These are cross-cutting and will corrupt results if not respected:

1. **Never use closing/last prices on in-round or in-play markets** — outcome-contaminated. Always anchor to scheduled event start, and use executed trades or tight two-sided mids. **Bare asks fabricate edges.**
2. **Every close-anchored T−1h result in `03_validation.md` is UNVERIFIED** (UFC MOV, WC props cheap-side, pooled props tables) — same methodology risk that killed Build 1.
3. **`last_price` is settlement-contaminated** — never use it for calibration.
4. **Event-cluster everything.** Bets within a tournament/game are correlated; treating each bet as an observation is how P-017M produced a phantom +9.1¢.
5. **Reproduce a founding number before it justifies a successor** — the P-016 v2 lesson, banked.
6. **Per-EVENT correlated exposure caps are mandatory** for every multi-name-per-event pod — the July 25 halt lesson.

---

## 8. Open items requiring your decision

1. **Approve the P-022 paper pod build.** It's green-lit, the spec is restored, and it is the only validated unbuilt edge we have. Blocking item is the scalar-settler fix.
2. **Commit the research artifacts to git — today.** `golf_quirks_research/` and five prompt files are **still untracked** (`git status` shows `??`). This is the exact condition that caused the July 25 file loss, where the P-022/P-023 harnesses vanished and had to be reconstructed. The markdown was recoverable from session copies; the `.py` files were not.
3. **Pick the next research run.** My recommendation, in order: **(a)** diagnose the P-001 `close_fair()` capture failure — it may be silently making a live gate unreachable; **(b)** finally run **P-020** (queued five times, one day of work, existing data); **(c)** the **free P-026 pre-trade test** (costs nothing, runs today); **(d)** rebuild the P-022 harness and run **P-023 make-cut Phase 2**.
4. **Decide whether to build the Polymarket leg** of the basis capture, which is now the sole blocker on EV-Map Build 3.

---

## 9. Recommended sequence

```
NOW (this week)
  ├─ Commit golf_quirks_research/ + prompts to git            [5 min, prevents repeat data loss]
  ├─ Verify P-017 settlement actually resolved post-Jul-28    [silent failure mode]
  ├─ Diagnose P-001 close_fair() 10-of-14 miss rate           [may be blocking a live gate]
  └─ Run the free P-026 co-leader mid-sum test                [$0, today]

NEXT (1–2 weeks)
  ├─ P-022: settler scalar fix → pre-register gate → build paper pod
  ├─ Run P-020 cross-venue backtest                           [1 day, 6th time of asking]
  └─ Rebuild P-022 harness → P-023 make-cut Phase 2

THEN (dates fixed by data)
  ├─ Aug  4 — P-018 in-play tick sample matures
  ├─ Aug 17 — MLB props execution test unblocks; P-015 first volume (US Open quals)
  ├─ Sept   — position P-026 for MLB season-end
  └─ Oct 15 — P-026 free falsification via first KXLEADER settlements
```

---

### One-line summary

Four pods are accumulating forward evidence on locked gates and need calendar rather than attention; one validated edge (**P-022**) is sitting unbuilt behind a settler bug and your approval; and **six hypotheses have never been tested at all** — four of which need only a day of work against data we already have.
