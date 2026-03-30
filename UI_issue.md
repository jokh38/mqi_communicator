# Root Cause Analysis: Pydantic Config Validation Silently Dropping Fields

**Date:** 2026-03-30
**Status:** Confirmed
**Severity:** High — causes dashboard startup failure and GPU monitoring failure

---

## Executive Summary

The Pydantic configuration models introduced in Phase 4 (`pydantic_models.py`) define strict schemas for each config section. Several fields present in `config/config.yaml` are **not** defined in their corresponding Pydantic models. Because Pydantic's default behavior for unknown fields is `extra = "ignore"`, these fields are **silently dropped** during validation. The application then falls back to hardcoded defaults, causing two primary failures: the dashboard does not auto-start, and GPU monitoring cannot execute.

---

## Root Cause

### The Mechanism

1. `Settings.__init__()` calls `_validate_config()`, which passes each YAML section into `AppConfig(**config_dict)`.
2. `AppConfig` has `extra = "allow"` at the root level — but this **does not propagate to nested models**.
3. Nested models (`UIConfig`, `GpuConfig`, etc.) use Pydantic's default `extra = "ignore"`.
4. Any field in the YAML that doesn't have a matching attribute in the Pydantic model is **silently discarded**.
5. `get_ui_config()` and `get_gpu_config()` return `self._validated_config.ui.model_dump()` — the truncated, validated dict — **not** the original YAML data.

### Why `extra = "allow"` on `AppConfig` Doesn't Help

```python
class AppConfig(BaseModel):
    model_config = {
        "extra": "allow",  # This only applies to AppConfig's own fields
    }
```

This setting allows extra top-level keys in `AppConfig` itself. It does **not** cascade into nested models like `UIConfig` or `GpuConfig`. Those models remain at the default `extra = "ignore"`.

---

## Issue 1: Dashboard Not Starting

### Affected Fields

| YAML Key | Expected Consumer | Status |
|---|---|---|
| `ui.auto_start` | `main.py:154` | ❌ Dropped |
| `ui.port` | `UIProcessManager` | ❌ Dropped |
| `ui.web.enabled` | `UIProcessManager._get_ui_command()` | ❌ Dropped |
| `ui.web.port` | `UIProcessManager` | ❌ Dropped |
| `ui.web.ttyd_path` | `UIProcessManager` | ❌ Dropped |
| `ui.web.bind_address` | `UIProcessManager` | ❌ Dropped |
| `ui.web.permit_write` | `UIProcessManager` | ❌ Dropped |
| `ui.web.reconnect` | `UIProcessManager` | ❌ Dropped |

### Current Pydantic Model (`UIConfig`)

```python
class UIConfig(BaseModel):
    refresh_interval_seconds: int = Field(default=1, ge=1, le=60)
    max_cases_display: int = Field(default=50, ge=1, le=1000)
```

Only 2 fields defined. The YAML has 9+ fields (including the nested `web` sub-object).

### Impact Chain

1. `main.py:154` — `self.settings.get_ui_config().get("auto_start", False)` → returns `False` (default).
2. `start_dashboard()` exits early with `"Dashboard auto-start disabled"`.
3. Dashboard process is never spawned.

### Secondary Impact: Web Mode Silently Disabled

`UIProcessManager._get_ui_command()` reads:

```python
ui_config = self.config.get_ui_config()
web_config = ui_config.get("web", {})  # {} because "web" was dropped
web_config.get("enabled", False)       # False — falls back to terminal mode
```

Even if `auto_start` were fixed, the ttyd web dashboard would never activate. The app would always use terminal mode (`CREATE_NEW_CONSOLE` on Windows).

### Tertiary Impact: Field Name Mismatch

`display.py:51` reads:

```python
self._refresh_rate = ui_config.get("refresh_interval", 2)
```

But the Pydantic model serializes it as `refresh_interval_seconds` (with `_seconds` suffix). Even the one field that IS validated gets read under the wrong key name, falling back to hardcoded `2` instead of the configured `1`.

---

## Issue 2: GPU Monitor Failure

### Affected Fields

| YAML Key | Expected Consumer | Status |
|---|---|---|
| `gpu.gpu_monitor_command` | `main.py:start_gpu_monitor()` | ❌ Dropped |
| `gpu.monitor_interval` | `main.py:start_gpu_monitor()` | ❌ Dropped |

### Current Pydantic Model (`GpuConfig`)

```python
class GpuConfig(BaseModel):
    enabled: bool = True
    memory_threshold_mb: int = 1000
    utilization_threshold_percent: int = 80
    polling_interval_seconds: int = 10
```

None of the fields in the YAML (`gpu_monitor_command`, `monitor_interval`) exist in this model.

### Impact Chain

1. `main.py:start_gpu_monitor()` calls `gpu_config.get('gpu_monitor_command')` → returns `None`.
2. `GpuMonitor.__init__` receives `command=None`.
3. When `_fetch_and_update_gpus()` runs `self.execution_handler.execute_command(command=self.command)`, it fails because `command` is `None`.
4. Additionally, `gpu_config.get('monitor_interval', 60)` → returns `60` instead of the configured `10`, degrading polling frequency.

### Error Manifestation

The exact error depends on `ExecutionHandler.execute_command()` implementation, but the command being `None` will cause a failure at the command execution layer — not at output parsing as originally reported. The root cause (field dropped by Pydantic) is the same regardless.

---

## Full Impact Summary

| Config Section | YAML Fields | Pydantic Fields | Fields Dropped | Impact |
|---|---|---|---|---|
| `ui` | `auto_start`, `port`, `web.*`, `refresh_interval_seconds`?, `max_cases_display`? | `refresh_interval_seconds`, `max_cases_display` | 8+ fields | Dashboard never starts; web mode never activates |
| `gpu` | `gpu_monitor_command`, `monitor_interval` | `enabled`, `memory_threshold_mb`, `utilization_threshold_percent`, `polling_interval_seconds` | 2 fields | GPU monitor crashes; polling interval defaults to 60s |

---

## Recommended Fixes

### Option A: Add Missing Fields to Pydantic Models (Recommended)

Update `UIConfig` and `GpuConfig` to include all fields used by the application:

```python
class WebConfig(BaseModel):
    enabled: bool = False
    port: int = Field(default=8080, ge=1, le=65535)
    ttyd_path: str = "ttyd"
    bind_address: str = "0.0.0.0"
    permit_write: bool = False
    reconnect: bool = True

class UIConfig(BaseModel):
    auto_start: bool = False
    port: int = Field(default=8501, ge=1, le=65535)
    web: WebConfig = Field(default_factory=WebConfig)
    refresh_interval_seconds: int = Field(default=1, ge=1, le=60)
    max_cases_display: int = Field(default=50, ge=1, le=1000)

class GpuConfig(BaseModel):
    enabled: bool = True
    gpu_monitor_command: Optional[str] = None
    monitor_interval: int = Field(default=60, ge=1, le=300)
    memory_threshold_mb: int = Field(default=1000, ge=0, le=100000)
    utilization_threshold_percent: int = Field(default=80, ge=0, le=100)
    polling_interval_seconds: int = Field(default=10, ge=1, le=300)
```

### Option B: Set `extra = "allow"` on All Nested Models

Add `model_config = {"extra": "allow"}` to `UIConfig`, `GpuConfig`, and every other nested model. This preserves unknown fields but loses type validation on them.

### Option C: Hybrid — YAML Fallback for Non-Validated Fields

Modify `get_ui_config()` and `get_gpu_config()` to merge validated fields back into the raw YAML dict:

```python
def get_ui_config(self) -> Dict[str, Any]:
    if self._validated_config:
        validated = self._validated_config.ui.model_dump()
        raw = self._yaml_config.get("ui", {})
        return {**raw, **validated}  # validated overrides raw for known fields
    return self._yaml_config.get("ui", {})
```

This preserves all YAML fields while still validating known ones. However, it undermines the purpose of Pydantic validation.

### Additional Fix: Field Name Mismatch in display.py

```python
# display.py:51 — Change this:
self._refresh_rate = ui_config.get("refresh_interval", 2)
# To this:
self._refresh_rate = ui_config.get("refresh_interval_seconds", 2)
```

---

## Validation Checklist

After applying fixes, verify:

- [ ] `settings.get_ui_config()` returns `auto_start: true` when configured
- [ ] `settings.get_ui_config()["web"]["enabled"]` returns the configured value
- [ ] `settings.get_gpu_config()["gpu_monitor_command"]` returns the nvidia-smi command string
- [ ] `settings.get_gpu_config()["monitor_interval"]` returns `10`, not `60`
- [ ] `display.py` reads `refresh_interval_seconds` (not `refresh_interval`)
- [ ] Dashboard starts automatically on app launch
- [ ] Web mode (ttyd) activates when `ui.web.enabled: true`
- [ ] GPU monitor successfully executes nvidia-smi command
