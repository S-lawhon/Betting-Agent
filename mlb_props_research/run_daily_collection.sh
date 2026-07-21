#!/bin/bash
# Daily driver for the multi-week live execution validation.
# Runs the snapshot collector for the day's slate, then records paper entries.
#
# Install as a daily job (NOT installed automatically):
#   crontab -e   ->   30 10 * * *  /path/to/run_daily_collection.sh
# Logs to data/live/daily_<date>.log

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
