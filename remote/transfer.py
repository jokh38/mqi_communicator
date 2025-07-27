"""
TransferManager - SFTP file transfer operations.

Moved from sftp_manager.py to provide unified file transfer management.
"""

import os
import paramiko
import stat
import time
import socket
from pathlib import Path
from typing import Optional, Dict, Any, TYPE_CHECKING

from core.logging import Logger

if TYPE_CHECKING:
    from remote.connection import ConnectionManager

# Security constants
MAX_DEPTH = 20  # Maximum recursion depth to prevent infinite loops


class TransferManager:
    """Manages SFTP file transfer operations using ConnectionManager."""
    
    def __init__(self, connection_manager: 'ConnectionManager', logger: Optional[Logger] = None):
        self.connection_manager = connection_manager
        self.logger = logger
        self.sftp = None
        self.transfer_stats = {
            'total_bytes': 0,
            'transferred_bytes': 0,
            'start_time': None,
            'current_speed': 0
        }
    
    def _ensure_connected(self) -> bool:
        """Ensure SFTP connection is established."""
        try:
            # Check connection status
            if not self.connection_manager.is_connected():
                if not self.connection_manager.connect():
                    return False
            
            if not self.sftp:
                self.sftp = self.connection_manager.get_sftp_client()
                if not self.sftp:
                    return False
            
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to establish SFTP connection: {e}")
            return False
    
    def _retry_on_network_error(self, func, max_retries: int = 3):
        """Retry function on network errors."""
        
        network_error_types = (
            socket.error,
            paramiko.SSHException,
            paramiko.AuthenticationException,
            paramiko.ChannelException,
            OSError,
            TimeoutError,
            ConnectionError
        )
        
        for attempt in range(max_retries):
            try:
                return func()
            except network_error_types as e:
                if attempt == max_retries - 1:
                    raise e
                if self.logger:
                    self.logger.warning(f"Network error on attempt {attempt + 1}/{max_retries}: {e}. Retrying...")
                # Reset connection on network error
                self._reset_connection()
                time.sleep(2 ** attempt)  # Exponential backoff
    
    def _reset_connection(self):
        """Reset SFTP connection."""
        try:
            if self.sftp:
                self.sftp.close()
                self.sftp = None
            self.connection_manager.disconnect()
        except Exception:
            pass
    
    def create_remote_directory(self, remote_path: str) -> bool:
        """Create remote directory recursively."""
        if not self._ensure_connected():
            return False
        
        try:
            path_parts = remote_path.split('/')
            current_path = "" if not remote_path.startswith('/') else "/"
            
            for part in path_parts:
                if not part:
                    continue
                
                if current_path and not current_path.endswith('/'):
                    current_path += '/'
                current_path += part
                
                try:
                    self.sftp.stat(current_path)
                except FileNotFoundError:
                    # Directory doesn't exist, create it
                    self.sftp.mkdir(current_path)
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to create remote directory {remote_path}: {e}")
            return False
    
    def upload_file(self, local_path: str, remote_path: str, case_id: str = "", 
                    status_display=None, current_file: int = 0, total_files: int = 0) -> bool:
        """Upload single file with pre-flight checks and post-flight verification."""
        def _upload_file_internal():
            if not self._ensure_connected():
                raise ConnectionError("Failed to establish SFTP connection")
            
            local_file = Path(local_path)
            
            # Pre-flight check: Ensure local file exists
            if not local_file.exists() or not local_file.is_file():
                raise FileNotFoundError(f"Local file does not exist or is not a file: {local_path}")
            
            local_size = local_file.stat().st_size
            
            # Initialize transfer tracking
            transfer_state = {
                'start_time': time.time(),
                'last_update_time': time.time(),
                'last_bytes': 0,
                'file_size': local_size
            }
            
            # Update display with current file transfer info
            if status_display and case_id:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    transfer_action="Uploading",
                    current_file_index=current_file if total_files > 0 else None,
                    total_files=total_files if total_files > 0 else None
                )
            
            # Upload file with progress callback
            def progress_callback(transferred, total):
                current_time = time.time()
                # Update every 0.5 seconds to avoid too frequent updates
                if current_time - transfer_state['last_update_time'] >= 0.5:
                    elapsed = current_time - transfer_state['start_time']
                    if elapsed > 0:
                        current_speed = transferred / elapsed / (1024 * 1024)  # MB/s
                        
                        # Update status display with real-time speed
                        if status_display and case_id:
                            status_display.update_case_status(
                                case_id=case_id,
                                status="PROCESSING",
                                transfer_action="Uploading",
                                transfer_speed=current_speed,
                                current_file_index=current_file if total_files > 0 else None,
                                total_files=total_files if total_files > 0 else None
                            )
                    
                    transfer_state['last_update_time'] = current_time
                    transfer_state['last_bytes'] = transferred
            
            upload_start_time = time.time()
            self.sftp.put(str(local_file), remote_path, callback=progress_callback)
            upload_duration = time.time() - upload_start_time
            
            # Calculate final transfer speed
            if upload_duration > 0:
                speed_bytes_per_sec = local_size / upload_duration
                speed_mbps = speed_bytes_per_sec / (1024 * 1024)
                
                # Update transfer stats
                self.transfer_stats['transferred_bytes'] += local_size
                self.transfer_stats['current_speed'] = speed_mbps
            
            # Post-flight verification: Check existence and size
            try:
                remote_stat = self.sftp.stat(remote_path)
                if remote_stat.st_size == local_size:
                    return True
                else:
                    error_msg = f"Size mismatch: local={local_size}, remote={remote_stat.st_size}"
                    if self.logger:
                        self.logger.error(f"File verification failed: {remote_path} - {error_msg}")
                    raise IOError(f"SFTP size mismatch for {remote_path}")
            except FileNotFoundError:
                if self.logger:
                    self.logger.error(f"Post-flight verification failed for '{remote_path}'. File not found on remote server.")
                raise
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Post-flight verification failed for '{remote_path}' with error: {e}")
                raise
        
        try:
            return self._retry_on_network_error(_upload_file_internal)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to upload file {local_path} after all checks and retries: {e}")
            return False
    
    def download_file(self, remote_path: str, local_path: str, 
                     status_display=None, case_id: str = "") -> bool:
        """Download single file with verification."""
        def _download_file_internal():
            if not self._ensure_connected():
                raise ConnectionError("Failed to establish SFTP connection")
            
            # Ensure local directory exists
            local_file = Path(local_path)
            local_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Get remote file size
            try:
                remote_stat = self.sftp.stat(remote_path)
                remote_size = remote_stat.st_size
            except FileNotFoundError:
                raise FileNotFoundError(f"Remote file not found: {remote_path}")
            
            # Update status display
            if status_display and case_id:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    transfer_action="Downloading"
                )
            
            # Download with progress callback
            def progress_callback(transferred, total):
                if status_display and case_id:
                    progress = (transferred / total) * 100 if total > 0 else 0
                    status_display.update_case_status(
                        case_id=case_id,
                        status="PROCESSING",
                        transfer_action="Downloading",
                        transfer_progress=progress
                    )
            
            download_start_time = time.time()
            self.sftp.get(remote_path, str(local_file), callback=progress_callback)
            download_duration = time.time() - download_start_time
            
            # Verify download
            if local_file.exists():
                local_size = local_file.stat().st_size
                if local_size == remote_size:
                    return True
                else:
                    if self.logger:
                        self.logger.error(f"Download verification failed: size mismatch for {local_path}")
                    raise IOError(f"Download size mismatch for {local_path}")
            else:
                raise FileNotFoundError(f"Downloaded file not found: {local_path}")
        
        try:
            return self._retry_on_network_error(_download_file_internal)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to download file {remote_path}: {e}")
            return False
    
    def upload_directory(self, local_path: str, remote_path: str, 
                        status_display=None, case_id: str = None) -> bool:
        """Upload entire directory recursively with progress reporting."""
        if not self._ensure_connected():
            return False
        
        try:
            local_dir = Path(local_path)
            if not local_dir.exists() or not local_dir.is_dir():
                if self.logger:
                    self.logger.error(f"Local directory not found: {local_path}")
                return False
            
            # Create remote directory structure
            if not self.create_remote_directory(remote_path):
                return False
            
            # Count total files for progress tracking
            total_files = sum(1 for _ in local_dir.rglob('*') if _.is_file())
            current_file = 0
            
            # Initialize transfer stats
            self.transfer_stats['start_time'] = time.time()
            self.transfer_stats['total_bytes'] = 0
            self.transfer_stats['transferred_bytes'] = 0
            
            # Upload files recursively
            for local_file in local_dir.rglob('*'):
                if local_file.is_file():
                    # Calculate relative path
                    relative_path = local_file.relative_to(local_dir)
                    remote_file_path = f"{remote_path}/{relative_path.as_posix()}"
                    
                    # Create remote subdirectory if needed
                    remote_dir = os.path.dirname(remote_file_path)
                    if remote_dir != remote_path:
                        self.create_remote_directory(remote_dir)
                    
                    # Upload file
                    current_file += 1
                    if not self.upload_file(str(local_file), remote_file_path, case_id, 
                                          status_display, current_file, total_files):
                        if self.logger:
                            self.logger.error(f"Failed to upload file: {local_file}")
                        return False
            
            if self.logger:
                elapsed = time.time() - self.transfer_stats['start_time']
                self.logger.info(f"Directory upload completed: {total_files} files in {elapsed:.2f}s")
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to upload directory {local_path}: {e}")
            return False
    
    def download_directory(self, remote_path: str, local_path: str, 
                          status_display=None, case_id: str = None) -> bool:
        """Download entire directory recursively."""
        if not self._ensure_connected():
            return False
        
        try:
            local_dir = Path(local_path)
            local_dir.mkdir(parents=True, exist_ok=True)
            
            # List remote directory contents
            def list_remote_files(path, depth=0):
                if depth > MAX_DEPTH:
                    return []
                
                files = []
                try:
                    for item in self.sftp.listdir_attr(path):
                        item_path = f"{path}/{item.filename}"
                        if stat.S_ISDIR(item.st_mode):
                            # Recursively list subdirectory
                            files.extend(list_remote_files(item_path, depth + 1))
                        else:
                            files.append(item_path)
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Failed to list remote directory {path}: {e}")
                
                return files
            
            remote_files = list_remote_files(remote_path)
            total_files = len(remote_files)
            current_file = 0
            
            # Download each file
            for remote_file in remote_files:
                # Calculate local file path
                relative_path = remote_file[len(remote_path):].lstrip('/')
                local_file_path = local_dir / relative_path
                
                current_file += 1
                if not self.download_file(remote_file, str(local_file_path), status_display, case_id):
                    if self.logger:
                        self.logger.error(f"Failed to download file: {remote_file}")
                    return False
            
            if self.logger:
                self.logger.info(f"Directory download completed: {total_files} files")
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to download directory {remote_path}: {e}")
            return False
    
    def file_exists(self, remote_path: str) -> bool:
        """Check if remote file exists."""
        if not self._ensure_connected():
            return False
        
        try:
            self.sftp.stat(remote_path)
            return True
        except FileNotFoundError:
            return False
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error checking file existence {remote_path}: {e}")
            return False
    
    def get_file_size(self, remote_path: str) -> Optional[int]:
        """Get remote file size."""
        if not self._ensure_connected():
            return None
        
        try:
            stat_info = self.sftp.stat(remote_path)
            return stat_info.st_size
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error getting file size {remote_path}: {e}")
            return None
    
    def remove_file(self, remote_path: str) -> bool:
        """Remove remote file."""
        if not self._ensure_connected():
            return False
        
        try:
            self.sftp.remove(remote_path)
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error removing file {remote_path}: {e}")
            return False
    
    def get_transfer_stats(self) -> Dict[str, Any]:
        """Get current transfer statistics."""
        return self.transfer_stats.copy()
    
    def reset_transfer_stats(self):
        """Reset transfer statistics."""
        self.transfer_stats = {
            'total_bytes': 0,
            'transferred_bytes': 0,
            'start_time': None,
            'current_speed': 0
        }
    
    def __enter__(self):
        """Context manager entry."""
        self._ensure_connected()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if self.sftp:
            try:
                self.sftp.close()
            except (OSError, paramiko.SSHException):
                pass
            self.sftp = None