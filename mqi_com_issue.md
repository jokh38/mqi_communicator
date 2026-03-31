# MQI Communicator HPC Execution Failure Analysis

**Date**: 2026-03-31  
**Cases Analyzed**: 55758663 (FAILED), 55061194 (COMPLETED)

---

## 1. Executive Summary

Two issues were identified:

1. **Primary — `tps_env` Segmentation Fault on Beams with Range Shifters/Blocks**: The MOQUI simulation binary (`tps_env`) crashes with a segfault for the two failing beams in case `55758663`. In this sample, those failing beams both include a range shifter and a block in the RT Plan. The logs point to a binary/runtime failure during simulation setup, but they do not fully exclude all Python-side input preparation effects.

2. **Secondary — Reporting / Aggregation Inconsistencies**: Case-level failure propagation is working as designed: if any beam fails, the case is marked `failed`. Separately, the main orchestrator log reports failed beam workers as "completed successfully", which is misleading and should be fixed.

---

## 2. Primary Issue: `tps_env` Segfault on Range Shifter Beams

### 2.1 Correlation Within the Analyzed Sample

| Case | Beam | Range Shifters | Blocks | Result |
|------|------|---------------|--------|--------|
| 55758663 | Beam 1 (`1G000:TX`) | 0 | 0 | **Success** |
| 55758663 | Beam 2 (`2G010:TX`) | 1 (`SNOUT_DEG_S`, 41mm) | 1 | **Segfault** |
| 55758663 | Beam 3 (`3G310:TX`) | 1 (`SNOUT_DEG_S`, 41mm) | 1 | **Segfault** |
| 55061194 | Beam 1 (`1G180:TX`) | 0 | 0 | **Success** |
| 55061194 | Beam 2 (`2G225:TX`) | 0 | 0 | **Success** |
| 55061194 | Beam 3 (`3G140:TX`) | 0 | 0 | **Success** |

**Observed correlation in this sample**: the two failing beams both have a range shifter and a block; the three successful beams shown do not. This is strong evidence, but not proof that the range shifter alone is the triggering factor.

### 2.2 Evidence from Simulation Logs

**Successful beam** (`sim_55758663_2025072123055700.log`):
```
Machine name: G1 (Same as selected gantry number)
RT Plan type: RT ION
Beam ID: 1
Number of range shifter : 0
Number of blocks : 0
=== Filtering beams in RT Plan (excluding SETUP) ===
...
Downloading node data.. : Number of scorers --> 0
    node's child[0]: 0x72d07a601200
...
Successfully wrote DICOM RT Dose file: ../data/Dose_dcm/55758663/1G000:TX_2_Dose.dcm
  Dimensions: 400x400x1
  Dose grid scaling: 2.38044e-10 Gy/pixel
  Max dose: 1.56002e-05 Gy

=== Beam 1 Timing Summary ===
  Initialization: 2674 ms
  Simulation: 8058 ms
  Output: 571 ms
  Beam total: 10732 ms (10.73 s)

=== Execution Timing Summary ===
Total execution time: 11993 ms
```

**Failed beam** (`sim_55758663_2025072123104200.log`):
```
Machine name: G1 (Same as selected gantry number)
RT Plan type: RT ION
Beam ID: 2
Number of range shifter : 1
RangeShifterID detected.. : SNOUT_DEG_S 
Range shifter thickness determined.. : 41 (mm) and position: 149.5 (mm)
(x, y, z) -> (0.000000, 0.000000, 149.500000)
Number of blocks : 1
=== Filtering beams in RT Plan (excluding SETUP) ===
...
=== Total treatment beams: 3 ===
Segmentation fault (core dumped)
```

**Failed beam** (`sim_55758663_2025072123154300.log`):
```
Machine name: G1 (Same as selected gantry number)
RT Plan type: RT ION
Beam ID: 3
Number of range shifter : 1
RangeShifterID detected.. : SNOUT_DEG_S 
Range shifter thickness determined.. : 41 (mm) and position: 150.5 (mm)
(x, y, z) -> (0.000000, 0.000000, 150.500000)
Number of blocks : 1
=== Filtering beams in RT Plan (excluding SETUP) ===
...
=== Total treatment beams: 3 ===
Segmentation fault (core dumped)
```

### 2.3 Crash Point

The crash occurs **after beam filtering** but **before node data download begins**. The observed sequence is:

```
1. Parse RT Plan DICOM                      ✓ (works)
2. Detect range shifter (thickness/position) ✓ (works)
3. Detect blocks                             ✓ (works)
4. Filter beams (exclude SETUP)              ✓ (works)
5. Begin simulation setup / geometry init    ✗ SEGFAULT HERE
```

Observation: the segfault happens after RT Plan parsing, range shifter/block detection, and beam filtering.

Assumption: this likely indicates a null pointer dereference or similar invalid memory access during later simulation setup, possibly geometry initialization for the beam configuration. The logs alone do not prove the exact crash site.

### 2.4 TPS Input Files Are Consistent in Structure

The `.in` files for all beams (passing and failing) have the same overall structure and differ only in the expected per-beam fields:

```ini
GPUID <varies>
RandomSeed -1932780356
UseAbsolutePath false
TotalThreads -1
MaxHistoriesPerBatch 10000000
Verbosity 0

ParentDir ../data/Outputs_csv/55758663
DicomDir rtplan
logFilePath log

GantryNum 1
BeamNumbers <1|2|3>

UsingPhantomGeo true
TwoCentimeterMode true

PhantomDimX 400
PhantomDimY 400
PhantomDimZ 400
PhantomUnitX 1
PhantomUnitY 1
PhantomUnitZ 1
PhantomPositionX -200.0
PhantomPositionY -200.0
PhantomPositionZ -380.0

Scorer Dose
SupressStd true
ReadStructure true
ROIName External

SourceType FluenceMap
SimulationType perBeam
ParticlesPerHistory 0.1

ScoreToCTGrid true
OutputDir ../data/Dose_dcm/55758663
OutputFormat dcm
OverwriteResults true
```

The only differences are `GPUID` and `BeamNumbers`. There are no range-shifter-specific parameters in the `.in` file; `tps_env` reads range shifter information from the DICOM RT Plan in the `rtplan/` directory. This makes the `.in` files themselves an unlikely cause of the failure, but it does not by itself rule out every upstream data-preparation issue.

---

## 3. Secondary Issue: Status / Reporting Behavior

### 3.1 Timeline of Case 55758663

| Time | Beam | Event |
|------|------|-------|
| 15:46:15.031 | Beam 2 | HPC Execution started |
| 15:46:15.035 | Beam 1 | HPC Execution started |
| 15:46:15.041 | Beam 3 | HPC Execution started |
| 15:46:45.067 | Beam 2 | **ERROR**: Segfault detected → status=failed |
| 15:46:45.069 | Beam 2 | Case marked FAILED (failed_beams=1) |
| 15:46:45.071 | Beam 1 | Progress updated to 100% |
| 15:46:45.072 | Beam 1 | HPC simulation completed successfully |
| 15:46:45.078 | Beam 3 | **ERROR**: Segfault detected → status=failed |
| 15:46:45.081 | Beam 1 | Uploaded result to PC_localdata ✓ |
| 15:46:45.082 | Beam 3 | Case marked FAILED (failed_beams=2) |
| 15:46:45.084 | Beam 1 | **Case marked FAILED** (failed_beams=2) ← even though Beam 1 succeeded |

### 3.2 The Problem

Beam 1 succeeds individually and produces a valid DICOM dose file, but the case is still marked `failed` because Beams 2 and 3 crashed. This is the intended aggregation rule: each worker checks sibling beam status at completion and marks the case as failed if any sibling beam has failed:

```
Worker log (beam 1): "Case '55758663' has failed beams. Marking case as FAILED."
  context: {"case_id": "55758663", "failed_beams": 2}
```

This case-level status update is correct. The actual operational concern is that a successful per-beam output such as `1G000:TX_2_Dose.dcm` still exists even though the parent case is failed, so downstream handling/reporting of partial outputs should be explicit.

---

## 4. Worker Log Error Pattern

The failure detection works correctly in `ExecutionHandler.wait_for_job_completion()`:

```python
failure_patterns = completion_markers.get("failure_patterns", 
    ["FATAL ERROR", "ERROR:", "Segmentation fault"])

for pattern in failure_patterns:
    if pattern in new_content:
        if process:
            process.kill()
            process.wait()
        return ExecutionHandler.JobWaitResult(
            failed=True,
            error=f"Simulation failed: found pattern '{pattern}' in log"
        )
```

The worker correctly detects `Segmentation fault` in the simulation log and transitions to the `Failed` state.

## 5. Main-Process Logging Inconsistency

The main orchestrator log is inconsistent with worker outcomes. In `src/core/worker.py`, `monitor_completed_workers()` logs:

```python
future.result()  # Raise exception if worker failed
_release_beam_gpu_assignment(beam_id, settings, logger)
logger.info(f"Beam worker {beam_id} completed successfully")
```

For case `55758663`, `logs/main.log` contains:

- `Beam worker 55758663_2025072123055700 completed successfully`
- `Beam worker 55758663_2025072123104200 completed successfully`
- `Beam worker 55758663_2025072123154300 completed successfully`

However, the two latter beams are failed in their worker logs due to `Segmentation fault`. This means the orchestrator's success message reflects process completion, not workflow success, and is misleading.

---

## 6. Recommendations

### 6.1 Primary Fix — Investigate `tps_env` Segfault

The strongest current evidence points to `tps_env` crashing on the failing beam configurations. Options:

| Option | Effort | Description |
|--------|--------|-------------|
| **Contact MOQUI developers** | Low | Report the segfault with the DICOM RT Plan file for case 55758663. The crash is reproducible for the two failing beam configurations and appears correlated with RT Plan features present only on those beams. |
| **Debug `tps_env` binary** | High | Run `tps_env` under GDB to get a backtrace: `gdb --args ./tps_env/tps_env moqui_tps_55758663_2025072123104200.in` → `run` → `bt` |
| **Check `tps_env` version** | Low | Verify the binary is up-to-date. Range shifter support may have a known fix in a newer build. |
| **Check DICOM RT Plan data** | Medium | Verify the range shifter parameters in the DICOM file are valid (thickness 41mm, position 149.5/150.5mm). Invalid or edge-case values may trigger the crash. |

### 6.2 Secondary Fix — Correct Main-Process Logging

Update the main-process log message in `monitor_completed_workers()` so it distinguishes:

- worker process exited cleanly
- beam workflow completed successfully
- beam workflow failed but handled internally

Without this change, `main.log` can incorrectly suggest full success for failed beams.

### 6.3 Quick Verification Command

```bash
# Reproduce the segfault directly
cd /home/jokh38/MOQUI_SMC/mqi_communicator
./tps_env/tps_env ../data/Outputs_csv/55758663/moqui_tps_55758663_2025072123104200.in

# Get a backtrace
gdb --args ./tps_env/tps_env ../data/Outputs_csv/55758663/moqui_tps_55758663_2025072123104200.in
```

---

## 7. Log File Reference

| Log File | Content |
|----------|---------|
| `logs/main.log` | Overall orchestrator flow — shows `Beam worker ... completed successfully` for failed beams, which is misleading |
| `logs/worker_55758663_2025072123055700.log` | Beam 1 (no range shifter) → **SUCCESS** |
| `logs/worker_55758663_2025072123104200.log` | Beam 2 (range shifter) → **FAILED** (segfault) |
| `logs/worker_55758663_2025072123154300.log` | Beam 3 (range shifter) → **FAILED** (segfault) |
| `logs/worker_55061194_*.log` | All 3 beams (no range shifter) → **SUCCESS** |
| `logs/sim_55758663_2025072123104200.log` | Raw simulation output showing `Segmentation fault (core dumped)` |
| `logs/sim_55758663_2025072123154300.log` | Raw simulation output showing `Segmentation fault (core dumped)` |
| `logs/sim_55758663_2025072123055700.log` | Raw simulation output — completed normally (11.993s) |
| `logs/sim_55061194_*.log` | All 3 beams completed normally |
