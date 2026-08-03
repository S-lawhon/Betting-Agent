#!/usr/bin/env bash
set -euo pipefail

# Install/check/remove the fixed-date August 4-8 autonomous research window.
# Run on the droplet as root from /opt/betting-pod-shop.

ROOT=/opt/betting-pod-shop
SERVICE=research-agent-codex-week.service
TIMER=research-agent-codex-week.timer
ACTION=${1:-check}

[[ $(id -u) -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }

install_week() {
  install -o root -g root -m 0644 "$ROOT/scripts/systemd/$SERVICE" "/etc/systemd/system/$SERVICE"
  install -o root -g root -m 0644 "$ROOT/scripts/systemd/$TIMER" "/etc/systemd/system/$TIMER"
  systemctl daemon-reload
  systemctl enable --now "$TIMER"
}

check_week() {
  systemctl show "$TIMER" --property=LoadState,ActiveState,UnitFileState --no-pager
  systemctl list-timers "$TIMER" --all --no-pager
  grep -q 'max_attempts_per_day: 1' "$ROOT/config/research_agent_runtime_codex_pilot.yaml"
  grep -q 'max_input_tokens_per_task: 250000' "$ROOT/config/research_agent_runtime_codex_pilot.yaml"
}

remove_week() {
  systemctl disable --now "$TIMER"
  rm -f "/etc/systemd/system/$SERVICE" "/etc/systemd/system/$TIMER"
  systemctl daemon-reload
}

case "$ACTION" in
  install) install_week; check_week ;;
  check) check_week ;;
  remove) remove_week ;;
  *) echo "usage: $0 {install|check|remove}" >&2; exit 1 ;;
esac
