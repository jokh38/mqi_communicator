# Startup Stale Process Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reclaim stale local MQI processes from the same repository and config when a new `main.py` instance starts.

**Architecture:** Add a startup cleanup helper around `MQIApplication` that persists runtime metadata for the active main process, verifies whether a prior recorded process still belongs to this repository/config, terminates the matching process tree, and falls back to a guarded process scan when metadata is missing or stale. Invoke cleanup before watcher, dashboard, GPU monitor, or worker-pool startup, and unregister the runtime metadata on shutdown.

**Tech Stack:** Python, `subprocess`, `pathlib`, `json`, `pytest`, `unittest.mock`

---

### Task 1: Add failing startup-cleanup tests

**Files:**
- Modify: `tests/test_main_loop.py`
- Test: `tests/test_main_loop.py`

**Step 1: Write the failing test**

```python
def test_run_reclaims_previous_matching_process_before_startup():
    ...
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_main_loop.py::test_run_reclaims_previous_matching_process_before_startup -v`
Expected: FAIL because startup cleanup is not invoked.

**Step 3: Write the failing fallback-scan test**

```python
def test_run_cleanup_ignores_different_config_process():
    ...
```

**Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_main_loop.py -k "cleanup or reclaims_previous" -v`
Expected: FAIL because repo/config matching and cleanup do not exist yet.

### Task 2: Implement runtime metadata and guarded process matching

**Files:**
- Modify: `main.py`
- Create: `src/infrastructure/process_registry.py`
- Test: `tests/test_main_loop.py`

**Step 1: Add minimal runtime registry implementation**

```python
class ProcessRegistry:
    def reclaim_previous_instance(self, current_pid: int) -> None:
        ...
```

**Step 2: Persist metadata for current process**

Run-time data should include PID, repo root, resolved config path, command line, and app-recorded start time in `.runtime/main_process.json`.

**Step 3: Implement metadata validation and process-scan fallback**

Match only `main.py` processes from the same repo/config, reject unrelated candidates, and terminate matching process trees.

**Step 4: Run targeted tests**

Run: `python -m pytest tests/test_main_loop.py -k "cleanup or reclaims_previous" -v`
Expected: PASS

### Task 3: Wire startup cleanup into application lifecycle

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_loop.py`

**Step 1: Invoke cleanup at the start of `MQIApplication.run()`**

Cleanup must happen after logging is available but before database scan, watcher startup, dashboard launch, GPU monitor startup, or worker dispatch.

**Step 2: Register current process after cleanup succeeds**

Ensure the current process overwrites prior runtime metadata.

**Step 3: Unregister on shutdown**

Remove runtime metadata only if it still belongs to the current process.

**Step 4: Run lifecycle-focused tests**

Run: `python -m pytest tests/test_main_loop.py -v`
Expected: PASS

### Task 4: Verify broader component behavior

**Files:**
- Modify: `tests/test_ui_process_manager.py` (only if needed)
- Test: `tests/test_main_loop.py`
- Test: `tests/test_ui_process_manager.py`

**Step 1: Run targeted regression tests**

Run: `python -m pytest tests/test_main_loop.py tests/test_ui_process_manager.py -v`
Expected: PASS

**Step 2: Review logging and failure paths**

Confirm cleanup errors are logged and that mismatched repo/config candidates are ignored rather than killed.
