#!/usr/bin/env python3
"""
scripts/maker_challenger_report.py
──────────────────────────────────
Tier-2 champion/challenger report for the P-016 Live Maker pilot.

Replays conservative challenger arms against the champion's recorded tape
and writes a PROPOSAL to live_agent_research/proposals/.

WRITES EXACTLY ONE THING: a markdown file under the proposals directory.
It does not edit config, parameters, pod state, the anchor cache, the
diagnostics state file, or the fills/quotes logs.  Per
RECURSIVE_REVIEW_DESIGN.md:

    "the scheduled agent writes proposals to live_agent_research/
     proposals/ and never edits config. Proposal -> human review ->
     manual commit with logged before/after."

It also makes NO API calls — the tape is already on disk — so it adds
zero load to the shared Kalshi rate budget (cf. src/book_capture.py's
RateBudget, which exists because that budget is shared with two live
trading services).

Usage:
    python3 -m scripts.maker_challenger_report [--fills PATH]
        [--out-dir live_agent_research/proposals] [--stdout]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.maker_challenger import (  # noqa: E402
    CHAMPION_ARM, DEFAULT_ARMS, GATE_HORIZON_S, ArmResult, ArmSpec,
    _mean, _pstdev, arms_from_config, build_tape, cluster_bootstrap_delta,
    detectable_effect_c, evaluate_arm, intracluster_correlation,
    verify_subset,
)

DEFAULT_FILLS = "data/trade_logs/maker_fills.jsonl"
DEFAULT_OUT_DIR = "live_agent_research/proposals"


def load_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a torn last line during live append
    return out


def render(champion: ArmResult, results: List[ArmResult],
           fills_path: Path) -> str:
    n_arms = len(results)
    icc, deff = intracluster_correlation(champion)
    sd = _pstdev([f.markout_c for f in champion.fills])
    now = datetime.now(timezone.utc)

    L: List[str] = []
    a = L.append
    a(f"# P-016 Tier-2 challenger proposal — {now.date().isoformat()}")
    a("")
    a("**Status:** PROPOSAL. Nothing has been applied. No config, "
      "parameter, or pod state was modified by this run.")
    a(f"**Generated:** {now.isoformat()} by "
      "`scripts/maker_challenger_report.py`")
    a(f"**Tape:** `{fills_path}` — champion fills only, +{GATE_HORIZON_S}s "
      "markout, fee-adjusted (general maker rate).")
    a("")
    a("## Champion (frozen)")
    a("")
    a(f"- Fills with a +{GATE_HORIZON_S}s markout: **{champion.n}** "
      f"across **{champion.games}** games")
    a(f"- Mean fee-adjusted markout: **{champion.mean_markout_c:+.2f}c** "
      f"(sd {sd:.2f}c)")
    a(f"- ICC by game {icc:.4f}, design effect {deff:.2f} → effective n "
      f"≈ {champion.n / deff:.0f}" if deff == deff else "- ICC: n/a")
    a("")
    a("The champion is mid-gate and MUST NOT be altered. Every arm below "
      "is a conservative transform evaluated offline against the tape the "
      "champion already produced; none of them quoted, and none of them "
      "could have changed a champion fill.")
    a("")
    a("## Arms")
    a("")
    a("| arm | fills | retention | mean markout | Δ vs champion | "
      f"{100*(1-0.05/n_arms):.1f}% CI (Bonferroni k={n_arms}) | P(better) | "
      "mean markout of fills given up |")
    a("|---|---:|---:|---:|---:|---|---:|---:|")
    a(f"| champion | {champion.n} | 100% | "
      f"{champion.mean_markout_c:+.2f}c | — | — | — | — |")

    stats = []
    for r in results:
        bs = cluster_bootstrap_delta(champion, r, n_arms=n_arms)
        stats.append((r, bs))
        a(f"| {r.spec.name} | {r.n} | {r.retention:.0%} | "
          f"{r.mean_markout_c:+.2f}c | {bs['delta_c']:+.2f}c | "
          f"[{bs['ci_lo_c']:+.2f}, {bs['ci_hi_c']:+.2f}]c | "
          f"{bs['p_improve']:.2f} | {r.mean_dropped_markout_c:+.2f}c |")
    a("")
    for r in results:
        a(f"- **{r.spec.name}** — {r.spec.description}")
    a("")

    a("## Power")
    a("")
    a(f"At the champion's observed sd of {sd:.2f}c and design effect "
      f"{deff:.2f}, with Bonferroni correction for k={n_arms} arms, the "
      "minimum detectable difference at 80% power is:")
    a("")
    a("| arm fills | min detectable Δ |")
    a("|---:|---:|")
    for n in (champion.n, 500, 1000, 2000, 4000):
        a(f"| {n:,} | {detectable_effect_c(n, deff, sd, n_arms):.2f}c |")
    a("")
    a("These use the UNPAIRED formula and are therefore an upper bound: "
      "arms share the champion's tape, so game-level shocks partly cancel "
      "in the difference and the realised bootstrap CIs above are tighter.")
    a("")

    a("## Verdict")
    a("")
    promising = [(r, b) for r, b in stats if b["ci_lo_c"] > 0]
    if promising:
        for r, b in promising:
            a(f"- **{r.spec.name}** clears zero on the corrected interval "
              f"(Δ {b['delta_c']:+.2f}c, CI [{b['ci_lo_c']:+.2f}, "
              f"{b['ci_hi_c']:+.2f}]c). This is a candidate for human "
              "review — NOT an approval, and not to be applied while the "
              "champion's 500-fill gate is running.")
    else:
        a("- **No arm clears zero** on its multiple-comparison-corrected "
          "interval. No change is proposed. The correct action is to keep "
          "collecting tape.")
    a("")
    a("## Standing constraints")
    a("")
    a("1. The champion is frozen until the 500-fill gate resolves. Even an "
      "arm that wins here must not be applied before then — swapping "
      "parameters mid-gate destroys the pre-registered sample.")
    a("2. Only conservative arms are evaluable: the tape records only "
      "prints that crossed the champion's quotes, so a tighter-quoting "
      "arm's fills do not exist in it. Tightening is Tier 3 regardless.")
    a("3. RECURSIVE_REVIEW_DESIGN.md requires hard `[min, max]` bounds per "
      "auto-adjustable parameter but does not record them. None were "
      "invented. Numeric bounds are a human decision and are a "
      "prerequisite for any future auto-apply layer.")
    a("")
    return "\n".join(L)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fills", default=DEFAULT_FILLS)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--config", default=None,
                    help="optional YAML to read pods.P-016.challenger.arms")
    ap.add_argument("--stdout", action="store_true",
                    help="print the proposal instead of writing a file")
    args = ap.parse_args(argv)

    fills_path = Path(args.fills)
    records = load_jsonl(fills_path)
    tape = build_tape(records)
    if not tape:
        print(f"maker_challenger: no usable tape in {fills_path} "
              f"(need FILL + {GATE_HORIZON_S}s MARKOUT pairs)")
        return 1

    config = None
    if args.config:
        import yaml
        config = yaml.safe_load(Path(args.config).read_text())
    specs: tuple = arms_from_config(config)

    champion = evaluate_arm(tape, CHAMPION_ARM)
    results = []
    for spec in specs:
        r = evaluate_arm(tape, spec)
        verify_subset(champion, r)     # observability guarantee, checked
        results.append(r)

    text = render(champion, results, fills_path)
    if args.stdout:
        print(text)
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = out_dir / f"P016_tier2_challenger_{stamp}.md"
    out.write_text(text)
    print(f"maker_challenger: wrote {out} "
          f"({champion.n} champion fills, {len(results)} arms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
