"""Unit tests for CaseRepository DTO mapping helpers."""

import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from src.domain.enums import BeamStatus, CaseStatus
from src.domain.models import BeamData, CaseData
from src.repositories.case_repo import CaseRepository


class TestCaseRepositoryMapping(unittest.TestCase):
    """Test cases for CaseRepository DTO mapping helpers."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_db_connection = Mock()
        self.mock_logger = Mock()
        self.repo = CaseRepository(self.mock_db_connection, self.mock_logger)

    def test_map_row_to_case_data_with_all_fields(self):
        """Test mapping a complete database row to CaseData."""
        # Arrange
        mock_row = {
            "case_id": "TEST001",
            "case_path": "/path/to/case",
            "status": "processing",
            "progress": 50.0,
            "created_at": "2025-01-01T10:00:00",
            "updated_at": "2025-01-01T11:00:00",
            "error_message": "Test error",
            "assigned_gpu": "GPU-0",
            "interpreter_completed": 1,
        }

        # Act
        result = self.repo._map_row_to_case_data(mock_row)

        # Assert
        self.assertIsInstance(result, CaseData)
        self.assertEqual(result.case_id, "TEST001")
        self.assertEqual(result.case_path, Path("/path/to/case"))
        self.assertEqual(result.status, CaseStatus.PROCESSING)
        self.assertEqual(result.progress, 50.0)
        self.assertEqual(result.created_at, datetime(2025, 1, 1, 10, 0, 0))
        self.assertEqual(result.updated_at, datetime(2025, 1, 1, 11, 0, 0))
        self.assertEqual(result.error_message, "Test error")
        self.assertEqual(result.assigned_gpu, "GPU-0")
        self.assertTrue(result.interpreter_completed)

    def test_map_row_to_case_data_with_null_updated_at(self):
        """Test mapping row with null updated_at field."""
        # Arrange
        mock_row = {
            "case_id": "TEST002",
            "case_path": "/path/to/case2",
            "status": "pending",
            "progress": 0.0,
            "created_at": "2025-01-01T10:00:00",
            "updated_at": None,
            "error_message": None,
            "assigned_gpu": None,
            "interpreter_completed": 0,
        }

        # Act
        result = self.repo._map_row_to_case_data(mock_row)

        # Assert
        self.assertEqual(result.case_id, "TEST002")
        self.assertEqual(result.status, CaseStatus.PENDING)
        self.assertIsNone(result.updated_at)
        self.assertIsNone(result.error_message)
        self.assertIsNone(result.assigned_gpu)
        self.assertFalse(result.interpreter_completed)

    def test_map_row_to_case_data_interpreter_completed_boolean(self):
        """Test interpreter_completed is correctly converted to boolean."""
        # Arrange - test with 0 (False)
        mock_row_false = {
            "case_id": "TEST003",
            "case_path": "/path/to/case3",
            "status": "pending",
            "progress": 0.0,
            "created_at": "2025-01-01T10:00:00",
            "updated_at": None,
            "error_message": None,
            "assigned_gpu": None,
            "interpreter_completed": 0,
        }

        # Act
        result_false = self.repo._map_row_to_case_data(mock_row_false)

        # Assert
        self.assertFalse(result_false.interpreter_completed)

        # Arrange - test with 1 (True)
        mock_row_true = mock_row_false.copy()
        mock_row_true["interpreter_completed"] = 1

        # Act
        result_true = self.repo._map_row_to_case_data(mock_row_true)

        # Assert
        self.assertTrue(result_true.interpreter_completed)

    def test_map_row_to_beam_data_with_all_fields(self):
        """Test mapping a complete database row to BeamData."""
        # Arrange
        mock_row = {
            "beam_id": "BEAM001",
            "parent_case_id": "TEST001",
            "beam_path": "/path/to/beam",
            "status": "hpc_running",
            "created_at": "2025-01-01T10:00:00",
            "updated_at": "2025-01-01T11:00:00",
            "hpc_job_id": "12345",
        }

        # Act
        result = self.repo._map_row_to_beam_data(mock_row)

        # Assert
        self.assertIsInstance(result, BeamData)
        self.assertEqual(result.beam_id, "BEAM001")
        self.assertEqual(result.parent_case_id, "TEST001")
        self.assertEqual(result.beam_path, Path("/path/to/beam"))
        self.assertEqual(result.status, BeamStatus.HPC_RUNNING)
        self.assertEqual(result.created_at, datetime(2025, 1, 1, 10, 0, 0))
        self.assertEqual(result.updated_at, datetime(2025, 1, 1, 11, 0, 0))
        self.assertEqual(result.hpc_job_id, "12345")

    def test_map_row_to_beam_data_with_null_fields(self):
        """Test mapping beam row with null optional fields."""
        # Arrange
        mock_row = {
            "beam_id": "BEAM002",
            "parent_case_id": "TEST002",
            "beam_path": "/path/to/beam2",
            "status": "pending",
            "created_at": "2025-01-01T10:00:00",
            "updated_at": None,
            "hpc_job_id": None,
        }

        # Act
        result = self.repo._map_row_to_beam_data(mock_row)

        # Assert
        self.assertEqual(result.beam_id, "BEAM002")
        self.assertEqual(result.status, BeamStatus.PENDING)
        self.assertIsNone(result.updated_at)
        self.assertIsNone(result.hpc_job_id)

    def test_map_row_to_beam_data_status_enum_conversion(self):
        """Test that various beam statuses are correctly converted to enums."""
        # Test different status values
        statuses = [
            ("pending", BeamStatus.PENDING),
            ("csv_interpreting", BeamStatus.CSV_INTERPRETING),
            ("uploading", BeamStatus.UPLOADING),
            ("tps_generation", BeamStatus.TPS_GENERATION),
            ("hpc_queued", BeamStatus.HPC_QUEUED),
            ("hpc_running", BeamStatus.HPC_RUNNING),
            ("downloading", BeamStatus.DOWNLOADING),
            ("postprocessing", BeamStatus.POSTPROCESSING),
            ("completed", BeamStatus.COMPLETED),
            ("failed", BeamStatus.FAILED),
        ]

        for status_str, expected_enum in statuses:
            with self.subTest(status=status_str):
                # Arrange
                mock_row = {
                    "beam_id": f"BEAM_{status_str}",
                    "parent_case_id": "TEST001",
                    "beam_path": "/path/to/beam",
                    "status": status_str,
                    "created_at": "2025-01-01T10:00:00",
                    "updated_at": None,
                    "hpc_job_id": None,
                }

                # Act
                result = self.repo._map_row_to_beam_data(mock_row)

                # Assert
                self.assertEqual(result.status, expected_enum)


if __name__ == '__main__':
    unittest.main()
