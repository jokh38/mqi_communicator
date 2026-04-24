# Failures

## 2026-04-24 Full Suite Validation Gap

Command:
`python -m pytest`

Observation:
- Full run was interrupted after it stopped producing output while entering web-related tests.
- Before the stall, failures appeared in settings/path, process hierarchy, progress pattern, kill script, and TPS generator areas.

Follow-up scoped command:
`python -m pytest tests/test_settings.py tests/test_kill_all_mqi_processes_script.py tests/test_pattern_verification.py tests/test_process_hierarchy.py tests/test_progress_tracking_refactor.py tests/test_tps_generator.py`

Result:
- 15 failed, 14 passed

Failure categories observed:
- Repository path expectation mismatch: tests expect `/home/SMC/MOQUI_SMC/...`, local config resolves `<repo-root>/MOQUI_SMC/...`.
- Sandbox write restriction: `tests/test_kill_all_mqi_processes_script.py` attempts to write `<repo-root>/MOQUI_SMC/mqi_transfer/Linux/app_config.ini`, outside this workspace writable root.
- Existing process launch contract tests fail on missing `start_new_session` / `preexec_fn` kwargs.
- Existing InitialState/progress pattern tests return `FailedState` instead of `SimulationState`.
- Existing TPS generator test expects `/home/SMC/MOQUI_SMC/...` output path while local config emits `<repo-root>/MOQUI_SMC/...`.

Component validation for Phase 1 passed:
`python -m pytest tests/test_pydantic_config.py tests/test_settings_pydantic_integration.py tests/test_database_connection.py tests/test_case_repo_atomic.py tests/test_dispatcher.py tests/test_execution_handler.py`

Result:
- 94 passed

## 2026-04-24 Phase 2 Full Suite Validation Gap

Command:
`timeout 90s python -m pytest`

Observation:
- Full run collected 256 tests and reached `tests/test_web_failure_views.py`.
- Before the stall, failures appeared in the same pre-existing categories recorded above:
  - `tests/test_pattern_verification.py`: 3 failures
  - `tests/test_process_hierarchy.py`: 2 failures
  - `tests/test_progress_tracking_refactor.py`: 2 failures
  - `tests/test_settings.py`: 7 failures
  - `tests/test_tps_generator.py`: 1 failure
- The run then stopped producing output in the web-test area and did not produce a final pytest summary.
- The lingering pytest process was terminated with:
  - `pkill -f 'python -m pytest' || true; pkill -f 'timeout 90s python -m pytest' || true; ps -eo pid,ppid,pgid,stat,cmd | rg 'pytest|timeout 90s python' || true`

Component validation for Phase 2 passed:
`python -m compileall -q src tests`

Result:
- Passed

Component validation for Phase 2 touched modules passed:
`python -m pytest tests/test_execution_handler.py tests/test_states.py tests/test_ui_provider.py tests/test_gpu_monitor.py tests/test_ui_process_manager.py tests/test_case_repo_mapping.py tests/test_case_repo_retry.py tests/test_ptn_checker_integration.py tests/test_ptn_checker_workflow.py tests/test_process_registry.py`

Result:
- 56 passed

Regression component validation for Phase 1 coverage passed after Phase 2:
`python -m pytest tests/test_pydantic_config.py tests/test_settings_pydantic_integration.py tests/test_database_connection.py tests/test_case_repo_atomic.py tests/test_dispatcher.py tests/test_execution_handler.py`

Result:
- 91 passed

## 2026-04-24 Phase 3 Batch 1 Validation

Component validation for Phase 3 batch 1 passed:
`python -m compileall -q src tests`

Result:
- Passed

Component validation for Phase 3 batch 1 touched modules passed:
`python -m pytest tests/test_process_registry.py tests/test_ui_process_manager.py tests/test_case_aggregator.py tests/test_fraction_grouper.py tests/test_gpu_monitor.py tests/test_gpu_status.py tests/test_case_repo_mapping.py`

Result:
- 36 passed

Full end-to-end validation was not rerun for this batch because the Phase 2 full-suite gap remains open.

## 2026-04-24 Phase 3 Batch 2 Validation

Initial component command:
`python -m compileall -q src tests && python -m pytest tests/test_dispatcher.py tests/test_worker.py tests/test_states.py tests/test_case_aggregator.py tests/test_fraction_grouper.py tests/test_tps_generator.py`

Observation:
- Compile passed.
- 46 tests passed and 2 failed.
- `tests/test_worker.py::test_try_allocate_pending_beams_writes_grouped_room_tps_files` expected `tmp/csv_output/G1/case-1`, but the committed worker code already used `tmp/csv_output/G1/case-1/Log_csv`; the test expectation was stale and was corrected.
- `tests/test_tps_generator.py::test_generate_tps_file_uses_beam_specific_output_dir` remains in the previously documented local path mismatch category: it expects `/home/SMC/MOQUI_SMC/...`, while this local environment resolves `<repo-root>/MOQUI_SMC/...`.

Follow-up component validation passed:
`python -m compileall -q src tests && python -m pytest tests/test_dispatcher.py tests/test_worker.py tests/test_states.py tests/test_case_aggregator.py tests/test_fraction_grouper.py`

Result:
- 45 passed

Scoped TPS generator validation excluding the known local path mismatch passed:
`python -m pytest tests/test_tps_generator.py -k 'not uses_beam_specific_output_dir'`

Result:
- 2 passed, 1 deselected

Full end-to-end validation was not rerun for this batch because the Phase 2 full-suite gap remains open.

## 2026-04-24 Phase 4 Validation

Component validation for Phase 4 passed:
`python -m pytest tests/test_pydantic_config.py tests/test_settings_pydantic_integration.py tests/test_tps_generator.py -q`

Result:
- 62 passed

Component validation for Phase 4 touched and adjacent modules passed:
`python -m compileall -q src tests`

Result:
- Passed

Additional scoped component validation passed:
`python -m pytest tests/test_pydantic_config.py tests/test_settings_pydantic_integration.py tests/test_tps_generator.py tests/test_states.py tests/test_gpu_monitor.py tests/test_gpu_status.py tests/test_case_repo_mapping.py -q`

Result:
- 88 passed

Observation:
- The previously documented local TPS generator path mismatch was corrected by deriving the expected base directory from `Settings`.
- Full end-to-end validation was not rerun because the Phase 2 full-suite gap remains open in unrelated areas.

## 2026-04-24 Phase 5 Validation

Component validation for Phase 5 passed:
`python -m pytest tests/test_enum_state_cleanup.py tests/test_states.py tests/test_dispatcher.py tests/test_case_repo_mapping.py tests/test_ui_provider.py tests/test_case_failure_metadata.py -q`

Result:
- 50 passed

Additional workflow-adjacent component validation passed:
`python -m pytest tests/test_workflow_manager.py tests/test_main_loop.py tests/test_worker.py -q`

Result:
- 39 passed

Compile validation passed:
`python -m compileall -q src tests`

Result:
- Passed

Web-adjacent validation attempts:
- `timeout 30s python -m pytest tests/test_web_failure_views.py -q`
  - Result: timed out with exit code 124 and no pytest summary
- `timeout 30s python -m pytest tests/test_web_retry_endpoint.py -q`
  - Result: timed out with exit code 124 and no pytest summary

Observation:
- The web-test timeout behavior is consistent with the previously documented full-suite/web-area validation gap.
- Full end-to-end validation was not rerun because that gap remains open.

## 2026-04-24 Phase 6 Batch 3 Validation

Component validation for Phase 6 batch 3 passed:
`python -m pytest tests/test_error_handling_logging_cleanup.py -q`

Result:
- 9 passed

Compile validation passed:
`python -m compileall -q src tests`

Result:
- Passed

Additional repository/GPU-adjacent component validation passed:
`python -m pytest tests/test_error_handling_logging_cleanup.py tests/test_gpu_monitor.py tests/test_gpu_status.py tests/test_case_repo_mapping.py tests/test_case_repo_retry.py tests/test_case_repo_atomic.py tests/test_dispatcher.py tests/test_states.py -q`

Result:
- 58 passed

Observation:
- This is component validated only. Full end-to-end validation was not rerun because the previously documented full-suite/web-area validation gap remains open.

## 2026-04-24 Phase 7 Validation

Component validation for Phase 7 passed:
`python -m pytest tests/test_import_documentation_cleanup.py -q`

Result:
- 3 passed

Compile validation passed:
`python -m compileall -q src tests`

Result:
- Passed

Additional touched and adjacent component validation passed:
`python -m pytest tests/test_import_documentation_cleanup.py tests/test_error_handling_logging_cleanup.py tests/test_case_aggregator.py tests/test_dispatcher.py tests/test_worker.py tests/test_gpu_monitor.py tests/test_ui_provider.py tests/test_ui_process_manager.py -q`

Result:
- 67 passed

Observation:
- This is component validated only. Full end-to-end validation was not rerun because the previously documented full-suite/web-area validation gap remains open.

## 2026-04-24 Post-Plan Full Automated Suite Validation

Previously failing non-web validation command now passes:
`python -m pytest tests/test_settings.py tests/test_kill_all_mqi_processes_script.py tests/test_pattern_verification.py tests/test_process_hierarchy.py tests/test_progress_tracking_refactor.py tests/test_tps_generator.py -q`

Result:
- 30 passed

Broad non-web validation now passes:
`python -m pytest --ignore=tests/test_web_failure_views.py --ignore=tests/test_web_retry_endpoint.py -q`

Result:
- 266 passed

Previously hanging web validations now pass:
`timeout 30s python -m pytest tests/test_web_failure_views.py -q`

Result:
- 7 passed

`timeout 30s python -m pytest tests/test_web_retry_endpoint.py -q`

Result:
- 3 passed

Full automated suite validation now passes:
`python -m pytest -q`

Result:
- 276 passed

Compile validation passed:
`python -m compileall -q src tests`

Result:
- Passed

Observation:
- The prior full-suite/web-area validation gap is resolved for the automated pytest suite.
- This is automated test-suite validation, not operational end-to-end validation. No live MOQUI/GPU/DICOM workflow run was executed in this environment.
