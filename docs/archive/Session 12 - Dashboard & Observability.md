# Session 12 — Dashboard & Observability

## Summary

Built the terminal dashboard (`src/dashboard.py`) — a fully formatted, optionally ANSI-coloured observability layer for the Betting Pod Shop engine.  The dashboard aggregates data from `AggregateRiskGuard`, `PodRunner`, `CapitalAllocator`, and `SettlementBridge` into a single printable panel.  Wired it into `main.py` via a new `--dashboard` / `-d` flag and an optional `renderer` parameter on `make_cycle_callback`.  All 516 tests pass (425 carried forward + 91 new), zero failures.

## New Files Created

### Source Modules (1 file)

| File | Lines | Purpose |
|------|-------|---------|
| `src/dashboard.py` | 260 | Terminal dashboard: ANSI colour helpers, four section renderers (`render_header`, `render_risk_panel`, `render_cycle_panel`, `render_pods_table`, `render_settlement_bar`, `render_two_column`), and `DashboardRenderer` class. |

### Test Files (1 file, 91 tests)

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_dashboard.py` | 91 | ANSI helpers (colorize, strip, vlen, rpad, pnl_color, pnl_str), render_header (lines, rules, title, timestamp, width), render_risk_panel (exposure, P&L, positions, active/halted state, no-color/color mode), render_cycle_panel (cycle#, duration, placed, errors, success rate), render_two_column (alignment, padding, empty, left-only), render_pods_table (empty placeholder, pod rows, totals, allocations, sort order), render_settlement_bar (positive/negative P&L, win rate, counts), DashboardRenderer (properties, render, clear_and_render, settlement, performances, allocations, color/no-color, ends with double rule, halted state), make_cycle_callback renderer integration, --dashboard flag (defaults, long form, short form) |

## Modified Files

### `src/main.py` (4 targeted edits)

1. **Import**: added `from src.dashboard import DashboardRenderer`
2. **`make_cycle_callback` signature**: added `renderer: Optional["DashboardRenderer"] = None` parameter.  When a renderer is provided, the callback calls `renderer.clear_and_render(snapshot, report)` and prints the result to stdout after every cycle.
3. **`_build_parser`**: added `-d` / `--dashboard` boolean flag.
4. **`main()`**: creates a `DashboardRenderer(use_color=True, width=80)` when `--dashboard` is set and passes it to `make_cycle_callback`.

## Dashboard Layout

```
════════════════════════════════════════════════════════════════════════════════
  BETTING POD SHOP                                     2026-01-15 14:32:07 UTC
════════════════════════════════════════════════════════════════════════════════

  RISK SNAPSHOT                           LAST CYCLE  (#42)
  Exposure       $  1,250.00  (12.5%)     Duration       0.43s
  Daily P&L      +$147.25  (+1.47%)       Pods           4
  Positions      8 open                   Placed         3
  Status         ACTIVE                   Skipped        12
                                          Errors         0
                                          Success        100%

────────────────────────────────────────────────────────────────────────────────
  POD PERFORMANCE

  Pod       Placed   Wins   Loss   Win%         P&L  Alloc%   Max$
  ──────────────────────────────────────────────────────────────────────────────
  P-001         15      9      6  60.0%    +$234.50   20.0%    200
  P-006          8      5      3  62.5%     +$89.00   15.0%    150
  P-009          3      3      0 100.0%     +$72.00   10.0%    250
  P-010         12      7      5  58.3%     +$41.75   10.0%    100
  ──────────────────────────────────────────────────────────────────────────────
  TOTAL         38     24     14  63.2%    +$437.25
────────────────────────────────────────────────────────────────────────────────
  SETTLED P&L: +$437.25  │  Win rate: 63.2%  │  Trades: 38  (W:24 L:12 V:2)
════════════════════════════════════════════════════════════════════════════════
```

In color mode (live terminal):
- **Cyan + bold** section titles (RISK SNAPSHOT, LAST CYCLE, POD PERFORMANCE)
- **Green** for positive P&L, active status, 100% success rate
- **Red** for negative P&L, errors, HALTED status
- **Yellow** for degraded (80–99%) success rate, borderline win rates (45–54%)
- Color applied *after* padding, so column alignment is never broken by escape codes

## Key Design Decisions

1. **Stdlib-only**: no external dependencies.  All colour output uses raw ANSI escape codes (`\033[...m`).  `_strip_ansi()` uses only `re` (stdlib) to compute visual lengths for proper column alignment.

2. **Color-after-pad pattern**: every P&L value is formatted as a plain string, right-padded/right-justified to its column width, and only then wrapped in ANSI codes.  This prevents escape codes from inflating the string length and breaking `f-string` width specifiers.

3. **`use_color=None` auto-detect**: `DashboardRenderer` defaults to `sys.stdout.isatty()`.  Tests always pass `use_color=False`, ensuring ANSI codes never interfere with string assertions.

4. **Side-by-side two-column layout**: `render_two_column(left, right, col_width)` uses `_rpad()` (which calls `_vlen()` → `_strip_ansi()`) so the right column starts at the correct visual position regardless of colour codes in the left column.

5. **Optional arguments throughout**: `render_pods_table` works with no allocations dict (allocation columns show 0); `DashboardRenderer.render()` omits the settlement bar when `settlement_summary=None`.  This allows the dashboard to start up usefully even before `SettlementBridge` and `CapitalAllocator` are wired in.

6. **`clear_and_render` for live refresh**: prepends `\033[2J\033[H` (erase display + cursor home) so repeated calls appear to update in-place in a live terminal without scroll.

7. **Renderer injection into `make_cycle_callback`**: the cycle callback renders and prints the dashboard after every cycle.  Errors in the renderer are caught and logged at DEBUG level (never crash the scan loop).

## Using the Dashboard

```bash
# Print dashboard after each scan cycle (single run):
python -m src.main --once --dashboard

# Live refresh every 5 minutes:
python -m src.main --loop --interval 300 --dashboard

# Dashboard + verbose pod log:
python -m src.main --loop --interval 60 --dashboard -v
```

Programmatic use:

```python
from src.dashboard import DashboardRenderer

renderer = DashboardRenderer(use_color=True, width=80)

# After each cycle:
print(renderer.clear_and_render(
    snapshot=guard.snapshot(),
    report=last_cycle_report,
    performances=allocator.performances(),
    allocations=allocator.allocations(),
    settlement_summary=bridge.summary(),
))
```

## Cumulative Project State

| Metric | Session 11 | Session 12 |
|--------|------------|------------|
| Source modules | 23 | 24 |
| Test files | 21 | 22 |
| Total tests | 425 | 516 (+91) |
| Pods implemented | 7 | 7 |
| Engine components | 3 | 3 |
| Entry points | 1 | 1 |
| Dashboard | — | ✓ live terminal |

## What's Next (Session 13+)

- **Activate P-009 and P-010 with real promos**: add entries to `config_multi_pod.yaml` under `pods.P-009.promos` / `pods.P-010.boosts`, set `status: pending`, run `python -m src.main --once --pods P-009,P-010 --dashboard` to see the hedge recommendations in the dashboard.
- **NowcastClient HTTP live run**: set `nowcast.fred_api_key` in config (free FRED registration), run `python -m src.main --once --pods P-012 --dashboard` — the dashboard will show the macro EV scan result.
- **T3 Political Fair-Value pod (P-011)**: Bayesian ensemble of polls + forecasters for election-cycle prediction markets.
- **Backtesting P-004 and P-012**: simulate historical cycles against archived FRED/Cleveland Fed CSVs to validate edge assumptions.
- **Per-venue exposure detail**: extend `render_risk_panel` to show `venue_exposure` breakdown (Kalshi / Polymarket / ForecastEx) when the dict is non-empty.
