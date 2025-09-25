import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.repositories.base import BaseRepository
from src.repositories.case_repo import CaseRepository
from src.database.connection import DatabaseConnection
from src.infrastructure.logging_handler import StructuredLogger
from src.domain.enums import CaseStatus

def test_repository_polymorphism():
    """
    Tests that a concrete repository (`CaseRepository`) correctly calls
    the 'protected' methods (`_execute_query`, `_log_operation`)
    from its abstract base class (`BaseRepository`).

    This test protects these base methods from being marked as dead code.
    """
    # Arrange
    mock_db_connection = MagicMock(spec=DatabaseConnection)
    mock_logger = MagicMock(spec=StructuredLogger)

    # We are testing the real CaseRepository, not a mock of it.
    case_repo = CaseRepository(mock_db_connection, mock_logger)

    # Spy on the protected methods from the base class by patching them on the instance.
    # This allows us to assert they were called without changing their implementation.
    with patch.object(case_repo, '_execute_query', wraps=case_repo._execute_query) as mock_execute_query, \
         patch.object(case_repo, '_log_operation', wraps=case_repo._log_operation) as mock_log_operation:

        # We need to mock the return value of the transaction context manager
        mock_db_connection.transaction.return_value.__enter__.return_value.execute.return_value.rowcount = 1

        # Act
        case_id = "case-polymorphism-001"
        case_path = Path("/tmp/case-001")
        case_repo.add_case(case_id, case_path)

        # Assert
        # Verify that the public method call resulted in calls to the base class methods.
        mock_log_operation.assert_called_once()
        mock_execute_query.assert_called_once()

        # Check some of the arguments for correctness
        mock_log_operation.assert_called_with("add_case", case_id, case_path=str(case_path))

        query_arg = mock_execute_query.call_args[0][0]
        params_arg = mock_execute_query.call_args[0][1]

        assert "INSERT INTO cases" in query_arg
        assert params_arg == (case_id, str(case_path), CaseStatus.PENDING.value, 0.0)
