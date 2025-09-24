# MQI Communicator Installation Guide

This guide provides step-by-step instructions for setting up the MQI Communicator application on your system.

## Prerequisites

### System Requirements
- **Python 3.8 or higher** (Python 3.10+ recommended)
- **Operating System**: Windows, Linux, or macOS
- **Memory**: Minimum 4GB RAM (8GB+ recommended for multi-case processing)
- **Storage**: At least 10GB free space for case data and logs

### External System Dependencies

#### NVIDIA GPU Support (Required for GPU monitoring)
- **NVIDIA drivers** with CUDA support installed
- **nvidia-smi** command available in system PATH
- For HPC systems: NVIDIA drivers on remote compute nodes

#### HPC Integration (Required for remote processing)
- **SSH client** configured on local system
- **SSH key-based authentication** set up for HPC access
- **SLURM workload manager** on HPC system (commands: `sbatch`, `squeue`, `scancel`)
- Network connectivity to HPC system

## Installation Steps

### 1. Clone or Download the Repository

```bash
# If using git
git clone <repository-url>
cd mqi_com_refactor

# Or extract from archive
unzip mqi_com_refactor.zip
cd mqi_com_refactor
```

### 2. Create Python Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate

# On Linux/macOS:
source venv/bin/activate
```

### 3. Install Python Dependencies

```bash
# Install required packages
pip install -r requirements.txt

# Verify installation
pip list
```

### 4. Configure the Application

#### Create Configuration File
Copy and customize the configuration file:

```bash
# Copy example config
cp config/config.yaml config/config_local.yaml
```

#### Edit Configuration Settings
Open `config/config_local.yaml` and update the following sections:

##### Base Directory
```yaml
paths:
  base_directory: "/path/to/your/mqi/data"  # Update this path
```

##### HPC Connection
```yaml
hpc_connection:
  host: "your.hpc.server.com"
  user: "your_username"
  ssh_key_path: "/path/to/your/ssh/private/key"  # Optional
  connection_timeout_seconds: 10
  command_timeout_seconds: 600
```

##### Executable Paths
```yaml
executables:
  python_interpreter: "/usr/bin/python3"  # Or your Python path
  mqi_interpreter: "{base_directory}/mqi_interpreter/main_cli.py"
  raw_to_dicom: "{base_directory}/RawToDCM/moqui_raw2dicom.py"
```

### 5. Set Up Directory Structure

Create the required directory structure:

```bash
# Create base directories (adjust path as needed)
mkdir -p /path/to/your/mqi/data/{data/log_SHI,data/cases,database,logs}

# Create case processing directories
mkdir -p /path/to/your/mqi/data/data/cases/{intermediate,raw,dicom}
```

### 6. Initialize Database

The application will automatically create the SQLite database on first run, but you can verify the database directory exists:

```bash
# Ensure database directory exists
mkdir -p /path/to/your/mqi/data/database
```

### 7. Set Up SSH Keys (For HPC Access)

#### Generate SSH Key Pair (if not already done)
```bash
# Generate SSH key
ssh-keygen -t rsa -b 4096 -f ~/.ssh/mqi_hpc_key

# Copy public key to HPC system
ssh-copy-id -i ~/.ssh/mqi_hpc_key.pub username@hpc.server.com
```

#### Test SSH Connection
```bash
# Test connection to HPC
ssh -i ~/.ssh/mqi_hpc_key username@hpc.server.com "echo 'Connection successful'"
```

### 8. Verify Installation

#### Test Basic Components
```bash
# Check Python and dependencies
python -c "import watchdog, rich, paramiko, yaml; print('All dependencies imported successfully')"

# Test nvidia-smi (if GPU monitoring is needed)
nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader,nounits
```

#### Run Application Test
```bash
# Run with test configuration
python main.py config/config_local.yaml
```

## Configuration Options

### Environment Variables
You can override configuration using environment variables:

```bash
# Database settings
export MQI_DB_PATH="/custom/path/to/mqi.db"
export MQI_LOG_LEVEL="DEBUG"

# Processing settings
export MQI_MAX_WORKERS="8"
export MQI_CASE_TIMEOUT="7200"

# GPU monitoring
export MQI_GPU_MONITOR_INTERVAL="30"
```

### Configuration File Sections

#### Dashboard Settings
```yaml
dashboard:
  auto_start: true                    # Auto-start dashboard UI
  refresh_interval_seconds: 1         # UI refresh rate
```

#### Application Settings
```yaml
application:
  max_workers: 4                      # Concurrent case processing
  scan_interval_seconds: 60           # Directory scan frequency
  polling_interval_seconds: 300       # HPC status check frequency
  local_execution_timeout_seconds: 300 # Local command timeout
```

#### Retry Policy
```yaml
retry_policy:
  max_retries: 3                      # Maximum retry attempts
  initial_delay_seconds: 5            # Initial retry delay
  max_delay_seconds: 60               # Maximum retry delay
  backoff_multiplier: 2.0             # Exponential backoff multiplier
```

## Running the Application

### Start the Application
```bash
# Run with default config
python main.py

# Run with custom config
python main.py config/config_local.yaml

# Run with specific log level
MQI_LOG_LEVEL=DEBUG python main.py
```

### Monitor Application
- The **dashboard** will automatically start if `dashboard.auto_start: true`
- **Log files** are written to the configured log directory
- **Database** contains case progress and GPU resource information

### Graceful Shutdown
- Press `Ctrl+C` to stop the application
- The system will complete current operations before shutting down
- All database connections and processes are properly cleaned up

## Troubleshooting

### Common Issues

#### Import Errors
```bash
# If you get import errors, verify virtual environment is activated
which python
pip list | grep -E "watchdog|rich|paramiko|PyYAML"
```

#### Permission Issues
```bash
# Ensure directories are writable
chmod -R 755 /path/to/your/mqi/data
```

#### SSH Connection Issues
```bash
# Test SSH connection manually
ssh -vvv -i ~/.ssh/mqi_hpc_key username@hpc.server.com

# Check SSH key permissions
chmod 600 ~/.ssh/mqi_hpc_key
chmod 644 ~/.ssh/mqi_hpc_key.pub
```

#### GPU Monitoring Issues
```bash
# Verify nvidia-smi works
nvidia-smi --help

# Check GPU status
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv
```

### Log Files
Check application logs for detailed error information:

```bash
# View main application log
tail -f /path/to/your/mqi/data/logs/mqi_communicator.log

# View specific worker logs
ls /path/to/your/mqi/data/logs/worker_*
```

### Database Issues
If database problems occur:

```bash
# Check database file permissions
ls -la /path/to/your/mqi/data/database/

# SQLite command line access (if needed)
sqlite3 /path/to/your/mqi/data/database/mqi_communicator.db ".tables"
```

## Development Setup

### Additional Development Dependencies
```bash
# Install development tools (optional)
pip install pytest black flake8 mypy
```

### Code Quality Tools
```bash
# Format code
black src/ main.py

# Lint code
flake8 src/ main.py

# Type checking
mypy src/ main.py
```

### Running Tests
```bash
# Run tests (when test suite is available)
pytest tests/
```

## Support

For issues and questions:
1. Check the **log files** for detailed error messages
2. Verify **configuration settings** match your environment
3. Test **SSH connectivity** and **GPU availability** independently
4. Review **file permissions** and **directory structure**

## Next Steps

After successful installation:
1. **Test with sample case data** to verify end-to-end functionality
2. **Monitor resource usage** during processing
3. **Set up backup procedures** for case data and database
4. **Configure monitoring** for production deployments