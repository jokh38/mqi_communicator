# Cleanup Plan

Based on verified `CODEBASE_AUDIT.md` findings. 7 phases, each independently committable.

---

## Phase 1: Critical Bugs (5 fixes)

### 1.1 BUG-1 — GPU leak in dispatcher
- **File:** `src/core/dispatcher.py:591`
- **Problem:** `release_all_for_case(case_id)` passes `case_id` but `gpu_resources.assigned_case` FK references `beams(beam_id)`. No rows ever match.
- **Fix:** Iterate beams for the case and release GPUs per beam_id. Use the existing `case_repo.get_beams_for_case(case_id)` to get beam IDs, then call `gpu_repo.release_all_for_case(beam_id)` for each. Alternatively, add a `release_all_for_case_beams(case_id)` method that joins `beams` table.
- **Test:** Verify GPU release actually occurs after dispatcher failure path.

### 1.2 BUG-2 — Pydantic field-name mismatches silently drop YAML values
- **Files:** `src/config/pydantic_models.py`, `config/config.yaml`
- **Problem:** 4 field names differ between YAML keys and Pydantic fields:
  - `synchronous_mode` (YAML) vs `synchronous` (Pydantic)
  - `cache_size_mb` (YAML) vs `cache_size` (Pydantic)
  - `max_case_retries` (YAML) vs `max_retries` (Pydantic)
  - `log_level` (YAML) vs `level` (Pydantic)
- **Fix:** Add `Field(alias="synchronous_mode")` etc. to Pydantic models and set `model_config = {"populate_by_name": True}` so both names work. Update `DatabaseConfig.cache_size` to accept MB and convert internally, or rename field to `cache_size_mb`.
- **Test:** Load config with non-default YAML values, verify Pydantic picks them up.

### 1.3 BUG-3 — Progress regression during simulation
- **File:** `src/handlers/execution_handler.py:156`
- **Problem:** Formula `(batch/total) * 60 + 30` starts at 36.0 (batch=1, total=10) which is below the 40.0 set by `SimulationState`.
- **Fix:** Change formula to `(batch/total) * 50 + 40`, which ranges from 45.0 to 90.0 (batch 1-based), starting above the config's 40.0 entry point. Regex pattern `(\d+) of (\d+) batches` confirms batch numbering is 1-based.
- **Test:** Verify monotonic progress values.

### 1.4 BUG-4 — Non-atomic `update_case_and_beams_status()`
- **File:** `src/repositories/case_repo.py:1064-1078`
- **Problem:** Two separate transactions. If second fails, case status is committed but beam statuses are unchanged.
- **Fix:** Wrap both operations in `with self.db.transaction() as conn:` and use `conn.execute()` directly (same pattern as `fail_case()`).
- **Test:** Simulate failure in second operation, verify rollback.

### 1.5 BUG-5 — Migration drops `has_live_compute` data
- **File:** `src/database/connection.py:385-400`
- **Problem:** `gpu_resources_new` table omits `has_live_compute`. Data lost, re-added with DEFAULT 0.
- **Fix:** Include `has_live_compute BOOLEAN NOT NULL DEFAULT 0` in the CREATE TABLE and add it to INSERT/SELECT columns.
- **Test:** Verify migration preserves existing `has_live_compute` values.

---

## Phase 2: Dead Code Removal (low risk, high impact)

### 2.1 Delete `src/config/constants.py`
- Inline the 3 used constants (`PHASE_HPC_RUNNING`, `PHASE_COMPLETED`, `PROGRESS_COMPLETED`) into `src/domain/states.py`
- Update `states.py` import to remove `from src.config.constants import ...`
- Delete file

### 2.2 Remove 20 dead production methods
Per the audit table. Remove each method and verify no callers exist.
Key removals:
- `format_progress_bar()` from `formatter.py`
- `_load_run_analysis()` from `ptn_checker.py` (also crashes on importlib)
- `validate_data_transfer_completion()` + `count_beam_subdirectories()` from `data_integrity_validator.py`
- `_process_case_data()` from `provider.py`
- `check_nvidia_smi_available()` from `gpu_monitor.py`
- `_get_process_creation_flags()`, `_verify_ttyd_startup()`, `_validate_ttyd_available()` from `ui_process_manager.py`
- `post_process()`, `download_directory()`, `cleanup()` from `execution_handler.py`
- `get_gpu_by_uuid()`, `find_and_lock_available_gpu()` from `gpu_repo.py`
- `get_cases_by_status()`, `get_all_active_cases()`, `assign_hpc_job_id_to_beam()`, `update_ptn_checker_status()`, `increment_ptn_checker_run_count()` from `case_repo.py`
- `resolve_path()` from `settings.py`

### 2.3 Decision on test-only methods
- `upload_file()`, `download_file()`, `upload_to_pc_localdata()` in `execution_handler.py`
- Decision: remove unless remote file transfer is planned. Remove corresponding tests.

### 2.4 Remove dead class `SystemStats` from `models.py`

---

## Phase 3: Duplication Extraction (6 extractions)

### 3.1 Process tree utilities → `src/infrastructure/process_utils.py`
- Extract from `process_registry.py` and `ui_process_manager.py`:
  - `terminate_process_tree()`
  - `terminate_process_tree_unix()`
  - `terminate_process_tree_windows()`
  - `collect_descendant_pids()`
  - `pid_exists()`
  - `get_process_command()`
- Both classes import and delegate to the shared module.

### 3.2 `_parse_delivery_timestamp` → `src/utils/planinfo.py`
- Extract from `case_aggregator.py:182` and `fraction_grouper.py:59`
- Both files import from new location.

### 3.3 GPU row-to-DTO mapping → `_map_row_to_gpu_resource()` in `gpu_repo.py`
- Extract inline into a private helper method within the same file.

### 3.4 TPS output dir resolution → helper function
- Extract the room-conditional path pattern from `dispatcher.py:213,475`, `worker.py:312`, `states.py:151` into a shared utility.

### 3.5 TPS generation multi-GPU loop
- `dispatcher.py:484-550` and `worker.py:298-381` share ~80 lines of near-identical logic.
- Extract shared logic into `src/core/tps_utils.py`.

### 3.6 PlanInfo beam number parser
- `case_aggregator.py:45-61` and `fraction_grouper.py:80-93` share ~15 lines.
- Extract into `src/utils/planinfo.py` (alongside `_parse_delivery_timestamp`).

---

## Phase 4: Config Layer Cleanup

### 4.1 Remove or wire dead Pydantic sections
- `RetryPolicyConfig` — either add `retry_policy` section to `config.yaml` and wire it through `settings.py`, or delete the model and remove from `AppConfig`.
- `ProgressTrackingConfig` — add `progress_tracking` section to `config.yaml` (it already has defaults in Pydantic). Wire through `settings.py` to replace hardcoded progress values.
- Dead `GpuConfig` fields (`enabled`, `memory_threshold_mb`, `utilization_threshold_percent`, `polling_interval_seconds`) — remove if unused by code, or wire them up.

### 4.2 Remove unused YAML keys
- `database.enable_foreign_keys` — remove from config.yaml
- `logging.console_level` — remove from config.yaml
- `processing.case_timeout_seconds` — remove from config.yaml
- `processing.scan_interval_seconds` — remove from config.yaml
- `ui.port` (8501) — remove or document that `ui.web.port` (8080) is the actual port
- `tps_generator.default_paths.base_directory` — remove (code reads `paths.base_directory`)

### 4.3 Fix `tps_generator.py` direct `_yaml_config` access
- `tps_generator.py:287` — replace `self.settings._yaml_config.get("paths", {})...` with `self.settings.get_path()` or add a public getter method.
- `tps_generator.py:362` — replace `self.settings._yaml_config.get("tps_generator", {})` with a public `get_tps_generator_config()` method on Settings.

### 4.4 Fix hardcoded values vs config contradictions
- `constants.py` `OutputFormat: "raw"` vs `config.yaml` `OutputFormat: dcm` — since constants.py is being deleted (Phase 2), this resolves itself.
- `MIN_GPU_MEMORY_MB = 2048` vs code default 1000 — resolves with constants.py deletion.

---

## Phase 5: Enum & State Machine Cleanup

### 5.1 Remove unused enum values
- `CaseStatus.POSTPROCESSING` — referenced in display/query logic but never set. Two options:
  - A) Wire it up: set `CaseStatus.POSTPROCESSING` during the postprocessing phase in the dispatcher. This requires adding a status transition.
  - B) Remove it: delete from enum, stage mapping, formatter colors, active statuses list, provider display.
  - **Recommended: Option A** — postprocessing is a real workflow phase, the enum should be set.
- `CaseStatus.CANCELLED` — keep for future cancellation feature, but document as "reserved, not yet implemented".
- ~~`BeamStatus.CSV_INTERPRETING`, `BeamStatus.TPS_GENERATION`~~ — **NOT DEAD.** These are set via `update_case_and_beams_status()` at `main.py:447` and `main.py:476`. No action needed.
- `WorkflowStep.PENDING`, `SIMULATION_RUNNING`, `COMPLETED`, `FAILED` — zero references. Remove these 4 values from the enum.

### 5.2 Fix double FAILED update
- **Option A:** Remove the `FAILED` update from `handle_state_exceptions` decorator (line 58-60), let `FailedState.execute()` handle it solely.
- **Option B:** Skip the update in `FailedState.execute()` if beam is already FAILED.
- **Recommended: Option B** — `FailedState.execute()` adds the important `update_case_status_from_beams()` call. Add guard: `if beam.status != BeamStatus.FAILED: self._update_status(...)`.

### 5.3 Fix CASE_STAGE_MAPPING gap (stage 2 missing)
- `PROCESSING` maps to stage 3 but should be stage 2. Change `CaseStatus.PROCESSING: 2` and update `POSTPROCESSING: 3`, `COMPLETED: 4`, `FAILED: 4`, `CANCELLED: 4`. Update `TOTAL_STAGES = 4`.

### 5.4 Replace hardcoded step-status strings
- `dispatcher.py` uses free-form strings `"started"`, `"completed"`, `"failed"`, `"pending"`, `"partial"` for workflow step status.
- Create a `StepStatus` enum with these values and use it consistently.

---

## Phase 6: Error Handling & Logging

### 6.1 Add `raise ... from e` at bare raise locations
- 8 locations across `data_integrity_validator.py`, `tps_generator.py`, `worker.py`.
- Add `from e` to preserve exception chain.

### 6.2 Replace `print()` with structured logger
- 17 locations across `dashboard.py`, `display.py`, `settings.py`, `ptn_checker.py`.
- Some in `dashboard.py` are pre-logger bootstrap — keep those as `print()` but add comment explaining why.
- For the rest, replace with `logger.info/warning/error()`.

### 6.3 Replace `logging.getLogger()` with `LoggerFactory`
- `settings.py:96,162,174` — settings is loaded before LoggerFactory may be available; evaluate if LoggerFactory can be used or if stdlib fallback is intentional.
- `execution_handler.py:331` — replace with `self.logger`.

### 6.4 Standardize fail-case pattern
- Document the canonical fail path: `fail_case()` for case-level failures, `update_beam_status(FAILED)` + `update_case_status_from_beams()` for beam-level failures.
- Review `dispatcher.py:161` `update_case_status(FAILED)` — should it also fail beams? If so, replace with `fail_case()`.

### 6.5 Wire up or remove retry error types
- `RetryableError` and `PermanentFailureError` are defined but never raised.
- Either raise them in appropriate places (e.g., transient GPU failures → `RetryableError`, data corruption → `PermanentFailureError`) or remove them and simplify the decorator.

### 6.6 Triage `except Exception` blocks (100 total)
- Not all are anti-patterns — some are legitimate top-level error boundaries.
- Priority: narrow the ones in critical paths (state machine, dispatcher, GPU operations) to catch specific exceptions.
- Lower priority: UI code `except Exception` blocks can remain broader since they protect display stability.

---

## Phase 7: Import & Documentation Cleanup

### 7.1 Remove unused imports
- `dashboard.py`: remove `import os`, `import json`, `from datetime import timezone, timedelta` (keep `datetime`)
- `formatter.py`: remove `from typing import Dict, Any`
- `gpu_monitor.py`: remove `import time`
- `case_aggregator.py`: remove `WorkflowStep` from import
- `dispatcher.py`: remove `scan_existing_cases`, `CaseDetectionHandler` from import

### 7.2 Remove redundant local re-imports
- `execution_handler.py:85-86` — remove local `import re`, `from pathlib import Path`
- `dispatcher.py:266` — remove local `from src.core.data_integrity_validator import DataIntegrityValidator`
- `worker.py:307` — remove local `from src.utils.db_context import get_db_session`

### 7.3 Update stale documentation
- `README.md:193,254` — remove paramiko references
- `INSTALLATION.md:144,233` — remove paramiko from dependency check commands
- `README.md` — fix "dashboard" config key reference to "ui"

---

## Execution Notes

- Each phase is a separate commit (or set of small commits)
- Run `python -m pytest` after each phase
- Phases 1-2 are highest priority (bugs + dead code)
- Phases 3-7 are quality improvements with decreasing urgency
- Phase 5 has design decisions (marked "Recommended") that may need confirmation
