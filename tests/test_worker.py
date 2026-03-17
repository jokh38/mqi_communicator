import builtins
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.core import worker


def _import_module_without_paramiko(module_name: str, monkeypatch: pytest.MonkeyPatch):
    original_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "paramiko":
            raise ModuleNotFoundError("No module named 'paramiko'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, "paramiko", raising=False)
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.delitem(sys.modules, "src.handlers.execution_handler", raising=False)
    monkeypatch.delitem(sys.modules, "src.utils.ssh_helper", raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked_import)
    return importlib.import_module(module_name)


@patch("src.core.worker.allocate_gpus_for_pending_beams")
@patch("src.repositories.gpu_repo.GpuRepository")
@patch("src.utils.db_context.get_db_session")
@patch("src.core.worker.TpsGenerator")
@patch("src.core.worker.submit_beam_worker")
def test_try_allocate_pending_beams_uses_persisted_beam_number(
    mock_submit_beam_worker,
    mock_tps_generator_cls,
    mock_get_db_session,
    mock_gpu_repo_cls,
    mock_allocate_gpus,
):
    mock_allocate_gpus.return_value = [{"gpu_uuid": "gpu-1"}]
    mock_tps_generator = MagicMock()
    mock_tps_generator.generate_tps_file_with_gpu_assignments.return_value = True
    mock_tps_generator_cls.return_value = mock_tps_generator
    mock_gpu_repo_cls.return_value = MagicMock()

    case_repo = MagicMock()
    case_repo.get_beams_for_case.return_value = [
        SimpleNamespace(beam_id="beam-02", beam_number=2),
        SimpleNamespace(beam_id="beam-10", beam_number=10),
    ]
    db_conn = MagicMock()

    repo_context = MagicMock()
    repo_context.__enter__.return_value = case_repo
    repo_context.__exit__.return_value = False
    db_context = MagicMock()
    db_context.__enter__.return_value = db_conn
    db_context.__exit__.return_value = False
    mock_get_db_session.side_effect = [repo_context, db_context]

    settings = MagicMock()
    settings.get_path.return_value = "/tmp/csv_output"
    settings.get_handler_mode.return_value = "local"
    logger = MagicMock()
    executor = MagicMock()
    active_futures = {}
    pending_beams = {
        "case-1": {
            "pending_jobs": [{"beam_id": "beam-10", "beam_path": Path("/cases/case-1/beam-10")}],
            "case_path": Path("/cases/case-1"),
        }
    }

    worker.try_allocate_pending_beams(
        pending_beams_by_case=pending_beams,
        executor=executor,
        active_futures=active_futures,
        settings=settings,
        logger=logger,
    )

    mock_tps_generator.generate_tps_file_with_gpu_assignments.assert_called_once()
    assert (
        mock_tps_generator.generate_tps_file_with_gpu_assignments.call_args.kwargs["beam_number"]
        == 10
    )


def test_worker_imports_without_paramiko_installed(monkeypatch):
    module = _import_module_without_paramiko("src.core.worker", monkeypatch)

    assert hasattr(module, "worker_main")
