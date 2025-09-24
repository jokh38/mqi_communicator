# =====================================================================================
# Target File: src/utils/path_manager.py
# Source Reference: Path handling logic from various modules
# =====================================================================================
"""Manages file system paths and operations in a centralized, reusable way."""

from pathlib import Path
from typing import List, Optional, Union, Dict, Any
import os
import tempfile
import shutil

from src.infrastructure.logging_handler import StructuredLogger
from src.domain.errors import ValidationError


class PathManager:
    """Manages file system paths and operations in a centralized, reusable way."""

    def __init__(self, logger: Optional[StructuredLogger] = None):
        """Initializes the path manager.

        Args:
            logger (Optional[StructuredLogger], optional): An optional logger instance. Defaults to None.
        """
        self.logger = logger

    def validate_case_path(self, case_path: Union[str, Path]) -> Path:
        """Validates and normalizes a case directory path.

        Args:
            case_path (Union[str, Path]): The path to the case directory.

        Raises:
            ValidationError: If the path does not exist or is not a directory.

        Returns:
            Path: The validated Path object.
        """
        path = Path(case_path)
        if not path.exists():
            raise ValidationError(f"Case path does not exist: {path}")
        if not path.is_dir():
            raise ValidationError(f"Case path is not a directory: {path}")
        return path

    def ensure_directory_exists(self, directory_path: Union[str, Path]) -> Path:
        """Ensures that a directory exists, creating it if necessary.

        Args:
            directory_path (Union[str, Path]): The path to the directory.

        Raises:
            ValidationError: If the directory could not be created.

        Returns:
            Path: The Path object of the directory.
        """
        path = Path(directory_path)
        try:
            path.mkdir(parents=True, exist_ok=True)
            self._log_path_operation("ensure_directory", path)
            return path
        except OSError as e:
            raise ValidationError(f"Failed to create directory: {path}") from e

    def get_temp_directory(self, prefix: str = "mqi_temp") -> Path:
        """Creates and returns a temporary directory path.

        Args:
            prefix (str, optional): The prefix for the temporary directory name. Defaults to "mqi_temp".

        Returns:
            Path: The path to the temporary directory.
        """
        return Path(tempfile.mkdtemp(prefix=prefix))

    def cleanup_temp_directory(self, temp_path: Path) -> None:
        """Safely removes a temporary directory and its contents.

        Args:
            temp_path (Path): The path to the temporary directory.
        """
        try:
            shutil.rmtree(temp_path)
            self._log_path_operation("cleanup_temp_directory", temp_path)
        except OSError as e:
            if self.logger:
                self.logger.error("Failed to cleanup temp directory", {"path": str(temp_path), "error": str(e)})

    def find_files_by_pattern(self, directory: Union[str, Path], pattern: str) -> List[Path]:
        """Finds files in a directory matching a glob pattern.

        Args:
            directory (Union[str, Path]): The directory to search in.
            pattern (str): The glob pattern to match.

        Returns:
            List[Path]: A list of matching file paths.
        """
        return list(Path(directory).glob(pattern))

    def get_file_size(self, file_path: Union[str, Path]) -> int:
        """Gets the size of a file in bytes.

        Args:
            file_path (Union[str, Path]): The path to the file.

        Raises:
            ValidationError: If the file is not found.

        Returns:
            int: The size of the file in bytes.
        """
        try:
            return os.path.getsize(file_path)
        except FileNotFoundError as e:
            raise ValidationError(f"File not found: {file_path}") from e

    def is_directory_writable(self, directory_path: Union[str, Path]) -> bool:
        """Checks if a directory is writable.

        Args:
            directory_path (Union[str, Path]): The path to the directory.

        Returns:
            bool: True if the directory is writable, False otherwise.
        """
        return os.access(directory_path, os.W_OK)

    def get_relative_path(self, path: Union[str, Path], base_path: Union[str, Path]) -> Path:
        """Gets the relative path from a base path.

        Args:
            path (Union[str, Path]): The path to make relative.
            base_path (Union[str, Path]): The base path.

        Returns:
            Path: The relative path.
        """
        return Path(os.path.relpath(path, base_path))

    def safe_copy_file(self, source: Union[str, Path], destination: Union[str, Path]) -> None:
        """Safely copies a file with error handling and logging.

        Args:
            source (Union[str, Path]): The source file path.
            destination (Union[str, Path]): The destination file path.

        Raises:
            ValidationError: If the copy operation fails.
        """
        try:
            shutil.copy2(source, destination)
            self._log_path_operation("copy_file", source)
        except (shutil.Error, IOError) as e:
            raise ValidationError(f"Failed to copy file from {source} to {destination}") from e

    def safe_move_file(self, source: Union[str, Path], destination: Union[str, Path]) -> None:
        """Safely moves a file with error handling and logging.

        Args:
            source (Union[str, Path]): The source file path.
            destination (Union[str, Path]): The destination file path.

        Raises:
            ValidationError: If the move operation fails.
        """
        try:
            shutil.move(str(source), str(destination))
            self._log_path_operation("move_file", source)
        except (shutil.Error, IOError) as e:
            raise ValidationError(f"Failed to move file from {source} to {destination}") from e

    def get_case_metadata(self, case_path: Path) -> Dict[str, Any]:
        """Extracts metadata from a case directory.

        Args:
            case_path (Path): The path to the case directory.

        Returns:
            Dict[str, Any]: A dictionary containing metadata about the case directory.
        """
        total_files = 0
        total_size = 0
        for dirpath, _, filenames in os.walk(case_path):
            total_files += len(filenames)
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        return {"file_count": total_files, "total_size_bytes": total_size}

    def _log_path_operation(self, operation: str, path: Union[str, Path], success: bool = True) -> None:
        """Logs a path operation for debugging and auditing.

        Args:
            operation (str): The name of the operation.
            path (Union[str, Path]): The path involved in the operation.
            success (bool, optional): Whether the operation was successful. Defaults to True.
        """
        if self.logger:
            self.logger.debug("Path operation", {
                "operation": operation,
                "path": str(path),
                "success": success
            })