# Fraction Grouping Implementation Spec

## Overview

Add fraction-grouping logic to `mqi_communicator` so that delivery folders on disk
are correctly partitioned into treatment fractions before processing. The first
complete fraction provides reference beams for moqui_SMC simulation; all fractions
are processed by PTN checker. Real-time waiting support polls for incomplete
fractions every 30 minutes until the 1-hour delivery window closes.

## Decision Record

| Question | Decision |
|----------|----------|
| Batch vs real-time | Both — single code path via fraction grouping |
| Which fraction for simulation | First complete fraction only |
| Which fractions for PTN | All fractions |
| Polling mechanism | Dedicated `FractionTracker` with 30-min interval |
| Initial case routing | Always through fraction grouping (no batch/RT special-casing) |
| Partial fraction handling | Auto-process with warning + dedicated log file |

---

## 1. Fraction Grouping Algorithm

**New module:** `src/core/fraction_grouper.py`

### Data Structures

```python
@dataclass
class Fraction:
    index: int                          # 1-based fraction number
    delivery_folders: List[Path]        # sorted by timestamp
    beam_numbers: List[int]             # DICOM_BEAM_NUMBER per folder
    start_time: datetime                # earliest delivery timestamp
    end_time: datetime                  # latest delivery timestamp
    status: str                         # "complete", "partial", "pending", "anomaly"
```

### `group_deliveries_into_fractions(delivery_folders, expected_beam_count, time_window=timedelta(hours=1)) -> List[Fraction]`

Pure function. No DB access, no logging.

1. Parse `TCSC_IRRAD_DATETIME` from each folder's `PlanInfo.txt` (reuses `_parse_delivery_timestamp`)
2. Sort all folders by timestamp ascending
3. Greedy grouping: start a new fraction when `folder.timestamp - fraction_start > time_window`
4. Assign status per fraction:
   - `len == expected_beam_count` -> `"complete"`
   - `len < expected_beam_count` and window closed -> `"partial"`
   - `len < expected_beam_count` and window still open -> `"pending"`
   - `len > expected_beam_count` -> `"anomaly"`
5. Duplicate beam numbers within a fraction are flagged but do not split the fraction

### `select_reference_fraction(fractions: List[Fraction]) -> Optional[Fraction]`

Returns the earliest fraction with status `"complete"`. Returns `None` if no
complete fraction exists.

---

## 2. Changes to `prepare_case_delivery_data`

### New Return Type

```python
@dataclass
class CaseDeliveryResult:
    beam_jobs: List[Dict[str, Any]]           # reference fraction beams for simulation
    delivery_records: List[Dict[str, Any]]     # all fractions for PTN
    fractions: List[Fraction]                  # full grouping metadata
    status: str                                # "ready" (complete or partial ref), "pending" (waiting)
    pending_reason: Optional[str]              # human-readable if status != "ready"
```

### Behavior

1. Call `group_deliveries_into_fractions()` on all delivery folders
2. Call `select_reference_fraction()` to get the simulation fraction
3. If no complete fraction and all windows closed -> use earliest partial + special log. Status = `"ready"`
4. If any fraction is `"pending"` -> status = `"pending"`, caller routes to `FractionTracker`
5. Build `beam_jobs` from reference fraction only
6. Build `delivery_records` from all fractions (each record tagged with `fraction_index`)
7. Unique beam count validation applies within the reference fraction, not globally

### Callers Updated

- `prepare_beam_jobs` -> returns `result.beam_jobs`
- `_discover_beams` in `main.py` -> handles `CaseDeliveryResult`
- `run_case_level_ptn_analysis` in `dispatcher.py` -> unpacks `CaseDeliveryResult`

---

## 3. FractionTracker

Co-located in `src/core/fraction_grouper.py`.

### Data Structures

```python
@dataclass
class PendingCase:
    case_id: str
    case_path: Path
    fractions: List[Fraction]
    expected_beam_count: int
    first_seen: datetime
    last_checked: datetime

@dataclass
class ReadyCase:
    case_id: str
    case_path: Path
    result: CaseDeliveryResult
```

### `FractionTracker`

```python
class FractionTracker:
    def __init__(self, poll_interval_seconds: int = 1800)  # 30 min

    def register(case_id, case_path, fractions, expected_beam_count)
    def check_pending(settings) -> List[ReadyCase]
    def remove(case_id)
    @property
    def pending_count -> int
```

**`check_pending` logic per tracked case:**

1. If `now - last_checked < poll_interval` -> skip
2. Re-scan delivery folders on disk
3. Re-run `group_deliveries_into_fractions()`
4. Complete fraction found -> remove, return as `ReadyCase`
5. All windows closed, no complete fraction -> remove, return as `ReadyCase` with partial status + special log
6. Otherwise update `last_checked`, keep polling

---

## 4. Model and Database Changes

### DeliveryData

Add field: `fraction_index: Optional[int] = None`

### Database Migration

`ALTER TABLE deliveries ADD COLUMN fraction_index INTEGER` in `init_db()`, guarded
by column-existence check (same pattern as existing migrations).

No new tables. Fractions are identified by the `fraction_index` value on delivery
records; the `Fraction` dataclass is in-memory only.

---

## 5. Dedicated Fraction Event Log

All fraction warnings log to both the caller's logger and a dedicated
`fraction_events` logger -> `logs/fraction_events.log`.

Uses existing `LoggerFactory.get_logger("fraction_events")` which automatically
creates a separate log file per logger name. No logging infrastructure changes needed.

### Events logged:

| Event | Level | Key fields |
|-------|-------|------------|
| Partial fraction used as reference | WARNING | case_id, fraction_index, expected vs delivered count, beam numbers, timestamps |
| Anomaly fraction (count > expected) | WARNING | case_id, fraction_index, expected vs delivered count, duplicate beams |
| Duplicate beam number within fraction | WARNING | case_id, fraction_index, beam_number, folder paths |
| Fraction timeout closure from tracker | WARNING | case_id, fraction_index, time elapsed, beams present |

---

## 6. Integration in `main.py`

### `MQIApplication.__init__`

```python
self.fraction_tracker = FractionTracker(poll_interval_seconds=1800)
```

### `_process_new_case`

After `prepare_case_delivery_data` returns:
- `status="ready"` -> proceed to CSV interpreting as before
- `status="pending"` -> `self.fraction_tracker.register(...)`, return early

### New `_process_ready_case(ready: ReadyCase, ...)`

Picks up from beam discovery with pre-computed `CaseDeliveryResult` and continues
through CSV interpreting -> TPS generation -> dispatch. Skips fraction grouping.

### `run_worker_loop`

Each iteration, after queue check and worker monitoring:

```python
ready_cases = self.fraction_tracker.check_pending(self.settings)
for ready in ready_cases:
    self._process_ready_case(ready, executor, active_futures, pending_beams_by_case)
```

---

## 7. Files Changed

| File | Change |
|------|--------|
| `src/core/fraction_grouper.py` | **NEW** — grouping algorithm, dataclasses, FractionTracker |
| `src/core/case_aggregator.py` | Refactor `prepare_case_delivery_data` to use fraction grouping, new return type |
| `src/domain/models.py` | Add `fraction_index` to `DeliveryData` |
| `src/database/connection.py` | Add `fraction_index` column migration |
| `src/core/dispatcher.py` | Update `run_case_level_ptn_analysis` for `CaseDeliveryResult` |
| `main.py` | `FractionTracker` integration, `_process_ready_case`, loop changes |
| `tests/test_fraction_grouper.py` | **NEW** — unit tests for grouping, selection, tracker |

## 8. Test Plan

### Unit Tests (`test_fraction_grouper.py`)

- Single fraction, all beams present -> one complete fraction
- Multiple fractions separated by hours -> correct grouping
- Midnight crossing within fraction -> same fraction
- Partial fraction (missing beams, window closed) -> status "partial"
- Pending fraction (window still open) -> status "pending"
- Anomaly fraction (more folders than beams) -> status "anomaly"
- Duplicate beam number within fraction -> warning, no split
- `select_reference_fraction` picks earliest complete
- `select_reference_fraction` returns None when no complete fraction
- Tracker: register + check before interval -> no result
- Tracker: register + check after interval with new folders -> ready
- Tracker: window closes with partial -> ready with partial status

### Integration Tests (updates to `test_case_aggregator.py`)

- `prepare_case_delivery_data` with multi-fraction data returns correct `CaseDeliveryResult`
- Reference beam jobs come from first complete fraction only
- Delivery records span all fractions with correct `fraction_index`
- Existing tests continue to pass (single-fraction cases work unchanged)
