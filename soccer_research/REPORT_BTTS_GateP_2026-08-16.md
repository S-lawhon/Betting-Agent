# BTTS Gate P — the placebo passed, and a stratified control killed the premise anyway

**Rule:** `soccer_research/BTTS_PLACEBO_RULE.md` (`4723ec2`), pre-registered before either test ran
**Supersedes the mechanism claim in:** `REPORT_BTTS_Decay_2026-08-16.md`

---

## VERDICT: the **first-half premise is REFUTED.** The +4.52¢ is a price-level effect, not a first-half effect.

| arm | net ¢/ct | 95% CI | z | fill |
|---|---:|---|---:|---:|
| first-half, sell (test) | **+4.52** | [+2.12, +6.91] | +3.71 | 15.43% |
| full-match, sell (placebo) | **−2.36** | [−4.29, −0.42] | −2.41 | 20.66% |
| first-half, **buy** (control) | +1.65 | [−0.49, +3.90] | +1.47 | 10.47% |

Gate P as written **PASSED** — placebo is −52% of the test and the CIs are
disjoint. **That pass is not real**, and the reason is a defect in the control
I specified, not in the arithmetic.

## 1. The stratified control

Both arms, bucketed by mean entry price:

| entry | full-match (control) | first-half (test) |
|---|---:|---:|
| [0.00, 0.15) | n=4, too few | **+9.50** (z=+5.60) |
| [0.15, 0.25) | **+16.97** (z=+5.66) | **+12.87** (z=+10.07) |
| [0.25, 0.40) | **+17.17** (z=+10.38) | −0.03 |
| [0.40, 0.60) | −4.73 (z=−2.91) | −16.12 (z=−3.15) |
| [0.60, 1.01) | **−18.74** (z=−13.29) | −18.49 (z=−4.21) |

**The effect is monotone in entry price and present in the control at least as
strongly as in the test.** Selling below ~25¢ earns; selling above ~40¢ loses.

The headline difference between the arms is **price mix, nothing else**:
first-half markets cluster in [0.15, 0.25) — the winning bucket — and
full-match markets cluster at [0.40, 0.60) and above, the losing ones.

## 2. My own control was mis-specified, and the pre-registration says why

`BTTS_PLACEBO_RULE.md` §3 justified full-match BTTS as the control partly
because *"it prices at a median mid 0.525 rather than 0.1875 — so it is not a
longshot."* **That is exactly backwards.** It controlled for venue, fee regime,
tick and settlement rulebook, and left uncontrolled the one variable that
drives the result. A control that differs from the test on the causal variable
is not a control.

This is the P-018 shape again — *a real number measuring the wrong thing* —
and it survived one pre-registered placebo before a second cut caught it.

## 3. What is actually left

Not "first-half BTTS is mispriced." What the data shows is **a price-level
effect on in-play Kalshi soccer BTTS: the cheap side is overpriced and the
expensive side is underpriced**, symmetric around roughly 0.30–0.40.

That is a **favourite-longshot claim**, which:

* is a **different hypothesis** from the one this workstream registered, and
  needs its own registration, universe definition and gate — it does not
  inherit Phase 2's PASS;
* **collides directly with P-019**, which killed favourite-longshot bias at the
  calibration gate in our Kalshi universe. Either P-019's universe excluded
  in-play soccer, or one of the two results is wrong. **That must be resolved
  before either is trusted**, and it is not resolved here;
* is **not obviously executable**, because selling at `ask − 1 tick` on a
  contract whose fair value sits near the bid is capturing a **wide relative
  spread** — 3.5¢ on an 18.75¢ mid is 19%, the caveat Phase 0 recorded and
  carried forward. Whether that survives queue position and size is untested.

## 4. Scalar correction: applied, immaterial

The 5 dropped `scalar` settlements are now included and booked at
`settlement_value_dollars`. **They carry 0 fills** — abandoned matches were not
trading in the window — so the headline is unchanged at +4.52¢. The fix was
correct to make; it validated nothing.

## 5. Status

* **Phase 2's PASS stands as arithmetic and falls as a mechanism.** Under
  `BTTS_1H_DECAY_RULE.md` §8.4 — *"the decay being real is not the finding"* —
  the finding required the effect to be attributable to first-half BTTS. It is
  not.
* **No capacity study is authorised**, because there is no longer a validated
  mechanism to size.
* The banked Phase 1 facts are unaffected: `SOCCERBTTS.pdf` ≡ `SOCCERHBTTS.pdf`,
  and the full-match payout criterion is monotone.
