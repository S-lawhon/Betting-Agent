#!/usr/bin/env bash
# Install the primary-droplet timer that reads Gate 0c from the P-029 host.
set -euo pipefail

APP="${APP:-/opt/betting-pod-shop}"
KEY="${P029_GATE0C_SSH_KEY:-/root/.ssh/p029_probe}"

[[ "${EUID}" -eq 0 ]] || { echo "ERROR: run as root on the primary droplet" >&2; exit 1; }
[[ -f "$KEY" ]] || { echo "ERROR: P-029 probe key missing: $KEY" >&2; exit 1; }
for unit in p029-gate0c-checkpoint.service p029-gate0c-checkpoint.timer; do
  [[ -f "$APP/scripts/systemd/$unit" ]] || {
    echo "ERROR: deployed unit missing: $unit" >&2
    exit 1
  }
done

install -d -m 0750 -o root -g root "$APP/data/p029_gate0c"
install -m 0644 \
  "$APP/scripts/systemd/p029-gate0c-checkpoint.service" \
  "$APP/scripts/systemd/p029-gate0c-checkpoint.timer" \
  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now p029-gate0c-checkpoint.timer
systemctl list-timers p029-gate0c-checkpoint.timer --no-pager
