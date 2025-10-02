## 1) DashboardLogger and StructuredLogger interplay

- Problem
  - `DashboardLogger` (src/ui/dashboard.py) subclasses `StructuredLogger` but does not call `super().__init__()`. It re-implements logger setup and uses `_create_json_formatter()` from the base. If `StructuredLogger`’s initializer performs essential setup, skipping it can cause subtle issues.
- Actions
  - Option A (recommended): Call `super().__init__(name, config)` first, then replace handlers to achieve file-only logging:
    - After `super().__init__`, remove console handler (if any) and re-attach only the rotating file handler with the JSON or plain formatter.
    - Keep behavior identical: file-only output, respect `structured_logging`, `tz_hours`, `max_file_size_mb`, and `backup_count`.
  - Option B: If intentionally bypassing base initialization, add explicit comments explaining why, and ensure all required attributes are set locally. Avoid relying on base internals unless guaranteed to be safe without initialization.
- Tests/Verification
  - Run UI startup path to confirm logs are written, no duplicate handlers, and no console output from `DashboardLogger`.

---

## 2) Settings: replace print with logging; early-boot safe

- Problem
  - `src/config/settings.py` uses `print()` for warnings/errors when the config file is missing or unreadable.
- Actions
  - Add optional logger injection to `Settings.__init__(..., logger: Optional[StructuredLogger] = None)` and store as `self._logger`.
  - Replace `print()` with `self._logger.warning/error` when a logger is provided; otherwise fall back to `print()` or a minimal stdlib `logging.getLogger(__name__)` configured with basicConfig. This avoids circular initialization at early startup.
  - Prefer structured messages with context (e.g., `{"config_path": str(config_path)}`) when using `StructuredLogger`.
- Tests/Verification
  - Unit test: simulate missing file and YAML parse error; assert warnings/errors logged (or captured via capsys if falling back to print).

---

## 3) Settings.get_path recursion depth and unresolved placeholders

- Problem
  - `get_path` limits recursive placeholder resolution to 5 iterations with no explicit error if placeholders remain.
- Actions
  - Make max depth configurable (e.g., top-level config key `settings: { path_resolution_max_depth: 8 }`) with default 5–10.
  - After the loop, detect unresolved `{placeholders}` via regex and raise a clear error listing remaining placeholders and the path name/mode for faster diagnosis.
- Tests/Verification
  - Add tests with deeply nested placeholders and with an intentionally unresolvable placeholder to confirm error clarity.

---

## 4) Hardcoded handler name ("CsvInterpreter")

- Problem
  - `get_database_path()` and `get_case_directories()` hardcode `handler_name="CsvInterpreter"`, tightly coupling configuration and code.
- Actions
  - Introduce `get_default_handler()` reading from YAML (e.g., `settings.default_handler`) or from `ExecutionHandler` with a documented rule. Alternatively, accept `handler_name` as a parameter and require callers to pass it.
  - Update methods to use provided/default handler rather than a fixed string.
- Tests/Verification
  - Add tests that set different default handler names and ensure paths resolve appropriately.

---

## 5) LoggerFactory lifecycle

- Problem
  - `LoggerFactory` requires explicit configuration (`LoggerFactory.configure(settings)`) before use.
- Actions
  - Ensure all entry points configure the factory as soon as `Settings` is instantiated:
    - `main.py` and any process bootstrap (e.g., UI process) should call `LoggerFactory.configure(settings)` before fetching any loggers.
  - Audit modules that call `LoggerFactory.get_logger()` to ensure they cannot be imported/used prior to configuration. If necessary, lazily obtain loggers at runtime instead of module import time.
- Tests/Verification
  - Integration tests that boot the app and UI to ensure no `RuntimeError` is raised.

---

## 6) Python typing compatibility

- Problem
  - `DashboardProcess.setup_database_components` uses `tuple[...]` return annotations, which require Python 3.9+.
- Actions
  - If supporting <3.9: switch to `from typing import Tuple` and annotate `-> Tuple[CaseRepository, GpuRepository]`.
  - Otherwise, clearly document Python 3.9+ in `INSTALLATION.md` and future `README.md`.
- Tests/Verification
  - Lint/type-check pass under targeted Python versions.

---

## 7) Test brittleness around command strings

- Problem
  - Some tests may assert specific command string contents, which can be brittle if templates change.
- Actions
  - Review `tests/test_dispatcher.py`. Prefer verifying behavior and argument components rather than exact full command strings. For example, parse arguments and assert key-value presence rather than string equality.
- Tests/Verification
  - Update tests accordingly; ensure they still fail on incorrect command construction but are resilient to benign template edits.

---

## 8) Tests referencing non-existent helpers

- Problem
  - A prior plan referenced a test for `get_pc_localdata_connection`; this is not confirmed here.
- Actions
  - Inspect `tests/test_settings.py`. If such a helper is referenced, either implement a thin wrapper around `get_connection_config()` or update the test to call `get_connection_config()` directly.
- Tests/Verification
  - Ensure tests pass with the chosen approach.

---

## 9) Configuration placeholders and production readiness

- Problem
  - Potential placeholder paths in `config/config.yaml` (e.g., `remote_case_path`, `remote_script_path`) may not be production-ready.
- Actions
  - Audit the YAML file and replace placeholders with working defaults or document environment-specific overrides. Provide a sample configuration snippet and reference environment variables if appropriate.
- Tests/Verification
  - Smoke test command/path resolution on both local and remote modes using the updated config.

---

## 10) Unused/redundant imports

- Problem
  - Likely unused imports across modules; not yet systematically cleaned.
- Actions
  - Add linter tooling (e.g., ruff/flake8) via dev dependencies and CI. Fix reported unused imports and minor stylistic issues.
- Tests/Verification
  - Linting passes; no functional changes introduced.

---

## 11) Documentation: add README

- Problem
  - No `README.md` present; only `INSTALLATION.md` and planning docs.
- Actions
  - Add a `README.md` including:
    - Project overview and architecture
    - Configuration overview (notably `ExecutionHandler` modes, logging config, default handler choice)
    - How to run (main app and UI), and how to configure `LoggerFactory`
    - Testing and linting instructions
    - Supported Python version(s)
- Tests/Verification
  - N/A. Improves onboarding and maintenance.

---

## Implementation order (suggested)

1. Settings improvements (logging injection, recursion handling, default handler).
2. LoggerFactory configuration at entry points.
3. DashboardLogger alignment with StructuredLogger (call super and adjust handlers).
4. Typing compatibility updates (or document Python version).
5. Update tests for Settings and command construction; remove brittleness.
6. Config audit for placeholders.
7. Add linter config and clean unused imports.
8. Add README.

---

## Risk notes

- Ensure early initialization paths do not create circular imports (Settings -> logging -> Settings). Keep Settings’ logging optional and late-bind when possible.
- When modifying DashboardLogger, verify no duplicate handlers and that console output is suppressed only for the UI logger.
- Be cautious changing handler names: provide backward-compatible defaults or migration notes.

---

## Follow-up tasks

- Consider adding structured error types around configuration resolution for clearer upstream handling.
- Add telemetry/logging around failed path/command resolutions to speed production diagnostics.
- Evaluate whether a central configuration schema (pydantic) would reduce runtime errors and improve editor tooling.
