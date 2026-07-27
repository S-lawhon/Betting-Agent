# P-022 Backtest Widening — PRE-DECLARATION

**Written and committed 2026-07-28, BEFORE any extended data was pulled or any
new number was seen.** That ordering is the whole point of this document: a
sample widened after a favourable result is a fitted sample, and the only
defence is writing down the plan and the refutation condition first.

Task: `research/prompts/PROMPT_P022_Widen_Backtest.md`.

---

## 1. What is being changed, and what is not

**Changed:** the number of tournaments in the Phase-2 *backtest*.

**Not changed, and not touchable under this task:**

| frozen | value |
|---|---|
| band | `(0.03, 0.12)` |
| quote offset | `+0.02` |
| placement window | `[12h, 24h]` |
| series | the 13 `KX*R{1,2,3}LEAD` |
| fill rule | pessimistic through-fill, taker buys strictly through the resting ask |
| forward gate | `P022_DECISION_RULE.md`, T = 14, **currently T = 0** |

**No sweeps of any kind.** If the wider data makes me want to re-optimise the
band, the offset or the window, the instruction is to **stop and report the
impulse instead of acting on it**. This is a sample extension, not a
re-parameterisation.

**This does not touch the forward gate.** A wider backtest changes the *prior*,
not the test. If the effect size moves materially, the correct response is to
report that T = 14's power calculation was derived against a now-superseded
effect and let Sam decide — **not** to re-derive the threshold.

---

## 2. How far back

**As far as the API actually returns, measured per series — not assumed.**

Every write-up to date says history reaches back "~1 month"; the 2026-07-26 run
found prints back to at least 2026-05-20. Both are claims about an API, so both
get measured: the first deliverable is the **earliest retrievable print per
series across all 13**, from `/markets/{ticker}/trades`, reported as a table
before any tournament is added.

The window is therefore defined by the measurement, not chosen after seeing
results: **every tournament whose determining round closed at or after the
measured per-series horizon, and before the original study's start.**

## 3. Which tournaments qualify

**The same inclusion rules as the original study, applied unchanged.** A
tournament is admitted only if it satisfies all of:

1. Settled, with a populated `result` on its markets.
2. Tick prints retrievable for the markets in band.
3. A resolvable close reference (now available externally via
   `src/golf_schedule.py`; the original study used cached settled
   `close_time`, which is the true early-close stamp).
4. An anchor price inside the `[12h, 24h]` window that is **contemporaneous**
   — see §4.

Tournaments failing any of these are **dropped, counted, and reported as
dropped**. They are not repaired, back-filled or approximated.

## 4. Anchor contemporaneity — the make-cut lesson, applied in advance

The make-cut study's "48h anchor" turned out to be a median **68h-old** price,
stale in 100% of markets, and posting it produced **+9.5¢/ct with a CI excluding
zero — a pure artifact**. P-023c then found the bias runs the *opposite* way on
its cohort, so this is not a "stale looks optimistic" correction that can be
subtracted out. Stale is simply wrong.

Therefore, for every added tournament: report the staleness distribution of the
anchor price, and **drop stale anchors rather than correcting them**. Staleness
threshold is declared here, in advance, as **anchor age > 6h at the moment of
posting** — a quarter of the 24h window width.

## 5. Reporting — old and new stay separable

1. The **original 19 tournaments remain individually visible** in the report.
2. The **added block is reported separately before anything is pooled**, with
   its own bootstrap CI.
3. Only then a pooled number.

A pooled figure that hides a deteriorating out-of-sample block is worse than no
figure.

Statistics as the house requires: **tournament-clustered** bootstrap (one
tournament = one observation, never per-contract), plus leave-one-out.

## 6. What would count as a refutation — declared now

Stated before the numbers exist, so it cannot be renegotiated after:

| outcome | condition |
|---|---|
| **STRENGTHENED** | The added block's mean effect is **positive** and its tournament-clustered CI overlaps the original 19's. Pooled effect holds or tightens. |
| **WEAKENED** | The added block's mean effect is **≤ 0**, **or** the pooled effect drops below **+2.1¢/ct** — the bottom of Phase 2's own reported range (+2.1 to +4.7¢/ct). This is a real finding and gets reported as prominently as a positive one. |
| **INCONCLUSIVE** | Fewer than **6** clean tournaments added, or more than half the added block is lost to stale anchors. |

**A WEAKENED result does not license a third window, a different band, or a
re-anchored study.** It gets reported, and it reaches Sam immediately. It does
not by itself kill a live pod running a locked forward gate — the forward test
is what decides that — but it changes the prior the gate was sized against.

## 7. Data handling

Everything pulled is cached under `golf_quirks_research/data/` and committed
**gzipped** under `golf_quirks_research/archive/`. This window rolls off the API
permanently: an uncommitted cache here is data that cannot be recovered, which
is exactly how the 2026-07-25 `golf_quirks_research/` harness loss happened.

`python3 golf_quirks_research/backtest_fade_fills.py --validate` must still
reproduce the original published cells afterwards. If it does not, the harness
changed and the run is void.

---

## 8. Status

**Sections 1–7 are the commitment. No extended data has been pulled at the time
of this commit.**

**APPROVED by Sam, 2026-07-28**, after this document was written and before any
extended data was pulled. `STATUS_REASSESSMENT_2026-07-27.md` §5.1 listed
*"approve the P-022 backtest widening — specifically, that extending the sample
backwards does not constitute re-fitting a locked rule"* as Sam's decision; that
approval is now on the record, in the correct order relative to the numbers.

The reasoning both of us gave: it does **not** constitute re-fitting, because
`P022_DECISION_RULE.md` governs the *forward* test — which stays locked at
T = 14 and reads T = 0 — and every parameter is frozen. A wider backtest changes
the **prior**, not the gate.

---

## 9. AMENDMENT — 2026-07-28, written BEFORE any effect number was computed

**The declared backward window is empty at the source, so §2 cannot be executed
as written.** This amendment restates what will be run instead. It is committed
before the replay, and at the time of writing I have seen **only event counts,
close dates and volumes** — no fill counts, no ¢/ct, no CI.

### What §2 assumed, and why it is wrong

§2 defined the window as `[measured per-series horizon, original study start)`
on the belief that "Kalshi history reaches back to at least 2026-05-20, not the
~1 month we assumed."

Measured: **Kalshi lists 78 settled round-leader events and ZERO of them close
before the study's own start.** The constraint was never the *trade-history*
horizon — it is the **listing** horizon. Round-leader markets did not exist
before mid-May 2026. There is nothing behind the study to widen into.

A second measurement, which matters for data handling rather than scope: tick
history is **already rolling off inside the existing sample.** Probing the
oldest cached tournaments, several now return no prints at all
(`KXPGAR1LEAD-THCCBN26` 0/6, `KXPGAR3LEAD-THCCBN26` 0/6,
`KXDPWORLDTOURR1LEAD-AUAOPBKT26` 0/6). The committed cache is now the **only**
copy of part of the Phase-2 sample.

### What will be run instead

Two groups, both under the **unchanged** inclusion rules and the unchanged
frozen parameters of §1:

**(a) Within-window gaps — 4 tournaments the original pull simply missed.**
`SOO26` (2026-05-24), `DOWC26` (06-11), `MEILCFSG26` (06-18), `USSOC26`
(07-02). These sit *inside* the study's own date span. Adding them applies the
same rules more completely to the same period; it is a completeness fix rather
than a change of window, and it is the least discretionary of anything
available.

**(b) Forward extension — 4 tournaments that settled after the cache ends.**
`3MO26` (07-21), `KIN26` (07-23), `ISPHWSO26` (07-23), `ISHSO26` (07-23). These
are a genuine extension of the period. **They are still backtest, not forward
evidence**: the live pod has never quoted, so no row here can be confused with
gate progress, and `T` remains 0 regardless of the outcome.

### Everything else in this document stands unchanged

The frozen parameters (§1), inclusion rules (§3), the >6h anchor-staleness drop
(§4), old-vs-new separation before pooling (§5), the refutation conditions (§6)
and the data handling (§7) all apply exactly as written. In particular
**WEAKENED is still: added block ≤ 0, or pooled below +2.1¢/ct**, and a
WEAKENED result still licenses nothing.

Groups (a) and (b) will additionally be reported **separately from each other**,
because a within-window gap and a forward extension are different kinds of
evidence and pooling them would hide that.
