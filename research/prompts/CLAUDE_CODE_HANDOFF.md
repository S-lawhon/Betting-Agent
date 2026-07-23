# Claude Code Handoff — Round-2 Test Candidates (P-021, P-020)

Two independent workstreams, each a **backtest-first** test of a new EV candidate. Both follow the house discipline: prove the edge on settled data with event/day-clustered stats BEFORE building any pod (this is how P-019 was killed cheaply).

## How to use
Each `PROMPT_*.md` is a self-contained, paste-able Claude Code task. Open one, paste it into a fresh Claude Code session in the repo (`~/Desktop/Betting Fund Project`), and let it run **Phase 1 only**, then review the REPORT before approving Phase 2. Run them in **separate sessions** — they're unrelated and each should stay focused.

The `PROMPT_*.md` files are the kickoff; the full detail lives in the matching `SPEC_*.md`.

| Order | Prompt | Spec | What it tests | Uses infra |
|---|---|---|---|---|
| **1st** | `prompts/PROMPT_P021_MLB_Totals.md` | `SPEC_P021_MLB_Totals_Sharp_Consensus.md` | Kalshi maker-free MLB totals/run-line vs sharp-book (Pinnacle-eu) consensus — the P-001 archetype extended | The Odds API (already paid), `devig.py`, `cross_venue_matcher.py` |
| **2nd** | `prompts/PROMPT_P020_CrossVenue.md` | `SPEC_P020_CrossVenue_Signal.md` | Kalshi politics/world price vs Polymarket oracle (read-only), CLV-gated taker signal | `polymarket_client.py` (reads), `cross_venue_matcher.py`, `collect_inplay_basis.py` template |

**Suggested order:** run **P-021 first** — it's the most infra-ready (Odds API we already have) and the closest extension of the one edge that works. P-020 second (highest capacity, but must beat our own prior tight-corridor finding).

## The one rule that matters
Every prompt ends Phase 1 at a **KILL-or-ADVANCE gate** with a committed REPORT. Do not let it build a pod, collector, or systemd unit until you've read that REPORT and approved. The expectation — given our track record — is that at least one of these dies at the gate, and that's the system working.
