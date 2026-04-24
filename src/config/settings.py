"""
Manages application configuration settings by loading them from a central YAML file.

This module provides a `Settings` class that acts as an intelligent interpreter
for the `config.yaml` file. It dynamically constructs paths and commands based
on the execution modes defined in the configuration.

Phase 4 Enhancement: Adds Pydantic-based configuration validation for type safety
and early error detection.
"""

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
import logging
from pydantic import ValidationError

from src.config.pydantic_models import AppConfig


def _detect_project_root() -> str:
    """Resolve the MOQUI_SMC project root.

    Preference order: MOQUI_SMC_ROOT env var, then the directory three
    levels above this file (mqi_communicator/src/config -> repo root).
    """
    env_root = os.environ.get("MOQUI_SMC_ROOT")
    if env_root:
        return str(Path(env_root).expanduser().resolve())
    return str(Path(__file__).resolve().parents[3])


class Settings:
    """
    Main configuration class that loads and interprets all settings from a YAML file.
    """

    def __init__(self, config_path: Optional[Path] = None, logger: Optional[Any] = None):
        """
        Initializes settings by loading the specified configuration file.

        Phase 4 Enhancement: Now validates configuration with Pydantic models.

        Args:
            config_path: Path to the YAML configuration file
            logger: Optional logger for warnings and errors

        Raises:
            ValidationError: If configuration validation fails
        """
        config_path = self._resolve_config_path(config_path)

        self._logger = logger  # Optional injected logger; avoid importing StructuredLogger to prevent cycles
        self._yaml_config: Dict[str, Any] = {}
        self._validated_config: Optional[AppConfig] = None  # Phase 4: Pydantic validated config
        self.execution_handler: Dict[str, str] = {}

        if config_path.exists():
            self._load_from_file(config_path)
            self._validate_config()  # Phase 4: Validate with Pydantic
        else:
            self._emit_warning(f"Configuration file not found at {config_path}")
            # Use default Pydantic config
            self._validated_config = AppConfig()

        # All settings should be accessed via get_*_config() methods.
        # The following attributes are removed for consistency and to avoid direct access.
        # self.database = self.get_database_config()
        # self.processing = self.get_processing_config()
        # self.ui = self.get_ui_config()
        # self.gpu = self.get_gpu_config()
        # self.logging = self.get_logging_config()
        # self.retry_policy = self.get_retry_policy_config()

    @staticmethod
    def _resolve_config_path(config_path: Optional[Path]) -> Path:
        """Resolve config paths relative to the communicator package when needed."""
        package_root = Path(__file__).resolve().parents[2]
        default_config = package_root / "config" / "config.yaml"

        if config_path is None:
            return default_config

        candidate = Path(config_path)
        if candidate.exists():
            return candidate

        if candidate.is_absolute():
            return candidate

        package_relative = package_root / candidate
        if package_relative.exists():
            return package_relative

        return candidate

    def _emit_warning(self, message: str, context: Optional[Dict[str, Any]] = None) -> None:
        try:
            if self._logger is not None:
                # StructuredLogger-compatible call if available
                if hasattr(self._logger, 'warning'):
                    self._logger.warning(message, context or None)
                    return
            # Settings is loaded before LoggerFactory is configured in several entry points,
            # so the bootstrap fallback intentionally uses stdlib logging.
            logging.basicConfig(level=logging.INFO, force=False)
            logging.getLogger(__name__).warning(message)
        except Exception:
            sys.stderr.write(f"Warning: {message}\n")

    def _resolve_base_directory(self) -> None:
        """Populate paths.base_directory from env / script location if unset,
        then expand {base_directory} in config values that are read without
        going through get_path / get_executable template resolution.
        """
        paths = self._yaml_config.setdefault("paths", {})
        configured = paths.get("base_directory")
        if not configured or not str(configured).strip():
            paths["base_directory"] = _detect_project_root()
        base_dir = paths["base_directory"]

        ptn = self._yaml_config.get("ptn_checker")
        if isinstance(ptn, dict):
            path_val = ptn.get("path")
            if isinstance(path_val, str) and "{base_directory}" in path_val:
                ptn["path"] = path_val.format(base_directory=base_dir)

    def _load_from_file(self, config_path: Path) -> None:
        """
        Loads the entire configuration from a YAML file into a dictionary.
        """
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self._yaml_config = yaml.safe_load(f) or {}
            self.execution_handler = self._yaml_config.get("ExecutionHandler", {})
            self._resolve_base_directory()
        except Exception as e:
            self._emit_warning(
                f"Could not load or parse config file {config_path}: {e}",
                {"config_path": str(config_path), "error": str(e)}
            )
            self._yaml_config = {}

    def _validate_config(self) -> None:
        """
        Validates the loaded configuration using Pydantic models.

        Phase 4: This method validates the configuration and raises ValidationError
        if the config is invalid.

        Raises:
            ValidationError: If configuration doesn't meet Pydantic schema requirements
        """
        try:
            # Extract sections that Pydantic models understand
            config_dict = {
                "database": self._yaml_config.get("database", {}),
                "processing": self._yaml_config.get("processing", {}),
                "progress_tracking": self._yaml_config.get("progress_tracking", {}),
                "logging": self._yaml_config.get("logging", {}),
                "gpu": self._yaml_config.get("gpu", {}),
                "moqui_runtime": self._yaml_config.get("moqui_runtime", {}),
                "ptn_checker": self._yaml_config.get("ptn_checker", {}),
                "ui": self._yaml_config.get("ui", {}),
                "retry_policy": self._yaml_config.get("retry_policy", {}),
            }

            # Validate with Pydantic
            self._validated_config = AppConfig(**config_dict)

            self._emit_info(
                "Configuration validated successfully",
                {"config_sections": list(config_dict.keys())}
            )

        except ValidationError as e:
            self._emit_error(
                "Configuration validation failed",
                {"validation_errors": str(e)}
            )
            raise

    def _emit_info(self, message: str, context: Optional[Dict[str, Any]] = None) -> None:
        """Emit info-level log message"""
        try:
            if self._logger is not None:
                if hasattr(self._logger, 'info'):
                    self._logger.info(message, context or None)
                    return
            logging.basicConfig(level=logging.INFO, force=False)
            logging.getLogger(__name__).info(message)
        except Exception:
            pass  # Silent fallback

    def _emit_error(self, message: str, context: Optional[Dict[str, Any]] = None) -> None:
        """Emit error-level log message."""
        try:
            if self._logger is not None:
                if hasattr(self._logger, 'error'):
                    self._logger.error(message, context or None)
                    return
            logging.basicConfig(level=logging.INFO, force=False)
            logging.getLogger(__name__).error(message)
        except Exception:
            sys.stderr.write(f"Error: {message}\n")


    def get_handler_mode(self, handler_name: str) -> str:
        """
        Determines the execution mode for a given handler.
        """
        mode = self.execution_handler.get(handler_name)
        if not mode:
            raise KeyError(
                f"Handler '{handler_name}' not found in ExecutionHandler config."
            )
        return mode

    def get_path(self, path_name: str, handler_name: str, **kwargs: Any) -> str:
        """
        Gets a fully resolved and formatted path for a given handler and context.
        """
        mode = self.get_handler_mode(handler_name)
        paths_config = self._yaml_config.get("paths", {})

        path_template = paths_config.get(mode, {}).get(path_name)
        if path_template is None:
            path_template = paths_config.get(path_name)

        if path_template is None:
            raise KeyError(f"Path '{path_name}' not found for mode '{mode}' or in base paths.")

        # Recursively resolve placeholders up to configurable depth
        max_depth = (
            self._yaml_config.get("settings", {}).get("path_resolution_max_depth")
            or 8
        )
        current = str(path_template)
        for _ in range(max_depth):
            placeholders = re.findall(r"\{(\w+)\}", current)
            if not placeholders:
                break

            made_a_change = False
            for p_name in placeholders:
                if p_name in kwargs:
                    continue
                try:
                    resolved_path = self.get_path(p_name, handler_name, **kwargs)
                    current = current.replace(f"{{{p_name}}}", resolved_path)
                    made_a_change = True
                except KeyError:
                    if p_name == 'base_directory':
                        base_dir = self._yaml_config.get("paths", {}).get("base_directory", "")
                        current = current.replace(f"{{{p_name}}}", base_dir)
                        made_a_change = True
                    elif p_name in {"room", "room_path"}:
                        current = current.replace(f"{{{p_name}}}", "")
                        made_a_change = True
            if not made_a_change:
                break

        # After resolution attempts, check for unresolved placeholders that are not in kwargs
        unresolved = re.findall(r"\{(\w+)\}", current)
        unresolved_not_in_kwargs = [p for p in unresolved if p not in kwargs]
        if unresolved_not_in_kwargs:
            raise ValueError(
                f"Unresolved placeholders {unresolved_not_in_kwargs} in path '{path_name}' for mode '{mode}'."
            )

        return current.format(**kwargs)


    def get_executable(self, exec_name: str, handler_name: str, **kwargs: Any) -> str:
        """
        Gets a fully resolved path to an executable for a given handler.
        """
        mode = self.get_handler_mode(handler_name)
        exec_config = self._yaml_config.get("executables", {})

        exec_template = exec_config.get(mode, {}).get(exec_name)
        if exec_template is None:
             raise KeyError(f"Executable '{exec_name}' not found for mode '{mode}'.")

        format_context = kwargs.copy()
        format_context.setdefault("base_directory", self._yaml_config.get("paths", {}).get("base_directory", ""))

        return exec_template.format(**format_context)

    def get_command(self, command_key: str, handler_name: str, **kwargs: Any) -> str:
        """
        Constructs a fully resolved and executable command string.
        """
        mode = self.get_handler_mode(handler_name)
        templates = self._yaml_config.get("command_templates", {}).get(mode, {})

        command_template = templates.get(command_key)
        if command_template is None:
            raise KeyError(f"Command template '{command_key}' not found for mode '{mode}'.")

        placeholders = re.findall(r"\{(\w+)\}", command_template)
        format_context = kwargs.copy()

        for p in placeholders:
            if p in format_context:
                continue
            try:
                format_context[p] = self.get_path(p, handler_name, **kwargs)
                continue
            except KeyError:
                format_context.pop(p, None)
            try:
                format_context[p] = self.get_executable(p, handler_name, **kwargs)
                continue
            except KeyError:
                format_context.pop(p, None)

            if p not in format_context:
                 raise ValueError(f"Could not resolve placeholder '{{{p}}}' for command '{command_key}'.")

        return command_template.format(**format_context)

    def get_database_config(self) -> Dict[str, Any]:
        """
        Gets the database configuration dictionary from the YAML file.

        Phase 4: Now returns validated configuration from Pydantic model.

        Returns:
            Dict[str, Any]: Database configuration (backward compatible dict format)
        """
        if self._validated_config:
            # Return validated config as dict for backward compatibility
            database_config = self._validated_config.database.model_dump()
            database_config["synchronous_mode"] = database_config["synchronous"]
            cache_size = database_config["cache_size"]
            if cache_size >= 0:
                database_config["cache_size_mb"] = cache_size
            return database_config
        return self._yaml_config.get("database", {})

    def get_logging_config(self) -> Dict[str, Any]:
        """
        Gets the logging configuration dictionary from the YAML file.

        Phase 4: Now returns validated configuration from Pydantic model.

        Returns:
            Dict[str, Any]: Logging configuration (backward compatible dict format)
        """
        if self._validated_config:
            logging_config = self._validated_config.logging.model_dump()
            logging_config["log_level"] = logging_config["level"]
        else:
            logging_config = self._yaml_config.get("logging", {}).copy()

        # Resolve log_dir path template
        if "log_dir" in logging_config:
            base_dir = self._yaml_config.get("paths", {}).get("base_directory", "")
            logging_config["log_dir"] = logging_config["log_dir"].format(base_directory=base_dir)
        return logging_config

    def get_processing_config(self) -> Dict[str, Any]:
        """
        Gets the processing configuration dictionary from the YAML file.

        Phase 4: Now returns validated configuration from Pydantic model.

        Returns:
            Dict[str, Any]: Processing configuration (backward compatible dict format)
        """
        if self._validated_config:
            processing_config = self._validated_config.processing.model_dump()
            processing_config["max_case_retries"] = processing_config["max_retries"]
            return processing_config
        return self._yaml_config.get("processing", {})

    def get_ui_config(self) -> Dict[str, Any]:
        """
        Gets the ui configuration dictionary from the YAML file.

        Phase 4: Now returns validated configuration from Pydantic model.

        Returns:
            Dict[str, Any]: UI configuration (backward compatible dict format)
        """
        if self._validated_config:
            return self._validated_config.ui.model_dump()
        return self._yaml_config.get("ui", {})

    def get_gpu_config(self) -> Dict[str, Any]:
        """
        Gets the gpu configuration dictionary from the YAML file.

        Phase 4: Now returns validated configuration from Pydantic model.

        Returns:
            Dict[str, Any]: GPU configuration (backward compatible dict format)
        """
        if self._validated_config:
            return self._validated_config.gpu.model_dump()
        return self._yaml_config.get("gpu", {})

    def get_moqui_runtime_config(self) -> Dict[str, Any]:
        """Return moqui runtime scheduling configuration."""
        if self._validated_config:
            return self._validated_config.moqui_runtime.model_dump()
        return self._yaml_config.get("moqui_runtime", {})

    def get_ptn_checker_config(self) -> Dict[str, Any]:
        """Return PTN checker integration configuration."""
        if self._validated_config:
            return self._validated_config.ptn_checker.model_dump()
        return self._yaml_config.get("ptn_checker", {})

    def get_validated_config(self) -> AppConfig:
        """Returns the typed validated Pydantic configuration."""
        if self._validated_config is None:
            self._validated_config = AppConfig()
        return self._validated_config

    def get_default_handler(self) -> str:
        """Returns the default handler name from config or a safe fallback."""
        # Support either nested "settings.default_handler" or top-level "default_handler"
        return (
            self._yaml_config.get("settings", {}).get("default_handler")
            or self._yaml_config.get("default_handler")
            or "CsvInterpreter"
        )

    def get_database_path(self, handler_name: Optional[str] = None) -> Path:
        """
        Returns the database path as a Path object.
        """
        handler = handler_name or self.get_default_handler()
        db_path = self.get_path("database_path", handler_name=handler)
        return Path(db_path)

    def get_case_directories(self, handler_name: Optional[str] = None) -> Dict[str, Path]:
        """
        Returns the case directory paths.
        """
        try:
            handler = handler_name or self.get_default_handler()
            scan_path = self.get_path("scan_directory", handler_name=handler)
            return {"scan": Path(scan_path)}
        except (KeyError, ValueError) as e:
            raise RuntimeError(f"Could not resolve scan directory path: {e}") from e


    def get_moqui_tps_parameters(self) -> Dict[str, Any]:
        """
        Gets the moqui_tps_parameters configuration dictionary from the YAML file.
        """
        return self._yaml_config.get("moqui_tps_parameters", {})

    def get_tps_generator_config(self) -> Dict[str, Any]:
        """
        Gets the tps_generator configuration dictionary from the YAML file.

        Returns:
            Dict[str, Any]: The raw tps_generator configuration including
                           default_paths and validation settings.
        """
        return self._yaml_config.get("tps_generator", {})

    def get_retry_policy_config(self) -> Dict[str, Any]:
        """Return validated retry policy configuration."""
        if self._validated_config:
            return self._validated_config.retry_policy.model_dump()
        return self._yaml_config.get("retry_policy", {})

    def get_progress_tracking_config(self) -> Dict[str, Any]:
        """Returns progress tracking config with safe defaults.

        Phase 4: Now returns validated configuration from Pydantic model.

        Returns:
            Dict[str, Any]: Progress tracking configuration with defaults

        Keys: polling_interval_seconds, coarse_phase_progress
        """
        if self._validated_config:
            return self._validated_config.progress_tracking.model_dump()

        # Fallback to old behavior if not validated
        cfg = self._yaml_config.get("progress_tracking", {}) or {}
        # Safe defaults
        coarse_defaults = {
            "CSV_INTERPRETING": 10.0,
            "HPC_RUNNING": 40.0,
            "POSTPROCESSING": 80.0,
            "COMPLETED": 100.0,
        }
        cfg.setdefault("polling_interval_seconds", 5)
        cfg.setdefault("coarse_phase_progress", coarse_defaults)
        return cfg

    def get_completion_patterns(self) -> Dict[str, Any]:
        """
        Gets the completion markers configuration for detecting simulation completion.

        Returns:
            Dict[str, Any]: Contains 'success_pattern' (str) and 'failure_patterns' (list)
        """
        return self._yaml_config.get("completion_markers", {
            "success_pattern": "Simulation completed successfully",
            "failure_patterns": ["FATAL ERROR", "ERROR:", "Segmentation fault"]
        })
