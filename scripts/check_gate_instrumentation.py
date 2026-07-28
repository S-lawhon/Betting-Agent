#!/usr/bin/env python3
"""
scripts/check_gate_instrumentation.py
─────────────────────────────────────
Enforce `docs/GATE_INSTRUMENTATION_STANDARD.md` against every registered gate.

    python3 -m scripts.check_gate_instrumentation
    python3 -m scripts.check_gate_instrumentation --json
    python3 -m scripts.check_gate_instrumentation --root /path/to/a/checkout

Exit 0 = every gate is instrumented.  Exit 1 = at least one is not.

WHY THIS EXISTS
───────────────
The 2026-07-28 throughput audit measured that this fund's constraint is
neither hypothesis supply nor observation supply.  It settles plenty — 432
P-001, 54 P-014, 38 P-017, 5 P-015 in 28 days.  **The scarce resource is
correctly instrumented observations**, and four of five gates were not
measuring anything, each for a different reason:

    P-001   CLV rows lag settlement; admissibility unmeasured
    P-014   NO SANCTIONED READER EXISTED AT ALL
    P-015   the reader pointed at `data/pods/P-015.jsonl`, a file that has
            never existed, and reported 0 against 5 real settlements
    P-017   working — the control
    P-022   could not compute its own placement window

Three were fixed by hand.  Nothing prevented the fourth recurrence, and this
repo has now produced the same class of defect five times — *"a filter
asserted in a docstring and applied in one call path is not a filter."*

WHAT THIS IS NOT
────────────────
It does not judge a gate's verdict, threshold, or decision rule.  It asks only
whether the gate is CAPABLE of measuring the thing it claims to measure.  A
gate can be fully instrumented and still be a bad gate.

Every check below is derived from a failure that actually happened here, and
each names it.  Checks that can only be verified PARTIALLY by machine say so
in their own `partial` flag rather than implying more rigour than they have.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:                                # pragma: no cover
    sys.stderr.write("PyYAML required\n")
    raise

# `blocked_on` vocabulary. A label outside this set is itself a finding: the
# whole point is that "time" was being used for gates that were blocked on a
# defect, on an unmeasured rate, and on a missing decision rule.
BLOCKED_ON_VOCAB = {
    "time":          "a sample is accruing at a KNOWN rate toward a defined gate",
    "defect":        "something is broken; calendar will not fix it",
    "measurement":   "the accrual rate is not yet measured, so no date can be stated",
    "backtest":      "code exists and is inert, waiting on its own validation study",
    "decision-rule": "observations are accruing but no pre-registered rule exists",
    "human":         "waiting on Sam",
    "data":          "waiting on a data pull that is already running",
    "external":      "waiting on a third party",
    "nothing":       "running normally",
}

import re

# Gate `source` values `manager/checks.py::_gate_progress` can actually
# resolve. A gate naming anything else is UNREADABLE BY CONSTRUCTION — P-014's
# failure, where `source: trade_log` mapped to nothing and the manager returned
# None while 345 settled rows sat in the log.
#
# Read out of checks.py rather than hard-coded here. Duplicating the mapping
# would make this check agree with a copy instead of with the code that runs,
# which is the same mistake in a new place.
_SOURCE_RE = re.compile(r'"(\w+_checkpoint)"\s*:\s*"')


def known_sources(root: Path) -> Optional[set]:
    """Sources the manager can resolve, or None if checks.py is unreadable."""
    p = root / "manager" / "checks.py"
    try:
        return set(_SOURCE_RE.findall(p.read_text(encoding="utf-8"))) or None
    except OSError:
        return None


class Result:
    def __init__(self, pod: str, check: str, ok: Optional[bool], detail: str,
                 partial: bool = False):
        self.pod, self.check, self.ok = pod, check, ok
        self.detail, self.partial = detail, partial

    def as_dict(self) -> Dict[str, Any]:
        return {"pod": self.pod, "check": self.check,
                "ok": self.ok, "partial": self.partial, "detail": self.detail}


# ── helpers ──────────────────────────────────────────────────────────

def _load_registry(root: Path) -> Dict[str, Any]:
    p = root / "manager" / "registry.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _reader_path(root: Path, gate: Dict[str, Any]) -> Optional[Path]:
    r = gate.get("reader")
    if r:
        p = root / r
        return p if p.exists() else None
    src = gate.get("source")
    if src:
        p = root / "scripts" / f"{src}.py"
        return p if p.exists() else None
    return None


def _import_reader(path: Path):
    """Import a reader module by path, isolated under its own name."""
    spec = importlib.util.spec_from_file_location(
        f"_gatecheck_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _declared_logs(mod) -> List[str]:
    """Every log path/glob the reader declares, across the naming conventions
    the readers actually use."""
    out: List[str] = []
    for name in ("DEFAULT_LOGS", "LOG_PATTERNS", "DEFAULT_PATTERNS"):
        v = getattr(mod, name, None)
        if isinstance(v, (list, tuple)):
            out.extend(str(x) for x in v)
    for name in ("DEFAULT_TRADE_LOG", "DEFAULT_CLV_LOG", "QUOTES_LOG",
                 "FILLS_LOG"):
        v = getattr(mod, name, None)
        if v is not None:
            out.append(str(v))
    return out


def _first_rows(root: Path, pattern: str, pod: str, limit: int = 400000) -> int:
    """Rows mentioning `pod` behind a path or glob, bounded."""
    n = 0
    for p in sorted(glob.glob(str(root / pattern)))[:12]:
        op = gzip.open if p.endswith(".gz") else open
        try:
            with op(p, "rt", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > limit:
                        break
                    if pod in line:
                        n += 1
        except OSError:
            continue
    return n


def _run_reader(root: Path, reader: Path, extra: Optional[List[str]] = None
                ) -> Tuple[int, Dict[str, Any], str]:
    """Run a reader as a subprocess with --json, from `root` as cwd."""
    cmd = [sys.executable, "-m", f"scripts.{reader.stem}", "--json"] + (extra or [])
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True,
                              text=True, timeout=300,
                              env={**os.environ, "PYTHONPATH": str(root)})
    except (subprocess.SubprocessError, OSError) as exc:
        return -1, {}, f"{type(exc).__name__}: {exc}"
    try:
        return proc.returncode, json.loads(proc.stdout or "{}"), proc.stderr[-400:]
    except json.JSONDecodeError:
        return proc.returncode, {}, (proc.stdout or proc.stderr)[-400:]


# ── the eight checks ─────────────────────────────────────────────────

def check_pod(root: Path, ws: Dict[str, Any]) -> List[Result]:
    pod = ws.get("id") or "?"
    gate = ws.get("gate") or {}
    out: List[Result] = []
    R = lambda c, ok, d, partial=False: out.append(Result(pod, c, ok, d, partial))  # noqa: E731

    # 1. A sanctioned reader exists and is named.
    #    P-014 declared `metric`, `source: trade_log` and `threshold: 500` with
    #    NO READER AT ALL, so the gate was unreadable by construction.
    reader = _reader_path(root, gate)
    if reader is None:
        R("1_sanctioned_reader", False,
          "no reader: gate names source={!r} reader={!r} and neither resolves "
          "to a file. The gate number has no sanctioned path."
          .format(gate.get("source"), gate.get("reader")))
        # Every remaining check needs the reader; report them as unknown rather
        # than as passes.
        for c in ("2_reads_the_real_file", "3_fails_to_none",
                  "5_emits_progress_and_threshold", "6_gate_key_matches"):
            R(c, None, "not evaluated — no reader")
    else:
        R("1_sanctioned_reader", True, f"reader: {reader.relative_to(root)}")

        # 2. The reader points at the file the pod ACTUALLY writes.
        #    P-015's reader globbed `data/pods/P-015.jsonl`, which has never
        #    existed, and reported 0 against 5 real settlements.
        try:
            mod = _import_reader(reader)
            declared = _declared_logs(mod)
        except Exception as exc:                   # noqa: BLE001
            declared = []
            R("2_reads_the_real_file", False,
              f"reader will not import: {type(exc).__name__}: {exc}")
        else:
            existing = [d for d in declared
                        if glob.glob(str(root / d)) or (root / d).exists()]
            rows = sum(_first_rows(root, d, pod) for d in existing[:6])
            any_rows = sum(_first_rows(root, d, "pod_id") for d in existing[:6])
            if not declared:
                R("2_reads_the_real_file", None,
                  "reader declares no log constants this check knows how to "
                  "read; verify by hand", partial=True)
            elif not existing:
                # P-015's exact bug: the reader globbed data/pods/P-015.jsonl,
                # which has never existed, and reported 0 against 5 real
                # settlements. A declared path that cannot exist is the failure
                # this check is for, and it is host-independent.
                R("2_reads_the_real_file", False,
                  "NONE of the reader's declared paths exist: {}"
                  .format(", ".join(declared[:4])))
            elif any_rows == 0:
                # The paths are real but this checkout carries no trade data at
                # all (the Mac; `data/` is not synced). Unknown, NOT a failure:
                # a check that fails on every developer machine gets ignored,
                # and an ignored check is the thing being prevented.
                R("2_reads_the_real_file", None,
                  "declared paths exist but this host carries no trade data at "
                  "all — run on the droplet to evaluate", partial=True)
            elif rows == 0:
                # LIMITATION, stated rather than left to be discovered: this
                # cannot distinguish "the reader points at the wrong file" from
                # "the pod has genuinely never produced a row". P-022 is the
                # live example of the second. It fails either way, because a
                # reader that has never been proven against a real row is
                # exactly what P-015 looked like for months.
                R("2_reads_the_real_file", False,
                  "reader's paths hold {} rows but NONE mentioning {}: {} "
                  "(either the path is wrong, or the pod has never produced a "
                  "row — both need a human)"
                  .format(any_rows, pod, ", ".join(existing[:4])))
            else:
                R("2_reads_the_real_file", True,
                  f"{rows} row(s) for {pod} behind {len(existing)} existing path(s)")

        # 3. The reader returns None / an explained zero when it cannot read —
        #    never a stale YAML value and never a fabricated default. The
        #    precedents are P-015's `or 0.9` breakeven, which invented a price
        #    for any row missing one, and the hand-typed `progress: 1`.
        with tempfile.TemporaryDirectory() as td:
            blank = Path(td)
            (blank / "scripts").mkdir()
            for f in (root / "scripts").glob("*.py"):
                (blank / "scripts" / f.name).write_bytes(f.read_bytes())
            for d in ("src", "manager"):
                if (root / d).exists():
                    os.symlink(root / d, blank / d)
            code, payload, err = _run_reader(blank, reader)
            prog = payload.get("progress") if payload else None
            if code == -1:
                R("3_fails_to_none", False, f"reader crashed on an empty root: {err}")
            elif payload and prog not in (None, 0, 0.0):
                R("3_fails_to_none", False,
                  f"reader reports progress={prog!r} with NO data present — "
                  "that number came from somewhere other than observations")
            elif not payload and code != 0 and err.strip():
                # Refusing loudly is the CORRECT behaviour and is what this
                # check is really asking for. What it forbids is inventing a
                # number, not declining to produce one.
                R("3_fails_to_none", True,
                  f"refuses to produce a number with no data: {err.strip()[:90]}")
            elif not payload:
                R("3_fails_to_none", False,
                  f"reader emitted no JSON and no error on an empty root "
                  f"(exit {code}) — silence is the one answer it must not give")
            else:
                R("3_fails_to_none", True,
                  f"progress={prog!r} with no data, and it explains itself: "
                  f"{str(payload.get('reason') or payload.get('verdict'))[:70]}")

        # 5. The gate is READABLE, not merely computable: progress AND
        #    threshold both come out of the sanctioned reader.
        code, payload, err = _run_reader(root, reader)
        if not payload:
            R("5_emits_progress_and_threshold", False,
              f"reader emitted no JSON: {err[:160]}")
            R("6_gate_key_matches", None, "not evaluated — reader emitted nothing")
        else:
            has_p = "progress" in payload
            has_t = payload.get("threshold") is not None
            R("5_emits_progress_and_threshold", has_p and has_t,
              f"progress={payload.get('progress')!r} threshold={payload.get('threshold')!r}")

            # 6. The gate keys off the field the pod ACTUALLY sets.
            #    The JDAY re-book set `outcome` while p017_checkpoint keys off
            #    `action`, so the gate did not move and the fix looked applied.
            #    Signature: rows exist for the pod, the reader finds none, and
            #    it offers no reason why.
            prog = payload.get("progress")
            # `verdict` counts as an explanation: P-001 explains its zero there
            # ("clean forward sample is empty; the epoch is ..."), and treating
            # that as unexplained was a false positive on the first live run.
            reason = str(payload.get("reason") or payload.get("verdict") or "")
            rows_exist = any(
                _first_rows(root, d, pod) > 0
                for d in (declared or [])[:6])
            if rows_exist and prog in (0, 0.0) and not reason:
                R("6_gate_key_matches", False,
                  f"rows for {pod} exist but the reader counts 0 and gives no "
                  "reason — the JDAY signature (outcome set, action keyed)")
            else:
                R("6_gate_key_matches", True,
                  "reader counts {} and explains any zero".format(prog),
                  partial=True)

    # 4. A pre-registered decision rule exists.
    rule = gate.get("rule_document") or ws.get("rule_document")
    status = str(gate.get("rule_status") or "")
    # A rule that exists but is not NAMED by the gate is a weaker failure than
    # no rule at all, and it is a different fix, so they are reported apart.
    stem = str(pod).replace("-", "")
    found = [str(f.relative_to(root))
             for f in root.rglob(f"{stem}_DECISION_RULE.md")]
    if rule and (root / str(rule)).exists():
        R("4_decision_rule", True, f"rule: {rule}")
    elif rule:
        R("4_decision_rule", False, f"gate names rule_document {rule!r}, which does not exist")
    elif status.upper().startswith("MISSING"):
        R("4_decision_rule", False, f"rule_status: {status}")
    elif found:
        R("4_decision_rule", False,
          "a rule exists at {} but the gate does not NAME it, so the locked "
          "document and the gate are joined only by convention"
          .format(found[0]))
    else:
        R("4_decision_rule", False,
          "no rule document anywhere, no rule_document, no rule_status — a "
          "threshold with no kill line, no promotion condition and no "
          "hard-kill z")

    # 7. Every consumer of the gate number is enumerated, not just the
    #    documented one. Mechanised as: the manager can actually RESOLVE the
    #    declared source. P-014 declared `source: trade_log`, which
    #    _gate_progress maps to nothing, so the manager returned None while the
    #    reader (had it existed) would have said otherwise.
    src = gate.get("source")
    known = known_sources(root)
    if not src:
        R("7_source_resolvable", False, "gate declares no source")
    elif known is None:
        R("7_source_resolvable", None,
          "manager/checks.py not readable from this root — cannot verify",
          partial=True)
    elif src not in known:
        R("7_source_resolvable", False,
          f"source={src!r} is not resolvable by manager/checks.py::"
          "_gate_progress — the gate is unreadable by the manager whatever the "
          "reader says")
    else:
        R("7_source_resolvable", True, f"source={src} resolves")

    # 8. A projection exists, or the gate is explicitly unprojectable. Never a
    #    made-up date. `manager/throughput.py` only projects `tier: validating`
    #    gates and renders anything else as None, which is the correct
    #    behaviour — what this rejects is a hand-typed date living beside it.
    # `locked_date` and the like are historical facts, not projections. What
    # this rejects is a FORWARD date nobody derives — the registry's
    # "late Aug - early Sept 2026" for P-001, which was unachievable at the
    # measured rate and had no reader behind it.
    hand_dates = [k for k in gate
                  if (k.startswith(("expected_", "eta", "forecast_"))
                      or k.endswith(("_eta", "_by")))
                  and not k.startswith("projected_")]
    if hand_dates:
        R("8_projection_not_invented", False,
          f"gate carries hand-written date field(s) {hand_dates} that no reader "
          "derives")
    else:
        R("8_projection_not_invented", True,
          "no hand-written date fields in the gate", partial=True)

    # blocked_on vocabulary, and labels contradicted by data.
    label = ws.get("blocked_on")
    if label not in BLOCKED_ON_VOCAB:
        R("9_blocked_on_vocabulary", False,
          f"blocked_on={label!r} is not in the vocabulary {sorted(BLOCKED_ON_VOCAB)}")
    elif label == "time" and reader is None:
        R("9_blocked_on_vocabulary", False,
          "blocked_on: time, but there is no reader — it is blocked on a "
          "DEFECT, and `time` says a sample is accruing at a known rate")
    elif label == "time" and not rule and status.upper().startswith("MISSING"):
        R("9_blocked_on_vocabulary", False,
          "blocked_on: time, but no decision rule exists — the honest label is "
          "`decision-rule`")
    else:
        R("9_blocked_on_vocabulary", True, f"blocked_on: {label}")

    return out


def run(root: Path, only: Optional[List[str]] = None) -> List[Result]:
    reg = _load_registry(root)
    out: List[Result] = []
    for ws in reg.get("workstreams", []) or []:
        pod = ws.get("id") or ""
        if not ws.get("gate"):
            continue
        if not str(pod).startswith("P-"):
            continue
        if only and pod not in only:
            continue
        out.extend(check_pod(root, ws))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Enforce docs/GATE_INSTRUMENTATION_STANDARD.md")
    ap.add_argument("--root", default=".")
    ap.add_argument("--pod", action="append")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()

    try:
        results = run(root, a.pod)
    except Exception as exc:                       # noqa: BLE001
        # A read failure is a FAILURE, never a skip — the same rule the fee
        # fixture follows. A checker that exits 0 because it could not run is
        # indistinguishable from a clean bill of health.
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"})
              if a.json else f"CHECK FAILED TO RUN: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    failed = [r for r in results if r.ok is False]
    if a.json:
        print(json.dumps({"results": [r.as_dict() for r in results],
                          "n_failed": len(failed)}, indent=1))
        return 1 if failed else 0

    pods = sorted({r.pod for r in results})
    print("Gate instrumentation standard")
    print("=" * 72)
    for pod in pods:
        rows = [r for r in results if r.pod == pod]
        bad = [r for r in rows if r.ok is False]
        print(f"\n  {pod}  {'FAIL' if bad else 'pass'}  "
              f"({len(bad)} of {len(rows)} checks failing)")
        for r in rows:
            mark = {True: "ok  ", False: "FAIL", None: "?   "}[r.ok]
            flag = " (partial)" if r.partial else ""
            print(f"    [{mark}] {r.check:32s} {r.detail}{flag}")
    print("\n" + "=" * 72)
    print(f"  {len(failed)} failing check(s) across {len(pods)} gate(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
