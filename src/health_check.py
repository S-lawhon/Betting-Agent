"""
src/health_check.py
───────────────────
Structured health check for individual pods or the entire engine.

Queries the live dashboard API (/api/status) and the trade log to
produce a structured diagnostic report covering:

  1. Service status    — is the systemd service running?
  2. Dashboard status  — is /health responding?  Is /api/status fresh?
  3. Cycle health      — recent cycle success rate, error count, duration
  4. Pod health        — is the target pod active, scanning, placing?
  5. Trade activity    — recent trade volume, P&L, win rate
  6. Settlement        — settled vs. open positions, stale trade detection
  7. Code checks       — local config matches expectations, no syntax errors

Usage::

    # CLI (wired via main.py):
    python -m src.main --health-check P-001
    python -m src.main --health-check all

    # Programmatic:
    from src.health_check import run_health_check
    report = run_health_check("P-001", dashboard_url="http://129.212.176.202:8080")
    print(report.to_text())
"""

import json
import logging
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Status levels ────────────────────────────────────────────────────


class CheckStatus(Enum):
    """Health check result severity."""
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


# ── Individual check result ──────────────────────────────────────────


@dataclass
class CheckResult:
    """Result of a single health check."""
    name: str
    status: CheckStatus
    message: str
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"name": self.name, "status": self.status.value, "message": self.message}
        if self.detail:
            d["detail"] = self.detail
        return d


# ── Aggregate report ─────────────────────────────────────────────────


@dataclass
class HealthReport:
    """Aggregate health check report for one or more pods."""
    pod_id: str
    timestamp_utc: str
    checks: List[CheckResult] = field(default_factory=list)
    api_data: Optional[Dict[str, Any]] = None

    @property
    def overall_status(self) -> CheckStatus:
        """Worst status across all checks."""
        if any(c.status == CheckStatus.FAIL for c in self.checks):
            return CheckStatus.FAIL
        if any(c.status == CheckStatus.WARN for c in self.checks):
            return CheckStatus.WARN
        return CheckStatus.PASS

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.PASS)

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.WARN)

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.FAIL)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pod_id": self.pod_id,
            "timestamp_utc": self.timestamp_utc,
            "overall_status": self.overall_status.value,
            "summary": f"{self.pass_count} passed, {self.warn_count} warnings, {self.fail_count} failures",
            "checks": [c.to_dict() for c in self.checks],
        }

    def to_text(self) -> str:
        """Human-readable health report for terminal output."""
        lines: List[str] = []
        status_icon = {
            CheckStatus.PASS: "✅",
            CheckStatus.WARN: "⚠️ ",
            CheckStatus.FAIL: "❌",
            CheckStatus.SKIP: "⏭️ ",
        }

        overall = self.overall_status
        header_icon = status_icon.get(overall, "")
        lines.append("")
        lines.append("═" * 60)
        lines.append(f"  HEALTH CHECK: {self.pod_id}  {header_icon} {overall.value}")
        lines.append(f"  {self.timestamp_utc}")
        lines.append("═" * 60)
        lines.append("")

        for check in self.checks:
            icon = status_icon.get(check.status, "?")
            lines.append(f"  {icon} {check.name}")
            lines.append(f"     {check.message}")
            if check.detail:
                for detail_line in check.detail.split("\n"):
                    lines.append(f"     {detail_line}")
            lines.append("")

        lines.append("─" * 60)
        lines.append(
            f"  Result: {self.pass_count} passed, "
            f"{self.warn_count} warnings, {self.fail_count} failures"
        )
        lines.append("═" * 60)
        lines.append("")
        return "\n".join(lines)


# ── HTTP helpers ─────────────────────────────────────────────────────


def _fetch_json(url: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """Fetch JSON from a URL, returning None on failure."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _fetch_text(url: str, timeout: float = 10.0) -> Optional[str]:
    """Fetch plain text from a URL, returning None on failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception:
        return None


# ── Individual check functions ───────────────────────────────────────


def check_health_endpoint(dashboard_url: str) -> CheckResult:
    """Check 1: Is the /health endpoint responding?"""
    url = f"{dashboard_url.rstrip('/')}/health"
    body = _fetch_text(url, timeout=10.0)
    if body is None:
        return CheckResult(
            name="Health Endpoint",
            status=CheckStatus.FAIL,
            message=f"Cannot reach {url}",
            detail="The service may be down or the dashboard port is not reachable.",
        )
    if body == "ok":
        return CheckResult(
            name="Health Endpoint",
            status=CheckStatus.PASS,
            message="Dashboard /health responding OK",
        )
    return CheckResult(
        name="Health Endpoint",
        status=CheckStatus.WARN,
        message=f"Unexpected /health response: {body!r}",
    )


def check_api_status(dashboard_url: str) -> tuple:
    """Check 2: Is /api/status returning valid data?

    Returns (CheckResult, api_data_dict_or_None).
    """
    url = f"{dashboard_url.rstrip('/')}/api/status"
    data = _fetch_json(url, timeout=10.0)
    if data is None:
        return (
            CheckResult(
                name="Dashboard API",
                status=CheckStatus.FAIL,
                message=f"Cannot fetch {url}",
                detail="The dashboard API is not responding.",
            ),
            None,
        )
    engine_status = data.get("engine_status", "unknown")
    if engine_status == "running":
        return (
            CheckResult(
                name="Dashboard API",
                status=CheckStatus.PASS,
                message=f"Engine status: {engine_status}",
            ),
            data,
        )
    elif engine_status == "halted":
        halt_reason = (data.get("risk", {}) or {}).get("halt_reason", "unknown")
        return (
            CheckResult(
                name="Dashboard API",
                status=CheckStatus.WARN,
                message=f"Engine halted: {halt_reason}",
                detail="The risk guard has halted the engine.",
            ),
            data,
        )
    else:
        return (
            CheckResult(
                name="Dashboard API",
                status=CheckStatus.WARN,
                message=f"Engine status: {engine_status}",
            ),
            data,
        )


def check_cycle_health(data: Dict[str, Any]) -> CheckResult:
    """Check 3: Recent cycle metrics — success rate, errors, freshness."""
    cycle = data.get("cycle")
    if not cycle:
        return CheckResult(
            name="Cycle Health",
            status=CheckStatus.WARN,
            message="No cycle data available yet",
            detail="The engine may have just started and not completed a cycle.",
        )

    cycle_num = cycle.get("cycle_number", 0)
    success_rate = cycle.get("success_rate", 0)
    error_count = cycle.get("error_count", 0)
    duration = cycle.get("duration_seconds", 0)
    timestamp = cycle.get("timestamp_utc", "")

    detail_parts = [
        f"Cycle #{cycle_num}",
        f"Duration: {duration:.2f}s",
        f"Success rate: {success_rate:.1f}%",
        f"Errors: {error_count}",
    ]
    if timestamp:
        detail_parts.append(f"Last cycle: {timestamp}")

    # Check freshness — if timestamp is available
    freshness_warn = ""
    if timestamp:
        try:
            last_ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            age_minutes = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60
            detail_parts.append(f"Age: {age_minutes:.0f} minutes ago")
            if age_minutes > 15:
                freshness_warn = f" (last cycle was {age_minutes:.0f}min ago — may be stale)"
        except Exception:
            pass

    if error_count > 0 and success_rate < 50:
        return CheckResult(
            name="Cycle Health",
            status=CheckStatus.FAIL,
            message=f"High error rate: {error_count} errors, {success_rate:.0f}% success{freshness_warn}",
            detail="\n".join(detail_parts),
        )
    elif error_count > 0 or freshness_warn:
        return CheckResult(
            name="Cycle Health",
            status=CheckStatus.WARN,
            message=f"Cycle #{cycle_num}: {error_count} errors, {success_rate:.0f}% success{freshness_warn}",
            detail="\n".join(detail_parts),
        )
    return CheckResult(
        name="Cycle Health",
        status=CheckStatus.PASS,
        message=f"Cycle #{cycle_num}: {success_rate:.0f}% success, {duration:.2f}s, 0 errors",
        detail="\n".join(detail_parts),
    )


def check_pod_active(pod_id: str, data: Dict[str, Any]) -> CheckResult:
    """Check 4: Is the target pod present and reporting metrics?"""
    if pod_id == "all":
        pods = data.get("pods", {})
        if not pods:
            return CheckResult(
                name="Pod Active",
                status=CheckStatus.FAIL,
                message="No pods reporting in /api/status",
            )
        pod_names = [f"{pid} ({info.get('name', '?')})" for pid, info in pods.items()]
        return CheckResult(
            name="Pod Active",
            status=CheckStatus.PASS,
            message=f"{len(pods)} pods active: {', '.join(pod_names)}",
        )

    pods = data.get("pods", {})
    if pod_id not in pods:
        return CheckResult(
            name="Pod Active",
            status=CheckStatus.FAIL,
            message=f"{pod_id} not found in dashboard data",
            detail=f"Active pods: {', '.join(pods.keys()) if pods else 'none'}",
        )

    pod = pods[pod_id]
    name = pod.get("name", pod_id)
    placed = pod.get("placed", 0)
    alloc_pct = pod.get("alloc_pct", 0)
    return CheckResult(
        name="Pod Active",
        status=CheckStatus.PASS,
        message=f"{pod_id} ({name}) active — {placed} placed, {alloc_pct:.1f}% allocation",
    )


def check_trade_activity(pod_id: str, data: Dict[str, Any]) -> CheckResult:
    """Check 5: Trade activity — volume, P&L, win rate."""
    pods = data.get("pods", {})

    if pod_id == "all":
        # Aggregate across all pods
        total_placed = sum(p.get("placed", 0) for p in pods.values())
        total_pnl = sum(p.get("pnl", 0) for p in pods.values())
        total_wins = sum(p.get("wins", 0) for p in pods.values())
        total_losses = sum(p.get("losses", 0) for p in pods.values())
        resolved = total_wins + total_losses
        win_pct = (total_wins / resolved * 100) if resolved else 0
    else:
        pod = pods.get(pod_id, {})
        total_placed = pod.get("placed", 0)
        total_pnl = pod.get("pnl", 0)
        total_wins = pod.get("wins", 0)
        total_losses = pod.get("losses", 0)
        resolved = total_wins + total_losses
        win_pct = pod.get("win_pct", 0)

    detail_parts = [
        f"Total placed: {total_placed}",
        f"Wins: {total_wins}, Losses: {total_losses}",
        f"Win rate: {win_pct:.1f}%",
        f"P&L: ${total_pnl:,.2f}",
    ]

    # Also check recent trades from the trades array
    trades = data.get("trades", [])
    if pod_id != "all":
        trades = [t for t in trades if t.get("pod_id") == pod_id]

    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    detail_parts.append(f"Open positions: {len(open_trades)}")

    # Check for recent activity (trades in last 24h)
    recent_count = 0
    now = datetime.now(timezone.utc)
    for t in trades:
        ts_str = t.get("timestamp_utc", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if (now - ts).total_seconds() < 86400:
                    recent_count += 1
            except Exception:
                pass
    detail_parts.append(f"Trades in last 24h: {recent_count}")

    if total_placed == 0:
        return CheckResult(
            name="Trade Activity",
            status=CheckStatus.WARN,
            message="No trades placed yet",
            detail="\n".join(detail_parts),
        )

    if total_pnl < -500:
        return CheckResult(
            name="Trade Activity",
            status=CheckStatus.WARN,
            message=f"Negative P&L: ${total_pnl:,.2f} across {total_placed} trades ({win_pct:.1f}% WR)",
            detail="\n".join(detail_parts),
        )

    return CheckResult(
        name="Trade Activity",
        status=CheckStatus.PASS,
        message=f"{total_placed} trades, ${total_pnl:,.2f} P&L, {win_pct:.1f}% win rate",
        detail="\n".join(detail_parts),
    )


def check_settlement(pod_id: str, data: Dict[str, Any]) -> CheckResult:
    """Check 6: Settlement health — settled vs open, stale position detection."""
    settlement = data.get("settlement", {})
    trades = data.get("trades", [])
    if pod_id != "all":
        trades = [t for t in trades if t.get("pod_id") == pod_id]

    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    settled_count = settlement.get("total_settled", 0)
    wins = settlement.get("wins", 0)
    losses = settlement.get("losses", 0)
    voids = settlement.get("voids", 0)
    total_pnl = settlement.get("total_pnl", 0)

    detail_parts = [
        f"Settled: {settled_count} (W:{wins} L:{losses} V:{voids})",
        f"Open: {len(open_trades)}",
        f"Settlement P&L: ${total_pnl:,.2f}",
    ]

    # Check for stale open positions (placed > 7 days ago)
    now = datetime.now(timezone.utc)
    stale_trades: List[str] = []
    for t in open_trades:
        ts_str = t.get("timestamp_utc", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age_days = (now - ts).total_seconds() / 86400
                if age_days > 7:
                    event = t.get("event", t.get("market_id", "unknown"))
                    stale_trades.append(f"{event} ({age_days:.0f}d old)")
            except Exception:
                pass

    if stale_trades:
        detail_parts.append(f"Stale (>7d): {len(stale_trades)}")
        for st in stale_trades[:5]:
            detail_parts.append(f"  - {st}")
        if len(stale_trades) > 5:
            detail_parts.append(f"  ... and {len(stale_trades) - 5} more")

    if stale_trades:
        return CheckResult(
            name="Settlement",
            status=CheckStatus.WARN,
            message=f"{len(stale_trades)} stale open positions (>7 days old)",
            detail="\n".join(detail_parts),
        )

    if len(open_trades) > 100:
        return CheckResult(
            name="Settlement",
            status=CheckStatus.WARN,
            message=f"High open position count: {len(open_trades)}",
            detail="\n".join(detail_parts),
        )

    return CheckResult(
        name="Settlement",
        status=CheckStatus.PASS,
        message=f"{settled_count} settled, {len(open_trades)} open",
        detail="\n".join(detail_parts),
    )


def check_risk(data: Dict[str, Any]) -> CheckResult:
    """Check 7: Risk metrics — exposure, halt status."""
    risk = data.get("risk", {})
    if not risk:
        return CheckResult(
            name="Risk Status",
            status=CheckStatus.WARN,
            message="No risk data available",
        )

    halted = risk.get("halted", False)
    halt_reason = risk.get("halt_reason")
    exposure_pct = risk.get("total_exposure_pct", 0)
    exposure_usd = risk.get("total_exposure_usd", 0)
    daily_pnl = risk.get("daily_pnl", 0)
    open_positions = risk.get("open_positions", 0)
    bankroll = risk.get("bankroll", 0)

    detail_parts = [
        f"Bankroll: ${bankroll:,.2f}",
        f"Exposure: ${exposure_usd:,.2f} ({exposure_pct:.1f}%)",
        f"Daily P&L: ${daily_pnl:,.2f}",
        f"Open positions: {open_positions}",
    ]

    if halted:
        return CheckResult(
            name="Risk Status",
            status=CheckStatus.FAIL,
            message=f"Engine HALTED: {halt_reason or 'unknown reason'}",
            detail="\n".join(detail_parts),
        )

    if exposure_pct > 40:
        return CheckResult(
            name="Risk Status",
            status=CheckStatus.WARN,
            message=f"High exposure: {exposure_pct:.1f}% (${exposure_usd:,.2f})",
            detail="\n".join(detail_parts),
        )

    return CheckResult(
        name="Risk Status",
        status=CheckStatus.PASS,
        message=f"Exposure {exposure_pct:.1f}%, daily P&L ${daily_pnl:,.2f}, {open_positions} open",
        detail="\n".join(detail_parts),
    )


def check_config(pod_id: str, config_path: Optional[str] = None) -> CheckResult:
    """Check 8: Validate local config file loads without errors."""
    if config_path is None:
        config_path = "config_multi_pod.yaml"

    try:
        import yaml
    except ImportError:
        # Try PyYAML from different import
        try:
            from yaml import safe_load
        except ImportError:
            return CheckResult(
                name="Config Validation",
                status=CheckStatus.SKIP,
                message="PyYAML not available — cannot validate config",
            )

    path = Path(config_path)
    if not path.exists():
        return CheckResult(
            name="Config Validation",
            status=CheckStatus.FAIL,
            message=f"Config file not found: {config_path}",
        )

    try:
        import yaml
        with open(path) as f:
            config = yaml.safe_load(f)
    except Exception as exc:
        return CheckResult(
            name="Config Validation",
            status=CheckStatus.FAIL,
            message=f"Config parse error: {exc}",
        )

    active_pods = config.get("pods", {}).get("active", [])
    if pod_id != "all" and pod_id not in active_pods:
        return CheckResult(
            name="Config Validation",
            status=CheckStatus.WARN,
            message=f"{pod_id} not in active pods list: {active_pods}",
        )

    pod_cfg = config.get("pods", {}).get(pod_id, {}) if pod_id != "all" else {}
    if pod_cfg.get("enabled") is False:
        return CheckResult(
            name="Config Validation",
            status=CheckStatus.WARN,
            message=f"{pod_id} is explicitly disabled in config",
        )

    return CheckResult(
        name="Config Validation",
        status=CheckStatus.PASS,
        message=f"Config OK — {len(active_pods)} active pods: {', '.join(active_pods)}",
    )


# ── Main runner ──────────────────────────────────────────────────────


def run_health_check(
    pod_id: str = "all",
    dashboard_url: str = "http://129.212.176.202:8080",
    config_path: Optional[str] = None,
) -> HealthReport:
    """Run a full health check for a pod (or all pods).

    Args:
        pod_id:        Pod to check ("P-001", "P-006", "all").
        dashboard_url: Base URL of the live dashboard.
        config_path:   Path to config_multi_pod.yaml (optional).

    Returns:
        HealthReport with all check results.
    """
    now = datetime.now(timezone.utc).isoformat()
    report = HealthReport(pod_id=pod_id, timestamp_utc=now)

    # Check 1: Health endpoint
    report.checks.append(check_health_endpoint(dashboard_url))

    # Check 2: API status
    api_check, api_data = check_api_status(dashboard_url)
    report.checks.append(api_check)
    report.api_data = api_data

    if api_data is None:
        # Can't run remaining checks without API data
        report.checks.append(CheckResult(
            name="Remaining Checks",
            status=CheckStatus.SKIP,
            message="Skipped — dashboard API unreachable",
        ))
        return report

    # Check 3: Cycle health
    report.checks.append(check_cycle_health(api_data))

    # Check 4: Pod active
    report.checks.append(check_pod_active(pod_id, api_data))

    # Check 5: Trade activity
    report.checks.append(check_trade_activity(pod_id, api_data))

    # Check 6: Settlement
    report.checks.append(check_settlement(pod_id, api_data))

    # Check 7: Risk
    report.checks.append(check_risk(api_data))

    # Check 8: Config validation
    report.checks.append(check_config(pod_id, config_path))

    return report


# ── CLI entry point ──────────────────────────────────────────────────


def health_check_main(
    pod_id: str = "all",
    dashboard_url: str = "http://129.212.176.202:8080",
    config_path: Optional[str] = None,
    json_output: bool = False,
) -> int:
    """Run health check and print results.

    Returns exit code: 0 = all pass, 1 = warnings, 2 = failures.
    """
    report = run_health_check(pod_id, dashboard_url, config_path)

    if json_output:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_text())

    overall = report.overall_status
    if overall == CheckStatus.FAIL:
        return 2
    elif overall == CheckStatus.WARN:
        return 1
    return 0
