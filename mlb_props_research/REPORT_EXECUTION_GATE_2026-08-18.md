# MLB Props Execution Gate — Terminal Unscorable Result

## Outcome

The first 27 chronological admissible game-days froze correctly, and the
sanctioned reader remained outcome-blind until 2026-08-18 00:30 ET. The locked
sample nevertheless cannot produce either `BUILD_CANDIDATE` or `STOP`.

Four selected `KXMLBHIT` markets are finalized scalar settlements:

| Ticker | Settlement value |
|---|---:|
| `KXMLBHIT-26AUG012040SFSD-SFLARRAEZ1-2` | $0.34 |
| `KXMLBHIT-26AUG021920BOSLAD-LADDRUSHING68-2` | $0.16 |
| `KXMLBHIT-26AUG161515STLCHC-CHCDSWANSON7-2` | $0.26 |
| `KXMLBHIT-26JUL221607STLLAA-STLPPAGS43-1` | $0.45 |

These are not delayed settlements. Kalshi reports each market as `finalized`
with `result="scalar"`. They will never become binary.

## Why the gate cannot be repaired

`MLB_PROPS_EXECUTION_RULE.md` was locked before outcomes were read and states
that a missing or scalar settlement makes the result unavailable until every
selected market has a binary result. It also fixes exactly the first 27 days
and authorizes no substitution or extension.

Using the scalar payout now would be economically sensible but would change the
payoff rule after unblinding. Dropping the four markets or replacing their days
would change the frozen sample. Both are prohibited. The honest result is
therefore `UNSCORABLE`, not an economic pass or failure.

## Disposition

- No build, canary, credentials, orders, or capital are authorized.
- The collector and daily checkpoint can stop; the frozen tape, settlement
  cache, and terminal checkpoint remain audit evidence.
- A successor study must pre-register realized scalar-payout handling and use
  a new forward sample. It does not inherit this gate's 27 days.
