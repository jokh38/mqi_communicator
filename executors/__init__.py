"""
Executors package for command execution abstraction.

This package provides abstractions for executing commands locally and remotely:
- BaseExecutor: Abstract base class for command execution
- LocalExecutor: Local command execution using subprocess
- RemoteExecutor: Remote command execution via SSH
"""

from .base import BaseExecutor, ExecutionResult
from .local import LocalExecutor
from .remote import RemoteExecutor

__all__ = ["BaseExecutor", "ExecutionResult", "LocalExecutor", "RemoteExecutor"]