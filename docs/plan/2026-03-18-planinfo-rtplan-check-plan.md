# PlanInfo RT Plan Check Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Hard-fail beam preparation when `PlanInfo.txt` contradicts the RT plan patient or beam numbering data.

**Architecture:** Parse required `PlanInfo.txt` fields in `case_aggregator`, compare them against RT-plan beam metadata from `DataIntegrityValidator`, and persist normalized treatment-beam indices only after the raw DICOM mapping passes validation. Keep the validation local to beam preparation so downstream TPS generation continues to consume normalized indices.

**Tech Stack:** Python, pytest, pydicom-backed RT plan metadata

---

### Task 1: Add failing tests for strict PlanInfo validation

**Files:**
- Modify: `tests/test_case_aggregator.py`

**Step 1: Write the failing tests**

- Add a case where `PlanInfo.txt` patient ID differs from RT-plan patient ID and assert `prepare_beam_jobs()` returns `[]`
- Add a case where two folders claim the same `DICOM_BEAM_NUMBER` and assert `prepare_beam_jobs()` returns `[]`

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_case_aggregator.py -q`
Expected: FAIL on the new assertions

### Task 2: Implement minimal PlanInfo parsing and validation

**Files:**
- Modify: `src/core/case_aggregator.py`

**Step 1: Parse required PlanInfo fields**

- Add a helper that reads `DICOM_PATIENT_ID` and `DICOM_BEAM_NUMBER`
- Return structured data or `None` on missing/invalid required fields

**Step 2: Replace fallback matching for PlanInfo-backed folders**

- Use raw `DICOM_BEAM_NUMBER` as the primary match key
- Validate uniqueness across beam folders
- Validate patient ID against RT-plan patient ID
- Hard-fail unresolved or contradictory mappings

**Step 3: Keep normalized treatment index output**

- Convert matched raw beam numbers to treatment-beam indices `1..N`

### Task 3: Verify the focused regression suite

**Files:**
- Test: `tests/test_case_aggregator.py`
- Test: `tests/test_dispatcher.py`
- Test: `tests/test_worker.py`

**Step 1: Run focused tests**

Run: `python -m pytest tests/test_worker.py tests/test_case_aggregator.py tests/test_dispatcher.py -q`
Expected: PASS
