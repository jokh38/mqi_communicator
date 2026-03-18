import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.infrastructure.process_registry import ProcessRegistry


def test_reclaim_previous_instance_terminates_matching_metadata_process(tmp_path):
    runtime_file = tmp_path / ".runtime" / "main_process.json"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text(json.dumps({
        "pid": 4242,
        "repo_root": str(tmp_path),
        "config_path": str(tmp_path / "config.yaml"),
        "command": "python main.py config.yaml",
        "recorded_at": 123.0,
    }))

    logger = MagicMock()
    registry = ProcessRegistry(
        repo_root=tmp_path,
        config_path=tmp_path / "config.yaml",
        logger=logger,
        runtime_file=runtime_file,
    )

    with patch.object(registry, "_pid_matches_instance", return_value=True), \
         patch.object(registry, "_terminate_process_tree", return_value=True) as terminate_mock, \
         patch.object(registry, "_wait_for_exit", return_value=True):
        registry.reclaim_previous_instance(current_pid=9999)

    terminate_mock.assert_called_once_with(4242)


def test_reclaim_previous_instance_falls_back_to_process_scan_and_ignores_other_configs(tmp_path):
    runtime_file = tmp_path / ".runtime" / "main_process.json"
    logger = MagicMock()
    registry = ProcessRegistry(
        repo_root=tmp_path,
        config_path=tmp_path / "config-a.yaml",
        logger=logger,
        runtime_file=runtime_file,
    )

    candidates = [
        {"pid": 1111, "command": "python /repo/main.py --config config-b.yaml"},
        {"pid": 2222, "command": "python /repo/main.py --config config-a.yaml"},
    ]

    with patch.object(registry, "_read_runtime_metadata", return_value=None), \
         patch.object(registry, "_find_matching_process_candidates", return_value=candidates), \
         patch.object(registry, "_candidate_matches_instance", side_effect=[False, True]), \
         patch.object(registry, "_terminate_process_tree", return_value=True) as terminate_mock, \
         patch.object(registry, "_wait_for_exit", return_value=True):
        registry.reclaim_previous_instance(current_pid=9999)

    terminate_mock.assert_called_once_with(2222)
