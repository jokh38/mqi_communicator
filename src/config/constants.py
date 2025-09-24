# =====================================================================================
# Target File: src/config/constants.py
# Source Reference: Constants scattered throughout original codebase
# =====================================================================================

"""Defines developer-managed constants for the application.

These constants are fixed values that are not meant to be configured
by users. They represent hardcoded application behavior and data
structures.
"""

# ===== DATABASE SCHEMA CONSTANTS =====
# These define the application's data structure
DB_SCHEMA_VERSION = "1.0"
CASES_TABLE_NAME = "cases"
GPU_RESOURCES_TABLE_NAME = "gpu_resources"
WORKFLOW_HISTORY_TABLE_NAME = "workflow_history"

# ===== FILE SYSTEM CONSTANTS =====
# Fixed file extensions and patterns the application recognizes
DICOM_FILE_EXTENSIONS = [".dcm", ".dicom"]
TPS_INPUT_FILE_NAME = "moqui_tps.in"
TPS_OUTPUT_FILE_PATTERN = "dose_*.raw"
LOG_FILE_EXTENSIONS = [".log", ".out", ".err"]

# Required input files for case processing
REQUIRED_CASE_FILES = [
    "input.dat",
    "geometry.dcm",
    "structure.dcm"
]

# ===== COMMAND TEMPLATES =====
# Fixed command patterns used throughout the application
NVIDIA_SMI_QUERY_COMMAND = (
    "nvidia-smi --query-gpu=index,uuid,utilization.gpu,memory.used,"
    "memory.total,temperature.gpu --format=csv,noheader,nounits"
)
PUEUE_STATUS_COMMAND = "pueue status --json"
PUEUE_ADD_COMMAND_TEMPLATE = "pueue add --group gpu{gpu_id} '{command}'"

# ===== WORKFLOW STATE NAMES =====
# Fixed workflow step identifiers (referenced in domain/states.py)
WORKFLOW_STEPS = [
    "PENDING",
    "CSV_INTERPRETING",
    "TPS_GENERATION",
    "UPLOADING",
    "HPC_SUBMISSION",
    "SIMULATION_RUNNING",
    "POSTPROCESSING",
    "COMPLETED",
    "FAILED"
]

# ===== VALIDATION CONSTANTS =====
# Fixed validation rules and limits
MAX_CASE_ID_LENGTH = 64
MIN_GPU_MEMORY_MB = 2048  # 2GB minimum for MOQUI simulation
MAX_BEAM_NUMBERS = 100
CASE_ID_VALID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"

# ===== ERROR MESSAGE TEMPLATES =====
# Fixed error message templates with placeholders
ERROR_MSG_CASE_NOT_FOUND = "Case '{case_id}' not found in database"
ERROR_MSG_GPU_NOT_AVAILABLE = "No available GPU found for case '{case_id}'"
ERROR_MSG_INVALID_CASE_PATH = (
    "Invalid case path: '{path}' does not exist or is not accessible"
)
ERROR_MSG_TPS_GENERATION_FAILED = "TPS generation failed for case '{case_id}': {details}"

# ===== UI DISPLAY CONSTANTS =====
# Fixed UI layout and formatting values
TERMINAL_MIN_WIDTH = 120
TERMINAL_MIN_HEIGHT = 30
TABLE_MAX_ROWS = 50
PROGRESS_BAR_WIDTH = 20

# Status color mappings (fixed application theme)
STATUS_COLORS = {
    "PENDING": "yellow",
    "RUNNING": "blue",
    "COMPLETED": "green",
    "FAILED": "red",
    "CANCELLED": "orange"
}

# ===== SYSTEM RESOURCE LIMITS =====
# Fixed limits to prevent resource exhaustion
MAX_CONCURRENT_CASES = 10
MAX_LOG_LINES_PER_CASE = 10000
MAX_ERROR_HISTORY_ENTRIES = 1000

# ===== MOQUI TPS PARAMETER NAMES =====
# Fixed parameter names expected by MOQUI TPS (cannot be changed)
TPS_REQUIRED_PARAMS = [
    'GPUID',
    'DicomDir',
    'logFilePath',
    'OutputDir',
    'BeamNumbers'
]

TPS_FIXED_PARAMS = {
    'RandomSeed': -1932780356,
    'UseAbsolutePath': True,
    'Verbosity': 0,
    'UsingPhantomGeo': True,
    'Scorer': 'Dose',
    'SourceType': 'FluenceMap',
    'SimulationType': 'perBeam',
    'ScoreToCTGrid': True,
    'OutputFormat': 'raw',
    'OverwriteResults': True,
    'ParticlesPerHistory': 1,
    'TwoCentimeterMode': True
}