"""Tests for the clear-db helper script."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_clear_db_script_removes_database_and_sqlite_sidecars(tmp_path: Path) -> None:
    base_directory = tmp_path / "base"
    data_directory = base_directory / "data"
    data_directory.mkdir(parents=True)

    db_path = data_directory / "test.db"
    wal_path = Path(f"{db_path}-wal")
    shm_path = Path(f"{db_path}-shm")

    for path in (db_path, wal_path, shm_path):
        path.write_text("placeholder", encoding="utf-8")

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "paths:",
                f'  base_directory: "{base_directory}"',
                "  local:",
                '    database_path: "{base_directory}/data/test.db"',
            ]
        ),
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["bash", "scripts/clear_db.sh", str(config_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for path in (db_path, wal_path, shm_path):
        assert not path.exists()
    assert "Removed" in result.stdout
