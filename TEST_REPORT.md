# MQI Communicator Test Report
**Date:** 2026-03-18 KST
**Validation scope:** component validated
**System under review:** current repository state in `/home/jokh38/MOQUI_SMC/mqi_communicator`

---

## Summary

This report was reviewed against the current source tree and the local automated test suite. The original report mixed source-backed findings with runtime/environment observations that are not reproducible from this repository alone.

Current component-validated status:

| Area | Status |
|------|--------|
| Unit/component test suite | PASS |
| GPU assignment schema/runtime consistency | PASS |
| Legacy GPU status normalization (`available` -> `idle`) | PASS |
| Runtime case/database/log observations from external environment | NOT VALIDATED |
| End-to-end workflow execution | NOT VALIDATED |

---

## Verification Performed

Exact command used:

```bash
python -m pytest
```

Result:

- `79 passed in 1.28s`

This is **component validated**, not end-to-end validated.

---

## Corrected Findings

### 1. GPU assignment mismatch was real and is now fixed

The original report correctly identified a mismatch between the `gpu_resources.assigned_case` schema and runtime usage.

Validated before fix:

- The database schema referenced `cases(case_id)`.
- Dispatcher and worker code stored beam IDs in `assigned_case`.

Applied fix:

- `gpu_resources.assigned_case` now references `beams(beam_id)`.
- Legacy databases are migrated to the corrected foreign key during `init_db()`.
- Fresh GPU reservations no longer write temporary case IDs into `assigned_case`; the column is only used for beam assignments.
- TPS-generation failure cleanup now releases GPUs by UUID instead of relying on case-id based release after beam assignment.

Files updated:

- [src/database/connection.py](/home/jokh38/MOQUI_SMC/mqi_communicator/src/database/connection.py)
- [src/repositories/gpu_repo.py](/home/jokh38/MOQUI_SMC/mqi_communicator/src/repositories/gpu_repo.py)
- [src/core/dispatcher.py](/home/jokh38/MOQUI_SMC/mqi_communicator/src/core/dispatcher.py)
- [tests/test_database_connection.py](/home/jokh38/MOQUI_SMC/mqi_communicator/tests/test_database_connection.py)
- [tests/test_dispatcher.py](/home/jokh38/MOQUI_SMC/mqi_communicator/tests/test_dispatcher.py)

### 2. `GpuStatus` warning was overstated for current code

The original warning said `'available'` was currently being produced and rejected as invalid. What is component-validated in the repository today is narrower:

- `GpuStatus` only allows `idle` and `assigned`.
- Database startup includes a migration that normalizes legacy `available` rows to `idle`.

That means the repository contains a compatibility fix for old data. It does **not** by itself prove a current live runtime defect.

### 3. Workflow-stuck / missing-output claims are not validated from this repo alone

These original claims may have been observed in a separate runtime environment, but they are not established by the repository review itself:

- beams stuck in `csv_interpreting`
- missing `Dose_dcm` output directory
- duplicate case insertion events for case `18977768`
- ttyd port `8080` conflict
- GPU monitor shutdown warnings from a live run
- historical GDCM assertion failure for case `55758663`

Those are environment/log/database observations. They require the specific runtime artifacts they were derived from. They should not be presented as current repository facts without attaching the supporting DB snapshot, logs, or reproducible steps.

---

## Known Gaps

- No live case database snapshot was provided for case `55061194`.
- No runtime logs were re-collected in this review.
- No end-to-end workflow run was executed against the external interpreter, MOQUI, or RawToDCM dependencies.
- Therefore this review cannot confirm or refute the original operational claims about a specific case run.

---

## Final Status

`component validated`

What is established now:

- the test suite passes
- the GPU assignment schema/runtime mismatch was real
- that mismatch has been fixed in code and covered by regression tests

What is not established now:

- end-to-end case processing success
- correctness of the original runtime observations for case `55061194`
