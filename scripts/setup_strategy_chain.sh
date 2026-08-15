#!/usr/bin/env bash
set -euo pipefail

# Install/check the deterministic recorder plus its bounded role executor.
# Run on the droplet as root from /opt/betting-pod-shop.

ROOT=/opt/betting-pod-shop
ACTION=${1:-check}

fail() { echo "ERROR: $*" >&2; exit 1; }

check_files() {
  [[ $(id -u) -eq 0 ]] || fail "run as root"
  [[ -x "$ROOT/venv/bin/python" ]] || fail "project venv missing"
  for unit in betting-strategy-agents.service \
      betting-strategy-chain.service betting-strategy-chain.timer; do
    [[ -f "$ROOT/scripts/systemd/$unit" ]] || fail "$unit missing"
  done
  [[ -f "$ROOT/config/strategy_chain_runtime.yaml" ]] || fail "chain config missing"
}

install_units() {
  check_files
  install -d -o bettingbot -g bettingbot -m 0750 \
    "$ROOT/data/strategy_agents" "$ROOT/data/strategy_agents/queue" \
    "$ROOT/data/strategy_agents/tasks"
  chown -R bettingbot:bettingbot "$ROOT/data/strategy_agents"
  for unit in betting-strategy-agents.service \
      betting-strategy-chain.service betting-strategy-chain.timer; do
    install -o root -g root -m 0644 "$ROOT/scripts/systemd/$unit" \
      "/etc/systemd/system/$unit"
  done
  systemctl daemon-reload
  systemctl enable --now betting-strategy-agents.service
  systemctl enable --now betting-strategy-chain.timer
}

check_units() {
  check_files
  systemctl show betting-strategy-agents.service \
    --property=LoadState,ActiveState,SubState,Result --no-pager
  systemctl show betting-strategy-chain.service \
    --property=LoadState,ActiveState,SubState,Result --no-pager
  systemctl show betting-strategy-chain.timer \
    --property=LoadState,ActiveState,UnitFileState --no-pager
  systemctl list-timers betting-strategy-chain.timer --all --no-pager
}

case "$ACTION" in
  install) install_units; check_units ;;
  check) check_units ;;
  *) fail "usage: $0 {install|check}" ;;
esac
