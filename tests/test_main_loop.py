import multiprocessing as mp
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from main import MQIApplication


def _mock_settings(**overrides):
    """Create a MagicMock settings with get_database_path returning a safe temp path."""
    settings = MagicMock()
    settings.get_database_path.return_value = Path(tempfile.gettempdir()) / "test_mqi.db"
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def test_run_worker_loop_processes_completed_workers_when_queue_is_empty():
    app = MQIApplication(config_path=Path("mqi_communicator/config/config.yaml"))
    app.logger = MagicMock()
    app.settings = _mock_settings()
    app.settings.get_processing_config.return_value = {"max_workers": 1}
    app.case_queue = MagicMock()
    app.case_queue.get.side_effect = [mp.queues.Empty(), KeyboardInterrupt()]
    app.shutdown_event = MagicMock()
    app.shutdown_event.is_set.side_effect = [False, False]

    executor = MagicMock()
    executor_context = MagicMock()
    executor_context.__enter__.return_value = executor
    executor_context.__exit__.return_value = False

    with patch("main.ProcessPoolExecutor", return_value=executor_context), \
         patch("main.monitor_completed_workers") as monitor_mock, \
         patch("main.time.sleep"):
        app.run_worker_loop()

    monitor_mock.assert_called_once()


def test_run_worker_loop_attempts_pending_beam_allocation_when_queue_is_empty():
    app = MQIApplication(config_path=Path("mqi_communicator/config/config.yaml"))
    app.logger = MagicMock()
    app.settings = _mock_settings()
    app.settings.get_processing_config.return_value = {"max_workers": 1}
    app.case_queue = MagicMock()
    app.case_queue.get.side_effect = [mp.queues.Empty(), KeyboardInterrupt()]
    app.shutdown_event = MagicMock()
    app.shutdown_event.is_set.side_effect = [False, False]

    executor = MagicMock()
    executor_context = MagicMock()
    executor_context.__enter__.return_value = executor
    executor_context.__exit__.return_value = False

    pending_case_path = Path("cases") / "case-1"

    def seed_pending(*_args, **_kwargs):
        _args[1]["case-1"] = {
            "case_path": pending_case_path,
            "pending_jobs": [{"beam_id": "beam-1", "beam_path": pending_case_path / "beam-1"}],
        }

    with patch("main.ProcessPoolExecutor", return_value=executor_context), \
         patch("main.monitor_completed_workers", side_effect=seed_pending), \
         patch("main.try_allocate_pending_beams") as allocate_mock, \
         patch("main.time.sleep"):
        app.run_worker_loop()

    allocate_mock.assert_called_once()


def test_start_gpu_monitor_reconciles_stale_assignments_with_case_repo_session():
    app = MQIApplication(config_path=Path("mqi_communicator/config/config.yaml"))
    app.logger = MagicMock()
    app.settings = _mock_settings()
    app.settings.execution_handler = {"GpuMonitor": "local"}
    app.settings.get_gpu_config.return_value = {
        "monitor_interval": 10,
        "gpu_monitor_command": "nvidia-smi --query-gpu=index --format=csv,noheader",
    }

    monitor_db_connection = MagicMock()
    case_repo = MagicMock()
    app._create_db_connection = MagicMock(return_value=monitor_db_connection)

    repo_context = MagicMock()
    repo_context.__enter__.return_value = case_repo
    repo_context.__exit__.return_value = False

    gpu_monitor = MagicMock()

    with patch("main.GpuRepository") as gpu_repo_cls, \
         patch("main.ExecutionHandler") as execution_handler_cls, \
         patch("main.GpuMonitor", return_value=gpu_monitor), \
         patch("main.get_db_session", return_value=repo_context):
        app.start_gpu_monitor()

    gpu_repo_cls.assert_called_once_with(monitor_db_connection, app.logger, app.settings)
    execution_handler_cls.assert_called_once()
    gpu_monitor.start.assert_called_once()
    gpu_monitor.reconcile_stale_assignments.assert_called_once_with(case_repo)


def test_run_reclaims_and_registers_matching_previous_process_before_startup():
    app = MQIApplication(config_path=Path("config/config.yaml"))
    lifecycle = []
    registry = MagicMock()

    def mark(name, value=None):
        lifecycle.append(name)
        return value

    def init_logging():
        app.logger = MagicMock()
        mark("logging")

    app.initialize_logging = MagicMock(side_effect=init_logging)
    app.initialize_database = MagicMock(side_effect=lambda: mark("database"))
    app.initialize_ssh_client = MagicMock(side_effect=lambda: mark("ssh"))
    app.start_file_watcher = MagicMock(side_effect=lambda: mark("watcher"))
    app.start_dashboard = MagicMock(side_effect=lambda: mark("dashboard"))
    app.start_gpu_monitor = MagicMock(side_effect=lambda: mark("gpu"))
    app.run_worker_loop = MagicMock(side_effect=lambda: mark("worker_loop"))
    app.shutdown = MagicMock(side_effect=lambda: mark("shutdown"))
    app.settings = _mock_settings(execution_handler={"GpuMonitor": "local"})

    registry.reclaim_previous_instance.side_effect = lambda current_pid: mark("reclaim")
    registry.register_current_process.side_effect = lambda current_pid: mark("register")

    with patch("main.ProcessRegistry", return_value=registry), \
         patch("main.scan_existing_cases", side_effect=lambda *args, **kwargs: mark("scan")), \
         patch("main.threading.Thread") as thread_cls:
        thread = MagicMock()
        thread.start.side_effect = lambda: mark("monitor_thread")
        thread_cls.return_value = thread
        app.run()

    testcase = unittest.TestCase()
    testcase.assertLess(lifecycle.index("reclaim"), lifecycle.index("database"))
    testcase.assertLess(lifecycle.index("register"), lifecycle.index("scan"))
