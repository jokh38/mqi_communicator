"""Regression tests for database initialization and migrations."""

import sqlite3
from pathlib import Path
from unittest.mock import Mock

from src.database.connection import DatabaseConnection


def test_init_db_migrates_legacy_available_gpu_status_to_idle(tmp_path: Path) -> None:
    """Legacy GPU rows using 'available' should be normalized to 'idle' on startup."""
    db_path = tmp_path / "legacy_gpu_status.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE gpu_resources (
            uuid TEXT PRIMARY KEY,
            gpu_index INTEGER NOT NULL,
            name TEXT NOT NULL,
            memory_total INTEGER NOT NULL,
            memory_used INTEGER NOT NULL,
            memory_free INTEGER NOT NULL,
            temperature INTEGER NOT NULL,
            utilization INTEGER NOT NULL,
            status TEXT NOT NULL,
            assigned_case TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO gpu_resources (
            uuid, gpu_index, name, memory_total, memory_used, memory_free,
            temperature, utilization, status, assigned_case
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "GPU-legacy",
            0,
            "RTX 3090",
            24576,
            0,
            24576,
            30,
            0,
            "available",
            None,
        ),
    )
    conn.commit()
    conn.close()

    settings = Mock()
    settings.get_database_config.return_value = {
        "connection_timeout_seconds": 30,
        "journal_mode": "WAL",
        "synchronous": "NORMAL",
        "cache_size": -2000,
    }
    logger = Mock()

    db = DatabaseConnection(db_path=db_path, settings=settings, logger=logger)
    try:
        db.init_db()
    finally:
        db.close()

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM gpu_resources WHERE uuid = ?",
            ("GPU-legacy",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "idle"
