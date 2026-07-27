#!/usr/bin/env bash
# Expand the committed golf-quirks caches back into golf_quirks_research/data/.
# See README.md in this directory for what each archive holds and why it is committed.
set -euo pipefail

ARCHIVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$(dirname "$ARCHIVE_DIR")/data"
FORCE="${1:-}"

if [ -d "$DATA_DIR" ] && [ -n "$(ls -A "$DATA_DIR" 2>/dev/null)" ] && [ "$FORCE" != "--force" ]; then
  echo "refusing to overwrite non-empty $DATA_DIR (pass --force if you mean it)" >&2
  exit 1
fi

mkdir -p "$DATA_DIR"
for t in leader_trades candles pull_logs; do
  echo "==> $t"
  tar xzf "$ARCHIVE_DIR/$t.tar.gz" -C "$DATA_DIR"
done
echo "==> schedule_probe_caches"
tar xzf "$ARCHIVE_DIR/schedule_probe_caches.tar.gz" -C "$DATA_DIR"
echo "==> settled_meta.jsonl"
gunzip -c "$ARCHIVE_DIR/settled_meta.jsonl.gz" > "$DATA_DIR/settled_meta.jsonl"

echo "restored to $DATA_DIR:"
du -sh "$DATA_DIR"
