import os
import paramiko
import stat
import time
import socket
from pathlib import Path
from typing import Optional, Callable, Dict, Any
import logging
from base_ssh_connector import BaseSSHConnector


class SFTPManager(BaseSSHConnector):
    def __init__(self, host: str, username: str, password: str, port: int = 22, timeout: int = 30, logger=None):
        super().__init__(host, username, password, port, timeout)
        self.sftp: Optional[paramiko.SFTPClient] = None
        self.logger = logger

    def _post_connect_setup(self) -> None:
        """Create SFTP client after SSH connection is established."""
        self.sftp = paramiko.SFTPClient.from_transport(self.transport)
    
    def _pre_disconnect_cleanup(self) -> None:
        """Close SFTP client before SSH disconnection."""
        if self.sftp:
            self.sftp.close()
            self.sftp = None

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
            logging.error(f"Failed to create remote directory {remote_path}: {e}")
            return False

    def upload_file(self, local_path: str, remote_path: str, case_id: str = "", 
                    status_display=None, current_file: int = 0, total_files: int = 0) -> bool:
        """Upload single file with pre-flight checks, post-flight verification, and network retry."""
        def _upload_file_internal():
            if not self._ensure_connected():
                raise ConnectionError("Failed to establish SFTP connection")
            
            local_file = Path(local_path)
            
            # 1. Pre-flight check: Ensure local file exists right before upload
            if not local_file.exists() or not local_file.is_file():
                logging.error(f"[SFTP] Pre-flight check failed: Local file not found at {local_path}")
                raise FileNotFoundError(f"Local file does not exist or is not a file: {local_path}")
            
            local_size = local_file.stat().st_size
            
            # Update rich display with current file transfer info
            if status_display and case_id:
                if total_files > 0:
                    transfer_info = f"Uploading ({current_file}/{total_files})"
                else:
                    transfer_info = "Uploading file..."
                
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    transfer_info=transfer_info
                )

            # The remote directory structure is assumed to be pre-created by `upload_directory`.
            # This function is now only responsible for the atomic upload operation.
            
            # 3. Upload file
            upload_start_time = time.time()
            self.sftp.put(str(local_file), remote_path)
            upload_duration = time.time() - upload_start_time
            
            # Log file upload completion with performance metrics (log file only, no console output)
            if self.logger:
                self.logger.log_network_activity({
                    "action": "file_upload_completed",
                    "local_path": str(local_file),
                    "remote_path": remote_path,
                    "file_size_bytes": local_size,
                    "upload_duration_ms": round(upload_duration * 1000, 2),
                    "transfer_rate_mbps": round((local_size / (1024 * 1024)) / upload_duration, 2) if upload_duration > 0 else 0
                })
                # No console output for file transfers - Rich display handles user feedback

            # 4. Post-flight verification: Check existence and size
            try:
                remote_stat = self.sftp.stat(remote_path)
                if remote_stat.st_size == local_size:
                    # File transfer verification logged to file only (no console output)
                    if self.logger:
                        self.logger.info(f"File transfer verified: {remote_path} (size: {remote_stat.st_size} bytes)")
                        # No console output for verification - reduces log spam
                    return True
                else:
                    error_msg = f"Size mismatch: local={local_size}, remote={remote_stat.st_size}"
                    if self.logger:
                        self.logger.error(f"File verification failed: {remote_path} - {error_msg}")
                    else:
                        logging.error(f"[SFTP] Post-flight verification failed for '{remote_path}'. {error_msg}")
                    raise IOError(f"SFTP size mismatch for {remote_path}")
            except FileNotFoundError:
                logging.error(f"[SFTP] Post-flight verification failed for '{remote_path}'. File not found on remote server.")
                raise
            except Exception as e:
                logging.error(f"[SFTP] Post-flight verification failed for '{remote_path}' with error: {e}")
                raise

        try:
            return self._retry_on_network_error(_upload_file_internal)
        except Exception as e:
            if self.logger:
                self.logger.log_exception(e, {
                    "operation": "file_upload_final_failure",
                    "local_path": str(local_path),
                    "case_id": case_id
                })
            else:
                logging.error(f"Failed to upload file {local_path} after all checks and retries: {e}")
            return False

    def upload_directory(self, local_path: str, remote_path: str, 
                        status_display: Optional[Any] = None, 
                        case_id: Optional[str] = None) -> bool:
        """Upload entire directory recursively with progress reporting."""
        if not self._ensure_connected():
            return False
        
        local_dir = Path(local_path)
        if not local_dir.is_dir():
            if self.logger:
                self.logger.error(f"Invalid directory path for upload: {local_dir}")
            else:
                logging.error(f"Local path is not a directory: {local_dir}")
            return False

        try:
            # Step 1: Create all directories on the remote first.
            # Directory upload start logged to file only (no console output)
            if self.logger:
                self.logger.info(f"Starting directory upload: {local_path} -> {remote_path}")
                # No console output for start - Rich display shows current operation
                
            self.create_remote_directory(remote_path)
            dir_count = 0
            for local_subdir in local_dir.rglob("*"):
                if local_subdir.is_dir():
                    relative_dir = local_subdir.relative_to(local_dir)
                    remote_subdir_path = f"{remote_path.rstrip('/')}/{str(relative_dir).replace(chr(92), '/')}"
                    self.create_remote_directory(remote_subdir_path)
                    dir_count += 1

            # Step 2: Upload all files now that directories are guaranteed to exist.
            all_files = [p for p in local_dir.rglob("*") if p.is_file()]
            total_files = len(all_files)
            
            # Log upload operation details (log file only, no console output) 
            if self.logger:
                self.logger.log_structured("INFO", "Directory upload started", {
                    "local_path": str(local_path),
                    "remote_path": remote_path,
                    "total_files": total_files,
                    "directories_created": dir_count,
                    "case_id": case_id
                })
                # No console output for directory operations - Rich display shows progress

            for i, file_path in enumerate(all_files):
                relative_path = file_path.relative_to(local_dir)
                remote_file_path = f"{remote_path.rstrip('/')}/{str(relative_path).replace(chr(92), '/')}"
                
                if not self.upload_file(str(file_path), remote_file_path, case_id, status_display, i+1, total_files):
                    logging.error(f"Stopping upload due to failure on file: {file_path}")
                    return False
                
                # Update progress
                if status_display and case_id:
                    progress = (i + 1) / total_files
                    transfer_info = f"Uploading: {i+1}/{total_files}"
                    status_display.update_case_status(
                        case_id=case_id,
                        status="PROCESSING",
                        stage="Uploading Data",
                        progress=progress,
                        transfer_info=transfer_info
                    )
            
            # Log successful directory upload completion (log file only, no console output)
            if self.logger:
                self.logger.log_structured("INFO", "Directory upload completed", {
                    "local_path": str(local_path),
                    "remote_path": remote_path,
                    "files_uploaded": total_files,
                    "directories_created": dir_count,
                    "case_id": case_id,
                    "success": True
                })
                # No console output for completion - Rich display shows final status
                
            if status_display and case_id:
                status_display.update_case_status(
                    case_id=case_id, 
                    status="PROCESSING", 
                    stage="Upload Complete",
                    transfer_info="All files uploaded successfully"
                )
            return True
            
        except Exception as e:
            # Log directory upload failure with context
            if self.logger:
                self.logger.log_exception(e, {
                    "operation": "directory_upload",
                    "local_path": str(local_path),
                    "remote_path": remote_path,
                    "case_id": case_id
                })
            else:
                logging.error(f"Failed to upload directory {local_path}: {e}")
            if status_display and case_id:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    stage="Upload Failed",
                    error_message=f"Upload failed: {str(e)}",
                    transfer_info=""
                )
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download single file with network retry."""
        def _download_file_internal():
            if not self._ensure_connected():
                raise ConnectionError("Failed to establish SFTP connection")
            
            # Create local directory if needed
            local_file = Path(local_path)
            local_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Download file
            self.sftp.get(remote_path, str(local_file))
            return True
        
        try:
            return self._retry_on_network_error(_download_file_internal)
        except Exception as e:
            logging.error(f"Failed to download file {remote_path} after retries: {e}")
            return False

    def download_directory(self, remote_path: str, local_path: str, 
                          status_display: Optional[Any] = None, 
                          case_id: Optional[str] = None) -> bool:
        """Download entire directory recursively with progress reporting."""
        if not self._ensure_connected():
            return False
        
        try:
            # Step 1: List all files to be downloaded to get a total count
            all_files = []
            items_to_scan = [remote_path]
            while items_to_scan:
                current_path = items_to_scan.pop()
                for item in self.sftp.listdir_attr(current_path):
                    item_full_path = f"{current_path.rstrip('/')}/{item.filename}"
                    if stat.S_ISDIR(item.st_mode):
                        items_to_scan.append(item_full_path)
                    else:
                        all_files.append(item_full_path)
            
            total_files = len(all_files)
            logging.info(f"[SFTP] Found {total_files} file(s) to download from {remote_path}.")
            
            # Step 2: Download files and update progress
            downloaded_count = 0
            for remote_file_path in all_files:
                # Construct local path
                relative_path = remote_file_path.replace(f"{remote_path.rstrip('/')}/", "", 1)
                local_file_path = Path(local_path) / relative_path

                # Ensure local directory exists
                local_file_path.parent.mkdir(parents=True, exist_ok=True)

                if not self.download_file(remote_file_path, str(local_file_path)):
                    logging.error(f"Stopping download due to failure on file: {remote_file_path}")
                    return False
                
                downloaded_count += 1
                if status_display and case_id:
                    progress = downloaded_count / total_files
                    transfer_info = f"Downloading: {downloaded_count}/{total_files}"
                    status_display.update_case_status(
                        case_id=case_id,
                        status="PROCESSING",
                        stage="Downloading Results",
                        progress=progress,
                        transfer_info=transfer_info
                    )
            
            logging.info(f"Successfully downloaded all files to {local_path}")
            if status_display and case_id:
                status_display.update_case_status(
                    case_id=case_id, 
                    status="PROCESSING", 
                    stage="Download Complete",
                    transfer_info="All files downloaded successfully"
                )
            return True
            
        except Exception as e:
            logging.error(f"Failed to download directory {remote_path}: {e}")
            if status_display and case_id:
                status_display.update_case_status(
                    case_id=case_id,
                    status="PROCESSING",
                    stage="Download Failed",
                    error_message=f"Download failed: {str(e)}",
                    transfer_info=""
                )
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
            logging.error(f"Error checking file existence {remote_path}: {e}")
            return False

    def get_file_size(self, remote_path: str) -> int:
        """Get remote file size in bytes."""
        if not self._ensure_connected():
            return 0
        
        try:
            stat_info = self.sftp.stat(remote_path)
            return stat_info.st_size
        except FileNotFoundError:
            return 0
        except Exception as e:
            logging.error(f"Error getting file size {remote_path}: {e}")
            return 0

    def list_directory(self, remote_path: str) -> list:
        """List contents of remote directory."""
        if not self._ensure_connected():
            return []
        
        try:
            return self.sftp.listdir(remote_path)
        except Exception as e:
            logging.error(f"Error listing directory {remote_path}: {e}")
            return []

    def remove_file(self, remote_path: str) -> bool:
        """Remove remote file."""
        if not self._ensure_connected():
            return False
        
        try:
            self.sftp.remove(remote_path)
            return True
        except Exception as e:
            logging.error(f"Error removing file {remote_path}: {e}")
            return False

    def remove_directory(self, remote_path: str) -> bool:
        """Remove remote directory recursively."""
        if not self._ensure_connected():
            return False
        
        try:
            # List directory contents
            for item in self.sftp.listdir_attr(remote_path):
                item_path = f"{remote_path.rstrip('/')}/{item.filename}"
                
                if stat.S_ISDIR(item.st_mode):
                    # Recursively remove subdirectory
                    if not self.remove_directory(item_path):
                        return False
                else:
                    # Remove file
                    if not self.remove_file(item_path):
                        return False
            
            # Remove empty directory
            self.sftp.rmdir(remote_path)
            return True
            
        except Exception as e:
            logging.error(f"Error removing directory {remote_path}: {e}")
            return False

    def sync_directory(self, local_path: str, remote_path: str, 
                      direction: str = "upload") -> bool:
        """Sync directory based on file timestamps."""
        if direction == "upload":
            return self.upload_directory(local_path, remote_path)
        elif direction == "download":
            return self.download_directory(remote_path, local_path)
        else:
            logging.error(f"Invalid sync direction: {direction}")
            return False

    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information."""
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "connected": self.connected,
            "transport_active": self.transport.is_active() if self.transport else False
        }

    def __enter__(self):
        """Context manager entry."""
        if not self.connect():
            raise ConnectionError("Failed to establish SFTP connection")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()

    def __del__(self):
        """Destructor - ensure connections are closed."""
        self.disconnect()