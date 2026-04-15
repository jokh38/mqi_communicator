# Failure Visibility And Retry UI Design

## Goal

Persist structured failure metadata for cases, expose both case-level and beam-level failure reasons in the dashboard, and provide a safe manual retry path for retryable failed cases.

## Scope

This design covers the remaining items from the failed-item retry investigation that were not completed in the earlier retry-mechanism fix:

- structured failure metadata in the `cases` table
- retryable vs permanent failure categorization
- case-level failure visibility in list/detail/dashboard views
- beam-level failure visibility in detail/log views
- retry metadata display
- manual retry action from the web UI
- workflow failure summary counts

The previously implemented retry-path changes remain in place:

- retryable failed cases can re-enter via startup scan and filesystem events
- retry mutation is centralized in `_process_new_case()`
- `reset_case_and_beams_for_retry()` exists

## Design Overview

The implementation adds one durable case-failure model and routes all visibility and retry eligibility through it.

### 1. Structured failure model on `cases`

Add three nullable columns:

- `failure_category TEXT`
  - values: `retryable`, `permanent`, or `NULL`
- `failure_phase TEXT`
  - values: `beam_discovery`, `csv_interpreting`, `tps_generation`, `simulation`, `result_validation`, or `NULL`
- `failure_details TEXT`
  - JSON payload with structured context such as beam id, root cause, or raw exception text

`CaseData` will carry these fields directly, and repository reads will select them everywhere a case row is fetched.

### 2. Failure classification contract

Introduce two explicit failure types:

- `RetryableError`
- `PermanentFailureError`

The existing prefix-based retry marker is kept only as backward-compatible support for already-failed rows and older call paths. New failures should be persisted with structured metadata on the case row.

Classification rules:

- transient infrastructure or incomplete-arrival problems => `retryable`
- deterministic validation/data-quality failures => `permanent`

The classifier is centralized so the queue, retry button, and dashboard do not each invent their own interpretation.

### 3. Case failure persistence path

`CaseRepository.fail_case()` becomes the canonical API for case-level failure persistence. It will accept:

- `error_message`
- `failure_category`
- `failure_phase`
- `failure_details`
- optional `increment_retry`

This ensures:

- case failure summary is durable
- dashboard reads one source of truth
- retry metadata is available without log parsing

`reset_case_and_beams_for_retry()` will clear all failure metadata in addition to status/progress/error resets.

### 4. Dashboard visibility model

#### Case list (`cases.html`)

Add a visible error column with truncated case-level error text and badge-style retryability display for failed cases.

#### Case detail (`case_detail.html`)

Add a failure summary panel showing:

- case-level error message
- failure category
- failure phase
- retry count vs configured max
- failure details if present

Add a beam failure panel/table showing:

- beam id
- beam status
- beam error message

This does not replace the existing log modal; it complements it with direct failure diagnosis on the main detail page.

#### Workflow overview (`workflow.html`)

Add system summary values:

- retryable failed case count
- permanent failed case count
- failed cases grouped by `failure_phase`

### 5. Manual retry action

Add a POST endpoint in `src/web/app.py`:

- validates that the case exists
- validates `FAILED`
- validates retryable classification
- validates `retry_count < max_case_retries`
- resets case and beams
- increments retry count
- queues the case for processing

The retry button is shown only for eligible failed cases.

The retry endpoint must use the same eligibility helper as startup/worker paths so UI retry and automatic retry stay consistent.

## Data Flow

### Automatic failure

1. workflow/dispatcher raises retryable or permanent failure
2. failure is converted to structured case metadata
3. `cases` row stores category, phase, and details
4. dashboard reads and displays metadata
5. automatic retry logic consults structured metadata first

### Manual retry

1. operator opens case detail page
2. UI shows retry button only when eligible
3. POST retry endpoint validates eligibility
4. repository resets case and beams, clears failure metadata, increments retry count
5. case is requeued

## Compatibility Strategy

The production database already contains failed rows without structured metadata. The implementation therefore needs a compatibility layer:

- if structured metadata exists, use it
- otherwise fall back to legacy error-message heuristics for retryability and dashboard badges

This keeps current rows usable immediately while new failures become structured.

## Error Handling

- invalid `failure_details` JSON should not break reads; the raw string can still be displayed
- manual retry attempts on non-eligible cases should return a clear HTTP error
- DB migration must be additive only

## Testing Strategy

1. database migration tests for new columns
2. repository tests for failure metadata persistence and reset clearing
3. retry-policy tests for structured metadata precedence and legacy fallback
4. web/provider tests for case and beam failure display payloads
5. endpoint tests for retry button eligibility and retry POST behavior

## Non-Goals

- redesigning the entire workflow state machine
- making incomplete-transfer cases remain `PENDING` instead of `FAILED`
- solving upstream PlanInfo/data-quality defects
- full end-to-end production verification against the live server
