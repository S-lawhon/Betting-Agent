#!/bin/bash
# Daily driver for the multi-week live execution validation.
# Runs the snapshot collector for the day's slate, then records paper entries.
#
# ── DO NOT CRON-INSTALL THIS ON THE DROPLET ────────────────────────────────
# Scheduled collection on the droplet is owned by the systemd timer:
#   scripts/mlb-props-collector.timer -> scripts/mlb-props-collector.service
# (installed to /etc/systemd/system/, running out of /opt/mlb-props).
#
# Adding the cron line below alongside that timer would run TWO collectors
# against one Kalshi account. Kalshi throttles on shared per-IP rate, so the
# second copy does not just duplicate data — it pushes the host over the
# ~7 req/s pain threshold and starves the trading services. The timer also
# passes --rate 2.0; this script does not, so a cron copy would burst at the
# 4.5 req/s client default. See collector.py's module docstring.
#
# This remains useful for a MANUAL local backfill run on the Mac:
#   bash run_daily_collection.sh
# Logs to data/live/daily_<date>.log
#
# NOTE: the systemd unit runs the collector leg ONLY. The paper_entries settle
# leg below has never been scheduled anywhere; it is still a manual step.

cd "$(dirname "$0")" || exit 1
DATE=$(date +%Y%m%d)
LOG="data/live/daily_${DATE}.log"

echo "=== collection start $(date) ===" >> "$LOG"

# collect through end of the night slate (local hour, 24h decimal)
python3 collector.py 23.75 >> "$LOG" 2>&1

echo "=== collection end $(date) ===" >> "$LOG"

# settle yesterday's paper entries once results are final
python3 paper_entries.py --settle >> "$LOG" 2>&1

echo "=== settle done $(date) ===" >> "$LOG"
