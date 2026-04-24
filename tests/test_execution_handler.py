import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.handlers.execution_handler import ExecutionHandler


def test_handler_initialization():
    handler = ExecutionHandler.__new__(ExecutionHandler)
    handler.settings = MagicMock()
    handler.logger = MagicMock()
    # No mode attribute should exist
    assert not hasattr(handler, "mode")


@patch("subprocess.run")
def test_execute_command_local_mode_uses_argument_list_and_shell_false(mock_run):
    handler = ExecutionHandler.__new__(ExecutionHandler)
    handler.settings = MagicMock()
    handler.logger = MagicMock()

    handler.execute_command(["ls", "-l"])

    mock_run.assert_called_once_with(
        ["ls", "-l"],
        shell=False,
        check=True,
        capture_output=True,
        text=True,
        cwd=None,
        timeout=30,
    )


@patch("subprocess.Popen")
def test_start_local_process_with_string_command(mock_popen):
    handler = ExecutionHandler.__new__(ExecutionHandler)
    handler.settings = MagicMock()
    handler.logger = MagicMock()

    handler.start_local_process("echo hello")

    mock_popen.assert_called_once_with(
        "echo hello",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        cwd=None,
    )


@patch("subprocess.Popen")
def test_start_local_process_with_list_command(mock_popen):
    handler = ExecutionHandler.__new__(ExecutionHandler)
    handler.settings = MagicMock()
    handler.logger = MagicMock()

    handler.start_local_process(["echo", "hello"])

    mock_popen.assert_called_once_with(
        ["echo", "hello"],
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        cwd=None,
    )


def test_wait_for_job_completion_simulation_progress_starts_at_or_above_running_phase(tmp_path):
    handler = ExecutionHandler.__new__(ExecutionHandler)
    handler.settings = MagicMock()
    handler.settings.get_processing_config.return_value = {}
    handler.settings.get_completion_patterns.return_value = {
        "success_pattern": "Simulation completed successfully",
        "failure_patterns": [],
    }
    handler.logger = MagicMock()

    log_path = tmp_path / "simulation.log"
    log_path.write_text("starting with 10 batches\nGenerating particles for (1 of 10 batches)\n")
    case_repo = MagicMock()
    process = MagicMock()
    process.poll.return_value = 0
    process.returncode = 0

    with patch("time.sleep"), patch("time.time", side_effect=[0.0, 0.0]):
        result = handler.wait_for_job_completion(
            timeout=30,
            poll_interval=1,
            log_file_path=str(log_path),
            beam_id="beam-1",
            case_repo=case_repo,
            process=process,
        )

    if result.failed:
        raise AssertionError(f"Expected successful process result, got {result!r}")
    first_progress = case_repo.update_beam_progress.call_args_list[0].args[1]
    if first_progress < 40.0:
        raise AssertionError(f"Expected first tracked progress >= 40.0, got {first_progress}")
