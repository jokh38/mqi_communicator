# MQI Communicator Testing Instructions

## Overview
This document provides comprehensive testing procedures for the MQI Communicator system, including monitoring, validation, and inconsistency detection across all system components.

## Test Environment Setup

### Prerequisites
- Python environment with all dependencies from `requirements.txt`
- NVIDIA GPU with `nvidia-smi` available
- SQLite3 client for database inspection
- Access to monitoring directories and log files

### Key Directories and Files

| Component | Location | Purpose |
|-----------|----------|---------|
| Main Application | `main.py` | Entry point for MQI Communicator |
| Configuration | `config/config.yaml` | System configuration |
| Application Logs | `logs/` | Main communicator logs |
| Simulation Logs | `logs/sim_{case_id}_{beam_id}.log` | Per-beam simulation logs |
| Database | `../data/mqi_communicator.db` | SQLite state database |
| Input Directory | `../data/SHI_log/` | Watched directory for new cases |
| CSV Outputs | `../data/Outputs_csv/` | Interpreted CSV data |
| DICOM Outputs | `../data/Dose_dcm/` | Final dose DICOM files |

## Testing Procedure

### Phase 1: Database Initialization

#### 1.1 Clean Existing Database
```bash
# Navigate to project directory
cd /home/jokh38/MOQUI_SMC/mqi_communicator

# Backup existing database (optional)
if [ -f ../data/mqi_communicator.db ]; then
    cp ../data/mqi_communicator.db ../data/mqi_communicator.db.backup_$(date +%Y%m%d_%H%M%S)
    echo "✓ Database backed up"
fi

# Remove existing database to start fresh
rm -f ../data/mqi_communicator.db
rm -f ../data/mqi_communicator.db-shm
rm -f ../data/mqi_communicator.db-wal
echo "✓ Database files removed"
```

**Expected Result:**
- Existing database files are backed up (if they exist)
- All database-related files (.db, .db-shm, .db-wal) are removed
- Clean slate for testing

#### 1.2 Initialize Fresh Database
```bash
# The database will be automatically initialized on first run
# Verify database directory exists
mkdir -p ../data
echo "✓ Data directory ready"

# Optional: Verify configuration is correct
python3 -c "from src.config.settings import Settings; s = Settings(); print('✓ Configuration valid')"
```

**Expected Result:**
- Data directory exists
- Configuration loads successfully
- System ready for fresh start

### Phase 2: Application Launch

#### 2.1 Start MQI Communicator
```bash
# Start in one terminal
cd /home/jokh38/MOQUI_SMC/mqi_communicator
python3 main.py config/config.yaml 2>&1 | tee test_run.log
```

#### 2.2 Initial Health Checks (within 30 seconds of launch)

**Monitor Log Output:**
```bash
# In another terminal, watch main log
tail -f logs/*.log | grep -E "(ERROR|WARNING|INFO|started|initialized)"
```

**Check Process Tree:**
```bash
# Verify main process and child processes
ps aux | grep -E "(main\.py|python.*mqi)" | grep -v grep

# Count worker processes (should match max_workers in config)
ps aux | grep -E "python.*worker" | grep -v grep | wc -l
```

**Verify Web Dashboard:**
```bash
# Check if web interface is accessible (if enabled)
curl -s http://localhost:8080 > /dev/null && echo "Dashboard accessible" || echo "Dashboard unavailable"

# Check Streamlit dashboard (if enabled)
curl -s http://localhost:8501 > /dev/null && echo "Streamlit accessible" || echo "Streamlit unavailable"
```

**Database Monitor:**
```bash
# Watch database updates in real-time
watch -n 2 "sqlite3 ../data/mqi_communicator.db 'SELECT case_id, status, progress FROM cases;'"
```

### Phase 3: Case Processing Monitoring

#### 3.1 Trigger Test Case
```bash
# Place test case in scan directory or wait for automatic detection
# Monitor file watcher detection
tail -f logs/*.log | grep -i "watching\|detected\|new case"
```

#### 3.2 Multi-Component Monitoring

**Terminal 1: Application Logs**
```bash
tail -f logs/*.log | grep -E "(Processing|status|ERROR|SUCCESS)"
```

**Terminal 2: Database State**
```bash
# Monitor case status progression
watch -n 1 "sqlite3 ../data/mqi_communicator.db '
SELECT
  case_id,
  status,
  ROUND(progress, 1) as progress_pct,
  error_message
FROM cases
ORDER BY created_at DESC
LIMIT 5;'"
```

**Terminal 3: Beam Status**
```bash
# Monitor individual beam processing
watch -n 2 "sqlite3 ../data/mqi_communicator.db '
SELECT
  beam_id,
  case_id,
  status,
  gpu_id
FROM beams
WHERE case_id IN (SELECT case_id FROM cases ORDER BY created_at DESC LIMIT 3)
ORDER BY case_id, beam_id;'"
```

**Terminal 4: GPU Status**
```bash
# Monitor GPU utilization
watch -n 3 "nvidia-smi --query-gpu=index,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu --format=csv,noheader,nounits"
```

**Terminal 5: Worker Processes**
```bash
# Monitor spawned worker processes
watch -n 2 "ps aux | grep -E 'python.*worker|tps_env' | grep -v grep"
```

**Terminal 6: Output Directories**
```bash
# Monitor file creation in output directories
watch -n 5 "echo '=== CSV Outputs ==='; ls -lht ../data/Outputs_csv/ | head -5; echo '=== DICOM Outputs ==='; ls -lht ../data/Dose_dcm/ | head -5"
```

#### 3.3 Detailed Log Analysis

**Check Simulation Logs:**
```bash
# Find latest simulation logs
ls -lt logs/sim_*.log | head -5

# Monitor for completion markers
tail -f logs/sim_*.log | grep -E "(Simulation|finalizing|FATAL|ERROR)"
```

**CSV Interpreter Logs:**
```bash
# Check CSV interpretation phase
grep -i "csv.*interpret" logs/*.log | tail -20
```

**TPS Generation Logs:**
```bash
# Check TPS file generation
grep -i "tps.*generat" logs/*.log | tail -20
ls -lht ../data/Outputs_csv/*/moqui_tps_*.in | head -10
```

### Phase 4: Validation and Consistency Checks

#### 4.1 Database Consistency

**Case-Beam Relationship:**
```bash
sqlite3 ../data/mqi_communicator.db "
SELECT
  c.case_id,
  c.status as case_status,
  COUNT(b.beam_id) as beam_count,
  SUM(CASE WHEN b.status = 'COMPLETED' THEN 1 ELSE 0 END) as completed_beams,
  SUM(CASE WHEN b.status = 'FAILED' THEN 1 ELSE 0 END) as failed_beams,
  SUM(CASE WHEN b.status = 'RUNNING' THEN 1 ELSE 0 END) as running_beams
FROM cases c
LEFT JOIN beams b ON c.case_id = b.case_id
GROUP BY c.case_id, c.status
ORDER BY c.created_at DESC;"
```

**Expected Result:**
- Case status should reflect beam completion (COMPLETED when all beams done)
- No orphaned beams without parent cases
- Beam counts match expected number from DICOM file

**GPU Assignment Consistency:**
```bash
sqlite3 ../data/mqi_communicator.db "
SELECT
  g.gpu_id,
  g.status as gpu_status,
  COUNT(b.beam_id) as assigned_beams
FROM gpus g
LEFT JOIN beams b ON g.gpu_id = b.gpu_id AND b.status = 'RUNNING'
GROUP BY g.gpu_id, g.status;"
```

**Expected Result:**
- Running beams should have valid GPU assignments
- GPU status should match actual nvidia-smi output
- No GPU over-assignment (multiple beams on single GPU unless intended)

#### 4.2 File System Consistency

**Output File Validation:**
```bash
# Check CSV outputs exist for processed cases
for case_dir in ../data/Outputs_csv/*/; do
  case_id=$(basename "$case_dir")
  echo "Case: $case_id"
  echo "  TPS files: $(find "$case_dir" -name "moqui_tps_*.in" | wc -l)"
  echo "  RTPLAN dir: $([ -d "$case_dir/rtplan" ] && echo "EXISTS" || echo "MISSING")"
done

# Check DICOM outputs
for case_dir in ../data/Dose_dcm/*/; do
  case_id=$(basename "$case_dir")
  echo "Case: $case_id"
  echo "  Dose files: $(find "$case_dir" -name "*.dcm" | wc -l)"
done
```

**Expected Result:**
- Each completed case has corresponding output files
- Number of TPS files matches number of beams
- DICOM dose files exist for completed beams

**Log File Completeness:**
```bash
# Check simulation log completeness
sqlite3 ../data/mqi_communicator.db "SELECT case_id, beam_id, status FROM beams WHERE status IN ('COMPLETED', 'RUNNING');" | while IFS='|' read case_id beam_id status; do
  log_file="logs/sim_${case_id}_${beam_id}.log"
  if [ -f "$log_file" ]; then
    echo "✓ $log_file exists (status: $status)"
    # Check for completion marker
    if grep -q "Simulation finalizing" "$log_file"; then
      echo "  ✓ Contains completion marker"
    else
      echo "  ✗ Missing completion marker"
    fi
  else
    echo "✗ $log_file MISSING (status: $status)"
  fi
done
```

#### 4.3 Process State Validation

**Worker Process Alignment:**
```bash
# Compare running beams in DB vs actual processes
echo "=== Running Beams in Database ==="
sqlite3 ../data/mqi_communicator.db "SELECT case_id, beam_id, gpu_id FROM beams WHERE status = 'RUNNING';"

echo "=== Active Worker Processes ==="
ps aux | grep -E "tps_env|worker" | grep -v grep

echo "=== GPU Processes ==="
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
```

**Expected Result:**
- Number of running beams matches active worker processes
- GPU processes align with database GPU assignments
- No zombie or orphaned processes

#### 4.4 Error Pattern Detection

**Scan for Errors:**
```bash
# Application errors
echo "=== Application Errors ==="
grep -i "error\|exception\|fatal" logs/*.log | tail -20

# Simulation errors
echo "=== Simulation Errors ==="
grep -i "error\|fatal\|segmentation" logs/sim_*.log | tail -20

# Database errors
echo "=== Failed Cases/Beams ==="
sqlite3 ../data/mqi_communicator.db "
SELECT case_id, beam_id, status, error_message
FROM beams
WHERE status = 'FAILED' OR error_message IS NOT NULL;"
```

### Phase 5: Shutdown and Post-Test Analysis

#### 5.1 Graceful Shutdown
```bash
# Send SIGINT to main process
pkill -SIGINT -f "python3 main.py"

# Monitor shutdown sequence
tail -f logs/*.log | grep -i "shutdown\|stopping\|closed"
```

**Expected Result:**
- All components shut down gracefully
- GPU monitor stops
- SSH connections closed
- Database connections released
- No orphaned worker processes

#### 5.2 Post-Test Database State
```bash
# Final case summary
sqlite3 ../data/mqi_communicator.db "
SELECT
  status,
  COUNT(*) as count,
  ROUND(AVG(progress), 1) as avg_progress
FROM cases
GROUP BY status;"

# Check for stuck beams
sqlite3 ../data/mqi_communicator.db "
SELECT case_id, beam_id, status, updated_at
FROM beams
WHERE status IN ('RUNNING', 'PENDING')
ORDER BY updated_at;"
```

## Inconsistency Detection Checklist

### Critical Inconsistencies (Require Investigation)

- [ ] **Database-Process Mismatch**: Beams marked RUNNING in DB but no corresponding process
- [ ] **GPU Assignment Conflict**: Multiple beams assigned to same GPU simultaneously
- [ ] **File-Database Mismatch**: Completed beams in DB with missing output files
- [ ] **Log-Status Mismatch**: Logs show completion but DB shows RUNNING/FAILED
- [ ] **Orphaned Resources**: GPU assignments without corresponding beam records
- [ ] **Stuck Cases**: Cases in PROCESSING for extended time with no active beams
- [ ] **Memory Leaks**: Continuously growing process memory over time
- [ ] **Zombie Processes**: Worker processes remain after beam completion

### Warning-Level Inconsistencies (Monitor)

- [ ] **Slow Progress**: Beams taking significantly longer than expected
- [ ] **GPU Underutilization**: Available GPUs not being assigned to pending beams
- [ ] **Log Size Growth**: Excessive log file sizes indicating repetitive errors
- [ ] **Database Bloat**: Unreleased SQLite locks or journal file growth
- [ ] **Partial Outputs**: Some output files missing for completed cases
- [ ] **Config-Runtime Mismatch**: Runtime behavior differs from config settings

## Expected System Behavior

### Normal Operation Flow

1. **Startup Sequence** (0-10s)
   - Logging initialized
   - Database schema verified
   - GPU monitor started
   - File watcher activated
   - Dashboard launched (if enabled)

2. **Case Detection** (varies)
   - New case directory detected in scan path
   - Case and beam records created in database
   - Status: PENDING → CSV_INTERPRETING

3. **CSV Interpretation** (10-60s per case)
   - CSV interpreter processes case files
   - Output written to Outputs_csv directory
   - Status: CSV_INTERPRETING → PROCESSING

4. **TPS Generation** (5-20s per case)
   - GPU assignments allocated
   - TPS input files generated per beam
   - Status: PROCESSING (TPS_GENERATION substatus)

5. **Simulation Execution** (varies by beam complexity)
   - Worker processes spawned
   - GPU resources assigned
   - Beam status: PENDING → RUNNING
   - Simulation logs created

6. **Completion** (5-30s per beam)
   - Simulation finalizes
   - DICOM files generated
   - Beam status: RUNNING → COMPLETED
   - GPU resources released

7. **Case Completion**
   - All beams completed
   - Case status: PROCESSING → COMPLETED
   - Final progress: 100%

### Performance Baselines

| Metric | Expected Range | Alert Threshold |
|--------|----------------|-----------------|
| Case Detection Latency | < 5s | > 30s |
| CSV Interpretation | 10-60s | > 120s |
| TPS Generation | 5-20s | > 60s |
| Beam Simulation | 1-30min | > 60min |
| GPU Allocation Time | < 2s | > 10s |
| Database Query Time | < 100ms | > 1s |
| Memory per Worker | 100-500MB | > 2GB |

## Automated Test Commands

### Quick Health Check
```bash
#!/bin/bash
echo "=== MQI Communicator Health Check ==="
echo "1. Process Status:"
pgrep -f "main.py" && echo "  ✓ Main process running" || echo "  ✗ Main process NOT running"

echo "2. Database:"
sqlite3 ../data/mqi_communicator.db "SELECT COUNT(*) FROM cases;" && echo "  ✓ Database accessible" || echo "  ✗ Database error"

echo "3. GPU:"
nvidia-smi > /dev/null 2>&1 && echo "  ✓ GPU accessible" || echo "  ✗ GPU not accessible"

echo "4. Recent Errors:"
error_count=$(grep -i "error\|fatal" logs/*.log 2>/dev/null | wc -l)
echo "  Recent errors: $error_count"

echo "5. Active Beams:"
active_beams=$(sqlite3 ../data/mqi_communicator.db "SELECT COUNT(*) FROM beams WHERE status = 'RUNNING';")
echo "  Running beams: $active_beams"
```

### Full Validation Script
```bash
#!/bin/bash
# Run comprehensive validation
set -e

echo "Running full MQI Communicator validation..."

# Database consistency
sqlite3 ../data/mqi_communicator.db <<EOF
.mode column
.headers on
SELECT 'Cases' as entity, COUNT(*) as total FROM cases
UNION ALL
SELECT 'Beams', COUNT(*) FROM beams
UNION ALL
SELECT 'GPUs', COUNT(*) FROM gpus;
EOF

# File system check
echo -e "\n=== Output Directory Status ==="
echo "CSV outputs: $(find ../data/Outputs_csv -type f | wc -l) files"
echo "DICOM outputs: $(find ../data/Dose_dcm -type f -name "*.dcm" | wc -l) files"

# Process check
echo -e "\n=== Process Status ==="
ps aux | grep -E "(main\.py|worker|tps_env)" | grep -v grep || echo "No processes found"

# GPU check
echo -e "\n=== GPU Status ==="
nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader

echo -e "\nValidation complete."
```

## Troubleshooting Common Issues

### Issue: Cases Stuck in CSV_INTERPRETING
**Check:**
- CSV interpreter process errors in logs
- Input file permissions and format
- CSV output directory write permissions

### Issue: Beams Stuck in PENDING
**Check:**
- GPU availability
- GPU monitor service status
- TPS file generation completion

### Issue: Simulation Logs Show Errors
**Check:**
- GPU memory availability
- CUDA/driver compatibility
- Input file correctness (TPS parameters)

### Issue: Database Locked Errors
**Check:**
- Multiple processes accessing database
- Unclosed connections
- Journal file size and WAL mode

## Conclusion

Follow this document systematically to ensure comprehensive testing of the MQI Communicator system. Pay special attention to the Inconsistency Detection Checklist and validate all critical components before declaring the system production-ready.
