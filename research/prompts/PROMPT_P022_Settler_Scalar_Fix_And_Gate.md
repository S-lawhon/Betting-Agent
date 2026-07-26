# Claude Code Task — P-022: Settler Scalar Fix + Pre-Registered Gate (BUILD IS **NOT** APPROVED)

> **Read this line twice: you are NOT building the P-022 pod in this task.** Sam has approved the settler fix and the gate registration only. The pod build is a separate, later approval. Do not create `src/pods/`, config `pods.active` entries, or systemd units.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper-mode; **no real orders, ever**).

P-022 (Round-Leader Dead-Heat Fade) is **green-lit** — the first fully-validated new edge since P-017. Phase 1: round-leader ties split payout $1/n (verified verbatim in `PGAROUNDLEADER.pdf`); 30% of golf rounds tie → 37% conditional payout haircut; fading the 5–10¢ band earns +4–6¢/ct. Phase 2 tick replay: **+3.4¢/ct at offset +0.02**, tournament-clustered CI **[+1.7, +5.1]**, **16 of 19 tournaments positive**, leave-one-out robust, surviving measured adverse selection. Reports: `golf_quirks_research/REPORT_Golf_Quirks_2026-07.md` and `REPORT_Golf_Quirks_Phase2_P022_2026-07.md`; spec at `golf_quirks_research/P-022_Fade_Pod_Spec.md`.

Two things must land before a single P-022 contract can be trusted.

## Part 1 — THE SETTLER BUG (this is the real gating item)

`src/kalshi_golf_settler.py` currently does this (~line 143):

```python
if result == SCALAR_RESULT:
    # "cancelled (result=scalar, competitor withdrew) — voiding"
    ... settle as "void" / "kalshi_withdrawn"
```

**This is wrong, and it is wrong in the single most important place for P-022.** `result="scalar"` is not a withdrawal void — it is Kalshi reporting a **dead-heat split**, and `settlement_value_dollars` carries the realised $1/n payout. Treating it as a void:

- **Erases the exact event P-022 exists to harvest.** The fade's entire edge is that ties pay the YES holder less than $1. Voiding those settlements would make a profitable fade look like it never traded.
- **Silently contaminates P-017 today.** P-017 is live in paper on top-N props, where `scalar` was measured at 9/200 settled markets. Every one of those has been booked as a void rather than a partial payout, so P-017's forward gate numbers are already distorted.

### Do
1. Fix the settler: on `result == "scalar"`, settle using `settlement_value_dollars` as the realised per-contract payout (it **is** the realised payout — do not re-derive it from a tie count). Compute P&L as `settlement_value − fill_price` on the appropriate side, net of fees, using the existing fee helpers.
2. Keep a genuine void path for genuine voids, and make sure the two are distinguishable in the trade log and in any downstream reader. A void and a partial payout must never again collapse into the same row.
3. **Tests are the deliverable here, not the fix.** Add `tests/` coverage for: scalar YES-side, scalar NO-side, a 2-way split, a 5-way split, scalar-with-missing-`settlement_value_dollars` (must NOT silently become a void — fail loudly), and a real void. Assert the existing unknown-result warning path still fires for genuinely novel result vocabulary.
4. **Backfill / re-derive P-017's history.** Identify every P-017 row already settled as `void` / `kalshi_withdrawn` that was actually a scalar, and produce a corrected P&L series. Write the correction to a **new** file — do not mutate live `data/` history in place. Report how much P-017's forward numbers move.

## Part 2 — PRE-REGISTER THE P-022 GATE (before any pod exists)

This is the P-013 lesson: P-013 lost $2,094 while its criteria were still being decided after the fact, which is why P-015's rule is locked in advance. **P-022 gets its rule written before it can ever trade.**

Write `golf_quirks_research/P022_DECISION_RULE.md`, modelled on `tennis_research/P015_DECISION_RULE.md`, and make it **locked**:

- **Unit of observation: the TOURNAMENT, not the contract.** Names within one tournament are correlated — this is how P-017M produced a phantom +9.1¢.
- **Threshold:** propose n tournaments with an explicit power calculation against the Phase-2 effect (+3.4¢/ct, tournament-clustered CI [+1.7,+5.1]) and its per-tournament dispersion. Do not pick a round number — derive it and show the arithmetic.
- **A marginal-result rule, registered in advance**, in the style of P-016's: state now what happens if the forward estimate lands positive-but-not-separable. One extension only, at unchanged parameters.
- **A hard-kill trip** at any n (z ≤ −2.0), mirroring P-015.
- **Mandatory collateral caps, restated as gate conditions**: per-name ≤0.5% bankroll, per-tournament ≤5%, aggregate ≤15%, quote only at H≈12–24h pre-round. The tail is real — Phase 2 showed losses concentrate in tournaments where a faded name actually leads, and the 2026-07-25 P-017 halt is the live proof that per-EVENT caps are mandatory for any multi-name-per-event pod.
- **A sample caveat, recorded up front:** Kalshi trade history reaches back only ~1 month, so Phase 2's 19 tournaments are late-June → July 2026. The forward test is the real replication, and the rule must not be renegotiated if forward disagrees with backtest.
- **Name the one sanctioned reader** for gate results (a `scripts/p022_checkpoint.py`-style script), so nobody eyeballs a P&L chart at 2am and calls it a verdict.

Write the checkpoint reader as a stub that will work once rows exist. That is the only new script this task produces.

## Explicitly OUT of scope
No `src/pods/round_leader_fade.py`. No `P-022` block in `config_multi_pod.yaml`. No systemd unit. No entry in `pods.active`. No orders of any kind.

## Definition of done
Settler scalar handling fixed with the full test matrix passing; P-017's mis-voided history re-derived into a new corrected file with the P&L delta reported; `P022_DECISION_RULE.md` committed and marked locked with a derived (not guessed) threshold and its power arithmetic; checkpoint reader stubbed; **no pod, no config, no deploy**. Report the P-017 contamination magnitude prominently — Sam needs that number.
