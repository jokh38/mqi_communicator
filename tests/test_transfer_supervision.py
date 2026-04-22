from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from main import MQIApplication
from src.infrastructure.transfer_process_manager import TransferProcessManager


def _make_manager(tmp_path: Path):
    logger = MagicMock()
    transfer_dir = tmp_path / "mqi_transfer" / "Linux"
    transfer_dir.mkdir(parents=True, exist_ok=True)
    (transfer_dir / "mqi_transfer.py").write_text("print('ok')\n")
    return TransferProcessManager(
        project_root=tmp_path,
        python_executable="/usr/bin/python3.10",
        logger=logger,
    )


def test_transfer_manager_returns_false_when_child_exits_before_listening(tmp_path: Path):
    manager = _make_manager(tmp_path)

    process = MagicMock()
    process.pid = 1234
    process.poll.return_value = 1

    with patch("src.infrastructure.transfer_process_manager.subprocess.Popen", return_value=process), \
         patch("src.infrastructure.transfer_process_manager.time.sleep"):
        assert manager.start() is False

    process.poll.assert_called()
    manager.logger.error.assert_called()


def test_communicator_startup_fails_hard_if_transfer_cannot_start():
    app = MQIApplication(config_path=Path("mqi_communicator/config/config.yaml"))
    app.logger = MagicMock()
    app.settings = MagicMock()
    app.settings.get_ui_config.return_value = {"auto_start": True, "web": {"enabled": True, "port": 8080}}
    app.settings.get_database_path.return_value = Path("/tmp/mqi_communicator.db")
    app.config_path = None

    transfer_manager = MagicMock()
    transfer_manager.start.return_value = False

    with patch("main.TransferProcessManager", return_value=transfer_manager), \
         patch("main.UIProcessManager") as ui_manager_cls:
        with pytest.raises(RuntimeError):
            app.start_dashboard()

    ui_manager_cls.assert_not_called()
    transfer_manager.start.assert_called_once()


def test_communicator_shutdown_stops_transfer_before_dashboard():
    app = MQIApplication(config_path=Path("mqi_communicator/config/config.yaml"))
    app.logger = MagicMock()
    app.transfer_process_manager = MagicMock()
    app.ui_process_manager = MagicMock()
    app.monitor_db_connection = MagicMock()
    app.process_registry = MagicMock()

    app.shutdown()

    app.transfer_process_manager.stop.assert_called_once()
    app.ui_process_manager.stop.assert_called_once()
