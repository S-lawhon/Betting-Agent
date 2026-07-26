# Claude Code Task — P-027: ECONSTAT Prelim-vs-Final Settlement Flips

> Background: `Deep Research R4 - Terms Census & Chart Markets 2026-07.md` §3. Settled-data study only, then STOP.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper-mode; **no real orders, ever**). Verified rule (ECONSTAT.pdf, quote it in your REPORT after fetching `https://kalshi-public-docs.s3.amazonaws.com/contract_terms/ECONSTAT.pdf`): markets settle on **"the first non-preliminary release"** of the statistic, while Last Trading Time is ~1 minute before the *release the market trades on*. For statistics with a **labeled preliminary/flash print** (UMich consumer sentiment prelim→final, S&P flash PMIs, foreign flash stats), price discovery happens on a number that is NOT the settlement value. Near-boundary strikes priced ≥90¢ off a flash within one revision-sigma of the strike are mechanically overpriced. **Screen precisely:** stats with no labeled preliminary (CPI, NFP, GDP advance) settle on their first print — no quirk; EXCLUDE them.

## Task
1. Enumerate the 148 ECONSTAT-template series (census cache exists at `/tmp/terms_census.json` in the research session — else regroup via `GET /series` per category matching `contract_terms_url` ending `ECONSTAT.pdf`; also check `ECONSTATTE.pdf`, 45 series). Classify each: does its source agency publish a labeled preliminary? Keep only those (expect UMich, flash PMI, foreign prelim CPI/GDP like KXITCPIPREL).
2. For each settled market in the kept set: record `settlement_value_dollars`, strike, and reconstruct BOTH the preliminary print and the final print from the source agency's published history (UMich and S&P PMI histories are public; foreign stats via the agency or FRED/TradingEconomics — cite exact sources per stat, don't fabricate).
3. **Count bracket flips:** cases where the preliminary print and the settlement (non-preliminary) print fall on opposite sides of the strike. Compute flip rate overall and vs distance-to-strike in revision-sigmas.
4. **Price test:** for flipped and near-boundary markets, pull the last pre-close trade/candle price. Did the market price the flip risk (e.g., 92¢ on a strike the final print reversed)? Simulated fade PnL net of taker fee, **clustered by release event**.
5. Honest capacity note: these books are thin ($13–55 depth family); report observed traded volume on the studied markets.

## Gate
- **ADVANCE** if flip rate on near-boundary strikes ≥5% AND last-trade prices show no flip discount (fade edge ≥2¢/ct net, release-clustered CI excluding zero).
- **KILL** if flips are rare or already priced. **MARGINAL** → note which specific stats carry the edge and the per-release recurrence.

Deliver `econstat_research/REPORT_ECONSTAT_Flips_2026-07.md` + `p027_params.json` + cached pulls under `econstat_research/data/`. **No pod. STOP at the REPORT.**
