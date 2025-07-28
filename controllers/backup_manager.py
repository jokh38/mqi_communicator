"""
Backup Manager Controller - Manages periodic data backups and cleanup of old archives.

This class handles monthly backup operations and cleanup of old backup files.
"""

import shutil
import zipfile
from pathlib import Path
from datetime import datetime, timedelta


class BackupManager:
    """Manages periodic data backups and cleanup of old archives."""
    
    def __init__(self, logger, config, backup_months_to_keep):
        """Initialize backup manager with dependencies."""
        self.logger = logger
        self.config = config
        self.backup_months_to_keep = backup_months_to_keep

    def run_backup_cycle(self) -> None:
        """Check if monthly backup is needed and perform it."""
        try:
            backup_info_file = Path("last_backup.txt")
            current_month = datetime.now().strftime("%Y-%m")
            
            # Check last backup month
            last_backup_month = None
            if backup_info_file.exists():
                try:
                    with open(backup_info_file, 'r', encoding='utf-8') as f:
                        last_backup_month = f.read().strip()
                except Exception as e:
                    self.logger.warning(f"Failed to read last backup info: {e}")
            
            # Perform backup if needed
            if last_backup_month != current_month:
                self.logger.info(f"Starting monthly backup for {current_month}")
                success = self._perform_monthly_backup()
                
                if success:
                    # Update last backup month
                    with open(backup_info_file, 'w', encoding='utf-8') as f:
                        f.write(current_month)
                    self.logger.info(f"Monthly backup completed successfully for {current_month}")
                else:
                    self.logger.error(f"Monthly backup failed for {current_month}")
            
        except Exception as e:
            self.logger.error(f"Error during monthly backup check: {e}")

    def _perform_monthly_backup(self) -> bool:
        """Perform monthly backup of case status and logs."""
        try:
            backup_context = self._create_backup_context()
            success = True
            
            success &= self._backup_case_status(backup_context)
            success &= self._backup_logs_directory(backup_context)
            self._backup_configuration_files(backup_context)  # Non-critical
            self._cleanup_old_backups(backup_context.backup_dir, self.backup_months_to_keep)
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error during monthly backup: {e}")
            return False
    
    def _create_backup_context(self):
        """Create backup context with necessary paths and current month."""
        from collections import namedtuple
        
        BackupContext = namedtuple('BackupContext', ['backup_dir', 'current_month'])
        
        backup_dir = Path("backups")
        backup_dir.mkdir(exist_ok=True)
        current_month = datetime.now().strftime("%Y-%m")
        
        return BackupContext(backup_dir, current_month)
    
    def _backup_case_status(self, context) -> bool:
        """Backup case_status.json file."""
        try:
            case_status_file = Path("case_status.json")
            if case_status_file.exists():
                backup_status_file = context.backup_dir / f"case_status_{context.current_month}.json"
                shutil.copy2(case_status_file, backup_status_file)
                self.logger.info(f"Backed up case_status.json to {backup_status_file}")
            else:
                self.logger.warning("case_status.json not found for backup")
            return True
        except Exception as e:
            self.logger.error(f"Failed to backup case_status.json: {e}")
            return False
    
    def _backup_logs_directory(self, context) -> bool:
        """Backup logs directory as zip file."""
        try:
            logs_dir = Path("logs")
            if logs_dir.exists() and logs_dir.is_dir():
                backup_logs_file = context.backup_dir / f"logs_{context.current_month}.zip"
                
                with zipfile.ZipFile(backup_logs_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for log_file in logs_dir.rglob("*"):
                        if log_file.is_file():
                            arcname = log_file.relative_to(logs_dir)
                            zipf.write(log_file, arcname)
                
                self.logger.info(f"Backed up logs directory to {backup_logs_file}")
            else:
                self.logger.warning("logs directory not found for backup")
            return True
        except Exception as e:
            self.logger.error(f"Failed to backup logs directory: {e}")
            return False
    
    def _backup_configuration_files(self, context) -> None:
        """Backup configuration files (non-critical operation)."""
        try:
            config_files = ["config.json"]
            for config_file in config_files:
                config_path = Path(config_file)
                if config_path.exists():
                    backup_config_file = context.backup_dir / f"{config_path.stem}_{context.current_month}{config_path.suffix}"
                    shutil.copy2(config_path, backup_config_file)
                    self.logger.debug(f"Backed up {config_file}")
        except Exception as e:
            self.logger.error(f"Failed to backup configuration files: {e}")

    def _cleanup_old_backups(self, backup_dir: Path, months_to_keep: int = 12) -> None:
        """Clean up old backup files, keeping only the specified number of months."""
        try:
            if not backup_dir.exists():
                return
            
            cutoff_month = self._calculate_cutoff_month(months_to_keep)
            old_files = self._find_old_backup_files(backup_dir, cutoff_month)
            self._remove_old_files(old_files)
            
        except Exception as e:
            self.logger.error(f"Error during backup cleanup: {e}")
    
    def _calculate_cutoff_month(self, months_to_keep: int) -> str:
        """Calculate the cutoff month for backup cleanup."""
        cutoff_date = datetime.now() - timedelta(days=months_to_keep * 30)
        return cutoff_date.strftime("%Y-%m")
    
    def _find_old_backup_files(self, backup_dir: Path, cutoff_month: str) -> list:
        """Find backup files older than the cutoff month."""
        old_files = []
        for backup_file in backup_dir.iterdir():
            if not backup_file.is_file():
                continue
                
            file_month = self._extract_month_from_filename(backup_file)
            if file_month and file_month < cutoff_month:
                old_files.append(backup_file)
        return old_files
    
    def _extract_month_from_filename(self, backup_file: Path) -> str:
        """Extract month string from backup filename."""
        try:
            parts = backup_file.stem.split('_')
            if len(parts) >= 2:
                file_month = parts[-1]  # Last part should be YYYY-MM
                if len(file_month) == 7:
                    return file_month
        except Exception:
            pass
        return None
    
    def _remove_old_files(self, old_files: list) -> None:
        """Remove old backup files and log results."""
        for old_file in old_files:
            try:
                old_file.unlink()
                self.logger.info(f"Removed old backup file: {old_file}")
            except Exception as e:
                self.logger.warning(f"Failed to remove old backup file {old_file}: {e}")
        
        if old_files:
            self.logger.info(f"Cleaned up {len(old_files)} old backup files")