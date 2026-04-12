# Fraction Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split delivery folders into treatment fractions using a 1-hour time window and DICOM-derived expected beam count. The first complete fraction provides reference beams for moqui_SMC simulation; all fractions feed the PTN checker. A `FractionTracker` polls incomplete fractions every 30 minutes until the window closes.

**Architecture:** A new pure-functional `src/core/fraction_grouper.py` module owns the grouping algorithm, reference-fraction selection, dataclasses, and a stateful `FractionTracker` that polls pending cases. `prepare_case_delivery_data` is refactored to return a `CaseDeliveryResult` object that either carries ready-to-process data or signals `"pending"`. `main.py` wires the tracker into the worker loop; the `deliveries` DB table gains a `fraction_index` column. Dedicated `logs/fraction_events.log` captures partial/anomaly events.

**Tech Stack:** Python 3, pytest, SQLite (via existing `DatabaseConnection`), watchdog (unchanged), existing `StructuredLogger`/`LoggerFactory`.

---

## Working Directory & Worktree Setup

All implementation happens inside a git worktree at `mqi_communicator/.worktree/fraction-grouping`. The main repo is `/mnt/c/MOQUI_SMC/mqi_communicator`.

### Task 0: Create worktree

- [ ] **Step 0.1: Verify main repo is clean of staged changes**

Run:
```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator && git status
```
Expected: `nothing added to commit` (untracked files like the spec doc are fine).

- [ ] **Step 0.2: Ensure `.worktree/` is gitignored**

Check `.gitignore`:
```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator && grep -E "^\.worktree" .gitignore || echo "NOT_FOUND"
```
If `NOT_FOUND`, append:
```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator && echo ".worktree/" >> .gitignore
```

- [ ] **Step 0.3: Create the worktree directory**

Run:
```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator && mkdir -p .worktree && git worktree add .worktree/fraction-grouping -b feature/fraction-grouping
```
Expected: "Preparing worktree (new branch 'feature/fraction-grouping')".

- [ ] **Step 0.4: All subsequent tasks run inside the worktree**

From here on, **every** `cd`, test run, and edit uses `/mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping` as the working directory.

- [ ] **Step 0.5: Commit the .gitignore change (if modified) in main repo**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator && git add .gitignore && git commit -m "chore: gitignore .worktree/"
```
Only run if `.gitignore` was modified in Step 0.2. Otherwise skip.

---

## File Structure

Inside the worktree (`.worktree/fraction-grouping/`):

**New files:**
- `src/core/fraction_grouper.py` — Dataclasses (`Fraction`, `CaseDeliveryResult`, `PendingCase`, `ReadyCase`), pure functions (`group_deliveries_into_fractions`, `select_reference_fraction`), `FractionTracker` class, fraction event logger.
- `tests/test_fraction_grouper.py` — Unit tests for all the above.

**Modified files:**
- `src/domain/models.py` — Add `fraction_index` to `DeliveryData`.
- `src/database/connection.py` — Add `fraction_index` column migration in `init_db()`.
- `src/repositories/case_repo.py` — Persist and read `fraction_index` on deliveries.
- `src/core/case_aggregator.py` — Refactor `prepare_case_delivery_data` to use fraction grouping and return `CaseDeliveryResult`. Update `prepare_beam_jobs` wrapper.
- `src/core/dispatcher.py` — Update `run_case_level_ptn_analysis` for the new return shape.
- `main.py` — Instantiate `FractionTracker`, route pending status, add `_process_ready_case`, call `check_pending()` each loop.
- `tests/test_case_aggregator.py` — Update existing tests for new return shape; add multi-fraction scenarios.

---

## Task 1: Add `fraction_index` to `DeliveryData` model

**Files:**
- Modify: `src/domain/models.py` (DeliveryData dataclass, around line 47-68)

- [ ] **Step 1.1: Read current DeliveryData dataclass**

Run:
```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && sed -n '46,68p' src/domain/models.py
```
Expected: shows the `DeliveryData` dataclass definition.

- [ ] **Step 1.2: Add `fraction_index` field**

Edit `src/domain/models.py`. Add the new field after `is_reference_delivery`:

```python
@dataclass
class DeliveryData:
    """Data Transfer Object for a single delivered beam log session."""

    delivery_id: str
    parent_case_id: str
    beam_id: str
    delivery_path: Path
    delivery_timestamp: datetime
    delivery_date: str
    raw_beam_number: Optional[int]
    treatment_beam_index: Optional[int]
    is_reference_delivery: bool
    fraction_index: Optional[int] = None
    ptn_status: Optional[str] = None
    ptn_last_run_at: Optional[datetime] = None
    gamma_pass_rate: Optional[float] = None
    gamma_mean: Optional[float] = None
    gamma_max: Optional[float] = None
    evaluated_points: Optional[int] = None
    report_path: Optional[Path] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
```

- [ ] **Step 1.3: Verify models still import cleanly**

Run:
```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -c "from src.domain.models import DeliveryData; import dataclasses; print([f.name for f in dataclasses.fields(DeliveryData)])"
```
Expected: list includes `'fraction_index'`.

- [ ] **Step 1.4: Commit**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git add src/domain/models.py && git commit -m "feat(models): add fraction_index to DeliveryData"
```

---

## Task 2: Database migration for `fraction_index` column

**Files:**
- Modify: `src/database/connection.py` (after line 296, within the delivery column migration block)
- Test: `tests/test_fraction_index_migration.py` (NEW)

- [ ] **Step 2.1: Write the failing migration test**

Create `tests/test_fraction_index_migration.py`:

```python
"""Verify the fraction_index column is present on the deliveries table after init_db."""

from pathlib import Path
from unittest.mock import MagicMock

from src.database.connection import DatabaseConnection


def test_deliveries_table_has_fraction_index_column(tmp_path):
    db_path = tmp_path / "test.db"
    logger = MagicMock()
    settings = MagicMock()
    conn = DatabaseConnection(db_path=db_path, settings=settings, logger=logger)
    conn.init_db()

    with conn.transaction() as raw_conn:
        cursor = raw_conn.execute("PRAGMA table_info(deliveries)")
        columns = [row[1] for row in cursor.fetchall()]

    assert "fraction_index" in columns, (
        f"Expected fraction_index column on deliveries table, got {columns!r}"
    )
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_index_migration.py -v
```
Expected: FAIL with `AssertionError: Expected fraction_index column on deliveries table`.

- [ ] **Step 2.3: Add the migration**

Edit `src/database/connection.py`. Locate the block starting `cursor = conn.execute("PRAGMA table_info(deliveries)")` (around line 262) and the `if 'error_message' not in delivery_columns:` block near line 294-296. **Immediately after** the existing `error_message` migration block for deliveries, add:

```python
                    if 'fraction_index' not in delivery_columns:
                        self.logger.info("Adding fraction_index column to deliveries table")
                        conn.execute("ALTER TABLE deliveries ADD COLUMN fraction_index INTEGER")
```

Also add `fraction_index INTEGER` to the `CREATE TABLE IF NOT EXISTS deliveries` statement so fresh databases include the column. Find the `CREATE TABLE` block (around line 200-223) and add the column just before `created_at`:

```python
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS deliveries (
                            delivery_id TEXT PRIMARY KEY,
                            parent_case_id TEXT NOT NULL,
                            beam_id TEXT NOT NULL,
                            delivery_path TEXT NOT NULL,
                            delivery_timestamp TIMESTAMP NOT NULL,
                            delivery_date TEXT NOT NULL,
                            raw_beam_number INTEGER,
                            treatment_beam_index INTEGER,
                            is_reference_delivery BOOLEAN DEFAULT 0,
                            ptn_status TEXT,
                            ptn_last_run_at TIMESTAMP,
                            gamma_pass_rate REAL,
                            gamma_mean REAL,
                            gamma_max REAL,
                            evaluated_points INTEGER,
                            report_path TEXT,
                            error_message TEXT,
                            fraction_index INTEGER,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (parent_case_id) REFERENCES cases (case_id),
                            FOREIGN KEY (beam_id) REFERENCES beams (beam_id)
                        )
                    """)
```

- [ ] **Step 2.4: Run the test to verify it passes**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_index_migration.py -v
```
Expected: PASS.

- [ ] **Step 2.5: Run existing DB tests to verify no regression**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_database_connection.py tests/test_db_context.py -v
```
Expected: all PASS.

- [ ] **Step 2.6: Commit**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git add src/database/connection.py tests/test_fraction_index_migration.py && git commit -m "feat(db): add fraction_index column to deliveries table"
```

---

## Task 3: Persist and read `fraction_index` in `CaseRepository`

**Files:**
- Modify: `src/repositories/case_repo.py` (`create_or_update_deliveries` around line 769, `_map_row_to_delivery_data` around line 937)
- Test: `tests/test_fraction_index_persistence.py` (NEW)

- [ ] **Step 3.1: Write the failing persistence test**

Create `tests/test_fraction_index_persistence.py`:

```python
"""Verify fraction_index is persisted and read back correctly."""

from pathlib import Path
from unittest.mock import MagicMock

from src.database.connection import DatabaseConnection
from src.repositories.case_repo import CaseRepository


def _make_repo(tmp_path):
    logger = MagicMock()
    settings = MagicMock()
    conn = DatabaseConnection(db_path=tmp_path / "test.db", settings=settings, logger=logger)
    conn.init_db()
    return CaseRepository(conn, logger), conn


def test_create_or_update_deliveries_persists_fraction_index(tmp_path):
    repo, _ = _make_repo(tmp_path)

    case_id = "caseA"
    repo.add_case(case_id, tmp_path / case_id)
    repo.create_case_with_beams(
        case_id,
        str(tmp_path / case_id),
        [{"beam_id": f"{case_id}_beam_1", "beam_path": tmp_path / case_id / "b1", "beam_number": 1}],
    )

    deliveries = [
        {
            "delivery_id": "d1",
            "beam_id": f"{case_id}_beam_1",
            "delivery_path": tmp_path / case_id / "2026032000000000",
            "delivery_timestamp": "2026-03-20T00:00:00",
            "delivery_date": "2026-03-20",
            "raw_beam_number": 1,
            "treatment_beam_index": 1,
            "is_reference_delivery": True,
            "fraction_index": 3,
        }
    ]
    repo.create_or_update_deliveries(case_id, deliveries)

    loaded = repo.get_deliveries_for_case(case_id)
    assert len(loaded) == 1
    assert loaded[0].fraction_index == 3, (
        f"Expected fraction_index=3, got {loaded[0].fraction_index!r}"
    )


def test_create_or_update_deliveries_defaults_fraction_index_to_none(tmp_path):
    repo, _ = _make_repo(tmp_path)
    case_id = "caseB"
    repo.add_case(case_id, tmp_path / case_id)
    repo.create_case_with_beams(
        case_id,
        str(tmp_path / case_id),
        [{"beam_id": f"{case_id}_beam_1", "beam_path": tmp_path / case_id / "b1", "beam_number": 1}],
    )

    deliveries = [
        {
            "delivery_id": "d1",
            "beam_id": f"{case_id}_beam_1",
            "delivery_path": tmp_path / case_id / "2026032000000000",
            "delivery_timestamp": "2026-03-20T00:00:00",
            "delivery_date": "2026-03-20",
            "raw_beam_number": 1,
            "treatment_beam_index": 1,
            "is_reference_delivery": True,
            # fraction_index omitted
        }
    ]
    repo.create_or_update_deliveries(case_id, deliveries)
    loaded = repo.get_deliveries_for_case(case_id)
    assert loaded[0].fraction_index is None
```

- [ ] **Step 3.2: Run test to verify it fails**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_index_persistence.py -v
```
Expected: FAIL — either AttributeError for `fraction_index` on DeliveryData (if Task 1 reverted) or the new test value doesn't propagate (we haven't updated the writer/reader yet).

- [ ] **Step 3.3: Update `create_or_update_deliveries` to write fraction_index**

Edit `src/repositories/case_repo.py`. Replace the method body around line 769-811 with:

```python
    def create_or_update_deliveries(self, case_id: str, deliveries: List[Dict[str, Any]]) -> None:
        """Upsert delivery-session records for a case."""
        self._log_operation(
            "create_or_update_deliveries",
            case_id=case_id,
            delivery_count=len(deliveries),
        )
        if not deliveries:
            return

        with self.db.transaction() as conn:
            for delivery in deliveries:
                conn.execute(
                    """
                    INSERT INTO deliveries (
                        delivery_id, parent_case_id, beam_id, delivery_path,
                        delivery_timestamp, delivery_date, raw_beam_number,
                        treatment_beam_index, is_reference_delivery, fraction_index,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(delivery_id) DO UPDATE SET
                        beam_id = excluded.beam_id,
                        delivery_path = excluded.delivery_path,
                        delivery_timestamp = excluded.delivery_timestamp,
                        delivery_date = excluded.delivery_date,
                        raw_beam_number = excluded.raw_beam_number,
                        treatment_beam_index = excluded.treatment_beam_index,
                        is_reference_delivery = excluded.is_reference_delivery,
                        fraction_index = excluded.fraction_index,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        delivery["delivery_id"],
                        case_id,
                        delivery["beam_id"],
                        str(delivery["delivery_path"]),
                        delivery["delivery_timestamp"],
                        delivery["delivery_date"],
                        delivery.get("raw_beam_number"),
                        delivery.get("treatment_beam_index"),
                        1 if delivery.get("is_reference_delivery") else 0,
                        delivery.get("fraction_index"),
                    ),
                )
```

- [ ] **Step 3.4: Update `_map_row_to_delivery_data` to read fraction_index**

In the same file, find `_map_row_to_delivery_data` (around line 937). Add the `fraction_index` line right after `is_reference_delivery`:

```python
    def _map_row_to_delivery_data(self, row) -> DeliveryData:
        """Maps a database row to a DeliveryData DTO."""
        return DeliveryData(
            delivery_id=row["delivery_id"],
            parent_case_id=row["parent_case_id"],
            beam_id=row["beam_id"],
            delivery_path=Path(row["delivery_path"]),
            delivery_timestamp=datetime.fromisoformat(row["delivery_timestamp"]),
            delivery_date=row["delivery_date"],
            raw_beam_number=row["raw_beam_number"],
            treatment_beam_index=row["treatment_beam_index"],
            is_reference_delivery=bool(row["is_reference_delivery"]),
            fraction_index=row["fraction_index"] if "fraction_index" in row.keys() else None,
            ptn_status=row["ptn_status"] if "ptn_status" in row.keys() else None,
            # ... leave the rest of the existing kwargs unchanged
```

(Preserve the rest of the `DeliveryData(...)` kwargs exactly — only insert the `fraction_index=` line at the correct position.)

- [ ] **Step 3.5: Run the test to verify it passes**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_index_persistence.py -v
```
Expected: both tests PASS.

- [ ] **Step 3.6: Run existing repo tests for regression**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_case_repo_mapping.py tests/test_ptn_checker_integration.py tests/test_ptn_checker_workflow.py -v
```
Expected: all PASS.

- [ ] **Step 3.7: Commit**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git add src/repositories/case_repo.py tests/test_fraction_index_persistence.py && git commit -m "feat(repo): persist fraction_index on deliveries"
```

---

## Task 4: Create `fraction_grouper` module — dataclasses only

**Files:**
- Create: `src/core/fraction_grouper.py`
- Test: `tests/test_fraction_grouper.py`

- [ ] **Step 4.1: Write failing import test**

Create `tests/test_fraction_grouper.py`:

```python
"""Tests for fraction_grouper module."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest


def test_fraction_dataclass_fields():
    from src.core.fraction_grouper import Fraction
    f = Fraction(
        index=1,
        delivery_folders=[Path("a")],
        beam_numbers=[1],
        start_time=datetime(2026, 3, 20, 10, 0, 0),
        end_time=datetime(2026, 3, 20, 10, 5, 0),
        status="complete",
    )
    assert f.index == 1
    assert f.status == "complete"


def test_case_delivery_result_dataclass_fields():
    from src.core.fraction_grouper import CaseDeliveryResult
    r = CaseDeliveryResult(
        beam_jobs=[],
        delivery_records=[],
        fractions=[],
        status="ready",
        pending_reason=None,
    )
    assert r.status == "ready"


def test_pending_case_dataclass_fields():
    from src.core.fraction_grouper import PendingCase
    pc = PendingCase(
        case_id="c1",
        case_path=Path("/tmp/c1"),
        fractions=[],
        expected_beam_count=4,
        first_seen=datetime(2026, 3, 20, 10, 0, 0),
        last_checked=datetime(2026, 3, 20, 10, 0, 0),
    )
    assert pc.case_id == "c1"


def test_ready_case_dataclass_fields():
    from src.core.fraction_grouper import ReadyCase, CaseDeliveryResult
    result = CaseDeliveryResult(
        beam_jobs=[], delivery_records=[], fractions=[], status="ready", pending_reason=None,
    )
    rc = ReadyCase(case_id="c1", case_path=Path("/tmp/c1"), result=result)
    assert rc.case_id == "c1"
    assert rc.result.status == "ready"
```

- [ ] **Step 4.2: Run the tests to verify they fail**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_grouper.py -v
```
Expected: all FAIL with `ModuleNotFoundError: No module named 'src.core.fraction_grouper'`.

- [ ] **Step 4.3: Create the module with only dataclasses**

Create `src/core/fraction_grouper.py`:

```python
# =====================================================================================
# Target File: src/core/fraction_grouper.py
# =====================================================================================
"""Fraction grouping, reference selection, and pending-fraction tracking.

Delivery folders on disk are grouped into treatment fractions by a 1-hour
time window combined with the DICOM-derived expected beam count. The first
complete fraction provides reference beams for simulation; all fractions feed
the PTN checker. FractionTracker polls cases whose fractions are still
accumulating at the time of first scan.

All logging of partial/anomaly events is tee'd to a dedicated "fraction_events"
logger so the operator can inspect fraction-level issues independently of the
per-case logs.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Fraction:
    """In-memory grouping of delivery folders belonging to one treatment fraction."""
    index: int
    delivery_folders: List[Path]
    beam_numbers: List[int]
    start_time: datetime
    end_time: datetime
    status: str  # "complete" | "partial" | "pending" | "anomaly"


@dataclass
class CaseDeliveryResult:
    """Output of prepare_case_delivery_data after fraction grouping."""
    beam_jobs: List[Dict[str, Any]]
    delivery_records: List[Dict[str, Any]]
    fractions: List[Fraction]
    status: str  # "ready" | "pending"
    pending_reason: Optional[str] = None


@dataclass
class PendingCase:
    """A case whose fraction is still accumulating on disk."""
    case_id: str
    case_path: Path
    fractions: List[Fraction]
    expected_beam_count: int
    first_seen: datetime
    last_checked: datetime


@dataclass
class ReadyCase:
    """A case that the tracker has resolved and is ready to process."""
    case_id: str
    case_path: Path
    result: CaseDeliveryResult
```

- [ ] **Step 4.4: Run the tests to verify they pass**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_grouper.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 4.5: Commit**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git add src/core/fraction_grouper.py tests/test_fraction_grouper.py && git commit -m "feat(fraction): add fraction_grouper module with dataclasses"
```

---

## Task 5: Implement `group_deliveries_into_fractions`

**Files:**
- Modify: `src/core/fraction_grouper.py`
- Modify: `tests/test_fraction_grouper.py`

- [ ] **Step 5.1: Write failing tests for the grouping algorithm**

Append to `tests/test_fraction_grouper.py`:

```python
# ---------------------------------------------------------------------------
# group_deliveries_into_fractions
# ---------------------------------------------------------------------------


def _write_planinfo(beam_dir: Path, beam_number: int, timestamp: str) -> None:
    """Write a PlanInfo.txt with the given beam number and TCSC_IRRAD_DATETIME."""
    beam_dir.mkdir(parents=True, exist_ok=True)
    (beam_dir / "PlanInfo.txt").write_text(
        "\n".join(
            [
                "DICOM_PATIENT_ID,04198922",
                f"DICOM_BEAM_NUMBER,{beam_number}",
                f"TCSC_IRRAD_DATETIME,{timestamp}",
            ]
        ),
        encoding="ascii",
    )


def test_single_fraction_all_beams_present_is_complete(tmp_path):
    from src.core.fraction_grouper import group_deliveries_into_fractions
    folders = []
    # 4 beams in a single ~5-minute window
    for i, (beam, ts) in enumerate(
        [
            (2, "20260319234800000000"),
            (3, "20260319235200000000"),
            (4, "20260319235600000000"),
            (5, "20260319235800000000"),
        ]
    ):
        d = tmp_path / ts[:16]
        _write_planinfo(d, beam, ts[:16])
        folders.append(d)

    # Use a fake "now" far in the past of the future — window closed.
    now = datetime(2026, 3, 21, 12, 0, 0)
    fractions = group_deliveries_into_fractions(
        folders, expected_beam_count=4, time_window=timedelta(hours=1), now=now
    )
    assert len(fractions) == 1
    assert fractions[0].status == "complete"
    assert fractions[0].beam_numbers == [2, 3, 4, 5]


def test_multiple_fractions_separated_by_hours(tmp_path):
    from src.core.fraction_grouper import group_deliveries_into_fractions
    # Fraction 1 on 2026-03-19, fraction 2 on 2026-03-20 (many hours apart)
    f1 = [(2, "20260319234800000000"), (3, "20260319235200000000")]
    f2 = [(2, "20260320181000000000"), (3, "20260320181400000000")]

    folders = []
    for beam, ts in f1 + f2:
        d = tmp_path / ts[:16]
        _write_planinfo(d, beam, ts[:16])
        folders.append(d)

    now = datetime(2026, 3, 21, 12, 0, 0)
    fractions = group_deliveries_into_fractions(
        folders, expected_beam_count=2, time_window=timedelta(hours=1), now=now
    )
    assert len(fractions) == 2
    assert fractions[0].status == "complete"
    assert fractions[1].status == "complete"


def test_midnight_crossing_stays_in_same_fraction(tmp_path):
    from src.core.fraction_grouper import group_deliveries_into_fractions
    # 23:58 and 00:02 — same fraction (within 1-hour window)
    entries = [
        (2, "20260319235800000000"),
        (3, "20260320000200000000"),
    ]
    folders = []
    for beam, ts in entries:
        d = tmp_path / ts[:16]
        _write_planinfo(d, beam, ts[:16])
        folders.append(d)

    now = datetime(2026, 3, 21, 12, 0, 0)
    fractions = group_deliveries_into_fractions(
        folders, expected_beam_count=2, time_window=timedelta(hours=1), now=now
    )
    assert len(fractions) == 1
    assert fractions[0].status == "complete"


def test_partial_fraction_window_closed(tmp_path):
    from src.core.fraction_grouper import group_deliveries_into_fractions
    # 2 folders, expected 4, window closed -> partial
    entries = [(2, "20260319234800000000"), (3, "20260319235200000000")]
    folders = []
    for beam, ts in entries:
        d = tmp_path / ts[:16]
        _write_planinfo(d, beam, ts[:16])
        folders.append(d)

    now = datetime(2026, 3, 21, 12, 0, 0)
    fractions = group_deliveries_into_fractions(
        folders, expected_beam_count=4, time_window=timedelta(hours=1), now=now
    )
    assert len(fractions) == 1
    assert fractions[0].status == "partial"


def test_pending_fraction_window_still_open(tmp_path):
    from src.core.fraction_grouper import group_deliveries_into_fractions
    # 2 folders, expected 4, now is 10 minutes after start -> still pending
    entries = [(2, "20260319234800000000"), (3, "20260319235200000000")]
    folders = []
    for beam, ts in entries:
        d = tmp_path / ts[:16]
        _write_planinfo(d, beam, ts[:16])
        folders.append(d)

    # Window opens at 23:48:00; now is 23:58 (10 min later) — still open.
    now = datetime(2026, 3, 19, 23, 58, 0)
    fractions = group_deliveries_into_fractions(
        folders, expected_beam_count=4, time_window=timedelta(hours=1), now=now
    )
    assert len(fractions) == 1
    assert fractions[0].status == "pending"


def test_anomaly_fraction_more_than_expected(tmp_path):
    from src.core.fraction_grouper import group_deliveries_into_fractions
    entries = [
        (2, "20260319234800000000"),
        (3, "20260319235200000000"),
        (4, "20260319235600000000"),
        (5, "20260319235800000000"),
        (2, "20260319235900000000"),  # duplicate -> 5 folders for 4 beams
    ]
    folders = []
    for i, (beam, ts) in enumerate(entries):
        d = tmp_path / f"{ts[:16]}_{i}"
        _write_planinfo(d, beam, ts[:16])
        folders.append(d)

    now = datetime(2026, 3, 21, 12, 0, 0)
    fractions = group_deliveries_into_fractions(
        folders, expected_beam_count=4, time_window=timedelta(hours=1), now=now
    )
    assert len(fractions) == 1
    assert fractions[0].status == "anomaly"


def test_empty_folder_list_returns_empty(tmp_path):
    from src.core.fraction_grouper import group_deliveries_into_fractions
    now = datetime(2026, 3, 21, 12, 0, 0)
    fractions = group_deliveries_into_fractions(
        [], expected_beam_count=4, time_window=timedelta(hours=1), now=now
    )
    assert fractions == []
```

- [ ] **Step 5.2: Run the tests to verify they fail**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_grouper.py -v -k "group or midnight or partial or pending_fraction or anomaly or empty_folder"
```
Expected: FAIL with `ImportError: cannot import name 'group_deliveries_into_fractions'`.

- [ ] **Step 5.3: Implement the grouping algorithm**

Append to `src/core/fraction_grouper.py`:

```python
# ---------------------------------------------------------------------------
# PlanInfo parsing (kept internal to this module — a minimal, pure version)
# ---------------------------------------------------------------------------


def _parse_planinfo(beam_path: Path) -> Optional[Dict[str, Any]]:
    """Parse TCSC_IRRAD_DATETIME and DICOM_BEAM_NUMBER from PlanInfo.txt."""
    planinfo_path = beam_path / "PlanInfo.txt"
    if not planinfo_path.exists():
        return None

    values: Dict[str, str] = {}
    try:
        for raw_line in planinfo_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            key, sep, value = raw_line.strip().partition(",")
            if sep:
                values[key.strip()] = value.strip()
    except OSError:
        return None

    timestamp_raw = values.get("TCSC_IRRAD_DATETIME") or beam_path.name
    beam_number_raw = values.get("DICOM_BEAM_NUMBER")

    timestamp: Optional[datetime] = None
    for candidate in (timestamp_raw, beam_path.name):
        try:
            timestamp = datetime.strptime(candidate, "%Y%m%d%H%M%S%f")
            break
        except (TypeError, ValueError):
            continue
    if timestamp is None:
        return None

    try:
        beam_number = int(beam_number_raw) if beam_number_raw is not None else None
    except ValueError:
        beam_number = None

    return {"timestamp": timestamp, "beam_number": beam_number}


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def group_deliveries_into_fractions(
    delivery_folders: List[Path],
    expected_beam_count: int,
    time_window: timedelta = timedelta(hours=1),
    now: Optional[datetime] = None,
) -> List[Fraction]:
    """Greedy time-window grouping of delivery folders into fractions.

    Args:
        delivery_folders: Candidate folders that may belong to different fractions.
        expected_beam_count: Expected beams per fraction (from RT Plan).
        time_window: Max span between first folder and subsequent folder in same fraction.
        now: Reference time for pending/partial determination; defaults to datetime.now().

    Returns:
        List of Fraction objects in chronological order. Each fraction is
        classified "complete", "partial", "pending", or "anomaly".
    """
    if not delivery_folders:
        return []

    reference_now = now or datetime.now()

    parsed = []
    for folder in delivery_folders:
        info = _parse_planinfo(folder)
        if info is None or info["timestamp"] is None:
            continue
        parsed.append((info["timestamp"], info["beam_number"], folder))

    parsed.sort(key=lambda row: row[0])

    fractions: List[Fraction] = []
    current_folders: List[Path] = []
    current_beams: List[int] = []
    current_times: List[datetime] = []
    fraction_start: Optional[datetime] = None
    fraction_index = 0

    def _close_current() -> None:
        nonlocal current_folders, current_beams, current_times, fraction_start, fraction_index
        if not current_folders:
            return
        fraction_index += 1
        start_time = current_times[0]
        end_time = current_times[-1]
        window_closed = (reference_now - start_time) > time_window
        delivered = len(current_folders)
        if delivered > expected_beam_count:
            status = "anomaly"
        elif delivered == expected_beam_count:
            status = "complete"
        elif window_closed:
            status = "partial"
        else:
            status = "pending"
        fractions.append(
            Fraction(
                index=fraction_index,
                delivery_folders=list(current_folders),
                beam_numbers=list(current_beams),
                start_time=start_time,
                end_time=end_time,
                status=status,
            )
        )
        current_folders = []
        current_beams = []
        current_times = []
        fraction_start = None

    for timestamp, beam_number, folder in parsed:
        if fraction_start is None:
            fraction_start = timestamp
        elif (timestamp - fraction_start) > time_window:
            _close_current()
            fraction_start = timestamp
        current_folders.append(folder)
        current_beams.append(beam_number if beam_number is not None else 0)
        current_times.append(timestamp)

    _close_current()
    return fractions
```

- [ ] **Step 5.4: Run the tests to verify they pass**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_grouper.py -v
```
Expected: all tests PASS.

- [ ] **Step 5.5: Commit**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git add src/core/fraction_grouper.py tests/test_fraction_grouper.py && git commit -m "feat(fraction): implement group_deliveries_into_fractions"
```

---

## Task 6: Implement `select_reference_fraction`

**Files:**
- Modify: `src/core/fraction_grouper.py`
- Modify: `tests/test_fraction_grouper.py`

- [ ] **Step 6.1: Write failing tests for reference selection**

Append to `tests/test_fraction_grouper.py`:

```python
# ---------------------------------------------------------------------------
# select_reference_fraction
# ---------------------------------------------------------------------------


def _make_fraction(index, status, folder_count=4):
    from src.core.fraction_grouper import Fraction
    return Fraction(
        index=index,
        delivery_folders=[Path(f"/f{index}_{i}") for i in range(folder_count)],
        beam_numbers=list(range(1, folder_count + 1)),
        start_time=datetime(2026, 3, 19 + index, 10, 0, 0),
        end_time=datetime(2026, 3, 19 + index, 10, 10, 0),
        status=status,
    )


def test_select_reference_fraction_picks_earliest_complete():
    from src.core.fraction_grouper import select_reference_fraction
    fractions = [
        _make_fraction(1, "partial"),
        _make_fraction(2, "complete"),
        _make_fraction(3, "complete"),
    ]
    picked = select_reference_fraction(fractions)
    assert picked is not None
    assert picked.index == 2


def test_select_reference_fraction_returns_none_when_no_complete():
    from src.core.fraction_grouper import select_reference_fraction
    fractions = [_make_fraction(1, "partial"), _make_fraction(2, "pending")]
    assert select_reference_fraction(fractions) is None


def test_select_reference_fraction_handles_empty_list():
    from src.core.fraction_grouper import select_reference_fraction
    assert select_reference_fraction([]) is None
```

- [ ] **Step 6.2: Run the tests to verify they fail**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_grouper.py -v -k "select_reference"
```
Expected: FAIL with `ImportError: cannot import name 'select_reference_fraction'`.

- [ ] **Step 6.3: Implement `select_reference_fraction`**

Append to `src/core/fraction_grouper.py`:

```python
def select_reference_fraction(fractions: List[Fraction]) -> Optional[Fraction]:
    """Return the earliest fraction with status 'complete', or None."""
    for fraction in fractions:
        if fraction.status == "complete":
            return fraction
    return None
```

- [ ] **Step 6.4: Run the tests to verify they pass**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_grouper.py -v
```
Expected: all tests PASS.

- [ ] **Step 6.5: Commit**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git add src/core/fraction_grouper.py tests/test_fraction_grouper.py && git commit -m "feat(fraction): implement select_reference_fraction"
```

---

## Task 7: Add fraction event logger helper

**Files:**
- Modify: `src/core/fraction_grouper.py`
- Modify: `tests/test_fraction_grouper.py`

- [ ] **Step 7.1: Write failing test for the fraction-event logger helper**

Append to `tests/test_fraction_grouper.py`:

```python
# ---------------------------------------------------------------------------
# fraction event logger
# ---------------------------------------------------------------------------


def test_get_fraction_event_logger_returns_structured_logger():
    from src.infrastructure.logging_handler import LoggerFactory
    from src.core.fraction_grouper import get_fraction_event_logger

    # Configure factory with a minimal in-memory config.
    LoggerFactory._config = None
    LoggerFactory._loggers = {}
    LoggerFactory.configure(
        {"log_dir": "/tmp", "log_level": "INFO", "structured_logging": False}
    )

    logger = get_fraction_event_logger()
    assert logger is not None
    assert logger.logger.name == "fraction_events"


def test_log_fraction_event_calls_warning_on_both_loggers():
    from unittest.mock import MagicMock
    from src.core.fraction_grouper import log_fraction_event

    case_logger = MagicMock()
    event_logger = MagicMock()

    log_fraction_event(
        case_logger,
        event_logger,
        "partial_reference_used",
        "Partial fraction used as reference",
        {"case_id": "c1", "fraction_index": 1},
    )
    case_logger.warning.assert_called_once()
    event_logger.warning.assert_called_once()

    # Context is passed through on both calls.
    _, case_kwargs = case_logger.warning.call_args
    _, event_kwargs = event_logger.warning.call_args
    case_arg = case_logger.warning.call_args[0]
    event_arg = event_logger.warning.call_args[0]
    # Signature: logger.warning(msg, context_dict)
    assert case_arg[1]["event"] == "partial_reference_used"
    assert event_arg[1]["event"] == "partial_reference_used"
    assert case_arg[1]["case_id"] == "c1"
```

- [ ] **Step 7.2: Run the test to verify it fails**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_grouper.py -v -k "fraction_event"
```
Expected: FAIL with `ImportError: cannot import name 'get_fraction_event_logger'`.

- [ ] **Step 7.3: Implement the logger helpers**

Append to `src/core/fraction_grouper.py`:

```python
# ---------------------------------------------------------------------------
# Fraction event logging
# ---------------------------------------------------------------------------


def get_fraction_event_logger():
    """Return the dedicated fraction_events StructuredLogger (logs/fraction_events.log)."""
    from src.infrastructure.logging_handler import LoggerFactory
    return LoggerFactory.get_logger("fraction_events")


def log_fraction_event(
    case_logger,
    event_logger,
    event: str,
    message: str,
    context: Dict[str, Any],
) -> None:
    """Emit a WARNING to both the case logger and the dedicated fraction events logger.

    Args:
        case_logger: The per-case StructuredLogger (or None to skip).
        event_logger: The fraction_events StructuredLogger.
        event: Short stable key for filtering (e.g. "partial_reference_used").
        message: Human-readable message.
        context: Structured fields. The "event" key is injected automatically.
    """
    enriched = dict(context)
    enriched["event"] = event
    if case_logger is not None:
        case_logger.warning(message, enriched)
    if event_logger is not None:
        event_logger.warning(message, enriched)
```

- [ ] **Step 7.4: Run the tests to verify they pass**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_grouper.py -v
```
Expected: all tests PASS.

- [ ] **Step 7.5: Commit**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git add src/core/fraction_grouper.py tests/test_fraction_grouper.py && git commit -m "feat(fraction): add dedicated fraction event logger"
```

---

## Task 8: Implement `FractionTracker`

**Files:**
- Modify: `src/core/fraction_grouper.py`
- Modify: `tests/test_fraction_grouper.py`

- [ ] **Step 8.1: Write failing tests for FractionTracker**

Append to `tests/test_fraction_grouper.py`:

```python
# ---------------------------------------------------------------------------
# FractionTracker
# ---------------------------------------------------------------------------


def _write_rtplan_stub(case_path: Path) -> Path:
    """Create an empty DICOM file so find_rtplan_file returns something when patched."""
    rtplan = case_path / "RP.stub.dcm"
    rtplan.write_text("", encoding="ascii")
    return rtplan


def test_tracker_register_and_pending_count(tmp_path):
    from src.core.fraction_grouper import FractionTracker, Fraction
    tracker = FractionTracker(poll_interval_seconds=1800)
    frac = Fraction(
        index=1,
        delivery_folders=[tmp_path / "f1"],
        beam_numbers=[1],
        start_time=datetime(2026, 3, 20, 10, 0, 0),
        end_time=datetime(2026, 3, 20, 10, 0, 0),
        status="pending",
    )
    tracker.register(
        case_id="c1",
        case_path=tmp_path,
        fractions=[frac],
        expected_beam_count=4,
        now=datetime(2026, 3, 20, 10, 0, 0),
    )
    assert tracker.pending_count == 1


def test_tracker_skips_before_interval_elapsed(tmp_path):
    from src.core.fraction_grouper import FractionTracker, Fraction
    tracker = FractionTracker(poll_interval_seconds=1800)
    frac = Fraction(
        index=1, delivery_folders=[], beam_numbers=[],
        start_time=datetime(2026, 3, 20, 10, 0, 0),
        end_time=datetime(2026, 3, 20, 10, 0, 0),
        status="pending",
    )
    tracker.register("c1", tmp_path, [frac], 4, now=datetime(2026, 3, 20, 10, 0, 0))

    def rescan(_case_path):
        raise AssertionError("rescan should not be called before interval elapses")

    ready = tracker.check_pending(
        rescan=rescan,
        now=datetime(2026, 3, 20, 10, 10, 0),  # only 10 min later
    )
    assert ready == []
    assert tracker.pending_count == 1


def test_tracker_returns_ready_case_when_complete_fraction_appears(tmp_path):
    from src.core.fraction_grouper import FractionTracker, Fraction, CaseDeliveryResult
    tracker = FractionTracker(poll_interval_seconds=1800)
    frac = Fraction(
        index=1, delivery_folders=[], beam_numbers=[],
        start_time=datetime(2026, 3, 20, 10, 0, 0),
        end_time=datetime(2026, 3, 20, 10, 0, 0),
        status="pending",
    )
    tracker.register("c1", tmp_path, [frac], 4, now=datetime(2026, 3, 20, 10, 0, 0))

    complete_frac = Fraction(
        index=1,
        delivery_folders=[tmp_path / "f1", tmp_path / "f2", tmp_path / "f3", tmp_path / "f4"],
        beam_numbers=[1, 2, 3, 4],
        start_time=datetime(2026, 3, 20, 10, 0, 0),
        end_time=datetime(2026, 3, 20, 10, 10, 0),
        status="complete",
    )
    result = CaseDeliveryResult(
        beam_jobs=[{"beam_id": "c1_beam_1"}],
        delivery_records=[{"delivery_id": "d1"}],
        fractions=[complete_frac],
        status="ready",
    )

    def rescan(_case_path):
        return result

    ready = tracker.check_pending(
        rescan=rescan,
        now=datetime(2026, 3, 20, 11, 0, 0),  # 1 hr later > 30 min interval
    )
    assert len(ready) == 1
    assert ready[0].case_id == "c1"
    assert ready[0].result.status == "ready"
    assert tracker.pending_count == 0


def test_tracker_closes_as_partial_after_window_expires(tmp_path):
    from src.core.fraction_grouper import FractionTracker, Fraction, CaseDeliveryResult
    tracker = FractionTracker(poll_interval_seconds=1800)
    frac = Fraction(
        index=1, delivery_folders=[tmp_path / "f1"], beam_numbers=[1],
        start_time=datetime(2026, 3, 20, 10, 0, 0),
        end_time=datetime(2026, 3, 20, 10, 0, 0),
        status="pending",
    )
    tracker.register("c1", tmp_path, [frac], 4, now=datetime(2026, 3, 20, 10, 0, 0))

    partial_frac = Fraction(
        index=1, delivery_folders=[tmp_path / "f1"], beam_numbers=[1],
        start_time=datetime(2026, 3, 20, 10, 0, 0),
        end_time=datetime(2026, 3, 20, 10, 0, 0),
        status="partial",
    )
    result = CaseDeliveryResult(
        beam_jobs=[{"beam_id": "c1_beam_1"}],
        delivery_records=[{"delivery_id": "d1"}],
        fractions=[partial_frac],
        status="ready",
        pending_reason="partial_reference",
    )

    def rescan(_case_path):
        return result

    ready = tracker.check_pending(
        rescan=rescan,
        now=datetime(2026, 3, 20, 12, 0, 0),  # window closed
    )
    assert len(ready) == 1
    assert ready[0].result.pending_reason == "partial_reference"
    assert tracker.pending_count == 0


def test_tracker_remove_clears_entry(tmp_path):
    from src.core.fraction_grouper import FractionTracker, Fraction
    tracker = FractionTracker(poll_interval_seconds=1800)
    frac = Fraction(
        index=1, delivery_folders=[], beam_numbers=[],
        start_time=datetime(2026, 3, 20, 10, 0, 0),
        end_time=datetime(2026, 3, 20, 10, 0, 0),
        status="pending",
    )
    tracker.register("c1", tmp_path, [frac], 4, now=datetime(2026, 3, 20, 10, 0, 0))
    tracker.remove("c1")
    assert tracker.pending_count == 0
```

- [ ] **Step 8.2: Run the tests to verify they fail**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_grouper.py -v -k "tracker"
```
Expected: FAIL with `ImportError: cannot import name 'FractionTracker'`.

- [ ] **Step 8.3: Implement FractionTracker**

Append to `src/core/fraction_grouper.py`:

```python
# ---------------------------------------------------------------------------
# FractionTracker
# ---------------------------------------------------------------------------


class FractionTracker:
    """Polls cases whose fractions are still accumulating on disk.

    Designed for dependency injection: `check_pending` takes a `rescan`
    callback so the main loop can supply the full case-delivery pipeline
    without this module importing case_aggregator (which would be a cycle).
    """

    def __init__(self, poll_interval_seconds: int = 1800):
        self._poll_interval = timedelta(seconds=poll_interval_seconds)
        self._pending: Dict[str, PendingCase] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def register(
        self,
        case_id: str,
        case_path: Path,
        fractions: List[Fraction],
        expected_beam_count: int,
        now: Optional[datetime] = None,
    ) -> None:
        """Start tracking a case whose fraction is pending."""
        ts = now or datetime.now()
        self._pending[case_id] = PendingCase(
            case_id=case_id,
            case_path=case_path,
            fractions=fractions,
            expected_beam_count=expected_beam_count,
            first_seen=ts,
            last_checked=ts,
        )

    def remove(self, case_id: str) -> None:
        self._pending.pop(case_id, None)

    def check_pending(
        self,
        rescan,
        now: Optional[datetime] = None,
    ) -> List[ReadyCase]:
        """Poll tracked cases; return those whose grouping has resolved.

        Args:
            rescan: Callable(case_path) -> CaseDeliveryResult. Invoked for each
                case whose last_checked is older than poll_interval.
            now: Reference time (defaults to datetime.now()).

        Returns:
            List of ReadyCase for cases that have become resolvable. Those
            cases are removed from the tracker.
        """
        reference_now = now or datetime.now()
        ready: List[ReadyCase] = []
        resolved_ids: List[str] = []

        for case_id, pending in list(self._pending.items()):
            if (reference_now - pending.last_checked) < self._poll_interval:
                continue

            result = rescan(pending.case_path)
            if result is None:
                # rescan declined; leave tracker state alone but update last_checked
                pending.last_checked = reference_now
                continue

            if result.status == "ready":
                ready.append(ReadyCase(
                    case_id=case_id,
                    case_path=pending.case_path,
                    result=result,
                ))
                resolved_ids.append(case_id)
            else:
                # Still pending — bump last_checked
                pending.last_checked = reference_now
                pending.fractions = result.fractions

        for case_id in resolved_ids:
            self._pending.pop(case_id, None)
        return ready
```

- [ ] **Step 8.4: Run the tests to verify they pass**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_grouper.py -v
```
Expected: all tests PASS.

- [ ] **Step 8.5: Commit**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git add src/core/fraction_grouper.py tests/test_fraction_grouper.py && git commit -m "feat(fraction): add FractionTracker with dependency-injected rescan"
```

---

## Task 9: Refactor `prepare_case_delivery_data` to return `CaseDeliveryResult`

**Files:**
- Modify: `src/core/case_aggregator.py` (lines 201-374)
- Modify: `tests/test_case_aggregator.py` (existing tests need updating)

This is the biggest refactor. We take the existing logic and wrap fraction grouping around it, preserving the delivery-record output but scoping beam_jobs to the reference fraction.

- [ ] **Step 9.1: Update existing `test_prepare_beam_jobs_maps_timestamp_folders_to_treatment_beam_indices` test**

The existing test provides 3 folders within ~12 minutes (same fraction by our definition) and expects 3 beam jobs. It should continue to pass. No change needed.

- [ ] **Step 9.2: Update existing `test_prepare_beam_jobs_fails_when_planinfo_beam_numbers_are_duplicated` test**

Read the current test (lines 103-148 of `tests/test_case_aggregator.py`). It creates 2 folders with the same beam number (2, 2) but expects only 1 beam in the RT plan. Under new rules: these two folders are within 1 hour -> one fraction with 2 folders vs expected 1 -> anomaly status. No complete fraction -> with all windows closed (dates are 2025-04-24, year 2025, now is current time which is definitely past) -> promoted to partial reference... wait, but anomaly isn't eligible as partial either since it has MORE folders than expected.

Decision: in the absence of any fraction with `status in {"complete", "partial"}`, the result is "ready" with 0 beam jobs but with a pending_reason and an anomaly log. However, the existing test expects `len(beam_jobs) == 1` (the earliest delivery as a reference job). Since the old code picked "earliest delivery per unique treatment_beam_index" and the RT plan says 1 beam was expected, it returned 1 beam job covering both duplicates as delivery records.

Rewrite this test to reflect the new grouping behavior. Replace lines 103-148 of `tests/test_case_aggregator.py` with:

```python
@patch("src.core.case_aggregator.LoggerFactory.get_logger")
@patch("src.core.case_aggregator.DataIntegrityValidator")
def test_prepare_case_delivery_data_handles_duplicate_beam_numbers_as_anomaly(
    mock_validator_cls,
    mock_get_logger,
    tmp_path,
):
    """Duplicate beam numbers within 1 hour form a single anomaly fraction.

    Two folders with beam number 2, but RT plan expects only 1 beam.
    This is an anomaly fraction (2 folders > 1 expected). No complete
    fraction exists; the call still returns a CaseDeliveryResult with
    status="ready" and pending_reason indicating anomaly, but with
    empty beam_jobs (no safe reference). All deliveries are recorded
    for PTN analysis.
    """
    logger = MagicMock()
    mock_get_logger.return_value = logger

    case_path = tmp_path / "55061194"
    case_path.mkdir()
    (case_path / "RP.test.dcm").write_text("", encoding="ascii")

    for folder_name in ("2025042401440800", "2025042401501400"):
        beam_dir = case_path / folder_name
        beam_dir.mkdir()
        (beam_dir / "sample.ptn").write_text("", encoding="ascii")
        _write_planinfo(beam_dir, "55061194", 2)

    validator = MagicMock()
    validator.find_rtplan_file.return_value = case_path / "RP.test.dcm"
    validator.parse_rtplan_beam_count.return_value = 1
    validator.get_beam_information.return_value = {
        "patient_id": "55061194",
        "beams": [{"beam_name": "Beam_2", "beam_number": 2}],
    }
    mock_validator_cls.return_value = validator

    result = prepare_case_delivery_data("55061194", case_path, settings=MagicMock())

    # New return: CaseDeliveryResult
    assert result.status == "ready"
    assert len(result.delivery_records) == 2, "Both deliveries should be recorded for PTN"
    assert len(result.fractions) == 1
    assert result.fractions[0].status == "anomaly"
```

- [ ] **Step 9.3: Add a new test for multi-fraction scenario**

Append to `tests/test_case_aggregator.py`:

```python
@patch("src.core.case_aggregator.LoggerFactory.get_logger")
@patch("src.core.case_aggregator.DataIntegrityValidator")
def test_prepare_case_delivery_data_groups_multiple_fractions(
    mock_validator_cls,
    mock_get_logger,
    tmp_path,
):
    """Two distinct fractions (days apart) yield one complete reference + all deliveries."""
    logger = MagicMock()
    mock_get_logger.return_value = logger

    case_path = tmp_path / "04198922"
    case_path.mkdir()
    (case_path / "RP.test.dcm").write_text("", encoding="ascii")

    # Fraction 1: 2026-03-19 23:48 — 23:58 (4 beams)
    # Fraction 2: 2026-03-20 18:10 — 18:20 (4 beams)
    entries = [
        ("20260319234800", 2),
        ("20260319235200", 3),
        ("20260319235600", 4),
        ("20260319235800", 5),
        ("20260320181000", 5),
        ("20260320181400", 4),
        ("20260320181700", 3),
        ("20260320182000", 2),
    ]
    for ts, beam in entries:
        d = case_path / ts
        d.mkdir()
        (d / "sample.ptn").write_text("", encoding="ascii")
        _write_planinfo(d, "04198922", beam)

    validator = MagicMock()
    validator.find_rtplan_file.return_value = case_path / "RP.test.dcm"
    validator.parse_rtplan_beam_count.return_value = 4
    validator.get_beam_information.return_value = {
        "patient_id": "04198922",
        "beams": [
            {"beam_name": "Beam_2", "beam_number": 2},
            {"beam_name": "Beam_3", "beam_number": 3},
            {"beam_name": "Beam_4", "beam_number": 4},
            {"beam_name": "Beam_5", "beam_number": 5},
        ],
    }
    mock_validator_cls.return_value = validator

    result = prepare_case_delivery_data("04198922", case_path, settings=MagicMock())

    assert result.status == "ready"
    assert len(result.fractions) == 2
    assert all(f.status == "complete" for f in result.fractions)
    # Beam jobs come from fraction 1 only
    assert len(result.beam_jobs) == 4
    fraction_1_folders = {f.name for f in result.fractions[0].delivery_folders}
    beam_job_folders = {job["beam_path"].name for job in result.beam_jobs}
    assert beam_job_folders == fraction_1_folders, (
        f"Expected beam jobs from fraction 1 {fraction_1_folders!r}, got {beam_job_folders!r}"
    )
    # All 8 deliveries recorded for PTN
    assert len(result.delivery_records) == 8
    # fraction_index set correctly
    f1_records = [r for r in result.delivery_records if r["fraction_index"] == 1]
    f2_records = [r for r in result.delivery_records if r["fraction_index"] == 2]
    assert len(f1_records) == 4
    assert len(f2_records) == 4
```

- [ ] **Step 9.4: Update test_prepare_beam_jobs_maps test assertions for new return shape**

The `prepare_beam_jobs` wrapper continues to return a list (unchanged API), so the existing test is already correct. Verify by reading lines 20-67 — it only accesses `result[i]["beam_path"]` and `result[i]["beam_number"]`, which we'll preserve.

- [ ] **Step 9.5: Update `tests/test_case_aggregator.py` imports if needed**

Ensure the import at the top is:

```python
from src.core.case_aggregator import prepare_beam_jobs, prepare_case_delivery_data
```

No changes needed if already present.

- [ ] **Step 9.6: Run the updated tests — expect failures**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_case_aggregator.py -v
```
Expected: `test_prepare_case_delivery_data_handles_duplicate_beam_numbers_as_anomaly` and `test_prepare_case_delivery_data_groups_multiple_fractions` FAIL because the current implementation returns a tuple, not `CaseDeliveryResult`.

- [ ] **Step 9.7: Refactor `prepare_case_delivery_data`**

Replace the entire body of `prepare_case_delivery_data` in `src/core/case_aggregator.py` (lines 201-374). Keep all the existing helper functions above (`_parse_required_planinfo`, `_match_metadata_by_raw_beam_number`, etc.) unchanged.

```python
def prepare_case_delivery_data(
    case_id: str, case_path: Path, settings
) -> "CaseDeliveryResult":
    """Return reference beam jobs plus all mapped delivery records for a case.

    Groups delivery folders into fractions using a 1-hour time window and the
    DICOM-expected beam count. The earliest complete fraction supplies the
    beam_jobs (reference deliveries for moqui_SMC simulation). Every fraction
    contributes delivery records (for PTN analysis), each tagged with a
    fraction_index.

    Status semantics:
      - "ready": result has beam_jobs (or will have anomaly-only fractions,
         in which case beam_jobs is [] but PTN still proceeds).
      - "pending": at least one fraction is still accumulating; caller should
         register the case with FractionTracker and try again later.
    """
    from src.core.fraction_grouper import (
        CaseDeliveryResult,
        get_fraction_event_logger,
        group_deliveries_into_fractions,
        log_fraction_event,
        select_reference_fraction,
    )

    logger = LoggerFactory.get_logger(f"dispatcher_{case_id}")
    event_logger = get_fraction_event_logger()

    logger.info(f"Scanning for beams for case: {case_id}")
    validator = DataIntegrityValidator(logger)

    rtplan_path = validator.find_rtplan_file(case_path)
    if not rtplan_path:
        logger.error(f"No RT Plan file found in case directory: {case_path}")
        return CaseDeliveryResult(
            beam_jobs=[], delivery_records=[], fractions=[], status="ready",
            pending_reason="no_rtplan",
        )

    try:
        expected_beam_count = validator.parse_rtplan_beam_count(rtplan_path)
        logger.info(f"RT Plan indicates {expected_beam_count} treatment beams for case {case_id}")
    except ProcessingError as e:
        logger.error(f"Failed to parse RT Plan file: {str(e)}")
        return CaseDeliveryResult(
            beam_jobs=[], delivery_records=[], fractions=[], status="ready",
            pending_reason="rtplan_parse_error",
        )

    if expected_beam_count == 0:
        logger.warning(f"RT plan indicates 0 beams for case {case_id}")
        return CaseDeliveryResult(
            beam_jobs=[], delivery_records=[], fractions=[], status="ready",
            pending_reason="zero_expected_beams",
        )

    dicom_parent_dir = rtplan_path.parent
    all_subdirs = [d for d in case_path.iterdir() if d.is_dir()]

    delivery_folders: List[Path] = []
    for subdir in all_subdirs:
        if subdir == dicom_parent_dir:
            continue
        has_ptn_files = list(subdir.glob("*.ptn"))
        has_planinfo = (subdir / "PlanInfo.txt").exists()
        if has_ptn_files or has_planinfo:
            delivery_folders.append(subdir)

    delivery_folders.sort(key=lambda p: p.name)
    if not delivery_folders:
        logger.error(f"No delivery folders found in case directory: {case_path}")
        return CaseDeliveryResult(
            beam_jobs=[], delivery_records=[], fractions=[], status="ready",
            pending_reason="no_delivery_folders",
        )

    beam_info = validator.get_beam_information(case_path)
    treatment_beams = beam_info.get("beams", [])
    rtplan_patient_id = str(beam_info.get("patient_id", "")).strip()
    if len(treatment_beams) != expected_beam_count:
        logger.error(
            "Beam metadata count mismatch during delivery preparation",
            {"expected": expected_beam_count, "metadata_count": len(treatment_beams)},
        )
        return CaseDeliveryResult(
            beam_jobs=[], delivery_records=[], fractions=[], status="ready",
            pending_reason="beam_metadata_mismatch",
        )

    # ---- Fraction grouping ----
    fractions = group_deliveries_into_fractions(
        delivery_folders, expected_beam_count=expected_beam_count
    )

    # If any fraction is still accumulating, bail out — caller will poll.
    if any(f.status == "pending" for f in fractions):
        return CaseDeliveryResult(
            beam_jobs=[], delivery_records=[], fractions=fractions, status="pending",
            pending_reason="fraction_accumulating",
        )

    # Log anomalies (does not block processing)
    for fraction in fractions:
        if fraction.status == "anomaly":
            log_fraction_event(
                logger, event_logger,
                event="anomaly_fraction",
                message="Anomaly fraction — more deliveries than expected beams",
                context={
                    "case_id": case_id,
                    "fraction_index": fraction.index,
                    "expected_beam_count": expected_beam_count,
                    "delivered_beam_count": len(fraction.delivery_folders),
                    "beam_numbers": fraction.beam_numbers,
                    "fraction_start": fraction.start_time.isoformat(),
                    "fraction_end": fraction.end_time.isoformat(),
                },
            )

    # ---- Reference fraction selection ----
    reference_fraction = select_reference_fraction(fractions)
    if reference_fraction is None:
        # Fall back to earliest partial fraction as reference, with special log
        partial_fractions = [f for f in fractions if f.status == "partial"]
        if partial_fractions:
            reference_fraction = partial_fractions[0]
            log_fraction_event(
                logger, event_logger,
                event="partial_reference_used",
                message="Partial fraction used as reference — not all expected beams delivered",
                context={
                    "case_id": case_id,
                    "fraction_index": reference_fraction.index,
                    "expected_beam_count": expected_beam_count,
                    "delivered_beam_count": len(reference_fraction.delivery_folders),
                    "beam_numbers": reference_fraction.beam_numbers,
                    "fraction_start": reference_fraction.start_time.isoformat(),
                    "fraction_end": reference_fraction.end_time.isoformat(),
                },
            )
        # else: reference stays None — only anomaly fractions exist, no safe ref.

    # ---- Map every delivery folder (all fractions) for PTN + beam_jobs for reference ----
    delivery_records: List[Dict[str, Any]] = []
    reference_by_treatment_index: Dict[int, Dict[str, Any]] = {}
    unresolved_folders: List[str] = []

    # Build a lookup: folder -> fraction_index
    folder_to_fraction: Dict[Path, int] = {}
    for fraction in fractions:
        for folder in fraction.delivery_folders:
            folder_to_fraction[folder] = fraction.index

    for delivery_path in delivery_folders:
        planinfo = _parse_required_planinfo(delivery_path)
        if not planinfo:
            logger.error(
                "Missing or invalid required PlanInfo data",
                {"delivery_folder": delivery_path.name},
            )
            return CaseDeliveryResult(
                beam_jobs=[], delivery_records=[], fractions=fractions, status="ready",
                pending_reason="planinfo_invalid",
            )

        if rtplan_patient_id and planinfo["patient_id"] != rtplan_patient_id:
            logger.error(
                "PlanInfo patient ID does not match RT plan patient ID",
                {
                    "delivery_folder": delivery_path.name,
                    "planinfo_patient_id": planinfo["patient_id"],
                    "rtplan_patient_id": rtplan_patient_id,
                },
            )
            return CaseDeliveryResult(
                beam_jobs=[], delivery_records=[], fractions=fractions, status="ready",
                pending_reason="patient_id_mismatch",
            )

        raw_beam_number = int(planinfo["beam_number"])
        matched_metadata = _match_metadata_by_raw_beam_number(raw_beam_number, treatment_beams)
        if not matched_metadata:
            matched_metadata = _map_beam_folder_to_metadata(delivery_path, treatment_beams)

        treatment_beam_index = (
            _resolve_treatment_beam_index(matched_metadata, treatment_beams)
            if matched_metadata
            else None
        )
        if not matched_metadata or treatment_beam_index is None:
            unresolved_folders.append(delivery_path.name)
            continue

        delivery_timestamp = _parse_delivery_timestamp(delivery_path)
        if delivery_timestamp is None:
            logger.error(
                "Could not determine delivery timestamp",
                {"delivery_folder": delivery_path.name},
            )
            return CaseDeliveryResult(
                beam_jobs=[], delivery_records=[], fractions=fractions, status="ready",
                pending_reason="timestamp_parse_error",
            )

        beam_id = f"{case_id}_beam_{int(treatment_beam_index)}"
        fraction_index = folder_to_fraction.get(delivery_path)
        record = {
            "delivery_id": _delivery_id(case_id, delivery_path),
            "beam_id": beam_id,
            "delivery_path": delivery_path,
            "delivery_timestamp": delivery_timestamp.isoformat(),
            "delivery_date": delivery_timestamp.strftime("%Y-%m-%d"),
            "raw_beam_number": raw_beam_number,
            "treatment_beam_index": int(treatment_beam_index),
            "is_reference_delivery": False,
            "fraction_index": fraction_index,
        }
        delivery_records.append(record)

        # Only build reference_by_treatment_index for folders in reference_fraction
        if reference_fraction is not None and delivery_path in reference_fraction.delivery_folders:
            existing = reference_by_treatment_index.get(int(treatment_beam_index))
            if existing is None or delivery_timestamp < existing["timestamp"]:
                reference_by_treatment_index[int(treatment_beam_index)] = {
                    "beam_id": beam_id,
                    "beam_path": delivery_path,
                    "beam_number": int(treatment_beam_index),
                    "timestamp": delivery_timestamp,
                }

    if unresolved_folders:
        logger.error(
            "Failed to map one or more delivery folders to RT Plan beam metadata",
            {"unresolved_folders": unresolved_folders},
        )
        return CaseDeliveryResult(
            beam_jobs=[], delivery_records=[], fractions=fractions, status="ready",
            pending_reason="unresolved_folders",
        )

    beam_jobs = sorted(
        (
            {
                "beam_id": info["beam_id"],
                "beam_path": info["beam_path"],
                "beam_number": info["beam_number"],
            }
            for info in reference_by_treatment_index.values()
        ),
        key=lambda job: job["beam_number"],
    )

    reference_ids = {job["beam_id"]: str(job["beam_path"]) for job in beam_jobs}
    for record in delivery_records:
        if reference_ids.get(record["beam_id"]) == str(record["delivery_path"]):
            record["is_reference_delivery"] = True

    delivery_records.sort(
        key=lambda record: (
            record["delivery_timestamp"],
            record["treatment_beam_index"] or 0,
            str(record["delivery_path"]),
        )
    )
    logger.info(
        "Prepared reference beams and delivery records",
        {
            "case_id": case_id,
            "reference_beams": len(beam_jobs),
            "deliveries": len(delivery_records),
            "fractions": len(fractions),
        },
    )
    return CaseDeliveryResult(
        beam_jobs=beam_jobs,
        delivery_records=delivery_records,
        fractions=fractions,
        status="ready",
    )
```

- [ ] **Step 9.8: Update `prepare_beam_jobs` to unpack the new return**

Replace lines 460-474 in `src/core/case_aggregator.py`:

```python
def prepare_beam_jobs(
    case_id: str, case_path: Path, settings
) -> List[Dict[str, Any]]:
    """Return unique reference beam jobs for dose computation.

    When a case contains repeated daily deliveries, the earliest delivery for each
    treatment beam becomes the reference beam job for moqui_SMC.
    """
    try:
        result = prepare_case_delivery_data(case_id, case_path, settings)
        return result.beam_jobs
    except Exception as e:
        logger = LoggerFactory.get_logger(f"dispatcher_{case_id}")
        logger.error("Failed to prepare beam jobs", {"case_id": case_id, "error": str(e)})
        return []
```

- [ ] **Step 9.9: Run case_aggregator tests — expect passing**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_case_aggregator.py -v
```
Expected: all PASS, including the new multi-fraction test.

- [ ] **Step 9.10: Run the full fraction_grouper suite to ensure no regression**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_fraction_grouper.py -v
```
Expected: all PASS.

- [ ] **Step 9.11: Commit**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git add src/core/case_aggregator.py tests/test_case_aggregator.py && git commit -m "refactor(case_aggregator): return CaseDeliveryResult with fraction grouping"
```

---

## Task 10: Update `run_case_level_ptn_analysis` for the new return shape

**Files:**
- Modify: `src/core/dispatcher.py` (around line 546-553)

- [ ] **Step 10.1: Locate the existing call site**

Read lines 540-560 of `src/core/dispatcher.py`:

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && sed -n '540,560p' src/core/dispatcher.py
```
Expected: shows the `beam_jobs, delivery_records = prepare_case_delivery_data(...)` line.

- [ ] **Step 10.2: Update the unpacking**

Edit `src/core/dispatcher.py`. Replace the block around line 546-553:

```python
    with get_db_session(settings, logger, handler_name="CsvInterpreter") as case_repo:
        deliveries = list(case_repo.get_deliveries_for_case(case_id) or [])
        if not deliveries:
            result = prepare_case_delivery_data(case_id, case_path, settings)
            beam_jobs = result.beam_jobs
            delivery_records = result.delivery_records
            if beam_jobs:
                case_repo.create_case_with_beams(case_id, str(case_path), beam_jobs)
                case_repo.create_or_update_deliveries(case_id, delivery_records)
                deliveries = list(case_repo.get_deliveries_for_case(case_id) or [])
```

- [ ] **Step 10.3: Run existing PTN checker tests to verify no regression**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_ptn_checker_integration.py tests/test_ptn_checker_workflow.py -v
```
Expected: all PASS.

- [ ] **Step 10.4: Commit**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git add src/core/dispatcher.py && git commit -m "refactor(dispatcher): use CaseDeliveryResult in run_case_level_ptn_analysis"
```

---

## Task 11: Wire FractionTracker into `main.py`

**Files:**
- Modify: `main.py` (MQIApplication class)
- Test: `tests/test_main_fraction_integration.py` (NEW)

- [ ] **Step 11.1: Write failing integration test for pending→ready flow**

Create `tests/test_main_fraction_integration.py`:

```python
"""Integration tests for FractionTracker wiring in MQIApplication."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.fraction_grouper import (
    CaseDeliveryResult,
    Fraction,
    FractionTracker,
    ReadyCase,
)


def test_fraction_tracker_registers_pending_and_yields_ready_on_rescan(tmp_path):
    """Simulates the full register -> check_pending -> rescan loop."""
    tracker = FractionTracker(poll_interval_seconds=1)

    pending_fraction = Fraction(
        index=1, delivery_folders=[], beam_numbers=[],
        start_time=datetime(2026, 3, 20, 10, 0, 0),
        end_time=datetime(2026, 3, 20, 10, 0, 0),
        status="pending",
    )
    tracker.register(
        "caseX", tmp_path, [pending_fraction], expected_beam_count=4,
        now=datetime(2026, 3, 20, 10, 0, 0),
    )
    assert tracker.pending_count == 1

    # First check — immediately after register, interval not elapsed.
    ready = tracker.check_pending(
        rescan=lambda _: None,
        now=datetime(2026, 3, 20, 10, 0, 0),
    )
    assert ready == []
    assert tracker.pending_count == 1

    # Second check — 2 seconds later, interval elapsed. Rescan returns ready.
    ready_result = CaseDeliveryResult(
        beam_jobs=[{"beam_id": "caseX_beam_1"}],
        delivery_records=[{"delivery_id": "d1"}],
        fractions=[Fraction(
            index=1,
            delivery_folders=[tmp_path / "f1"],
            beam_numbers=[1, 2, 3, 4],
            start_time=datetime(2026, 3, 20, 10, 0, 0),
            end_time=datetime(2026, 3, 20, 10, 10, 0),
            status="complete",
        )],
        status="ready",
    )
    ready = tracker.check_pending(
        rescan=lambda _: ready_result,
        now=datetime(2026, 3, 20, 10, 0, 2),
    )
    assert len(ready) == 1
    assert ready[0].case_id == "caseX"
    assert tracker.pending_count == 0
```

- [ ] **Step 11.2: Run the test — expect PASS (just uses already-built APIs)**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_main_fraction_integration.py -v
```
Expected: PASS (this is a sanity test that our API composes correctly).

- [ ] **Step 11.3: Add FractionTracker to `MQIApplication.__init__`**

Edit `main.py`. At the top, add the import (insert after existing `from src.core.case_aggregator import prepare_case_delivery_data`):

```python
from src.core.case_aggregator import prepare_case_delivery_data
from src.core.fraction_grouper import FractionTracker, ReadyCase
```

In `MQIApplication.__init__` (around lines 60-80), add after `self.service_monitor_thread`:

```python
        self.fraction_tracker = FractionTracker(poll_interval_seconds=1800)
```

- [ ] **Step 11.4: Refactor `_discover_beams` to return `CaseDeliveryResult`**

Replace the method (currently lines 257-286 of `main.py`):

```python
    def _discover_beams(self, case_id: str, case_path: Path, case_repo: CaseRepository):
        """Discovers beams and validates data transfer completion.

        Returns a CaseDeliveryResult. Caller inspects .status:
          - "ready": .beam_jobs can be used (may be empty for anomaly-only or
            error states with a pending_reason).
          - "pending": register with fraction_tracker, do not process yet.
        """
        self.logger.info(
            f"Discovering beams and validating data transfer for case: {case_id}"
        )
        result = prepare_case_delivery_data(case_id, case_path, self.settings)

        if result.status == "pending":
            # Do not persist yet; tracker will retry.
            return result

        if not result.beam_jobs:
            case_repo.add_case(case_id, case_path)
            self.logger.error(
                f"No beams found or data transfer incomplete for case {case_id}",
                {"pending_reason": result.pending_reason},
            )
            case_repo.fail_case(
                case_id,
                f"No beams found or data transfer incomplete ({result.pending_reason or 'unknown'})",
            )
            return result

        case_repo.create_case_with_beams(case_id, str(case_path), result.beam_jobs)
        case_repo.create_or_update_deliveries(case_id, result.delivery_records)
        self.logger.info(
            f"Created {len(result.beam_jobs)} beam records and "
            f"{len(result.delivery_records)} delivery records in DB for case {case_id}"
        )
        return result
```

- [ ] **Step 11.5: Update `_process_new_case` to handle pending status**

Replace the method body (lines 429-485 of `main.py`). Preserve the existing flow, but add pending-status branching after `_discover_beams`:

```python
    def _process_new_case(
        self,
        case_data: dict,
        executor: ProcessPoolExecutor,
        active_futures: Dict,
        pending_beams_by_case: Dict,
    ) -> None:
        """Processes a new case from the queue."""
        case_id = case_data["case_id"]
        case_path = Path(case_data["case_path"])
        queue_reason = case_data.get("reason")
        self.logger.info(f"Processing new case: {case_id}")

        with get_db_session(self.settings, self.logger) as case_repo:
            if queue_reason == "ptn_checker":
                run_case_level_ptn_analysis(case_id, case_path, self.settings)
                return

            # Step 1: Discover beams with fraction grouping
            result = self._discover_beams(case_id, case_path, case_repo)

            if result.status == "pending":
                self.logger.info(
                    f"Case {case_id} has pending fractions — registering with tracker",
                    {"case_id": case_id, "fractions": len(result.fractions)},
                )
                self.fraction_tracker.register(
                    case_id=case_id,
                    case_path=case_path,
                    fractions=result.fractions,
                    expected_beam_count=len(result.fractions[0].delivery_folders)
                        if result.fractions else 0,
                )
                return

            if not result.beam_jobs:
                return

            self._continue_processing_ready_case(
                case_id, case_path, result, case_repo,
                executor, active_futures, pending_beams_by_case,
            )

    def _continue_processing_ready_case(
        self,
        case_id: str,
        case_path: Path,
        result,
        case_repo: CaseRepository,
        executor: ProcessPoolExecutor,
        active_futures: Dict,
        pending_beams_by_case: Dict,
    ) -> None:
        """Shared continuation from _discover_beams success through to dispatch."""
        beam_jobs = result.beam_jobs

        # Step 2: CSV interpreting
        if not self._run_csv_interpreting(case_id, case_path, case_repo):
            return

        # Step 3: TPS generation
        gpu_assignments = self._run_tps_generation(
            case_id, case_path, len(beam_jobs), case_repo
        )
        if gpu_assignments is None:
            return
        if len(gpu_assignments) == 0:
            return

        # Step 4: Dispatch workers
        self._dispatch_workers(
            case_id, case_path, beam_jobs, gpu_assignments, case_repo,
            executor, active_futures, pending_beams_by_case,
        )
```

- [ ] **Step 11.6: Add `_process_ready_case` for tracker-resolved cases**

Add this new method to `MQIApplication` (right after `_continue_processing_ready_case`):

```python
    def _process_ready_case(
        self,
        ready: ReadyCase,
        executor: ProcessPoolExecutor,
        active_futures: Dict,
        pending_beams_by_case: Dict,
    ) -> None:
        """Process a case that FractionTracker just resolved.

        Skips fraction grouping (already done) but runs the full pipeline:
        persist beams/deliveries, CSV interpret, TPS, dispatch.
        """
        case_id = ready.case_id
        case_path = ready.case_path
        self.logger.info(f"Fraction tracker resolved pending case: {case_id}")

        with get_db_session(self.settings, self.logger) as case_repo:
            if not ready.result.beam_jobs:
                case_repo.add_case(case_id, case_path)
                case_repo.fail_case(
                    case_id,
                    f"Fraction grouping yielded no beams ({ready.result.pending_reason or 'unknown'})",
                )
                return

            case_repo.create_case_with_beams(case_id, str(case_path), ready.result.beam_jobs)
            case_repo.create_or_update_deliveries(case_id, ready.result.delivery_records)
            self._continue_processing_ready_case(
                case_id, case_path, ready.result, case_repo,
                executor, active_futures, pending_beams_by_case,
            )
```

- [ ] **Step 11.7: Add tracker polling to `run_worker_loop`**

Edit `run_worker_loop` (around lines 487-541). After the existing `try_allocate_pending_beams` call, add tracker polling:

```python
                    # Poll FractionTracker for cases whose fractions resolved
                    if self.fraction_tracker.pending_count > 0:
                        def _rescan(case_path: Path):
                            case_id, _ = case_path.name, case_path
                            return prepare_case_delivery_data(
                                case_id, case_path, self.settings
                            )

                        ready_cases = self.fraction_tracker.check_pending(rescan=_rescan)
                        for ready in ready_cases:
                            self._process_ready_case(
                                ready, executor, active_futures, pending_beams_by_case,
                            )
```

The full block looks like:

```python
                    # Periodically try to allocate GPUs for pending beams even if
                    # no worker completed during this iteration.
                    if pending_beams_by_case:
                        try_allocate_pending_beams(
                            pending_beams_by_case,
                            executor,
                            active_futures,
                            self.settings,
                            self.logger,
                        )

                    # Poll FractionTracker for cases whose fractions resolved
                    if self.fraction_tracker.pending_count > 0:
                        def _rescan(case_path: Path):
                            case_id = case_path.name
                            return prepare_case_delivery_data(
                                case_id, case_path, self.settings
                            )

                        ready_cases = self.fraction_tracker.check_pending(rescan=_rescan)
                        for ready in ready_cases:
                            self._process_ready_case(
                                ready, executor, active_futures, pending_beams_by_case,
                            )
```

- [ ] **Step 11.8: Run existing main loop tests**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest tests/test_main_loop.py tests/test_main_fraction_integration.py -v
```
Expected: all PASS. If `test_main_loop.py` fails due to MQIApplication signature changes, read the failure and adjust the test's MagicMock expectations — main.py's public contract hasn't changed.

- [ ] **Step 11.9: Commit**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git add main.py tests/test_main_fraction_integration.py && git commit -m "feat(main): integrate FractionTracker into worker loop"
```

---

## Task 12: Full regression test run

**Files:** none — verification only.

- [ ] **Step 12.1: Run the complete test suite**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -m pytest -v
```
Expected: all tests PASS.

- [ ] **Step 12.2: If any existing test fails, investigate**

Read the failure. Do NOT modify tests to make them pass unless the test is genuinely asserting old behavior that we intentionally changed (only Task 9 did that). For any failure not covered by our refactor, treat it as a regression and fix the code.

- [ ] **Step 12.3: Verify the fraction events log file would be created**

Run a quick smoke test:
```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && python -c "
from src.infrastructure.logging_handler import LoggerFactory
LoggerFactory.configure({'log_dir': '/tmp/fraction-smoke', 'log_level': 'INFO', 'structured_logging': True})
from src.core.fraction_grouper import get_fraction_event_logger, log_fraction_event
l = get_fraction_event_logger()
log_fraction_event(None, l, 'smoke', 'smoke test', {'case_id': 'x'})
import os
print('fraction_events.log exists:', os.path.exists('/tmp/fraction-smoke/fraction_events.log'))
"
```
Expected: `fraction_events.log exists: True`.

- [ ] **Step 12.4: Final commit (empty if nothing to commit)**

```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git status
```
If clean, task complete. If any pending changes, review and commit with a clear message.

---

## Task 13: Cleanup — remove worktree if user decides to merge from main repo

**Files:** none — operational cleanup.

This task is deferred to the user's discretion. Options:

1. **Leave the worktree in place** until the user reviews the feature branch.
2. **Push the branch and remove the worktree**:
```bash
cd /mnt/c/MOQUI_SMC/mqi_communicator/.worktree/fraction-grouping && git push -u origin feature/fraction-grouping
cd /mnt/c/MOQUI_SMC/mqi_communicator && git worktree remove .worktree/fraction-grouping
```

Do NOT execute this task automatically. Prompt the user before cleanup.

---

## Spec Coverage Checklist

| Spec section | Task(s) |
|---|---|
| 1. Fraction Grouping Algorithm (`group_deliveries_into_fractions`, `select_reference_fraction`) | 5, 6 |
| 2. Changes to `prepare_case_delivery_data` (CaseDeliveryResult) | 9 |
| 3. FractionTracker | 4 (dataclass), 8 (class) |
| 4. DeliveryData + DB `fraction_index` | 1, 2, 3 |
| 5. Dedicated fraction events log | 7 |
| 6. Integration in main.py | 11 |
| 7. Files Changed list | All |
| 8. Test Plan items | 5, 6, 8, 9, 11 |

All spec sections covered.
