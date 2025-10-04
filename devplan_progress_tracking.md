# Development Plan: Real-time MC Simulation Progress Tracking (Revised)

## 1. Introduction

This document outlines the development plan for enhancing the MQI Communicator to provide real-time progress updates for Monte Carlo (MC) simulations. The current system only determines the final status (e.g., success or failure) after the simulation process has fully completed.

By parsing real-time log output from the tps_env executable, we can extract key information to calculate and display the simulation's progress, significantly improving user experience.

**Key Revisions from Original Plan:**
- Focuses on beam-level progress tracking (not case-level, which already exists)
- Handles both local and remote execution modes properly
- Uses log file tailing instead of just stdout streaming
- Aligns with existing state machine architecture
- Addresses database schema requirements

---

## 2. Current System Analysis

### 2.1. Completion Detection

**File:** `config/config.yaml:104-109`

The existing mechanism for tracking simulation completion is based on post-mortem log analysis:

```yaml
completion_markers:
  success_pattern: "Simulation completed successfully"
  failure_patterns:
    - "FATAL ERROR"
    - "ERROR:"
    - "Segmentation fault"
```

**Limitation:** This approach provides no visibility into the simulation's status while it is running. The system can only report a binary state after completion.

### 2.2. Execution Modes

**File:** `src/domain/states.py:211-243` (HpcExecutionState)

The system supports two execution modes:
- **Local Mode:** Direct execution via subprocess (line 231-243)
- **Remote Mode:** Job submission via HPC scheduler (line 213-229)

**Current Behavior:**
- Local: Executes `tps_env` directly, blocks until completion
- Remote: Submits job, polls for completion status

Both modes redirect output to log files at `{base_directory}/tps_env/logs/{case_id}_{beam_id}.log`

### 2.3. Database Schema

**File:** `src/database/connection.py:128-140`

Current `beams` table schema:
```sql
CREATE TABLE IF NOT EXISTS beams (
    beam_id TEXT PRIMARY KEY,
    parent_case_id TEXT NOT NULL,
    beam_path TEXT NOT NULL,
    status TEXT NOT NULL,
    hpc_job_id TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_case_id) REFERENCES cases (case_id)
)
```

**Missing:** No `progress` column exists for per-beam progress tracking.

### 2.4. UI Display

**File:** `src/ui/display.py:245-315`

The UI already displays:
- Case-level progress bars (line 291)
- Beam status indicators (line 299-313)
- No progress bars for individual beams (line 308: empty string)

---

## 3. Proposed Solution: Real-time Log Parsing

### 3.1. Critical Log Patterns (To Be Verified)

Analysis of expected simulation log format reveals two key patterns:

**Total Workload Identification:**
```
Uploading particles with batch.. : Particle count --> 10000000 with 18 batches
```
- Regex: `r"with (\d+) batches"`
- Extracts: Total Batches (Y)

**Current Progress Identification:**
```
Generating particles for (1 of 18 batches) in CPU ..
...
Generating particles for (2 of 18 batches) in CPU ..
```
- Regex: `r"Generating particles for \((\d+) of \d+ batches\)"`
- Extracts: Current Batch (X)

**⚠️ IMPORTANT:** These patterns must be verified against actual `tps_env` output before implementation.

### 3.2. Progress Calculation

```python
Progress (%) = (Current Batch / Total Batches) * 100
```

---

## 4. Implementation Plan

### Phase 1: Database Schema Update

**File:** `src/database/connection.py`

**Action:** Add `progress` column to `beams` table

```python
# In init_db() method, add to beam table creation (line 128):
conn.execute("""
    CREATE TABLE IF NOT EXISTS beams (
        beam_id TEXT PRIMARY KEY,
        parent_case_id TEXT NOT NULL,
        beam_path TEXT NOT NULL,
        status TEXT NOT NULL,
        progress REAL DEFAULT 0.0,        -- NEW COLUMN
        hpc_job_id TEXT,
        error_message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (parent_case_id) REFERENCES cases (case_id)
    )
""")

# Add migration logic for existing databases (after line 197):
cursor = conn.execute("PRAGMA table_info(beams)")
beam_columns = [column[1] for column in cursor.fetchall()]
if 'progress' not in beam_columns:
    self.logger.info("Adding progress column to beams table")
    conn.execute("ALTER TABLE beams ADD COLUMN progress REAL DEFAULT 0.0")
```

**Testing:**
- Verify column is added to new databases
- Verify migration works for existing databases
- Confirm default value is 0.0

---

### Phase 2: Repository Method Addition

**File:** `src/repositories/case_repo.py`

**Action:** Add method to update beam progress

```python
def update_beam_progress(self, beam_id: str, progress: float) -> None:
    """Updates the progress of a beam.

    Args:
        beam_id (str): The beam identifier.
        progress (float): Progress percentage (0-100).
    """
    self._log_operation("update_beam_progress", beam_id=beam_id, progress=progress)

    # Clamp progress to valid range
    progress = max(0.0, min(100.0, progress))

    query = """
        UPDATE beams
        SET progress = ?, updated_at = CURRENT_TIMESTAMP
        WHERE beam_id = ?
    """
    self._execute_query(query, (progress, beam_id))

    self.logger.debug(
        "Beam progress updated",
        {"beam_id": beam_id, "progress": progress}
    )
```

**Testing:**
- Unit test for valid progress values (0-100)
- Test clamping for out-of-range values
- Verify updated_at timestamp changes

---

### Phase 3: Log Parser Implementation

**New File:** `src/handlers/log_parser.py`

Create a dedicated log parser for TPS output:

```python
"""Parser for tps_env simulation log output."""

import re
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class SimulationProgress:
    """Container for simulation progress data."""
    current_batch: int
    total_batches: int
    progress_percent: float


class TpsLogParser:
    """Parses tps_env log output to extract progress information."""

    # Regex patterns for log parsing
    TOTAL_BATCHES_PATTERN = re.compile(r"with (\d+) batches")
    CURRENT_BATCH_PATTERN = re.compile(r"Generating particles for \((\d+) of (\d+) batches\)")

    def __init__(self):
        self.total_batches: Optional[int] = None
        self.current_batch: int = 0

    def parse_line(self, line: str) -> Optional[SimulationProgress]:
        """Parse a single log line and return progress if found.

        Args:
            line: A single line from the log file

        Returns:
            SimulationProgress if progress information found, None otherwise
        """
        # First, try to extract total batches if we don't have it yet
        if self.total_batches is None:
            match = self.TOTAL_BATCHES_PATTERN.search(line)
            if match:
                self.total_batches = int(match.group(1))

        # Then, try to extract current batch progress
        if self.total_batches is not None:
            match = self.CURRENT_BATCH_PATTERN.search(line)
            if match:
                self.current_batch = int(match.group(1))
                total_from_line = int(match.group(2))

                # Verify consistency
                if total_from_line != self.total_batches:
                    # Log warning but use original total_batches
                    pass

                progress_percent = (self.current_batch / self.total_batches) * 100

                return SimulationProgress(
                    current_batch=self.current_batch,
                    total_batches=self.total_batches,
                    progress_percent=progress_percent
                )

        return None

    def reset(self):
        """Reset parser state for a new simulation."""
        self.total_batches = None
        self.current_batch = 0
```

**Testing:**
- Unit tests with sample log lines
- Test edge cases (missing patterns, malformed logs)
- Verify state management across multiple lines

---

### Phase 4: Log Tailer Implementation

**New File:** `src/handlers/log_tailer.py`

Create a log file tailer that works for both local and remote modes:

```python
"""Utilities for tailing log files in both local and remote modes."""

import time
from pathlib import Path
from typing import Iterator, Optional
import paramiko

from src.infrastructure.logging_handler import StructuredLogger


class LocalLogTailer:
    """Tails a local log file, yielding new lines as they appear."""

    def __init__(self, log_path: Path, logger: StructuredLogger, poll_interval: float = 0.5):
        self.log_path = log_path
        self.logger = logger
        self.poll_interval = poll_interval

    def tail(self, timeout: Optional[float] = None) -> Iterator[str]:
        """Tail the log file, yielding new lines.

        Args:
            timeout: Maximum time to wait for new content (None = wait forever)

        Yields:
            New lines from the log file
        """
        start_time = time.time()

        # Wait for log file to be created
        while not self.log_path.exists():
            if timeout and (time.time() - start_time) > timeout:
                self.logger.warning("Log file not created within timeout",
                                   {"path": str(self.log_path)})
                return
            time.sleep(self.poll_interval)

        with open(self.log_path, 'r') as f:
            # Start from beginning
            while True:
                line = f.readline()
                if line:
                    yield line.rstrip('\n')
                else:
                    # Check for timeout
                    if timeout and (time.time() - start_time) > timeout:
                        break
                    time.sleep(self.poll_interval)


class RemoteLogTailer:
    """Tails a remote log file via SSH."""

    def __init__(self, ssh_client: paramiko.SSHClient,
                 remote_log_path: str,
                 logger: StructuredLogger,
                 poll_interval: float = 1.0):
        self.ssh_client = ssh_client
        self.remote_log_path = remote_log_path
        self.logger = logger
        self.poll_interval = poll_interval

    def tail(self, timeout: Optional[float] = None) -> Iterator[str]:
        """Tail the remote log file via SSH.

        Args:
            timeout: Maximum time to wait for completion

        Yields:
            New lines from the remote log file
        """
        # Use tail -f with timeout
        tail_cmd = f"tail -f -n +1 {self.remote_log_path}"

        stdin, stdout, stderr = self.ssh_client.exec_command(tail_cmd, get_pty=True)

        start_time = time.time()

        try:
            while True:
                # Check for timeout
                if timeout and (time.time() - start_time) > timeout:
                    break

                # Read line with timeout
                if stdout.channel.recv_ready():
                    line = stdout.readline()
                    if line:
                        yield line.rstrip('\n')
                else:
                    time.sleep(self.poll_interval)

                # Check if command finished
                if stdout.channel.exit_status_ready():
                    # Read remaining lines
                    for line in stdout:
                        yield line.rstrip('\n')
                    break
        finally:
            # Clean up: send SIGTERM to tail process
            try:
                stdin.write('\x03')  # Ctrl+C
                stdin.flush()
            except:
                pass
```

**Testing:**
- Test local tailing with mock log files
- Test remote tailing with mock SSH client
- Verify timeout handling
- Test graceful shutdown

---

### Phase 5: Progress Monitor Integration

**New File:** `src/handlers/progress_monitor.py`

Create a progress monitor that combines log tailing and parsing:

```python
"""Progress monitoring for TPS simulations."""

import threading
from pathlib import Path
from typing import Optional, Callable
import paramiko

from src.handlers.log_parser import TpsLogParser
from src.handlers.log_tailer import LocalLogTailer, RemoteLogTailer
from src.infrastructure.logging_handler import StructuredLogger


class ProgressMonitor:
    """Monitors TPS simulation progress by tailing and parsing log files."""

    def __init__(self, beam_id: str, logger: StructuredLogger):
        self.beam_id = beam_id
        self.logger = logger
        self.parser = TpsLogParser()
        self._stop_event = threading.Event()

    def monitor_local(self,
                     log_path: Path,
                     progress_callback: Callable[[str, float], None],
                     timeout: Optional[float] = None):
        """Monitor a local log file for progress updates.

        Args:
            log_path: Path to local log file
            progress_callback: Callback(beam_id, progress_percent)
            timeout: Maximum monitoring time
        """
        tailer = LocalLogTailer(log_path, self.logger)

        try:
            for line in tailer.tail(timeout=timeout):
                if self._stop_event.is_set():
                    break

                progress = self.parser.parse_line(line)
                if progress:
                    progress_callback(self.beam_id, progress.progress_percent)
        except Exception as e:
            self.logger.error("Error monitoring local log",
                            {"beam_id": self.beam_id, "error": str(e)})

    def monitor_remote(self,
                      ssh_client: paramiko.SSHClient,
                      remote_log_path: str,
                      progress_callback: Callable[[str, float], None],
                      timeout: Optional[float] = None):
        """Monitor a remote log file for progress updates.

        Args:
            ssh_client: SSH client connection
            remote_log_path: Path to remote log file
            progress_callback: Callback(beam_id, progress_percent)
            timeout: Maximum monitoring time
        """
        tailer = RemoteLogTailer(ssh_client, remote_log_path, self.logger)

        try:
            for line in tailer.tail(timeout=timeout):
                if self._stop_event.is_set():
                    break

                progress = self.parser.parse_line(line)
                if progress:
                    progress_callback(self.beam_id, progress.progress_percent)
        except Exception as e:
            self.logger.error("Error monitoring remote log",
                            {"beam_id": self.beam_id, "error": str(e)})

    def stop(self):
        """Stop monitoring."""
        self._stop_event.set()
```

**Testing:**
- Integration tests with actual log files
- Test callback invocation
- Test thread safety
- Verify clean shutdown

---

### Phase 6: State Machine Integration

**File:** `src/domain/states.py`

**Action:** Modify `HpcExecutionState` to integrate progress monitoring

```python
class HpcExecutionState(WorkflowState):
    """HPC execution state: runs simulation and monitors progress."""

    def __init__(self, execution_handler: Optional[ExecutionHandler] = None):
        self._injected_handler = execution_handler
        self._progress_monitor: Optional[ProgressMonitor] = None

    @handle_state_exceptions
    def execute(self, context: 'WorkflowManager') -> WorkflowState:
        """Submits a MOQUI simulation and monitors progress."""
        context.logger.info("Starting HPC simulation for beam",
                            {"beam_id": context.id})
        handler = self._injected_handler or context.execution_handler

        # Get beam info and paths
        beam = context.case_repo.get_beam(context.id)
        if not beam:
            raise ProcessingError(f"Could not retrieve beam data for beam_id: {context.id}")

        handler_name = "HpcJobSubmitter"
        mode = context.settings.get_handler_mode(handler_name)

        # Get paths for command and log
        tps_input_file = context.settings.get_path(
            "tps_input_file",
            handler_name=handler_name,
            case_id=beam.parent_case_id,
            beam_id=context.id
        )
        mqi_run_dir = context.settings.get_path("mqi_run_dir", handler_name=handler_name)
        remote_log_path = context.settings.get_path(
            "remote_log_path",
            handler_name=handler_name,
            case_id=beam.parent_case_id,
            beam_id=context.id
        )

        # Create progress callback
        def update_progress(beam_id: str, progress: float):
            """Callback to update beam progress in database."""
            try:
                context.case_repo.update_beam_progress(beam_id, progress)
            except Exception as e:
                context.logger.warning("Failed to update beam progress",
                                     {"beam_id": beam_id, "error": str(e)})

        # Initialize progress monitor
        from src.handlers.progress_monitor import ProgressMonitor
        self._progress_monitor = ProgressMonitor(context.id, context.logger)

        # Start monitoring in background thread
        monitor_thread = None

        try:
            if mode == "remote":
                # Remote mode: submit job and monitor remote log
                submission = handler.submit_simulation_job(
                    handler_name=handler_name,
                    command_key="remote_submit_simulation",
                    tps_input_file=tps_input_file,
                    mqi_run_dir=mqi_run_dir,
                    remote_log_path=remote_log_path,
                    case_id=beam.parent_case_id,
                    beam_id=context.id
                )
                if not getattr(submission, "success", False):
                    raise ProcessingError(f"Failed to submit HPC simulation: {getattr(submission, 'error', 'unknown error')}")

                # Start monitoring remote log
                import threading
                monitor_thread = threading.Thread(
                    target=self._progress_monitor.monitor_remote,
                    args=(handler._ssh_client, remote_log_path, update_progress),
                    kwargs={"timeout": context.settings.get_processing_config().get("hpc_job_timeout_seconds", 3600)},
                    daemon=True
                )
                monitor_thread.start()

                # Wait for job completion
                wait_res = handler.wait_for_job_completion(getattr(submission, "job_id", None))
                if getattr(wait_res, "failed", False):
                    raise ProcessingError(getattr(wait_res, "error", "HPC job failed"))

            else:
                # Local mode: execute directly and monitor local log
                from pathlib import Path
                local_log_path = Path(remote_log_path)  # Uses same path template

                # Start monitoring in background
                import threading
                monitor_thread = threading.Thread(
                    target=self._progress_monitor.monitor_local,
                    args=(local_log_path, update_progress),
                    kwargs={"timeout": context.settings.get_processing_config().get("hpc_job_timeout_seconds", 3600)},
                    daemon=True
                )
                monitor_thread.start()

                # Execute simulation (blocks until complete)
                command = context.settings.get_command(
                    "remote_submit_simulation",
                    handler_name=handler_name,
                    tps_input_file=tps_input_file,
                    mqi_run_dir=mqi_run_dir,
                    remote_log_path=remote_log_path,
                    case_id=beam.parent_case_id,
                    beam_id=context.id
                )
                result = handler.execute_command(command)
                if not result.success:
                    raise ProcessingError(f"Failed to execute simulation: {result.error}")

            # Ensure monitor thread finishes
            if monitor_thread:
                monitor_thread.join(timeout=5.0)

            context.logger.info("HPC simulation completed successfully",
                                {"beam_id": context.id})
            return DownloadState()

        finally:
            # Clean up monitor
            if self._progress_monitor:
                self._progress_monitor.stop()

    def get_state_name(self) -> str:
        return "HPC Execution"
```

**Testing:**
- Unit tests with mocked progress monitor
- Integration tests with actual simulations
- Test both local and remote modes
- Verify thread cleanup

---

### Phase 7: UI Update for Beam Progress

**File:** `src/ui/provider.py`

**Action:** Add progress field to beam display data

```python
def _process_cases_with_beams_data(self, raw_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Processes raw case+beam data into display format."""
    processed = []
    for item in raw_data:
        case = item["case_data"]
        beams = item["beams"]

        # Process beam data
        beam_display_data = []
        for beam in beams:
            beam_display_data.append({
                "beam_id": beam["beam_id"],
                "status": beam["status"],
                "progress": beam.get("progress", 0.0),  # NEW FIELD
                "elapsed_time": (
                    (datetime.now() - beam["created_at"]).total_seconds()
                    if beam["created_at"] else 0
                ),
                "hpc_job_id": beam["hpc_job_id"],
                "error_message": beam.get("error_message")
            })

        # ... rest of method unchanged
```

**File:** `src/repositories/case_repo.py`

**Action:** Update `get_all_active_cases_with_beams` to include progress

```python
# In get_all_active_cases_with_beams method, update beam query (line 556):
beam_query = """
    SELECT beam_id, status, progress, created_at, updated_at, hpc_job_id, error_message
    FROM beams
    WHERE parent_case_id = ?
    ORDER BY beam_id ASC
"""

# Update beam data dict (line 566):
beams.append({
    "beam_id": beam_row["beam_id"],
    "status": BeamStatus(beam_row["status"]),
    "progress": beam_row["progress"],  # NEW FIELD
    "created_at": datetime.fromisoformat(beam_row["created_at"]),
    "updated_at": (
        datetime.fromisoformat(beam_row["updated_at"])
        if beam_row["updated_at"] else None
    ),
    "hpc_job_id": beam_row["hpc_job_id"],
    "error_message": beam_row["error_message"]
})
```

**File:** `src/ui/display.py`

**Action:** Display beam progress bars in UI

```python
# In _create_cases_panel method, update beam row display (line 305):
table.add_row(
    f"  ├─ {beam_name}",
    formatter.get_beam_status_text(beam['status']),
    formatter.format_progress_bar(beam.get('progress', 0.0)),  # CHANGED: Show progress
    hpc_display,
    formatter.format_elapsed_time(beam['elapsed_time']),
    formatter.format_error_message(beam.get('error_message', ''), max_length=40),
    style="dim"
)
```

**Testing:**
- Verify progress displays correctly in UI
- Test with various progress values (0, 50, 100)
- Verify formatting with progress bar utility

---

## 5. Testing Strategy

### 5.1. Pattern Validation (CRITICAL)
**Priority: Highest**

Before any implementation:
1. Capture actual `tps_env` log output from running simulation
2. Verify regex patterns match real logs
3. Document any variations in log format
4. Update patterns if needed

### 5.2. Unit Tests

```python
# tests/test_log_parser.py
def test_parse_total_batches():
    parser = TpsLogParser()
    line = "Uploading particles with batch.. : Particle count --> 10000000 with 18 batches"
    result = parser.parse_line(line)
    assert parser.total_batches == 18

def test_parse_current_batch():
    parser = TpsLogParser()
    parser.total_batches = 18
    line = "Generating particles for (5 of 18 batches) in CPU .."
    result = parser.parse_line(line)
    assert result.current_batch == 5
    assert result.progress_percent == pytest.approx(27.78, rel=0.01)

# tests/test_progress_monitor.py
def test_progress_callback_invoked(tmp_path):
    # Test that callback is called with correct progress values
    pass

# tests/test_case_repo.py
def test_update_beam_progress():
    # Test database update for beam progress
    pass
```

### 5.3. Integration Tests

```python
# tests/integration/test_progress_tracking.py
def test_local_simulation_with_progress(test_db, tmp_path):
    # Run actual local simulation and verify progress updates
    pass

def test_remote_simulation_with_progress(test_db, mock_ssh):
    # Mock SSH and verify remote progress monitoring
    pass
```

### 5.4. Edge Case Testing

- Empty log files
- Malformed log lines
- Missing total batches line
- Inconsistent batch counts
- Log file rotation during monitoring
- Network interruptions (remote mode)
- Thread cleanup on errors

---

## 6. Rollout Plan

### Phase 1: Database & Repository (Week 1)
- Add progress column to schema
- Implement update_beam_progress method
- Write and run tests
- Deploy database migration

### Phase 2: Log Parsing & Tailing (Week 2)
- Validate regex patterns with real logs
- Implement TpsLogParser
- Implement LocalLogTailer and RemoteLogTailer
- Write unit tests
- Verify pattern matching accuracy

### Phase 3: Progress Monitor (Week 3)
- Implement ProgressMonitor
- Write integration tests
- Test thread management
- Verify callback mechanism

### Phase 4: State Integration (Week 4)
- Update HpcExecutionState
- Test local mode progress tracking
- Test remote mode progress tracking
- Integration testing

### Phase 5: UI Updates (Week 5)
- Update repository queries
- Update UI provider
- Update UI display
- End-to-end testing

### Phase 6: Production Validation (Week 6)
- Deploy to staging
- Monitor real simulations
- Collect metrics
- Address any issues
- Production deployment

---

## 7. Risk Mitigation

### Risk 1: Log Pattern Mismatch
**Likelihood:** High
**Impact:** Critical
**Mitigation:**
- Validate patterns before implementation
- Make patterns configurable in config.yaml
- Add fallback to pattern-less monitoring
- Log warnings when patterns don't match

### Risk 2: Performance Impact
**Likelihood:** Medium
**Impact:** Medium
**Mitigation:**
- Use separate threads for monitoring
- Limit database update frequency (e.g., every 1% change)
- Add throttling to progress updates
- Monitor CPU/memory usage

### Risk 3: Thread Management Issues
**Likelihood:** Medium
**Impact:** High
**Mitigation:**
- Use daemon threads
- Implement proper cleanup in finally blocks
- Add timeout handling
- Test thread lifecycle thoroughly

### Risk 4: Remote SSH Stability
**Likelihood:** Medium
**Impact:** Medium
**Mitigation:**
- Add SSH connection retry logic
- Handle connection drops gracefully
- Fall back to polling if tailing fails
- Log all SSH errors

---

## 8. Success Metrics

### User Experience
- [ ] Users can see real-time progress for running beams
- [ ] Progress updates appear within 5 seconds of log changes
- [ ] UI remains responsive during progress updates

### Technical
- [ ] Progress accuracy within ±1% of actual completion
- [ ] No memory leaks from monitoring threads
- [ ] <5% CPU overhead from progress monitoring
- [ ] Works reliably in both local and remote modes

### Reliability
- [ ] Handles malformed logs without crashing
- [ ] Recovers from network interruptions (remote mode)
- [ ] Cleans up resources on errors
- [ ] No impact on simulation execution

---

## 9. Configuration Updates

**File:** `config/config.yaml`

Add new configuration section:

```yaml
# Progress Tracking Configuration
progress_tracking:
  enabled: true
  patterns:
    total_batches: "with (\\d+) batches"
    current_batch: "Generating particles for \\((\\d+) of \\d+ batches\\)"
  monitoring:
    poll_interval_seconds: 0.5        # How often to check for new log lines
    update_throttle_percent: 1.0      # Only update DB if progress changes by this much
    timeout_seconds: 3600             # Stop monitoring after this timeout
```

---

## 10. Documentation Requirements

### User Documentation
- Update user guide with progress tracking feature
- Add screenshots of progress display
- Document expected behavior

### Developer Documentation
- Document log parser architecture
- Explain progress monitoring flow
- Provide examples of adding new patterns

### Operations Documentation
- Document configuration options
- Provide troubleshooting guide
- Explain monitoring and metrics

---

## 11. Comparison with Original Plan

| Aspect | Original Plan | Revised Plan |
|--------|---------------|--------------|
| **Progress Level** | Not clearly specified | Beam-level (DB schema aligned) |
| **Execution Modes** | Local only | Both local and remote |
| **Log Access** | stdout streaming | Log file tailing |
| **Architecture** | Handler updates DB directly | State machine orchestrates |
| **Remote Support** | "Future enhancement" | Fully implemented |
| **Database Changes** | Vague | Explicit schema + migration |
| **Pattern Validation** | Assumed | Explicitly required |
| **Testing Strategy** | Minimal | Comprehensive |

---

## 12. Conclusion

This revised plan provides a complete, production-ready approach to real-time progress tracking that:

1. **Aligns with existing architecture:** Uses state machine pattern, respects separation of concerns
2. **Handles all execution modes:** Full support for local and remote
3. **Addresses database requirements:** Clear schema changes and migrations
4. **Provides robust error handling:** Graceful degradation on failures
5. **Includes comprehensive testing:** Unit, integration, and edge case coverage
6. **Manages technical debt:** Clean abstractions, no shortcuts
7. **Validates assumptions:** Requires pattern verification before implementation

The plan is structured for incremental rollout with clear success criteria at each phase.
