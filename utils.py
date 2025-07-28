"""
Generic utility functions for the mqi_communicator project.

This module contains stateless helper functions that can be used across
different modules without introducing coupling.
"""

import hashlib
import functools
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


def retry_on_exception(max_retries: int = 3, 
                      delay_seconds: float = 1.0,
                      exceptions_to_catch: Union[Exception, tuple] = Exception,
                      backoff_strategy: str = "exponential",
                      logger=None):
    """
    Decorator for retrying function execution on specific exceptions.
    
    Args:
        max_retries: Maximum number of retry attempts
        delay_seconds: Base delay between retries in seconds
        exceptions_to_catch: Exception types to catch and retry on
        backoff_strategy: 'linear', 'exponential', or 'fixed'
        logger: Logger instance for retry notifications
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):  # +1 for initial attempt
                try:
                    return func(*args, **kwargs)
                except exceptions_to_catch as e:
                    last_exception = e
                    
                    if attempt < max_retries:  # Don't delay after the last attempt
                        if backoff_strategy == "exponential":
                            delay = delay_seconds * (2 ** attempt)
                        elif backoff_strategy == "linear":
                            delay = delay_seconds * (attempt + 1)
                        else:  # fixed
                            delay = delay_seconds
                        
                        if logger:
                            logger.warning(f"Attempt {attempt + 1}/{max_retries + 1} failed: {e}. Retrying in {delay:.2f}s...")
                        
                        time.sleep(delay)
                    else:
                        if logger:
                            logger.error(f"All {max_retries + 1} attempts failed. Last error: {e}")
            
            # Re-raise the last exception if all retries failed
            raise last_exception
        
        return wrapper
    return decorator


def calculate_file_hash(file_path: Union[str, Path], algorithm: str = "md5") -> str:
    """
    Calculate hash of a file.
    
    Args:
        file_path: Path to the file
        algorithm: Hash algorithm ('md5', 'sha1', 'sha256')
    
    Returns:
        str: Hexadecimal hash string
    """
    hash_obj = hashlib.new(algorithm)
    
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_obj.update(chunk)
    
    return hash_obj.hexdigest()


def calculate_directory_hash(directory_path: Union[str, Path], algorithm: str = "md5") -> str:
    """
    Calculate hash of directory contents for change detection.
    
    Args:
        directory_path: Path to the directory
        algorithm: Hash algorithm ('md5', 'sha1', 'sha256')
    
    Returns:
        str: Hexadecimal hash string
    """
    directory_path = Path(directory_path)
    
    if not directory_path.exists():
        return ""
    
    hash_obj = hashlib.new(algorithm)
    
    # Include all files with their sizes and modification times
    for file_path in sorted(directory_path.rglob("*")):
        if file_path.is_file():
            try:
                stat = file_path.stat()
                hash_obj.update(f"{file_path.name}:{stat.st_size}:{stat.st_mtime}".encode())
            except (OSError, IOError):
                continue
    
    return hash_obj.hexdigest()


def ensure_directory(directory_path: Union[str, Path]) -> bool:
    """
    Ensure directory exists, create if necessary.
    
    Args:
        directory_path: Path to directory
    
    Returns:
        bool: True if directory exists or was created successfully
    """
    try:
        Path(directory_path).mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def get_directory_size(directory_path: Union[str, Path]) -> int:
    """
    Get total size of directory in bytes.
    
    Args:
        directory_path: Path to directory
    
    Returns:
        int: Total size in bytes
    """
    directory_path = Path(directory_path)
    
    if not directory_path.exists():
        return 0
    
    total_size = 0
    for file_path in directory_path.rglob("*"):
        if file_path.is_file():
            try:
                total_size += file_path.stat().st_size
            except Exception:
                continue
    
    return total_size


def format_bytes(bytes_count: int) -> str:
    """
    Format bytes into human readable string.
    
    Args:
        bytes_count: Number of bytes
    
    Returns:
        str: Formatted string (e.g., "1.5 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_count < 1024.0:
            return f"{bytes_count:.1f} {unit}"
        bytes_count /= 1024.0
    return f"{bytes_count:.1f} PB"


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to human readable string.
    
    Args:
        seconds: Duration in seconds
    
    Returns:
        str: Formatted string (e.g., "2h 30m 15s")
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    
    minutes = int(seconds // 60)
    seconds = int(seconds % 60)
    
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    
    hours = int(minutes // 60)
    minutes = int(minutes % 60)
    
    return f"{hours}h {minutes}m {seconds}s"


def safe_dict_get(dictionary: Dict[str, Any], key_path: str, default: Any = None) -> Any:
    """
    Safely get nested dictionary value using dot notation.
    
    Args:
        dictionary: Dictionary to search
        key_path: Dot-separated key path (e.g., "config.servers.host")
        default: Default value if key not found
    
    Returns:
        Any: Value at key path or default
    """
    keys = key_path.split('.')
    current = dictionary
    
    try:
        for key in keys:
            current = current[key]
        return current
    except (KeyError, TypeError):
        return default


def flatten_dict(dictionary: Dict[str, Any], separator: str = '.') -> Dict[str, Any]:
    """
    Flatten nested dictionary using separator.
    
    Args:
        dictionary: Dictionary to flatten
        separator: Separator for nested keys
    
    Returns:
        Dict[str, Any]: Flattened dictionary
    """
    def _flatten(obj, parent_key=''):
        items = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                new_key = f"{parent_key}{separator}{key}" if parent_key else key
                items.extend(_flatten(value, new_key).items())
        else:
            return {parent_key: obj}
        return dict(items)
    
    return _flatten(dictionary)


def chunk_list(lst: List[Any], chunk_size: int) -> List[List[Any]]:
    """
    Split list into chunks of specified size.
    
    Args:
        lst: List to chunk
        chunk_size: Size of each chunk
    
    Returns:
        List[List[Any]]: List of chunks
    """
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def validate_file_path(file_path: Union[str, Path], must_exist: bool = True) -> bool:
    """
    Validate file path.
    
    Args:
        file_path: Path to validate
        must_exist: Whether file must exist
    
    Returns:
        bool: True if valid
    """
    try:
        path = Path(file_path)
        
        if must_exist:
            return path.exists() and path.is_file()
        
        # Check if parent directory exists and path is valid
        return path.parent.exists() and str(path).strip() != ""
    except Exception:
        return False


def sanitize_filename(filename: str, replacement: str = "_") -> str:
    """
    Sanitize filename by replacing invalid characters.
    
    Args:
        filename: Original filename
        replacement: Character to replace invalid chars with
    
    Returns:
        str: Sanitized filename
    """
    invalid_chars = '<>:"/\\|?*'
    sanitized = filename
    
    for char in invalid_chars:
        sanitized = sanitized.replace(char, replacement)
    
    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip(' .')
    
    # Ensure filename is not empty
    if not sanitized:
        sanitized = f"unnamed{replacement}file"
    
    return sanitized


def merge_dicts(*dicts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge multiple dictionaries, with later dicts overriding earlier ones.
    
    Args:
        *dicts: Dictionaries to merge
    
    Returns:
        Dict[str, Any]: Merged dictionary
    """
    result = {}
    for d in dicts:
        if isinstance(d, dict):
            result.update(d)
    return result


def timestamp_to_string(timestamp: Optional[datetime] = None, 
                       format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Convert timestamp to formatted string.
    
    Args:
        timestamp: Datetime object (current time if None)
        format_str: Format string
    
    Returns:
        str: Formatted timestamp string
    """
    if timestamp is None:
        timestamp = datetime.now()
    
    return timestamp.strftime(format_str)


def string_to_timestamp(timestamp_str: str, 
                       format_str: str = "%Y-%m-%d %H:%M:%S") -> Optional[datetime]:
    """
    Convert string to timestamp.
    
    Args:
        timestamp_str: Timestamp string
        format_str: Format string
    
    Returns:
        Optional[datetime]: Parsed datetime or None if invalid
    """
    try:
        return datetime.strptime(timestamp_str, format_str)
    except ValueError:
        return None


def is_port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    """
    Check if a port is open on a host.
    
    Args:
        host: Host address
        port: Port number
        timeout: Connection timeout in seconds
    
    Returns:
        bool: True if port is open
    """
    
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            return result == 0
    except Exception:
        return False