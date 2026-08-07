# MLB win-probability calibration - pre-registered decision rule

**Status: LOCKED BLIND. Written 2026-08-07 before the evaluator existed and
before any outcome from the forward sample was read.** This is a model study,
not a P-018 revival and not authority to trade.

## 1. Question

Does `src/mlb_win_prob` improve on the contemporaneous Kalshi game-market mid
when both are scored against the same completed MLB games at the same times?

The model is allowed to use one market input: a true pregame home-win prior.
It must then add value from public game state. The benchmark remains the live
Kalshi mid at each scored checkpoint.

## 2. Why the P-018 replay cannot answer this

The retired P-018 replay has two orientation defects that make its model
disagreement unsuitable as calibration evidence:

1. It applies home-win probability to every team ticker, including away-team
   YES markets, without complementing either the model or settlement.
2. When the first captured observation is already in play, it uses that live
   mid as the pregame prior and applies the current state again.

P-018 remains KILLED by its mechanism gate. No result under this rule can
change that verdict. A positive result would support a new model-based research
card; it would not authorize P-016 or P-018.

## 3. Forward sample and outcome blindness

The sanctioned evaluator has separate `arm`, `status`, and `evaluate` modes.

- `arm` writes an immutable manifest containing the UTC arm time and SHA-256
  hashes of this rule and `src/mlb_win_prob.py`.
- Only games with ticker-scheduled starts at or after the arm time are eligible.
- `status` reads book captures only. It must not call StatsAPI or inspect game
  outcomes.
- `evaluate` refuses to run before seven full days have elapsed from arming.
- The first look is at 60 completed eligible games. The only extension is to
  120 games under the rule in section 8.

Changing model constants, observation construction, thresholds, or hashes
requires a new manifest and resets the sample to zero.

## 4. Orientation and admissibility

The independent unit is one game, keyed by the shared Kalshi event ticker.
Both team markets may provide prices but never count as two games.

- Parse away and home team codes from the matchup segment of the ticker.
- A home-team YES mid is a home-win probability.
- An away-team YES mid is complemented to `1 - mid`.
- If both sides are present at the same checkpoint, use the home-team market;
  otherwise use the away complement.
- Exclude an event if team orientation is missing or ambiguous.
- Exclude postponed, suspended, cancelled, tied, or non-final games.

The pregame prior is the latest oriented mid between six hours and five minutes
before the ticker-scheduled first pitch. An event without that true pregame
anchor is excluded. A live mid is never inverted into a synthetic pregame
prior for the primary analysis.

## 5. Checkpoints and state timing

Score the game at four fixed offsets from the ticker-scheduled first pitch:
30, 60, 90, and 120 minutes. At each offset, use the nearest oriented book
capture within 90 seconds. Each game must contribute at least two checkpoints.

For each selected market timestamp `t`, reconstruct the most recent completed
MLB StatsAPI play at or before `t - 30 seconds` and evaluate the frozen model
with the true pregame prior. This 30-second delay is the primary path and
prevents a historical replay from granting the model instantaneous state.

The full analysis is repeated at a 60-second state delay. The slower path is a
mandatory robustness gate, not an optional sensitivity table.

## 6. Statistic

For final home outcome `y`, compute paired Brier loss at each admitted
checkpoint:

```
model_loss  = (model_home_probability - y)^2
market_loss = (market_home_mid - y)^2
delta       = market_loss - model_loss
```

Positive `delta` means the model is better. Average checkpoint deltas within
each game first, then take the equal-weight mean across games. Confidence
intervals use a seeded 5,000-resample game bootstrap. Dense captures and games
with more admitted checkpoints therefore receive no extra weight.

Report coverage, exclusion reasons, both lag paths, model and market Brier
scores, mean paired delta, 95% interval, and every per-game aggregate. The
all-game result is reported before any subgroup.

## 7. Sanctioned implementation

Only `inplay_research/mlb_wp_calibration.py` may produce a verdict under this
rule. Its result artifact must repeat the manifest hashes and refuse a mismatch.
Synthetic tests must cover home/away orientation, pregame anchoring, checkpoint
selection, equal game weighting, clustered resampling, hash mismatch, and the
read-not-before boundary.

The old P-018 replay and its cached outputs are explicitly unsanctioned for
this question.

## 8. Decision

At the first eligible evaluation with at least 60 completed games:

| Condition | Verdict |
|---|---|
| Both the 30-second and 60-second lag paths have mean delta > 0 and 95% lower bound > 0 | **PASS** |
| The 30-second primary mean delta <= 0 | **KILL** |
| Otherwise | **NO DECISION; extend once to 120 games** |

At 120 games, anything short of PASS is **KILL for this frozen model version**.
This terminal rule is an opportunity-cost decision, not proof that no richer
baseball model can ever outperform the market.

## 9. Interpretation limits

Book capture is REST-polled and coverage-biased toward liquid markets. A PASS
applies only to captured `KXMLBGAME` markets and says nothing about executable
maker or taker edge after spread, fees, latency, or adverse selection. Those
would require a separate strategy specification and execution gate.
