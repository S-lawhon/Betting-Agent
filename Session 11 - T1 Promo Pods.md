# Session 11 — T1 Promo Pods (P-009 & P-010)

## Summary

Built the two highest-revenue T1 pods: the Sign-Up Bonus Blitz (P-009) and Daily Odds Boost Grind (P-010), plus the shared promo math library that underlies both. These were the top-priority items in the build roadmap by immediate revenue potential ($3K–6K/month combined). All 425 tests pass (352 carried forward + 73 new), zero failures.

## New Files Created

### Source Modules (3 files)

| File | Lines | Purpose |
|------|-------|---------|
| `src/promo_calculator.py` | 215 | Shared promo math: odds conversion (American↔decimal), free bet hedge sizing and guaranteed profit, risk-free bet EV, odds boost EV and locked-profit calculation. `PromoRecord` dataclass covers all three promo types. |
| `src/pods/signup_bonus_pod.py` | 220 | P-009: Sign-Up Bonus Blitz. Reads configured free-bet and risk-free promos, calculates optimal hedge size and locked profit, places the hedge at Kalshi/Polymarket in paper or live mode. |
| `src/pods/boost_scanner_pod.py` | 240 | P-010: Daily Odds Boost Grind. Scans configured odds boosts, evaluates EV at boosted odds vs fair probability, supports EV mode (take the boost as-is) and Locked mode (hedge for guaranteed profit). |

### Test Files (3 files, 73 tests)

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_promo_calculator.py` | 31 | Odds conversion (American/decimal/implied), free bet hedge size + outcome equalisation + conversion rate + edge cases, risk-free EV, boost EV/EV% + breakeven probability, boost hedge size + locked profit + outcome equalisation, PromoRecord (is_active/effective_stake/to_dict) |
| `tests/test_signup_bonus_pod.py` | 22 | Metadata, happy path (PLACED/fields/ev/edge_pct/extra/log), skips (used/expired/low-conversion→SKIPPED_EDGE/missing-odds/wrong-type), dedup, from_config |
| `tests/test_boost_scanner_pod.py` | 20 | Metadata, EV mode (PLACED/fields/ev/max-stake-cap/extra/log), locked mode (PLACED/hedge-size-in-extra/fallback-to-ev), skips (used/expired/negative-EV→SKIPPED_EDGE/missing-fair-prob/wrong-type), dedup, multi-boost, from_config |

## Key Design Decisions

1. **Execution split — manual sportsbook bet, automated hedge**: FanDuel and DraftKings don't have public betting APIs. The user places the sportsbook bet manually, and the pod automates the hedge at Kalshi/Polymarket. P-009's `action=PLACED` means "hedge was placed" — the JSONL log records the hedge details so the user knows exactly what to do on the sportsbook side. In paper mode, both actions are simulated.

2. **Free bet math**: The core insight is that a free bet's "stake" is not at risk — only profit is returned on a win. This means the free bet behaves like a call option. The hedge equalises the YES-win and NO-win payouts:
   ```
   hedge_size = free_bet × (decimal_odds_sb − 1) × p_no
   locked_profit = hedge_size × (1/p_no − 1)
   conversion_rate = (decimal_odds_sb − 1) × (1 − p_no)
   ```
   At +200 odds with a Kalshi NO price of 0.62, conversion rate = 76% — $100 free bet → $76 guaranteed.

3. **P-010 dual mode**: `ev` mode accepts variance (best when you have many boosts; EV adds up over volume). `locked` mode hedges the boost for zero-variance guaranteed profit (useful for large single boosts). If no hedge price is available in locked mode, it falls back to EV mode automatically.

4. **PromoRecord as the universal promo data model**: One dataclass covers free bets, risk-free bets, and odds boosts. Type-specific fields are optional; callers use the relevant ones. This allows a single `from_config()` YAML list to mix promo types.

5. **Config-driven workflow**: Add new promos to `config_multi_pod.yaml` under `P-009.promos` or `P-010.boosts`. Both pods auto-register in POD_REGISTRY and are loaded by PodRunner. No code changes required to add new accounts.

## Promo Math Reference

```python
# Free Bet Conversion (+200 at sportsbook, 0.62 NO at Kalshi)
hedge  = free_bet_hedge_size(100, 3.0, 0.62)    # $117.80
profit = free_bet_guaranteed_profit(100, 3.0, 0.62)  # $72.20
rate   = free_bet_conversion_rate(3.0, 0.62)     # 72.2%

# Odds Boost EV (+350 boosted vs +150 true odds, $25 max stake)
ev_pct = odds_boost_ev_pct(4.5, 0.38)           # 0.71 → 71% EV per dollar
ev_usd = odds_boost_ev(25, 4.5, 0.38)           # $17.75 EV

# Locked Boost (+350, p_no=0.64, $25 stake)
hedge  = odds_boost_hedge_size(25, 4.5, 0.64)   # $72
locked = odds_boost_locked_profit(25, 4.5, 0.64)  # $15.50 guaranteed
```

## Using the Pods

**To add a new free bet:**
```yaml
# In config_multi_pod.yaml, under pods.P-009.promos:
- promo_id: FD-FB-2026-03
  sportsbook: fanduel
  promo_type: free_bet
  amount: 200
  boosted_odds: 200        # +200 American
  fair_yes_prob: 0.42      # From scanner/odds_client
  hedge_venue: kalshi
  hedge_market_id: CHIEFS-2026-03
  hedge_no_price: 0.60
  expiry: "2026-03-01"
  status: pending
```

**To add a daily odds boost:**
```yaml
# In config_multi_pod.yaml, under pods.P-010.boosts:
- promo_id: DK-BOOST-2026-03-01
  sportsbook: draftkings
  promo_type: odds_boost
  amount: 50
  original_odds: 120       # +120 base odds
  boosted_odds: 400        # +400 boosted
  fair_yes_prob: 0.45
  max_stake: 50
  expiry: "2026-03-01"
  status: pending
```

## Cumulative Project State

| Metric | Session 9 | Session 10 | Session 11 |
|--------|-----------|------------|------------|
| Source modules | 17 | 20 | 23 |
| Test files | 15 | 18 | 21 |
| Total tests | 268 | 352 | 425 |
| Pods implemented | 5 | 5 | 7 (+P-009, P-010) |
| Engine components | 3 | 3 | 3 |
| Entry points | 0 | 1 | 1 |
| Revenue-capable pods | 1 (P-001) | 1 | 3 (+P-009, P-010 manual flow) |

## What's Next (Session 12+)

- **Activate P-009 and P-010**: Add your first real promos to `config_multi_pod.yaml`, flip `status: pending`, run `python -m src.main --once --pods P-009,P-010` to see the hedge recommendations.
- **Dashboard extension**: Extend the terminal dashboard to show per-pod P&L, promo conversion rates, and aggregate risk snapshot.
- **T3 pods**: Political Fair-Value Model (P-011) for election cycles — Bayesian ensemble of polls + forecasters.
- **Backtesting P-004 and P-012**: Run historical simulations against archived FRED/Cleveland Fed data to validate the edge models.
- **NowcastClient HTTP wiring live run**: Enable P-012 with a free FRED API key and run first live economic scan.
