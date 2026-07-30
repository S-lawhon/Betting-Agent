# P-022 — the size screen is an §8.1 change, and the pre-registration says so in writing

**Written:** 2026-07-30, ~14h before the POI26 window opens at 16:00Z.
**Status of the four pre-window blockers:** three are closed. This report is about the fourth one's fix.
**Recommendation: do not deploy `297ce2b` to the droplet before this window. Set `min_top_size: 0` and deploy the rest, or hold the deploy entirely.**

---

## 1. Where the blockers actually stand

| # | Blocker | State |
|---|---|---|
| 1 | §7 cap counts filled collateral only | **Closed.** `7dad433`, 2026-07-28, deployed. Sizing now measures room against `exposure` (filled + resting quotes) and reserves before resting. Verified: `size × per_ct_coll ≤ cap − filled` on all three caps. |
| 2 | Every in-band reference is a one-sided ask | **Investigated and pre-registered.** `8aa1607` + `research/REPORT_P022_OneSided_2026-07-28.md`; stratification filed in `golf_quirks_research/P022_ONESIDED_PREREGISTRATION_2026-07-29.md`. |
| 3 | POI26's close reference is uncalibrated | **Closed by pre-registered exclusion.** `P022_POI26_PREREGISTRATION_2026-07-28.md`. Quote it, discard from T, and `LAG_DAY_H["KXCHAMPTOUR"]` goes n=1 → n=2. |
| 4 | No size or depth screen | **A fix shipped on 2026-07-30 (`297ce2b`) that the 07-29 pre-registration had already ruled out of bounds.** See below. Committed and pushed, **not deployed.** |

So the thing worth an hour before the window is not the collateral cap. It is the screen that was added to close blocker 4.

## 2. The conflict, in the two documents' own words

`golf_quirks_research/P022_ONESIDED_PREREGISTRATION_2026-07-29.md`, §8, filed 2026-07-29 with Sam's sign-off:

> **Also unaddressed by design:** the pod has no size or depth screen, and the size resting ahead of its quote is bimodal (median 13 contracts, max 1,122). **Adding one is an §8.1 change and is not part of this registration.**

The same document's §2, listing what is pinned:

> **No pod parameter changes.** Band `(0.03, 0.12)`, offset `+0.02`, window `[12h, 24h]`, caps `0.5 / 5 / 15 %`, 13 series. **No depth screen**, no `_mid()` change. Nothing here resets T under §8.1.

`research/REPORT_P022_Size_Screen_2026-07-30.md`, written the next day:

> This is a **tightening** of the quoted population — allowed at any time under the pre-registration rules.

Both cannot hold. `P022_DECISION_RULE.md` §8.1 reads "No mid-flight parameter changes. Offset, band, window, series set, caps. Any change resets T to 0 under a new pod ID." The screen's report leaned on the fact that "size screen" is not literally in that five-item list. The pre-registration filed the day before had already answered the question directly, naming a size or depth screen as an §8.1 change by name.

§8.1's reset is about **population identity**, not about generosity. A change that narrows the quoted set changes which markets the 24 tournaments are measured on, exactly as a change that widens it would. Nothing in §8 treats tightening as exempt, and §8.2 forbids the mirror-image move of computing a verdict on a sub-slice, which is what a screen does prospectively rather than after the fact.

## 3. What the screen actually selects, measured on the census already in the repo

`golf_quirks_research/live_book_census_aigwo26.json` (146 markets, AIGWO26 R1, captured 2026-07-28) carries both `top_ask_qty`, which the screen tests, and `size_ahead_of_quote`, which is what has to be swept before a resting sell-YES quote can fill. On the 24 in-band books, 23 of them one-sided asks:

| | n | `size_ahead_of_quote` |
|---|---:|---|
| Passes `min_top_size: 100` | 10 | 801, 801, 801, 801, 801, 801, 801, 811, 812, 1121.5 |
| Refused as thin | 14 | 12 or 13 on all 13 one-sided books; 320 on the one two-sided book (`NELKOR`) |

There is no overlap and nothing in between. The pre-registration named this distribution as bimodal, median 13 and max 1,122. The screen keeps the 1,122 side and discards the median-13 side.

**The threshold value does not matter.** Sweeping it, which the screen's own report notes was not done, gives the identical partition at 20, 50, 100 and 500, because `top_ask_qty` on this population is either 1 or 800-plus with nothing in between. Reproduce with `python3 golf_quirks_research/screen_vs_size_ahead.py --threshold N`. So this is not a calibration question about the number 100. It is a single binary decision about whether to quote the median-13 mode at all, and that is the mode the pod can actually get filled in.

The two quantities also diverge by a median factor of **12×** on the refused books: 1 contract at the top of book against 12 within 2¢ of it. A screen on top-of-book size is not a screen on fill difficulty, and on these books it is not even a good proxy for one.

Restated as fill mechanics: a fill needs a YES-taker print strictly through the quote price, per `_check_fills`. On the 13 refused books that is a sweep of about 12 contracts plus the quote. On the 10 kept books it is a sweep of about 801. **The screen retains the books that are roughly 65× harder to get filled on and discards the ones that are reachable.**

The screen's stated purpose is reference validity, and that concern is real: a 1-contract ask at 4¢ is a thin thing to price a 6¢ quote off. Note though that a phantom-high ask biases a *seller's* quote in the seller's favour, so the phantom risk here is population drift rather than adverse pricing, which is the §8.1 concern again rather than an argument for the screen.

## 4. Why the A/B could not have detected this

The screen's live A/B reports 12 quotes with the screen off and 12 with it on, and correctly explains that the per-tournament collateral cap binds before the candidate list runs out. That is exactly why quote count is the wrong readout: it is pinned by §7 and is insensitive to which books the capital flows to. The quantity that moved is the fill probability of the quoted set, and it was not measured.

This is the P-017A lesson in its original form. That study died at a 2.2% contract fill fraction against a 25% floor, and the standing rule it produced was **never propose a maker variant without a fill estimate first**. The screen changes the fill distribution of the quoted population and ships with no fill estimate.

## 5. The second, independent problem: it breaks the pre-registered diagnostic

The one-sided pre-registration's early readout is a fill rate with a stop-and-report trigger, and it pins the definition deliberately:

> **Denominator note, fixed now:** a "posted market" is a ticker that received ≥ 1 QUOTE row within a tournament … Same definition as `quirks_common.replay`'s `posted_markets` / `filled_markets`, so the live number and the backtest number are the same quantity.

The backtest cells it compares against (67% two-sided, 47% traded one-sided, 15% bare one-sided ask) were computed with no size screen. With the screen on, thin books produce `REFUSED` rows rather than `QUOTE` rows, so they leave the denominator, and the live fill rate is no longer the same quantity as the backtest number the pre-registration pinned it to. The ≤25% stop-and-report trigger would then fire, or fail to fire, on a population it was never defined over.

## 6. Recommendation

**Do not put the screen into this window.** Two ways to get there:

1. **Preferred.** Set `pods.P-022.quoting.min_top_size: 0` in `config_multi_pod.yaml`, which is the explicitly tested disable path, then deploy. The observability that shipped in `8aa1607` stays live, so every QUOTE row still records `book_side`, `yes_bid`, `yes_ask`, `bid_qty`, `ask_qty`. The registered population is quoted, T keeps accruing, and the screen question is answered later from real fill data rather than from a judgment call about the number 100.
2. **Simplest.** Hold the deploy. The droplet is already running the pre-screen code, so the registered configuration is what quotes at 16:00Z. The §7 fix is already deployed, so nothing load-bearing is waiting behind this.

Then decide the screen on the merits, as its own registration. Two things are worth knowing before that decision, and both are cheap:

- **Stratify the first tournaments' fills by `size_ahead_of_quote`, not by `top_ask_qty`.** The census shows the two diverge on exactly the books in question: 1 contract at the top of book against 12 within 2¢. The screen tests the first quantity; fills depend on the second.
- **If a screen is wanted, the case for an upper bound is at least as strong as the case for a lower one**, since 801 ahead of a 5-lot is the shape P-017A died on. That is a new hypothesis and needs a P-022b registration with its own fill estimate, not a config edit.

The screen's own report flags that the 100 was "a judgment call … not swept" and that it gates 62% of the in-band population. Both are true and both understate it: on the AIGWO26 census the gate is not a 62% trim of a continuum, it is a clean cut that keeps only the far mode, and no value between 20 and 500 changes which books survive it.

## 7. What I could not check

Cowork cannot SSH (port 22 blocked outbound), so the droplet's actual running revision is unverified from here. `scripts/p022_droplet_state_check.sh` in this commit prints the running unit's revision, the effective `min_top_size`, and today's REFUSED/QUOTE counts. Run it from the Mac or the DigitalOcean web console before 16:00Z.
