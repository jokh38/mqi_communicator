import logging
import shutil
import time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any


class DirectoryManager:
    def __init__(self, local_base: str, remote_base: str, output_base: str, 
                 sftp_manager=None, remote_executor=None):
        self.local_base = Path(local_base)
        self.remote_base = remote_base
        self.output_base = Path(output_base)
        self.sftp_manager = sftp_manager
        self.remote_executor = remote_executor

    def ensure_local_directory(self, directory_path: str) -> bool:
        """Ensure local directory exists, create if necessary."""
        try:
            path = Path(directory_path)
            
            if path.exists():
                return True
            
            path.mkdir(parents=True, exist_ok=True)
            logging.info(f"Created local directory: {directory_path}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to create local directory {directory_path}: {e}")
            return False

    def ensure_remote_directory(self, directory_path: str) -> bool:
        """Ensure remote directory exists, create if necessary."""
        try:
            if not self.remote_executor:
                logging.error("Remote executor not available")
                return False
            
            if self.remote_executor.check_directory_exists(directory_path):
                return True
            
            if self.remote_executor.create_directory(directory_path):
                logging.info(f"Created remote directory: {directory_path}")
                return True
            else:
                logging.error(f"Failed to create remote directory: {directory_path}")
                return False
                
        except Exception as e:
            logging.error(f"Error ensuring remote directory {directory_path}: {e}")
            return False

    def create_case_workspace(self, case_id: str) -> bool:
        """Create workspace directories for a case (local and remote)."""
        try:
            # Create local directory
            local_path = self.get_case_local_path(case_id)
            if not self.ensure_local_directory(str(local_path)):
                return False
            
            # Create remote directory
            remote_path = self.get_case_remote_path(case_id)
            if not self.ensure_remote_directory(remote_path):
                return False
            
            logging.info(f"Created workspace for case: {case_id}")
            return True
            
        except Exception as e:
            logging.error(f"Error creating workspace for case {case_id}: {e}")
            return False

    def create_output_directory(self, case_id: str, date: Optional[datetime] = None) -> bool:
        """Create output directory with monthly structure."""
        try:
            if date is None:
                date = datetime.now()
            
            output_path = self.get_case_output_path(case_id, date)
            
            if self.ensure_local_directory(str(output_path)):
                logging.info(f"Created output directory for case: {case_id}")
                return True
            else:
                return False
                
        except Exception as e:
            logging.error(f"Error creating output directory for case {case_id}: {e}")
            return False

    def get_case_local_path(self, case_id: str) -> Path:
        """Get local path for a case."""
        return self.local_base / case_id

    def get_case_remote_path(self, case_id: str) -> str:
        """Get remote path for a case."""
        return f"{self.remote_base}/{case_id}"

    def get_case_output_path(self, case_id: str, date: Optional[datetime] = None) -> Path:
        """Get output path for a case with monthly structure."""
        if date is None:
            date = datetime.now()
        
        year = str(date.year)
        month = f"{date.month:02d}"
        
        return self.output_base / year / month / case_id

    def validate_case_directory(self, case_id: str) -> bool:
        """Validate that local case directory exists."""
        try:
            case_path = self.get_case_local_path(case_id)
            return case_path.exists() and case_path.is_dir()
            
        except Exception as e:
            logging.error(f"Error validating case directory {case_id}: {e}")
            return False

    def validate_remote_case_directory(self, case_id: str) -> bool:
        """Validate that remote case directory exists."""
        try:
            if not self.remote_executor:
                return False
            
            remote_path = self.get_case_remote_path(case_id)
            return self.remote_executor.check_directory_exists(remote_path)
            
        except Exception as e:
            logging.error(f"Error validating remote case directory {case_id}: {e}")
            return False

    def cleanup_old_directories(self, days: int = 30) -> int:
        """Clean up old directories based on age."""
        try:
            cutoff_time = time.time() - (days * 24 * 3600)
            cleaned_count = 0
            
            if not self.local_base.exists():
                return 0
            
            # Find old directories
            for directory in self.local_base.rglob("*"):
                if directory.is_dir():
                    try:
                        if directory.stat().st_mtime < cutoff_time:
                            shutil.rmtree(directory)
                            cleaned_count += 1
                            logging.info(f"Removed old directory: {directory}")
                    except Exception as e:
                        logging.warning(f"Failed to remove directory {directory}: {e}")
            
            return cleaned_count
            
        except Exception as e:
            logging.error(f"Error cleaning up old directories: {e}")
            return 0

    def get_directory_size(self, directory_path: str) -> int:
        """Get total size of directory in bytes."""
        try:
            path = Path(directory_path)
            
            if not path.exists():
                return 0
            
            total_size = 0
            for file_path in path.rglob("*"):
                if file_path.is_file():
                    try:
                        total_size += file_path.stat().st_size
                    except Exception:
                        continue
            
            return total_size
            
        except Exception as e:
            logging.error(f"Error getting directory size {directory_path}: {e}")
            return 0

    def get_remote_directory_size(self, directory_path: str) -> int:
        """Get total size of remote directory in KB."""
        try:
            if not self.remote_executor:
                return 0
            
            # Use du command to get directory size
            result = self.remote_executor.execute_command(f"du -sk {directory_path}")
            
            if result["exit_code"] == 0 and result["stdout"]:
                # Parse output (format: "size_in_kb    path")
                size_kb = int(result["stdout"].split()[0])
                return size_kb
            
            return 0
            
        except Exception as e:
            logging.error(f"Error getting remote directory size {directory_path}: {e}")
            return 0

    def list_case_directories(self) -> List[str]:
        """List all case directories in local base."""
        try:
            if not self.local_base.exists():
                return []
            
            case_ids = []
            for item in self.local_base.iterdir():
                if item.is_dir():
                    case_ids.append(item.name)
            
            return sorted(case_ids)
            
        except Exception as e:
            logging.error(f"Error listing case directories: {e}")
            return []

    def list_remote_case_directories(self) -> List[str]:
        """List all case directories in remote base."""
        try:
            if not self.remote_executor:
                return []
            
            # List contents of remote base directory
            result = self.remote_executor.execute_command(f"ls -1 {self.remote_base}")
            
            if result["exit_code"] != 0:
                return []
            
            case_ids = []
            for line in result["stdout"].strip().split('\n'):
                if line.strip():
                    item_path = f"{self.remote_base}/{line.strip()}"
                    if self.remote_executor.check_directory_exists(item_path):
                        case_ids.append(line.strip())
            
            return sorted(case_ids)
            
        except Exception as e:
            logging.error(f"Error listing remote case directories: {e}")
            return []

    def get_directory_info(self, case_id: str) -> Dict[str, Any]:
        """Get comprehensive information about a case directory."""
        try:
            case_path = self.get_case_local_path(case_id)
            
            info = {
                "case_id": case_id,
                "local_path": str(case_path),
                "remote_path": self.get_case_remote_path(case_id),
                "exists": False,
                "size_bytes": 0,
                "file_count": 0,
                "last_modified": None,
                "created": None
            }
            
            if self.validate_case_directory(case_id):
                info["exists"] = True
                info["size_bytes"] = self.get_directory_size(str(case_path))
                
                # Get file count
                file_count = 0
                for item in case_path.rglob("*"):
                    if item.is_file():
                        file_count += 1
                info["file_count"] = file_count
                
                # Get timestamps
                try:
                    stat = case_path.stat()
                    info["last_modified"] = datetime.fromtimestamp(stat.st_mtime)
                    info["created"] = datetime.fromtimestamp(stat.st_ctime)
                except Exception:
                    pass
            
            return info
            
        except Exception as e:
            logging.error(f"Error getting directory info for {case_id}: {e}")
            return {"case_id": case_id, "exists": False, "size_bytes": 0}

    def sync_directories(self, case_id: str, direction: str = "upload", status_display=None) -> bool:
        """Sync directories between local and remote."""
        try:
            if not self.sftp_manager:
                logging.error("SFTP manager not available")
                return False
            
            if direction == "upload":
                # Upload from local to remote
                if not self.create_case_workspace(case_id):
                    return False
                
                local_path = str(self.get_case_local_path(case_id))
                remote_path = self.get_case_remote_path(case_id)
                
                return self.sftp_manager.upload_directory(local_path, remote_path, status_display, case_id)
                
            elif direction == "download":
                # Download from remote to output
                if not self.create_output_directory(case_id):
                    return False
                
                remote_path = self.get_case_remote_path(case_id)
                local_path = str(self.get_case_output_path(case_id))
                
                return self.sftp_manager.download_directory(remote_path, local_path, status_display, case_id)
                
            else:
                logging.error(f"Invalid sync direction: {direction}")
                return False
                
        except Exception as e:
            logging.error(f"Error syncing directories for case {case_id}: {e}")
            return False

    def backup_case_directory(self, case_id: str, backup_location: str) -> bool:
        """Create backup of case directory."""
        try:
            case_path = self.get_case_local_path(case_id)
            
            if not self.validate_case_directory(case_id):
                logging.error(f"Case directory not found: {case_id}")
                return False
            
            backup_path = Path(backup_location) / f"{case_id}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # Create backup
            shutil.copytree(case_path, backup_path)
            
            logging.info(f"Created backup for case {case_id} at {backup_path}")
            return True
            
        except Exception as e:
            logging.error(f"Error creating backup for case {case_id}: {e}")
            return False

    def restore_case_directory(self, case_id: str, backup_path: str) -> bool:
        """Restore case directory from backup."""
        try:
            source_path = Path(backup_path)
            target_path = self.get_case_local_path(case_id)
            
            if not source_path.exists():
                logging.error(f"Backup not found: {backup_path}")
                return False
            
            # Remove existing directory if it exists
            if target_path.exists():
                shutil.rmtree(target_path)
            
            # Restore from backup
            shutil.copytree(source_path, target_path)
            
            logging.info(f"Restored case {case_id} from backup {backup_path}")
            return True
            
        except Exception as e:
            logging.error(f"Error restoring case {case_id} from backup: {e}")
            return False

    def get_disk_usage(self) -> Dict[str, Any]:
        """Get disk usage information for managed directories."""
        try:
            usage_info = {}
            
            # Local base directory
            if self.local_base.exists():
                usage_info["local_base"] = {
                    "path": str(self.local_base),
                    "size_bytes": self.get_directory_size(str(self.local_base)),
                    "exists": True
                }
            else:
                usage_info["local_base"] = {"exists": False}
            
            # Output directory
            if self.output_base.exists():
                usage_info["output_base"] = {
                    "path": str(self.output_base),
                    "size_bytes": self.get_directory_size(str(self.output_base)),
                    "exists": True
                }
            else:
                usage_info["output_base"] = {"exists": False}
            
            # Remote base directory
            if self.remote_executor:
                remote_size = self.get_remote_directory_size(self.remote_base)
                usage_info["remote_base"] = {
                    "path": self.remote_base,
                    "size_kb": remote_size,
                    "exists": remote_size > 0
                }
            
            return usage_info
            
        except Exception as e:
            logging.error(f"Error getting disk usage: {e}")
            return {}

    def verify_directory_integrity(self, case_id: str) -> Dict[str, Any]:
        """Verify integrity of case directory."""
        try:
            case_path = self.get_case_local_path(case_id)
            
            integrity_info = {
                "case_id": case_id,
                "valid": False,
                "errors": [],
                "file_count": 0,
                "total_size": 0
            }
            
            if not self.validate_case_directory(case_id):
                integrity_info["errors"].append("Directory does not exist")
                return integrity_info
            
            # Check directory accessibility
            try:
                file_count = 0
                total_size = 0
                
                for item in case_path.rglob("*"):
                    if item.is_file():
                        try:
                            size = item.stat().st_size
                            total_size += size
                            file_count += 1
                        except Exception as e:
                            integrity_info["errors"].append(f"Cannot access file {item}: {e}")
                
                integrity_info["file_count"] = file_count
                integrity_info["total_size"] = total_size
                
                if file_count > 0:
                    integrity_info["valid"] = len(integrity_info["errors"]) == 0
                else:
                    integrity_info["errors"].append("No files found in directory")
                
            except Exception as e:
                integrity_info["errors"].append(f"Cannot scan directory: {e}")
            
            return integrity_info
            
        except Exception as e:
            logging.error(f"Error verifying directory integrity for {case_id}: {e}")
            return {"case_id": case_id, "valid": False, "errors": [str(e)]}

    def get_directory_tree(self, case_id: str, max_depth: int = 3) -> Dict[str, Any]:
        """Get directory tree structure for a case."""
        try:
            case_path = self.get_case_local_path(case_id)
            
            if not self.validate_case_directory(case_id):
                return {"case_id": case_id, "tree": {}, "exists": False}
            
            def build_tree(path: Path, current_depth: int = 0) -> Dict[str, Any]:
                if current_depth > max_depth:
                    return {"...": "max_depth_reached"}
                
                tree = {}
                try:
                    for item in path.iterdir():
                        if item.is_dir():
                            tree[item.name] = build_tree(item, current_depth + 1)
                        else:
                            tree[item.name] = {"type": "file", "size": item.stat().st_size}
                except Exception:
                    tree["error"] = "cannot_access"
                
                return tree
            
            return {
                "case_id": case_id,
                "tree": build_tree(case_path),
                "exists": True
            }
            
        except Exception as e:
            logging.error(f"Error getting directory tree for {case_id}: {e}")
            return {"case_id": case_id, "tree": {}, "exists": False, "error": str(e)}