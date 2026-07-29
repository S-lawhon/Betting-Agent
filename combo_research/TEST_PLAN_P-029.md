# P-029 Combo Maker — Test Plan & Runbook

**Created:** 2026-07-28 · **Capital authorised:** <$1,000, Sam accepts full loss
**Hard kill switch:** **$500 cumulative realised loss** (permanent halt) · **$100/day** (24h pause)
**Host:** new dedicated VPS · **Auth client:** proper `src/kalshi_private.py`, reusable

Research: `combo_research/REPORT_Combo_MM_2026-07-28.md` · Spec: `SPEC_P-029_Combo_Maker.md`

---

## 0. Do this first — rotate the Kalshi API key

The RSA private key in `.env` was exposed in a Cowork transcript on 2026-07-28. It is **not** in
git (`.env` is gitignored), so the exposure is the transcript only — but the key can place and
cancel real trades.

1. Kalshi → Settings → API Keys → delete the key matching `KALSHI_API_KEY_ID`
2. Generate a new pair; save the private key to a file outside the repo
3. Set `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`; **delete the inline
   `KALSHI_PRIVATE_KEY` variable** so a multi-line PEM can never be echoed again
4. Review Kalshi's activity log

**Then create a SECOND, read-only key** for Phases 0–1. Kalshi scopes are
`read, write, write::trade, …` — a read-scoped key cannot trade even if it leaks, and Phases 0–1
never need to. Only Phase 2 onward uses the write key.

---

## Why this order

The binding gate is **fill rate**, and the house rule after P-017A says no maker pod may be
proposed without a fill estimate. But fill rate has a cheap proxy that needs neither credentials
nor capital, so the plan spends *nothing* until that proxy says the economics are there.

Each phase must clear before the next begins. **Phases are not parallel.**

| phase | needs | capital | duration | answers |
|---|---|---|---|---|
| 0 Public shadow | nothing | **$0** | 5–7 days | competition margin |
| 1 RFQ observer | read-only key | **$0** | 3–5 days | flow rate, latency, leg visibility |
| 2 Interception test | write key | **~$20** | 1 day | do resting orders beat the RFQ |
| 3 Live micro-quoting | write key | **≤$500** | 14 days | **fill rate + realised edge** |

---

## Phase 0 — Public shadow logger ($0, no credentials, RUNNING)

`combo_research/shadow_public.py` — already written, tested against the live API, and collecting.

**What it does.** Every combo market is instantiated on demand when someone builds a ticket, so a
newly-created KXMVE market *is* an RFQ, visible without credentials. The logger sweeps the two live
rolling collections for new target-zone combos, snapshots the underlying leg books at that instant,
and later records whether the combo traded and at what price.

    winning_price − our_model_price = the room a quoter had

**Why this is the right first move.** It attacks Gate 1's core question — is there margin between
fair value and where combos actually trade — for zero dollars and zero counterparty risk. If the
margin is thin, we stop here and have spent nothing.

**Design rule it enforces:** it stores **raw leg marks**, not just a derived price, so Phase 0 data
can be re-priced under the Gate 3 copula without re-collecting. Kalshi purges settled combo markets
after ~3 months; anything not captured now is gone permanently.

### Run it

```bash
# on the VPS
python3 shadow_public.py --db /var/lib/p029/shadow.sqlite      # runs indefinitely
python3 shadow_public.py --db /var/lib/p029/shadow.sqlite --report
```

Single-instance guarded (a second copy refuses to start — two instances lock each other out of
SQLite and silently stop collecting, and a gap in the tape cannot be backfilled).

### First live signal — 2026-07-29, n=39, INDICATIVE ONLY

| slice | n | median | mean | frac > 0 |
|---|---|---|---|---|
| all | 39 | +0.69¢ | +1.66¢ | 72% |
| in-zone | 10 | +1.06¢ | +1.19¢ | 80% |

**Read this cautiously and do not update on it.** n=39 over minutes; the sample is selected toward
combos that trade instantly; and **part of this margin is genuine leg correlation, not profit** —
independence understates a correlated joint, so a positive margin is *expected* even against a
perfectly fair quoter. The number that matters is this distribution after a week, compared against
a correlation-aware price rather than the independence product.

Note also: "trade rate among resolved" is biased upward until the run is older than 48h, because a
traded combo resolves immediately while an untraded one waits out the window.

### Gate 0 — decide after 5–7 days

- **CONTINUE if** the in-zone median margin against a *correlation-adjusted* price is **≥ +2.0¢**
  with n ≥ 500 resolved in-zone combos.
- **STOP if** the median is **< +1.0¢** — there is not enough room between fair value and the
  winning quote to pay for the build, whatever the historical tape said.
- If it lands between, extend one week rather than proceeding on a marginal read.

---

## Phase 1 — RFQ observer (read-only key, $0)

Build `src/kalshi_private.py`: RSA-PSS request signing (`KALSHI-ACCESS-KEY`,
`-SIGNATURE`, `-TIMESTAMP`), REST helpers, and a `communications` WebSocket consumer.
**This is the module the whole stack is missing** — there is currently no authenticated Kalshi
client anywhere in the repo, which is also why P-022 is "ARMED" but has no path to actually place
its 13 quotes. Building it properly here unblocks both.

Subscribe to `wss://external-api-ws.kalshi.com/communications`. `rfq_created` is broadcast to **all**
subscribers and carries `mve_selected_legs` in the message body — so the observer sees the full leg
set with no follow-up call.

**Measure:** RFQ arrival rate by hour and by sport; what share fall in the target zone; time from
`rfq_created` to the resulting trade (this is the real reaction budget, and the report could only
bound it from documentation); and push latency.

**Gate 1a — CONTINUE if** target-zone RFQ arrivals ≥ **50/day** and median `rfq_created` → trade
is ≥ **5 s**. Below 5 s, the resting-interception path is dead and Phase 2 becomes a sub-second
build — a materially different cost.

---

## Phase 2 — Resting interception test (~$20, one afternoon)

The single highest-leverage unknown in the whole plan. Rulebook 5.3.D.d–e says RFQ orders enter the
public book at *lower* time priority than resting orders, and *"Quoter and Requester may in fact
never transact a contract between them, if existing book liquidity exists at the quoted price."*
If true, we can capture flow at our own price without racing the auction.

**Experiment — you are both sides, so it costs almost nothing:**

1. Pick a live combo market with 2 legs in the target zone.
2. Bot rests a small offer (5 contracts) at a price you are happy to be filled at.
3. **You** build that exact combo in the Kalshi app and request a quote.
   (`is_ordered=false` everywhere, so the same leg set resolves to the same market.)
4. Accept whatever quote comes back.
5. Check `GET /portfolio/fills`: did the bot's resting order fill ahead of the accepted quote?

**Gate 2 — records the execution path, does not kill the pod.** Resting works → Phase 3 is a
seconds-scale build on the new VPS. Resting does not work → Phase 3 needs sub-second RFQ response,
and we re-scope before spending the $500.

---

## Phase 3 — Live micro-quoting (≤$500, 14 days) — THE REAL GATE

Rest offers (or respond to RFQs, per Gate 2) in target-zone combos at the correlation-aware model
price plus margin. **1–5 contracts per market.** At ~80¢ collateral, $500 supports ~600 contracts
outstanding — enough for a usable fill-rate confidence interval.

### Hard limits, wired as code and not as guidance

```
HALT_CUMULATIVE_LOSS = 500.00     # permanent stop, requires manual restart
HALT_DAILY_LOSS      = 100.00     # 24h pause
MAX_CONTRACTS_PER_MARKET = 5
MAX_OPEN_COLLATERAL  = 500.00
HARD_EXCLUDE_PRICE_ABOVE = 0.60   # measured -2.46 c/ct; never quote here
HARD_EXCLUDE_PRICE_BELOW = 0.05
LEGS_MIN, LEGS_MAX = 2, 4
```

The halt must be checked **before every order**, computed from `GET /portfolio/fills` and
settlements — never from an in-process counter, which resets on restart. This is the failure mode
that let P-014 write `fill_price: null` on 356 rows for four months.

### Pre-registered kill criteria (fixed now, per §7 of the report)

- **KILL if** fill fraction < **10%** of priced target-zone RFQ flow
- **KILL if** fewer than **200 fills** accumulate in 14 days (underpowered — extend or stop, do
  not conclude)
- **KILL if** realised P&L per filled contract < **+1.0¢** with a day-clustered CI excluding the
  measured +3.17¢

Thresholds may be **tightened** at any time; **loosened only** with written justification and a
forward-only sample (the P-022 amendment precedent).

### Settlement, from day one

`KalshiComboSettler` ships with Phase 3, not after. P-017 shipped without a settler and
`on_settlement` was dead code for months.
- Settle only on a populated `result`; `status="closed"` is **not** settled.
- **`result="scalar"` is a partial payout at `settlement_value_dollars`, never VOID.** 56,128
  settled combos resolve scalar. Booking them at $0 repeats the golf-settler error exactly.

---

## Runs in parallel with everything, starting now

**The settled-combo archiver.** Kalshi purges settled combo markets after ~3 months, so only
2026-05 → 2026-07 exists today. Every day this does not run is a day of NFL/NBA-season combo data
that will never exist — and the season is the regime that actually matters, since the entire
research tape is summer MLB/WNBA/World Cup. Archive gzipped under `combo_research/archive/` and
commit. **This is worth doing even if P-029 is killed at Gate 0.**

---

## What Sam needs to do

| when | action |
|---|---|
| **now** | Rotate the Kalshi API key (§0). Create a separate read-only key. |
| **now** | Provision the VPS (2GB is fine for Phases 0–1; 4GB before Phase 3). Send me the host and I'll deploy the shadow logger and the archiver under systemd. |
| **now** | `git add combo_research && git commit` — the bridge can't stage (it leaves an unremovable `.git/index.lock`; I moved two stale ones to `_to_delete/` for you to remove). |
| **after Gate 0** | Confirm go/no-go on the margin read before any credentials are used. |
| **Phase 2** | ~15 minutes on your phone building one combo in the Kalshi app while the bot rests an order. |
| **before Phase 3** | Confirm the account can trade combos, and fund it to ~$600. |

## Open items carried from the research

1. Confirm with Kalshi: exact collateral on a short combo, and whether **any** leg netting exists.
   No documentation answers this, and it is what sizes the strategy.
2. Same-game correlation is unmeasured (n=39, both live collections are cross-slate). Blocked until
   NBA/NFL season — another reason the archiver matters now.
3. HVM timers contradict between the docs (3s/1s) and the CFTC filing (2s/3s). Assume the tighter
   of each until measured in Phase 1.
