import paramiko
import socket
import time
import logging
from typing import Optional, Any
from abc import ABC, abstractmethod


class BaseSSHConnector(ABC):
    """Base class for SSH/SFTP connections with common functionality."""
    
    def __init__(self, host: str, username: str, password: str, port: int = 22, timeout: int = 30):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.transport: Optional[paramiko.Transport] = None
        self.connected = False
        
        # Retry configuration
        self.max_retries = 3
        self.retry_delay = 5  # seconds
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
    
    def _retry_on_network_error(self, func, *args, **kwargs):
        """Retry function execution on network errors with exponential backoff."""
        last_exception = None
        
        for attempt in range(self.max_retries + 1):  # +1 for initial attempt
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                if not self._is_network_error(e):
                    # Not a network error, don't retry
                    raise e
                
                if attempt < self.max_retries:
                    # Calculate delay with exponential backoff
                    delay = self.retry_delay * (2 ** attempt)
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
    
    def connect(self) -> bool:
        """Establish SSH connection."""
        try:
            # Create transport with timeout
            self.transport = paramiko.Transport((self.host, self.port))
            self.transport.banner_timeout = 30  # Set banner timeout
            self.transport.auth_timeout = 30    # Set auth timeout
            
            # Connect with authentication
            self.transport.connect(username=self.username, password=self.password)
            
            self.connected = True
            logging.info(f"Successfully connected to {self.host}:{self.port}")
            self._post_connect_setup()
            
            return True
            
        except paramiko.SSHException as e:
            if "Invalid packet blocking" in str(e) or "Error reading SSH protocol banner" in str(e):
                logging.error(f"SSH protocol error - check if {self.host}:{self.port} is actually an SSH server: {e}")
            else:
                logging.error(f"SSH connection failed: {e}")
            self.connected = False
            return False
        except Exception as e:
            logging.error(f"SSH connection failed: {e}")
            self.connected = False
            return False
    
    @abstractmethod
    def _post_connect_setup(self) -> None:
        """Subclass-specific setup after connection is established."""
        pass
    
    def disconnect(self) -> None:
        """Close SSH connection."""
        try:
            self._pre_disconnect_cleanup()
            
            if self.transport:
                self.transport.close()
                self.transport = None
                
            self.connected = False
            
        except Exception as e:
            logging.error(f"Error during disconnect: {e}")
    
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