"""
Remote package for SSH/SFTP communication abstractions.

This package provides abstractions for remote communication:
- ConnectionManager: SSH connection management and client creation
- TransferManager: SFTP file transfer operations
"""

from .connection import ConnectionManager
from .transfer import TransferManager

__all__ = ["ConnectionManager", "TransferManager"]