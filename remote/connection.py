"""
ConnectionManager - SSH connection management.

Merges functionality from ssh_connection_manager.py and base_ssh_connector.py
to provide unified SSH connection management.
"""

import paramiko
import socket
import time
import threading
from typing import Optional, Dict, ClassVar, Any

from core.config import ConfigManager
from core.logging import Logger


class ConnectionManager:
    """Centralized SSH connection manager with unified transport and client management."""
    
    # Class-level connection rate limiter
    _connection_locks: ClassVar[Dict[str, threading.Lock]] = {}
    _last_connection_times: ClassVar[Dict[str, float]] = {}
    _global_lock: ClassVar[threading.Lock] = threading.Lock()
    
    def __init__(self, config_manager: ConfigManager, logger: Optional[Logger] = None):
        """
        Initialize SSH connection manager.
        
        Args:
            config_manager: ConfigManager instance for connection details
            logger: Logger instance for logging
        """
        self.config_manager = config_manager
        self.logger = logger
        
        # Extract connection details from config
        credentials = config_manager.get_credentials()
        self.host = config_manager.get_linux_gpu_ip()
        self.username = credentials["username"]
        self.password = credentials["password"]
        
        config = config_manager.get_config()
        self.port = config.get("servers", {}).get("ssh_port", 22)
        self.timeout = config.get("servers", {}).get("ssh_timeout", 30)
        
        # Connection state
        self.transport: Optional[paramiko.Transport] = None
        self.connected = False
        
        # Retry configuration
        self.max_retries = 10
        self.retry_delay = 2
        self.min_connection_interval = 1.0
        self.network_error_types = (
            socket.error,
            paramiko.SSHException,
            paramiko.AuthenticationException,
            paramiko.ChannelException,
            OSError,
            TimeoutError
        )
    
    def _is_network_error(self, exception: Exception) -> bool:
        """Check if the exception is a network-related error."""
        return isinstance(exception, self.network_error_types)
    
    def _enforce_connection_rate_limit(self):
        """Enforce rate limiting to prevent server overload."""
        host_key = f"{self.host}:{self.port}"
        
        with self._global_lock:
            # Get or create lock for this host
            if host_key not in self._connection_locks:
                self._connection_locks[host_key] = threading.Lock()
            
            host_lock = self._connection_locks[host_key]
        
        with host_lock:
            current_time = time.time()
            last_connection_time = self._last_connection_times.get(host_key, 0)
            
            time_since_last = current_time - last_connection_time
            if time_since_last < self.min_connection_interval:
                sleep_time = self.min_connection_interval - time_since_last
                if self.logger:
                    self.logger.debug(f"Rate limiting: waiting {sleep_time:.2f}s before connecting to {host_key}")
                time.sleep(sleep_time)
            
            # Update last connection time
            self._last_connection_times[host_key] = time.time()
    
    def connect(self) -> bool:
        """Establish SSH connection with improved timeout and retry logic."""
        max_attempts = 3
        
        for attempt in range(max_attempts):
            try:
                if self._attempt_connection(attempt, max_attempts):
                    return True
            except Exception as e:
                if not self._handle_connection_error(e, attempt, max_attempts):
                    break
        
        self.connected = False
        return False
    
    def _attempt_connection(self, attempt: int, max_attempts: int) -> bool:
        """Attempt a single SSH connection."""
        self._prepare_connection_attempt(attempt, max_attempts)
        
        sock, transport = self._create_transport()
        
        try:
            transport.connect(username=self.username, password=self.password)
            self._finalize_successful_connection(transport, attempt)
            return True
        except Exception:
            self._cleanup_failed_connection(sock, transport)
            raise
    
    def _prepare_connection_attempt(self, attempt: int, max_attempts: int) -> None:
        """Prepare for a connection attempt."""
        self._cleanup_existing_transport()
        
        if attempt > 0:
            self._wait_before_retry(attempt, max_attempts)
        
        self._enforce_connection_rate_limit()
    
    def _cleanup_existing_transport(self) -> None:
        """Clean up any existing transport."""
        if self.transport:
            try:
                self.transport.close()
            except (OSError, paramiko.SSHException):
                pass
            finally:
                self.transport = None
    
    def _wait_before_retry(self, attempt: int, max_attempts: int) -> None:
        """Wait before retrying connection."""
        delay = self.retry_delay
        if self.logger:
            self.logger.info(f"Retrying SSH connection in {delay} seconds (attempt {attempt + 1}/{max_attempts})")
        time.sleep(delay)
    
    def _create_transport(self) -> tuple:
        """Create socket and transport objects."""
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        transport = paramiko.Transport(sock)
        return sock, transport
    
    def _finalize_successful_connection(self, transport, attempt: int) -> None:
        """Finalize a successful connection."""
        self.transport = transport
        self.transport.set_keepalive(60)
        self.connected = True
        if self.logger:
            self.logger.info(f"Successfully connected to {self.host}:{self.port} on attempt {attempt + 1}")
    
    def _cleanup_failed_connection(self, sock, transport) -> None:
        """Clean up resources after failed connection."""
        if transport and transport != self.transport:
            try:
                transport.close()
            except (OSError, paramiko.SSHException):
                pass
        if sock:
            try:
                sock.close()
            except (OSError, socket.error):
                pass
    
    def _handle_connection_error(self, error: Exception, attempt: int, max_attempts: int) -> bool:
        """Handle connection errors and determine if retry is appropriate."""
        if isinstance(error, paramiko.SSHException):
            return self._handle_ssh_exception(error, attempt, max_attempts)
        
        if self.logger:
            self.logger.error(f"SSH connection failed on attempt {attempt + 1}/{max_attempts}: {error}")
        return attempt < max_attempts - 1
    
    def _handle_ssh_exception(self, error: paramiko.SSHException, attempt: int, max_attempts: int) -> bool:
        """Handle SSH-specific exceptions."""
        error_msg = str(error)
        
        if self._is_retryable_error(error_msg):
            return self._handle_retryable_error(error_msg, attempt, max_attempts)
        
        if self.logger:
            self.logger.error(f"SSH connection failed with non-retryable error: {error_msg}")
        return False
    
    def _is_retryable_error(self, error_msg: str) -> bool:
        """Check if an error is retryable."""
        retryable_errors = [
            "Invalid packet blocking", "Error reading SSH protocol banner", 
            "utf-8", "UnicodeDecodeError", "WinError 10038",
            "소켓 이외의 개체에 작업을 시도했습니다", "Bad file descriptor",
            "Socket is not valid", "EOFError", "Connection lost",
            "Connection reset", "Connection refused", "Timeout"
        ]
        return any(keyword in error_msg for keyword in retryable_errors)
    
    def _handle_retryable_error(self, error_msg: str, attempt: int, max_attempts: int) -> bool:
        """Handle retryable errors."""
        if self.logger:
            self.logger.warning(f"SSH connection error on attempt {attempt + 1}/{max_attempts}: {error_msg}")
        
        if attempt < max_attempts - 1:
            if self.logger:
                self.logger.info(f"Retrying SSH connection in {self.retry_delay} seconds (attempt {attempt + 2}/{max_attempts})")
            time.sleep(self.retry_delay)
            return True
        
        if self.logger:
            self.logger.error(f"SSH connection failed after {max_attempts} attempts due to persistent errors")
        return False
    
    def disconnect(self) -> None:
        """Close SSH connection and perform cleanup."""
        if self.logger:
            self.logger.info(f"Disconnecting from {self.host}:{self.port}...")
        try:
            if self.transport:
                try:
                    if self.transport.is_active():
                        self.transport.close()
                        if self.logger:
                            self.logger.info("Transport closed successfully.")
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Error while closing transport: {e}")
                finally:
                    self.transport.sock = None
                    self.transport = None
            
            self.connected = False
            if self.logger:
                self.logger.info(f"Disconnection from {self.host}:{self.port} completed.")
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error during disconnect: {e}")
        finally:
            self.transport = None
            self.connected = False
    
    def _ensure_connected(self) -> bool:
        """Ensure connection is active, reconnect if necessary."""
        if not self.connected or not self.transport or not self.transport.is_active():
            return self.connect()
        return True
    
    def get_ssh_client(self) -> Optional[paramiko.SSHClient]:
        """Get SSH client using the managed transport."""
        if not self._ensure_connected():
            return None
        
        ssh_client = None
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client._transport = self.transport
            return ssh_client
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to create SSH client: {e}")
            # Clean up partially created client
            if ssh_client:
                try:
                    ssh_client.close()
                except (OSError, paramiko.SSHException):
                    pass
            return None
    
    def get_sftp_client(self) -> Optional[paramiko.SFTPClient]:
        """Get SFTP client using the managed transport."""
        if not self._ensure_connected():
            return None
        
        sftp_client = None
        try:
            sftp_client = paramiko.SFTPClient.from_transport(self.transport)
            return sftp_client
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to create SFTP client: {e}")
            # Clean up partially created client
            if sftp_client:
                try:
                    sftp_client.close()
                except (OSError, paramiko.SSHException):
                    pass
            return None
    
    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information."""
        return {
            'host': self.host,
            'port': self.port,
            'username': self.username,
            'connected': self.connected,
            'transport_active': self.transport.is_active() if self.transport else False
        }
    
    def test_connection(self) -> bool:
        """Test the connection by attempting to connect and check transport."""
        try:
            if not self.connected:
                if not self.connect():
                    return False
            
            # Test if transport is active
            if self.transport and self.transport.is_active():
                return True
            else:
                return False
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Connection test failed: {e}")
            return False
    
    def is_connected(self) -> bool:
        """Check if connection is active."""
        return (self.connected and 
                self.transport is not None and 
                self.transport.is_active())

    def reconnect(self) -> bool:
        """Disconnect and then reconnect."""
        if self.logger:
            self.logger.info("Reconnecting SSH connection...")
        self.disconnect()
        return self.connect()
    
    def get_host(self) -> str:
        """Get the host address."""
        return self.host
    
    def get_port(self) -> int:
        """Get the port number."""
        return self.port
    
    def get_username(self) -> str:
        """Get the username."""
        return self.username
    
    def __enter__(self):
        """Context manager entry."""
        if not self.connect():
            raise ConnectionError(f"Failed to connect to {self.host}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
    
    def __del__(self):
        """Destructor to ensure connection is closed."""
        try:
            self.disconnect()
        except Exception:
            pass