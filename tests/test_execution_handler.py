import logging
import shutil
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
    )


@patch("shutil.copy")
def test_upload_file_copies_locally(mock_copy):
    handler = ExecutionHandler.__new__(ExecutionHandler)
    handler.settings = MagicMock()
    handler.logger = MagicMock()

    handler.upload_file("a.txt", "b.txt")
    mock_copy.assert_called_once_with("a.txt", "b.txt")


@patch("shutil.copy")
def test_download_file_copies_locally(mock_copy):
    handler = ExecutionHandler.__new__(ExecutionHandler)
    handler.settings = MagicMock()
    handler.logger = MagicMock()

    handler.download_file("src.txt", "dst.txt")
    mock_copy.assert_called_once_with("src.txt", "dst.txt")


def test_upload_to_pc_localdata_local_mode_logs_simulation(caplog, tmp_path):
    handler = ExecutionHandler.__new__(ExecutionHandler)
    handler.settings = MagicMock()
    handler.logger = MagicMock()

    source_file = tmp_path / "source.txt"
    source_file.write_text("test content")
    case_id = "case_123"

    with caplog.at_level(logging.INFO):
        handler.upload_to_pc_localdata(source_file, case_id)

    assert f"Simulating upload by copying to local directory for case {case_id}" in caplog.text
    shutil.rmtree(f"./localdata_uploads/{case_id}", ignore_errors=True)


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
