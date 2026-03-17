"""Database context manager helper for simplified database session management."""

from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from src.config.settings import Settings
from src.database.connection import DatabaseConnection
from src.infrastructure.logging_handler import StructuredLogger
from src.repositories.case_repo import CaseRepository


def get_handler_db_path(settings: Settings, handler_name: Optional[str] = None) -> Path:
    """Resolve database path using handler-specific configuration when provided."""
    if handler_name:
        db_path_str = settings.get_path("database_path", handler_name=handler_name)
        return Path(db_path_str)
    return settings.get_database_path()

@contextmanager
def get_db_session(
    settings: Settings,
    logger: StructuredLogger,
    handler_name: Optional[str] = None
) -> Generator[CaseRepository, None, None]:
    """Provides a transactional database session with automatic cleanup.

    This context manager encapsulates the common pattern of:
    1. Getting database path from settings
    2. Creating a database connection
    3. Creating a case repository
    4. Ensuring connection cleanup in finally block

    Args:
        settings: Application settings object
        logger: Structured logger instance
        handler_name: Optional handler name for path resolution

    Yields:
        CaseRepository: Initialized case repository ready for use

    Example:
        with get_db_session(settings, logger) as case_repo:
            case_data = case_repo.get_case(case_id)
            case_repo.update_case_status(case_id, CaseStatus.PROCESSING)
    """
    db_path = get_handler_db_path(settings, handler_name)
    db_conn = None
    try:
        db_conn = DatabaseConnection(db_path=db_path, settings=settings, logger=logger)
        yield CaseRepository(db_conn, logger)
    finally:
        if db_conn:
            db_conn.close()
