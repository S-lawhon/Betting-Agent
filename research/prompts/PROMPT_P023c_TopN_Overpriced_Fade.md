# Claude Code Task — P-023c: The Over-Priced Top-N Flip-Side (never specced; cheap)

> A negative result nobody turned over. Round 3 measured it, Round 4 recorded it, and no one asked the obvious question.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper-mode; **no real orders, ever**).

P-023's top-N *buy* basket was **KILLED**: tie inflation is real, but it is already priced at the 48h anchor. The kill numbers, though, were not merely "zero":

- **PGA top-N control: −3.2¢/ct**
- **Round-based top-N: −5 to −16¢/ct**

Those are not null results. They say the market **systematically over-prices** these contracts — which is a **fade candidate**, and it is the same shape as the two things that have actually worked here (P-017 harvests a mechanical mispricing; P-022 fades an over-priced tail). It has never been specced or tested.

## The honest prior — state it, then try to kill it
Be sceptical by default. A negative number in a killed buy-side study is **not** evidence of a sell-side edge until it survives all four of these, and this task is mostly about ruling them out:

1. **It may be pure fee/spread drag.** The buy side pays the spread; a mirrored sell side pays it too. If −3.2¢ is just the round-trip cost, the fade is 0¢, not +3.2¢. Compute the fee-and-spread-neutral figure explicitly before anything else.
2. **It may be an anchor artefact.** Verify the 48h anchor was constructed from executed prints or tight two-sided quotes — **bare asks fabricate edges**, and these are thin books.
3. **It may not be fillable.** Fading means resting an offer and waiting for someone to lift it. That is the P-016 adverse-selection trap and it is the reason P-022 needed a Phase 2. A settled-data average does not survive contact with fill realism.
4. **The sell side of a cheap contract is tail-heavy.** Selling a 6¢ name risks 94¢ to win 6¢. P-019 died here; P-022 only survived with hard per-event caps.

## Task
### Phase 1 — settled data (do this alone, then STOP)
1. Recover the P-023 cohort definitions from `golf_quirks_research/REPORT_Golf_Quirks_2026-07.md` §on top-N. Reuse the cached pulls in `golf_quirks_research/data/` — do not re-pull what already exists.
2. Rebuild the buy-side result first as a **replication check**. If you cannot reproduce −3.2¢ / −5 to −16¢, stop and report that — the flip-side question is moot until the base number is trusted.
3. Invert to a sell/make-NO study, computing net of the **actual** maker fee for these series (`src/kalshi_fees.py`; confirm quadratic/maker-zero per series — do not assume).
4. Decompose the negative: how much is tie/settlement mechanics, how much is spread, how much is fee, how much is unexplained? **The fade only has a thesis if a mechanical component survives.** If it is all spread, that is a KILL and it is a cheap one.
5. Segment by anchor horizon (48h / 24h / 12h) and by price band, tournament-clustered. Report where the over-pricing concentrates — if it is only in one band at one horizon, treat that as noise-fitting unless there is a mechanical story for it.

### Phase 2 — fill realism (ONLY if Phase 1 clears)
Reuse the P-022 tick-replay harness with sides flipped (see `PROMPT_P023_MakeCut_Phase2.md` — the harness must be rebuilt first; coordinate so it is built once and shared). Strictly-through fills only, adverse-selection diagnostic E[settlement | filled] vs E[settlement | posted], tournament-clustered bootstrap, leave-one-out.

## Gate
- **KILL** if the over-pricing is explained by spread + fees, or if it does not survive tournament-clustered CIs, or if it lives in only one band/horizon with no mechanical story.
- **ADVANCE to Phase 2** only if a mechanical component survives net of realistic costs.
- **ADVANCE to spec** only if Phase 2 clears ≥ +2¢/ct with a tournament-clustered CI excluding zero.

## Definition of done
`golf_quirks_research/REPORT_TopN_Overpriced_2026-07.md` committed with: the replication check, the four-way decomposition, the segmentation table, and an explicit KILL / ADVANCE verdict that directly addresses all four sceptical priors above. **No pod, no config, no service.** The honest expectation is that this dies at the decomposition step — and that is a good, cheap outcome.
