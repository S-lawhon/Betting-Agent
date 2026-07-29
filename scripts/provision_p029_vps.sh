#!/usr/bin/env bash
# provision_p029_vps.sh — set up the P-029 combo research box.
#
# Idempotent: safe to re-run. Installs the Phase 0 shadow logger and the
# settled-combo archiver as systemd units, with the disk guards this project
# learned the hard way (June 2026: an unbounded log filled a 2GB droplet and
# caused a five-week OOM crash loop).
#
# Neither service needs Kalshi credentials — Phase 0 and the archiver are
# entirely public-data. Credentials arrive at Phase 1.
#
# Usage, as root on the droplet:
#   curl -fsSL <raw-url>/scripts/provision_p029_vps.sh | bash
# or:
#   bash provision_p029_vps.sh

set -euo pipefail

REPO="${REPO:-https://github.com/S-lawhon/Betting-Agent.git}"
DIR="${DIR:-/opt/p029}"
DATA="${DATA:-/var/lib/p029}"
USER_NAME="${USER_NAME:-p029}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "host"
uname -a; echo; free -m | head -2; echo; df -h / | tail -1

say "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git sqlite3 curl >/dev/null
python3 --version

say "user and directories"
id -u "$USER_NAME" >/dev/null 2>&1 || useradd -r -m -s /usr/sbin/nologin "$USER_NAME"
mkdir -p "$DATA/archive" "$DIR"
chown -R "$USER_NAME:$USER_NAME" "$DATA"

say "code"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch --quiet origin && git -C "$DIR" reset --hard --quiet origin/HEAD
else
  git clone --quiet "$REPO" "$DIR"
fi
chown -R "$USER_NAME:$USER_NAME" "$DIR"

for f in combo_research/shadow_public.py combo_research/archive_settled_combos.py; do
  if [ ! -f "$DIR/$f" ]; then
    echo "MISSING: $f"
    echo "  The repo does not contain it yet — commit and push from your Mac, then re-run."
    exit 1
  fi
done

say "python deps"
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --quiet --upgrade pip
"$DIR/venv/bin/pip" install --quiet requests cryptography websocket-client pyyaml
echo "installed: requests cryptography websocket-client pyyaml"

say "systemd: shadow logger (Phase 0)"
cat >/etc/systemd/system/p029-shadow.service <<EOF
[Unit]
Description=P-029 combo shadow logger (Phase 0, public data only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$DIR/combo_research
ExecStart=$DIR/venv/bin/python3 $DIR/combo_research/shadow_public.py --db $DATA/shadow.sqlite
Restart=always
RestartSec=30
# Do not let a runaway logger take the box down again.
MemoryMax=512M
Nice=5
StandardOutput=append:$DATA/shadow.log
StandardError=append:$DATA/shadow.log

[Install]
WantedBy=multi-user.target
EOF

say "systemd: settled-combo archiver (daily)"
cat >/etc/systemd/system/p029-archive.service <<EOF
[Unit]
Description=P-029 settled combo archiver (Kalshi purges these after ~3 months)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$DIR/combo_research
ExecStart=$DIR/venv/bin/python3 $DIR/combo_research/archive_settled_combos.py --out $DATA/archive --days-back 7
MemoryMax=1G
Nice=10
StandardOutput=append:$DATA/archive.log
StandardError=append:$DATA/archive.log
EOF

cat >/etc/systemd/system/p029-archive.timer <<EOF
[Unit]
Description=Run the P-029 combo archiver daily

[Timer]
OnCalendar=*-*-* 09:30:00 UTC
Persistent=true
RandomizedDelaySec=600

[Install]
WantedBy=timers.target
EOF

say "log rotation"
cat >/etc/logrotate.d/p029 <<EOF
$DATA/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su $USER_NAME $USER_NAME
}
EOF

say "starting"
systemctl daemon-reload
systemctl enable --now p029-shadow.service
systemctl enable --now p029-archive.timer
sleep 6

say "status"
systemctl is-active p029-shadow.service && echo "  shadow logger: running"
systemctl list-timers p029-archive.timer --no-pager | head -3

say "first archive run (this one takes a while)"
systemctl start p029-archive.service --no-block
echo "  started in the background; follow it with:"
echo "    tail -f $DATA/archive.log"

say "done"
cat <<EOF
Shadow logger  : systemctl status p029-shadow
                 tail -f $DATA/shadow.log
Gate 0 report  : $DIR/venv/bin/python3 $DIR/combo_research/shadow_public.py \\
                   --db $DATA/shadow.sqlite --report
Archive report : $DIR/venv/bin/python3 $DIR/combo_research/archive_settled_combos.py \\
                   --out $DATA/archive --report

Neither service uses Kalshi credentials. Nothing here can place an order.
EOF
