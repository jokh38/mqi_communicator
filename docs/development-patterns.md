# Development Patterns and Conventions

This document describes the official design patterns and conventions used in the MQI Communicator codebase. These patterns have been verified by tests in `tests/test_pattern_verification.py`.

## Table of Contents
1. [Error Handling Pattern](#error-handling-pattern)
2. [Progress Tracking Pattern](#progress-tracking-pattern)
3. [Path Resolution Pattern](#path-resolution-pattern)
4. [State Transition Pattern](#state-transition-pattern)

---

## Error Handling Pattern

### Pattern Description
All state execution errors are caught by the `handle_state_exceptions` decorator and converted to a `FailedState` transition.

### Rationale
- **Fail-Safe Design**: Ensures that any unexpected error during workflow execution results in a controlled failure state rather than a crash
- **Consistent Error Handling**: Centralizes error handling logic in one decorator
- **Observability**: All errors are logged with beam context before transitioning to failed state

### Implementation
```python
@handle_state_exceptions
def execute(self, context: 'WorkflowManager') -> WorkflowState:
    # State implementation
    pass
```

### Behavior
- **On Exception**:
  - Logs error with beam ID, state name, and exception type
  - Updates beam status to `FAILED` in database
  - Returns `FailedState` instance
- **On Success**: Passes through the normal return value

### Verification
See `tests/test_pattern_verification.py::TestErrorHandlingPattern`

---

## Progress Tracking Pattern

### Pattern Description
Progress tracking updates are **defensive** - they silently ignore all errors and never interrupt the workflow.

### Rationale
- **Non-Critical Feature**: Progress tracking is a UX enhancement, not a core workflow requirement
- **Defensive Programming**: Config errors, missing settings, or type errors should not crash production workflows
- **Graceful Degradation**: If progress tracking fails, the workflow continues normally

### Implementation
```python
try:
    config = context.settings.get_progress_tracking_config()
    phase_progress = config.get("coarse_phase_progress", {})
    p = phase_progress.get(PHASE_UPLOADING)
    if p is not None:
        context.case_repo.update_beam_progress(context.id, float(p))
except Exception:
    pass  # Silently ignore all progress tracking errors
```

### Behavior
- **On Config Error**: Silently skips progress update
- **On Missing Phase**: Silently skips progress update (`p` is `None`)
- **On Type Error**: Silently skips progress update
- **Never Raises**: The `except Exception: pass` block catches everything

### Verification
See `tests/test_pattern_verification.py::TestProgressTrackingPattern`

---

## Path Resolution Pattern

### Pattern Description
All file and directory paths are resolved through `settings.get_path()` for consistency and configurability.

### Rationale
- **Centralized Configuration**: Path templates are defined in one place (`config.yaml`)
- **Environment Independence**: Paths can differ between local/remote modes
- **Variable Substitution**: Supports placeholders like `{case_id}`, `{beam_id}`, `{handler_name}`
- **Testability**: Easy to mock in unit tests

### Implementation
```python
# Good: Use settings.get_path()
csv_output_dir = context.settings.get_path(
    "csv_output_dir",
    handler_name="CsvInterpreter"
)

# Good: With variable substitution
remote_beam_path = context.settings.get_path(
    "remote_beam_path",
    handler_name="HpcJobSubmitter",
    case_id=beam.parent_case_id,
    beam_id=context.id
)

# Bad: Hardcoded paths
csv_output_dir = "/home/user/mqi/csv_output"  # DON'T DO THIS
```

### Behavior
- Reads path template from `config.yaml`
- Substitutes variables (e.g., `{case_id}`, `{beam_id}`)
- Returns resolved absolute path as string
- Raises `KeyError` if path key not found in config

### Verification
See `tests/test_pattern_verification.py::TestPathHandlingPattern`

---

## State Transition Pattern

### Pattern Description
The workflow follows a linear state machine with predictable transitions.

### Rationale
- **Predictability**: Easy to understand and debug the workflow
- **No Cycles**: Prevents infinite loops
- **Clear Failure Path**: Any state can transition to `FailedState`
- **Single Terminal State**: `CompletedState` is the only success terminal

### State Sequence
```
InitialState
    ↓
FileUploadState
    ↓
HpcExecutionState
    ↓
DownloadState
    ↓
PostprocessingState
    ↓
UploadResultToPCLocalDataState
    ↓
CompletedState (Terminal)
```

### Special Transitions
- **Any State → FailedState**: Via `handle_state_exceptions` decorator on error
- **Terminal States**: `CompletedState` and `FailedState` return `None`

### Implementation
Each state's `execute()` method returns the next state:
```python
def execute(self, context: 'WorkflowManager') -> WorkflowState:
    # Perform state work
    return NextState()  # or return None if terminal
```

### Verification
See `tests/test_pattern_verification.py::TestStateTransitionPattern`

---

## Future Pattern Evolution

As the codebase evolves, new patterns may be added to this document. When adding a new pattern:

1. **Write Verification Tests**: Add tests to `test_pattern_verification.py`
2. **Verify Current Code**: Ensure tests pass with existing implementation
3. **Document Here**: Add pattern description with rationale
4. **Enforce in Code Review**: New code should follow documented patterns

---

## Pattern Violations

If you find code that violates these patterns, it should be:
1. Considered a bug (unless there's a documented exception)
2. Fixed to conform to the pattern
3. Covered by a test to prevent regression

## References

- Pattern Verification Tests: `tests/test_pattern_verification.py`
- State Machine Implementation: `src/domain/states.py`
- Settings System: `src/config/settings.py`
- Constants: `src/config/constants.py`
