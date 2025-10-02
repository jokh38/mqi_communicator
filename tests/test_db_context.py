"""Unit tests for database context manager helper."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from src.utils.db_context import get_db_session


class TestDbContext(unittest.TestCase):
    """Test cases for the database context manager."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_settings = Mock()
        self.mock_logger = Mock()
        self.test_db_path = Path("/tmp/test.db")

    @patch('src.utils.db_context.DatabaseConnection')
    @patch('src.utils.db_context.CaseRepository')
    def test_get_db_session_creates_connection_and_repository(
        self, mock_case_repo_class, mock_db_conn_class
    ):
        """Test that get_db_session creates database connection and repository."""
        # Arrange
        self.mock_settings.get_database_path.return_value = self.test_db_path
        mock_db_conn_instance = Mock()
        mock_case_repo_instance = Mock()
        mock_db_conn_class.return_value = mock_db_conn_instance
        mock_case_repo_class.return_value = mock_case_repo_instance

        # Act
        with get_db_session(self.mock_settings, self.mock_logger) as case_repo:
            # Assert - within context
            self.assertEqual(case_repo, mock_case_repo_instance)
            mock_db_conn_class.assert_called_once_with(
                db_path=self.test_db_path,
                settings=self.mock_settings,
                logger=self.mock_logger
            )
            mock_case_repo_class.assert_called_once_with(
                mock_db_conn_instance,
                self.mock_logger
            )

        # Assert - connection closed after context
        mock_db_conn_instance.close.assert_called_once()

    @patch('src.utils.db_context.DatabaseConnection')
    @patch('src.utils.db_context.CaseRepository')
    def test_get_db_session_with_handler_name(
        self, mock_case_repo_class, mock_db_conn_class
    ):
        """Test that get_db_session uses handler_name for path resolution."""
        # Arrange
        handler_name = "test_handler"
        custom_path = "/custom/path/test.db"
        self.mock_settings.get_path.return_value = custom_path
        mock_db_conn_instance = Mock()
        mock_db_conn_class.return_value = mock_db_conn_instance
        mock_case_repo_class.return_value = Mock()

        # Act
        with get_db_session(
            self.mock_settings,
            self.mock_logger,
            handler_name=handler_name
        ) as case_repo:
            # Assert
            self.mock_settings.get_path.assert_called_once_with(
                "database_path",
                handler_name=handler_name
            )
            mock_db_conn_class.assert_called_once_with(
                db_path=Path(custom_path),
                settings=self.mock_settings,
                logger=self.mock_logger
            )

    @patch('src.utils.db_context.DatabaseConnection')
    @patch('src.utils.db_context.CaseRepository')
    def test_get_db_session_closes_connection_on_exception(
        self, mock_case_repo_class, mock_db_conn_class
    ):
        """Test that connection is closed even when exception occurs."""
        # Arrange
        self.mock_settings.get_database_path.return_value = self.test_db_path
        mock_db_conn_instance = Mock()
        mock_db_conn_class.return_value = mock_db_conn_instance
        mock_case_repo_instance = Mock()
        mock_case_repo_class.return_value = mock_case_repo_instance

        # Act & Assert
        with self.assertRaises(ValueError):
            with get_db_session(self.mock_settings, self.mock_logger) as case_repo:
                raise ValueError("Test exception")

        # Assert - connection still closed
        mock_db_conn_instance.close.assert_called_once()

    @patch('src.utils.db_context.DatabaseConnection')
    @patch('src.utils.db_context.CaseRepository')
    def test_get_db_session_handles_none_connection(
        self, mock_case_repo_class, mock_db_conn_class
    ):
        """Test that get_db_session handles None connection gracefully."""
        # Arrange
        self.mock_settings.get_database_path.return_value = self.test_db_path
        mock_db_conn_class.return_value = None

        # Act - should not raise even with None connection
        with get_db_session(self.mock_settings, self.mock_logger) as case_repo:
            pass

        # No assertion needed - just verify it doesn't crash


if __name__ == '__main__':
    unittest.main()
