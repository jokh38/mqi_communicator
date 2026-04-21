# Beam Folder Mapping Fix Handoff

## Summary

Case `55061194` was failing in `mqi_communicator` before CSV interpretation or simulation. The failure happened during case aggregation, where timestamp-named beam folders such as `2025042401440800` could not be mapped to RT Plan beam metadata.

## Root Cause

The mapping logic in `src/core/case_aggregator.py` only matched beam folders by:

- numeric suffix from the folder name
- exact/fuzzy match against RT Plan `beam_name`

That does not work for real incoming folders named by irradiation timestamp. The case data already includes the needed mapping in each folder's `PlanInfo.txt`:

- `DICOM_BEAM_NUMBER,2`
- `DICOM_BEAM_NUMBER,3`
- `DICOM_BEAM_NUMBER,4`

## Fix Implemented

Updated [case_aggregator.py](/home/SMC/MOQUI_SMC/mqi_communicator/src/core/case_aggregator.py) to:

1. Read `DICOM_BEAM_NUMBER` from `PlanInfo.txt` when present.
2. Use that explicit beam number before folder-name-based matching.
3. Sort beam folders by directory name so `prepare_beam_jobs()` returns deterministic ordering.

Added regression coverage in [test_case_aggregator.py](/home/SMC/MOQUI_SMC/mqi_communicator/tests/test_case_aggregator.py).

## Verification

Commands used:

```bash
python -m pytest /home/SMC/MOQUI_SMC/mqi_communicator/tests/test_case_aggregator.py /home/SMC/MOQUI_SMC/mqi_communicator/tests/test_dispatcher.py /home/SMC/MOQUI_SMC/mqi_communicator/tests/test_worker.py -q
```

Observed result:

- `8 passed in 0.50s`

Direct real-case verification:

```bash
python - <<'PY'
from pathlib import Path
from src.config.settings import Settings
from src.core.case_aggregator import prepare_beam_jobs
from src.infrastructure.logging_handler import LoggerFactory

settings = Settings(Path('config/config.yaml'))
LoggerFactory.configure(settings)
case_path = Path('/home/SMC/MOQUI_SMC/data/SHI_log/55061194')
beam_jobs = prepare_beam_jobs('55061194', case_path, settings)
print('beam_job_count=', len(beam_jobs))
for job in beam_jobs:
    print(job['beam_path'].name, job['beam_number'])
PY
```

Observed result:

- `beam_job_count= 3`
- `2025042401440800 2`
- `2025042401501400 3`
- `2025042401552900 4`

Status: `component validated`

## Remaining Gap

This is not `end-to-end validated`.

The patched code fixes the original mapping failure, but the live communicator session did not automatically re-enqueue `55061194` after stale database rows were deleted. That appears to be a separate watcher/startup-rescan behavior issue, not the beam mapping bug itself.

## Next Session Starting Point

1. Restart `mqi_communicator/main.py` from a clean state.
2. Trigger a fresh case-detection event for `55061194` or another case.
3. Watch:
   - `mqi_communicator/logs/main.log`
   - `mqi_communicator/logs/dispatcher_55061194.log`
   - SQLite `data/mqi_communicator.db`
   - worker processes
   - `nvidia-smi`
   - output directories under configured CSV / dose paths
4. If the case is still not picked up, investigate directory watcher / startup scan behavior separately.
