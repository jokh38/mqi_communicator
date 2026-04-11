from pathlib import Path
from unittest.mock import MagicMock, patch
import platform
import subprocess

from src.config.settings import Settings
from src.infrastructure.ui_process_manager import UIProcessManager


def make_manager():
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config/config.yaml"
    settings = Settings(config_path)
    logger = MagicMock()
    return UIProcessManager(
        database_path="/tmp/mqi_communicator.db",
        config_path=config_path,
        config=settings,
        logger=logger,
    )


def test_ensure_web_port_ready_reclaims_port():
    manager = make_manager()
    owner_info = {"pid": 1234, "command": "python -m uvicorn src.web.app:app"}

    with (
        patch.object(manager, "_check_port_available", return_value=False),
        patch.object(manager, "_find_port_owner_info", return_value=owner_info),
        patch.object(manager, "_terminate_process_tree", return_value=True),
        patch.object(manager, "_wait_for_port_available", return_value=True)
    ):
        assert manager._ensure_web_port_ready(8080) is True

    manager.logger.warning.assert_called()


@patch("src.infrastructure.ui_process_manager.subprocess.Popen")
def test_start_web_mode(mock_popen):
    manager = make_manager()
    
    # Mock settings to return web mode
    mock_ui_config = {
        "mode": "web",
        "web": {"port": 8080, "host": "0.0.0.0"}
    }
    manager.config.get_ui_config = MagicMock(return_value=mock_ui_config)
    
    process = MagicMock()
    process.poll.return_value = None
    process.stdout = MagicMock()
    process.stderr = MagicMock()
    process.pid = 4321
    mock_popen.return_value = process

    with (
        patch.object(manager, "_ensure_web_port_ready", return_value=True) as ensure_port_mock,
        patch.object(manager, "_get_ui_command", return_value=["python", "-m", "uvicorn", "src.web.app:app"]),
        patch("src.infrastructure.ui_process_manager.time.sleep")
    ):
        assert manager.start() is True

    ensure_port_mock.assert_called_once_with(8080)
    mock_popen.assert_called_once()