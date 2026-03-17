import multiprocessing as mp
from pathlib import Path
from unittest.mock import MagicMock, patch

from main import MQIApplication


def test_run_worker_loop_processes_completed_workers_when_queue_is_empty():
    app = MQIApplication(config_path=Path("mqi_communicator/config/config.yaml"))
    app.logger = MagicMock()
    app.settings = MagicMock()
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
    app.settings = MagicMock()
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
