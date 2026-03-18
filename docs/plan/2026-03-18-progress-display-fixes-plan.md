# Progress Display Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix progress display to (1) show case progress updating continuously from 0% → 100%, (2) show beam progress normalized to current stage (0% → 100% for each subtask), and (3) display progress percentages instead of stage numbers for active statuses.

**Architecture:**
1. **Case progress**: Keep existing logic (averages beam progress) which already allows continuous 0-100% updates
2. **Beam progress display**: Modify `format_stage_display()` to normalize overall progress (0-100%) to stage-relative progress (0-100%) for display, showing progress within the current subtask
3. **Stage ranges**: Define progress ranges for each stage (e.g., CSV_INTERPRETING: 0-10%, UPLOADING: 10-20%, HPC_RUNNING: 30-90%) to normalize display

**Tech Stack:** Python 3.x, Rich library for text formatting, SQLite database

---

## Task List

### Task 1: Define stage progress ranges and normalization function

**Files:**
- Modify: `src/domain/enums.py`

**Step 1: Add stage progress range constants**

Add stage progress range definitions to `src/domain/enums.py` after line 89:

```python
# Progress ranges for each stage (used for normalizing display)
# Each stage has a start and end percentage in the overall workflow
BEAM_STAGE_RANGES = {
    BeamStatus.PENDING: (0, 0),         # No progress for PENDING
    BeamStatus.CSV_INTERPRETING: (0, 10),   # Stage 1: 0-10%
    BeamStatus.UPLOADING: (10, 20),         # Stage 2: 10-20%
    BeamStatus.TPS_GENERATION: (20, 30),     # Stage 3: 20-30%
    BeamStatus.HPC_QUEUED: (30, 30),       # Stage 4: Fixed at 30%
    BeamStatus.HPC_RUNNING: (30, 90),       # Stage 4: 30-90% (simulation phase)
    BeamStatus.DOWNLOADING: (90, 95),       # Stage 5: 90-95%
    BeamStatus.POSTPROCESSING: (95, 100),    # Stage 6: 95-100%
    BeamStatus.COMPLETED: (100, 100),       # Stage 7: 100%
    BeamStatus.FAILED: (0, 0),            # Failed state
}

CASE_STAGE_RANGES = {
    CaseStatus.PENDING: (0, 0),            # No progress for PENDING
    CaseStatus.CSV_INTERPRETING: (0, 10),  # Stage 1: 0-10%
    CaseStatus.PROCESSING: (10, 95),       # Stage 4: 10-95% (beams in HPC stages)
    CaseStatus.POSTPROCESSING: (95, 100),   # Stage 6: 95-100%
    CaseStatus.COMPLETED: (100, 100),        # Stage 7: 100%
    CaseStatus.FAILED: (0, 0),             # Failed state
    CaseStatus.CANCELLED: (0, 0),          # Cancelled state
}
```

**Step 2: Add progress normalization helper function**

Add a function to normalize progress within a current stage:

```python
def normalize_stage_progress(status, overall_progress: float) -> float:
    """Normalize overall workflow progress to 0-100% range for current stage.

    Args:
        status: BeamStatus or CaseStatus enum value
        overall_progress: Overall workflow progress (0-100%)

    Returns:
        Normalized progress (0-100%) within the current stage
    """
    if isinstance(status, BeamStatus):
        stage_ranges = BEAM_STAGE_RANGES
    elif isinstance(status, CaseStatus):
        stage_ranges = CASE_STAGE_RANGES
    else:
        return 0.0

    stage_range = stage_ranges.get(status, (0, 0))
    stage_start, stage_end = stage_range

    # Terminal states or fixed progress
    if stage_start == stage_end:
        return 100.0

    # Clamp overall progress to stage range
    clamped_progress = max(stage_start, min(stage_end, overall_progress))

    # Normalize to 0-100% range for current stage
    stage_width = stage_end - stage_start
    if stage_width == 0:
        return 100.0

    normalized = ((clamped_progress - stage_start) / stage_width) * 100
    return min(100.0, max(0.0, normalized))
```

**Step 3: Verify normalization function**

Run: `python -c "from src.domain.enums import normalize_stage_progress, BeamStatus; print(normalize_stage_progress(BeamStatus.HPC_RUNNING, 60.0))"`
Expected: Output shows 50.0 (60 is halfway between 30 and 90)

**Step 4: Commit**

```bash
git add src/domain/enums.py
git commit -m "feat: add stage progress ranges and normalization function"
```

### Task 2: Update format_stage_display() to use normalized progress

**Files:**
- Modify: `src/ui/formatter.py:160-217`

**Step 1: Import normalization function**

Update imports in `src/ui/formatter.py` (around line 12):

```python
from src.domain.enums import (
    CaseStatus, GpuStatus, BeamStatus,
    BEAM_STAGE_MAPPING, CASE_STAGE_MAPPING, TOTAL_STAGES,
    normalize_stage_progress,  # Add this import
)
```

**Step 2: Modify format_stage_display() to normalize beam progress**

Replace the entire `format_stage_display()` function (lines 160-203) with:

```python
def format_stage_display(
    status,
    progress: float = 0.0,
    total_stages: int = TOTAL_STAGES,
) -> Text:
    """Format progress as percentage normalized to current stage.

    For beams: Shows 0-100% progress within the current stage
    For cases: Shows 0-100% overall workflow progress
    """
    # Get current stage number
    if isinstance(status, BeamStatus):
        current_stage = BEAM_STAGE_MAPPING.get(status, 0)
    elif isinstance(status, CaseStatus):
        current_stage = CASE_STAGE_MAPPING.get(status, 0)
    else:
        current_stage = 0

    # Determine color based on progress and stage
    if status in (BeamStatus.FAILED, CaseStatus.FAILED, CaseStatus.CANCELLED):
        color = "bold red"
    elif current_stage >= total_stages:
        color = "bold green"
    elif progress >= 100:
        color = "bold green"
    elif progress >= 90:
        color = "cyan"
    elif progress >= 50:
        color = "blue"
    else:
        color = "yellow"

    # Display format logic
    if isinstance(status, BeamStatus):
        # Beams: show normalized progress within current stage
        if status == BeamStatus.PENDING:
            # PENDING shows stage format
            return Text(f"{current_stage}/{total_stages}", style=color)
        elif status in (BeamStatus.COMPLETED, BeamStatus.FAILED):
            # Terminal states show stage format
            return Text(f"{current_stage}/{total_stages}", style=color)
        else:
            # Active stages show normalized percentage (0-100% within current stage)
            normalized = normalize_stage_progress(status, progress)
            return Text(f"{normalized:.0f}%", style=color)
    else:
        # Cases: show overall progress percentage directly
        if status == CaseStatus.PENDING:
            return Text(f"{current_stage}/{total_stages}", style=color)
        elif status in (CaseStatus.COMPLETED, CaseStatus.FAILED, CaseStatus.CANCELLED):
            return Text(f"{current_stage}/{total_stages}", style=color)
        else:
            # Active cases show overall workflow progress (0-100%)
            return Text(f"{progress:.0f}%", style=color)
```

**Step 3: Verify beam progress normalization**

Run: `python -c "from src.ui.formatter import format_stage_display; from src.domain.enums import BeamStatus; print(format_stage_display(BeamStatus.HPC_RUNNING, 60.0))"`
Expected: Output shows "50%" (normalized: (60-30)/(90-30) * 100 = 50%)

**Step 4: Verify case progress display**

Run: `python -c "from src.ui.formatter import format_stage_display; from src.domain.enums import CaseStatus; print(format_stage_display(CaseStatus.PROCESSING, 50.0))"`
Expected: Output shows "50%" (overall workflow progress, not normalized)

**Step 5: Commit**

```bash
git add src/ui/formatter.py
git commit -m "fix: normalize beam progress display to current stage range"
```

### Task 3: Verify case progress updates continuously from beams

**Files:**
- Verify: `src/core/case_aggregator.py:210-220` (update_case_status_from_beams function)

**Step 1: Verify current logic**

Read lines 210-220 of `src/core/case_aggregator.py`:

```python
else:
    try:
        progress = sum(max(0.0, min(100.0, getattr(b, "progress", 0.0))) for b in beams) / total_beams
    except Exception:
        progress = (completed_beams / total_beams) * 100
    case_repo.update_case_status(case_id, CaseStatus.PROCESSING, progress=progress)
```

Expected: This already calculates average of all beam progress, allowing continuous 0-100% updates

**Step 2: Manual verification**

Run application with multiple beams and observe case progress.
Expected: Case progress updates from 10% → 20% → ... → 100% as beams complete stages

**Step 3: No commit needed**

This is a verification task. Case progress should already work correctly.

### Task 4: Create tests for progress display normalization

**Files:**
- Create: `tests/test_formatter.py`

**Step 1: Create test file for formatter**

Create `tests/test_formatter.py` with tests for normalized progress display:

```python
import pytest
from src.ui.formatter import format_stage_display
from src.domain.enums import BeamStatus, CaseStatus, normalize_stage_progress

class TestProgressNormalization:
    """Tests for progress normalization and display formatting."""

    def test_pending_shows_stage_format(self):
        """PENDING status should show stage format 0/7."""
        result = format_stage_display(BeamStatus.PENDING, 0.0)
        assert "0/7" in str(result)
        assert "%" not in str(result)

    def test_completed_shows_stage_format(self):
        """COMPLETED status should show stage format 7/7."""
        result = format_stage_display(BeamStatus.COMPLETED, 100.0)
        assert "7/7" in str(result)
        assert "%" not in str(result)

    def test_beam_progress_normalized_to_current_stage(self):
        """Beam progress should be normalized to current stage range."""
        # CSV_INTERPRETING: 0-10%
        result = format_stage_display(BeamStatus.CSV_INTERPRETING, 5.0)
        assert "50%" in str(result)  # 5 is halfway between 0 and 10

        # UPLOADING: 10-20%
        result = format_stage_display(BeamStatus.UPLOADING, 15.0)
        assert "50%" in str(result)  # 15 is halfway between 10 and 20

        # HPC_RUNNING: 30-90%
        result = format_stage_display(BeamStatus.HPC_RUNNING, 60.0)
        assert "50%" in str(result)  # 60 is halfway between 30 and 90

        # HPC_RUNNING at 90%
        result = format_stage_display(BeamStatus.HPC_RUNNING, 90.0)
        assert "100%" in str(result)

    def test_case_progress_shows_overall_percentage(self):
        """Case progress should show overall workflow percentage (0-100%)."""
        # PROCESSING status with 50% progress
        result = format_stage_display(CaseStatus.PROCESSING, 50.0)
        assert "50%" in str(result)
        # Should NOT show stage format
        assert "/" not in str(result)

        # POSTPROCESSING status with 97% progress
        result = format_stage_display(CaseStatus.POSTPROCESSING, 97.0)
        assert "97%" in str(result)

    def test_normalize_stage_progress_function(self):
        """Test the normalization function directly."""
        # HPC_RUNNING range 30-90
        assert normalize_stage_progress(BeamStatus.HPC_RUNNING, 30) == 0.0
        assert normalize_stage_progress(BeamStatus.HPC_RUNNING, 60) == 50.0
        assert normalize_stage_progress(BeamStatus.HPC_RUNNING, 90) == 100.0

        # UPLOADING range 10-20
        assert normalize_stage_progress(BeamStatus.UPLOADING, 10) == 0.0
        assert normalize_stage_progress(BeamStatus.UPLOADING, 15) == 50.0
        assert normalize_stage_progress(BeamStatus.UPLOADING, 20) == 100.0
```

**Step 2: Run tests to verify changes**

Run: `pytest tests/test_formatter.py -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git add tests/test_formatter.py
git commit -m "test: add tests for progress normalization and display"
```

### Task 5: Verify beam progress updates in workflow states

**Files:**
- Verify: `src/domain/states.py:136-138, 240-242, 262-264, 284-286, 321-323, 437-439` (progress update calls)

**Step 1: Verify progress updates in workflow states**

Check that all workflow states update beam progress with appropriate percentages:
- FileUploadState (UPLOADING): 20%
- HpcExecutionState (HPC_QUEUED): 30%, (HPC_RUNNING): 30-90%
- DownloadState (DOWNLOADING): 85%
- CompletedState (COMPLETED): 100%

Expected: All states call `context.case_repo.update_beam_progress(context.id, float(p))`

**Step 2: Verify local mode progress tracking in execution_handler**

Read lines 149-228 of `src/handlers/execution_handler.py` to confirm HPC_RUNNING progress updates from 30% to 90% based on log file parsing.
Expected: Lines 205-212 calculate sim_progress = (current_batch / total_batches) * 60 + 30

**Step 3: No commit needed**

This is a verification task. Beam progress should already be updating correctly.

### Task 6: Add TPS_GENERATION progress update if missing

**Files:**
- Verify: `src/domain/states.py` (check if TPS_GENERATION state exists and updates progress)

**Step 1: Search for TPS_GENERATION state**

Search for TPS_GENERATION state implementation in `src/domain/states.py`.
Expected: May or may not exist. If it exists, verify it updates progress to 15.0%.

**Step 2: Add TPS_GENERATION progress update if missing**

If TPS_GENERATION state exists but doesn't update progress, add progress update:

```python
# In the appropriate state execute method
try:
    p = context.settings.get_progress_tracking_config().get("coarse_phase_progress", {}).get("TPS_GENERATION")
    if p is not None:
        context.case_repo.update_beam_progress(context.id, float(p))
except Exception as e:
    context.logger.debug("Progress update failed", {"beam_id": context.id, "error": str(e)})
```

**Step 3: Commit if changes made**

```bash
git add src/domain/states.py
git commit -m "fix: update beam progress during TPS_GENERATION stage"
```

### Task 7: Add coarse_phase_progress config to config.yaml

**Files:**
- Modify: `config/config.yaml` (add new section)

**Step 1: Add progress_tracking section to config**

Add progress tracking configuration to `config/config.yaml` to make coarse phase progress percentages explicit:

```yaml
# =====================================================================================
# Progress Tracking - 단계별 진행률 표시 설정
# =====================================================================================
progress_tracking:
  polling_interval_seconds: 5
  coarse_phase_progress:
    CSV_INTERPRETING: 10.0
    UPLOADING: 20.0
    TPS_GENERATION: 15.0
    HPC_QUEUED: 30.0
    HPC_RUNNING: 70.0
    DOWNLOADING: 85.0
    POSTPROCESSING: 95.0
    COMPLETED: 100.0
```

Note: These values are already in `src/config/settings.py` as defaults, but adding to config makes them explicit and configurable.

**Step 2: Verify config loads correctly**

Run: `python -c "from src.config.settings import Settings; s = Settings(); print(s.get_progress_tracking_config())"`
Expected: Returns dict with coarse_phase_progress containing all status percentages

**Step 3: Commit**

```bash
git add config/config.yaml
git commit -m "config: add progress_tracking section with coarse phase percentages"
```

---

## Testing Strategy

### Manual Testing Checklist

**Beam Progress Display (normalized to current stage):**
- [ ] Beam in PENDING shows "0/7"
- [ ] Beam entering CSV_INTERPRETING (10%) shows "0%" → increases to "100%" as it completes
- [ ] Beam entering UPLOADING (20%) shows "0%" → increases to "100%" as it completes
- [ ] Beam entering HPC_QUEUED (30%) shows "0%" (fixed at start of stage)
- [ ] Beam during HPC_RUNNING (30-90%) shows incrementing from "0%" → "100%"
  - At overall 30% (start): shows "0%"
  - At overall 60% (middle): shows "50%"
  - At overall 90% (end): shows "100%"
- [ ] Beam entering DOWNLOADING (90-95%) shows "0%" → "100%" as it completes
- [ ] Beam entering POSTPROCESSING (95-100%) shows "0%" → "100%" as it completes
- [ ] Beam completing (100%) shows "7/7"
- [ ] Failed beam shows "7/7" in red

**Case Progress Display (overall workflow progress):**
- [ ] Case in PENDING shows "0/7"
- [ ] Case in PROCESSING shows overall percentage (e.g., "45%", "60%", "80%")
- [ ] Case in POSTPROCESSING shows "95%" → "100%"
- [ ] Case completing shows "7/7"

**Continuous Progress Updates:**
- [ ] Case progress updates smoothly from 0% → 100% as beams complete (not stuck at stage 4/7)
- [ ] Beam progress shows granular updates within each stage (especially during HPC_RUNNING)

### Unit Tests

Run: `pytest tests/test_formatter.py -v`
Expected: All tests pass

### Integration Tests

Run existing test suite: `pytest tests/ -v`
Expected: All existing tests still pass (no regressions)

---

## Documentation Updates

No documentation updates required. The progress display format change is a UI improvement that doesn't affect public APIs or configuration.
