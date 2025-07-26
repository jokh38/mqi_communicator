# AI Code Assistant Instructions for Refactoring

This document outlines the plan to refactor the `mqi_communicator` application. Follow these instructions step-by-step.

## 1. Refactor `main_controller.py`

**Goal:** Decompose the monolithic `MainController` class into smaller, single-responsibility classes. Each new class should be under 300 lines of code.

**Analysis:** `main_controller.py` currently handles:
- Application lifecycle (startup, shutdown, locking).
- Background thread management.
- System monitoring and health checks.
- Case scanning and scheduling logic.
- Backup and recovery operations.
- The main application loop.

**Instructions:**

1.  **Create a new directory `controllers/`**.
2.  **Create `controllers/__init__.py`**.
3.  **Create `controllers/lifecycle_manager.py`:**
    - Move methods related to application startup, shutdown, and process locking from `MainController`.
    - Methods to move: `__init__` (partially), `_acquire_lock`, `_cleanup_lock_file`, `run`, `stop`, `shutdown`, `cleanup_resources`, `_cleanup_active_remote_processes`.
    - The new `LifecycleManager` class will contain the main application loop and orchestrate other controllers.
4.  **Create `controllers/scheduler.py`:**
    - Move methods related to case scanning, queuing, and scheduling.
    - Methods to move: `_scan_for_new_cases`, `_background_case_processor`, `_calculate_next_scan_time`, `_check_waiting_cases`, `_add_to_waiting_list`, `_remove_from_waiting_list`.
    - This class will manage the `scan_queue` and interact with `JobService`.
5.  **Create `controllers/system_monitor.py`:**
    - Move methods related to system health and status display updates.
    - Methods to move: `_monitor_system_health`, `_update_status_display`, `get_system_status`.
    - This class will run in a background thread.
6.  **Create `controllers/recovery_manager.py`:**
    - Move methods related to startup recovery and periodic backups.
    - Methods to move: `_recovery_startup_checks`, `_cleanup_stale_case_resources`, `_check_and_perform_monthly_backup`, `_perform_monthly_backup`, `_cleanup_old_backups`.
7.  **Refactor `main_controller.py`:**
    - Rename it to `main.py`.
    - This file should now only be the entry point.
    - It will initialize the `LifecycleManager` and other core components (like `ConfigManager`, `Logger`) and start the application.
    - The `MainController` class will be removed and its responsibilities distributed to the new controller classes.

## 2. Improve Configuration Management

**Goal:** Enhance the configuration system to support environment-specific settings and add validation.

**Instructions:**

1.  **Add `pydantic` to `requirements.txt`:**
    - Add the line `pydantic>=2.0.0` to the core dependencies section.
2.  **Create environment-specific config files:**
    - Create a `config/` directory.
    - Move `config.json` to `config/default.json`.
    - Create `config/development.json` and `config/production.json` as examples, possibly overriding some default values (e.g., logging level).
3.  **Refactor `core/config.py`:**
    - Create Pydantic models to define the structure of your configuration (e.g., `ServersConfig`, `PathsConfig`, `AppConfig`). This provides validation.
    - Modify `ConfigManager` to:
        - Read a `APP_ENV` environment variable (defaulting to `development`).
        - Load `config/default.json` first.
        - Load `config/{APP_ENV}.json` and merge it over the default settings.
        - Validate the final configuration against the Pydantic models upon loading.
        - Raise a `ValidationError` if the configuration is invalid.

## 3. Implement Test Coverage

**Goal:** Establish a testing foundation with `pytest` to ensure code stability and reliability.

**Instructions:**

1.  **Uncomment testing dependencies in `requirements.txt`:**
    - Uncomment `pytest`, `pytest-cov`, `pytest-mock`, and `coverage`.
2.  **Create test directory structure:**
    - Create a `tests/` directory at the project root.
    - Inside `tests/`, create `unit/` and `integration/` subdirectories.
    - Add `__init__.py` files to `tests/`, `tests/unit/`, and `tests/integration/`.
3.  **Write Unit Tests (Phase 1):**
    - **Target:** `core/config.py`.
    - **File:** `tests/unit/test_config.py`.
    - **Scenarios:**
        - Test loading default and environment-specific configurations.
        - Test that Pydantic validation catches invalid data (e.g., wrong type for a port).
        - Use `pytest.MonkeyPatch` to set the `APP_ENV` environment variable for tests.
        - Use mock files for configuration to avoid relying on the actual `config/` directory.
4.  **Write Unit Tests (Phase 2):**
    - **Target:** `utils.py` (if any pure functions exist) and `models/` classes.
    - **File:** `tests/unit/test_models.py`.
    - **Scenarios:**
        - Test the initialization and methods of `Case` and `Job` models.
5.  **Write Integration Tests (Phase 1):**
    - **Target:** Test the interaction between the new `Scheduler` and `JobService`.
    - **File:** `tests/integration/test_scheduling.py`.
    - **Scenarios:**
        - Use `pytest-mock` to mock external dependencies like `RemoteExecutor` and `ConnectionManager`.
        - Test that a new case is correctly identified, queued by the `Scheduler`, and a job is created by the `JobService`.
        - Test the GPU allocation logic by mocking the `ResourceManager`.
6.  **Add `pytest.ini` configuration:**
    - Create a `pytest.ini` file in the root directory.
    - Configure it to automatically find tests in the `tests/` directory and set options for `pytest-cov` to generate a coverage report.
    ```ini
    [pytest]
    testpaths = tests
    addopts = -ra -q --cov=. --cov-report=html
    ```
