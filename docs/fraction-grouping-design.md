# Fraction Grouping Design

## Context

This document captures findings from investigating how `mqi_communicator` distinguishes
individual treatment fractions from the beam delivery log folders on disk, and the design
decisions needed to handle edge cases correctly.

---

## Data Structure

Each patient case lives under a study UID directory:

```
data/2026_04_log/G1/<patient_id>/<study_uid>/
    RP.<uid>.dcm                  ← DICOM RT Plan
    20260319234839000/            ← delivery folder (named by timestamp)
    20260319235205000/
    20260319235603000/
    20260319235857000/
    20260320181054000/
    ...
```

Each delivery folder contains:
- `*.ptn` — proton log files (one per energy layer)
- `*.mgn` — magnet log files
- `PlanInfo.txt` — beam metadata written by the treatment control system

### PlanInfo.txt Fields

```
DICOM_PATIENT_ID,04198922
DICOM_STUDY_INS_UID,1.2.840...
DICOM_BEAM_NUMBER,2
TCSC_IRRAD_DATETIME,2026031923483900
TCSC_PATIENT_ID,0234545
TCSC_PLAN_NUMBER,1
TCSC_FIELD_NUMBER,2
START_LAYER_NUMBER,1
STOP_LAYER_NUMBER,15
SHARE_DIR,\\192.168.3.107\Share\ScanActual
ACTUAL_DIR,04198922\1.2.840...\2026031923483900
```

Key fields for fraction grouping:
- `DICOM_BEAM_NUMBER` — which beam within the plan (matches DICOM RT Plan beam sequence)
- `TCSC_IRRAD_DATETIME` — irradiation start datetime (`YYYYMMDDHHMMSSff`), same value as folder name
- `TCSC_PATIENT_ID` — unique per beam folder; increments across beams but is in a different range per fraction; **not usable for grouping**
- `TCSC_PLAN_NUMBER` — always `1` in observed data; not useful for grouping

---

## Observed Fraction Structure (Real Data)

Case `04198922`, study `1.2.840.113619.2.278.3.2462062185.863.1773269171.448`:

| Folder | Date | Time | Beam # | Fraction |
|--------|------|------|--------|----------|
| 2026031923483900 | 2026-03-19 | 23:48 | 2 | F1 |
| 2026031923520500 | 2026-03-19 | 23:52 | 3 | F1 |
| 2026031923560300 | 2026-03-19 | 23:56 | 4 | F1 |
| 2026031923585700 | 2026-03-19 | 23:58 | 5 | F1 |
| 2026032018105400 | 2026-03-20 | 18:10 | 5 | F2 |
| 2026032018140900 | 2026-03-20 | 18:14 | 4 | F2 |
| 2026032018171800 | 2026-03-20 | 18:17 | 3 | F2 |
| 2026032018204100 | 2026-03-20 | 18:20 | 2 | F2 |
| 2026032317525500 | 2026-03-23 | 17:52 | 5 | F3 |
| 2026032317555800 | 2026-03-23 | 17:55 | 4 | F3 |
| 2026032317590700 | 2026-03-23 | 17:59 | 3 | F3 |
| 2026032318020500 | 2026-03-23 | 18:02 | 2 | F3 |

Observations:
- Beams within a fraction are delivered ~3-5 minutes apart
- Fractions are separated by many hours or days
- Fraction 1 is missing beam 1 — partial delivery is a real clinical scenario
- Beam ordering within a fraction varies (F2 delivers 5→4→3→2, F1 delivers 2→3→4→5)

---

## Current Code Behavior and Gap

The current implementation in `case_aggregator.py:prepare_case_delivery_data()` treats
each delivery folder as an independent `DeliveryData` record. It groups repeated
deliveries of the same `treatment_beam_index` across all folders and selects the earliest
as the reference delivery for simulation. There is **no fraction-grouping logic**.

### Midnight Crossing Problem

If a fraction starts before midnight and the last beam finishes after midnight, the
`delivery_date` field (derived from `TCSC_IRRAD_DATETIME`) will differ across beams in
the same fraction. The current code does not use `delivery_date` for grouping, so this
does not cause a hard failure, but it produces incorrect `delivery_date` metadata and
would misrepresent the fraction in any reporting.

Example (hypothetical):
```
23:58:00 Beam 2  → delivery_date = 2026-03-19   ← same fraction
00:02:00 Beam 3  → delivery_date = 2026-03-20   ← same fraction, wrong date
```

---

## Proposed Fraction Grouping Algorithm

### Core Idea

Combine two signals:
1. **DICOM beam count** (from RT Plan) as a hard target per fraction
2. **1-hour time window** as the boundary condition

### Algorithm

```
1. Read RT Plan → expected_beam_count (e.g., 4)
2. Sort all delivery folders by TCSC_IRRAD_DATETIME ascending
3. Greedy grouping:
     fraction_start = timestamp of first folder
     current_fraction = []
     for each folder in sorted order:
         if (folder.timestamp - fraction_start) <= 1 hour:
             add folder to current_fraction
         else:
             close current_fraction → emit as fraction record
             start new fraction with this folder
             fraction_start = folder.timestamp
4. Close the last open fraction
5. Sanity check each fraction:
     - if len(folders) == expected_beam_count → complete
     - if len(folders) < expected_beam_count → partial delivery, warn and process
     - if len(folders) > expected_beam_count → anomaly, flag for review
```

### On Duplicate Beam Numbers

A duplicate `DICOM_BEAM_NUMBER` within the 1-hour window indicates a **beam
interruption and re-delivery**, not a new fraction. This should be treated as an
exception case with a warning logged, not as a fraction boundary. The time-gap rule
remains the sole hard boundary.

### Why Not Use Duplicate Beam Number as a Boundary

Splitting on duplicate beam number would incorrectly split an interrupted fraction into
two incomplete fractions, neither of which would pass the beam count sanity check. It is
better to keep the interrupted re-delivery in the same fraction group and flag it for
human review.

---

## Real-Time Waiting Behavior

In real-time operation the communicator scans the directory while treatment is ongoing.
When it detects a new case (RT Plan present) it must not begin processing until a
complete fraction has accumulated.

### Required Behavior

```
1. Detect RT Plan → read expected_beam_count = 4
2. Scan delivery folders → found = 1 (treatment in progress)
3. found < expected_beam_count AND within 1-hour window → WAIT, keep polling
4. Scan again → found = 4, all within 1-hour window → fraction complete → queue
```

### States for a Fraction Candidate

| State | Condition | Action |
|-------|-----------|--------|
| **pending** | Within 1-hour window, count < expected | Keep polling |
| **complete** | Window still open OR just closed, count == expected | Queue for processing |
| **partial** | 1-hour window closed, count < expected | Queue with warning |
| **anomaly** | count > expected | Flag for review, do not process |

### Timeout / Partial Delivery Handling

If `expected_beam_count` is never reached within the 1-hour window (e.g., treatment
was interrupted and not resumed), the fraction is closed as a **partial delivery**.
Processing proceeds with available beams and a warning is recorded. This handles the
clinical scenario where a patient cannot tolerate the full fraction.

### Batch vs Real-Time Mode

- **Batch mode** (historical data): all folders already exist; grouping runs once over
  all folders; the pending state never applies.
- **Real-time mode** (live treatment): the scanner loop must maintain state across
  polls, tracking which fraction candidates are still open (pending) vs closed.

The grouping algorithm itself is identical in both modes. The difference is only in
whether the scanner loop needs to persist fraction candidate state between iterations.

---

## Open Questions

1. **Is there a signal from the treatment machine** (e.g., a completion file, a network
   event) that could replace polling and provide a reliable "fraction done" trigger?
   This would eliminate the need for the timeout heuristic entirely.

2. **What is the correct handling of beam 1 being absent** in fraction 1 of the observed
   data? Is beam 1 a setup/reference field that is intentionally excluded from log
   collection, or was it genuinely not delivered? This affects whether `expected_beam_count`
   should come from the total RT Plan beam count or from a filtered treatment-beam-only count.

3. **What is the maximum realistic inter-beam gap within a fraction?** The observed data
   shows ~3-5 minutes. A 1-hour threshold is conservative. Confirming the clinical
   upper bound would allow tightening this if needed.
