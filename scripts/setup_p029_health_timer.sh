#!/usr/bin/env bash
# setup_p029_health_timer.sh — move the P-029 health check from the Mac to the
# droplet, one time. Run FROM THE MAC (the only machine holding a key to both
# boxes). Idempotent: safe to re-run.
#
# What it does, in order:
#   1. Generates a dedicated ed25519 probe key on the droplet (/root/.ssh/
#      p029_probe) if absent — the Mac's betting_deploy private key is never
#      copied anywhere.
#   2. Authorizes that key on the P-029 box with `restrict` (no forwarding,
#      no pty), and pins the P-029 host key into the droplet's known_hosts so
#      the probe's BatchMode ssh does not die on first contact.
#   3. Installs p029-health-check.{service,timer} from the DEPLOYED tree into
#      /etc/systemd/system and enables the timer.
#   4. Runs the check once end-to-end and shows the heartbeat rows it wrote.
#
# Prerequisite: `bash scripts/deploy.sh 129.212.176.202 restart` has shipped
# the env-override version of scripts/p029_health_check.py and the unit files
# to /opt/betting-pod-shop — this script checks and refuses otherwise.
#
# Afterwards, DISABLE the `p029-daily-health-check` scheduled task on the Mac:
# two writers appending to the same heartbeat files from different clocks
# would make staleness unreadable. Manual runs by hand remain fine.

set -euo pipefail

DROPLET="${DROPLET:-root@129.212.176.202}"
P029_IP="${P029_IP:-143.198.162.120}"
P029="root@${P029_IP}"
KEY="${KEY:-$HOME/.ssh/betting_deploy}"
APP=/opt/betting-pod-shop
PROBE_KEY=/root/.ssh/p029_probe

dssh() { ssh -i "$KEY" -o IdentitiesOnly=yes "$DROPLET" "$@"; }
pssh() { ssh -i "$KEY" -o IdentitiesOnly=yes "$P029" "$@"; }
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "preflight: deployed tree has the droplet-mode script + units"
dssh "grep -q P029_HEALTH_SSH_KEY $APP/scripts/p029_health_check.py \
        && test -f $APP/scripts/systemd/p029-health-check.service \
        && test -f $APP/scripts/systemd/p029-health-check.timer" || {
  echo "ABORT: $APP on the droplet predates this change."
  echo "  Run:  bash scripts/deploy.sh 129.212.176.202 restart   and re-run."
  exit 1
}

say "probe key on droplet"
dssh "test -f $PROBE_KEY || ssh-keygen -t ed25519 -N '' \
        -C p029-health-probe@droplet -f $PROBE_KEY"
PUB="$(dssh "cat $PROBE_KEY.pub")"
echo "  $PUB"

say "authorize on P-029 box (restrict: no forwarding, no pty)"
# Idempotency keys on the comment, which ssh-keygen -C fixed above.
pssh "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys \
        && chmod 600 ~/.ssh/authorized_keys \
        && grep -q p029-health-probe@droplet ~/.ssh/authorized_keys \
        || printf 'restrict %s\n' '$PUB' >> ~/.ssh/authorized_keys"

say "pin P-029 host key on droplet"
dssh "ssh-keygen -F $P029_IP -f /root/.ssh/known_hosts >/dev/null 2>&1 \
        || ssh-keyscan -T 10 $P029_IP >> /root/.ssh/known_hosts 2>/dev/null"

say "install + enable timer"
dssh "cp $APP/scripts/systemd/p029-health-check.service \
         $APP/scripts/systemd/p029-health-check.timer /etc/systemd/system/ \
        && systemctl daemon-reload \
        && systemctl enable --now p029-health-check.timer"
dssh "systemctl list-timers p029-health-check.timer --no-pager | head -3"

say "verification run (one full check, from the droplet)"
dssh "systemctl start p029-health-check.service; \
        systemctl is-active p029-health-check.service; \
        journalctl -u p029-health-check.service -n 12 --no-pager -o cat"
echo
echo "  newest heartbeat rows:"
dssh "tail -n1 $APP/data/p029_heartbeat/p029_shadow.jsonl \
              $APP/data/p029_heartbeat/p029_archive.jsonl" || true

say "done"
cat <<'EOF'
NOW DISABLE the Mac scheduled task `p029-daily-health-check` — the droplet
timer owns the daily cadence from here. (Running the script by hand from the
Mac stays safe: rows carry their own timestamps and checked_from hostname.)

The next daily brief should show both p029 heartbeats fresh. If the
verification run above printed FAIL for either job, that is a REAL on-box
finding — diagnose the P-029 box, do not touch the heartbeat files.
EOF
