#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

SERVICE_FILES=(
    "$PROJECT_DIR/mqi-transfer.service"
    "$PROJECT_DIR/mqi_communicator.service"
)

for f in "${SERVICE_FILES[@]}"; do
    if [[ ! -f "$f" ]]; then
        echo "ERROR: service file not found: $f" >&2
        exit 1
    fi
    sudo cp "$f" /etc/systemd/system/
    echo "Copied $(basename "$f") -> /etc/systemd/system/"
done

sudo systemctl daemon-reload
echo "systemd daemon reloaded"

sudo systemctl enable mqi-transfer.service mqi_communicator.service
echo "Services enabled"

sudo systemctl start mqi_communicator
echo "mqi_communicator started (mqi-transfer pulled in via Requires=)"

sudo systemctl status --no-pager mqi-transfer.service mqi_communicator.service
