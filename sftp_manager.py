import paramiko
import stat
import time
import socket
from pathlib import Path
from typing import Optional, Callable, Dict, Any
import logging
from base_ssh_connector import BaseSSHConnector


class SFTPManager(BaseSSHConnector):
    def __init__(self, host: str, username: str, password: str, port: int = 22, timeout: int = 30):
        super().__init__(host, username, password, port, timeout)
        self.sftp: Optional[paramiko.SFTPClient] = None

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

    def upload_file(self, local_path: str, remote_path: str, 
                   progress_callback: Optional[Callable] = None) -> bool:
        """Upload single file with optional progress callback and network retry."""
        def _upload_file_internal():
            if not self._ensure_connected():
                raise ConnectionError("Failed to establish SFTP connection")
            
            local_file = Path(local_path)
            
            if not local_file.exists():
                raise FileNotFoundError(f"Local file does not exist: {local_path}")
            
            # Create remote directory if needed
            remote_dir = str(Path(remote_path).parent)
            if remote_dir != ".":
                self.create_remote_directory(remote_dir)
            
            # Upload file
            self.sftp.put(str(local_file), remote_path, callback=progress_callback)
            return True
        
        try:
            return self._retry_on_network_error(_upload_file_internal)
        except Exception as e:
            logging.error(f"Failed to upload file {local_path} after retries: {e}")
            return False

    def upload_directory(self, local_path: str, remote_path: str, 
                        progress_callback: Optional[Callable] = None) -> bool:
        """Upload entire directory recursively."""
        if not self._ensure_connected():
            return False
        
        try:
            local_dir = Path(local_path)
            
            if not local_dir.exists() or not local_dir.is_dir():
                logging.error(f"Local directory does not exist: {local_path}")
                return False
            
            # Create remote directory
            if not self.create_remote_directory(remote_path):
                return False
            
            # Upload all files
            for file_path in local_dir.rglob("*"):
                if file_path.is_file():
                    # Calculate relative path
                    relative_path = file_path.relative_to(local_dir)
                    remote_file_path = f"{remote_path.rstrip('/')}/{str(relative_path).replace(chr(92), '/')}"
                    
                    if not self.upload_file(str(file_path), remote_file_path, progress_callback):
                        return False
            
            return True
            
        except Exception as e:
            logging.error(f"Failed to upload directory {local_path}: {e}")
            return False

    def download_file(self, remote_path: str, local_path: str, 
                     progress_callback: Optional[Callable] = None) -> bool:
        """Download single file with optional progress callback and network retry."""
        def _download_file_internal():
            if not self._ensure_connected():
                raise ConnectionError("Failed to establish SFTP connection")
            
            # Create local directory if needed
            local_file = Path(local_path)
            local_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Download file
            self.sftp.get(remote_path, str(local_file), callback=progress_callback)
            return True
        
        try:
            return self._retry_on_network_error(_download_file_internal)
        except Exception as e:
            logging.error(f"Failed to download file {remote_path} after retries: {e}")
            return False

    def download_directory(self, remote_path: str, local_path: str, 
                          progress_callback: Optional[Callable] = None) -> bool:
        """Download entire directory recursively."""
        if not self._ensure_connected():
            return False
        
        try:
            # Create local directory
            local_dir = Path(local_path)
            local_dir.mkdir(parents=True, exist_ok=True)
            
            # List remote directory
            for item in self.sftp.listdir_attr(remote_path):
                remote_item_path = f"{remote_path.rstrip('/')}/{item.filename}"
                local_item_path = local_dir / item.filename
                
                if stat.S_ISDIR(item.st_mode):
                    # Recursively download subdirectory
                    if not self.download_directory(remote_item_path, str(local_item_path), progress_callback):
                        return False
                else:
                    # Download file
                    if not self.download_file(remote_item_path, str(local_item_path), progress_callback):
                        return False
            
            return True
            
        except Exception as e:
            logging.error(f"Failed to download directory {remote_path}: {e}")
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