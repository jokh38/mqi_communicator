# Deployment Guide: Test Bed vs SMC Production

This document describes the differences between the test bed environment (`jokh38`) and the SMC distributed production environment, and the configuration changes required when deploying to each.

## Environments

| | Test Bed (`jokh38`) | SMC Production |
|---|---|---|
| **Host** | `jokh38@T640` (local dev machine) | `/home/SMC` (distributed deployment) |
| **Purpose** | Development, testing, debugging | Clinical operation |
| **GPU** | 4x RTX 2080 (sm_75) | Same or equivalent |
| **Base path** | `/home/jokh38/MOQUI_SMC` | `/home/SMC/MOQUI_SMC` |

## Configuration Changes

All environment-specific paths are in `config/config.yaml`. When switching between environments, update the following entries:

### 1. `paths.base_directory`

```yaml
# Test bed
paths:
  base_directory: "/home/jokh38/MOQUI_SMC"

# SMC production
paths:
  base_directory: "/home/SMC/MOQUI_SMC"
```

This is the root path. All other `paths.local.*` entries use `{base_directory}` interpolation, so changing this one value reconfigures the scan directory, output directories, database path, and all tool paths automatically.

### 2. `executables.local.python`

```yaml
# Test bed
executables:
  local:
    python: "/home/jokh38/MOQUI_SMC/mqi_communicator/.venv/bin/python3"

# SMC production
executables:
  local:
    python: "/home/SMC/MOQUI_SMC/mqi_communicator/.venv/bin/python3"
```

Must point to the venv Python of the target environment.

### 3. `ptn_checker.path`

```yaml
# Test bed
ptn_checker:
  path: "/home/jokh38/MOQUI_SMC/ptn_checker"

# SMC production
ptn_checker:
  path: "/home/SMC/MOQUI_SMC/ptn_checker"
```

### 4. `tps_generator.default_paths.base_directory`

```yaml
# Test bed
tps_generator:
  default_paths:
    base_directory: "/home/jokh38/MOQUI_SMC"

# SMC production
tps_generator:
  default_paths:
    base_directory: "/home/SMC/MOQUI_SMC"
```

## Known Issues on the Test Bed

These issues have been encountered when running the system on `jokh38`. They may or may not apply to SMC.

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

- [ ] Update all 4 path entries in `config/config.yaml` to `/home/SMC/MOQUI_SMC`
- [ ] Rebuild moqui_SMC binary (`cmake --build build/tps_env` from `moqui_SMC/`)
- [ ] Install Python dependencies in `.venv` (`pip install -r requirements.txt`)
- [ ] Pin `starlette<1.0` in the venv
- [ ] Verify GPU visibility (`nvidia-smi`)
- [ ] Verify scan directory exists and is populated
- [ ] Run `./start_all.sh` and confirm both `mqi_transfer` and `mqi_communicator` stay running
- [ ] Access web dashboard at `http://<host>:8080/ui/workflow`
