# The live fee bill, by pod — 2026-07-28

**Source:** production droplet `129.212.176.202`, `/opt/betting-pod-shop/data/trade_logs/`
**Harness:** [`fee_bill_by_pod.py`](fee_bill_by_pod.py) · **Raw:** [`fee_bill_2026-07-28.json`](fee_bill_2026-07-28.json)
**Origin:** the companion task deferred by
`golf_research/REPORT_P017_Maker_2026-07-30.md` §8 — it needed the deploy key.

Computed against the **deployed** `src/kalshi_fees.py`, not a local copy, so the
fee model is exactly production's. Read-only pull.

---

## 0. Read this first

> **Every record is `mode: paper` — 3766 of 3766. No money was paid.**
> This is a *notional* bill: what the book would have cost had it traded live at
> the sizes it logged.

> **And "recoverable" is an upper bound on an opportunity, not a P&L line.**
> The study that prompted this one measured a real, correctly-sized fee
> advantage (+2.62¢/ct) and found it **completely unreachable** — you fill 2.2%
> of what you quote. The $1,487.75 below is what *perfect costless making*
> would have saved. For the one pod where that was actually tested, the
> realisable share was zero.

---

## 1. The bill

Kalshi only, `action == PLACED`, deduped by fingerprint, 2026-03-04 → 07-28.

| pod | trades | contracts | notional | **taker fee** | ¢/ct | VWAP | fee % of notional | window |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| P-001 | 2233 | 75,181 | $15,661 | **$597.01** | 0.794 | 0.208 | 3.81% | 04-03 → 07-28 |
| P-002 *(Kalshi leg)* | 978 | 36,092 | $12,984 | **$501.69** | 1.390 | 0.360 | 3.86% | 04-01 → 07-21 |
| `?` *(pre-`pod_id`)* | 417 | 38,216 | $12,715 | **$444.54** | 1.163 | 0.333 | 3.50% | 03-04 → 04-01 |
| P-013 *(killed)* | 89 | 62,628 | $5,485 | **$261.93** | 0.418 | 0.088 | 4.78% | 03-29 → 03-31 |
| P-017 | 44 | 3,187 | $398 | **$23.71** | 0.744 | 0.125 | 5.95% | 07-21 → 07-27 |
| P-015 | 5 | 78 | $70 | **$0.46** | 0.597 | 0.904 | 0.66% | 07-25 |
| **TOTAL** | **3766** | | | **$1,829.34** | | | | |

**Maker side** — `maker_fills.jsonl`, the standalone engines that never write to
`trade_log`:

| pod | fills | contracts | notional | maker fee | ¢/ct | VWAP | series charges? |
|---|---:|---:|---:|---:|---:|---:|---|
| P-016 | 691 | 5,774 | $3,096 | **$20.58** | 0.356 | 0.536 | **yes** (`KXMLBGAME`) |

123 shadow fills excluded.

**Excluded — not Kalshi:**

| | trades | notional | VWAP |
|---|---:|---:|---:|
| P-002 @ polymarket | 978 | $12,984 | 0.265 |
| P-006 @ polymarket | 1905 | $29,028 | 0.428 |

**P-006 trades Polymarket exclusively and therefore has no Kalshi fee bill at
all.** P-002 is an arb: half its legs are Kalshi, half Polymarket.

---

## 2. The brief's premise does not survive measurement

The originating brief asserted that P-001 and P-015 "trade near the fee peak on
maker-**charging** series (tennis match markets: VWAP 0.519, taker 1.156¢/ct)".

| series | measured VWAP | measured ¢/ct | maker charges? |
|---|---:|---:|---|
| `KXATPMATCH` | **0.213** | 0.835 | yes |
| `KXWTAMATCH` | **0.291** | 1.109 | yes |

**Neither is near 0.519.** The fee is `0.07·P·(1−P)`, peaking at P=0.50 and
1.75¢/ct. P-001's book-wide VWAP is **0.208**, paying **0.794¢/ct** — it trades
cheap longshots, structurally *away* from the peak, at under half peak cost.
P-015 is five trades and $0.46; nothing about it is material either way.

The pods that genuinely sit near the peak are **P-016** (VWAP 0.536 — and it is
already a maker, paying 0.356¢/ct) and **P-002's Kalshi leg** (VWAP 0.360,
1.390¢/ct). If there is a fee-peak problem in this book, it is P-002's, not
P-001's.

*(The measured ¢/ct is below `fee(VWAP)` throughout because the fee is concave
in price: the contract-weighted mean of `0.07·P·(1−P)` is less than
`0.07·V·(1−V)` at the weighted-mean price V. Both numbers are reported so the
gap is visible rather than looking like an error.)*

---

## 3. What making instead of taking would recover

| pod | taker | maker-if-made | **recoverable** | % | on maker-free | on maker-charging |
|---|---:|---:|---:|---:|---:|---:|
| P-001 | $597.01 | $132.53 | **$464.48** | 77.8% | $66.88 | $530.13 |
| P-002 | $501.69 | $125.26 | **$376.43** | 75.0% | $0.65 | $501.03 |
| `?` | $444.54 | $83.68 | **$360.86** | 81.2% | $109.81 | $334.73 |
| P-013 | $261.93 | $0.00 | **$261.93** | 100% | $261.93 | $0.00 |
| P-017 | $23.71 | $0.00 | **$23.71** | 100% | $23.71 | $0.00 |
| P-015 | $0.46 | $0.12 | **$0.35** | 75.0% | $0.00 | $0.46 |
| **TOTAL** | **$1,829.34** | **$341.59** | **$1,487.75** | **81.3%** | | |

The split is exact by construction: maker-free (`quadratic`) series recover
**100%**, maker-charging (`quadratic_with_maker_fees`) recover exactly **75%**,
because the maker coefficient (0.0175) is precisely ¼ of the taker
coefficient (0.07).

**Where the money actually is:** `KXMLBGAME` alone is **$614.75** — a third of
the entire bill — at 1.304¢/ct on VWAP 0.359. That is P-001's MLB book plus
P-002's Kalshi leg. Tennis (`KXATPMATCH` + `KXWTAMATCH` + `KXATPSETWINNER`) is
**$476.91** across 50,024 contracts. `KXBTCD` is $244.35 but at only 0.415¢/ct
— it is 58,922 contracts of 8.7¢ longshots, i.e. volume, not rate.

Top 15 Kalshi series by fee paid are in the JSON under `by_series_kalshi`.

---

## 4. Four traps in this data

Anyone redoing this will hit all four. They are encoded in the harness.

1. **Records carry no fee field.** The bill must be *computed*:
   `contracts = position_size_usd / fill_price`, then
   `fee_per_contract(fill_price, maker, series) × contracts`.
2. **Most files in `data/trade_logs/` are snapshots, not history.** Only
   `trade_log.jsonl`, `trade_log.archive_*.jsonl.gz` and
   `archive/2026-*.jsonl.gz` are distinct; the `.bak*`, `*pre-compact*` and
   `*pre_cleanup*` files repeat those same records and would multiply the bill
   several times over. Fingerprint dedup is the second line of defence.
3. **The live `trade_log.jsonl` holds almost nothing** — 73 of 3766 Kalshi
   PLACED trades at time of writing, because rotation moves history out.
   Reading it alone understates the bill by ~98%. (Same rotation behaviour
   that orphaned 177 open positions previously.)
4. **41% of PLACED trades are Polymarket and must not be charged Kalshi fees.**
   The `venue` field is authoritative — it **disagrees with a "ticker starts
   with `0x`" heuristic on 233 rows**. Rows with no `venue` field all carry
   `KX*` tickers and belong to P-001 / pre-`pod_id` records, so they default to
   kalshi. *The first version of this analysis omitted this gate and charged
   Kalshi fees to P-006, which trades Polymarket exclusively — the error was
   caught before any number was reported.*

---

## 5. Defect found: P-014 records no fill price

**All 356 of P-014's PLACED trades carry `fill_price: null`**, spanning
**2026-03-28 → 2026-07-28** — the pod's entire life, still occurring today. No
other pod has this.

Consequence: P-014's fee cost is **uncomputable** (no price → no
`0.07·P·(1−P)`), and so is its contract count (`position_size_usd / fill_price`
is undefined), and therefore every per-contract metric. It is the only pod
excluded from this bill, and it is excluded silently unless you look at
`excluded_rows_by_pod`.

`src/trade_log_schema.py` lists `fill_price` as a **required** field, so
validation is accepting `None` on a required field — 356 times, over four
months. Filed as a separate task; it is an observability fix, not a trading
change.

---

## 6. Reproduce

```bash
scp fee_audit_research/fee_bill_by_pod.py root@129.212.176.202:/tmp/
ssh root@129.212.176.202 "cd /opt/betting-pod-shop && python3 /tmp/fee_bill_by_pod.py"
```

Add `--json` for the machine-readable form (what
`fee_bill_2026-07-28.json` holds). The script is read-only and takes
`--repo-root` / `--data-dir` to run anywhere; note the **local** trade log is
from 2026-03-13 and will not reproduce these figures — that staleness is why
this had to be a remote pull.
