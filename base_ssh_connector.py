import paramiko
import socket
import time
import logging
import threading
from typing import Optional, Any, Dict, ClassVar
from abc import ABC, abstractmethod


class BaseSSHConnector(ABC):
    """Base class for SSH/SFTP connections with common functionality."""
    
    # Class-level connection rate limiter
    _connection_locks: ClassVar[Dict[str, threading.Lock]] = {}
    _last_connection_times: ClassVar[Dict[str, float]] = {}
    _global_lock: ClassVar[threading.Lock] = threading.Lock()
    
    def __init__(self, host: str, username: str, password: str, port: int = 22, timeout: int = 30):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.transport: Optional[paramiko.Transport] = None
        self.connected = False
        
        # Gentle retry configuration
        self.max_retries = 10  # Max retries set to 10
        self.retry_delay = 2   # Initial delay reduced to 2 seconds
        self.min_connection_interval = 1.0 # Reduced to 1 second for faster retries in a controlled test
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
                logging.debug(f"Rate limiting: waiting {sleep_time:.2f}s before connecting to {host_key}")
                time.sleep(sleep_time)
            
            # Update last connection time
            self._last_connection_times[host_key] = time.time()
    
    def _retry_on_network_error(self, func, *args, **kwargs):
        """Retry function execution on network errors with exponential backoff."""
        last_exception = None
        
        for attempt in range(self.max_retries + 1):  # +1 for initial attempt
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                # Explicitly check for FileNotFoundError and do not retry
                if isinstance(e, FileNotFoundError):
                    logging.error(f"Local file not found: {e}. This is not a network error and will not be retried.")
                    raise e

                if not self._is_network_error(e):
                    # Not a network error, don't retry
                    raise e
                
                if attempt < self.max_retries:
                    # Use a fixed delay instead of exponential backoff
                    delay = self.retry_delay
                    logging.warning(f"Network error on attempt {attempt + 1}/{self.max_retries + 1}: {e}. "
                                  f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                    
                    # Try to reconnect if connection was lost
                    if not self.connected:
                        self.connect()
                else:
                    logging.error(f"Failed after {self.max_retries + 1} attempts. Last error: {e}")
        
        # If we get here, all retries failed
        raise last_exception
    
    def _create_transport(self):
        """Create and configure a Paramiko transport object with socket validation."""
        try:
            # Create transport with socket validation
            transport = paramiko.Transport((self.host, self.port))
            
            # Validate socket is properly created
            if not hasattr(transport, 'sock') or not transport.sock:
                raise paramiko.SSHException("Transport socket was not created properly")
            
            # Check socket file descriptor
            try:
                fd = transport.sock.fileno()
                if fd == -1:
                    raise paramiko.SSHException("Transport socket has invalid file descriptor")
            except (OSError, AttributeError) as e:
                raise paramiko.SSHException(f"Transport socket validation failed: {e}")
            
            # Very gentle timeouts to give server maximum time
            transport.banner_timeout = 120  # 2 minutes - very patient
            transport.auth_timeout = 120    # 2 minutes for auth
            transport.handshake_timeout = 120  # 2 minutes for handshake
            
            # Gentle keepalive settings
            transport.set_keepalive(60)  # Even less frequent keepalives
            
            # Conservative transport configuration
            transport.use_compression(False)  # Disable compression for stability
            transport.window_size = 65536      # Smaller window size
            transport.max_packet_size = 32768  # Smaller packet size
            
            return transport
        except paramiko.SSHException as e:
            # This helps catch issues even before the banner is read
            raise paramiko.SSHException(f"Failed to create transport: {e}")
        except Exception as e:
            raise paramiko.SSHException(f"Unexpected error creating transport: {e}")

    def connect(self) -> bool:
        """Establish SSH connection with improved timeout and retry logic."""
        max_attempts = 3
        
        for attempt in range(max_attempts):
            try:
                # Clean up any existing transport
                if self.transport:
                    try:
                        self.transport.close()
                    except:
                        pass
                    self.transport = None
                
                # Add a small fixed delay between attempts
                if attempt > 0:
                    delay = self.retry_delay
                    logging.info(f"Retrying SSH connection in {delay} seconds (attempt {attempt + 1}/{max_attempts})")
                    time.sleep(delay)
                
                self._enforce_connection_rate_limit()
                
                # Create a new transport and connect
                sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                self.transport = paramiko.Transport(sock)
                
                self.transport.connect(username=self.username, password=self.password)
                
                self.connected = True
                logging.info(f"Successfully connected to {self.host}:{self.port} on attempt {attempt + 1}")
                self._post_connect_setup()
                
                return True
                
            except paramiko.SSHException as e:
                error_msg = str(e)
                
                # Log the raw banner if it's a banner error
                if "banner" in error_msg.lower():
                    try:
                        # Attempt to read raw data from the socket for logging
                        raw_banner_data = self.transport.sock.recv(1024) if self.transport and self.transport.sock else b''
                        logging.error(f"Raw data received from server on banner error: {raw_banner_data!r}")
                    except Exception as sock_err:
                        logging.error(f"Could not retrieve raw banner data from socket: {sock_err}")

                # Comprehensive list of retryable SSH errors
                retryable_errors = [
                    "Invalid packet blocking", 
                    "Error reading SSH protocol banner", 
                    "utf-8", 
                    "UnicodeDecodeError",
                    "WinError 10038",
                    "소켓 이외의 개체에 작업을 시도했습니다",
                    "Bad file descriptor",
                    "Socket is not valid",
                    "EOFError",
                    "Connection lost",
                    "Connection reset",
                    "Connection refused",
                    "Timeout"
                ]
                
                if any(keyword in error_msg for keyword in retryable_errors):
                    logging.warning(f"SSH connection error on attempt {attempt + 1}/{max_attempts}: {error_msg}")
                    if attempt < max_attempts - 1:
                        # Use a fixed delay for retrying
                        total_delay = self.retry_delay
                        logging.info(f"Retrying SSH connection in {total_delay} seconds (attempt {attempt + 2}/{max_attempts})")
                        time.sleep(total_delay)
                        continue  # Retry on all retryable errors
                    else:
                        logging.error(f"SSH connection failed after {max_attempts} attempts due to persistent errors")
                else:
                    logging.error(f"SSH connection failed with non-retryable error: {error_msg}")
                    break  # Don't retry on authentication errors, etc.
                
            except Exception as e:
                logging.error(f"SSH connection failed on attempt {attempt + 1}/{max_attempts}: {e}")
                if attempt < max_attempts - 1:
                    continue  # Retry on general errors
        
        # All attempts failed
        self.connected = False
        return False
    
    @abstractmethod
    def _post_connect_setup(self) -> None:
        """Subclass-specific setup after connection is established."""
        pass
    
    def disconnect(self) -> None:
        """Close SSH connection and perform aggressive cleanup."""
        logging.info(f"Disconnecting from {self.host}:{self.port}...")
        try:
            self._pre_disconnect_cleanup()
            
            if self.transport:
                try:
                    # Check if transport is still connected before closing
                    if self.transport.is_active():
                        self.transport.close()
                        logging.info("Transport closed successfully.")
                except Exception as e:
                    logging.warning(f"Error while closing transport: {e}")
                finally:
                    # Aggressively clean up transport and its socket
                    self.transport.sock = None
                    self.transport = None
            
            self.connected = False
            logging.info(f"Disconnection from {self.host}:{self.port} completed.")
            
        except Exception as e:
            logging.error(f"Error during disconnect: {e}")
        finally:
            # Ensure all resources are released
            self.transport = None
            self.connected = False
    
    @abstractmethod
    def _pre_disconnect_cleanup(self) -> None:
        """Subclass-specific cleanup before disconnection."""
        pass
    
    def _ensure_connected(self) -> bool:
        """Ensure connection is active, reconnect if necessary."""
        if not self.connected or not self.transport or not self.transport.is_active():
            return self.connect()
        return True
    
    def get_connection_info(self) -> dict:
        """Get connection information."""
        return {
            'host': self.host,
            'port': self.port,
            'username': self.username,
            'connected': self.connected,
            'transport_active': self.transport.is_active() if self.transport else False
        }
    
    def test_connection(self) -> bool:
        """Test the connection by attempting to connect and disconnect."""
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
            logging.error(f"Connection test failed: {e}")
            return False
    
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
            pass  # Ignore errors during cleanup