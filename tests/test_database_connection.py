"""Regression tests for database initialization and migrations."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

from src.database.connection import DatabaseConnection
from src.repositories.gpu_repo import GpuRepository


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

    if row is None:
        raise AssertionError("Expected migrated GPU row to exist")
    if row[0] != "idle":
        raise AssertionError(f"Expected GPU status 'idle', got {row[0]!r}")


def test_init_db_uses_beam_foreign_key_for_gpu_assignments(tmp_path: Path) -> None:
    """GPU assignment column should reference beams, not cases."""
    db_path = tmp_path / "gpu_assignment_fk.db"
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
        foreign_keys = db.connection.execute("PRAGMA foreign_key_list(gpu_resources)").fetchall()
    finally:
        db.close()

    assigned_case_fks = [fk for fk in foreign_keys if fk[3] == "assigned_case"]
    if not assigned_case_fks:
        raise AssertionError("Expected gpu_resources.assigned_case foreign key")
    if assigned_case_fks[0][2] != "beams":
        raise AssertionError(f"Expected foreign key target 'beams', got {assigned_case_fks[0][2]!r}")


def test_init_db_migrates_legacy_gpu_assignment_foreign_key_to_beams(tmp_path: Path) -> None:
    """Existing databases with the old cases FK should be rebuilt to use beams."""
    db_path = tmp_path / "legacy_gpu_assignment_fk.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE cases (
            case_id TEXT PRIMARY KEY,
            case_path TEXT NOT NULL DEFAULT '/tmp/case-1',
            status TEXT NOT NULL DEFAULT 'pending',
            progress REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            error_message TEXT,
            assigned_gpu TEXT,
            interpreter_completed BOOLEAN DEFAULT 0,
            retry_count INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE beams (
            beam_id TEXT PRIMARY KEY,
            parent_case_id TEXT NOT NULL,
            beam_path TEXT NOT NULL,
            beam_number INTEGER,
            status TEXT NOT NULL,
            progress REAL DEFAULT 0.0,
            hpc_job_id TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parent_case_id) REFERENCES cases (case_id)
        )
        """
    )
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
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (assigned_case) REFERENCES cases (case_id)
        )
        """
    )
    conn.execute("INSERT INTO cases (case_id) VALUES ('case-1')")
    conn.execute(
        """
        INSERT INTO beams (
            beam_id, parent_case_id, beam_path, beam_number, status, progress
        ) VALUES ('beam-1', 'case-1', '/tmp/beam-1', 1, 'pending', 0.0)
        """
    )
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """
        INSERT INTO gpu_resources (
            uuid, gpu_index, name, memory_total, memory_used, memory_free,
            temperature, utilization, status, assigned_case
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("GPU-1", 0, "RTX 3090", 24576, 1024, 23552, 30, 5, "assigned", "beam-1"),
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
        foreign_keys = db.connection.execute("PRAGMA foreign_key_list(gpu_resources)").fetchall()
        migrated_row = db.connection.execute(
            "SELECT assigned_case FROM gpu_resources WHERE uuid = ?",
            ("GPU-1",),
        ).fetchone()
    finally:
        db.close()

    assigned_case_fks = [fk for fk in foreign_keys if fk[3] == "assigned_case"]
    if not assigned_case_fks:
        raise AssertionError("Expected gpu_resources.assigned_case foreign key after migration")
    if assigned_case_fks[0][2] != "beams":
        raise AssertionError(f"Expected foreign key target 'beams', got {assigned_case_fks[0][2]!r}")
    if migrated_row is None:
        raise AssertionError("Expected migrated GPU assignment row to exist")
    if migrated_row[0] != "beam-1":
        raise AssertionError(f"Expected migrated assignment 'beam-1', got {migrated_row[0]!r}")


def test_find_and_lock_multiple_gpus_reserves_without_persisting_case_id(tmp_path: Path) -> None:
    """Fresh reservations should not persist case IDs into the beam assignment column."""
    db_path = tmp_path / "gpu_reservation.db"
    settings = Mock()
    settings.get_database_config.return_value = {
        "connection_timeout_seconds": 30,
        "journal_mode": "WAL",
        "synchronous": "NORMAL",
        "cache_size": -2000,
    }
    settings.get_gpu_config.return_value = {"min_memory_mb": 1000}
    logger = Mock()

    db = DatabaseConnection(db_path=db_path, settings=settings, logger=logger)
    try:
        db.init_db()
        db.connection.execute(
            """
            INSERT INTO gpu_resources (
                uuid, gpu_index, name, memory_total, memory_used, memory_free,
                temperature, utilization, status, assigned_case
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("GPU-1", 0, "RTX 3090", 24576, 1024, 23552, 30, 5, "idle", None),
        )

        repo = GpuRepository(db_connection=db, logger=logger, settings=settings)
        allocations = repo.find_and_lock_multiple_gpus(case_id="case-1", num_gpus=1)
        row = db.connection.execute(
            "SELECT status, assigned_case FROM gpu_resources WHERE uuid = ?",
            ("GPU-1",),
        ).fetchone()
    finally:
        db.close()

    expected_allocations = [{"gpu_uuid": "GPU-1", "gpu_id": 0, "memory_free": 23552}]
    if allocations != expected_allocations:
        raise AssertionError(f"Unexpected allocations: {allocations!r}")
    if row is None:
        raise AssertionError("Expected reserved GPU row to exist")
    if row[0] != "assigned":
        raise AssertionError(f"Expected GPU status 'assigned', got {row[0]!r}")
    if row[1] is not None:
        raise AssertionError(f"Expected assigned_case to remain NULL, got {row[1]!r}")


def test_init_db_retries_transient_database_lock(tmp_path: Path) -> None:
    """Schema initialization should retry transient SQLITE_BUSY failures."""
    db_path = tmp_path / "transient_lock.db"
    settings = Mock()
    settings.get_database_config.return_value = {
        "connection_timeout_seconds": 30,
        "journal_mode": "WAL",
        "synchronous_mode": "NORMAL",
        "cache_size_mb": 64,
        "busy_timeout_ms": 5000,
        "schema_init_retry_attempts": 2,
        "schema_init_retry_delay_ms": 0,
    }
    logger = Mock()

    db = DatabaseConnection(db_path=db_path, settings=settings, logger=logger)
    original_transaction = db.transaction
    attempts = 0

    @contextmanager
    def flaky_transaction():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        with original_transaction() as conn:
            yield conn

    db.transaction = flaky_transaction

    try:
        db.init_db()
    finally:
        db.close()

    if attempts != 2:
        raise AssertionError(f"Expected 2 init_db attempts, got {attempts}")


def test_update_resources_preserves_assigned_gpu_state_and_beam_mapping(tmp_path: Path) -> None:
    """GPU monitor refreshes must not erase active beam assignments."""
    db_path = tmp_path / "gpu_refresh.db"
    settings = Mock()
    settings.get_database_config.return_value = {
        "connection_timeout_seconds": 30,
        "journal_mode": "WAL",
        "synchronous_mode": "NORMAL",
        "cache_size_mb": 64,
        "busy_timeout_ms": 5000,
    }
    logger = Mock()

    db = DatabaseConnection(db_path=db_path, settings=settings, logger=logger)
    try:
        db.init_db()
        db.connection.execute(
            """
            INSERT INTO cases (case_id, case_path, status)
            VALUES ('case-1', '/tmp/case-1', 'processing')
            """
        )
        db.connection.execute(
            """
            INSERT INTO beams (
                beam_id, parent_case_id, beam_path, beam_number, status, progress
            ) VALUES ('beam-1', 'case-1', '/tmp/beam-1', 1, 'running', 50.0)
            """
        )
        db.connection.execute(
            """
            INSERT INTO gpu_resources (
                uuid, gpu_index, name, memory_total, memory_used, memory_free,
                temperature, utilization, status, assigned_case
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("GPU-1", 0, "RTX 3090", 24576, 1024, 23552, 30, 5, "assigned", "beam-1"),
        )
        db.connection.commit()

        repo = GpuRepository(db_connection=db, logger=logger, settings=settings)
        repo.update_resources([
            {
                "uuid": "GPU-1",
                "gpu_index": 0,
                "name": "RTX 3090",
                "memory_total": 24576,
                "memory_used": 4096,
                "memory_free": 20480,
                "temperature": 42,
                "utilization": 88,
            }
        ])
        row = db.connection.execute(
            """
            SELECT status, assigned_case, memory_used, memory_free, utilization
            FROM gpu_resources
            WHERE uuid = ?
            """,
            ("GPU-1",),
        ).fetchone()
    finally:
        db.close()

    if row is None:
        raise AssertionError("Expected refreshed GPU row to exist")
    if row["status"] != "assigned":
        raise AssertionError(f"Expected status 'assigned', got {row['status']!r}")
    if row["assigned_case"] != "beam-1":
        raise AssertionError(f"Expected assigned beam 'beam-1', got {row['assigned_case']!r}")
    if row["memory_used"] != 4096:
        raise AssertionError(f"Expected memory_used 4096, got {row['memory_used']!r}")
    if row["memory_free"] != 20480:
        raise AssertionError(f"Expected memory_free 20480, got {row['memory_free']!r}")
    if row["utilization"] != 88:
        raise AssertionError(f"Expected utilization 88, got {row['utilization']!r}")


def test_init_db_creates_ptn_checker_columns_for_new_database(tmp_path: Path) -> None:
    db_path = tmp_path / "ptn_columns_new.db"
    settings = Mock()
    settings.get_database_config.return_value = {
        "connection_timeout_seconds": 30,
        "journal_mode": "WAL",
        "synchronous_mode": "NORMAL",
        "cache_size_mb": 64,
        "busy_timeout_ms": 5000,
    }
    logger = Mock()

    db = DatabaseConnection(db_path=db_path, settings=settings, logger=logger)
    try:
        db.init_db()
        columns = {
            row[1]: row
            for row in db.connection.execute("PRAGMA table_info(cases)").fetchall()
        }
    finally:
        db.close()

    for column_name in ("ptn_checker_run_count", "ptn_checker_last_run_at", "ptn_checker_status"):
        if column_name not in columns:
            raise AssertionError(f"Expected PTN checker column {column_name!r} in cases table")


def test_init_db_migrates_existing_cases_table_with_ptn_checker_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "ptn_columns_migration.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE cases (
            case_id TEXT PRIMARY KEY,
            case_path TEXT NOT NULL,
            status TEXT NOT NULL,
            progress REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            error_message TEXT,
            assigned_gpu TEXT,
            interpreter_completed BOOLEAN DEFAULT 0,
            retry_count INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()

    settings = Mock()
    settings.get_database_config.return_value = {
        "connection_timeout_seconds": 30,
        "journal_mode": "WAL",
        "synchronous_mode": "NORMAL",
        "cache_size_mb": 64,
        "busy_timeout_ms": 5000,
    }
    logger = Mock()

    db = DatabaseConnection(db_path=db_path, settings=settings, logger=logger)
    try:
        db.init_db()
        columns = {
            row[1]: row
            for row in db.connection.execute("PRAGMA table_info(cases)").fetchall()
        }
    finally:
        db.close()

    if columns["ptn_checker_run_count"][4] != "0":
        raise AssertionError(
            f"Expected default 0 for ptn_checker_run_count, got {columns['ptn_checker_run_count'][4]!r}"
        )
