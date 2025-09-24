"""Contains the abstract base class for all repository implementations."""
from abc import ABC
from typing import Optional, Any

from src.database.connection import DatabaseConnection
from src.infrastructure.logging_handler import StructuredLogger


class BaseRepository(ABC):
    """Abstract base class for all repository implementations.

    This class provides common database access patterns and error handling.
    """

    def __init__(self, db_connection: DatabaseConnection, logger: StructuredLogger):
        """Initialize the repository with a database connection and logger.

        Args:
            db_connection (DatabaseConnection): The database connection manager.
            logger (StructuredLogger): The logger for recording repository operations.
        """
        self.db = db_connection
        self.logger = logger

    def _execute_query(
        self,
        query: str,
        params: tuple = (),
        fetch_one: bool = False,
        fetch_all: bool = False,
    ) -> Optional[Any]:
        """Execute a database query with error handling and logging.

        Args:
            query (str): The SQL query to execute.
            params (tuple, optional): The parameters for the query. Defaults to ().
            fetch_one (bool, optional): Whether to fetch one result. Defaults to False.
            fetch_all (bool, optional): Whether to fetch all results. Defaults to False.

        Returns:
            Optional[Any]: The query results based on the fetch parameters.
        """
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(query, params)

                if fetch_one:
                    return cursor.fetchone()
                elif fetch_all:
                    return cursor.fetchall()
                else:
                    # For INSERT, UPDATE, DELETE, return the number of affected rows
                    return cursor.rowcount
                    
        except Exception as e:
            self.logger.error(
                f"Database query failed: {query}", {"error": str(e), "params": params}
            )
            raise

    def _log_operation(self, operation: str, entity_id: str = None, **context):
        """Log repository operations for debugging and monitoring.

        Args:
            operation (str): A description of the operation.
            entity_id (str, optional): The ID of the entity being operated on. Defaults to None.
            **context: Additional context for logging.
        """
        log_data = {"repository": self.__class__.__name__, "operation": operation}

        if entity_id:
            log_data["entity_id"] = entity_id

        log_data.update(context)

        self.logger.debug("Repository operation", log_data)