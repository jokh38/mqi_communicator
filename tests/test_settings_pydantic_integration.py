"""Integration tests for Settings class with Pydantic validation.

These tests verify that the Settings class correctly integrates Pydantic
validation while maintaining backward compatibility with existing APIs.
"""

import pytest
import tempfile
import yaml
from pathlib import Path
from pydantic import ValidationError

from src.config.settings import Settings


class TestSettingsPydanticIntegration:
    """Test Settings class integrates Pydantic validation correctly"""

    @pytest.fixture
    def valid_config_file(self, tmp_path):
        """Create a valid config file for testing"""
        config = {
            "database": {
                "connection_timeout_seconds": 30,
                "journal_mode": "WAL",
                "synchronous": "NORMAL",
                "cache_size": -2000
            },
            "processing": {
                "max_retries": 3,
                "retry_delay_seconds": 5,
                "max_workers": 4
            },
            "progress_tracking": {
                "polling_interval_seconds": 5,
                "coarse_phase_progress": {
                    "CSV_INTERPRETING": 10.0,
                    "SIMULATION_RUNNING": 40.0,
                    "COMPLETED": 100.0
                }
            },
            "logging": {
                "level": "INFO",
                "log_dir": "{base_directory}/logs",
                "max_file_size_mb": 10,
                "backup_count": 5
            },
            "gpu": {
                "gpu_monitor_command": "nvidia-smi",
                "monitor_interval": 10,
                "assignment_grace_period_seconds": 60,
            },
            "moqui_runtime": {
                "multigpu_enabled": True,
                "beam_uses_all_available_gpus": True,
                "max_gpus_per_beam": 4
            },
            "ptn_checker": {
                "path": "/tmp/ptn_checker",
                "output_subdir": "ptn_checker_output",
                "stability_window_seconds": 30,
                "min_file_age_seconds": 5,
                "size_poll_interval_seconds": 1
            },
            "ui": {
                "mode": "web",
                "auto_start": True,
                "refresh_interval_seconds": 1,
                "max_cases_display": 50,
                "web": {"port": 8080, "host": "0.0.0.0"},
            },
            "retry_policy": {
                "max_attempts": 4,
                "initial_delay_seconds": 2,
                "max_delay_seconds": 30,
                "backoff_factor": 1.5,
            },
            "paths": {
                "base_directory": "/tmp/test"
            },
            "tps_generator": {
                "validation": {
                    "required_params": ["GPUID", "OutputDir"],
                },
                "default_paths": {
                    "interpreter_outputs_dir": "{paths.local.csv_output_dir}",
                    "outputs_dir": "{paths.local.simulation_output_dir}",
                },
            },
            "ExecutionHandler": {
                "CsvInterpreter": "local"
            }
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)
        return config_path

    @pytest.fixture
    def invalid_config_file(self, tmp_path):
        """Create an invalid config file for testing"""
        config = {
            "database": {
                "connection_timeout_seconds": 500,  # > 300 (invalid)
                "journal_mode": "WAL"
            },
            "paths": {
                "base_directory": "/tmp/test"
            },
            "ExecutionHandler": {
                "CsvInterpreter": "local"
            }
        }
        config_path = tmp_path / "invalid_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f)
        return config_path

    def test_settings_loads_valid_config(self, valid_config_file):
        """Test Settings successfully loads and validates valid config"""
        settings = Settings(config_path=valid_config_file)

        # Verify validated config is accessible
        assert hasattr(settings, '_validated_config')
        assert settings._validated_config is not None

    def test_settings_detects_invalid_config(self, invalid_config_file):
        """Test Settings detects invalid configuration at load time"""
        with pytest.raises(ValidationError) as exc_info:
            Settings(config_path=invalid_config_file)

        assert "connection_timeout_seconds" in str(exc_info.value)

    def test_settings_backward_compatible_get_database_config(self, valid_config_file):
        """Test get_database_config() returns dict for backward compatibility"""
        settings = Settings(config_path=valid_config_file)

        db_config = settings.get_database_config()

        # Should return a dict (backward compatible)
        assert isinstance(db_config, dict)
        assert db_config["connection_timeout_seconds"] == 30
        assert db_config["journal_mode"] == "WAL"

    def test_settings_backward_compatible_get_processing_config(self, valid_config_file):
        """Test get_processing_config() returns dict for backward compatibility"""
        settings = Settings(config_path=valid_config_file)

        proc_config = settings.get_processing_config()

        assert isinstance(proc_config, dict)
        assert proc_config["max_retries"] == 3
        assert proc_config["max_workers"] == 4

    def test_settings_preserves_yaml_alias_keys_for_runtime_callers(self, tmp_path):
        """Validated config should expose operator YAML keys consumed by runtime code."""
        config = {
            "database": {
                "synchronous_mode": "OFF",
                "cache_size_mb": 128,
            },
            "processing": {
                "max_case_retries": 7,
            },
            "logging": {
                "log_level": "ERROR",
            },
            "paths": {
                "base_directory": "/tmp/test",
            },
            "ExecutionHandler": {
                "CsvInterpreter": "local",
            },
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        settings = Settings(config_path=config_path)

        db_config = settings.get_database_config()
        processing_config = settings.get_processing_config()
        logging_config = settings.get_logging_config()

        assert db_config["synchronous_mode"] == "OFF"
        assert db_config["cache_size_mb"] == 128
        assert processing_config["max_case_retries"] == 7
        assert logging_config["log_level"] == "ERROR"

    def test_settings_backward_compatible_get_moqui_runtime_config(self, valid_config_file):
        settings = Settings(config_path=valid_config_file)

        runtime_config = settings.get_moqui_runtime_config()

        assert isinstance(runtime_config, dict)
        assert runtime_config["multigpu_enabled"] is True
        assert runtime_config["beam_uses_all_available_gpus"] is True
        assert runtime_config["max_gpus_per_beam"] == 4

    def test_settings_backward_compatible_get_ptn_checker_config(self, valid_config_file):
        settings = Settings(config_path=valid_config_file)

        ptn_config = settings.get_ptn_checker_config()

        assert isinstance(ptn_config, dict)
        assert ptn_config["path"] == "/tmp/ptn_checker"
        assert ptn_config["output_subdir"] == "ptn_checker_output"

    def test_settings_backward_compatible_get_progress_tracking_config(self, valid_config_file):
        """Test get_progress_tracking_config() returns dict for backward compatibility"""
        settings = Settings(config_path=valid_config_file)

        progress_config = settings.get_progress_tracking_config()

        assert isinstance(progress_config, dict)
        assert progress_config["polling_interval_seconds"] == 5
        assert "CSV_INTERPRETING" in progress_config["coarse_phase_progress"]

    def test_settings_backward_compatible_get_retry_policy_config(self, valid_config_file):
        """Test get_retry_policy_config() returns validated retry policy values."""
        settings = Settings(config_path=valid_config_file)

        retry_config = settings.get_retry_policy_config()

        assert retry_config == {
            "max_attempts": 4,
            "initial_delay_seconds": 2,
            "max_delay_seconds": 30,
            "backoff_factor": 1.5,
        }

    def test_settings_public_tps_generator_config_preserves_validation_section(
        self, valid_config_file
    ):
        """TPS generator callers should not need direct _yaml_config access."""
        settings = Settings(config_path=valid_config_file)

        tps_config = settings.get_tps_generator_config()

        assert tps_config["validation"]["required_params"] == ["GPUID", "OutputDir"]
        assert "base_directory" not in tps_config["default_paths"]

    def test_settings_uses_pydantic_defaults_for_missing_sections(self, tmp_path):
        """Test Settings uses Pydantic defaults when config sections are missing"""
        minimal_config = {
            "paths": {
                "base_directory": "/tmp/test"
            },
            "ExecutionHandler": {
                "CsvInterpreter": "local"
            }
        }
        config_path = tmp_path / "minimal.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(minimal_config, f)

        settings = Settings(config_path=config_path)

        # Should use Pydantic defaults
        db_config = settings.get_database_config()
        assert db_config["connection_timeout_seconds"] == 30  # Pydantic default
        assert db_config["journal_mode"] == "WAL"  # Pydantic default

    def test_settings_provides_pydantic_config_access(self, valid_config_file):
        """Test Settings provides access to typed Pydantic config"""
        settings = Settings(config_path=valid_config_file)

        # Should have typed access
        assert hasattr(settings, 'get_validated_config')
        validated = settings.get_validated_config()

        # Should be AppConfig instance
        from src.config.pydantic_models import AppConfig
        assert isinstance(validated, AppConfig)
        assert validated.database.connection_timeout_seconds == 30

    def test_settings_validation_preserves_extra_fields(self, tmp_path):
        """Test Settings preserves extra fields not in Pydantic schema"""
        config_with_extras = {
            "database": {
                "connection_timeout_seconds": 30,
                "custom_field": "custom_value"  # Not in Pydantic model
            },
            "custom_section": {
                "some_value": 123
            },
            "paths": {
                "base_directory": "/tmp/test"
            },
            "ExecutionHandler": {
                "CsvInterpreter": "local"
            }
        }
        config_path = tmp_path / "config_extras.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config_with_extras, f)

        settings = Settings(config_path=config_path)

        # Extra fields should still be accessible via _yaml_config
        assert "custom_section" in settings._yaml_config
        assert settings._yaml_config["custom_section"]["some_value"] == 123


class TestSettingsValidationWarnings:
    """Test Settings provides helpful validation warnings"""

    def test_settings_warns_on_invalid_config_with_fallback(self, tmp_path, caplog):
        """Test Settings can provide warnings for validation errors"""
        # This test verifies that Settings can optionally warn instead of fail
        # (if we add a validation_mode option in the future)
        pass  # Future enhancement
