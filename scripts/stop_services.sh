#!/usr/bin/env bash
set -euo pipefail

sudo systemctl stop mqi_communicator.service
echo "mqi_communicator stopped"

sudo systemctl stop mqi-transfer.service
echo "mqi-transfer stopped"

sudo systemctl status --no-pager mqi-transfer.service mqi_communicator.service || true
