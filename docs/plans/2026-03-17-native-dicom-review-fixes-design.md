# Native DICOM Review Fixes Design

## Context

The current implementation still assumes MOQUI produces `.raw` beam outputs that must be converted into DICOM through `raw_to_dcm`. That assumption is no longer valid. The current MOQUI version produces DICOM output directly, and native DICOM is now the only supported result format.

This design revises the earlier review-fix plan so the workflow removes raw-output handling entirely while still addressing the original review findings:

- remote HPC jobs must be polled until completion
- beam ordering must use explicit beam metadata, not `beam_id` sort order
- local command execution should avoid shell-string fragility
- SSH-only dependencies should not break local-mode imports/tests

## Goals

- Remove `raw_to_dcm` and all `.raw`-based workflow assumptions.
- Make native DICOM output the only result path for both local and remote execution.
- Preserve the earlier correctness fixes for job polling, beam numbering, command execution, and SSH dependency isolation.

## Non-Goals

- Supporting both raw and DICOM output modes.
- Preserving compatibility with old `raw_to_dcm` scripts or state transitions.
- Claiming end-to-end validation without a real MOQUI native-DICOM run.

## Approaches Considered

### 1. Full native-DICOM-only workflow

Remove conversion entirely and make result handling operate directly on downloaded/generated DICOM artifacts.

Pros:
- matches the real product constraint
- simplifies workflow states
- deletes dead code and tests

Cons:
- requires updates across workflow, tests, settings, and docs

### 2. Keep postprocessing state as a no-op validator

Retain workflow structure but replace conversion logic with directory checks.

Pros:
- smaller structural change

Cons:
- preserves misleading architecture
- keeps dead concepts alive

### 3. Abstract multiple output formats

Add a result-format abstraction that could support raw and DICOM.

Pros:
- flexible

Cons:
- violates YAGNI
- conflicts with the explicit requirement that native DICOM is the only supported path

## Recommendation

Use approach 1. The workflow should treat MOQUI-generated DICOM as the only simulation artifact and remove the raw conversion pipeline entirely.

## Revised Architecture

### Result flow

1. TPS generation uses explicit DICOM beam numbers persisted in beam records.
2. HPC execution submits the job and polls scheduler state until completion or failure.
3. Download/result handling resolves the native DICOM output path for the beam and downloads or locates DICOM artifacts directly.
4. Upload/finalization operates on the DICOM directory or file set without any conversion step.

### State machine changes

- `DownloadState` becomes responsible for locating native DICOM results.
- `PostprocessingState` is removed.
- `UploadResultToPCLocalDataState` either remains with format-neutral behavior or is renamed in implementation if the code becomes clearer that way.
- Shared context should carry a final DICOM path, not `raw_output_file`.

### Execution handler changes

- Remove `ExecutionHandler.run_raw_to_dcm()`.
- Keep file transfer helpers, but extend them if needed for directory-oriented DICOM download/upload.
- Implement real remote job polling through scheduler status commands.
- Normalize local command execution to argument lists with `shell=False`.

### Data model changes

- Add explicit `beam_number` storage to `BeamData` and the `beams` table.
- Stop deriving beam numbers from `ORDER BY beam_id ASC`.
- Use persisted `beam_number` in dispatcher, worker, TPS generation, and result matching.

### Configuration/path changes

- Replace raw-output assumptions with native-DICOM result locations.
- Inspect and update the path key(s) used by `DownloadState` so they match actual MOQUI native DICOM output naming.
- Remove references to `raw_to_dicom_script` and related postprocessing paths where they are no longer used.

## Error Handling

- Remote scheduler failure, cancellation, timeout, or query failure must keep the workflow from entering result download.
- Missing `beam_number` for a beam should fail fast.
- Missing native DICOM result files after successful completion should fail the beam with a clear path-specific error.
- Local-mode code paths should continue to import cleanly without `paramiko` present.

## Testing Strategy

- Red tests first for native-DICOM-only behavior.
- Replace conversion-focused state tests with direct DICOM result handling tests.
- Add coverage for explicit beam-number usage in result path resolution.
- Keep targeted tests for remote job polling, shell-free local execution, and lazy SSH dependency loading.
- Validation remains `component validated` unless a real MOQUI native-DICOM workflow run is performed.

## Risks

- The exact native DICOM output layout may differ between local and remote environments. The implementation plan should include an early task to verify current configured output paths before refactoring the workflow.
- Removing `PostprocessingState` may require test and state-transition adjustments in more places than currently visible from the small unit suite.

## Success Criteria

- No production code or tests refer to `raw_to_dcm`, `.raw` beam outputs, or conversion-based postprocessing.
- Workflow result handling uses native DICOM artifacts directly.
- Beam/result matching uses explicit persisted beam numbers.
- Remote HPC completion is confirmed before result handling begins.
- Verification is reported as `component validated` unless a separate end-to-end run is executed.
