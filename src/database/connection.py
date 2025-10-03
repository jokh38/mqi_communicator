"""Manages SQLite database connections, transactions, and schema initialization."""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from src.config.settings import Settings  # Updated import
from src.domain.errors import DatabaseError
from src.infrastructure.logging_handler import StructuredLogger


class DatabaseConnection:
    """
    Manages SQLite database connections, transactions, and schema initialization.

    This class uses a Settings object to configure the database connection,
    following the single-source-of-truth principle.
    """

    def __init__(self, db_path: Path, settings: Settings, logger: StructuredLogger):
        """
        Initializes the database connection manager.

        Args:
            db_path (Path): Path to the SQLite database file.
            settings (Settings): The application's settings object.
            logger (StructuredLogger): Logger for recording database events.
        """
        self.db_path = db_path
        self.settings = settings
        self.logger = logger
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connect()

    def _connect(self) -> None:
        """
        Establishes a connection to the SQLite database using configuration from Settings.

        Raises:
            DatabaseError: If the connection fails.
        """
        db_config = self.settings.get_database_config()
        timeout = db_config.get("connection_timeout_seconds", 30)
        journal_mode = db_config.get("journal_mode", "WAL")
        synchronous = db_config.get("synchronous", "NORMAL")
        cache_size = db_config.get("cache_size", -2000)

        try:
            self._conn = sqlite3.connect(str(self.db_path),
                                         timeout=timeout,
                                         check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

            # Apply configuration settings
            self._conn.execute(f"PRAGMA journal_mode = {journal_mode}")
            self._conn.execute(f"PRAGMA synchronous = {synchronous}")
            self._conn.execute(f"PRAGMA cache_size = {cache_size}")

            self.logger.info(
                "Database connection established", {
                    "db_path": str(self.db_path),
                    "journal_mode": journal_mode,
                    "synchronous": synchronous,
                })

        except sqlite3.Error as e:
            self.logger.error(
                "Failed to connect to database", {
                    "db_path": str(self.db_path),
                    "error": str(e)
                })
            raise DatabaseError(f"Failed to connect to database: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager for handling database transactions with thread-safe locking.
        """
        with self._lock:
            if not self._conn:
                raise DatabaseError("Database connection is not established")

            if self._conn.in_transaction:
                yield self._conn
                return

            try:
                self._conn.execute("BEGIN")
                yield self._conn
                self._conn.commit()
            except Exception as e:
                self._conn.rollback()
                self.logger.error("Transaction failed, rolling back",
                                  {"error": str(e)})
                raise

    def init_db(self) -> None:
        """Initializes the database schema, creating all necessary tables and indexes."""
        try:
            with self.transaction() as conn:
                # Create cases table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cases (
                        case_id TEXT PRIMARY KEY,
                        case_path TEXT NOT NULL,
                        status TEXT NOT NULL,
                        progress REAL DEFAULT 0.0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        error_message TEXT,
                        assigned_gpu TEXT,
                        interpreter_completed BOOLEAN DEFAULT 0,
                        FOREIGN KEY (assigned_gpu) REFERENCES gpu_resources (uuid)
                    )
                """)
                # Create beams table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS beams (
                        beam_id TEXT PRIMARY KEY,
                        parent_case_id TEXT NOT NULL,
                        beam_path TEXT NOT NULL,
                        status TEXT NOT NULL,
                        progress REAL DEFAULT 0.0,
                        hpc_job_id TEXT,
                        error_message TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (parent_case_id) REFERENCES cases (case_id)
                    )
                """)
                # Create gpu_resources table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS gpu_resources (
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
                """)
                # Create workflow_steps table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS workflow_steps (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        case_id TEXT NOT NULL,
                        step TEXT NOT NULL,
                        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        completed_at TIMESTAMP,
                        status TEXT NOT NULL,
                        error_message TEXT,
                        metadata TEXT,
                        FOREIGN KEY (case_id) REFERENCES cases (case_id)
                    )
                """)
                # Create indexes
                conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_status ON cases (status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_updated ON cases (updated_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_gpu_status ON gpu_resources (status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_case ON workflow_steps (case_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_beams_parent_case ON beams (parent_case_id)")

                cursor = conn.execute("PRAGMA table_info(gpu_resources)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'gpu_index' not in columns:
                    self.logger.info("Adding gpu_index column to gpu_resources table")
                    conn.execute("ALTER TABLE gpu_resources ADD COLUMN gpu_index INTEGER DEFAULT 0")

                # Add interpreter_completed column to cases table if it doesn't exist
                cursor = conn.execute("PRAGMA table_info(cases)")
                case_columns = [column[1] for column in cursor.fetchall()]
                if 'interpreter_completed' not in case_columns:
                    self.logger.info("Adding interpreter_completed column to cases table")
                    conn.execute("ALTER TABLE cases ADD COLUMN interpreter_completed BOOLEAN DEFAULT 0")

                # Add error_message column to beams table if it doesn't exist
                cursor = conn.execute("PRAGMA table_info(beams)")
                beam_columns = [column[1] for column in cursor.fetchall()]
                if 'error_message' not in beam_columns:
                    self.logger.info("Adding error_message column to beams table")
                    conn.execute("ALTER TABLE beams ADD COLUMN error_message TEXT")
                # Add progress column to beams table if it doesn't exist
                if 'progress' not in beam_columns:
                    self.logger.info("Adding progress column to beams table")
                    conn.execute("ALTER TABLE beams ADD COLUMN progress REAL DEFAULT 0.0")


            self.logger.info("Database schema initialized successfully")

        except sqlite3.Error as e:
            self.logger.error("Failed to initialize database schema", {"error": str(e)})
            raise DatabaseError(f"Failed to initialize database schema: {e}")

    def close(self) -> None:
        """Closes the database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
                self.logger.info("Database connection closed")

    @property
    def connection(self) -> sqlite3.Connection:
        """Provides access to the raw connection for repository classes."""
        if not self._conn:
            raise DatabaseError("Database connection is not established")
        return self._conn