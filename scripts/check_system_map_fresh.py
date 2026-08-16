#!/usr/bin/env python3
"""Is docs/system_map.html still telling the truth?

    python3 scripts/check_system_map_fresh.py          # report, exit 1 if stale
    python3 scripts/check_system_map_fresh.py --json   # machine-readable

WHY
───
The system map is a hand-written explainer, so it goes stale silently. On
2026-08-02 it still claimed "3 services", "1,357 tests" and no agent layer at
all — written 23 Jul and overtaken within a fortnight.

This measures the facts that ARE derivable from the repo and compares them to
what the page claims. It deliberately does NOT try to judge prose: a section
can be factually correct and still describe a system that no longer exists.
That judgement is what the scheduled refresh agent is for; this just gives it
(and you) an exact starting point.

WHAT IT CANNOT SEE
──────────────────
Anything that only exists on the droplet — which units are actually active,
live gate progress, real P&L. `services_installed` below counts unit FILES in
the repo, not running services. A cloud agent has no SSH access, so it must
report those as unverified rather than guess.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "system_map.html"

# Facts the page states in its header strip, and how to measure each one.
# `label` must match the <span> text in the fact chip.


def _count_tests() -> Optional[int]:
    """Collected test count. None if pytest cannot run here."""
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
            cwd=ROOT, capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\s+tests?\s+collected", res.stdout)
    return int(m.group(1)) if m else None


def _active_pods() -> Optional[int]:
    try:
        import yaml
    except ImportError:
        return None
    try:
        cfg = yaml.safe_load((ROOT / "config_multi_pod.yaml").read_text()) or {}
    except OSError:
        return None
    active = (cfg.get("pods") or {}).get("active") or []
    return len(active)


def measure() -> Dict[str, Any]:
    agents = sorted(p.stem for p in (ROOT / ".claude" / "agents").glob("*.md"))
    units = sorted(p.name for p in (ROOT / "scripts" / "systemd").glob("*.service"))
    return {
        "active_pods": _active_pods(),
        "agent_roles": len(agents),
        "agent_names": agents,
        "python_modules": len(list((ROOT / "src").glob("*.py"))),
        "tests": _count_tests(),
        "services_installed": len(units),
        "unit_files": units,
    }


def claimed() -> Dict[str, Any]:
    """What the header strip currently asserts."""
    html = PAGE.read_text(encoding="utf-8")
    out: Dict[str, Any] = {}
    for num, label in re.findall(
            r'<div class="fact"><b>([\d,~]+)</b>\s*<span>—?\s*([^<]+)</span>', html):
        out[label.strip()] = int(num.replace(",", "").replace("~", ""))
    m = re.search(r"<span>as of</span> <b>([^<]+)</b>", html)
    out["as_of"] = m.group(1).strip() if m else None
    return out


# claimed-label -> measured-key
PAIRS = [
    ("active pods", "active_pods"),
    ("agent roles", "agent_roles"),
    ("Python modules", "python_modules"),
    ("tests", "tests"),
    ("service definitions", "services_installed"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-tests", action="store_true",
                    help="skip the pytest collection (slow)")
    args = ap.parse_args()

    if not PAGE.exists():
        print(f"ERROR: {PAGE} not found", file=sys.stderr)
        return 2

    got = measure()
    if args.skip_tests:
        got["tests"] = None
    says = claimed()

    drift: List[Dict[str, Any]] = []
    unmeasured: List[str] = []
    for label, key in PAIRS:
        want, have = says.get(label), got.get(key)
        if have is None:
            unmeasured.append(label)
            continue
        if want is None:
            drift.append({"fact": label, "claimed": None, "measured": have,
                          "note": "page does not state this"})
        elif want != have:
            drift.append({"fact": label, "claimed": want, "measured": have})

    # Agents named in .claude/agents/ but never mentioned anywhere on the page.
    html = PAGE.read_text(encoding="utf-8")
    missing_agents = [a for a in got["agent_names"] if a not in html]

    result = {
        "page": str(PAGE.relative_to(ROOT)),
        "as_of_on_page": says.get("as_of"),
        "measured": {k: v for k, v in got.items() if k != "agent_names"},
        "claimed": says,
        "drift": drift,
        "agents_not_mentioned": missing_agents,
        "unmeasured_here": unmeasured,
        "not_verifiable_offline": [
            "which systemd units are actually ACTIVE on the droplet",
            "live gate progress and any P&L",
        ],
        "stale": bool(drift or missing_agents),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 1 if result["stale"] else 0

    print(f"system map freshness — {result['page']} (as of {says.get('as_of')})")
    if drift:
        print("\n  DRIFTED facts:")
        for d in drift:
            note = f"  [{d['note']}]" if d.get("note") else ""
            print(f"    {d['fact']:<18} page says {d['claimed']}, "
                  f"repo says {d['measured']}{note}")
    if missing_agents:
        print("\n  AGENTS the page never mentions:")
        for a in missing_agents:
            print(f"    {a}")
    if unmeasured:
        print(f"\n  could not measure here: {', '.join(unmeasured)}")
    print("\n  not verifiable without the droplet:")
    for x in result["not_verifiable_offline"]:
        print(f"    {x}")
    print(f"\n  VERDICT: {'STALE' if result['stale'] else 'current'}")
    return 1 if result["stale"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
