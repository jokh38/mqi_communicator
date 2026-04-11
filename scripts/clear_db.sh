#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${1:-config/config.yaml}"

if [[ ! -f "$CONFIG_PATH" ]]; then
    echo "Config file not found: $CONFIG_PATH" >&2
    exit 1
fi

DB_PATH="$(
python3 - "$CONFIG_PATH" <<'PY'
from pathlib import Path
import sys
import yaml

config_path = Path(sys.argv[1]).resolve()
config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

paths = config.get("paths", {})
base_directory = paths.get("base_directory", "")
database_template = ((paths.get("local") or {}).get("database_path"))

if not database_template:
    raise SystemExit("database_path is not configured under paths.local")

resolved = database_template.format(base_directory=base_directory)
print(Path(resolved))
PY
)"

for target in "$DB_PATH" "${DB_PATH}-wal" "${DB_PATH}-shm"; do
    if [[ -e "$target" ]]; then
        rm -f -- "$target"
        echo "Removed $target"
    fi
done
