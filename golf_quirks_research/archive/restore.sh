#!/usr/bin/env bash
# Expand the committed golf-quirks caches back into golf_quirks_research/data/.
# See README.md in this directory for what each archive holds and why it is committed.
#
#   restore.sh [--force] [--published-only]
#
# Default restores the 2026-07-28 WIDENED caches (404 leader markets / 13,473
# candles) — the current state of the sample. They are strict supersets of the
# published 364, so `backtest_fade_fills.py --validate` (which pins itself to
# published_universe_364.json) reproduces the published cells either way.
# --published-only restores the frozen 364 instead, e.g. to confirm that the
# pinning is doing the work rather than the cache happening to agree.
set -euo pipefail

ARCHIVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$(dirname "$ARCHIVE_DIR")/data"

FORCE=""
SUFFIX="_widened_20260728"
for arg in "$@"; do
  case "$arg" in
    --force)           FORCE="--force" ;;
    --published-only)  SUFFIX="" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [ -d "$DATA_DIR" ] && [ -n "$(ls -A "$DATA_DIR" 2>/dev/null)" ] && [ "$FORCE" != "--force" ]; then
  echo "refusing to overwrite non-empty $DATA_DIR (pass --force if you mean it)" >&2
  exit 1
fi

mkdir -p "$DATA_DIR"
for t in leader_trades candles; do
  # tar merges into an existing tree, so restoring --published-only over a
  # widened tree would leave the 40 extra markets in place and silently hand
  # back the wrong universe. Clear the subdir first; everything in it comes
  # from the tarball we are about to expand.
  if [ -d "$DATA_DIR/$t" ]; then
    echo "==> clearing $DATA_DIR/$t"
    rm -rf "${DATA_DIR:?}/$t"
  fi
  echo "==> $t$SUFFIX"
  tar xzf "$ARCHIVE_DIR/$t$SUFFIX.tar.gz" -C "$DATA_DIR"
done
# The P-023 cohorts. Omitted before 2026-07-28, which was silent rather than
# loud: backtest_makecut_fills.py ran to completion against an absent cache and
# wrote a results file with every cohort empty.
for t in makecut_trades livtopn_trades topn_full_trades topn_round_trades; do
  echo "==> $t"
  rm -rf "${DATA_DIR:?}/$t"
  tar xzf "$ARCHIVE_DIR/$t.tar.gz" -C "$DATA_DIR"
done
echo "==> pull_logs"
tar xzf "$ARCHIVE_DIR/pull_logs.tar.gz" -C "$DATA_DIR"
echo "==> schedule_probe_caches"
tar xzf "$ARCHIVE_DIR/schedule_probe_caches.tar.gz" -C "$DATA_DIR"
echo "==> settled_meta.jsonl"
gunzip -c "$ARCHIVE_DIR/settled_meta.jsonl.gz" > "$DATA_DIR/settled_meta.jsonl"

echo "restored to $DATA_DIR:"
du -sh "$DATA_DIR"
