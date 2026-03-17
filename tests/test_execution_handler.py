import builtins
import importlib
import logging
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from src.handlers.execution_handler import ExecutionHandler


def _import_module_without_paramiko(module_name: str, monkeypatch: pytest.MonkeyPatch):
    original_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "paramiko":
            raise ModuleNotFoundError("No module named 'paramiko'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, "paramiko", raising=False)
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked_import)
    return importlib.import_module(module_name)


@pytest.fixture
def mock_ssh_client():
    mock_ssh = MagicMock(spec=paramiko.SSHClient)
    mock_ssh.exec_command.return_value = (MagicMock(), MagicMock(), MagicMock())
    mock_ssh.open_sftp.return_value = MagicMock(spec=paramiko.SFTPClient)
    return mock_ssh


def test_handler_initialization_local_mode():
    handler = ExecutionHandler(mode="local")
    assert handler.mode == "local"


def test_handler_initialization_remote_mode():
    handler = ExecutionHandler(mode="remote", ssh_client=MagicMock())
    assert handler.mode == "remote"


@patch("subprocess.run")
def test_execute_command_local_mode_uses_argument_list_and_shell_false(mock_run):
    handler = ExecutionHandler(mode="local")

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
def test_upload_file_local_mode(mock_copy):
    handler = ExecutionHandler(mode="local")
    handler.upload_file("a.txt", "b.txt")
    mock_copy.assert_called_once_with("a.txt", "b.txt")


def test_execute_command_remote_mode(mock_ssh_client):
    handler = ExecutionHandler(mode="remote", ssh_client=mock_ssh_client)
    handler.execute_command("ls -l")
    mock_ssh_client.exec_command.assert_called_once_with("ls -l")


def test_upload_file_remote_mode(mock_ssh_client):
    handler = ExecutionHandler(mode="remote", ssh_client=mock_ssh_client)
    handler.upload_file("a.txt", "b.txt")
    mock_ssh_client.open_sftp.assert_called_once()
    mock_sftp = mock_ssh_client.open_sftp()
    mock_sftp.put.assert_called_once_with("a.txt", "b.txt")


def test_wait_for_job_completion_remote_polls_scheduler_until_terminal_state():
    handler = ExecutionHandler(mode="remote", ssh_client=MagicMock())
    handler.execute_command = MagicMock(
        side_effect=[
            MagicMock(success=True, output="RUNNING", error="", return_code=0),
            MagicMock(success=True, output="COMPLETED", error="", return_code=0),
        ]
    )

    result = handler.wait_for_job_completion(job_id="12345", timeout=5, poll_interval=0)

    assert result.failed is False
    assert handler.execute_command.call_count == 2


@pytest.mark.parametrize("scheduler_output", ["FAILED", "CANCELLED", "TIMEOUT"])
def test_wait_for_job_completion_remote_fails_for_unsuccessful_terminal_states(
    scheduler_output,
):
    handler = ExecutionHandler(mode="remote", ssh_client=MagicMock())
    handler.execute_command = MagicMock(
        return_value=MagicMock(success=True, output=scheduler_output, error="", return_code=0)
    )

    result = handler.wait_for_job_completion(job_id="12345", timeout=5, poll_interval=0)

    assert result.failed is True
    assert scheduler_output in result.error


@patch.object(ExecutionHandler, "_is_process_running", return_value=False)
def test_wait_for_job_completion_local_succeeds_when_process_exits_and_output_exists(
    _mock_is_process_running,
    tmp_path: Path,
):
    handler = ExecutionHandler(mode="local")
    log_file = tmp_path / "simulation.log"
    log_file.write_text("Beam setup complete\n")
    output_dir = tmp_path / "beam_10"
    output_dir.mkdir()

    result = handler.wait_for_job_completion(
        timeout=1,
        poll_interval=0,
        log_file_path=str(log_file),
        local_pid=4321,
        expected_output_dir=str(output_dir),
    )

    assert result.failed is False


@patch("subprocess.run")
def test_submit_simulation_job_local_mode_returns_background_pid(mock_run):
    handler = ExecutionHandler(mode="local")
    handler.settings = MagicMock()
    handler.settings.get_command.return_value = "nohup bash -c 'cd /tmp && ./tps_env/tps_env input.in > out.log 2>&1' > /dev/null 2>&1 &"
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="4321\n",
        stderr="",
    )

    result = handler.submit_simulation_job(
        handler_name="HpcJobSubmitter",
        command_key="remote_submit_simulation",
    )

    assert result.success is True
    assert result.local_pid == 4321
    mock_run.assert_called_once_with(
        ["bash", "-lc", "nohup bash -c 'cd /tmp && ./tps_env/tps_env input.in > out.log 2>&1' > /dev/null 2>&1 & echo $!"],
        check=True,
        capture_output=True,
        text=True,
        cwd=None,
    )


def test_submit_simulation_job_local_mode_requires_handler_context():
    handler = ExecutionHandler(mode="local")
    result = handler.submit_simulation_job("some/script/path.sh")

    assert result.success is False
    assert "handler_name and command_key" in result.error


def test_submit_simulation_job_remote_mode_refactored(mock_ssh_client):
    handler = ExecutionHandler(mode="remote", ssh_client=mock_ssh_client)
    script_path = "/remote/path/to/submit_job.sh"

    mock_stdin, mock_stdout, mock_stderr = MagicMock(), MagicMock(), MagicMock()
    mock_stdout.read.return_value = b"Submitted batch job 12345"
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_stderr.read.return_value = b""
    mock_ssh_client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

    result = handler.submit_simulation_job(script_path)

    mock_ssh_client.exec_command.assert_called_once_with(f"sbatch {script_path}")
    assert result.success
    assert result.job_id == "12345"


def test_upload_to_pc_localdata_local_mode_logs_simulation(caplog, tmp_path):
    handler = ExecutionHandler(mode="local")
    source_file = tmp_path / "source.txt"
    source_file.write_text("test content")
    case_id = "case_123"

    with caplog.at_level(logging.INFO):
        handler.upload_to_pc_localdata(source_file, case_id)

    assert f"Simulating upload by copying to local directory for case {case_id}" in caplog.text
    shutil.rmtree(f"./localdata_uploads/{case_id}")


def test_execution_handler_imports_without_paramiko_installed(monkeypatch):
    module = _import_module_without_paramiko("src.handlers.execution_handler", monkeypatch)

    assert hasattr(module, "ExecutionHandler")
