# 2026-03-18 Simulation Hang Handoff

## Scope

Observation only, component validated only.

This note records the current post-startup runtime failure after fixing the
`tps_env` executable path from `./build/tps_env/tps_env` to
`./tps_env/tps_env`.

## What Was Verified

- The communicator starts successfully.
- The duplicate-case startup failure did not occur on a clean DB.
- The application:
  - initializes the SQLite DB
  - scans the case directory
  - starts the dashboard via `ttyd`
  - starts the GPU monitor
  - discovers case `55061194`
  - runs CSV interpretation
  - runs TPS generation
  - allocates GPUs
  - dispatches beam workers
- The runtime command now resolves correctly to:

```bash
nohup bash -c 'cd /home/SMC/MOQUI_SMC/moqui && ./tps_env/tps_env /home/SMC/MOQUI_SMC/data/Outputs_csv/55061194/moqui_tps_55061194_2025042401440800.in > /home/SMC/MOQUI_SMC/mqi_communicator/logs/sim_55061194_55061194_2025042401440800.log 2>&1' > /dev/null 2>&1 &
```

## Reproduced Failure

The run stalls during local simulation completion handling.

Observed runtime state during the stall:

- `main.py` remains running
- worker pool child `main.py` processes remain running
- `ttyd` and the dashboard remain running
- two active `tps_env` processes were observed for beams:
  - `55061194_2025042401440800`
  - `55061194_2025042401501400`
- beam `55061194_2025042401552900` had a simulation log file but no active
  `tps_env` process at the observation point

SQLite state at the time of observation:

```text
cases:
55061194 | processing | 50.0 | error_message=NULL

beams:
55061194_2025042401440800 | csv_interpreting | 70.0
55061194_2025042401501400 | csv_interpreting | 70.0
55061194_2025042401552900 | csv_interpreting | 70.0

gpu_resources:
GPU 0 assigned to 55061194_2025042401440800
GPU 1 assigned to 55061194_2025042401501400
GPU 2 assigned to 55061194_2025042401552900
GPU 3 idle
```

## Log Evidence

Worker logs stop at the same point for all three beams:

- `Starting HPC simulation for beam`
- progress update to `30.0`
- progress update to `70.0`

No later log entries appear for:

- `HPC simulation completed successfully`
- download step
- upload step
- beam failure cleanup
- GPU release

Current simulation log contents are short and do not contain completion markers.

Observed sizes:

```text
430 logs/sim_55061194_55061194_2025042401440800.log
430 logs/sim_55061194_55061194_2025042401501400.log
430 logs/sim_55061194_55061194_2025042401552900.log
```

Observed log content was limited to RT plan parsing / beam-filtering text such as:

- machine name
- RT plan type
- beam ID
- range shifter / block count
- treatment beam filtering summary

No success marker and no configured failure marker appeared.

## Root-Cause Hypothesis

The stall is likely in local `wait_for_job_completion()` log monitoring rather
than in startup, dispatch, or command construction.

Relevant file:

- `src/handlers/execution_handler.py`

Relevant behavior:

- local mode waits for either:
  - configured success pattern
  - configured failure patterns
  - timeout
- it does not appear to use process exit status for local `tps_env`
  completion

Configured completion patterns from `config/config.yaml`:

```yaml
completion_markers:
  success_pattern: "Simlulation finalizing.."
  failure_patterns:
    - "FATAL ERROR"
    - "ERROR:"
    - "Segmentation fault"
```

The likely next check is whether real `tps_env` output ever emits
`Simlulation finalizing..` exactly, or whether the process can exit without
writing any configured terminal marker.

## Commands Used

Launch:

```bash
setsid /home/SMC/miniconda3/bin/python main.py >/tmp/mqi_watch_run.log 2>&1 < /dev/null &
```

Observation:

```bash
pgrep -af 'main.py|tps_env|ttyd|src.ui.dashboard'
sqlite3 /home/SMC/MOQUI_SMC/data/mqi_communicator.db "..."
tail -n 200 /tmp/mqi_watch_run.log
tail -n 120 logs/worker_55061194_*.log
tail -n 80 logs/sim_55061194_*.log
wc -c logs/sim_55061194_*.log
```

## Current State After This Session

The reproduced run left stale runtime state in the DB again:

- case row present
- beam rows present
- GPU assignments present

Before another clean reproduction, reset:

- `cases`
- `beams`
- `workflow_steps`
- `gpu_resources.assigned_case` and `gpu_resources.status`

## Recommended Next Step

Trace and verify `ExecutionHandler.wait_for_job_completion()` in local mode.

Specifically:

1. Check whether `tps_env` exits while the worker is still waiting on a missing
   success marker.
2. Compare actual `tps_env` terminal output against the configured
   `success_pattern`.
3. Decide whether local completion detection should also use child-process exit
   status, file existence in `data/Dose_dcm/<case_id>/beam_<n>`, or different
   terminal markers.
