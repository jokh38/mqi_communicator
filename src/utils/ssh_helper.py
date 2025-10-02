"""SSH client initialization helper for HPC connections."""

import paramiko
from typing import Optional

from src.config.settings import Settings
from src.infrastructure.logging_handler import StructuredLogger


def create_ssh_client(settings: Settings, logger: StructuredLogger) -> Optional[paramiko.SSHClient]:
    """Creates and connects an SSH client based on HPC configuration.

    This helper encapsulates the common pattern of creating and configuring
    an SSH client for HPC connections, including:
    1. Getting HPC configuration from settings
    2. Validating required connection parameters
    3. Creating and configuring the SSH client
    4. Establishing the connection with proper timeout handling

    Args:
        settings: Application settings containing HPC connection details
        logger: Structured logger for connection events

    Returns:
        Connected paramiko.SSHClient instance, or None if HPC not configured
        or connection fails

    Example:
        ssh_client = create_ssh_client(settings, logger)
        if ssh_client:
            # Use SSH client for remote operations
            pass
    """
    try:
        hpc_config = settings.get_hpc_connection()

        # Validate configuration
        if not hpc_config or not all(k in hpc_config for k in ["host", "user", "ssh_key_path"]):
            logger.warning("HPC connection details not fully configured. Remote operations disabled.")
            return None

        # Create and configure client
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Extract connection parameters with defaults
        connection_timeout = hpc_config.get("connection_timeout_seconds")
        if connection_timeout is None:
            logger.warning("HPC connection_timeout_seconds not in config, defaulting to 30 seconds.")
            connection_timeout = 30

        # Establish connection
        logger.info("Connecting to HPC", {"host": hpc_config["host"]})
        ssh_client.connect(
            hostname=hpc_config.get("host"),
            username=hpc_config.get("user"),
            key_filename=hpc_config.get("ssh_key_path"),
            port=hpc_config.get("port", 22),
            timeout=connection_timeout
        )

        logger.info("Successfully connected to HPC")
        return ssh_client

    except Exception as e:
        logger.error("Failed to establish SSH connection to HPC", {"error": str(e)})
        return None
