# Failure Visibility And Retry UI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist structured case failure metadata and expose case-level and beam-level failure diagnosis, retry state, workflow failure summaries, and a manual retry action in the dashboard.

**Architecture:** Extend the `cases` schema and `CaseData` DTO with structured failure fields, route failure persistence through repository APIs, and surface the new metadata through `DashboardDataProvider`, FastAPI handlers, and templates. Keep retry eligibility centralized by preferring structured metadata while retaining backward-compatible fallback for legacy failed rows.

**Tech Stack:** Python, SQLite, FastAPI, Jinja2, pytest

---

### Task 1: Add failing tests for structured failure metadata persistence

**Files:**
- Create: `tests/test_case_failure_metadata.py`
- Modify: `src/database/connection.py`
- Modify: `src/domain/models.py`
- Modify: `src/repositories/case_repo.py`
- Test: `tests/test_case_failure_metadata.py`

**Step 1: Write the failing test**

Add tests that assert:
- schema initialization creates or migrates `failure_category`, `failure_phase`, and `failure_details`
- `CaseData` mapping returns those fields
- `fail_case()` persists structured metadata
- `reset_case_and_beams_for_retry()` clears structured failure metadata

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_case_failure_metadata.py -q`
Expected: FAIL because the schema and mapping do not expose structured failure metadata yet.

**Step 3: Write minimal implementation**

Implement additive schema migration, DTO changes, repository field selection/mapping, and failure/reset persistence behavior.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_case_failure_metadata.py -q`
Expected: PASS

### Task 2: Add failing tests for structured retry classification

**Files:**
- Modify: `src/core/retry_policy.py`
- Modify: `src/domain/errors.py`
- Modify: `src/domain/states.py`
- Test: `tests/test_retry_policy.py`
- Modify: `tests/test_states.py`

**Step 1: Write the failing test**

Add tests that assert:
- structured `failure_category='retryable'` overrides legacy message heuristics
- structured `failure_category='permanent'` blocks retry
- `PermanentFailureError` is available
- state exception handling preserves retryable/permanent distinction in persisted failure data

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_retry_policy.py tests/test_states.py -q`
Expected: FAIL because structured category precedence and permanent failure classification do not exist yet.

**Step 3: Write minimal implementation**

Implement `PermanentFailureError`, update retry-policy helpers, and extend exception handling to preserve structured semantics while keeping backward compatibility.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_retry_policy.py tests/test_states.py -q`
Expected: PASS

### Task 3: Add failing tests for provider and list/detail dashboard visibility

**Files:**
- Modify: `src/ui/provider.py`
- Modify: `src/web/templates/cases.html`
- Modify: `src/web/templates/case_detail.html`
- Test: `tests/test_ui_provider.py`
- Create: `tests/test_web_failure_views.py`

**Step 1: Write the failing test**

Add tests that assert:
- processed case display data includes error text, failure category, failure phase, retry counts, and parsed failure details
- case list view renders a case-level error column
- case detail view renders case-level failure summary and beam-level error messages

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ui_provider.py tests/test_web_failure_views.py -q`
Expected: FAIL because provider payloads and templates do not expose the new failure fields yet.

**Step 3: Write minimal implementation**

Update provider processing and the list/detail templates to render the new case-level and beam-level failure information.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ui_provider.py tests/test_web_failure_views.py -q`
Expected: PASS

### Task 4: Add failing tests for workflow summary and retry endpoint

**Files:**
- Modify: `src/ui/provider.py`
- Modify: `src/web/app.py`
- Modify: `src/web/templates/workflow.html`
- Test: `tests/test_web_failure_views.py`
- Create: `tests/test_web_retry_endpoint.py`

**Step 1: Write the failing test**

Add tests that assert:
- workflow stats include retryable/permanent failure counts and phase summary
- case detail context exposes retry eligibility
- retry POST endpoint rejects ineligible cases
- retry POST endpoint resets, increments, and requeues eligible cases

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web_failure_views.py tests/test_web_retry_endpoint.py -q`
Expected: FAIL because the workflow summary and retry endpoint do not exist yet.

**Step 3: Write minimal implementation**

Implement provider summary metrics, the retry endpoint, and the template wiring for retry controls and failure summary blocks.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_web_failure_views.py tests/test_web_retry_endpoint.py -q`
Expected: PASS

### Task 5: Run focused regression verification for the full change

**Files:**
- Modify: `src/database/connection.py`
- Modify: `src/domain/models.py`
- Modify: `src/domain/errors.py`
- Modify: `src/domain/states.py`
- Modify: `src/repositories/case_repo.py`
- Modify: `src/core/retry_policy.py`
- Modify: `src/ui/provider.py`
- Modify: `src/web/app.py`
- Modify: `src/web/templates/cases.html`
- Modify: `src/web/templates/case_detail.html`
- Modify: `src/web/templates/workflow.html`

**Step 1: Run focused verification suite**

Run: `python -m pytest tests/test_case_failure_metadata.py tests/test_retry_policy.py tests/test_states.py tests/test_ui_provider.py tests/test_web_failure_views.py tests/test_web_retry_endpoint.py -q`
Expected: PASS

**Step 2: Run adjacent retry/dashboard regressions**

Run: `python -m pytest tests/test_workflow_manager.py tests/test_case_repo_retry.py tests/test_main_loop.py tests/test_dispatcher.py -q`
Expected: PASS

**Step 3: Review diff**

Run: `git diff -- src/database/connection.py src/domain/models.py src/domain/errors.py src/domain/states.py src/repositories/case_repo.py src/core/retry_policy.py src/ui/provider.py src/web/app.py src/web/templates/cases.html src/web/templates/case_detail.html src/web/templates/workflow.html tests/test_case_failure_metadata.py tests/test_retry_policy.py tests/test_states.py tests/test_ui_provider.py tests/test_web_failure_views.py tests/test_web_retry_endpoint.py`
Expected: Only failure-metadata, visibility, and retry-UI changes are present.
