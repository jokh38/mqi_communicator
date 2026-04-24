# Deployment Guide: Test Bed vs SMC Production

This document describes the differences between the test bed and the SMC
distributed production environment, and the configuration changes required
when deploying to each.

## Environments

| | Test Bed | SMC Production |
|---|---|---|
| **Host** | developer workstation (e.g., T640) | distributed deployment host |
| **Purpose** | Development, testing, debugging | Clinical operation |
| **GPU** | 4x RTX 2080 (sm_75) | Same or equivalent |
| **Base path** | wherever the repo is checked out | wherever the repo is checked out |

## Base path resolution

`paths.base_directory` in `config/config.yaml` is resolved at runtime by
`mqi_communicator/src/config/settings.py` using this order:

1. The value in `config/config.yaml` if non-empty.
2. The `MOQUI_SMC_ROOT` environment variable.
3. The directory three levels above `settings.py` — i.e., the repo root
   that contains `mqi_communicator/`, `mqi_transfer/`, `moqui_SMC/`, etc.

You normally do **not** need to hardcode a path. Just check the repo out
anywhere and run. All downstream `paths.local.*`, `executables.local.python`,
and `ptn_checker.path` entries use `{base_directory}` interpolation, so they
follow automatically.

If you need to override, either edit `paths.base_directory` in the YAML
or export `MOQUI_SMC_ROOT` before launch.

`mqi_transfer/Linux/mqi_transfer.py` uses the same two-level fallback
(`MOQUI_SMC_ROOT` env var, or its own script location) to resolve the
relative `output_root` in `app_config.ini`.

## Known Issues

### Corrupt `.pyc` Bytecode Files

**Symptom**: `ValueError: bad marshal data (unknown type code)` on import.

**Cause**: Python version mismatch or incomplete `.pyc` compilation after dependency changes.

**Fix**:

```bash
# Clear all bytecode caches
find .venv/lib -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find .venv/lib -name "*.pyc" -delete 2>/dev/null

# Also clear in mqi_interpreter if it fails
find ../mqi_interpreter -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find ../mqi_interpreter -name "*.pyc" -delete 2>/dev/null
```

### Starlette 1.0 Template API Incompatibility

**Symptom**: Web dashboard returns HTTP 500 on every page. Uvicorn stderr shows `TypeError: unhashable type: 'dict'` in `jinja2/utils.py`.

**Cause**: Starlette 1.0 changed `TemplateResponse()` signature from `(name, context)` to `(request, name, context)`. The application code uses the old calling convention.

**Fix**: Pin starlette to 0.x:

```bash
.venv/bin/python3 -m pip install 'starlette<1.0'
```

Alternatively, update all `TemplateResponse` calls in `src/web/app.py` to the new API.

### Corrupt SQLite Database

**Symptom**: `database disk image is malformed` during startup.

**Fix**: Delete the database and let the application recreate it on next startup:

```bash
rm data/mqi_communicator.db
```

This is safe on the test bed. On SMC, back up the database first if it contains clinical data.

### CUDA Build: `atomicAdd(double*)` Compilation Error

**Symptom**: `no instance of overloaded function "atomicAdd" matches the argument list — argument types are: (double *, double)` when building moqui_SMC with default `CMAKE_CUDA_ARCHITECTURES=75`.

**Cause**: The scorer uses `double` atomics. CMake needs to generate PTX for sm_60+ where `atomicAdd(double*)` is supported.

**Fix**: Build with explicit architecture list:

```bash
cd moqui_SMC
rm -rf build/tps_env
cmake -S tps_env -B build/tps_env -DCMAKE_CUDA_ARCHITECTURES="60;75"
cmake --build build/tps_env
```

## Deployment Checklist

When deploying to SMC production:

- [ ] Check the repo out to the target path; no path edits needed in `config.yaml`.
- [ ] Optionally export `MOQUI_SMC_ROOT` if the service runs with a non-standard layout.
- [ ] Rebuild moqui_SMC binary (`cmake --build build/tps_env` from `moqui_SMC/`)
- [ ] Install Python dependencies in `.venv` (`pip install -r requirements.txt`)
- [ ] Pin `starlette<1.0` in the venv
- [ ] Verify GPU visibility (`nvidia-smi`)
- [ ] Verify scan directory exists and is populated
- [ ] Substitute `@@MOQUI_USER@@` / `@@MOQUI_ROOT@@` in the `.service` files
      before installing under `/etc/systemd/system/`.
- [ ] Run `./start_all.sh` and confirm both `mqi_transfer` and `mqi_communicator` stay running
- [ ] Access web dashboard at `http://<host>:8080/ui/workflow`
