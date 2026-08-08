#!/usr/bin/env python3
"""Emit a blinded research calibration suite or grade completed observations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.research_calibration import (  # noqa: E402
    ResearchCalibrationError,
    blinded_suite,
    grade_suite,
)


def _read_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ResearchCalibrationError("calibration file must be an object")
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", type=Path,
        default=ROOT / "config/research_calibration_cases.yaml")
    parser.add_argument(
        "--results", type=Path,
        help="JSON object with an observations array; omit to emit blinded cases")
    args = parser.parse_args(argv)
    try:
        suite = _read_yaml(args.cases)
        if args.results:
            results = json.loads(args.results.read_text(encoding="utf-8"))
            observations = results.get("observations") if isinstance(
                results, dict) else None
            if not isinstance(observations, list):
                raise ResearchCalibrationError(
                    "results must contain an observations array")
            output = grade_suite(suite, observations)
            code = 0 if output["passed"] else 1
        else:
            output = blinded_suite(suite)
            code = 0
        print(json.dumps(output, indent=2, sort_keys=True))
        return code
    except (OSError, ValueError, yaml.YAMLError, json.JSONDecodeError,
            ResearchCalibrationError) as exc:
        print(f"research calibration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
