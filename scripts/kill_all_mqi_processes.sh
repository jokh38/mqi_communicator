#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$PROJECT_DIR")"

CONFIG_PATH="${1:-config/config.yaml}"
DRY_RUN="${MQI_KILL_SCRIPT_DRY_RUN:-0}"

MAIN_RUNTIME_FILE="$PROJECT_DIR/.runtime/main_process.json"
UI_PID_FILE="$PROJECT_DIR/.runtime/ui_process.pid"
TRANSFER_CONFIG_PATH="$REPO_ROOT/mqi_transfer/Linux/app_config.ini"

declare -A SEEN_PIDS=()
COLLECTED_PIDS=()

log() {
    printf '%s\n' "$1"
}

run_cmd() {
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '[dry-run] %s\n' "$*"
        return 0
    fi
    "$@"
}

collect_pid() {
    local pid="$1"
    if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
        return 0
    fi
    if [[ "$pid" -eq "$$" ]]; then
        return 0
    fi
    if [[ -n "${SEEN_PIDS[$pid]:-}" ]]; then
        return 0
    fi
    SEEN_PIDS["$pid"]=1
    COLLECTED_PIDS+=("$pid")
}

read_ui_port() {
    python3 - "$CONFIG_PATH" <<'PY'
from pathlib import Path
import sys
import yaml

config_path = Path(sys.argv[1])
config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
ui = config.get("ui") or {}
web = ui.get("web") or {}
print(web.get("port", 8080))
PY
}

read_transfer_port() {
    python3 - "$TRANSFER_CONFIG_PATH" <<'PY'
import configparser
from pathlib import Path
import sys

config_path = Path(sys.argv[1])
config = configparser.ConfigParser()
if config_path.exists():
    config.read(config_path)
print(config.getint("server", "listen_port", fallback=80))
PY
}

collect_runtime_pids() {
    local pid line
    if [[ -f "$MAIN_RUNTIME_FILE" ]]; then
        pid="$(
            python3 - "$MAIN_RUNTIME_FILE" <<'PY'
import json
from pathlib import Path
import sys

runtime_path = Path(sys.argv[1])
try:
    metadata = json.loads(runtime_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)

pid = metadata.get("pid")
if isinstance(pid, int):
    print(pid)
PY
        )"
        collect_pid "$pid"
    fi

    if [[ -f "$UI_PID_FILE" ]]; then
        while IFS= read -r line; do
            for pid in $line; do
                collect_pid "$pid"
            done
        done < "$UI_PID_FILE"
    fi
}

collect_matching_processes() {
    local pattern pid
    for pattern in \
        "mqi_communicator/main.py" \
        "uvicorn src.web.app:app" \
        "mqi_transfer.py"
    do
        if command -v pgrep >/dev/null 2>&1; then
            while IFS= read -r pid; do
                collect_pid "$pid"
            done < <(pgrep -f "$pattern" 2>/dev/null || true)
        fi
    done
}

collect_port_listeners() {
    local port="$1"
    local line pid
    if command -v lsof >/dev/null 2>&1; then
        while IFS= read -r pid; do
            collect_pid "$pid"
        done < <(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    fi
    if command -v fuser >/dev/null 2>&1; then
        while IFS= read -r line; do
            for pid in $line; do
                collect_pid "$pid"
            done
        done < <(fuser -n tcp "$port" 2>/dev/null || true)
    fi
}

kill_pid() {
    local pid="$1"
    if [[ "$DRY_RUN" != "1" ]] && ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    log "Stopping PID $pid"
    run_cmd kill -TERM "$pid"

    if [[ "$DRY_RUN" == "1" ]]; then
        return 0
    fi

    for _ in $(seq 1 10); do
        if ! kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        sleep 0.2
    done

    log "Force killing PID $pid"
    run_cmd kill -KILL "$pid"
}

remove_runtime_files() {
    if [[ -f "$MAIN_RUNTIME_FILE" || -f "$UI_PID_FILE" ]]; then
        log "Removing stale runtime PID files"
    fi
    run_cmd rm -f "$MAIN_RUNTIME_FILE" "$UI_PID_FILE"
}

if [[ ! -f "$CONFIG_PATH" ]]; then
    echo "Config file not found: $CONFIG_PATH" >&2
    exit 1
fi

UI_PORT="$(read_ui_port)"
TRANSFER_PORT="$(read_transfer_port)"

log "Stopping MQI services"
run_cmd sudo systemctl stop mqi_communicator.service
run_cmd sudo systemctl stop mqi-transfer.service

log "Collecting related PIDs"
collect_runtime_pids
collect_matching_processes
collect_port_listeners "$UI_PORT"
collect_port_listeners "$TRANSFER_PORT"

if [[ "${#COLLECTED_PIDS[@]}" -eq 0 ]]; then
    log "No remaining MQI-related processes found"
else
    for pid in "${COLLECTED_PIDS[@]}"; do
        kill_pid "$pid"
    done
fi

remove_runtime_files

log "MQI process cleanup complete"
