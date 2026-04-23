"""Tests for the MQI process cleanup helper script."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_kill_all_mqi_processes_script_stops_services_and_targets_runtime_and_port_pids(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parent.parent

    log_path = tmp_path / "commands.log"
    bin_dir = tmp_path / "bin"
    runtime_dir = repo_root / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "ui:",
                "  web:",
                "    port: 9091",
            ]
        ),
        encoding="utf-8",
    )

    transfer_config = repo_root.parent / "mqi_transfer" / "Linux" / "app_config.ini"
    original_transfer_config = transfer_config.read_text(encoding="utf-8")
    main_runtime = runtime_dir / "main_process.json"
    ui_pid_file = runtime_dir / "ui_process.pid"

    main_runtime.write_text(
        '{"pid": 501, "repo_root": "/tmp/repo", "config_path": "/tmp/config.yaml"}',
        encoding="utf-8",
    )
    ui_pid_file.write_text("502\n", encoding="utf-8")

    bin_dir.mkdir()
    _write_executable(
        bin_dir / "sudo",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf 'sudo:%s\\n' "$*" >> "{log_path}"
"$@"
""",
    )
    _write_executable(
        bin_dir / "systemctl",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf 'systemctl:%s\\n' "$*" >> "{log_path}"
""",
    )
    _write_executable(
        bin_dir / "pgrep",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf 'pgrep:%s\\n' "$*" >> "{log_path}"
pattern="${{@: -1}}"
case "$pattern" in
  *mqi_communicator/main.py*) printf '601\\n' ;;
  *uvicorn\\ src.web.app:app*) printf '602\\n' ;;
  *mqi_transfer.py*) printf '603\\n' ;;
esac
""",
    )
    _write_executable(
        bin_dir / "lsof",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf 'lsof:%s\\n' "$*" >> "{log_path}"
case "$*" in
  *9091*) printf '701\\n702\\n' ;;
  *9001*) printf '703\\n' ;;
esac
""",
    )
    _write_executable(
        bin_dir / "fuser",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf 'fuser:%s\\n' "$*" >> "{log_path}"
case "$*" in
  *9091*) printf '702 704\\n' ;;
  *9001*) printf '703 705\\n' ;;
esac
""",
    )

    try:
        transfer_config.write_text(
            "\n".join(
                [
                    "[server]",
                    "listen_port = 9001",
                ]
            ),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["MQI_KILL_SCRIPT_DRY_RUN"] = "1"

        result = subprocess.run(
            ["bash", "scripts/kill_all_mqi_processes.sh", str(config_path)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    finally:
        transfer_config.write_text(original_transfer_config, encoding="utf-8")
        main_runtime.unlink(missing_ok=True)
        ui_pid_file.unlink(missing_ok=True)

    assert result.returncode == 0, result.stderr
    command_log = log_path.read_text(encoding="utf-8")

    assert "[dry-run] sudo systemctl stop mqi_communicator.service" in result.stdout
    assert "[dry-run] sudo systemctl stop mqi-transfer.service" in result.stdout
    assert "pgrep:-f mqi_communicator/main.py" in command_log
    assert "pgrep:-f uvicorn src.web.app:app" in command_log
    assert "pgrep:-f mqi_transfer.py" in command_log
    assert "lsof:-tiTCP:9091 -sTCP:LISTEN" in command_log
    assert "lsof:-tiTCP:9001 -sTCP:LISTEN" in command_log
    assert "fuser:-n tcp 9091" in command_log
    assert "fuser:-n tcp 9001" in command_log

    for pid in ("501", "502", "601", "602", "603", "701", "702", "703", "704", "705"):
        assert f"[dry-run] kill -TERM {pid}" in result.stdout

    assert "[dry-run] rm -f" in result.stdout
    assert "MQI process cleanup complete" in result.stdout
