#!/usr/bin/env bash

set -euo pipefail

LIMIT_MB="${1:-300}"
CHECK_INTERVAL="${2:-5min}"

SCRIPT_PATH="/usr/local/bin/check-warp-memory.sh"
SERVICE_PATH="/etc/systemd/system/check-warp-memory.service"
TIMER_PATH="/etc/systemd/system/check-warp-memory.timer"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root"
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found. This script requires systemd."
  exit 1
fi

echo "Installing WARP memory watchdog..."
echo "Memory limit: ${LIMIT_MB} MB"
echo "Check interval: ${CHECK_INTERVAL}"

cat > "$SCRIPT_PATH" <<EOF
#!/usr/bin/env bash

set -euo pipefail

LIMIT_MB=${LIMIT_MB}
SERVICE_NAME="warp-svc"

PID=\$(pidof "\$SERVICE_NAME" || true)

if [ -z "\$PID" ]; then
  logger "\$SERVICE_NAME is not running, skipping memory check"
  exit 0
fi

RSS_KB=\$(ps -o rss= -p "\$PID" | awk '{print \$1}')

if [ -z "\$RSS_KB" ]; then
  logger "Could not read RSS for \$SERVICE_NAME"
  exit 0
fi

RSS_MB=\$((RSS_KB / 1024))

if [ "\$RSS_MB" -gt "\$LIMIT_MB" ]; then
  logger "\$SERVICE_NAME memory is \${RSS_MB}MB, limit is \${LIMIT_MB}MB. Restarting \$SERVICE_NAME."
  systemctl restart "\$SERVICE_NAME"
else
  logger "\$SERVICE_NAME memory is \${RSS_MB}MB, limit is \${LIMIT_MB}MB. OK."
fi
EOF

chmod +x "$SCRIPT_PATH"

cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Check Cloudflare WARP memory usage

[Service]
Type=oneshot
ExecStart=${SCRIPT_PATH}
EOF

cat > "$TIMER_PATH" <<EOF
[Unit]
Description=Run Cloudflare WARP memory check periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=${CHECK_INTERVAL}
Unit=check-warp-memory.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now check-warp-memory.timer

echo
echo "Installed successfully."
echo
echo "Timer status:"
systemctl --no-pager status check-warp-memory.timer || true

echo
echo "Next runs:"
systemctl list-timers --all | grep check-warp-memory || true

echo
echo "Useful commands:"
echo "  systemctl status check-warp-memory.timer"
echo "  systemctl status check-warp-memory.service"
echo "  journalctl -u check-warp-memory.service -n 50 --no-pager"
echo "  journalctl -t check-warp-memory.sh -n 50 --no-pager"
