#!/usr/bin/env python3
"""Refresh authoritative settlement documents and invalidate changed policies."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.settlement_registry import SettlementRegistry  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "config" / "settlement_equivalence.yaml")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data" / "settlement_registry")
    args = parser.parse_args(argv)
    state = SettlementRegistry.load(args.config).refresh(args.output_dir)
    print("settlement registry: status={} documents={} changed={}".format(
        state["status"], len(state["documents"]),
        len(state["changed_documents"])))
    return 0 if state["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
