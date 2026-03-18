# MQI Communicator Scripts

## initialize_test.sh

Enhanced initialization script that clears database, logs, and output files for testing.

### Usage

```bash
./scripts/initialize_test.sh [config_path] [options]
```

### Options

| Option | Description |
|--------|-------------|
| `--db` | Clear database files only (`.db`, `.db-wal`, `.db-shm`) |
| `--logs` | Clear log files only (`logs/*.log`) |
| `--outputs` | Clear output files only (CSV and simulation outputs) |
| `--all` | Clear everything (database + logs + outputs) |
| *(none)* | **Default**: Clear everything |

### Examples

```bash
# Initialize everything (default)
./scripts/initialize_test.sh config/config.yaml

# Clear only database files
./scripts/initialize_test.sh config/config.yaml --db

# Clear only log files
./scripts/initialize_test.sh config/config.yaml --logs

# Clear only output files
./scripts/initialize_test.sh config/config.yaml --outputs

# Clear logs and outputs, keep database
./scripts/initialize_test.sh config/config.yaml --logs --outputs

# Explicitly clear everything
./scripts/initialize_test.sh config/config.yaml --all
```

### What Gets Cleared

#### Database (`--db`)
- `mqi_communicator.db`
- `mqi_communicator.db-wal` (Write-Ahead Log)
- `mqi_communicator.db-shm` (Shared Memory)

#### Logs (`--logs`)
- All `*.log` files in `logs/` directory

#### Outputs (`--outputs`)
- CSV output directories: `data/Outputs_csv/*/`
- Simulation output directories: `data/Dose_dcm/*/`

### Safety Features

- ✅ Validates config file exists
- ✅ Refuses to clear unsafe paths (`/`, `.`)
- ✅ Shows clear summary of what was removed
- ✅ Handles missing files/directories gracefully
- ✅ Exit code 0 on success, 1 on error

### Output Format

```
===================================================================
Initializing MQI Communicator - Clearing selected components
===================================================================

[1/3] Clearing database files...
  ✓ Removed mqi_communicator.db
  ✓ Removed mqi_communicator.db-wal

[2/3] Clearing log files...
  ✓ Removed 18 log file(s) from /path/to/logs

[3/3] Clearing output files...
  ✓ Removed 3 case director(ies) from CSV outputs
  • Simulation output directory not configured or not found

===================================================================
✓ Initialization complete: 22 item(s) removed
===================================================================
```
