#!/bin/bash
# Pull collected snapshots from the droplet into local data/live/ for analysis.
cd "$(dirname "$0")" || exit 1
rsync -avz --ignore-existing \
  root@129.212.176.202:/opt/mlb-props/mlb_props_research/data/live/snapshots_*.jsonl \
  data/live/
echo "synced. local snapshot days:"
ls -1 data/live/snapshots_*.jsonl | wc -l
