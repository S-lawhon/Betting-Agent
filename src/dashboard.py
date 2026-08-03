"""
src/dashboard.py
────────────────
Terminal dashboard for the Betting Pod Shop.

Renders a formatted, optionally ANSI-coloured panel showing:
  • Risk snapshot  (exposure, daily P&L, halt status)
  • Last cycle     (placed / skipped / errors, duration)
  • Per-pod table  (P&L, win rate, capital allocation)
  • Settlement bar (aggregate settled P&L, win rate, trade counts)

Requires only the Python stdlib — no external dependencies.

Usage::

    from src.dashboard import DashboardRenderer

    renderer = DashboardRenderer(use_color=True, width=80)
    output = renderer.render(
        snapshot=guard.snapshot(),
        report=last_cycle_report,
        performances=allocator.performances(),
        allocations=allocator.allocations(),
        settlement_summary=bridge.summary(),
    )
    print(output)
"""

import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── ANSI colour constants ────────────────────────────────────────────


class _C:
    """ANSI escape codes (module-private)."""
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[31m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    CYAN   = "\033[36m"


def colorize(text: str, color: str, bold: bool = False) -> str:
    """Wrap *text* in ANSI colour/bold codes and reset at the end."""
    prefix = _C.BOLD if bold else ""
    return f"{prefix}{color}{text}{_C.RESET}"


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape codes from *text*."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


def _vlen(text: str) -> int:
    """Visual (printable) length of a string — ignores ANSI codes."""
    return len(_strip_ansi(text))


def _rpad(text: str, width: int) -> str:
    """Right-pad *text* to visual *width* characters."""
    return text + " " * max(0, width - _vlen(text))


# ── P&L / percentage formatters ──────────────────────────────────────


def _pnl_color(value: float) -> str:
    """Return the ANSI colour code appropriate for a P&L value."""
    if value > 0:
        return _C.GREEN
    if value < 0:
        return _C.RED
    return _C.DIM


def _pnl_str(value: float) -> str:
    """Format a P&L amount with sign: '+$1,234.56' or '-$0.50'."""
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"+${value:,.2f}"


# ── Section renderers ────────────────────────────────────────────────


def render_header(
    width: int = 78,
    timestamp: Optional[str] = None,
) -> List[str]:
    """Return the two-line title header (no ANSI colour).

    Args:
        width:     Column width for the dashboard.
        timestamp: Pre-formatted UTC timestamp; defaults to now.

    Returns:
        Three lines: top rule, title+timestamp, bottom rule.
    """
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    title = "BETTING POD SHOP"
    # Fill space between title and timestamp; clamp to at least 1 space
    inner = width - 4   # 2-char indent each side
    gap = max(1, inner - len(title) - len(ts))
    body = f"  {title}{' ' * gap}{ts}"
    return [
        "═" * width,
        body,
        "═" * width,
    ]


def render_risk_panel(
    snapshot: Any,
    use_color: bool = True,
) -> List[str]:
    """Return lines describing the current aggregate risk state.

    Args:
        snapshot:  ``RiskSnapshot`` from ``AggregateRiskGuard.snapshot()``.
        use_color: Whether to emit ANSI colour codes.

    Returns:
        List of plain/coloured text lines (no trailing newlines).
    """
    def _col(text: str, color: str, bold: bool = False) -> str:
        return colorize(text, color, bold) if use_color else text

    exp_usd  = snapshot.total_exposure_usd
    exp_pct  = snapshot.total_exposure_pct * 100
    dpnl     = snapshot.daily_pnl
    dpnl_pct = snapshot.daily_pnl_pct * 100
    pos      = snapshot.open_positions
    halted   = snapshot.halted
    reason   = getattr(snapshot, "halt_reason", None) or ""
    enforcing = getattr(snapshot, "enforcing", True)
    would_halt = getattr(snapshot, "would_halt", False)
    shadow    = getattr(snapshot, "shadow_counts", None) or {}

    # P&L with colour applied *after* formatting (correct ANSI alignment)
    pnl_plain = _pnl_str(dpnl)
    pnl_text  = _col(pnl_plain, _pnl_color(dpnl)) if use_color else pnl_plain

    # Status.  In shadow mode a halt is OBSERVED, not applied — saying
    # plain "HALTED" would read as "the engine stopped trading", which
    # is the opposite of what happened.
    if halted:
        status_plain = "HALTED" + (f" — {reason}" if reason else "")
        status_text  = _col(status_plain, _C.RED, bold=True)
    elif would_halt:
        status_plain = "ACTIVE (would be halted"
        status_plain += f": {reason})" if reason else ")"
        status_text   = _col(status_plain, _C.YELLOW, bold=True)
    else:
        status_text = _col("ACTIVE", _C.GREEN, bold=True)

    title = _col("  RISK SNAPSHOT", _C.CYAN, bold=True)
    if not enforcing:
        title += _col("  [SHADOW — limits measured, not enforced]", _C.YELLOW)

    lines = [
        title,
        f"  {'Exposure':<14} ${exp_usd:>10,.2f}  ({exp_pct:.1f}%)",
        f"  {'Daily P&L':<14} {pnl_text}  ({dpnl_pct:+.2f}%)",
        f"  {'Positions':<14} {pos} open",
        f"  {'Status':<14} {status_text}",
    ]

    # Trades the guard WOULD have blocked and let through anyway. This
    # is the number the shadow run exists to produce — it is the size of
    # the constraint the strategies will meet once enforcement is back
    # on, without having paid for it in lost sample.
    if not enforcing and shadow:
        total = sum(shadow.values())
        detail = ", ".join(
            f"{kind}={n}" for kind, n in sorted(
                shadow.items(), key=lambda kv: -kv[1])
        )
        lines.append(
            f"  {'Shadow':<14} "
            + _col(f"{total} trade(s) passed through", _C.YELLOW)
        )
        lines.append(f"  {'':<14} {detail}")

    return lines


def render_cycle_panel(
    report: Any,
    use_color: bool = True,
) -> List[str]:
    """Return lines summarising the most recent scan cycle.

    Args:
        report:    ``CycleReport`` from ``PodRunner.run_once()``.
        use_color: Whether to emit ANSI colour codes.

    Returns:
        List of plain/coloured text lines.
    """
    def _col(text: str, color: str, bold: bool = False) -> str:
        return colorize(text, color, bold) if use_color else text

    placed_text = str(report.placed_count)
    if use_color and report.placed_count > 0:
        placed_text = _col(placed_text, _C.GREEN, bold=True)

    err_text = str(report.error_count)
    if use_color and report.error_count > 0:
        err_text = _col(err_text, _C.RED, bold=True)

    sr = report.success_rate * 100
    sr_plain = f"{sr:.0f}%"
    if use_color:
        sr_color = _C.GREEN if sr == 100 else (_C.YELLOW if sr >= 80 else _C.RED)
        sr_text = _col(sr_plain, sr_color)
    else:
        sr_text = sr_plain

    title = _col(f"  LAST CYCLE  (#{report.cycle_number})", _C.CYAN, bold=True)

    return [
        title,
        f"  {'Duration':<14} {report.duration_seconds:.2f}s",
        f"  {'Pods':<14} {report.pods_scanned}",
        f"  {'Placed':<14} {placed_text}",
        f"  {'Skipped':<14} {report.skipped_count}",
        f"  {'Errors':<14} {err_text}",
        f"  {'Success':<14} {sr_text}",
    ]


def render_two_column(
    left: List[str],
    right: List[str],
    col_width: int = 39,
) -> List[str]:
    """Render two line-lists side-by-side.

    Left column is padded to *col_width* visual characters; right column
    is appended directly (handles ANSI codes via ``_rpad``/``_vlen``).

    Args:
        left:      Lines for the left column.
        right:     Lines for the right column.
        col_width: Visual character width of the left column.

    Returns:
        Combined list of lines.
    """
    n = max(len(left), len(right))
    result = []
    for i in range(n):
        l_line = left[i]  if i < len(left)  else ""
        r_line = right[i] if i < len(right) else ""
        result.append(_rpad(l_line, col_width) + r_line)
    return result


def render_pods_table(
    performances: Dict[str, Any],
    allocations: Optional[Dict[str, Any]] = None,
    width: int = 78,
    use_color: bool = True,
) -> List[str]:
    """Return a formatted per-pod performance table.

    Args:
        performances: ``{pod_id: PodPerformance}`` from
                      ``CapitalAllocator.performances()``.
        allocations:  ``{pod_id: PodAllocation}`` from
                      ``CapitalAllocator.allocations()`` (optional).
        width:        Terminal column width.
        use_color:    Whether to emit ANSI colour codes.

    Returns:
        List of formatted lines.
    """
    def _col(text: str, color: str, bold: bool = False) -> str:
        return colorize(text, color, bold) if use_color else text

    sep   = "  " + "─" * (width - 2)
    title = _col("  POD PERFORMANCE", _C.CYAN, bold=True)

    lines: List[str] = [title, ""]

    if not performances:
        lines.append("  (no performance data yet)")
        return lines

    # ── Header row ────────────────────────────────────────────────────
    hdr = (
        f"  {'Pod':<8} {'Placed':>7} {'Wins':>6} "
        f"{'Loss':>6} {'Win%':>6} {'P&L':>11} {'Alloc%':>7} {'Max$':>6}"
    )
    lines.append(hdr)
    lines.append(sep)

    # ── Pod rows ──────────────────────────────────────────────────────
    t_placed = t_wins = t_losses = 0
    total_pnl = 0.0

    for pod_id in sorted(performances):
        perf   = performances[pod_id]
        alloc  = (allocations or {}).get(pod_id)

        placed   = perf.total_placed
        wins     = perf.total_wins
        losses   = perf.total_losses
        pnl      = perf.total_pnl
        resolved = wins + losses
        win_pct  = (wins / resolved * 100) if resolved else 0.0

        alloc_pct = (alloc.allocation_pct * 100) if alloc else 0.0
        max_pos   = int(alloc.max_position_usd) if alloc else 0

        # Pad plain string first, then apply colour (preserves column width)
        pnl_plain  = _pnl_str(pnl).rjust(11)
        pnl_text   = _col(pnl_plain, _pnl_color(pnl)) if use_color else pnl_plain

        lines.append(
            f"  {pod_id:<8} {placed:>7} {wins:>6} "
            f"{losses:>6} {win_pct:>5.1f}% {pnl_text} "
            f"{alloc_pct:>6.1f}% {max_pos:>6}"
        )

        t_placed += placed
        t_wins   += wins
        t_losses += losses
        total_pnl += pnl

    # ── Totals row ────────────────────────────────────────────────────
    lines.append(sep)
    resolved_total  = t_wins + t_losses
    total_win_pct   = (t_wins / resolved_total * 100) if resolved_total else 0.0
    total_pnl_plain = _pnl_str(total_pnl).rjust(11)
    total_pnl_text  = _col(total_pnl_plain, _pnl_color(total_pnl), bold=True) \
                      if use_color else total_pnl_plain

    lines.append(
        f"  {'TOTAL':<8} {t_placed:>7} {t_wins:>6} "
        f"{t_losses:>6} {total_win_pct:>5.1f}% {total_pnl_text}"
    )

    return lines


def render_settlement_bar(
    summary: Dict[str, Any],
    use_color: bool = True,
) -> List[str]:
    """Return a one-line settlement totals bar.

    Args:
        summary:   Dict from ``SettlementBridge.summary()``.
        use_color: Whether to emit ANSI colour codes.

    Returns:
        Single-element list containing the formatted bar.
    """
    def _col(text: str, color: str, bold: bool = False) -> str:
        return colorize(text, color, bold) if use_color else text

    total_pnl    = summary.get("total_pnl", 0.0)
    total_settled = summary.get("total_settled", 0)
    wins         = summary.get("wins", 0)
    losses       = summary.get("losses", 0)
    voids        = summary.get("voids", 0)
    win_rate     = summary.get("win_rate", 0.0) * 100

    pnl_plain = _pnl_str(total_pnl)
    pnl_text  = _col(pnl_plain, _pnl_color(total_pnl), bold=True) \
                if use_color else pnl_plain

    wr_plain = f"{win_rate:.1f}%"
    if use_color:
        wr_color = _C.GREEN if win_rate >= 55 else (_C.YELLOW if win_rate >= 45 else _C.RED)
        wr_text = _col(wr_plain, wr_color)
    else:
        wr_text = wr_plain

    return [
        f"  SETTLED P&L: {pnl_text}"
        f"  │  Win rate: {wr_text}"
        f"  │  Trades: {total_settled}  (W:{wins} L:{losses} V:{voids})"
    ]


# ── Dashboard renderer ───────────────────────────────────────────────


class DashboardRenderer:
    """Renders a full terminal dashboard for the Betting Pod Shop.

    Combines the risk snapshot, last-cycle summary, per-pod performance
    table, and settlement totals into a single printable string.

    Args:
        use_color: Whether to emit ANSI colour codes.
                   ``None`` (default) auto-detects from ``sys.stdout.isatty()``.
        width:     Terminal column width (default 78).
    """

    def __init__(
        self,
        use_color: Optional[bool] = None,
        width: int = 78,
    ):
        if use_color is None:
            use_color = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._use_color = bool(use_color)
        self._width     = int(width)

    # ── Properties ───────────────────────────────────────────────────

    @property
    def use_color(self) -> bool:
        """Whether ANSI colour codes are emitted."""
        return self._use_color

    @property
    def width(self) -> int:
        """Terminal column width used for layout."""
        return self._width

    # ── Public API ───────────────────────────────────────────────────

    def render(
        self,
        snapshot: Any,
        report: Any,
        performances: Optional[Dict[str, Any]] = None,
        allocations: Optional[Dict[str, Any]] = None,
        settlement_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Render a complete dashboard frame as a multi-line string.

        Args:
            snapshot:           ``RiskSnapshot`` from
                                ``AggregateRiskGuard.snapshot()``.
            report:             ``CycleReport`` from the most recent
                                ``PodRunner.run_once()``.
            performances:       ``{pod_id: PodPerformance}`` from
                                ``CapitalAllocator.performances()``
                                (optional — shows empty table if absent).
            allocations:        ``{pod_id: PodAllocation}`` from
                                ``CapitalAllocator.allocations()``
                                (optional — allocation columns omitted if absent).
            settlement_summary: ``dict`` from ``SettlementBridge.summary()``
                                (optional — settlement bar omitted if absent).

        Returns:
            Multi-line string ready for ``print()``.
        """
        w = self._width
        c = self._use_color

        lines: List[str] = []

        # Title header
        lines.extend(render_header(w))
        lines.append("")

        # Side-by-side: risk (left) + last cycle (right)
        left  = render_risk_panel(snapshot, use_color=c)
        right = render_cycle_panel(report, use_color=c)
        col_w = w // 2
        lines.extend(render_two_column(left, right, col_w))

        lines.append("")
        lines.append("─" * w)

        # Pod performance table
        lines.extend(render_pods_table(performances or {}, allocations, w, c))

        # Settlement bar (only when data provided)
        if settlement_summary is not None:
            lines.append("─" * w)
            lines.extend(render_settlement_bar(settlement_summary, c))

        lines.append("═" * w)
        return "\n".join(lines)

    def clear_and_render(self, *args: Any, **kwargs: Any) -> str:
        """Clear the terminal, then render a fresh dashboard frame.

        Uses ANSI ``ESC[2J`` (erase display) and ``ESC[H`` (cursor home)
        so the panel appears to update in-place on repeated calls.

        All arguments are forwarded directly to :meth:`render`.
        """
        return "\033[2J\033[H" + self.render(*args, **kwargs)
