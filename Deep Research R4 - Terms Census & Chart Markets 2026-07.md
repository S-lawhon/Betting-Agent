# Deep Research Round 4 — The Terms Census, Chart Markets, and Stat-Leader Splits

**Betting Pod Shop · Strategy research**
**Prepared July 24, 2026 (overnight run)**

---

## Where the pipeline stands, and why this round hunted where it did

Since round 3, the golf-quirk gates ran and delivered the fund's **first fully-validated new edge since P-017**: **P-022 (round-leader dead-heat fade) passed both phases** — settled-data ADVANCE (+4–6¢/ct, CI excluding zero) *and* the tick-replay fill-realism study (+3.4¢/ct net of measured adverse selection, 16/19 tournaments, leave-one-out robust). It is green-lit for a paper pod, awaiting your approval. **P-023 split**: the top-N basket was killed (the inflation is real but priced), while **PGA make-cut ADVANCED** (+4.8¢/ct, 10 tournaments) and LIV/DPW remain marginal on 2–4 events. **P-020 (Poly→Kalshi) still has not been run** — fourth reminder; it remains the highest-capacity untested candidate on the board.

The meta-lesson driving tonight's hunt: **the methodology is now a production line** — rulebook audit → settled-count test → price-vs-value gate → tick-replay fill realism. One reading of golf contract terms yielded two survivors. So round 4 ran a **census of Kalshi's entire contract-terms universe** (10,555 series across 2,767 distinct templates, grouped via each series' `contract_terms_url`) and deep-read the highest-potential unexplored templates, alongside one external-reference sweep aimed at the largest unwatched entertainment family. Twenty-one template PDFs were fetched and read verbatim; nothing below rests on a guessed rule.

---

## Ranked opportunity map

| # | Opportunity | Mechanism | Evidence status | Timing | Verdict |
|---|---|---|---|---|---|
| **1** | **Music-chart mid-week edge** (P-025): KXTOPSONG/KXTOPALBUM vs HITS mid-weeks + kworb + ToTC | External info in an unwatched corner: outcome ~knowable days before books close | **Live 7–15¢ gaps verified on settled candles; reference 52/52 on final #1 calls; live tell trading now** | Weekly, all year | **Backtest prompt attached** |
| **2** | **Season stat-leader tie fade** (P-026): KXLEADER MLB wins / NFL INTs / NFL rush-rec TDs | $1/n dead-heat split (same certified rule as P-022); NFL INT tie rate **47%**, MLB pitcher wins **30%** | Rule verified live in market rules; 30-season tie tables computed from primary sources | **MLB wins resolves ~Oct 15 — 12 weeks out** | Position + falsify at settlement |
| **3** | **ECONSTAT prelim-vs-final flips** (P-027) | Certified rule: settles on "first NON-preliminary release" while trading happens on the flash print (UMich, flash PMI) | Rule verified; flip frequency unmeasured | Monthly releases | Settled-data study |
| 4 | **Award "tie" strikes + favorite haircut** (GLOBES/CRITICS/AMAS) | Verified rule: a tied winner pays **ZERO** (the "tie" strike wins instead) — retail treats tie strikes as dead | Rule verified; ~300 series; small per-category | Awards season | Satellite census |
| 5 | RT/RTTV non-partition ("all resolve No" fallback) + Monday-10AM read-time drift | Verified rules; NO-side convexity on fringe titles; score drift vs opening-weekend anchor | Rules verified; magnitude unmeasured | Ongoing (308 series) | Satellite census |
| 6 | WINS/EXACTWINS partition dutch-book scanner | Same underlying across ladder + exact partitions; thin books can violate coherence | Templates verified identical | NFL lists now | Cheap live scanner |
| — | Olympics "most golds" tie contradiction | The terms PDF contradicts itself ($1/n vs No) — a written-clarification item, not a trade | Contradiction verified in PDF; **series status needs checking** (Milano-Cortina was Feb 2026) | — | Escalate to Kalshi support |
| — | ACHIEVEMENTS cancellation → last-traded-price | Manipulation surface in thin books | Rule verified | — | Surveillance rule, not a position |
| — | Advance-type series $1/n risk | **Cleared** — verified settled UCL advance markets pay each qualifier $1.00 in full | — | — | Non-issue (good news) |

---

## #1 — Music-Chart Mid-Week Edge (P-025)

The best-shaped external-information candidate since P-001, because both halves of the house law are verified rather than hypothesized.

**Why the price is wrong:** the Billboard #1 album/song is largely *knowable days before Kalshi's books close*. The tracking week ends Thursday; Kalshi markets stay open until **Sunday 23:59 ET** and Billboard announces ~Tuesday. Public references: **HITS Daily Double's Midweek 20** (free, scrapeable, dated archives), **kworb.net** daily Spotify data (free, archived), and **Talk of the Charts**, whose self-audit shows **52/52 correct final #1 predictions** over the last year (76% on *early* mid-week calls in contested weeks). **Why nobody's fixed it:** the counterparty is stan retail — dozens of 1–5¢ lottery strikes per event carry real volume — plausibly the most price-insensitive flow on the platform.

**Verified in-sample, not hypothesized:** settled candlesticks show winners trading **85–93¢ mid-tracking-week** (after HITS data existed) and **87–89¢ even after the tracking week closed** — 7–15¢ captured by anyone reading free public data, against a 0.6–1¢ taker fee. And a live tell as of tonight: `KXTOPSONG-26AUG08-CHO` at **85/88** while the song has been #1 on Spotify for **68 straight days at +31% over #2**.

**Honest bounds:** capacity is the binding constraint — roughly **$1–3k/week deployable, ~$100–300/week EV**, scaling maybe 2–3× across the Spotify-threshold and album-debut series that share the same scraper stack. The loss weeks are surprise bundle/variant drops (the 5 early-call misses), which cost 80¢+ — so the strategy restricts to weeks where the mid-week margin is wide, and sizes to a ~1-in-5 contested-week miss rate. Durability: 6–18 months before Kalshi-native chart-watchers compress the mid-week window; the post-close window already shows partial pick-over (some weeks print 99¢ by Saturday). The backtest + a $0 six-week forward log (attached prompt) settle it.

## #2 — Season Stat-Leader Tie Fade (P-026)

P-022's validated mechanism — the $1/n dead-heat split, **confirmed verbatim in live KXLEADER market rules** — pointed at season stat-leader markets, with the tie frequencies now computed from 30 seasons of primary-source data rather than assumed:

| Stat | Tie rate (30 seasons) | Verdict |
|---|---|---|
| **NFL INT leader** | **47%** (incl. a 5-way) | Prime target |
| **MLB pitcher wins** | **30%** (= golf's split rate) | Prime target — **resolves ~Oct 15** |
| NFL rushing / receiving TDs | 20% each | Targets |
| MLB HR / RBI / saves; NFL passing TDs | ~7% | Marginal |
| NFL sacks (half-sacks), MLB SB/Ks, yardage, rate stats, NBA per-game | ~0% | Never fade on tie logic |

A December NFL INT co-leader priced at 40¢ is worth ~30¢ under the split; a visible late-September MLB-wins dead heat carries a 25–35% conditional haircut (**5–10¢ on 20–35¢ names**). Today's MLB wins book is the classic setup: six names fragmented at 7–20¢. Two structural cautions from the memo, both worth respecting: correlation is total within a stat-year (one tie event per market), and there are only ~6–8 independent stat-seasons per year — so cap at 1–2% of bankroll per stat-season and prefer resting NO bids over crossing 3–7¢ spreads. The elegant **$0 pre-trade test**: in any final-week dead heat, sum the co-leaders' YES mids — if they sum to ≳100¢, the market is pricing $1 payouts and ignoring the split; trade. And the free falsification lands **Oct 15**, when 14 MLB leader series produce the first-ever KXLEADER settlements: any tie prints `settlement_value_dollars` = $0.50/$0.33 (confirms) or $1.00 to a declared winner (falsifies).

## #3 — ECONSTAT Prelim-vs-Final Flips (P-027)

A genuinely new quirk from the template audit, verified verbatim: ECONSTAT markets (148 series) settle on *"the first **non-preliminary** release"* while trading closes one minute before the *flash/preliminary* print — so for statistics with a labeled preliminary (UMich sentiment, S&P flash PMIs, some foreign stats), the market's entire price discovery happens on a number that is not the settlement value. Near-boundary strikes priced 90¢+ off a flash that sits within one revision-sigma of the strike are mechanically overpriced. The screen is precise: **only stats with a labeled preliminary qualify** (CPI/NFP first prints govern — no quirk there). Settled-data study: join settlement values against both prints, count bracket flips, compare to last-trade prices, cluster by release.

## #4–6 — Satellites and scanners

The **award tie regime** find is small but wide: GLOBES/CRITICS/AMAS certified terms say a *tied* winner's strike pays **No** — the separate "tie" strike wins — so retail-dead tie strikes are structurally underpriced and favorite YES carries a ~0.5–1.5%/category haircut across ~300 series (note: OSCARS/EMMYS/GRAMMY templates are *silent* on ties — that's un-modelable discretion, not edge; skip those). The **RT family** (308 series) has NO-side convexity (no-score → *all* strikes resolve No, including "below 50") plus a Monday-10AM read-time that differs from the opening-weekend score retail anchors on. The **WINS partition scanner** is a cheap standing internal-consistency check now that NFL season series are listing. All three are census-style settled-data studies an agent can run in an afternoon each; none is a fund-mover alone.

---

## Recommended sequence

1. **Approve/queue the P-022 paper pod build** — it has passed everything; the build spec is already in `golf_quirks_research/P-022_Fade_Pod_Spec.md`. (Mind the flagged settler fix: round-leader `scalar` = dead-heat payout, not a void.)
2. **Ship the P-025 music-chart backtest prompt** (attached) — the strongest new find, testable on settled data + a $0 forward log.
3. **Run P-023 make-cut Phase 2** (fill-realism replay — reuses the P-022 harness nearly unchanged) and, still, **the P-020 backtest**.
4. **Position for P-026**: MLB wins resolves in 12 weeks; the falsification is free at settlement, and the pre-trade sum-of-mids test costs nothing meanwhile.
5. Queue P-027 and the satellite censuses behind those.

The asymmetry note from round 3 holds and strengthens: this round's top candidates arrive with rules quoted from certified terms, tie tables computed from primary sources, and — for P-025 — mispricing already visible in settled candles and one live quote. The gates will still kill some of them; that remains the system working.

---

## Sources

- Kalshi contract-terms PDFs (21 read verbatim): ACHIEVEMENTS, NFLWINS/NFLEXACTWINS/MLBWINS, RT/RTTV/METACRITIC, OSCARS/GRAMMY/EMMYS/GLOBES/AMAS/ACMA/CRITICS, WOLYMPICSEVENTFINISH, SOCCEREXACTSCORE/SOCCERADVANCE, QUARTERKPIS/ANNUALKPI, ECONSTAT, BILLBOARDPEAK, CYCLINGACHIEVEMENTS, RANKLISTBILLBOARDCHARTS — under `https://kalshi-public-docs.s3.amazonaws.com/contract_terms/` and `https://assets.kalshi.com/contract_terms/`
- Kalshi public API (terms census over 10,555 series / 2,767 templates; live quotes; settled candlesticks), pulled 2026-07-23/24 — https://api.elections.kalshi.com/trade-api/v2
- HITS Daily Double Midweek 20 (free, dated archives) — https://www.hitsdailydouble.com/charts/midweek-20
- kworb.net US Spotify daily (free, archived) — https://kworb.net/spotify/country/us_daily.html
- Talk of the Charts accuracy self-audit (52/52 final #1s; timestamped receipts) — https://talkofthecharts.org/chart-notes/auditing-1-year-of-hot-100-predictions
- Tie-rate primary sources: MLB/NFL annual-leader lists (Wikipedia, cross-validated identical vs Baseball-Reference year-by-year leader pages); Rocket Richard no-tiebreaker language — links in `research/` agent memos
- Internal: `golf_quirks_research/REPORT_Golf_Quirks_2026-07.md` + `REPORT_Golf_Quirks_Phase2_P022_2026-07.md` (the validated production line this round generalizes)

*Evidence-quality flags: ToTC accuracy is a self-audit (timestamped and re-auditable — the attached prompt re-grades from originals). The Olympics "most golds" contradiction is real in the PDF but the live-series status needs verification (Milano-Cortina ran Feb 2026); treat as a written-clarification item. All edge sizes for P-025/P-026/P-027 are [reasoned estimates] pending their gates; the P-022/P-023 numbers are [validated internal].*
