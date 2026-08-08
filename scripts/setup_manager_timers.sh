#!/usr/bin/env bash
# setup_manager_timers.sh — one-time installer for manager timer automation on
# the droplet. Run from the Mac (or any host with SSH access).
#
# What it does:
#   1) Verifies the deployed tree has manager-* unit files.
#   2) Installs manager-{collect,alert,brief,gate-rollup}.{service,timer} into
#      /etc/systemd/system.
#   3) daemon-reload + enable --now the four timers.
#   4) Primes the gate rollup, then runs collect/alert and prints timer status.
#
# Prerequisite:
#   The repo has already been synced to /opt/betting-pod-shop on the droplet.

set -euo pipefail

DROPLET="${DROPLET:-root@129.212.176.202}"
KEY="${KEY:-$HOME/.ssh/betting_deploy}"
APP="${APP:-/opt/betting-pod-shop}"
MIRROR="${MIRROR:-/opt/betting-agent-mirror}"

UNITS=(
  manager-collect.service
  manager-collect.timer
  manager-alert.service
  manager-alert.timer
  manager-brief.service
  manager-brief.timer
  manager-gate-rollup.service
  manager-gate-rollup.timer
)

dssh() { ssh -i "$KEY" -o IdentitiesOnly=yes "$DROPLET" "$@"; }
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "preflight: manager unit files exist in deployed tree"
dssh "for u in ${UNITS[*]}; do test -f '$APP/scripts/systemd/'\"\$u\" || { echo missing: \$u; exit 1; }; done"

say "install units into /etc/systemd/system"
dssh "for u in ${UNITS[*]}; do cp '$APP/scripts/systemd/'\"\$u\" '/etc/systemd/system/'\"\$u\"; done"

say "repair manager git-mirror ownership"
dssh "if test -d '$MIRROR/.git'; then chown -R bettingbot:bettingbot '$MIRROR'; fi"

dssh "systemctl daemon-reload"

say "enable timers"
dssh "systemctl enable --now manager-collect.timer manager-alert.timer manager-brief.timer manager-gate-rollup.timer"

say "prime gate rollup"
dssh "systemctl start manager-gate-rollup.service"

say "prime snapshot + alert state"
dssh "systemctl start manager-collect.service manager-alert.service"

say "timer status"
dssh "systemctl list-timers --all | grep -E 'manager-(collect|alert|brief|gate-rollup)'"

say "recent logs"
dssh "journalctl -u manager-collect -u manager-alert -u manager-brief -u manager-gate-rollup -n 30 --no-pager"

say "done"
echo "Manager timers are installed and enabled on ${DROPLET}."
