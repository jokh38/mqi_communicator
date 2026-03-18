# Clear DB Script Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a reusable shell script that deletes the configured SQLite database file and its SQLite sidecar files for a fresh application start.

**Architecture:** Add a small `scripts/clear_db.sh` utility that reads a YAML config path, resolves `paths.base_directory` and `paths.local.database_path`, validates the resolved target, and deletes only the SQLite database file plus `-wal` and `-shm` sidecars. Cover the behavior with a subprocess-based pytest test and document the command in the README.

**Tech Stack:** Bash, Python stdlib, PyYAML, pytest

---

### Task 1: Add script coverage first

**Files:**
- Create: `tests/test_clear_db_script.py`

**Step 1: Write the failing test**

Create a subprocess-based test that:
- writes a temporary config with a temp `base_directory`
- creates `tmp.db`, `tmp.db-wal`, and `tmp.db-shm`
- runs `bash scripts/clear_db.sh <config>`
- asserts the files are deleted and stdout mentions removal

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_clear_db_script.py -v`
Expected: FAIL because `scripts/clear_db.sh` does not exist yet.

### Task 2: Implement the script

**Files:**
- Create: `scripts/clear_db.sh`

**Step 1: Write minimal implementation**

Implement a Bash script that:
- accepts optional config path, defaulting to `config/config.yaml`
- uses Python with `yaml.safe_load` to resolve the configured DB path
- rejects empty or root-like paths
- deletes the DB, WAL, and SHM files if present
- prints which files were removed or skipped

**Step 2: Run focused test to verify it passes**

Run: `python -m pytest tests/test_clear_db_script.py -v`
Expected: PASS

### Task 3: Document usage

**Files:**
- Modify: `README.md`

**Step 1: Add usage note**

Document `bash scripts/clear_db.sh [config_path]` near the database/runtime usage section.

**Step 2: Run focused test again**

Run: `python -m pytest tests/test_clear_db_script.py -v`
Expected: PASS
