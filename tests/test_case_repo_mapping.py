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
        self.mock_db_connection = Mock()
        self.mock_logger = Mock()
        self.repo = CaseRepository(self.mock_db_connection, self.mock_logger)

    def test_map_row_to_case_data_with_all_fields(self):
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

        result = self.repo._map_row_to_case_data(mock_row)

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

        result = self.repo._map_row_to_case_data(mock_row)

        self.assertEqual(result.case_id, "TEST002")
        self.assertEqual(result.status, CaseStatus.PENDING)
        self.assertIsNone(result.updated_at)
        self.assertIsNone(result.error_message)
        self.assertIsNone(result.assigned_gpu)
        self.assertFalse(result.interpreter_completed)

    def test_map_row_to_case_data_interpreter_completed_boolean(self):
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

        result_false = self.repo._map_row_to_case_data(mock_row_false)

        self.assertFalse(result_false.interpreter_completed)

        mock_row_true = mock_row_false.copy()
        mock_row_true["interpreter_completed"] = 1

        result_true = self.repo._map_row_to_case_data(mock_row_true)

        self.assertTrue(result_true.interpreter_completed)

    def test_map_row_to_beam_data_with_all_fields(self):
        mock_row = {
            "beam_id": "BEAM001",
            "parent_case_id": "TEST001",
            "beam_path": "/path/to/beam",
            "status": "simulation_running",
            "progress": 42.0,
            "created_at": "2025-01-01T10:00:00",
            "updated_at": "2025-01-01T11:00:00",
            "hpc_job_id": "12345",
            "beam_number": 10,
        }

        result = self.repo._map_row_to_beam_data(mock_row)

        self.assertIsInstance(result, BeamData)
        self.assertEqual(result.beam_id, "BEAM001")
        self.assertEqual(result.parent_case_id, "TEST001")
        self.assertEqual(result.beam_path, Path("/path/to/beam"))
        self.assertEqual(result.status, BeamStatus.SIMULATION_RUNNING)
        self.assertEqual(result.progress, 42.0)
        self.assertEqual(result.created_at, datetime(2025, 1, 1, 10, 0, 0))
        self.assertEqual(result.updated_at, datetime(2025, 1, 1, 11, 0, 0))
        self.assertEqual(result.hpc_job_id, "12345")
        self.assertEqual(result.beam_number, 10)

    def test_map_row_to_beam_data_with_null_fields(self):
        mock_row = {
            "beam_id": "BEAM002",
            "parent_case_id": "TEST002",
            "beam_path": "/path/to/beam2",
            "status": "pending",
            "progress": 0.0,
            "created_at": "2025-01-01T10:00:00",
            "updated_at": None,
            "hpc_job_id": None,
            "beam_number": None,
        }

        result = self.repo._map_row_to_beam_data(mock_row)

        self.assertEqual(result.beam_id, "BEAM002")
        self.assertEqual(result.status, BeamStatus.PENDING)
        self.assertIsNone(result.updated_at)
        self.assertIsNone(result.hpc_job_id)
        self.assertIsNone(result.beam_number)

    def test_map_row_to_beam_data_status_enum_conversion(self):
        statuses = [
            ("pending", BeamStatus.PENDING),
            ("csv_interpreting", BeamStatus.CSV_INTERPRETING),
            ("tps_generation", BeamStatus.TPS_GENERATION),
            ("simulation_running", BeamStatus.SIMULATION_RUNNING),
            ("postprocessing", BeamStatus.POSTPROCESSING),
            ("completed", BeamStatus.COMPLETED),
            ("failed", BeamStatus.FAILED),
        ]

        for status_str, expected_enum in statuses:
            with self.subTest(status=status_str):
                mock_row = {
                    "beam_id": f"BEAM_{status_str}",
                    "parent_case_id": "TEST001",
                    "beam_path": "/path/to/beam",
                    "status": status_str,
                    "progress": 0.0,
                    "created_at": "2025-01-01T10:00:00",
                    "updated_at": None,
                    "hpc_job_id": None,
                    "beam_number": None,
                }

                result = self.repo._map_row_to_beam_data(mock_row)

                self.assertEqual(result.status, expected_enum)

    def test_get_beams_for_case_orders_by_beam_number_before_beam_id(self):
        self.repo._execute_query = Mock(return_value=[])

        self.repo.get_beams_for_case("CASE001")

        query = self.repo._execute_query.call_args[0][0]
        self.assertIn("beam_number", query)
        self.assertIn("ORDER BY", query)
        self.assertIn("beam_id ASC", query)


if __name__ == "__main__":
    unittest.main()
