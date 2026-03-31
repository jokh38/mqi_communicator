"""Tests for Pydantic-based configuration models.

This test module follows TDD principles (Red-Green-Refactor) for Phase 4:
Pydantic Configuration Validation.
"""

import pytest
from pydantic import ValidationError
from pathlib import Path

# Will be implemented in src/config/pydantic_models.py
from src.config.pydantic_models import (
    DatabaseConfig,
    ProcessingConfig,
    ProgressTrackingConfig,
    LoggingConfig,
    GpuConfig,
    WebConfig,
    UIConfig,
    AppConfig,
)


class TestDatabaseConfig:
    """Test DatabaseConfig Pydantic model validation"""

    def test_database_config_with_defaults(self):
        """Test DatabaseConfig uses sensible defaults"""
        config = DatabaseConfig()

        assert config.connection_timeout_seconds == 30
        assert config.journal_mode == "WAL"
        assert config.synchronous == "NORMAL"
        assert config.cache_size == -2000

    def test_database_config_with_custom_values(self):
        """Test DatabaseConfig accepts custom valid values"""
        config = DatabaseConfig(
            connection_timeout_seconds=60,
            journal_mode="DELETE",
            synchronous="FULL",
            cache_size=-4000
        )

        assert config.connection_timeout_seconds == 60
        assert config.journal_mode == "DELETE"
        assert config.synchronous == "FULL"
        assert config.cache_size == -4000

    def test_database_config_validates_timeout_min(self):
        """Test DatabaseConfig rejects timeout below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(connection_timeout_seconds=0)

        assert "connection_timeout_seconds" in str(exc_info.value)

    def test_database_config_validates_timeout_max(self):
        """Test DatabaseConfig rejects timeout above maximum"""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(connection_timeout_seconds=301)

        assert "connection_timeout_seconds" in str(exc_info.value)

    def test_database_config_validates_journal_mode(self):
        """Test DatabaseConfig rejects invalid journal mode"""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(journal_mode="INVALID")

        assert "journal_mode" in str(exc_info.value)

    def test_database_config_validates_synchronous(self):
        """Test DatabaseConfig rejects invalid synchronous value"""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(synchronous="INVALID")

        assert "synchronous" in str(exc_info.value)


class TestProcessingConfig:
    """Test ProcessingConfig Pydantic model validation"""

    def test_processing_config_with_defaults(self):
        """Test ProcessingConfig uses sensible defaults"""
        config = ProcessingConfig()

        assert config.max_retries == 3
        assert config.retry_delay_seconds == 5
        assert config.max_workers == 4

    def test_processing_config_with_custom_values(self):
        """Test ProcessingConfig accepts custom valid values"""
        config = ProcessingConfig(
            max_retries=5,
            retry_delay_seconds=10,
            max_workers=8
        )

        assert config.max_retries == 5
        assert config.retry_delay_seconds == 10
        assert config.max_workers == 8

    def test_processing_config_validates_max_retries(self):
        """Test ProcessingConfig rejects negative retries"""
        with pytest.raises(ValidationError) as exc_info:
            ProcessingConfig(max_retries=-1)

        assert "max_retries" in str(exc_info.value)

    def test_processing_config_validates_retry_delay(self):
        """Test ProcessingConfig rejects invalid retry delay"""
        with pytest.raises(ValidationError) as exc_info:
            ProcessingConfig(retry_delay_seconds=0)

        assert "retry_delay_seconds" in str(exc_info.value)

    def test_processing_config_validates_max_workers(self):
        """Test ProcessingConfig rejects invalid max_workers"""
        with pytest.raises(ValidationError) as exc_info:
            ProcessingConfig(max_workers=0)

        assert "max_workers" in str(exc_info.value)


class TestProgressTrackingConfig:
    """Test ProgressTrackingConfig Pydantic model validation"""

    def test_progress_tracking_with_defaults(self):
        """Test ProgressTrackingConfig uses sensible defaults"""
        config = ProgressTrackingConfig()

        assert config.polling_interval_seconds == 5
        assert "CSV_INTERPRETING" in config.coarse_phase_progress
        assert config.coarse_phase_progress["CSV_INTERPRETING"] == 10.0
        assert config.coarse_phase_progress["COMPLETED"] == 100.0

    def test_progress_tracking_with_custom_values(self):
        """Test ProgressTrackingConfig accepts custom values"""
        custom_progress = {
            "PHASE1": 25.0,
            "PHASE2": 50.0,
            "PHASE3": 100.0
        }
        config = ProgressTrackingConfig(
            polling_interval_seconds=10,
            coarse_phase_progress=custom_progress
        )

        assert config.polling_interval_seconds == 10
        assert config.coarse_phase_progress == custom_progress

    def test_progress_tracking_validates_polling_interval(self):
        """Test ProgressTrackingConfig rejects invalid polling interval"""
        with pytest.raises(ValidationError) as exc_info:
            ProgressTrackingConfig(polling_interval_seconds=0)

        assert "polling_interval_seconds" in str(exc_info.value)

    def test_progress_tracking_validates_progress_values(self):
        """Test ProgressTrackingConfig validates progress percentages"""
        with pytest.raises(ValidationError) as exc_info:
            ProgressTrackingConfig(
                coarse_phase_progress={"PHASE": 150.0}  # > 100
            )

        assert "coarse_phase_progress" in str(exc_info.value)


class TestLoggingConfig:
    """Test LoggingConfig Pydantic model validation"""

    def test_logging_config_with_defaults(self):
        """Test LoggingConfig uses sensible defaults"""
        config = LoggingConfig()

        assert config.level == "INFO"
        assert config.log_dir == "{base_directory}/logs"
        assert config.max_file_size_mb == 10
        assert config.backup_count == 5

    def test_logging_config_with_custom_values(self):
        """Test LoggingConfig accepts custom values"""
        config = LoggingConfig(
            level="DEBUG",
            log_dir="/custom/logs",
            max_file_size_mb=20,
            backup_count=10
        )

        assert config.level == "DEBUG"
        assert config.log_dir == "/custom/logs"
        assert config.max_file_size_mb == 20
        assert config.backup_count == 10

    def test_logging_config_validates_level(self):
        """Test LoggingConfig rejects invalid log level"""
        with pytest.raises(ValidationError) as exc_info:
            LoggingConfig(level="INVALID")

        assert "level" in str(exc_info.value)

    def test_logging_config_validates_file_size(self):
        """Test LoggingConfig rejects invalid file size"""
        with pytest.raises(ValidationError) as exc_info:
            LoggingConfig(max_file_size_mb=0)

        assert "max_file_size_mb" in str(exc_info.value)


class TestGpuConfig:
    """Test GpuConfig Pydantic model validation"""

    def test_gpu_config_with_defaults(self):
        """Test GpuConfig uses sensible defaults"""
        config = GpuConfig()

        assert config.enabled is True
        assert config.gpu_monitor_command is None
        assert config.monitor_interval == 60
        assert config.assignment_grace_period_seconds == 60
        assert config.memory_threshold_mb == 1000
        assert config.utilization_threshold_percent == 80
        assert config.polling_interval_seconds == 10

    def test_gpu_config_with_monitor_command(self):
        """Test GpuConfig accepts gpu_monitor_command and monitor_interval from YAML"""
        nvidia_cmd = (
            "nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used,"
            "memory.free,temperature.gpu,utilization.gpu --format=csv,noheader,nounits"
        )
        config = GpuConfig(
            gpu_monitor_command=nvidia_cmd,
            monitor_interval=10,
            assignment_grace_period_seconds=90,
        )

        assert config.gpu_monitor_command == nvidia_cmd
        assert config.monitor_interval == 10
        assert config.assignment_grace_period_seconds == 90

    def test_gpu_config_validates_thresholds(self):
        """Test GpuConfig validates threshold values"""
        with pytest.raises(ValidationError) as exc_info:
            GpuConfig(utilization_threshold_percent=150)

        assert "utilization_threshold_percent" in str(exc_info.value)

    def test_gpu_config_validates_monitor_interval(self):
        """Test GpuConfig rejects monitor_interval below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            GpuConfig(monitor_interval=0)

        assert "monitor_interval" in str(exc_info.value)

    def test_gpu_config_validates_assignment_grace_period(self):
        """Test GpuConfig rejects negative assignment grace periods"""
        with pytest.raises(ValidationError) as exc_info:
            GpuConfig(assignment_grace_period_seconds=-1)

        assert "assignment_grace_period_seconds" in str(exc_info.value)


class TestWebConfig:
    """Test WebConfig Pydantic model validation"""

    def test_web_config_with_defaults(self):
        """Test WebConfig uses sensible defaults"""
        config = WebConfig()

        assert config.enabled is False
        assert config.port == 8080
        assert config.ttyd_path == "ttyd"
        assert config.bind_address == "0.0.0.0"
        assert config.permit_write is False
        assert config.reconnect is True

    def test_web_config_with_custom_values(self):
        """Test WebConfig accepts custom values"""
        config = WebConfig(enabled=True, port=9090, ttyd_path="/usr/local/bin/ttyd")

        assert config.enabled is True
        assert config.port == 9090
        assert config.ttyd_path == "/usr/local/bin/ttyd"

    def test_web_config_validates_port(self):
        """Test WebConfig rejects invalid port"""
        with pytest.raises(ValidationError) as exc_info:
            WebConfig(port=0)

        assert "port" in str(exc_info.value)


class TestUIConfig:
    """Test UIConfig Pydantic model validation"""

    def test_ui_config_with_defaults(self):
        """Test UIConfig uses sensible defaults"""
        config = UIConfig()

        assert config.auto_start is False
        assert config.port == 8501
        assert isinstance(config.web, WebConfig)
        assert config.web.enabled is False
        assert config.refresh_interval_seconds == 1
        assert config.max_cases_display == 50

    def test_ui_config_with_yaml_values(self):
        """Test UIConfig accepts all fields present in config.yaml"""
        config = UIConfig(
            auto_start=True,
            port=8501,
            web=WebConfig(
                enabled=True,
                port=8080,
                ttyd_path="ttyd",
                bind_address="0.0.0.0",
                permit_write=False,
                reconnect=True,
            ),
            refresh_interval_seconds=1,
            max_cases_display=50,
        )

        assert config.auto_start is True
        assert config.web.enabled is True
        assert config.web.port == 8080

    def test_ui_config_model_dump_includes_web(self):
        """Test UIConfig.model_dump() includes the nested web dict"""
        config = UIConfig(auto_start=True, web=WebConfig(enabled=True))
        dumped = config.model_dump()

        assert dumped["auto_start"] is True
        assert isinstance(dumped["web"], dict)
        assert dumped["web"]["enabled"] is True

    def test_ui_config_validates_values(self):
        """Test UIConfig validates configuration values"""
        with pytest.raises(ValidationError) as exc_info:
            UIConfig(refresh_interval_seconds=0)

        assert "refresh_interval_seconds" in str(exc_info.value)


class TestAppConfig:
    """Test AppConfig (root configuration) Pydantic model"""

    def test_app_config_with_defaults(self):
        """Test AppConfig initializes all sub-configs with defaults"""
        config = AppConfig()

        assert isinstance(config.database, DatabaseConfig)
        assert isinstance(config.processing, ProcessingConfig)
        assert isinstance(config.progress_tracking, ProgressTrackingConfig)
        assert isinstance(config.logging, LoggingConfig)
        assert isinstance(config.gpu, GpuConfig)
        assert isinstance(config.ui, UIConfig)

    def test_app_config_with_custom_sections(self):
        """Test AppConfig accepts custom sub-configurations"""
        config = AppConfig(
            database=DatabaseConfig(connection_timeout_seconds=60),
            processing=ProcessingConfig(max_retries=5)
        )

        assert config.database.connection_timeout_seconds == 60
        assert config.processing.max_retries == 5

    def test_app_config_validates_nested(self):
        """Test AppConfig validates nested configurations"""
        with pytest.raises(ValidationError) as exc_info:
            AppConfig(
                database=DatabaseConfig(connection_timeout_seconds=500)  # > 300
            )

        # Error message should mention the validation error
        error_str = str(exc_info.value)
        assert "connection_timeout_seconds" in error_str
        assert "less than or equal to 300" in error_str


class TestConfigFromDict:
    """Test loading Pydantic configs from dictionary (YAML-like)"""

    def test_app_config_from_dict(self):
        """Test AppConfig can be instantiated from dict"""
        config_dict = {
            "database": {
                "connection_timeout_seconds": 45,
                "journal_mode": "WAL"
            },
            "processing": {
                "max_retries": 4
            }
        }

        config = AppConfig(**config_dict)

        assert config.database.connection_timeout_seconds == 45
        assert config.processing.max_retries == 4

    def test_app_config_from_dict_with_validation_error(self):
        """Test AppConfig raises ValidationError for invalid dict"""
        config_dict = {
            "database": {
                "connection_timeout_seconds": 1000  # > 300
            }
        }

        with pytest.raises(ValidationError):
            AppConfig(**config_dict)
