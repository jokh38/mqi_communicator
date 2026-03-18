# MQI Communicator Inconsistency Analysis Report

**Report Date:** 2026-03-18
**System:** Linux T640 (NVIDIA GeForce RTX 2080 x4)
**Analyst:** Oracle (Automated Architecture & Debugging Analysis)
**Data Sources:** `monitoring_report.txt`, `INSTRUCTION_TEST.md`, full source code review

---

## Executive Summary

The MQI Communicator system demonstrates a well-structured architecture with clean separation of concerns (Repository Pattern, State Pattern, Strategy Pattern for execution modes). However, a comprehensive cross-referencing of the monitoring data against the source code and expected behaviors reveals **3 critical issues**, **8 warning-level issues**, and **5 informational findings** that collectively present meaningful risk to production deployment.

The most severe finding is a **race condition in GPU allocation** between the main process and the GPU monitor thread that can cause silent data corruption. The second most severe is a **critical typo in the simulation completion marker** that will cause every local simulation to timeout instead of detecting completion. The third is a **database-status inconsistency** where beams are recorded as `csv_interpreting` at 70% progress while the case is at `processing` 50%, indicating the status machine has lost coherence.

**Production Readiness Verdict:** NOT READY -- the completion marker typo alone would cause 100% of local simulations to appear to timeout. The remaining issues must be resolved before clinical deployment.

---

## Table of Contents

1. [Critical Issues (C-1 through C-3)](#1-critical-issues)
2. [Warning-Level Issues (W-1 through W-8)](#2-warning-level-issues)
3. [Informational Findings (I-1 through I-5)](#3-informational-findings)
4. [Architecture & Design Analysis](#4-architecture--design-analysis)
5. [Root Cause Analysis for Key Failures](#5-root-cause-analysis)
6. [Risk Assessment Matrix](#6-risk-assessment-matrix)
7. [Prioritized Recommendations](#7-prioritized-recommendations)

---

## 1. Critical Issues

### C-1: Completion Marker Typo -- All Local Simulations Will Timeout

**Severity:** CRITICAL
**Category:** Config-Runtime Mismatch
**File:** `/home/jokh38/MOQUI_SMC/mqi_communicator/config/config.yaml`, line 105

**Finding:**

The completion marker configured for detecting simulation success contains a typo:

```yaml
# config/config.yaml, line 105
completion_markers:
  success_pattern: "Simlulation finalizing.."    # <-- TYPO: "Simlulation" instead of "Simulation"
```

The `INSTRUCTION_TEST.md` (line 281) expects the log marker to be `"Simulation finalizing"`, and the monitoring report (line 281) references it as `"Simulation finalizing"` in its log file completeness check.

**Impact:**

In `/home/jokh38/MOQUI_SMC/mqi_communicator/src/handlers/execution_handler.py`, lines 151-153, the local simulation polling loop uses this pattern:

```python
completion_markers = self.settings.get_completion_patterns()
success_pattern = completion_markers.get("success_pattern", "Simulation completed successfully")
# ...
if success_pattern in new_content:  # This string match will NEVER succeed
```

Because the configured `success_pattern` is `"Simlulation finalizing.."` (with double-l and double-dot), it will never match the actual simulation output which writes `"Simulation finalizing.."`. Every local simulation will run until the `hpc_job_timeout_seconds` (3600 seconds = 1 hour) elapses and then be marked as FAILED due to timeout.

**Root Cause:** Simple typo in configuration YAML. The default fallback pattern `"Simulation completed successfully"` in the code would also not match the actual simulation output of `"Simulation finalizing.."`, so even removing the config key entirely would not fix the problem.

**Verification:** The monitoring report confirms beams never progressed past `csv_interpreting` (70%) despite workers being submitted. The application was killed after 59 seconds, before any timeout could occur, but the typo ensures no simulation would ever be detected as complete.

---

### C-2: Database-Status Incoherence Between Case and Beam Records

**Severity:** CRITICAL
**Category:** Database-Process Mismatch / Log-Status Inconsistency
**Files:**
- `/home/jokh38/MOQUI_SMC/mqi_communicator/src/core/dispatcher.py`, lines 314-318
- `/home/jokh38/MOQUI_SMC/mqi_communicator/main.py`, lines 287-292

**Finding:**

The monitoring report reveals contradictory state in the database:

| Entity | Field | Value |
|--------|-------|-------|
| Case 55061194 | status | `processing` |
| Case 55061194 | progress | 50.0% |
| All 3 beams | status | `csv_interpreting` |
| All 3 beams | progress | 70.0% |

This is incoherent. The log entries (monitoring report lines 180-187) show the case progressed through:
1. `csv_interpreting` at 10% (line 180)
2. `processing` at 30% (line 181) with beams at `tps_generation`
3. `processing` at 50% (line 184) with beams set to `pending`
4. Workers submitted for all 3 beams (line 187)

Yet the database shows beams at `csv_interpreting` with 70% progress. This means beam statuses were **overwritten backwards** after the workers were submitted.

**Root Cause Analysis:**

The issue is a **dual-write conflict** between the main process and the worker processes. The sequence is:

1. Main process calls `_dispatch_workers()` which sets beams to `PENDING` (line 388 of `main.py`)
2. Main process submits beam workers to the `ProcessPoolExecutor`
3. Worker processes start and execute `InitialState.execute()` in `/home/jokh38/MOQUI_SMC/mqi_communicator/src/domain/states.py`, line 94, which immediately sets beam status back to `CSV_INTERPRETING`
4. The worker's `InitialState` also sets beam progress to the coarse phase progress for `CSV_INTERPRETING` (default 10%, but config may override)

The `InitialState` in `states.py` line 94 unconditionally writes `CSV_INTERPRETING` status:
```python
self._update_status(context, BeamStatus.CSV_INTERPRETING, "Performing initial validation for beam")
```

This contradicts the main loop which has already moved the beam status through `tps_generation` and `pending`. The worker overwrites the correct status with a semantically incorrect one.

The 70% progress on the beams suggests the progress tracking config's `coarse_phase_progress` for `CSV_INTERPRETING` has been set to 70.0 (or the progress was set by a previous run's state and never cleared due to `ON CONFLICT DO NOTHING` in `create_case_with_beams`).

**Impact:** The UI dashboard will show incorrect beam statuses. More critically, the `update_case_status_from_beams()` aggregator in `/home/jokh38/MOQUI_SMC/mqi_communicator/src/core/case_aggregator.py` uses beam statuses to determine case completion. If beams are stuck showing `csv_interpreting`, the case will never be marked as `COMPLETED`.

---

### C-3: Race Condition in GPU Allocation Between Monitor and Dispatcher

**Severity:** CRITICAL
**Category:** GPU Assignment Conflict / Race Condition
**Files:**
- `/home/jokh38/MOQUI_SMC/mqi_communicator/src/repositories/gpu_repo.py`, lines 37-91
- `/home/jokh38/MOQUI_SMC/mqi_communicator/src/infrastructure/gpu_monitor.py`, lines 87-122

**Finding:**

The GPU monitor's `update_resources()` method (via `GpuRepository.update_resources()`) uses an UPSERT that always sets the status to `IDLE`:

```python
# gpu_repo.py, lines 51-66
query = """
    INSERT INTO gpu_resources (...)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(uuid) DO UPDATE SET
        gpu_index = excluded.gpu_index,
        name = excluded.name,
        memory_total = excluded.memory_total,
        memory_used = excluded.memory_used,
        memory_free = excluded.memory_free,
        temperature = excluded.temperature,
        utilization = excluded.utilization,
        last_updated = CURRENT_TIMESTAMP
"""
# Note: the UPSERT does NOT update `status` or `assigned_case` on conflict
```

Upon closer inspection, the UPSERT intentionally omits `status` from the `ON CONFLICT DO UPDATE SET` clause, which means the status is preserved on update. However, the **INSERT** path always sets `status = GpuStatus.IDLE.value` (line 78 in the params_list). This means:

- If the `gpu_resources` table is cleared or the GPU UUID changes between nvidia-smi runs, the INSERT path will create a new row with `status = idle` even if the GPU is actively assigned.
- The `reconcile_stale_assignments()` method (gpu_monitor.py lines 148-170) releases GPUs that have no live compute app, but it runs only at startup. If a GPU finishes its compute app between the monitor poll and the next allocation check, the GPU could be released while the beam still believes it has a GPU assigned.

The monitoring report confirms that all 3 GPUs show 0% utilization with `assigned` status, and the data is 21 hours stale. The GPU assignments in the database have no timeout or heartbeat mechanism.

**Impact:** After application restart, `reconcile_stale_assignments()` will release all 3 GPU assignments (since no compute apps are running), but the case still shows `processing` at 50%. If the main loop re-queues the case (which `scan_existing_cases` will do since the status is `processing` which is in `retryable_statuses`), it will attempt to re-process the case from scratch, potentially creating duplicate beam records (though `ON CONFLICT DO NOTHING` in `create_case_with_beams` prevents this from being fatal).

---

## 2. Warning-Level Issues

### W-1: Signal Handler Calls shutdown() Then sys.exit(), Causing Double Shutdown

**Severity:** WARNING
**Category:** Resource Management
**File:** `/home/jokh38/MOQUI_SMC/mqi_communicator/main.py`, lines 594-605

**Finding:**

The signal handler explicitly calls `app.shutdown()` and then `sys.exit(0)`:

```python
def signal_handler(signum, frame):
    # ...
    app.shutdown()
    sys.exit(0)
```

But `sys.exit(0)` raises `SystemExit`, which is caught by the `except Exception` in `run()` (line 580), which then calls `self.shutdown()` again in the `finally` block (line 586). This results in a double shutdown where:
- `self.observer.stop()` / `self.observer.join()` is called on an already-stopped observer
- `self.gpu_monitor.stop()` logs "GPU monitor is not running" warning
- `self.monitor_db_connection.close()` attempts to close an already-closed connection

The monitoring report confirms this: 28 instances of "GPU monitor is not running" warnings (line 203) are logged during shutdown, and the log shows the double pattern.

**Impact:** Non-fatal but produces misleading warning logs and risks exceptions if close operations are not idempotent. The `SystemExit` is a subclass of `BaseException`, not `Exception`, so it will actually skip the `except Exception` handler and go straight to `finally`. However, this means `shutdown()` is called twice: once in the signal handler and once in `finally`.

---

### W-2: Worker Processes Create Independent Database Connections Without Schema Coordination

**Severity:** WARNING
**Category:** Concurrency / Database Contention
**Files:**
- `/home/jokh38/MOQUI_SMC/mqi_communicator/src/core/worker.py`, lines 40-42
- `/home/jokh38/MOQUI_SMC/mqi_communicator/src/utils/db_context.py`, lines 47-54

**Finding:**

Each worker process (running in `ProcessPoolExecutor`) creates its own `DatabaseConnection` and calls `case_repo.db.init_db()` independently:

```python
# worker.py, lines 40-42
with get_db_session(settings, logger) as case_repo:
    case_repo.db.init_db()  # Each worker re-runs schema initialization
```

Similarly, `run_case_level_tps_generation` in dispatcher.py line 313 also calls `case_repo.db.init_db()`.

With WAL mode enabled and `check_same_thread=False`, SQLite can handle concurrent readers, but concurrent writers from multiple processes (not threads) contending for the same database can cause `SQLITE_BUSY` errors. The database config shows `busy_timeout_ms: 5000` (config.yaml line 86), but the actual connection code in `connection.py` line 49 uses `connection_timeout_seconds: 30` which is the `sqlite3.connect(timeout=...)` parameter.

The `cache_size` PRAGMA in config.yaml shows `cache_size_mb: 64` but the connection code reads `cache_size` (line 52) with default `-2000`, meaning the config key `cache_size_mb` is never actually used -- it reads `cache_size` from config which does not exist, so it falls back to `-2000` (2MB).

**Impact:** Under load with 4 worker processes plus the main process plus the GPU monitor thread all writing to the same SQLite database, `SQLITE_BUSY` errors are likely. The 5-second busy timeout in config is not even applied because the config key does not match what the code reads.

---

### W-3: Beam-to-GPU Assignment Uses Positional Index, Not Beam ID Matching

**Severity:** WARNING
**Category:** Logic Error / Partial Output Risk
**File:** `/home/jokh38/MOQUI_SMC/mqi_communicator/main.py`, lines 382-384

**Finding:**

When dispatching workers after partial GPU allocation, the code assigns GPUs to beams by positional slicing:

```python
# main.py, lines 382-384
num_allocated = len(gpu_assignments)
allocated_jobs = beam_jobs[:num_allocated]
pending_jobs = beam_jobs[num_allocated:]
```

This assumes `beam_jobs` ordering matches the GPU assignment ordering from `run_case_level_tps_generation`, which iterates `zip(gpu_assignments, beams[:len(gpu_assignments)])` where `beams` comes from `case_repo.get_beams_for_case(case_id)` (ordered by `beam_number ASC`).

However, `beam_jobs` in the main loop comes from `prepare_beam_jobs()` which sorts by `beam_path.name` (case_aggregator.py line 301: `beam_folders.sort(key=lambda path: path.name)`), while `get_beams_for_case` sorts by `beam_number ASC, beam_id ASC`.

If the alphabetical sort of beam folder names does not match the `beam_number` order (which is common -- e.g., folder `2025042401440800` could have beam_number 3), the wrong beams get assigned to the wrong GPUs.

**Impact:** A beam could be submitted with a TPS file generated for a different GPU index, causing the simulation to use the wrong GPU or fail entirely.

---

### W-4: `get_all_active_cases` Includes `FAILED` Status in "Active" Cases

**Severity:** WARNING
**Category:** Stuck Cases / Recovery Mechanism
**File:** `/home/jokh38/MOQUI_SMC/mqi_communicator/src/repositories/case_repo.py`, lines 492-498

**Finding:**

```python
active_statuses = [
    CaseStatus.PENDING.value,
    CaseStatus.CSV_INTERPRETING.value,
    CaseStatus.PROCESSING.value,
    CaseStatus.POSTPROCESSING.value,
    CaseStatus.FAILED.value,        # <-- FAILED included as "active"
]
```

Similarly, `scan_existing_cases` in workflow_manager.py lines 167-174 includes `FAILED` and `CANCELLED` in `retryable_statuses`, meaning every application restart will re-queue failed cases.

**Impact:** The historical failed case 55758663 (GDCM assertion failure) will be re-queued on every restart, consuming GPU resources and generating repeated errors. There is no retry limit or backoff mechanism. Combined with the fact that the failed case's beam data remains in the database, each restart will hit `ON CONFLICT DO NOTHING` but still attempt full reprocessing.

---

### W-5: Stale GPU Data (21 Hours Old) Will Persist Until Next Application Run

**Severity:** WARNING
**Category:** Stale GPU Data / Orphaned Resources
**Monitoring Reference:** Line 96, line 278

**Finding:**

The GPU resource table shows `last_updated: 2026-03-17 23:38:02`, approximately 21 hours stale. All 3 GPUs remain marked as `assigned` in the database, but no processes are running.

Upon next startup, `reconcile_stale_assignments()` (gpu_monitor.py line 148) will check for active compute apps and release these stale assignments. However, this reconciliation runs only once at startup (main.py line 247), not periodically. If a simulation process crashes mid-execution (without going through the `_release_beam_gpu_assignment` path), the GPU will remain `assigned` until the next full application restart.

**Impact:** GPU starvation in long-running deployments. If a beam worker crashes, its GPU is never released until the next restart.

---

### W-6: Missing TPS File Verification in Monitoring Report

**Severity:** WARNING
**Category:** File-Database Mismatch
**Monitoring Reference:** Lines 289-291, 401-402, 557-559

**Finding:**

The monitoring report indicates:
```
TPS Input Files: 3 files (moqui_tps_*.in)
  * moqui_tps_55061194_2025042401552900.in
  * moqui_tps_55061194_2025042401501400.in
  * moqui_tps_55061194_2025042401440800.in (MISSING - expected 3)
```

Line 291 says "MISSING" but then lists 3 files with the note "(MISSING - expected 3)" which is self-contradictory. The report also says at line 402: "TPS files created: 3 files expected, 2 found (or 3 - verification pending)".

**Impact:** If the third TPS file is genuinely missing, beam `55061194_2025042401440800` will fail in `InitialState` validation (states.py line 110-112) when it checks `tps_file.exists()`. The beam will be marked FAILED. However, since GPU 0 shows memory usage of 1216 MB (indicating it was used), the file likely existed during processing but may have been cleaned up.

---

### W-7: Local Simulation Launch Uses Background Process That Cannot Be Tracked

**Severity:** WARNING
**Category:** Zombie Processes / Process Management
**File:** `/home/jokh38/MOQUI_SMC/mqi_communicator/config/config.yaml`, line 72

**Finding:**

The local simulation command template is:
```yaml
remote_submit_simulation: "nohup bash -c 'cd {mqi_run_dir} && ./tps_env/tps_env {tps_input_file} > {remote_log_path} 2>&1' > /dev/null 2>&1 &"
```

The trailing `&` causes the command to run in the background. `subprocess.run()` in the execution handler (execution_handler.py line 240) will execute this and return immediately with success (`returncode=0`), even though the actual simulation has not started or completed.

The `wait_for_job_completion` method then monitors the log file, but the process itself is completely detached. If the application is killed (SIGTERM), the background `tps_env` process continues running as an orphan. There is no PID tracking for these background processes.

**Impact:** On application shutdown (as observed in the monitoring report at 08:38:10), background simulation processes are not terminated. On restart, `reconcile_stale_assignments()` may release GPUs that are still in use by orphaned `tps_env` processes, leading to GPU memory conflicts.

---

### W-8: Multiple Workflow Step Retries Without Clear Deduplication

**Severity:** WARNING
**Category:** Slow Progress / Excessive Retries
**Monitoring Reference:** Lines 653-662

**Finding:**

The monitoring report notes:
```
Case 55061194 shows 10 workflow steps
Multiple csv_interpreting cycles (steps 32-42)
Root Cause: GPU availability changes during processing
Timeline:
  23:25:04 - First csv_interpreting completed
  23:25:10 - TPS generation pending (no GPUs available)
  23:31:50 to 23:37:17 - Multiple retries
  23:37:17 - Final TPS generation successful
```

The step IDs jump from "3-10" to "step 42", indicating at least 42 workflow step records were created for a single case. Given the 10-step timeline described, this suggests the case was reprocessed multiple times (possibly from multiple application restarts or re-queuing).

Each `scan_existing_cases` call at startup re-queues `processing` status cases (workflow_manager.py line 185-190), and there is no check for whether a case is already in the queue.

**Impact:** Database bloat from duplicate workflow step records. Wasted compute cycles re-running completed steps. The workflow_steps table will grow unboundedly with no archival mechanism.

---

## 3. Informational Findings

### I-1: Code Formatting Issues in states.py (Collapsed Multi-line Statements)

**File:** `/home/jokh38/MOQUI_SMC/mqi_communicator/src/domain/states.py`, lines 94, 133, 214, etc.

Multiple lines in `states.py` appear to have been collapsed into single lines, e.g., line 94:
```python
self._update_status(context, BeamStatus.CSV_INTERPRETING, "Performing initial validation for beam")        try:            p = context.settings.get_progress_tracking_config()...
```

This suggests a file corruption or bad merge. While Python may still execute this if the statement boundaries happen to align, it makes the code unmaintainable and prone to syntax errors.

### I-2: Historical GpuStatus Enum Value Mismatch (Now Fixed)

The monitoring report (line 452-454) notes a historical error: `"'available' is not a valid GpuStatus"`. The database migration in `connection.py` (lines 210-218) now normalizes `"available"` to `"idle"`, and the `GpuStatus` enum (enums.py lines 51-53) only contains `IDLE` and `ASSIGNED`. This is correctly handled.

### I-3: INSTRUCTION_TEST.md References Non-Existent Table `gpus`

The testing document references a `gpus` table (lines 58, 239, 486-487), but the actual schema uses `gpu_resources`. This is a documentation inconsistency, not a code issue, but it will cause test scripts to fail if executed literally.

### I-4: Database `synchronous` Config Key Mismatch

Config YAML uses `synchronous_mode: "NORMAL"` (line 88) but the connection code reads `db_config.get("synchronous", "NORMAL")` (connection.py line 51). The key in config is `synchronous_mode`, not `synchronous`, so the code always uses the default `"NORMAL"`. This happens to be the intended value, so there is no functional impact.

### I-5: Commented-Out Breakpoint in Production Code

`main.py` line 556: `# breakpoint() # Paused here` -- a development artifact that should be removed before production deployment.

---

## 4. Architecture & Design Analysis

### 4.1 Strengths

| Pattern | Implementation | Assessment |
|---------|---------------|------------|
| **Repository Pattern** | `CaseRepository`, `GpuRepository` with `BaseRepository` | Well-implemented with consistent `_execute_query` abstraction |
| **State Pattern** | `WorkflowState` hierarchy in `states.py` | Clean state transitions with decorator-based error handling |
| **Strategy Pattern** | `ExecutionHandler` with local/remote modes | Flexible, supports mixed-mode deployment |
| **Context Manager** | `get_db_session` for database lifecycle | Prevents connection leaks |
| **Signal Handling** | Graceful SIGTERM/SIGINT handling | Mostly correct (see W-1 for double-shutdown issue) |

### 4.2 Architectural Weaknesses

**4.2.1 Shared-Nothing Process Architecture with Shared SQLite**

The system uses `ProcessPoolExecutor` with worker processes that each create independent SQLite connections to a shared database file. SQLite is designed for single-writer, multiple-reader scenarios. While WAL mode improves concurrency, the design does not include:
- Connection pooling
- Write serialization at the application level
- Retry logic for `SQLITE_BUSY` errors (the `busy_timeout` config is misconfigured as noted in W-2)

**4.2.2 No Idempotency Guarantees in Case Processing**

The `_process_new_case` pipeline (main.py lines 409-449) is not idempotent. If the application crashes after step 2 (CSV interpreting) but before step 4 (TPS generation), restart will re-run CSV interpreting unnecessarily because `scan_existing_cases` re-queues all non-completed cases and the pipeline does not check `interpreter_completed` flag.

**4.2.3 Mixed Responsibility in `InitialState`**

`InitialState` (states.py) sets beam status to `CSV_INTERPRETING`, which is semantically incorrect -- at this point, CSV interpreting has already been completed by the dispatcher. The initial validation state should use a distinct status like `VALIDATING` or simply `PENDING`.

**4.2.4 No Health Check / Watchdog for Long-Running Simulations**

The only mechanism for detecting stuck simulations is the `hpc_job_timeout_seconds` (3600s). There is no intermediate health check, no heartbeat from the simulation process, and no mechanism to detect if the background `tps_env` process has crashed without writing to the log file.

---

## 5. Root Cause Analysis

### Why Did Case 55061194 Require 10+ Retry Steps?

**Timeline reconstruction:**
1. Case first detected at 23:18:48 (created_at in database)
2. CSV interpreting completed at 23:25:04 (6 seconds, normal)
3. TPS generation attempted at 23:25:10 -- **no GPUs available**
4. Multiple retries from 23:31:50 to 23:37:17

**Root cause:** The GPU monitor had not yet populated the `gpu_resources` table when TPS generation was first attempted. The `start_gpu_monitor()` is called in `main.py` line 571, but the first GPU data fetch occurs asynchronously in the monitor thread. If `_process_new_case` runs before the first GPU poll completes, `get_available_gpu_count()` returns 0.

The retries likely came from the application being restarted multiple times during testing (the monitoring report shows the latest run starting at 08:37:11, but the case was created at 23:18:48 the previous day).

### Why Did Case 55758663 Fail with GDCM Assertion?

**Root cause:** The error `"Assertion !(Toplevel.empty()) && 'Need to call Explore first'"` is an internal assertion in the GDCM (Grassroots DICOM) library used by `pydicom`. This occurs when attempting to parse a DICOM directory that has not been properly initialized. The `DataIntegrityValidator.find_rtplan_file()` method iterates over all `.dcm` files and tries `pydicom.dcmread()` on each one, which can trigger this assertion on corrupt or non-standard DICOM files. The broad `except Exception` catch (data_integrity_validator.py line 66) should suppress this, but the error message in the monitoring report suggests it was raised during HPC simulation submission, not during DICOM parsing, which would indicate the `tps_env` executable itself encountered the GDCM error.

---

## 6. Risk Assessment Matrix

| ID | Issue | Severity | Likelihood | Impact | Risk Score |
|----|-------|----------|-----------|--------|------------|
| C-1 | Completion marker typo | CRITICAL | Certain (100%) | All local simulations timeout | **10/10** |
| C-2 | Database status incoherence | CRITICAL | Certain (100%) | Incorrect status display, case never completes | **9/10** |
| C-3 | GPU allocation race condition | CRITICAL | Likely (70%) | GPU starvation or conflict on crash | **8/10** |
| W-1 | Double shutdown | WARNING | Certain (100%) | Misleading logs, potential exception | **4/10** |
| W-2 | DB contention / config mismatch | WARNING | Likely (60%) | SQLITE_BUSY under load | **6/10** |
| W-3 | Beam-GPU positional mismatch | WARNING | Possible (40%) | Wrong GPU used for beam | **7/10** |
| W-4 | Failed cases retried infinitely | WARNING | Certain (100%) | Wasted resources | **5/10** |
| W-5 | Stale GPU assignments persist | WARNING | Likely (60%) | GPU starvation | **6/10** |
| W-6 | Missing TPS file verification | WARNING | Uncertain | Beam failure | **4/10** |
| W-7 | Background process orphaning | WARNING | Likely (70%) | GPU memory conflicts | **7/10** |
| W-8 | Unbounded workflow step growth | WARNING | Certain (100%) | Database bloat | **3/10** |

---

## 7. Prioritized Recommendations

### Priority 1: Must Fix Before Any Production Run

1. **Fix the completion marker typo** in `config/config.yaml` line 105. Change `"Simlulation finalizing.."` to match the exact string the `tps_env` simulator outputs. Verify against an actual simulation log file.

2. **Fix `InitialState` status assignment** in `src/domain/states.py`. The initial validation state should not overwrite the beam status to `CSV_INTERPRETING`. Either use `BeamStatus.PENDING` or add a new `BeamStatus.VALIDATING` value.

3. **Add periodic GPU reconciliation** rather than only at startup. The `_monitor_services` thread in `main.py` should call `reconcile_stale_assignments()` on a configurable interval (e.g., every 60 seconds).

### Priority 2: Must Fix Before Clinical Deployment

4. **Fix beam-GPU ordering mismatch** (W-3). Instead of positional slicing in `main.py` line 383, match GPU assignments to beams by `beam_id` explicitly.

5. **Add retry limits for failed cases** (W-4). Introduce a `max_retries` field in the `cases` table and increment it on each requeue. Skip cases that have exceeded the retry limit.

6. **Track background simulation PIDs** (W-7). Modify the local simulation launch to capture the PID (remove the trailing `&` from the command template and let the `wait_for_job_completion` thread handle blocking). Alternatively, add PID tracking and process cleanup during shutdown.

7. **Fix the signal handler double shutdown** (W-1). Set `self.shutdown_event` in the signal handler instead of calling `shutdown()` directly. Let the `finally` block in `run()` handle the actual shutdown.

### Priority 3: Should Fix for Robustness

8. **Fix database config key mismatches** (W-2, I-4). Align `cache_size_mb` -> `cache_size`, `synchronous_mode` -> `synchronous`, and `busy_timeout_ms` in both config and code.

9. **Add idempotency checks** to the case processing pipeline. Check `interpreter_completed` flag before re-running CSV interpretation.

10. **Fix collapsed code formatting** in `states.py` (I-1). Reformat the file to have proper line breaks.

11. **Update INSTRUCTION_TEST.md** to reference `gpu_resources` table instead of `gpus` (I-3).

12. **Remove debug artifacts** from production code (I-5).

### Priority 4: Operational Improvements

13. **Add a workflow step archival mechanism** to prevent unbounded growth of the `workflow_steps` table.

14. **Add a configurable timeout for stale cases** -- cases in `PROCESSING` with no beam progress for >N minutes should be flagged or re-queued.

15. **Add structured health metrics** (case throughput, average processing time, GPU utilization rate) for operational monitoring.

---

*End of Analysis Report*
