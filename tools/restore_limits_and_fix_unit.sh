#!/usr/bin/env bash
set -euo pipefail
DUMP="${1:-/root/bot_finuchet/_backups/code_dump-20250831-155108.txt}"
cd /root/bot_finuchet
test -s "$DUMP" || { echo "❌ dump not found: $DUMP"; exit 1; }

# extractor
python3 tools/extract_from_dump.py "$DUMP" "routers/messages.py" --out "routers/messages.py"
python3 -m py_compile routers/messages.py

# unit override
mkdir -p /etc/systemd/system/finuchet.service.d
cat >/etc/systemd/system/finuchet.service.d/override.conf << 'EOF'
[Unit]
Wants=network-online.target
After=network-online.target
Requires=
[Service]
WorkingDirectory=/root/bot_finuchet
EnvironmentFile=-/root/.env
EnvironmentFile=-/root/bot_finuchet/.env
Environment=PYTHONPATH=/root/bot_finuchet
EOF
systemctl daemon-reload
systemctl restart finuchet
systemctl status -n 40 --no-pager finuchet || true
