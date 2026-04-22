from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.infrastructure.transfer_process_manager import TransferProcessManager
from src.infrastructure.ui_process_manager import UIProcessManager


def _make_transfer_manager(tmp_path: Path) -> TransferProcessManager:
    logger = MagicMock()
    transfer_dir = tmp_path / "mqi_transfer" / "Linux"
    transfer_dir.mkdir(parents=True, exist_ok=True)
    (transfer_dir / "mqi_transfer.py").write_text("print('ok')\n", encoding="utf-8")
    return TransferProcessManager(
        project_root=tmp_path,
        python_executable="/usr/bin/python3",
        logger=logger,
    )


def _make_ui_manager(tmp_path: Path) -> UIProcessManager:
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config/config.yaml"
    settings = MagicMock()
    settings.get_logging_config.return_value = {"log_dir": "logs"}
    settings.get_ui_config.return_value = {
        "mode": "web",
        "web": {"enabled": True, "port": 8080, "host": "0.0.0.0"},
    }
    logger = MagicMock()
    manager = UIProcessManager(
        database_path="/tmp/mqi_communicator.db",
        config_path=config_path,
        config=settings,
        logger=logger,
    )
    manager._pid_file = tmp_path / "ui_process.pid"
    return manager


def test_transfer_manager_linux_launch_does_not_detach_session(tmp_path: Path):
    manager = _make_transfer_manager(tmp_path)

    process = MagicMock()
    process.pid = 1234
    process.poll.side_effect = [None, None]

    with (
        patch("src.infrastructure.transfer_process_manager.platform.system", return_value="Linux"),
        patch("src.infrastructure.transfer_process_manager.subprocess.Popen", return_value=process) as popen_mock,
        patch.object(manager, "_port_is_listening", return_value=True),
        patch("src.infrastructure.transfer_process_manager.time.sleep"),
    ):
        assert manager.start() is True

    _, kwargs = popen_mock.call_args
    assert "start_new_session" not in kwargs


def test_ui_manager_linux_web_launch_does_not_detach_session(tmp_path: Path):
    manager = _make_ui_manager(tmp_path)

    process = MagicMock()
    process.pid = 4321
    process.poll.return_value = None

    with (
        patch("src.infrastructure.ui_process_manager.platform.system", return_value="Linux"),
        patch("src.infrastructure.ui_process_manager.subprocess.Popen", return_value=process) as popen_mock,
        patch.object(manager, "_reclaim_stale_ui_process"),
        patch.object(manager, "_ensure_web_port_ready", return_value=True),
        patch.object(manager, "_get_ui_command", return_value=["python", "-m", "uvicorn", "src.web.app:app"]),
        patch.object(manager, "_save_ui_pid"),
        patch("src.infrastructure.ui_process_manager.time.sleep"),
    ):
        assert manager.start() is True

    _, kwargs = popen_mock.call_args
    assert "start_new_session" not in kwargs


def test_request_parent_death_signal_calls_prctl_on_linux():
    from src.infrastructure.process_lifecycle import set_parent_death_signal

    libc = MagicMock()
    libc.prctl.return_value = 0

    with (
        patch("src.infrastructure.process_lifecycle.platform.system", return_value="Linux"),
        patch("src.infrastructure.process_lifecycle.ctypes.CDLL", return_value=libc),
    ):
        set_parent_death_signal()

    libc.prctl.assert_called_once_with(1, 15, 0, 0, 0)


def test_dashboard_main_requests_parent_death_signal_before_running(tmp_path: Path):
    from src.ui import dashboard

    db_path = tmp_path / "dashboard.db"
    db_path.write_text("", encoding="utf-8")

    with (
        patch.object(dashboard, "set_parent_death_signal", create=True) as death_mock,
        patch.object(dashboard.DashboardProcess, "run", return_value=None) as run_mock,
        patch("src.ui.dashboard.argparse.ArgumentParser.parse_args", return_value=Namespace(database_path=str(db_path), config=None)),
    ):
        dashboard.main()

    death_mock.assert_called_once()
    run_mock.assert_called_once()


def test_web_app_creation_requests_parent_death_signal():
    from src.web import app as web_app

    with patch.object(web_app, "set_parent_death_signal", create=True) as death_mock:
        web_app.create_app()

    death_mock.assert_called_once()
