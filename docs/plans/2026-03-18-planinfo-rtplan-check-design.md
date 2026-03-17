# PlanInfo RT Plan Check Design

## Goal

Add strict cross-check logic between each beam folder's `PlanInfo.txt` and the RT plan so beam matching hard-fails on contradictions instead of falling back to weaker heuristics.

## Approved Scope

Option 1: strict minimal validation.

- Parse `DICOM_PATIENT_ID` and `DICOM_BEAM_NUMBER` from each `PlanInfo.txt`
- Match folders to RT-plan treatment beams by raw DICOM beam number
- Hard-fail if:
  - `PlanInfo.txt` is missing required fields
  - patient ID disagrees with RT plan
  - beam number is duplicated across folders
  - beam number does not map to exactly one RT-plan treatment beam
- Continue storing normalized treatment-beam indices `1..N` for TPS generation

## Non-Goals

- No fallback to fuzzy folder-name matching when `PlanInfo.txt` is present
- No broader `PlanInfo.txt` schema validation
- No end-to-end workflow changes
