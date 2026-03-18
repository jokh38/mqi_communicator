#!/usr/bin/env bash

set -euo pipefail

# Usage: ./clear_db.sh [config_path] [--db] [--logs] [--outputs] [--all]
# Default: clears all (database, logs, outputs)

CONFIG_PATH="${1:-config/config.yaml}"
shift || true

# Parse flags
CLEAR_DB=0
CLEAR_LOGS=0
CLEAR_OUTPUTS=0

if [[ $# -eq 0 ]]; then
  # No flags: clear everything
  CLEAR_DB=1
  CLEAR_LOGS=1
  CLEAR_OUTPUTS=1
else
  # Parse explicit flags
  for arg in "$@"; do
    case "$arg" in
      --db) CLEAR_DB=1 ;;
      --logs) CLEAR_LOGS=1 ;;
      --outputs) CLEAR_OUTPUTS=1 ;;
      --all)
        CLEAR_DB=1
        CLEAR_LOGS=1
        CLEAR_OUTPUTS=1
        ;;
      *)
        echo "Unknown flag: $arg" >&2
        echo "Usage: $0 [config_path] [--db] [--logs] [--outputs] [--all]" >&2
        exit 1
        ;;
    esac
  done
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Config file not found: ${CONFIG_PATH}" >&2
  exit 1
fi

# Extract paths from config using Python
read -r DB_PATH LOG_DIR CSV_OUTPUT_DIR SIM_OUTPUT_DIR BASE_DIR < <(
python - "$CONFIG_PATH" <<'PY'
from pathlib import Path
import sys

import yaml

config_path = Path(sys.argv[1])
with config_path.open("r", encoding="utf-8") as handle:
    config = yaml.safe_load(handle) or {}

paths = config.get("paths") or {}
base_directory = paths.get("base_directory")
local_paths = paths.get("local") or {}

database_path = local_paths.get("database_path")
csv_output_dir = local_paths.get("csv_output_dir")
simulation_output_dir = local_paths.get("simulation_output_dir")

logging_config = config.get("logging") or {}
log_dir = logging_config.get("log_dir", "logs")

if not database_path:
    raise SystemExit("database_path is missing from config")

# Resolve paths with base_directory
def resolve(path_template):
    if base_directory and "{base_directory}" in path_template:
        return path_template.replace("{base_directory}", base_directory)
    return path_template

db_resolved = str(Path(resolve(database_path)).expanduser())
# For output directories, remove {case_id} placeholder first
csv_resolved = resolve(csv_output_dir).replace("/{case_id}", "") if csv_output_dir else ""
csv_resolved = str(Path(csv_resolved).expanduser()) if csv_resolved else ""
sim_resolved = resolve(simulation_output_dir).replace("/{case_id}", "") if simulation_output_dir else ""
sim_resolved = str(Path(sim_resolved).expanduser()) if sim_resolved else ""

# Log dir is relative to project root (where config is)
log_resolved = str((config_path.parent.parent / log_dir).resolve())

print(f"{db_resolved} {log_resolved} {csv_resolved} {sim_resolved} {base_directory or ''}")
PY
)

echo "==================================================================="
echo "Initializing MQI Communicator - Clearing selected components"
echo "==================================================================="
echo ""

# Safety checks
if [[ -z "${DB_PATH}" || "${DB_PATH}" == "/" || "${DB_PATH}" == "." ]]; then
  echo "ERROR: Refusing to clear unsafe database path: ${DB_PATH}" >&2
  exit 1
fi

total_removed=0

# ============================================
# Clear Database
# ============================================
if [[ "${CLEAR_DB}" -eq 1 ]]; then
  echo "[1/3] Clearing database files..."
  removed_any=0
  for target in "${DB_PATH}" "${DB_PATH}-wal" "${DB_PATH}-shm"; do
    if [[ -e "${target}" ]]; then
      rm -f "${target}"
      echo "  ✓ Removed $(basename "${target}")"
      removed_any=1
      total_removed=$((total_removed + 1))
    fi
  done
  if [[ "${removed_any}" -eq 0 ]]; then
    echo "  • No database files found"
  fi
  echo ""
fi

# ============================================
# Clear Logs
# ============================================
if [[ "${CLEAR_LOGS}" -eq 1 ]]; then
  echo "[2/3] Clearing log files..."
  if [[ -d "${LOG_DIR}" ]]; then
    log_count=$(find "${LOG_DIR}" -type f -name "*.log" 2>/dev/null | wc -l)
    if [[ "${log_count}" -gt 0 ]]; then
      find "${LOG_DIR}" -type f -name "*.log" -delete
      echo "  ✓ Removed ${log_count} log file(s) from ${LOG_DIR}"
      total_removed=$((total_removed + log_count))
    else
      echo "  • No log files found in ${LOG_DIR}"
    fi
  else
    echo "  • Log directory not found: ${LOG_DIR}"
  fi
  echo ""
fi

# ============================================
# Clear Outputs
# ============================================
if [[ "${CLEAR_OUTPUTS}" -eq 1 ]]; then
  echo "[3/3] Clearing output files..."

  # Clear CSV outputs
  if [[ -n "${CSV_OUTPUT_DIR}" && -d "${CSV_OUTPUT_DIR}" ]]; then
    csv_count=$(find "${CSV_OUTPUT_DIR}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
    if [[ "${csv_count}" -gt 0 ]]; then
      rm -rf "${CSV_OUTPUT_DIR:?}"/*
      echo "  ✓ Removed ${csv_count} case director(ies) from CSV outputs"
      total_removed=$((total_removed + csv_count))
    else
      echo "  • No CSV output directories found"
    fi
  else
    echo "  • CSV output directory not configured or not found"
  fi

  # Clear simulation outputs
  if [[ -n "${SIM_OUTPUT_DIR}" && -d "${SIM_OUTPUT_DIR}" ]]; then
    sim_count=$(find "${SIM_OUTPUT_DIR}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
    if [[ "${sim_count}" -gt 0 ]]; then
      rm -rf "${SIM_OUTPUT_DIR:?}"/*
      echo "  ✓ Removed ${sim_count} case director(ies) from simulation outputs"
      total_removed=$((total_removed + sim_count))
    else
      echo "  • No simulation output directories found"
    fi
  else
    echo "  • Simulation output directory not configured or not found"
  fi
  echo ""
fi

# ============================================
# Summary
# ============================================
echo "==================================================================="
if [[ "${total_removed}" -gt 0 ]]; then
  echo "✓ Initialization complete: ${total_removed} item(s) removed"
else
  echo "✓ Initialization complete: No items to remove (already clean)"
fi
echo "==================================================================="
