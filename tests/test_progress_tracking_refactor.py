"""Tests for Phase 3: Progress Tracking Refactoring

These tests verify that progress tracking behavior remains identical
before and after refactoring.
"""

import pytest
from unittest.mock import Mock
from pathlib import Path

from src.domain.states import (
    InitialState,
    FileUploadState,
    HpcExecutionState,
    DownloadState,
    PostprocessingState,
    CompletedState,
)


class TestProgressTrackingBaseline:
    """Baseline tests: Verify current progress tracking behavior"""

    def test_initial_state_silently_ignores_progress_errors(self):
        """Baseline: InitialState should never raise exceptions from progress tracking"""
        state = InitialState()
        mock_context = Mock()
        mock_context.id = "test_beam_123"
        mock_context.path = Mock()
        mock_context.path.is_dir = Mock(return_value=True)
        mock_context.logger = Mock()
        mock_context.case_repo = Mock()
        mock_context.shared_context = {}

        # Simulate various errors
        test_cases = [
            # Config method raises KeyError
            KeyError("Config missing"),
            # Config method raises AttributeError
            AttributeError("Attribute missing"),
            # Config method raises ValueError
            ValueError("Invalid value"),
        ]

        for error in test_cases:
            mock_context.settings = Mock()
            mock_context.settings.get_progress_tracking_config = Mock(side_effect=error)
            mock_context.settings.get_path = Mock(return_value="/tmp/csv_output")

            # Mock beam data
            mock_beam = Mock()
            mock_beam.parent_case_id = "case_123"
            mock_context.case_repo.get_beam = Mock(return_value=mock_beam)

            # Create TPS file
            tps_dir = Path("/tmp/csv_output/case_123")
            tps_dir.mkdir(parents=True, exist_ok=True)
            tps_file = tps_dir / "moqui_tps_test_beam_123.in"
            tps_file.touch()

            try:
                # Baseline verification: Should NOT raise exception
                result = state.execute(mock_context)

                # Should continue to next state
                assert isinstance(result, FileUploadState)

                # Should NOT update progress due to error
                mock_context.case_repo.update_beam_progress.assert_not_called()
            finally:
                # Cleanup
                if tps_file.exists():
                    tps_file.unlink()
                if tps_dir.exists():
                    tps_dir.rmdir()
                if tps_dir.parent.exists():
                    tps_dir.parent.rmdir()

            # Reset mock for next test case
            mock_context.case_repo.update_beam_progress.reset_mock()

    def test_initial_state_updates_progress_when_config_valid(self):
        """Baseline: InitialState should update progress when config is valid"""
        state = InitialState()
        mock_context = Mock()
        mock_context.id = "test_beam_456"
        mock_context.path = Mock()
        mock_context.path.is_dir = Mock(return_value=True)
        mock_context.logger = Mock()
        mock_context.case_repo = Mock()
        mock_context.shared_context = {}

        # Valid config
        mock_context.settings = Mock()
        mock_context.settings.get_progress_tracking_config = Mock(
            return_value={
                "coarse_phase_progress": {
                    "CSV_INTERPRETING": 10.0
                }
            }
        )
        mock_context.settings.get_path = Mock(return_value="/tmp/csv_output2")

        # Mock beam data
        mock_beam = Mock()
        mock_beam.parent_case_id = "case_456"
        mock_context.case_repo.get_beam = Mock(return_value=mock_beam)

        # Create TPS file
        tps_dir = Path("/tmp/csv_output2/case_456")
        tps_dir.mkdir(parents=True, exist_ok=True)
        tps_file = tps_dir / "moqui_tps_test_beam_456.in"
        tps_file.touch()

        try:
            # Baseline verification: Should update progress
            result = state.execute(mock_context)

            # Should continue to next state
            assert isinstance(result, FileUploadState)

            # Should update progress with value from config
            mock_context.case_repo.update_beam_progress.assert_called_once_with(
                "test_beam_456", 10.0
            )
        finally:
            # Cleanup
            if tps_file.exists():
                tps_file.unlink()
            if tps_dir.exists():
                tps_dir.rmdir()
            if tps_dir.parent.exists():
                tps_dir.parent.rmdir()

    def test_file_upload_state_updates_progress_in_remote_mode(self):
        """Baseline: FileUploadState should update progress in remote mode"""
        state = FileUploadState()
        mock_context = Mock()
        mock_context.id = "test_beam_789"
        mock_context.logger = Mock()
        mock_context.case_repo = Mock()
        mock_context.shared_context = {"tps_file_path": Path("/tmp/test.in")}
        mock_context.execution_handler = Mock()

        # Create mock TPS file
        Path("/tmp/test.in").touch()

        # Remote mode + valid config
        mock_context.settings = Mock()
        mock_context.settings.get_handler_mode = Mock(return_value='remote')
        mock_context.settings.get_progress_tracking_config = Mock(
            return_value={
                "coarse_phase_progress": {
                    "UPLOADING": 20.0
                }
            }
        )
        mock_context.settings.get_path = Mock(return_value="/remote/beam/path")

        # Mock beam data
        mock_beam = Mock()
        mock_beam.parent_case_id = "case_789"
        mock_context.case_repo.get_beam = Mock(return_value=mock_beam)

        # Mock successful upload
        mock_result = Mock()
        mock_result.success = True
        mock_context.execution_handler.upload_file = Mock(return_value=mock_result)

        try:
            # Baseline verification: Should update progress
            result = state.execute(mock_context)

            # Should continue to next state
            assert isinstance(result, HpcExecutionState)

            # Should update progress with value from config
            mock_context.case_repo.update_beam_progress.assert_called_once_with(
                "test_beam_789", 20.0
            )
        finally:
            # Cleanup
            if Path("/tmp/test.in").exists():
                Path("/tmp/test.in").unlink()

    def test_completed_state_uses_default_progress_value(self):
        """Baseline: CompletedState should use default 100.0 if config missing"""
        state = CompletedState()
        mock_context = Mock()
        mock_context.id = "test_beam_complete"
        mock_context.logger = Mock()
        mock_context.case_repo = Mock()

        # Config with missing COMPLETED key
        mock_context.settings = Mock()
        mock_context.settings.get_progress_tracking_config = Mock(
            return_value={
                "coarse_phase_progress": {}  # No COMPLETED key
            }
        )

        # Mock beam data
        mock_beam = Mock()
        mock_beam.parent_case_id = "case_complete"
        mock_beam.beam_id = "test_beam_complete"
        mock_beam.status = "COMPLETED"
        mock_context.case_repo.get_beam = Mock(return_value=mock_beam)
        # get_beams_for_case must return a list for len() to work
        mock_context.case_repo.get_beams_for_case = Mock(return_value=[mock_beam])

        # Baseline verification: Should use default 100.0
        result = state.execute(mock_context)

        # Terminal state returns None
        assert result is None

        # Should update progress with default value
        mock_context.case_repo.update_beam_progress.assert_called_once_with(
            "test_beam_complete", 100.0
        )
