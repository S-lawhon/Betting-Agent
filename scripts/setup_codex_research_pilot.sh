#!/usr/bin/env bash
set -euo pipefail

# Install and operate the manual, one-assignment Codex research pilot.
# Run on the droplet as root from /opt/betting-pod-shop.

ROOT=/opt/betting-pod-shop
STATE=/var/lib/research-codex
UNIT=research-agent-codex-pilot.service
ACTION=${1:-check}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ $(id -u) -eq 0 ]] || fail "run as root"
[[ -d "$ROOT" ]] || fail "$ROOT does not exist"
[[ -x /usr/local/bin/codex ]] || fail "/usr/local/bin/codex is not installed"
[[ -x "$ROOT/venv/bin/python" ]] || fail "project virtualenv is unavailable"
[[ -f "$ROOT/scripts/systemd/$UNIT" ]] || fail "pilot unit is unavailable"
[[ -f "$ROOT/config/research_agent_runtime_codex_pilot.yaml" ]] || fail "pilot config is unavailable"

install_runtime() {
  install -d -o bettingbot -g bettingbot -m 0700 "$STATE" "$STATE/workspace"
  install -d -o bettingbot -g bettingbot -m 0750 \
    "$ROOT/data/research_execution" "$ROOT/research/dispositions"
  install -o root -g root -m 0644 \
    "$ROOT/scripts/systemd/$UNIT" "/etc/systemd/system/$UNIT"
  systemctl daemon-reload
}

check_runtime() {
  /usr/local/bin/codex --version
  systemctl show "$UNIT" --property=LoadState,ActiveState,SubState --no-pager
  if [[ -f "$STATE/auth.json" ]]; then
    stat -c 'auth.json owner=%U group=%G mode=%a' "$STATE/auth.json"
  else
    echo "auth.json: MISSING (copy Codex auth before running the pilot)"
  fi
  if [[ -e "$STATE/pilot-enabled" ]]; then
    echo "pilot marker: ENABLED"
  else
    echo "pilot marker: disabled"
  fi
}

run_once() {
  [[ -f "$STATE/auth.json" ]] || fail "$STATE/auth.json is missing"
  chown bettingbot:bettingbot "$STATE/auth.json"
  chmod 0600 "$STATE/auth.json"
  install -o bettingbot -g bettingbot -m 0600 /dev/null "$STATE/pilot-enabled"
  if ! systemctl start "$UNIT"; then
    rm -f "$STATE/pilot-enabled"
    journalctl -u "$UNIT" -n 100 --no-pager
    fail "pilot failed; enable marker removed"
  fi
  rm -f "$STATE/pilot-enabled"
  journalctl -u "$UNIT" -n 100 --no-pager
  echo "Pilot completed; enable marker removed. Review the artifact and disposition before another run."
}

case "$ACTION" in
  install)
    install_runtime
    check_runtime
    ;;
  check)
    check_runtime
    ;;
  run-once)
    install_runtime
    run_once
    ;;
  *)
    fail "usage: $0 {check|install|run-once}"
    ;;
esac
