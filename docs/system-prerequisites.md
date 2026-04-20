# System Prerequisites

This document lists non-Python dependencies required by the current codebase.
These tools are not installable through `requirements.txt`.

## Required By Configuration

### `ttyd`

- Required when `ui.web.enabled: true` in [`config/config.yaml`](/home/SMC/MOQUI_SMC/mqi_communicator/config/config.yaml).
- Used by [`src/infrastructure/ui_process_manager.py`](/home/SMC/MOQUI_SMC/mqi_communicator/src/infrastructure/ui_process_manager.py) to expose the Rich dashboard over HTTP.
- If `ttyd` is missing, dashboard startup fails with `ttyd not available`.

Install reference:

- https://github.com/tsl0922/ttyd

If you do not want to install it, set `ui.web.enabled: false`.

### `nvidia-smi`

- Required for live GPU monitoring in [`src/infrastructure/gpu_monitor.py`](/home/SMC/MOQUI_SMC/mqi_communicator/src/infrastructure/gpu_monitor.py).
- Typically provided by the NVIDIA driver installation.

## Environment-Specific External Scripts

The checked-in configuration also references external project scripts and paths
that must exist on the target machine:

- `mqi_interpreter/main_cli.py`
- `RawToDCM`

Verify the configured paths under [`config/config.yaml`](/home/SMC/MOQUI_SMC/mqi_communicator/config/config.yaml) before starting the application.
