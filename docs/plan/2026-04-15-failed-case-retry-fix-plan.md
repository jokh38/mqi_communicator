# Failed Case Retry Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow retryable failed cases to be automatically retried without retrying terminal or non-retryable failures.

**Architecture:** Add a shared retryability classifier plus an atomic repository reset path for failed cases. Queue eligibility remains in startup scan and filesystem handling, while `main.py` becomes the only place that resets failed case state, increments `retry_count`, and resumes processing.

**Tech Stack:** Python, pytest, SQLite repository layer, watchdog-based workflow manager

---

### Task 1: Add failing tests for retry classification and startup/file-watcher behavior

**Files:**
- Modify: `tests/test_workflow_manager.py`
- Test: `tests/test_workflow_manager.py`

**Step 1: Write the failing test**

Add targeted tests that assert:
- startup scan queues retryable `FAILED` cases without incrementing `retry_count`
- startup scan skips non-retryable `FAILED` cases
- file watcher ignores non-retryable failed cases
- file watcher allows retryable failed cases through

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_workflow_manager.py -q`
Expected: FAIL because current code treats all `FAILED` cases as terminal and increments retries in startup scan.

**Step 3: Write minimal implementation**

Add a shared helper for retryable failed case classification and update workflow-manager queue eligibility logic to use it.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_workflow_manager.py -q`
Expected: PASS

### Task 2: Add failing tests for case retry reset semantics in repository and main loop

**Files:**
- Modify: `tests/test_case_repo.py` or create `tests/test_case_repo_retry.py`
- Modify: `tests/test_main.py` or create `tests/test_main_retry.py`
- Test: `tests/test_case_repo_retry.py`
- Test: `tests/test_main_retry.py`

**Step 1: Write the failing test**

Add tests that assert:
- `reset_case_and_beams_for_retry(case_id)` resets case and beams to clean `PENDING` state and clears stale errors/progress
- `_process_new_case()` skips completed/cancelled/non-retryable failed cases
- `_process_new_case()` resets and increments once for retryable failed cases under the configured retry limit
- `_process_new_case()` skips retryable failed cases that already hit the limit

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_case_repo_retry.py tests/test_main_retry.py -q`
Expected: FAIL because reset helper and failed-case retry flow do not exist yet.

**Step 3: Write minimal implementation**

Implement the repository reset method and the centralized retry gate in `main.py`.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_case_repo_retry.py tests/test_main_retry.py -q`
Expected: PASS

### Task 3: Wire retryable failure signaling into runtime failure handling

**Files:**
- Modify: `src/domain/errors.py`
- Modify: `src/domain/states.py`
- Modify: `src/repositories/case_repo.py`
- Test: `tests/test_states.py`

**Step 1: Write the failing test**

Add a focused state-machine test asserting that `RetryableError` preserves retryable failure marking instead of collapsing into an undifferentiated permanent failure.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_states.py -q`
Expected: FAIL because runtime exception handling currently treats all exceptions as plain failed beams.

**Step 3: Write minimal implementation**

Extend retryable failure metadata handling so retryable failures are distinguishable via persisted error messages used by the short-term classifier.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_states.py -q`
Expected: PASS

### Task 4: Verify end-to-end component behavior of the retry fix

**Files:**
- Modify: `src/core/workflow_manager.py`
- Modify: `main.py`
- Modify: `src/repositories/case_repo.py`
- Test: `tests/test_workflow_manager.py`
- Test: `tests/test_case_repo_retry.py`
- Test: `tests/test_main_retry.py`
- Test: `tests/test_states.py`

**Step 1: Run the focused verification suite**

Run: `python -m pytest tests/test_workflow_manager.py tests/test_case_repo_retry.py tests/test_main_retry.py tests/test_states.py -q`
Expected: PASS

**Step 2: Run any adjacent regression tests that cover touched paths**

Run: `python -m pytest tests/test_case_aggregator.py tests/test_dispatcher.py -q`
Expected: PASS

**Step 3: Review the diff**

Run: `git diff -- src/core/workflow_manager.py main.py src/repositories/case_repo.py src/domain/errors.py src/domain/states.py tests/test_workflow_manager.py tests/test_case_repo_retry.py tests/test_main_retry.py tests/test_states.py`
Expected: Only retry-fix and test changes are present.
