import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from src.database.connection import DatabaseConnection
from src.domain.enums import BeamStatus, CaseStatus
from src.repositories.case_repo import CaseRepository


def _make_connection(db_path: Path) -> DatabaseConnection:
    logger = MagicMock()
    settings = MagicMock()
    settings.get_database_config.return_value = {}
    return DatabaseConnection(db_path=db_path, settings=settings, logger=logger)


def _make_repo(tmp_path: Path) -> CaseRepository:
    conn = _make_connection(tmp_path / "failure-metadata.db")
    conn.init_db()
    return CaseRepository(conn, MagicMock())


def test_init_db_adds_structured_failure_columns_for_new_and_existing_schema(tmp_path: Path):
    fresh_db = tmp_path / "fresh.db"
    fresh_conn = _make_connection(fresh_db)
    fresh_conn.init_db()

    with fresh_conn.transaction() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(cases)").fetchall()
        }

    assert {"failure_category", "failure_phase", "failure_details"} <= columns

    legacy_db = tmp_path / "legacy.db"
    raw_conn = sqlite3.connect(legacy_db)
    raw_conn.execute(
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
            retry_count INTEGER DEFAULT 0,
            ptn_checker_run_count INTEGER DEFAULT 0,
            ptn_checker_last_run_at TIMESTAMP,
            ptn_checker_status TEXT
        )
        """
    )
    raw_conn.execute(
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
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    raw_conn.execute(
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
            core_clock INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            assigned_case TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    raw_conn.execute(
        """
        CREATE TABLE workflow_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL,
            step TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            status TEXT NOT NULL,
            error_message TEXT,
            metadata TEXT
        )
        """
    )
    raw_conn.execute(
        """
        CREATE TABLE deliveries (
            delivery_id TEXT PRIMARY KEY,
            parent_case_id TEXT NOT NULL,
            beam_id TEXT NOT NULL,
            delivery_path TEXT NOT NULL,
            delivery_timestamp TIMESTAMP NOT NULL,
            delivery_date TEXT NOT NULL,
            raw_beam_number INTEGER,
            treatment_beam_index INTEGER,
            is_reference_delivery BOOLEAN DEFAULT 0,
            ptn_status TEXT,
            ptn_last_run_at TIMESTAMP,
            gamma_pass_rate REAL,
            gamma_mean REAL,
            gamma_max REAL,
            evaluated_points INTEGER,
            report_path TEXT,
            error_message TEXT,
            fraction_index INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    raw_conn.commit()
    raw_conn.close()

    migrated_conn = _make_connection(legacy_db)
    migrated_conn.init_db()
    with migrated_conn.transaction() as conn:
        migrated_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(cases)").fetchall()
        }

    assert {"failure_category", "failure_phase", "failure_details"} <= migrated_columns


def test_case_mapping_and_failure_metadata_persistence(tmp_path: Path):
    repo = _make_repo(tmp_path)
    case_id = "case-metadata"
    case_path = tmp_path / case_id
    repo.create_case_with_beams(
        case_id,
        str(case_path),
        [
            {
                "beam_id": f"{case_id}_beam_1",
                "beam_path": case_path / "beam1",
                "beam_number": 1,
            }
        ],
    )

    details = {
        "summary": "TPS generation failed",
        "beam_errors": [{"beam_id": f"{case_id}_beam_1", "message": "Beam 1 failed"}],
    }
    repo.fail_case(
        case_id,
        "TPS generation failed",
        failure_category="retryable",
        failure_phase="tps_generation",
        failure_details=details,
    )

    case_data = repo.get_case(case_id)

    assert case_data.status == CaseStatus.FAILED
    assert case_data.error_message == "TPS generation failed"
    assert case_data.failure_category == "retryable"
    assert case_data.failure_phase == "tps_generation"
    assert case_data.failure_details == details


def test_reset_case_and_beams_for_retry_clears_structured_failure_metadata(tmp_path: Path):
    repo = _make_repo(tmp_path)
    case_id = "case-retry"
    case_path = tmp_path / case_id
    repo.create_case_with_beams(
        case_id,
        str(case_path),
        [
            {
                "beam_id": f"{case_id}_beam_1",
                "beam_path": case_path / "beam1",
                "beam_number": 1,
            }
        ],
    )

    repo.fail_case(
        case_id,
        "old case failure",
        failure_category="permanent",
        failure_phase="simulation",
        failure_details={"summary": "old case failure"},
    )
    repo.update_beam_status(
        f"{case_id}_beam_1",
        BeamStatus.FAILED,
        error_message="old beam failure",
    )
    repo.update_beam_progress(f"{case_id}_beam_1", 88.0)

    repo.reset_case_and_beams_for_retry(case_id)

    case_data = repo.get_case(case_id)
    beam_data = repo.get_beam(f"{case_id}_beam_1")

    assert case_data.status == CaseStatus.PENDING
    assert case_data.progress == 0.0
    assert case_data.error_message == ""
    assert case_data.failure_category is None
    assert case_data.failure_phase is None
    assert case_data.failure_details is None
    assert beam_data.status == BeamStatus.PENDING
    assert beam_data.progress == 0.0
    assert beam_data.error_message == ""
