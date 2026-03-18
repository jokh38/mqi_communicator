# Native DICOM Review Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the raw-to-DICOM pipeline and fix the review findings by making native MOQUI DICOM output the only result path, while also adding real remote HPC job monitoring, explicit beam metadata, hardened local command execution, and isolated SSH dependencies.

**Architecture:** The workflow is simplified to treat native DICOM output as the only simulation artifact. Beam metadata is persisted in the database and used consistently for TPS generation and result matching, remote HPC jobs are polled until completion before download begins, local commands are executed with argument lists instead of shell strings, and SSH-only imports are pushed out of locally reachable code paths.

**Tech Stack:** Python, sqlite3, pytest, unittest.mock, subprocess, pathlib, existing workflow/state/repository modules

---

## Preconditions

- Create or use an isolated git worktree before implementation.
- Confirm the current MOQUI native DICOM output layout and configured result path templates before touching workflow logic.
- Install dependencies needed for tests, especially `paramiko`, or intentionally verify lazy-import behavior in an environment where it is absent.
- Keep validation claims honest: `component validated` unless a real local/remote MOQUI run is executed.

### Task 1: Replace raw-pipeline tests with native DICOM regression tests

**Files:**
- Modify: `tests/test_execution_handler.py`
- Modify: `tests/test_states.py`
- Modify: `tests/test_dispatcher.py`
- Modify: `tests/test_case_repo_mapping.py`
- Create: `tests/test_worker.py`
- Test: `tests/test_execution_handler.py`
- Test: `tests/test_states.py`
- Test: `tests/test_dispatcher.py`
- Test: `tests/test_case_repo_mapping.py`
- Test: `tests/test_worker.py`

**Step 1: Remove obsolete `raw_to_dcm` test assumptions and add native DICOM result tests**

```python
def test_download_state_stores_final_dicom_path_for_native_output(...):
    beam.beam_number = 10
    manager.execution_handler.download_file.return_value = MagicMock(success=True)

    next_state = DownloadState().execute(manager)

    assert manager.shared_context["final_result_path"] == str(expected_dicom_dir)
    assert isinstance(next_state, UploadResultToPCLocalDataState)
```

**Step 2: Add a failing test proving there is no postprocessing conversion step**

```python
def test_download_state_transitions_directly_to_upload_state(...):
    next_state = DownloadState().execute(manager)
    assert not hasattr(manager.execution_handler, "run_raw_to_dcm")
    assert isinstance(next_state, UploadResultToPCLocalDataState)
```

**Step 3: Add failing remote job polling coverage**

```python
def test_wait_for_job_completion_remote_polls_scheduler_until_terminal_state(...):
    ...
    assert result.failed is False
```

```python
@pytest.mark.parametrize("scheduler_output", ["FAILED", "CANCELLED", "TIMEOUT"])
def test_wait_for_job_completion_remote_fails_for_unsuccessful_terminal_states(...):
    ...
    assert result.failed is True
```

**Step 4: Add failing tests for explicit beam ordering**

```python
def test_get_beams_for_case_orders_by_beam_number_before_beam_id(...):
    ...
    assert [beam.beam_number for beam in result] == [2, 10]
```

```python
def test_try_allocate_pending_beams_uses_persisted_beam_number(...):
    ...
    tps_generator.generate_tps_file_with_gpu_assignments.assert_called_with(..., beam_number=10)
```

**Step 5: Add failing tests for shell-free local execution and import-time SSH decoupling**

```python
@patch("subprocess.run")
def test_execute_command_local_mode_uses_argument_list_and_shell_false(mock_run):
    handler.execute_command(["ls", "-l"])
    mock_run.assert_called_once_with(["ls", "-l"], shell=False, check=True, ...)
```

```python
def test_execution_handler_imports_without_paramiko_installed(monkeypatch):
    ...
```

**Step 6: Run the regression targets**

Run:
```bash
python -m pytest tests/test_execution_handler.py tests/test_states.py tests/test_dispatcher.py tests/test_case_repo_mapping.py tests/test_worker.py -v
```

Expected: FAIL, with failures tied to missing native DICOM handling, missing beam-number support, stubbed remote polling, shell-based command execution, and eager SSH imports.

**Step 7: Commit the red tests**

```bash
git add tests/test_execution_handler.py tests/test_states.py tests/test_dispatcher.py tests/test_case_repo_mapping.py tests/test_worker.py
git commit -m "test: add native dicom review regressions"
```

### Task 2: Persist explicit beam metadata in the schema and repository layer

**Files:**
- Modify: `src/database/connection.py`
- Modify: `src/domain/models.py`
- Modify: `src/repositories/case_repo.py`
- Modify: `tests/test_case_repo_mapping.py`
- Test: `tests/test_case_repo_mapping.py`

**Step 1: Extend the `beams` schema with `beam_number`**

```python
conn.execute("""
    CREATE TABLE IF NOT EXISTS beams (
        ...,
        beam_number INTEGER,
        ...
    )
""")

cursor = conn.execute("PRAGMA table_info(beams)")
beam_columns = [column[1] for column in cursor.fetchall()]
if "beam_number" not in beam_columns:
    conn.execute("ALTER TABLE beams ADD COLUMN beam_number INTEGER")
```

**Step 2: Extend `BeamData` with `beam_number`**

```python
@dataclass
class BeamData:
    ...
    beam_number: Optional[int] = None
```

**Step 3: Map beam-number values and expose an update helper**

```python
def update_beam_number(self, beam_id: str, beam_number: int) -> None:
    query = "UPDATE beams SET beam_number = ?, updated_at = CURRENT_TIMESTAMP WHERE beam_id = ?"
    self._execute_query(query, (beam_number, beam_id))
```

**Step 4: Order beams by explicit beam metadata**

```python
query = """
    SELECT * FROM beams
    WHERE parent_case_id = ?
    ORDER BY
        CASE WHEN beam_number IS NULL THEN 1 ELSE 0 END,
        beam_number ASC,
        beam_id ASC
"""
```

**Step 5: Update atomic beam creation to accept optional `beam_number`**

```python
beam_records = [
    (
        job["beam_id"],
        case_id,
        str(job["beam_path"]),
        job.get("beam_number"),
        BeamStatus.PENDING.value,
        0.0,
    )
    for job in beam_jobs
]
```

**Step 6: Run repository-focused verification**

Run:
```bash
python -m pytest tests/test_case_repo_mapping.py -v
```

Expected: PASS

**Step 7: Commit the schema/repository work**

```bash
git add src/database/connection.py src/domain/models.py src/repositories/case_repo.py tests/test_case_repo_mapping.py
git commit -m "feat: persist explicit beam numbers"
```

### Task 3: Populate beam numbers during TPS preparation and use them everywhere

**Files:**
- Modify: `src/core/dispatcher.py`
- Modify: `src/core/worker.py`
- Modify: `src/core/tps_generator.py`
- Modify: `src/domain/states.py`
- Modify: `tests/test_dispatcher.py`
- Modify: `tests/test_states.py`
- Modify: `tests/test_worker.py`
- Test: `tests/test_dispatcher.py`
- Test: `tests/test_states.py`
- Test: `tests/test_worker.py`

**Step 1: Persist DICOM beam numbers during case-level TPS preparation**

```python
for beam, beam_number in zip(beams, treatment_beam_numbers):
    case_repo.update_beam_number(beam.beam_id, beam_number)
```

**Step 2: Require stored beam numbers for per-beam TPS generation**

```python
beam_number = beam.beam_number
if beam_number is None:
    raise ProcessingError(f"Beam number missing for {beam.beam_id}")
```

**Step 3: Update pending-beam allocation flow to use stored beam metadata**

```python
beam_data = next((b for b in beams if b.beam_id == beam_id), None)
beam_number = beam_data.beam_number
if beam_number is None:
    logger.error(...)
    all_success = False
    continue
```

**Step 4: Remove sorted-position beam-number derivation from download logic**

```python
beam_number = beam.beam_number
if beam_number is None:
    raise ProcessingError(f"Beam number missing for beam_id: {context.id}")
```

**Step 5: Run workflow beam-number verification**

Run:
```bash
python -m pytest tests/test_dispatcher.py tests/test_states.py tests/test_worker.py -v
```

Expected: PASS, proving beam/result matching no longer depends on lexicographic `beam_id` order.

**Step 6: Commit the beam-number propagation**

```bash
git add src/core/dispatcher.py src/core/worker.py src/core/tps_generator.py src/domain/states.py tests/test_dispatcher.py tests/test_states.py tests/test_worker.py
git commit -m "fix: use explicit beam metadata across workflow"
```

### Task 4: Remove `raw_to_dcm` and collapse the workflow to native DICOM handling

**Files:**
- Modify: `src/handlers/execution_handler.py`
- Modify: `src/domain/states.py`
- Modify: `tests/test_execution_handler.py`
- Modify: `tests/test_states.py`
- Modify: `README.md`
- Test: `tests/test_execution_handler.py`
- Test: `tests/test_states.py`

**Step 1: Delete `ExecutionHandler.run_raw_to_dcm()` and its tests**

```python
# Remove the method entirely and replace any callers with direct native DICOM handling.
```

**Step 2: Remove `PostprocessingState` and raw-path shared-context usage**

```python
context.shared_context["final_result_path"] = str(dicom_result_dir)
return UploadResultToPCLocalDataState()
```

**Step 3: Rewrite `DownloadState` around native DICOM outputs**

```python
dicom_result_dir = Path(
    context.settings.get_path(
        "simulation_output_dir",
        handler_name="PostProcessor",
        case_id=beam.parent_case_id,
        beam_number=beam.beam_number,
    )
)

if mode == "remote":
    remote_result_dir = context.settings.get_path(
        "remote_beam_result_path",
        handler_name="HpcJobSubmitter",
        case_id=beam.parent_case_id,
        beam_id=context.id,
        beam_number=beam.beam_number,
    )
    # Replace with directory-aware download helper if needed
```

**Step 4: If MOQUI emits multiple DICOM files per beam, add directory-aware transfer logic**

```python
def download_directory(self, remote_dir: str, local_dir: str) -> DownloadResult:
    ...
```

**Step 5: Make upload/finalization operate on final DICOM paths only**

```python
final_dir_path = Path(context.shared_context["final_result_path"])
result = context.execution_handler.upload_to_pc_localdata(
    local_path=final_dir_path,
    case_id=beam.parent_case_id,
    settings=context.settings,
)
```

**Step 6: Run native-DICOM workflow tests**

Run:
```bash
python -m pytest tests/test_execution_handler.py tests/test_states.py -v
```

Expected: PASS, with no references to `.raw`, `raw_to_dcm`, or conversion-based postprocessing.

**Step 7: Commit the raw-pipeline removal**

```bash
git add src/handlers/execution_handler.py src/domain/states.py tests/test_execution_handler.py tests/test_states.py README.md
git commit -m "refactor: remove raw conversion pipeline"
```

### Task 5: Implement real remote HPC job monitoring

**Files:**
- Modify: `src/handlers/execution_handler.py`
- Modify: `src/domain/states.py`
- Modify: `tests/test_execution_handler.py`
- Modify: `tests/test_states.py`
- Test: `tests/test_execution_handler.py`
- Test: `tests/test_states.py`

**Step 1: Add a scheduler-status helper**

```python
def _get_remote_job_status(self, job_id: str) -> tuple[bool, str]:
    command = self.settings.get_command(
        "remote_check_job_status",
        handler_name="HpcJobSubmitter",
        job_id=job_id,
    )
    result = self.execute_command(command)
    return result.success, (result.output or "").strip()
```

**Step 2: Implement remote polling in `wait_for_job_completion()`**

```python
if self.mode == "remote" and job_id:
    start_time = time.time()
    while time.time() - start_time < timeout:
        ok, scheduler_output = self._get_remote_job_status(job_id)
        if not ok:
            return ExecutionHandler.JobWaitResult(True, "Failed to query remote job status")
        normalized = scheduler_output.upper()
        if normalized == "":
            return ExecutionHandler.JobWaitResult(failed=False)
        if any(state in normalized for state in ["COMPLETED", "COMPLETING"]):
            return ExecutionHandler.JobWaitResult(failed=False)
        if any(state in normalized for state in ["FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL"]):
            return ExecutionHandler.JobWaitResult(True, f"Remote job {job_id} ended unsuccessfully: {scheduler_output}")
        time.sleep(poll_interval)
```

**Step 3: Persist job IDs after submission**

```python
context.case_repo.assign_hpc_job_id_to_beam(context.id, submission.job_id)
```

**Step 4: Run remote-monitoring verification**

Run:
```bash
python -m pytest tests/test_execution_handler.py tests/test_states.py -v
```

Expected: PASS

**Step 5: Commit the HPC monitoring fix**

```bash
git add src/handlers/execution_handler.py src/domain/states.py tests/test_execution_handler.py tests/test_states.py
git commit -m "fix: poll remote jobs before result handling"
```

### Task 6: Remove shell-string reliance from local command execution

**Files:**
- Modify: `src/handlers/execution_handler.py`
- Modify: `src/core/dispatcher.py`
- Modify: `tests/test_execution_handler.py`
- Modify: `tests/test_dispatcher.py`
- Test: `tests/test_execution_handler.py`
- Test: `tests/test_dispatcher.py`

**Step 1: Add a local command normalization helper**

```python
from shlex import split as shlex_split

def _normalize_local_command(self, command: str | list[str]) -> list[str]:
    if isinstance(command, list):
        return command
    return shlex_split(command)
```

**Step 2: Execute local commands with `shell=False`**

```python
argv = self._normalize_local_command(command)
result = subprocess.run(
    argv,
    shell=False,
    check=True,
    capture_output=True,
    text=True,
    cwd=cwd,
)
```

**Step 3: Refactor dispatcher CSV interpreter execution to use explicit cwd and argv**

```python
command = [
    str(python_exe),
    str(mqi_script),
    "--logdir", str(case_path),
    "--outputdir", csv_output_dir,
]
result = execution_handler.execute_command(command, cwd=Path(mqi_interpreter_dir))
```

**Step 4: Run shell-hardening verification**

Run:
```bash
python -m pytest tests/test_execution_handler.py tests/test_dispatcher.py -v
```

Expected: PASS

**Step 5: Commit the command-execution hardening**

```bash
git add src/handlers/execution_handler.py src/core/dispatcher.py tests/test_execution_handler.py tests/test_dispatcher.py
git commit -m "fix: harden local command execution"
```

### Task 7: Reduce import-time coupling to SSH-only dependencies

**Files:**
- Modify: `src/handlers/execution_handler.py`
- Modify: `src/core/dispatcher.py`
- Modify: `src/core/worker.py`
- Modify: `src/utils/ssh_helper.py`
- Modify: `tests/test_execution_handler.py`
- Modify: `tests/test_dispatcher.py`
- Test: `tests/test_execution_handler.py`
- Test: `tests/test_dispatcher.py`

**Step 1: Replace eager `paramiko` imports in locally reachable modules**

```python
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import paramiko
```

**Step 2: Move runtime imports into SSH-only branches**

```python
def create_ssh_client(...):
    import paramiko
    ...
```

**Step 3: Use string annotations for SSH-specific types**

```python
ssh_client: Optional["paramiko.SSHClient"] = None
self._sftp_client: Optional["paramiko.SFTPClient"] = None
```

**Step 4: Run import-decoupling verification**

Run:
```bash
python -m pytest tests/test_execution_handler.py tests/test_dispatcher.py -v
```

Expected: PASS, including local-mode imports without collection-time SSH dependency failures.

**Step 5: Commit the SSH isolation**

```bash
git add src/handlers/execution_handler.py src/core/dispatcher.py src/core/worker.py src/utils/ssh_helper.py tests/test_execution_handler.py tests/test_dispatcher.py
git commit -m "fix: isolate ssh dependencies from local paths"
```

### Task 8: Run consolidated verification and document remaining gaps

**Files:**
- Modify: `README.md`
- Modify: `20260317_improve_plan.md`
- Test: `tests/test_execution_handler.py`
- Test: `tests/test_states.py`
- Test: `tests/test_dispatcher.py`
- Test: `tests/test_case_repo_mapping.py`
- Test: `tests/test_worker.py`

**Step 1: Update docs to describe native DICOM-only behavior**

```markdown
- Native MOQUI DICOM output is the only supported result format.
- The raw-to-DICOM postprocessing pipeline has been removed.
- Beam/result matching depends on persisted DICOM beam numbers.
- Remote HPC jobs are polled before result handling begins.
```

**Step 2: Run the focused component suite**

Run:
```bash
python -m pytest tests/test_execution_handler.py tests/test_states.py tests/test_dispatcher.py tests/test_case_repo_mapping.py tests/test_worker.py -v
```

Expected: PASS

**Step 3: Run the broader suite if dependencies are available**

Run:
```bash
python -m pytest -v
```

Expected: PASS if runtime dependencies are installed; otherwise document the exact blocker.

**Step 4: Record validation scope explicitly**

```markdown
Validation status: `component validated`

Exact verification commands:
- `python -m pytest tests/test_execution_handler.py tests/test_states.py tests/test_dispatcher.py tests/test_case_repo_mapping.py tests/test_worker.py -v`
- `python -m pytest -v`

Known gaps:
- No end-to-end MOQUI native-DICOM run was executed.
- Remote scheduler command semantics still need confirmation in the target HPC environment.
- Native DICOM result layout must match the configured local/remote path templates.
```

**Step 5: Commit docs and verification updates**

```bash
git add README.md 20260317_improve_plan.md
git commit -m "docs: record native dicom workflow and validation scope"
```
