"""
Manages application configuration settings by loading them from a central YAML file.

This module provides a `Settings` class that acts as an intelligent interpreter
for the `config.yaml` file. It dynamically constructs paths and commands based
on the execution modes defined in the configuration.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class Settings:
    """
    Main configuration class that loads and interprets all settings from a YAML file.
    """

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initializes settings by loading the specified configuration file.
        """
        if config_path is None:
            config_path = Path("config/config.yaml")

        self._yaml_config: Dict[str, Any] = {}
        self.execution_handler: Dict[str, str] = {}
        if config_path.exists():
            self._load_from_file(config_path)
        else:
            print(f"Warning: Configuration file not found at {config_path}")

        # All settings should be accessed via get_*_config() methods.
        # The following attributes are removed for consistency and to avoid direct access.
        # self.database = self.get_database_config()
        # self.processing = self.get_processing_config()
        # self.ui = self.get_ui_config()
        # self.gpu = self.get_gpu_config()
        # self.logging = self.get_logging_config()
        # self.retry_policy = self.get_retry_policy_config()

    def _load_from_file(self, config_path: Path) -> None:
        """
        Loads the entire configuration from a YAML file into a dictionary.
        """
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self._yaml_config = yaml.safe_load(f)
            self.execution_handler = self._yaml_config.get("ExecutionHandler", {})
        except Exception as e:
            print(f"Warning: Could not load or parse config file {config_path}: {e}")
            self._yaml_config = {}

    def get_handler_mode(self, handler_name: str) -> str:
        """
        Determines the execution mode ('local' or 'remote') for a given handler.
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

        # Recursively resolve placeholders
        for _ in range(5):  # Limit recursion to prevent infinite loops
            placeholders = re.findall(r"\{(\w+)\}", path_template)
            if not placeholders:
                break

            made_a_change = False
            for p_name in placeholders:
                if p_name in kwargs:
                    continue
                try:
                    resolved_path = self.get_path(p_name, handler_name, **kwargs)
                    path_template = path_template.replace(f"{{{p_name}}}", resolved_path)
                    made_a_change = True
                except KeyError:
                    if p_name == 'base_directory':
                        base_dir = self._yaml_config.get("paths", {}).get("base_directory", "")
                        path_template = path_template.replace(f"{{{p_name}}}", base_dir)
                        made_a_change = True

            if not made_a_change:
                break

        return path_template.format(**kwargs)

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
                pass
            try:
                format_context[p] = self.get_executable(p, handler_name, **kwargs)
                continue
            except KeyError:
                pass

            # Resolve connection details
            connections = self.get_connection_config()
            if p == "pc_ip" and "pc_localdata" in connections:
                format_context["pc_ip"] = connections["pc_localdata"].get("ip")
                continue
            if p == "pc_user" and "pc_localdata" in connections:
                format_context["pc_user"] = connections["pc_localdata"].get("user")
                continue

            if p not in format_context:
                 raise ValueError(f"Could not resolve placeholder '{{{p}}}' for command '{command_key}'.")

        return command_template.format(**format_context)

    def get_database_config(self) -> Dict[str, Any]:
        """
        Gets the database configuration dictionary from the YAML file.
        """
        return self._yaml_config.get("database", {})

    def get_logging_config(self) -> Dict[str, Any]:
        """
        Gets the logging configuration dictionary from the YAML file.
        """
        logging_config = self._yaml_config.get("logging", {}).copy()
        if "log_dir" in logging_config:
            base_dir = self._yaml_config.get("paths", {}).get("base_directory", "")
            logging_config["log_dir"] = logging_config["log_dir"].format(base_directory=base_dir)
        return logging_config

    def get_processing_config(self) -> Dict[str, Any]:
        """
        Gets the processing configuration dictionary from the YAML file.
        """
        return self._yaml_config.get("processing", {})

    def get_retry_policy_config(self) -> Dict[str, Any]:
        """
        Gets the retry policy configuration dictionary from the YAML file.
        """
        return self._yaml_config.get("retry_policy", {})

    def get_ui_config(self) -> Dict[str, Any]:
        """
        Gets the ui configuration dictionary from the YAML file.
        """
        return self._yaml_config.get("ui", {})

    def get_gpu_config(self) -> Dict[str, Any]:
        """
        Gets the gpu configuration dictionary from the YAML file.
        """
        return self._yaml_config.get("gpu", {})

    def get_connection_config(self) -> Dict[str, Any]:
        """
        Gets the connections configuration dictionary from the YAML file.
        """
        return self._yaml_config.get("connections", {})

    def get_database_path(self) -> Path:
        """
        Returns the database path as a Path object.
        """
        db_path = self.get_path("database_path", handler_name="CsvInterpreter")
        return Path(db_path)

    def get_case_directories(self) -> Dict[str, Path]:
        """
        Returns the case directory paths.
        """
        try:
            scan_path = self.get_path("scan_directory", handler_name="CsvInterpreter")
            return {"scan": Path(scan_path)}
        except (KeyError, ValueError) as e:
            raise RuntimeError(f"Could not resolve scan directory path: {e}") from e

    def get_hpc_connection(self) -> Optional[Dict[str, Any]]:
        """
        Returns HPC connection information.
        """
        return self._yaml_config.get("hpc", None)

    def get_moqui_tps_parameters(self) -> Dict[str, Any]:
        """
        Gets the moqui_tps_parameters configuration dictionary from the YAML file.
        """
        return self._yaml_config.get("moqui_tps_parameters", {})

    def get_hpc_paths(self) -> Dict[str, Any]:
        """
        Gets the HPC paths configuration dictionary from the YAML file.
        """
        hpc_config = self.get_hpc_connection()
        if hpc_config:
            return hpc_config.get("paths", {})
        return {}

    def get_tps_generator_config(self) -> Dict[str, Any]:
        """
        Gets the tps_generator configuration dictionary from the YAML file.

        Returns:
            Dict[str, Any]: The raw tps_generator configuration including
                           default_paths and validation settings.
        """
        return self._yaml_config.get("tps_generator", {})