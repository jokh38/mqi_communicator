"""Recovery Manager for MOQUI automation system.

Handles startup recovery checks and periodic backup operations.
"""

import shutil
import zipfile
from pathlib import Path
from datetime import datetime, timedelta


class RecoveryManager:
    """Manages startup recovery checks and periodic backup operations."""
    
    def __init__(self, case_service, resource_manager, remote_executor, 
                 state_manager, logger, backup_months_to_keep):
        """Initialize recovery manager with dependencies."""
        self.case_service = case_service
        self.resource_manager = resource_manager
        self.remote_executor = remote_executor
        self.state_manager = state_manager
        self.logger = logger
        self.backup_months_to_keep = backup_months_to_keep

    def recovery_startup_checks(self) -> None:
        """Perform recovery checks on startup."""
        try:
            self.logger.info("Performing startup recovery checks")
            
            # Recover ALL cases left in PROCESSING state (regardless of duration)
            recovery_result = self.case_service.recover_stale_jobs(max_processing_hours=0)
            
            if recovery_result["recovered_count"] > 0:
                self.logger.warning(f"Recovered {recovery_result['recovered_count']} stale cases: {recovery_result['recovered_cases']}")
                
                # Clean up resources for recovered cases
                for case_id in recovery_result['recovered_cases']:
                    self._cleanup_stale_case_resources(case_id)
            else:
                self.logger.info("No stale cases found during startup")
            
            # Clean up any zombie GPU processes and stale locks
            if self.resource_manager:
                zombie_processes = self.resource_manager.cleanup_zombie_processes()
                if zombie_processes:
                    self.logger.warning(f"Cleaned up {len(zombie_processes)} zombie GPU processes")
                else:
                    self.logger.info("No zombie GPU processes found")
                
                # Clean up stale GPU locks
                cleaned_locks = self.resource_manager.cleanup_stale_locks()
                if cleaned_locks:
                    self.logger.warning(f"Cleaned up stale locks for GPUs: {cleaned_locks}")
                else:
                    self.logger.info("No stale GPU locks found")
            
        except Exception as e:
            self.logger.error(f"Error during startup recovery checks: {e}")

    def _cleanup_stale_case_resources(self, case_id: str) -> None:
        """Clean up resources for a stale case."""
        try:
            case_status = self.state_manager.get_case_status(case_id)
            if not case_status:
                return
            
            # Clean up remote processes
            remote_pid = case_status.get('remote_pid')
            if remote_pid and self.remote_executor:
                try:
                    result = self.remote_executor.execute_command(f"kill {remote_pid}")
                    if result['exit_code'] == 0:
                        self.logger.info(f"Killed remote process {remote_pid} for stale case {case_id}")
                    else:
                        self.logger.warning(f"Failed to kill remote process {remote_pid} for case {case_id}: {result['stderr']}")
                except Exception as e:
                    self.logger.warning(f"Error killing remote process {remote_pid} for case {case_id}: {e}")
            
            # Release GPU locks through resource manager
            locked_gpus = case_status.get('locked_gpus', [])
            if locked_gpus and self.resource_manager:
                for gpu_id in locked_gpus:
                    try:
                        self.resource_manager.release_gpu_resource(gpu_id)
                        self.logger.info(f"Released GPU lock for GPU {gpu_id} (case {case_id})")
                    except Exception as e:
                        self.logger.warning(f"Error releasing GPU lock for GPU {gpu_id} (case {case_id}): {e}")
            
        except Exception as e:
            self.logger.error(f"Error cleaning up resources for stale case {case_id}: {e}")

    def check_and_perform_monthly_backup(self) -> None:
        """Check if monthly backup is needed and perform it."""
        try:
            backup_info_file = Path("last_backup.txt")
            current_month = datetime.now().strftime("%Y-%m")
            
            # Check last backup month
            last_backup_month = None
            if backup_info_file.exists():
                try:
                    with open(backup_info_file, 'r') as f:
                        last_backup_month = f.read().strip()
                except Exception as e:
                    self.logger.warning(f"Failed to read last backup info: {e}")
            
            # Perform backup if needed
            if last_backup_month != current_month:
                self.logger.info(f"Starting monthly backup for {current_month}")
                success = self._perform_monthly_backup()
                
                if success:
                    # Update last backup month
                    with open(backup_info_file, 'w') as f:
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