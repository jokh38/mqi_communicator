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
            backup_dir = Path("backups")
            backup_dir.mkdir(exist_ok=True)
            
            current_month = datetime.now().strftime("%Y-%m")
            success = True
            
            # Backup case_status.json
            try:
                case_status_file = Path("case_status.json")
                if case_status_file.exists():
                    backup_status_file = backup_dir / f"case_status_{current_month}.json"
                    shutil.copy2(case_status_file, backup_status_file)
                    self.logger.info(f"Backed up case_status.json to {backup_status_file}")
                else:
                    self.logger.warning("case_status.json not found for backup")
            except Exception as e:
                self.logger.error(f"Failed to backup case_status.json: {e}")
                success = False
            
            # Backup logs directory
            try:
                logs_dir = Path("logs")
                if logs_dir.exists() and logs_dir.is_dir():
                    backup_logs_file = backup_dir / f"logs_{current_month}.zip"
                    
                    with zipfile.ZipFile(backup_logs_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                        for log_file in logs_dir.rglob("*"):
                            if log_file.is_file():
                                # Add file to zip with relative path
                                arcname = log_file.relative_to(logs_dir)
                                zipf.write(log_file, arcname)
                    
                    self.logger.info(f"Backed up logs directory to {backup_logs_file}")
                else:
                    self.logger.warning("logs directory not found for backup")
            except Exception as e:
                self.logger.error(f"Failed to backup logs directory: {e}")
                success = False
            
            # Backup configuration files
            try:
                config_files = ["config.json"]
                for config_file in config_files:
                    config_path = Path(config_file)
                    if config_path.exists():
                        backup_config_file = backup_dir / f"{config_path.stem}_{current_month}{config_path.suffix}"
                        shutil.copy2(config_path, backup_config_file)
                        self.logger.debug(f"Backed up {config_file}")
            except Exception as e:
                self.logger.error(f"Failed to backup configuration files: {e}")
                # Don't fail the entire backup for config files
            
            # Clean up old backups (keep last configured months)
            try:
                self._cleanup_old_backups(backup_dir, months_to_keep=self.backup_months_to_keep)
            except Exception as e:
                self.logger.warning(f"Failed to cleanup old backups: {e}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error during monthly backup: {e}")
            return False

    def _cleanup_old_backups(self, backup_dir: Path, months_to_keep: int = 12) -> None:
        """Clean up old backup files, keeping only the specified number of months."""
        try:
            if not backup_dir.exists():
                return
            
            # Calculate cutoff date
            cutoff_date = datetime.now() - timedelta(days=months_to_keep * 30)
            cutoff_month = cutoff_date.strftime("%Y-%m")
            
            # Find old backup files
            old_files = []
            for backup_file in backup_dir.iterdir():
                if backup_file.is_file():
                    # Extract month from filename (format: name_YYYY-MM.ext)
                    try:
                        parts = backup_file.stem.split('_')
                        if len(parts) >= 2:
                            file_month = parts[-1]  # Last part should be YYYY-MM
                            if len(file_month) == 7 and file_month < cutoff_month:
                                old_files.append(backup_file)
                    except Exception:
                        continue
            
            # Remove old files
            for old_file in old_files:
                try:
                    old_file.unlink()
                    self.logger.info(f"Removed old backup file: {old_file}")
                except Exception as e:
                    self.logger.warning(f"Failed to remove old backup file {old_file}: {e}")
            
            if old_files:
                self.logger.info(f"Cleaned up {len(old_files)} old backup files")
            
        except Exception as e:
            self.logger.error(f"Error during backup cleanup: {e}")