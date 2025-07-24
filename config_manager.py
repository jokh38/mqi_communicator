import json
from pathlib import Path
from typing import Dict, List, Any


class ConfigManager:
    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from JSON file."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(f"Invalid JSON in config file: {e}", "", 0)

    def get_windows_pc_ip(self) -> str:
        """Get Windows PC server IP address."""
        return self.config["servers"]["windows_pc"]

    def get_linux_gpu_ip(self) -> str:
        """Get Linux GPU server IP address."""
        return self.config["servers"]["linux_gpu"]

    def get_credentials(self) -> Dict[str, str]:
        """Get server credentials."""
        return {
            "username": self.config["credentials"]["username"],
            "password": self.config["credentials"]["password"]
        }

    def get_local_logdata_path(self) -> str:
        """Get local log data directory path."""
        return self.config["paths"]["local_logdata"]

    def get_remote_workspace_path(self) -> str:
        """Get remote workspace directory path."""
        return self.config["paths"]["remote_workspace"]

    def get_local_output_path(self) -> str:
        """Get local output directory path."""
        return self.config["paths"]["local_output"]

    def get_scanning_interval(self) -> int:
        """Get scanning interval in minutes."""
        return self.config["scanning"]["interval_minutes"]

    def get_max_concurrent_cases(self) -> int:
        """Get maximum concurrent cases."""
        return self.config["scanning"]["max_concurrent_cases"]

    def get_total_gpus(self) -> int:
        """Get total number of GPUs."""
        return self.config["gpu_management"]["total_gpus"]

    def get_reserved_gpus(self) -> List[int]:
        """Get list of reserved GPU IDs."""
        return self.config["gpu_management"]["reserved_gpus"]

    def get_available_gpus(self) -> List[int]:
        """Get list of available GPU IDs (excluding reserved)."""
        total_gpus = self.get_total_gpus()
        reserved_gpus = self.get_reserved_gpus()
        return [gpu_id for gpu_id in range(total_gpus) if gpu_id not in reserved_gpus]

    def get_memory_threshold(self) -> int:
        """Get GPU memory threshold in MB."""
        return self.config["gpu_management"]["memory_threshold_mb"]

    def get_monitoring_interval(self) -> int:
        """Get GPU monitoring interval in seconds."""
        return self.config["gpu_management"]["monitoring_interval_sec"]

    def reload_config(self) -> None:
        """Reload configuration from file."""
        self.config = self._load_config()

    def update_config(self, key_path: str, value: Any) -> None:
        """Update configuration value using dot notation (e.g., 'servers.windows_pc')."""
        keys = key_path.split('.')
        current = self.config
        
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        current[keys[-1]] = value
        
        # Save updated config
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2)

    def get_config(self) -> Dict[str, Any]:
        """Get complete configuration dictionary."""
        return self.config.copy()

    def get_moqui_tps_params(self) -> Dict[str, Any]:
        """Get moqui_tps parameters from configuration and log generation success."""
        try:
            tps_params = self.config.get("moqui_tps_params", {})
            if tps_params:
                # Log successful parameter retrieval as specified in monitoring plan
                print(f"Successfully retrieved moqui_tps.in parameters from configuration: {len(tps_params)} parameters")
            return tps_params
        except Exception as e:
            print(f"Failed to retrieve moqui_tps.in parameters from configuration: {e}")
            return {}