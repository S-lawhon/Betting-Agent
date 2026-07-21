# Book Capture Daemon — build notes & deployment

**Date:** 2026-07-20
**Status:** built, tested (26 tests), validated against live API — **NOT YET DEPLOYED**
**Files:** `src/book_capture.py`, `scripts/run_book_capture.py`,
`scripts/betting-book-capture.service`, `tests/test_book_capture.py`

## Why

Three workstreams were gated on sub-5-minute book data and **nothing was
scheduled to collect it**:

| Consumer | Gate | Source |
|---|---|---|
| R-EV-MAP Build 3 | 2 weeks of quote capture: fill rates at ±1¢ around mid, post-fill adverse selection, effective spread capture | `05_build_roadmap.md:40`, registry `build3-gate` |
| MLB props Phase 4 | correlation overlay — lags real but underpowered at 5-min sampling | `PHASE2_REPORT.md:52` |
| P-016 repricing lag | sizing the repricing-lag edge | registry `websocket-capture` |

Every night this does not run is calibration data lost forever (same standing
debt as the settled-market archiver, 90-day API history horizon).

## Why REST polling, not websocket

The registry asks for "1-minute **or** websocket". Kalshi's websocket requires
an **authenticated production API key**. This project holds **demo credentials
only** (`.env`, `KALSHI_ENVIRONMENT=demo`, and the file's own TODO says
production creds were never swapped in). Demo does not carry real production
book data. So the 1-minute REST leg is the available option, not a shortcut.

The on-disk schema is deliberately websocket-shaped (per-snapshot rows keyed by
ticker+epoch) so a future authenticated ws feed can write the same records with
no migration.

## ⚠️ The finding that changed the design

Before deploying I measured the host's existing API load. **The API budget is
already saturated:**

| Service | HTTP 429s / 24h |
|---|---:|
| `betting-pod-shop` (main 5-min engine) | **829** |
| `betting-live-maker` (P-016) | **0** |

And it is **escalating with the MLB slate** — hourly counts on 2026-07-20/21:

```
17:00Z   5      22:00Z  50      02:00Z  110
18:00Z   8      23:00Z  71      03:00Z  134   <- peak
19:00Z   9      00:00Z  51      04:00Z   28
```

Peak 429s coincide exactly with peak MLB game time — i.e. exactly when capture
is most active and most valuable. Naively adding 5 req/s would likely have
pushed **P-016 into 429s and contaminated a running pre-registered gate**
(282/500 fills). P-016's client is currently clean; the main engine's is not.

### Root cause: CONCURRENCY, not request rate

**Initial diagnosis (rate) was wrong — corrected after the golf backtest
re-pull measured the real constraint.** Kalshi throttles by **concurrent
connections**, not requests/second:

- 12 threads → constant 429s
- 1 connection at 0.15s spacing (≈6.7 req/s) → **zero** 429s

That explains the table above exactly. `src/multi_executor.py:217` runs
**one thread per pod**:

```python
workers = max_workers or len(pods)
```

`max_workers` is **not set anywhere** in config, so with 6 active pods
(P-001, P-002, P-006, P-014, P-015, P-017) the engine opens 6 concurrent
Kalshi connections every cycle — and at peak MLB each of those threads has a
full slate to scan. P-016 takes zero 429s because it is single-threaded and
sequential.

**The fix is capping `max_workers` (2–3), not slowing anything down.** The
5-minute cycle budget is generous enough to absorb the serialisation. Not
applied here — it changes live scanning behaviour and wants a human call.

This is the "rate-limit-aware async client" infra debt from
`05_build_roadmap.md:65` biting in production, though the roadmap's own
framing ("429s above ~7 req/s") is itself misleading — it is not a rate
threshold. **Live problem, independent of this daemon, nobody tracking it.**

### Consequences for the design

1. **Deployed rate is 2.5 req/s**, not the module default of 5.0 — set
   explicitly in the unit's `ExecStart`. Given the corrected concurrency
   diagnosis this is **over-conservative**: the daemon is single-threaded and
   sequential (one connection, like P-016, which takes zero 429s), so it is
   not the shape of client that triggers throttling. Keeping it low for the
   first deployment anyway, since the host is already unhealthy. **Once a
   clean 24h is observed, raise toward 5.0** and widen coverage to the full
   slate — check `_429s` in the logs first.
2. **Adaptive backoff.** Any 429 halves the daemon's rate immediately (bucket
   emptied, floor 0.5 req/s); clean cycles drift back up at 5%/cycle. Slow
   recovery is deliberate — fast recovery would oscillate against the main
   engine's own retry storms. Capture is the lowest-priority API consumer on
   the box and must yield first.
3. `Nice=10`, `IOSchedulingClass=idle`, `MemoryMax=384M` — capture yields on
   CPU/IO too.

This required a **backward-compatible** optional `on_status` callback on
`KalshiPublic.get()`. Default `None` = byte-identical behaviour for P-016 and
every existing caller; 132 existing kalshi/maker tests pass unchanged.

## No silent caps

The market cap is **derived from the rate budget**, not hand-set:

```
max_markets = floor(rate * cadence * safety / requests_per_market)
```

If discovery exceeds it, the daemon truncates by **lowest 24h volume first**,
logs at WARNING with exact counts, **and writes a `DISCOVERY` record to the
data file** so an analyst reading the capture later sees the coverage gap
rather than assuming completeness. (House rule after the P-017 cap incident;
a silent cap reads downstream as "we covered everything".)

## Validated against live API (2026-07-20)

```
discovered   : 74 markets (KXMLBGAME)
cycle time   : 29s for 74 markets  (well inside the 60s cadence)
snapshots    : 74/74 written, 72 with a two-sided quote, 13 with trade prints
est. volume  : ~36 MB/day raw, ~5 MB/day gzipped
droplet disk : 52 GB free — 2 weeks ≈ 0.5 GB raw. Non-issue.
```

At the deployed 2.5 req/s the cap is 45 markets, which covers a live slate
(15 games × 2 sides = 30) with headroom, prioritised by volume.

## Storage

Append-only JSONL, one file per UTC day, gzipped on rotation.
`data/book_capture/book_capture_YYYY-MM-DD.jsonl`.

Parquet was rejected: the established `weather_depth.py` pattern rewrites the
whole file each run (`pd.concat(read_parquet(...))`), which is O(n²) — fine at
2 snapshots/day, fatal at 1440.

## Deploy (NOT YET RUN — blocked pending approval)

```bash
cd "/Users/samlawhon/Desktop/Betting Fund Project"
scp src/book_capture.py src/kalshi_public.py \
    root@129.212.176.202:/opt/betting-pod-shop/src/
scp scripts/run_book_capture.py \
    root@129.212.176.202:/opt/betting-pod-shop/scripts/
scp scripts/betting-book-capture.service \
    root@129.212.176.202:/etc/systemd/system/

ssh root@129.212.176.202 '
  chown -R bettingbot:bettingbot /opt/betting-pod-shop/src /opt/betting-pod-shop/scripts
  mkdir -p /opt/betting-pod-shop/data/book_capture
  chown bettingbot:bettingbot /opt/betting-pod-shop/data/book_capture
  systemctl daemon-reload
  systemctl enable --now betting-book-capture
  sleep 90 && systemctl status betting-book-capture --no-pager
'
```

Do **not** use `scripts/deploy.sh` for this — it restarts `betting-pod-shop`
and would risk gapping P-016's mid-gate sample. The commands above touch
neither running service.

**Watch after deploy:** `journalctl -u betting-book-capture -f`. If
`HTTP 429 — throttling down` appears repeatedly, the host is more saturated
than measured; stop the unit and fix the main engine first.

**Kill switch:** `touch /opt/betting-pod-shop/data/KILL_CAPTURE` (idles within
one cycle, unit stays up). Full stop: `systemctl stop betting-book-capture`.

## Open / deferred

- **Polymarket mid leg is NOT built.** Build 3's gate wants fill rates around
  *Polymarket* mid. Deferred deliberately: the cross-venue leg depends on
  Polymarket having live MLB regular-season markets, which was unverified when
  this was written. See `research/OPEN_QUESTIONS_2026-07-20.md` Q3 — that
  research confirms Polymarket MLB **is** live now, and notes the real risk is
  ~40¢ top-of-book spreads making basis measurement mostly a measure of
  Polymarket's own spread. Wire the second leg only after reading that caveat.
- **Main engine 429 storm is unfixed** and is the top production finding here.
- Series list is `KXMLBGAME` only. Add prop series (`KXMLBHIT` etc.) for the
  Phase 4 correlation work — but recompute the rate budget first.
