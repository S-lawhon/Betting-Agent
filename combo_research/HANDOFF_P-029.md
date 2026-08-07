# P-029 Combo Market-Making — Workstream Handoff

**Written:** 2026-07-29 · **Updated:** 2026-08-07

## Current status — read this before the historical handoff below

The original Gate 0 is **CLOSED: STOP** (2026-08-05): median margin −2.54¢,
n=62,227. A settlement join then identified three mechanistic pricing defects,
which were corrected without changing the closed verdict. Sam approved a new,
frozen, forward-only **Gate 0c** before its window opened.

- Collect first-seen combos **2026-08-06 through 2026-08-19**.
- Do not read the result before **2026-08-23**.
- The sanctioned reader is `scripts/p029_gate0c_checkpoint.py`; it enforces the
  blind date and frozen model hash.
- Gate 0c requires BOTH the model-margin condition and the realized-settlement
  condition in `recalib/PREREG_P029_Gate0c_Forward.md`.
- `p029-shadow.service` must run at `MemoryMax=768M`; the archive timer remains
  mandatory regardless of the eventual verdict.
- Apply and verify those host prerequisites from a Mac with P-029 SSH access:
  `bash scripts/deploy_p029_gate0c.sh`. It transfers only the frozen reader,
  model, copula module, and host upgrade script; it does not transfer data or
  credentials. The host change is a systemd drop-in, not a unit rewrite.
- The hourly `p029-health-check` heartbeat verifies the effective memory limit
  and records current/peak/swap usage, cgroup memory events, PID/restart delta,
  and the in-zone resolver backlog throughout the forward window.
- The primary droplet owns registered reads via
  `p029-gate0c-checkpoint.timer`: August 23 at 12:30 UTC, then August 30 only
  when the first result is mixed or insufficient. Results are immutable,
  timestamped JSON plus an atomic `latest.json`; the manager consumes that
  remote result rather than trying to read `/var/lib/p029` locally.
- **Reader extension bug fixed before the first permitted read:** the August 30
  path now includes first-seen combos through August 26. The prior reader kept
  its SQL end date frozen at August 19 even on the extension date, which would
  have called the same sample an extension. A final mixed result is reported
  `INCONCLUSIVE`; it cannot silently grant a second extension.
- **Operational deviation, 2026-08-07:** the Gate 0c deployment restart left
  `p029-shadow.service` unable to traverse its root-owned `combo_research/`
  working directory from 00:17:11 to 00:23:01 UTC (about six minutes). The tape
  gap cannot be backfilled and must be disclosed with the August 23 read. The
  deployment now suppresses rsync ownership/mode propagation, restores checkout
  traversal explicitly, and rejects a process that is merely active between
  crash-loop attempts. The repaired logger has remained running at the frozen
  `MemoryMax=768M`; the first and subsequent resolver cycles reported
  `due 0 in-zone`.
- Sam confirmed the exposed account RSA key was rotated on 2026-08-07. Phase 1
  is still unauthorized and, if Gate 0c passes, requires a separate explicit
  decision plus confirmation or creation of a distinct read-only Kalshi key.

On the P-029 host, the eventual read is:

```bash
/opt/p029/venv/bin/python3 /opt/p029/scripts/p029_gate0c_checkpoint.py --json
```

The remainder of this document preserves the July 29 build history and original
Gate 0 runbook. Where it conflicts with the status above, the status above and
the Gate 0c preregistration govern.

**Read this first.** It is the entry point to the whole workstream; the other documents are the
detail behind it.

| document | what it holds |
|---|---|
| `REPORT_Combo_MM_2026-07-28.md` | the research: measurement, four kill attempts, the numbers |
| `SPEC_P-029_Combo_Maker.md` | what the pod would be, if it clears the gates |
| `TEST_PLAN_P-029.md` | the four phases, kill switch, and runbook |
| `HANDOFF_P-029.md` *(this file)* | state, what was built, what was learned, what is next |
| `harness/` + `logs/` | every script and log behind the reported numbers |

---

## 1. The one-paragraph version

Kalshi "combos" are multi-leg parlays traded by **RFQ**, not on an order book. Retail buys them and
loses; the seller's side is **maker-fee-free**. Measured over 68 event days and 556M contracts, the
seller earns **+3.17 ¢/contract** in a target zone of **2–4 legs priced 10–35¢** (day-clustered 95%
CI [+2.78, +3.57], first-print basis). The edge survived four adversarial checks and shows **no
decay in that zone** across 11 weeks. It is the largest measured edge this project has produced.

**It is not yet actionable.** Every number is measured on trades that *happened* — at prices where
some incumbent quoter already won. Nothing yet says what fraction of flow *we* would win at a price
that preserves the edge. That is Gate 0, and it costs nothing to answer.

---

## 2. Where things stand right now

**Running, unattended, no credentials, no capital:**

- **Phase 0 shadow logger** — `p029-shadow.service` on `143.198.162.120`, collecting since
  2026-07-29 03:17 UTC. Measures the competition margin from public data.
- **Settled-combo archiver** — `p029-archive.timer`, daily 09:33 UTC. Captures settled combos before
  Kalshi purges them.

**Update 2026-07-29 (ρ fit session — `REPORT_P029_Rho_Fit_2026-07.md` for detail):**

- **The leg correlation is now MEASURED, not a prior**, off the 8-day archive (103,019
  settled combos, all legs settled): `cross +0.107 [+0.040,+0.144]`,
  `same_day +0.116 [+0.099,+0.384]`, `same_game +0.208 [+0.084,+0.221]` — **all above
  their priors**. `same_player` is unidentified (431 pairs on 2 event days) and keeps the
  0.35 prior; `load_rho` now refuses `identified: false` blocks (was a live bug).
  `combo_research/fitted_rho.json`, committed.
- **The Gate 0 in-zone median vs the copula moved +2.87¢ → +1.85¢** under fitted ρ
  (n=26 traded in-zone rows) — **below the +2.0¢ CONTINUE line, in the extend band**,
  and above the +1.0¢ STOP. Not decidable at this n (gate needs 500), but the margin
  SHRANK when the prior became a measurement. Whole-universe median vs fitted copula is
  *negative* (−0.93¢).
- The shadow DB is migrated (`copula_price`, `leg_relation` columns) and pre-copula rows
  are backfilled from stored marks (`backfill_copula.py`; it waits out the logger's
  minutes-long write locks — never restart the logger for a backfill). The gate read on
  2026-08-04 should **recompute zone membership uniformly offline** rather than trust the
  stored `in_zone` across the indep→copula code boundary.
- Archiver verified healthy on the fixed code (623.8M peak vs 1G cap, 0 swap, 0 errors,
  day-over-day growth) — but the first TIMER run of the fixed code fires 2026-07-30
  09:34:45 UTC and should be checked after (`REPORT_P029_Archiver_Health_2026-07-29.md`).

**Built and tested, not yet used against a live account:**

- `src/kalshi_private.py` — the authenticated Kalshi client (71 tests).
- `scripts/setup_subaccount.py` — subaccount isolation and its verification.
- The `LossGuard` $500 / $100 kill switch.

**Not started:** Phases 1–3. No order has been placed. **No capital is at risk.**

---

## 3. What was built, and why each piece exists

### `src/kalshi_private.py` — the authenticated client
Before this, **the stack had no authenticated Kalshi path at all**: `kalshi_public.py` is read-only
by construction, there was no request signing anywhere, and every pod ran `paper`/`demo`. **This is
why P-022 could be "ARMED" with 13 quotes and no way to place them.** Built once, properly, for
every pod that eventually goes live.

- **Signing** — `timestamp_ms + METHOD + path`, path including `/trade-api/v2` and **excluding** the
  query string; RSA-PSS/MGF1-SHA256 at digest salt length; base64; timestamp in **milliseconds**.
  Verified two ways: a test that cryptographically verifies the signature over the exact documented
  message (and asserts it must *fail* if the query string is included — the classic silent 401), and
  a live call with a throwaway key that returned **401 `authentication_error`, not 400**, proving
  Kalshi parsed all three headers and rejected on identity alone.
- **Closed by default** — `allow_orders=False`; every order/RFQ/quote method raises `OrdersDisabled`
  otherwise. `dry_run=True` logs the body and never touches the network.
- **`LossGuard`** — $500 cumulative (permanent halt) / $100 daily. Reads the **exchange**, not an
  in-process counter, because a counter resets on restart. **Fails closed.**
  **Both thresholds are NET realised loss, changed 2026-07-29** — they were gross (the sum of the
  losing settlements alone) until a review found the guard would permanently halt a *winning* book:
  100 wins at +$8 against 5 losses at −$120 is +$200 net but $600 gross, tripping a $500 "budget"
  that had not been spent. For a maker collecting a few cents on ~94% of contracts and paying out on
  the rest, gross runs ahead of net continuously, so the old reading would have ended Phase 3 early
  and looked like a failed experiment. `gross_loss` and `net_pnl` are both on `LossState` so the
  distinction stays visible. **This changes what a pre-registered number means** — $500 is now a real
  drawdown ceiling, which is strictly *more* permissive than what was registered on 2026-07-28.
  Tighten it if that is not the intent; do not loosen it further without writing down why.
- **Subaccount routing**, RFQ/quote helpers, combo market creation, the `communications` WebSocket
  consumer, and a 0.1¢-grid price snapper.

### `combo_research/shadow_public.py` — Phase 0
The idea that makes Gate 0 free: **a newly-created KXMVE market *is* an RFQ.** Combo markets are
instantiated on demand when someone builds a ticket, so the RFQ lifecycle is observable without
credentials. The logger watches the live rolling collections, snapshots the underlying leg books at
that instant, and later records whether the combo traded and at what price.

`winning_price − our_model_price` = the room a quoter had.

**It stores raw leg marks, not just a derived price**, so the data can be re-priced under the Gate 3
copula without re-collecting. Single-instance locked, because two copies deadlock SQLite and
silently stop collecting — and a gap in the tape cannot be backfilled.

### `combo_research/archive_settled_combos.py` — the data asset
**Kalshi purges settled combo markets after ~3 months.** Only 2026-05 → 2026-07 exists, and it is
entirely summer MLB/WNBA/World Cup. NFL and NBA — the regimes that actually matter — have never been
captured. **This runs regardless of the P-029 verdict.** The strategy is a hypothesis; the data is
the asset, and it cannot be bought later at any price.

### Scripts
`check_kalshi_auth.py` (read-only credential check; refuses to order) ·
`inspect_settlements.py` (dumps the settlement schema — the diagnostic that caught the blind guard) ·
`setup_subaccount.py` (isolation + proof) · `provision_p029_vps.sh` (idempotent VPS setup).

---

## 4. The measurement, in brief

Full detail in `REPORT_Combo_MM_2026-07-28.md` §4.

| | target zone (2–4 legs, 10–35¢) | whole universe |
|---|---|---|
| last-price basis | +7.20 ¢/ct, CI [+6.28, +8.08] | +2.79 ¢/ct [+2.20, +3.42] |
| **first-print (conservative)** | **+3.17 ¢/ct [+2.78, +3.57]** | +1.23 ¢/ct |
| return on collateral / turn | 3.98% | — |
| losing days | 4.4% | 17.6% |
| decay, 11 weeks | **none** (slope CI straddles zero) | −0.5 ¢/ct per month |

**Four kill attempts, all survived:**

1. **Settlement contamination** — if `last_price` were rewritten at settlement the whole thing is
   circular. It is not: YES-settled combos mean 40.24¢ with only 8% ever printing above 90¢.
2. **Contract-weighting** — a maker earns per contract, not per market. +2.74 ¢/ct [+2.31, +3.19];
   losing days fall 17.6% → 2.9%.
3. **Correlated book** — measured, not assumed. Real: sd at 5,000 contracts is **2.8× independence**
   and mean/sd saturates near 1.09. Survivable, but **size per turn, not per contract.**
4. **Decay** — the aggregate edge *is* being competed away at ~−0.5 ¢/ct per month, and it is **not**
   a composition artefact. But the decay lives entirely in the many-leg sub-5¢ tail. The target zone
   shows no trend.

**Actionable targeting rules that fell out:** the edge peaks at **20–35¢ (+7.9 to +8.9 ¢/ct)**;
**above 60¢ the seller LOSES** (−2.46 ¢/ct); 10+ leg combos have decayed to +0.40 ¢/ct.

### The finding that changes the build cost
Kalshi's CFTC-filed rulebook says RFQ execution enters the **public book** at *lower time priority
than resting orders* — *"Quoter and Requester may in fact never transact a contract between them, if
existing book liquidity exists at the quoted price."* And `rfq_created` is broadcast to **all**
WebSocket subscribers with the legs attached. So the play may be: see the RFQ, price it, rest an
offer, intercept the fill at your own price — collapsing the latency requirement from sub-second to
seconds, with no market-maker approval needed. **Untested; that is Gate 2, and it costs ~$20.**

---

## 5. Why this is not simply the sixth maker kill

The house record is maker/fade **0 for 5** and multi-leg/structural **0 for 5**. Combos are both, so
the prior was applied adversarially throughout. Five mechanical differences:

1. **The fee sign flips.** All 15 combo series are `quadratic` — **the maker pays nothing.** Every
   prior maker kill fought a fee.
2. **The house edge law stops binding.** "Stay in the tails" is a *fee* law. With a zero maker fee
   the quoter can sit at 20–35¢ — where the edge is largest — without paying for it. **First
   strategy examined where the tail rule does not apply.**
3. **The counterparty is structurally disadvantaged**, not incidentally. Retail cannot post limit
   orders on combos; they are price takers by construction, average implied win rate 9% vs 43%.
4. **The edge is measured on fills, not quotes.** P-017A's +13.69¢ was on quotes and died at a 2.2%
   fill fraction. The RFQ winner's curse is already inside this number.
5. **Independent corroboration** — Bloomberg, 2026-07-28: $294M retail combo losses YTD, attributed
   to "professional market makers and algorithmic traders … through automated quoting."

What it still shares with the graveyard: it is a maker strategy. **That is exactly what Gate 1
tests.**

---

## 6. What happens next

Phases are **sequential**; each gates the next. Full detail in `TEST_PLAN_P-029.md`.

| phase | needs | capital | question |
|---|---|---|---|
| **0** shadow *(running)* | nothing | **$0** | is there room between fair value and the winning price? |
| **1** RFQ observer | read-only key | $0 | flow rate, push latency, real reaction budget |
| **2** interception test | write key | ~$20 | do resting orders really beat the RFQ? |
| **3** live micro-quoting | write key | ≤$500 | **fill rate + realised edge** |

### Gate 0 — due ~2026-08-04
```bash
/opt/p029/venv/bin/python3 /opt/p029/combo_research/shadow_public.py \
  --db /var/lib/p029/shadow.sqlite --report
```
- **CONTINUE** if in-zone median margin ≥ **+2.0¢** against a *correlation-adjusted* price, n ≥ 500.
- **STOP** if < **+1.0¢**.
- Between the two: extend one week rather than proceed on a marginal read.

**Interpretation caveats.** The raw statistic is `winning_price − independence_product`, and **part
of any positive margin is genuine leg correlation, not profit** — independence understates a
correlated joint, so a positive number is *expected* even against a perfectly fair quoter. An early
n=39 read showed only **+1.06¢** in-zone, far thinner than the +7.2¢ historical tape. **STOP is a
live outcome and should be reported plainly, not explained away.** Also ignore "trade rate among
resolved" until the run is older than 48h — it is biased upward by construction.

### Before Phase 3
Fund to ~$600 (balance was $291.64); transfer into subaccount 1; run
`setup_subaccount.py --verify`; set `LossGuard(since=<experiment start>)`; bump the VPS to 4 GB.

---

## 7. Operating facts a future session needs

### Infrastructure
- **VPS `143.198.162.120`** (DO NYC1, Ubuntu 24.04, 2 vCPU / 2 GB + 2 GB swap). Code `/opt/p029`,
  venv `/opt/p029/venv`, data `/var/lib/p029`, user `p029`, logrotate daily×14.
- **The Cowork sandbox CANNOT SSH** — port 22 is blocked outbound (`github.com:22` times out too);
  container egress, not a firewall, and no run mode fixes it. **Package work as self-contained
  scripts and have Sam run them**, via the DigitalOcean web console if his Mac has no key installed.
- Memory is tight: 512M + 1G caps against 1,967M, overlapping daily at 09:33 UTC. Caps mean systemd
  kills the cgroup, not the box. **4 GB before Phase 3.**

### Isolation — one account per person
**Kalshi permits only one account per person** and auto-denies further registrations. **Never create
a second account.** Sam's account carries heavy **manual GUI** trading (830 settlements, −$3,449.88
lifetime, 3 open positions) — all manual; nothing from this project has ever traded live.
**Subaccounts (0–63) are the sanctioned isolation, and they are API-only** — invisible to Kalshi's
web and mobile apps, so manual trading cannot reach one. **P-029 uses subaccount 1.** Use
`subaccount` **and** `since=` together, never either alone.

### Kalshi API traps banked this round
- **`/portfolio/settlements` returns NO P&L field.** Derive it:
  `revenue/100 − yes_total_cost_dollars − no_total_cost_dollars − fee_cost`.
  **`revenue` and `value` are integer CENTS; the cost fields are DOLLAR strings.** `value` is the
  per-contract settlement value (max 100 = $1.00) — **never sum it.**
- **`status=open` is the FILTER; rows read `active`.** `"open"` never appears as a status *value*.
- **`/markets?ticker=X` (singular) is silently ignored** and returns the unfiltered list with
  HTTP 200. Use `?tickers=` (plural CSV) — which 414s at ~9,600 URL chars, so **batch by character
  budget**, not count (combo tickers are ~47 chars vs ~30).
- **Combos quote on a 0.1¢ `deci_cent` grid.** Read the tick from `price_ranges`, **not**
  `tick_size` — that returns `None` even on sub-cent markets.
- **`result="scalar"` is a partial payout at `settlement_value_dollars`, never VOID** (56,128 rows).
- `get_collections()` reads the **`multivariate_contracts`** key, not `collections`.
- **`CreateRFQ` has no leg fields** — materialise the combo first via
  `POST /multivariate_event_collections/{ticker}` (capped at 5,000/week).
- Quote create/cancel cost **2 rate tokens**, not the default 10.
- Only **two collections are live at a time** (rolling `-R`, re-opened daily) — re-resolve per cycle.
- **HVM timers contradict**: docs say 3s/1s, the CFTC filing says 2s/3s. Assume the tighter.

---

## 8. Incidents this session, and the rules they produced

**1. Private key leaked into a transcript.** A `sed 's/=.*/=<redacted>/'` over `.env` redacted only
the first line of a multi-line PEM. **Rule: never `cat`/`sed`/`grep` `.env` or any file that may
hold a PEM — ask the user to confirm values instead.** Fixed structurally by moving to
`KALSHI_PRIVATE_KEY_PATH` and deleting the inline variable.

**2. A backup file leaked credentials to the public repo.** `.env.backup-preflight` — created during
that fix — was committed and pushed, because `.gitignore` had `.env`, which matches only that exact
filename. Four keys rotated; purged and verified unreachable; `.gitignore` now has `.env*`.
**Rule: after creating any file next to secrets, run `git check-ignore` before telling anyone to
commit.**

**3. The loss guard was silently blind.** It summed `realized_pnl_dollars`, which Kalshi does not
return, found nothing on all 830 rows, and reported **$0.00** — it would never have halted.
**Rule: dump the real schema from a live account before writing safety-critical code against a
documented field name.** The guard now fails closed when no row is readable.

**4. The archiver would have destroyed the data it protects.** It buffered every row in RAM (18.4M
rows on a 2 GB box), and appended to one gzip file — where a process killed mid-append truncates the
stream and makes the **entire file** unreadable, which systemd restarts do routinely. Now streams,
and writes immutable numbered parts via temp-file + atomic rename. Verified by SIGKILL: all 22
parts / 550,000 rows stayed readable.

**5. The archiver then wedged anyway — the fix moved the memory, it did not remove it** (found
2026-07-29, one day after shipping). Streaming fixed the *write* buffer but dedup still built an
in-RAM `set` per day. Run 1 succeeded only because the archive was empty (`already_present: 0`,
6,677,105 rows). Every run afterwards had to rebuild a 6.7M-entry set inside `MemoryMax=1G` before
writing anything, and could not: the service sat pinned **11 KB under its cgroup cap for four
hours**, read **249 GB against a 462 MB archive**, wrote **zero bytes**, and — because it never left
`activating` — **blocked its own daily timer**, so the archive would have silently stopped updating
with nothing marked `failed`. The archiver worked exactly once, by construction.
Dedup now lives in SQLite (`seen.sqlite`), so memory is flat in archive size: measured **34 MB RSS
vs 1.0 GB**, indexing ~4M tickers in three minutes on the same box. Parts are recorded as indexed
only *after* they are durably written, so a crash re-indexes rather than drops.
**Rules: (a) a wedged service is not a failed one — monitor for `activating` that never ends, not
just for `failed`; (b) when you fix an unbounded-memory bug, check whether the bound moved to
another structure in the same loop; (c) `--days-back` is now 2, not 7 — raising it scales the index
build linearly.** `return 0 if pub.errors == 0 else 0` — both branches identical — meant no archiver
failure could ever reach systemd either; it now exits non-zero on an aborted run.

---

## 9. Open questions

1. **How many quoters answer a typical combo RFQ.** Undocumented; the single most important unknown.
   Phase 1 measures it.
2. **Do incumbents price correlation?** Evidence conflicts; their profitability argues they do.
   Independence understates a correlated 3-leg joint by ~30–35% relative — larger than the entire
   edge — so **a naive independence quoter trades at negative expectancy** against anyone with a
   copula.
3. **Same-game correlation** — unmeasured (n=39; both live collections are cross-slate). Blocked
   until NBA/NFL season, which is another reason the archiver matters now.
4. **Exact collateral on a short combo, and whether any leg netting exists.** Undocumented. Assume
   full `(1 − price)`, unhedged and unnettable; hedging legs *adds* capital.
5. **Push-feed latency** (`rfq_created` → local receipt). No published figures.
6. **Sam's −$3,449.88 lifetime P&L** is explained as manual GUI trading, but has not been reconciled
   against a Kalshi statement. `since=` makes it moot for P-029.

---

## 10. If you are picking this up cold

1. Read this file, then `REPORT_Combo_MM_2026-07-28.md` §4 (the measurement) and §6 (what could
   still kill it).
2. Check whether Gate 0 has been run. If not, run it. **Do not skip to building.**
3. Respect the pre-registered thresholds. They may be **tightened** at any time and **loosened only**
   with written justification and a forward-only sample (the P-022 amendment precedent).
4. The temptation will be to trust the +3.17¢ because it is large and well-measured. It is the
   *incumbent's* realised edge. Ours is unknown until Gate 1 returns a fill rate — and the house rule
   after P-017A is explicit: **never propose a maker variant without a fill estimate first.**
