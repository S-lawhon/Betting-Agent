#!/usr/bin/env bash
set -euo pipefail

# Install, inspect, or manually run the permanent daily screened Codex
# research service. Run on the droplet as root from /opt/betting-pod-shop.

ROOT=/opt/betting-pod-shop
STATE=/var/lib/research-codex
SERVICE=research-agent-screened-daily.service
TIMER=research-agent-screened-daily.timer
DEEP_SERVICE=research-agent-deep-daily.service
DEEP_TIMER=research-agent-deep-daily.timer
LEGACY_TIMER=research-agent-codex-week.timer
CONFIG=config/research_agent_runtime_screened_daily.yaml
DEEP_CONFIG=config/research_agent_runtime_deep_daily.yaml
ACTION=${1:-check}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

require_runtime() {
  [[ $(id -u) -eq 0 ]] || fail "run as root"
  [[ -d "$ROOT" ]] || fail "$ROOT does not exist"
  [[ -x /usr/local/bin/codex ]] || fail "/usr/local/bin/codex is not installed"
  [[ -x "$ROOT/venv/bin/python" ]] || fail "project virtualenv is unavailable"
  [[ -f "$ROOT/scripts/systemd/$SERVICE" ]] || fail "$SERVICE is unavailable"
  [[ -f "$ROOT/scripts/systemd/$TIMER" ]] || fail "$TIMER is unavailable"
  [[ -f "$ROOT/scripts/systemd/$DEEP_SERVICE" ]] || fail "$DEEP_SERVICE is unavailable"
  [[ -f "$ROOT/scripts/systemd/$DEEP_TIMER" ]] || fail "$DEEP_TIMER is unavailable"
  [[ -f "$ROOT/$CONFIG" ]] || fail "$CONFIG is unavailable"
  [[ -f "$ROOT/$DEEP_CONFIG" ]] || fail "$DEEP_CONFIG is unavailable"
}

install_daily() {
  require_runtime
  install -d -o bettingbot -g bettingbot -m 0700 "$STATE" "$STATE/workspace"
  install -d -o bettingbot -g bettingbot -m 0750 \
    "$ROOT/data/research_execution" \
    "$ROOT/data/research_execution/deep_queue" \
    "$ROOT/data/strategy_agents" "$ROOT/data/strategy_agents/queue" \
    "$ROOT/data/strategy_agents/queue/scout" "$ROOT/research/dispositions"
  [[ -s "$STATE/auth.json" ]] || fail \
    "$STATE/auth.json is missing; complete Codex device authentication first"
  chown bettingbot:bettingbot "$STATE/auth.json"
  chmod 0600 "$STATE/auth.json"

  install -o root -g root -m 0644 \
    "$ROOT/scripts/systemd/$SERVICE" "/etc/systemd/system/$SERVICE"
  install -o root -g root -m 0644 \
    "$ROOT/scripts/systemd/$TIMER" "/etc/systemd/system/$TIMER"
  install -o root -g root -m 0644 \
    "$ROOT/scripts/systemd/$DEEP_SERVICE" "/etc/systemd/system/$DEEP_SERVICE"
  install -o root -g root -m 0644 \
    "$ROOT/scripts/systemd/$DEEP_TIMER" "/etc/systemd/system/$DEEP_TIMER"

  # The August 4-8 fixed-date pilot is historical. Disable it if it remains
  # installed so operators have one unambiguous Codex research schedule.
  systemctl disable --now "$LEGACY_TIMER" >/dev/null 2>&1 || true
  systemctl daemon-reload
  systemctl enable --now "$TIMER"
  systemctl enable --now "$DEEP_TIMER"
}

check_daily() {
  require_runtime
  /usr/local/bin/codex --version
  if [[ -s "$STATE/auth.json" ]]; then
    stat -c 'auth.json owner=%U group=%G mode=%a' "$STATE/auth.json"
  else
    echo "auth.json: MISSING"
  fi
  systemctl show "$TIMER" \
    --property=LoadState,ActiveState,UnitFileState --no-pager
  systemctl list-timers "$TIMER" --all --no-pager
  systemctl show "$SERVICE" \
    --property=LoadState,ActiveState,SubState,Result --no-pager
  systemctl show "$DEEP_TIMER" \
    --property=LoadState,ActiveState,UnitFileState --no-pager
  systemctl list-timers "$DEEP_TIMER" --all --no-pager
  systemctl show "$DEEP_SERVICE" \
    --property=LoadState,ActiveState,SubState,Result --no-pager
  grep -q '^mode: execute$' "$ROOT/$CONFIG"
  grep -q '^stage_mode: screen_only$' "$ROOT/$CONFIG"
  grep -q '^  max_attempts_per_day: 6$' "$ROOT/$CONFIG"
  grep -q '^stage_mode: deep_only$' "$ROOT/$DEEP_CONFIG"
  grep -q '^  max_attempts_per_day: 4$' "$ROOT/$DEEP_CONFIG"
  grep -q '^  max_input_tokens_per_task: 250000$' "$ROOT/$CONFIG"
}

run_now() {
  require_runtime
  [[ -s "$STATE/auth.json" ]] || fail "$STATE/auth.json is missing"
  # This deliberately uses the normal daily config and therefore cannot
  # bypass the UTC-day attempt or cost fuses. If today's two model attempts
  # were already consumed, the worker exits safely without another call.
  systemctl reset-failed "$SERVICE" || true
  systemctl start "$SERVICE"
  journalctl -u "$SERVICE" -n 120 --no-pager
}

case "$ACTION" in
  install)
    install_daily
    check_daily
    ;;
  check)
    check_daily
    ;;
  run-now)
    run_now
    ;;
  *)
    fail "usage: $0 {install|check|run-now}"
    ;;
esac
