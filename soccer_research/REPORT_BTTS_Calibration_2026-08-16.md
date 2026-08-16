# BTTS — there is no favourite-longshot bias, and two of my own diagnostics were wrong

**Supersedes the mechanism claims in** `REPORT_BTTS_Decay_2026-08-16.md` **and**
`REPORT_BTTS_GateP_2026-08-16.md`. Same cached data, offline.

---

## VERDICT: the market is CALIBRATED. **P-019 is CONFIRMED, not contradicted.**
## Nothing here is an edge. No FLB strategy exists to build.

## 1. The clean diagnostic

Every open in-play minute with a two-sided quote, bucketed by the
**contemporaneous** mid — no conditioning on the price path, no fill selection.
Both arms pooled, 2,900 markets.

| mid bucket | minutes | realized YES |
|---|---:|---:|
| [0.00, 0.15) | 32,732 | **9.1%** |
| [0.15, 0.25) | 56,678 | **17.9%** |
| [0.25, 0.40) | 59,444 | **31.4%** |
| [0.40, 0.60) | 152,589 | **51.5%** |
| [0.60, 1.01) | 49,480 | **69.5%** |

**Price tracks outcome to within a point or two at every level.** The
fill-conditioned sample matches it (10.1 / 16.3 / 29.5 / 49.2 / 70.6%), so the
fill rule is not selecting favourably either.

Per-fill EV against our own offer, contemporaneous:

| offer bucket | fills | mean offer | realized YES | EV/ct | half-spread − 1¢ |
|---|---:|---:|---:|---:|---:|
| [0.00, 0.15) | 3,266 | 0.103 | 10.1% | **+0.18¢** | +0.70¢ |
| [0.15, 0.25) | 7,908 | 0.192 | 16.3% | +2.90¢ | +0.94¢ |
| [0.25, 0.40) | 8,547 | 0.324 | 29.5% | +2.88¢ | +1.40¢ |
| [0.40, 0.60) | 17,533 | 0.495 | 49.2% | +0.29¢ | +1.47¢ |
| [0.60, 1.01) | 8,606 | 0.724 | 70.6% | +1.82¢ | +5.26¢ |

Small, positive, **non-monotone**, and of the same order as the half-spread
less a tick. **That is spread capture — ordinary market making — not a
longshot mispricing.** The cheapest bucket, where FLB predicts the largest
overpricing, earns the least.

## 2. Two errors of mine, both in the same direction

**(a) The "monotone price-level effect" (Gate P report §1) does not exist.**
I bucketed *markets* by their **path-average fill price**. Prices converge to
the outcome, so a market with a low average price is disproportionately one
that settled NO. **Bucketing that way sorts markets by their result** — it is
conditioning on the future, and it manufactured both the +17¢ (sell cheap) and
the +37¢ (buy expensive, z = 25) figures. A z of 25 on a 50¢ contract should
have been treated as an alarm, not a finding, the moment it appeared.

**(b) The +4.52¢ headline is an aggregation artifact of the same family.**
Equal-weight-per-match over *mean P&L per fill* mixes the true per-fill EV
(≈ +1 to +3¢) with outcome-driven path structure — a YES match contributes a
large negative per-fill number, a NO match a large positive one, and matches
carry wildly different fill counts. The contemporaneous per-fill EV in §1 is
the honest statistic and it is several times smaller.

**Gate P's arithmetic still stands** (first-half +4.52¢ vs full-match −2.36¢,
disjoint CIs); **my explanation of it did not.** Since per-fill EV is positive
in both arms at every price level, the arm-level gap is an artifact of
aggregation, not a directional edge in either.

## 3. The P-019 collision, resolved

It resolves **in P-019's favour, on independent data.** P-019 killed FLB on
29,476 settled contracts in politics and sports season-futures; this is in-play
soccer BTTS, a universe it never touched, and the calibration curve is flat
here too. Two independent universes, same answer.

**"We price it better than Kalshi" is now 0 for 9.**

## 4. What is actually left, and why it is the trap this fund knows

A per-fill EV of roughly **+1 to +3¢ from resting one tick inside the ask on a
calibrated market.** That is the maker's half-spread, and this fund has
measured it before:

* **maker/fade is 0 for 6**, and
* **P-017A measured its edge correctly to within 0.05¢ and died at a 2.2% fill
  rate.**

Nothing in this harness models queue position. It assumes a fill whenever price
prints strictly through an offer that improves the best ask by a tick — i.e. it
assumes we are always at the front. Real queueing can only reduce the 15%
fill rate, and the standing rule is that the cost of *being filled only when
you are wrong* is the largest term every time it is measured.

**Recommendation: do not build a favourite-longshot strategy. There is no
favourite-longshot bias to harvest.** Closing `R-SOCCER-BTTS-1H`.

## 5. What survives, unaffected

1. `SOCCERBTTS.pdf` ≡ `SOCCERHBTTS.pdf` — byte-identical, one rulebook.
2. Full-match BTTS's payout criterion is **monotone**.
3. **`close_time` on 1H BTTS is outcome-dependent** (YES closes at the second
   goal, NO at half-time).
4. **No in-play capture is needed for soccer** — minute candles are
   retrospective.
5. **Kalshi in-play soccer BTTS is well calibrated across the full price
   range.** That is a genuine, reusable measurement, and it is a *negative*
   result worth as much as the others: it forecloses every "we price this
   better" variant on this surface.
