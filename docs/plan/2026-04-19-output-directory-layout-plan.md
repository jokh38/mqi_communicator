# Output Directory Layout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Update `mqi_communicator` so existing processing uses the new room-grouped output layout rooted at `data/Output/{room_path}{case_id}` without changing processing behavior beyond directory paths.

**Architecture:** The current path model is centralized in `config/config.yaml` and resolved through `Settings.get_path(...)`, so the main implementation should be configuration-first. Code changes should be limited to places that display, infer, or test old folder names (`Outputs_csv`, `Dose_dcm`, `Daily`). No workflow logic, parsing logic, or execution sequencing should change.

**Tech Stack:** Python, YAML config templates, pytest, FastAPI UI provider tests

---

### Task 1: Confirm and lock the target path mapping in tests

**Files:**
- Modify: `tests/test_settings.py`
- Modify: `tests/test_tps_generator.py`
- Modify: `tests/test_ui_provider.py`
- Modify: `tests/test_web_failure_views.py`
- Modify: `tests/test_states.py`

**Step 1: Write/update failing assertions for the new directory layout**

Update tests so they expect:
- communicator CSV/TPS artifacts under `data/Output/{room_path}{case_id}/Log_csv`
- PTN output under `data/Output/{room_path}{case_id}/Daily_PTN`
- simulation/final DICOM output under `data/Output/{room_path}{case_id}/Dose`

Specific assertions to change:
- `tests/test_settings.py`
  - `test_repo_config_runs_built_tps_env_from_moqui_root`
  - `test_repo_config_resolves_ptn_checker_output_dir_with_room_grouping`
- `tests/test_tps_generator.py`
  - `test_generate_tps_file_uses_beam_specific_output_dir`
- `tests/test_ui_provider.py`
  - `test_provider_surfaces_finished_result_output_locations`
  - `test_provider_guesses_grouped_room_output_location`
- `tests/test_web_failure_views.py`
  - `test_case_detail_renders_grouped_room_output_locations`
- `tests/test_states.py`
  - rename local fixture dirs to reflect `Output/.../Dose` and `Output/.../Log_csv` where path names are asserted or implied

**Step 2: Run the targeted tests to verify they fail against current config**

Run:
```bash
python -m pytest tests/test_settings.py tests/test_tps_generator.py tests/test_ui_provider.py tests/test_web_failure_views.py tests/test_states.py -q
```

Expected:
- FAILURES showing old paths like `Outputs_csv`, `Dose_dcm`, or `Daily`

**Step 3: Commit the red test snapshot if working in an isolated branch**

```bash
git add tests/test_settings.py tests/test_tps_generator.py tests/test_ui_provider.py tests/test_web_failure_views.py tests/test_states.py
git commit -m "test: capture new output directory layout expectations"
```

### Task 2: Repoint centralized path templates in configuration

**Files:**
- Modify: `config/config.yaml`

**Step 1: Update the path templates only**

Change these path templates in `config/config.yaml`:
- `paths.local.csv_output_dir`
  - from: `"{base_directory}/data/Outputs_csv"`
  - to: `"{base_directory}/data/Output"`
- `paths.local.ptn_checker_output_dir`
  - from: `"{base_directory}/data/Daily/{room_path}{case_id}"`
  - to: `"{base_directory}/data/Output/{room_path}{case_id}/Daily_PTN"`
- `paths.local.simulation_output_dir`
  - from: `"{base_directory}/data/Dose_dcm/{room_path}{case_id}"`
  - to: `"{base_directory}/data/Output/{room_path}{case_id}/Dose"`
- `paths.local.final_dicom_dir`
  - from: `"{base_directory}/data/Dose_dcm/{room_path}{case_id}"`
  - to: `"{base_directory}/data/Output/{room_path}{case_id}/Dose"`
- `paths.local.tps_input_file`
  - from: `"{base_directory}/data/Outputs_csv/{room_path}{case_id}/moqui_tps_{beam_id}.in"`
  - to: `"{base_directory}/data/Output/{room_path}{case_id}/Log_csv/moqui_tps_{beam_id}.in"`

Update matching `moqui_tps_parameters` entries:
- `ParentDir` -> `"{base_directory}/data/Output/{room_path}{case_id}/Log_csv"`
- `DicomPath` -> `"{base_directory}/data/Output/{room_path}{case_id}/Log_csv/plan"`
- `logFilePath` -> `"{base_directory}/data/Output/{room_path}{case_id}/Log_csv/log"`
- `OutputDir` -> `"{base_directory}/data/Output/{room_path}{case_id}/Dose"`

**Step 2: Run config- and path-focused tests**

Run:
```bash
python -m pytest tests/test_settings.py tests/test_tps_generator.py -q
```

Expected:
- PASS for config resolution and generated TPS path assertions

**Step 3: Commit the config template change**

```bash
git add config/config.yaml tests/test_settings.py tests/test_tps_generator.py
git commit -m "config: point communicator outputs to grouped Output layout"
```

### Task 3: Update UI/provider assumptions about legacy folder names

**Files:**
- Modify: `src/ui/provider.py`
- Modify: `tests/test_ui_provider.py`
- Modify: `tests/test_web_failure_views.py`

**Step 1: Replace hard-coded sibling folder guesses with the new folder layout**

In `src/ui/provider.py`:
- keep using resolved config paths where available
- replace fallback guesses:
  - `"Outputs_csv"` -> `"Output"` plus `Log_csv` beneath the case root
  - `"Dose_dcm"` -> `"Output"` plus `Dose` beneath the case root
- ensure `_resolve_output_candidates(...)` still deduplicates paths and still supports room-grouped paths

Recommended concrete behavior:
- fallback CSV/TPS candidate should resolve to `.../Output/{room}/{case_id}/Log_csv`
- fallback DICOM candidate should resolve to `.../Output/{room}/{case_id}/Dose`

If `_guess_sibling_output_dir(...)` remains generic, add a thin wrapper or a dedicated helper for the new nested `Output/<room>/<case>/subdir` pattern instead of overloading old folder-name semantics.

**Step 2: Run UI/provider targeted tests**

Run:
```bash
python -m pytest tests/test_ui_provider.py tests/test_web_failure_views.py -q
```

Expected:
- PASS with output locations rendered under `data/Output/...`

**Step 3: Commit the UI/path inference update**

```bash
git add src/ui/provider.py tests/test_ui_provider.py tests/test_web_failure_views.py
git commit -m "fix: surface grouped output locations in UI"
```

### Task 4: Align state-oriented tests with the new directory names

**Files:**
- Modify: `tests/test_states.py`

**Step 1: Update simulation and validation test fixtures**

Adjust temporary directory names used in `tests/test_states.py` so they mirror the new layout:
- simulation outputs under `tmp_path / "Output" / "G1" / "case-abc" / "Dose"` or the flat equivalent used by the test
- TPS input directories under `tmp_path / "Output" / "G1" / "case-abc" / "Log_csv"`

Do not change test intent. Only align directory naming with the new structure.

**Step 2: Run state tests**

Run:
```bash
python -m pytest tests/test_states.py -q
```

Expected:
- PASS with no behavior change beyond path expectations

**Step 3: Commit the test alignment**

```bash
git add tests/test_states.py
git commit -m "test: align workflow state fixtures with new output layout"
```

### Task 5: Component validation and regression check

**Files:**
- No code changes required unless failures expose more hard-coded paths

**Step 1: Run the full targeted regression suite**

Run:
```bash
python -m pytest tests/test_settings.py tests/test_tps_generator.py tests/test_ui_provider.py tests/test_web_failure_views.py tests/test_states.py -q
```

Expected:
- all targeted tests PASS

**Step 2: Optional wider grep to catch remaining hard-coded operational paths**

Run:
```bash
rg -n "Outputs_csv|Dose_dcm|data/Daily|Daily_PTN|Log_csv|data/Output" src tests config
```

Expected:
- remaining legacy hits should be documentation-only or intentionally untouched references

**Step 3: Record validation scope in the handoff**

Report as:
- `component validated` if only the targeted pytest suite was run
- not `end-to-end validated`, because no live remote execution or real case run was performed

**Step 4: Final commit**

```bash
git add config/config.yaml src/ui/provider.py tests/test_settings.py tests/test_tps_generator.py tests/test_ui_provider.py tests/test_web_failure_views.py tests/test_states.py
git commit -m "refactor: adopt grouped output directory layout"
```
