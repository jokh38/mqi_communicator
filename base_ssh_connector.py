import paramiko
import socket
import time
from typing import Optional, Dict, Any
from error_handler import retry_on_exception


class BaseSSHConnector:
    """Base SSH connector with centralized retry logic for connections."""
    
    def __init__(self, hostname: str, username: str, password: str = None, 
                 private_key_path: str = None, port: int = 22, timeout: int = 30, logger=None):
        self.hostname = hostname
        self.username = username
        self.password = password
        self.private_key_path = private_key_path
        self.port = port
        self.timeout = timeout
        self.logger = logger
        self.client: Optional[paramiko.SSHClient] = None
        self._connection_failures = 0
        
        # Apply retry decorator to connect method
        self.connect = retry_on_exception(
            max_retries=3,
            delay_seconds=2.0,
            exceptions_to_catch=(paramiko.AuthenticationException, socket.timeout, 
                               socket.error, paramiko.SSHException, OSError),
            backoff_strategy="exponential",
            logger=self.logger
        )(self._connect_impl)
    
    def _connect_impl(self) -> bool:
        """
        Connect to SSH server with retry logic.
        
        Returns:
            bool: True if connection successful, False otherwise
            
        Raises:
            Exception: After all retry attempts are exhausted
        """
        try:
            if self.client:
                self.disconnect()
            
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Prepare authentication parameters
            auth_kwargs = {
                'hostname': self.hostname,
                'port': self.port,
                'username': self.username,
                'timeout': self.timeout
            }
            
            # Add authentication method
            if self.private_key_path:
                try:
                    private_key = paramiko.RSAKey.from_private_key_file(self.private_key_path)
                    auth_kwargs['pkey'] = private_key
                except (paramiko.ssh_exception.PasswordRequiredException, FileNotFoundError):
                    if self.password:
                        auth_kwargs['password'] = self.password
                    else:
                        raise paramiko.AuthenticationException("Private key requires passphrase or password needed")
            elif self.password:
                auth_kwargs['password'] = self.password
            else:
                raise paramiko.AuthenticationException("No authentication method provided")
            
            # Attempt connection
            self.client.connect(**auth_kwargs)
            
            # Test connection with a simple command
            stdin, stdout, stderr = self.client.exec_command('echo "test"', timeout=10)
            test_result = stdout.read().decode().strip()
            
            if test_result != "test":
                raise paramiko.SSHException("Connection test failed")
            
            if self.logger:
                self.logger.info(f"Successfully connected to {self.hostname}:{self.port}")
            
            # Reset failure counter on successful connection
            self._connection_failures = 0
            return True
            
        except Exception as e:
            self._connection_failures += 1
            if self.logger:
                self.logger.error(f"SSH connection failed to {self.hostname}:{self.port}: {e}")
            
            # Clean up on failure
            if self.client:
                try:
                    self.client.close()
                except:
                    pass
                self.client = None
            
            # Re-raise to let the retry decorator handle it
            raise
    
    def disconnect(self) -> None:
        """Disconnect from SSH server."""
        try:
            if self.client:
                self.client.close()
                self.client = None
                if self.logger:
                    self.logger.info(f"Disconnected from {self.hostname}:{self.port}")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Error during disconnect: {e}")
    
    def is_connected(self) -> bool:
        """Check if SSH connection is active."""
        try:
            if not self.client:
                return False
            
            # Try to get transport
            transport = self.client.get_transport()
            if not transport or not transport.is_active():
                return False
            
            # Simple test command with short timeout
            stdin, stdout, stderr = self.client.exec_command('echo "alive"', timeout=5)
            result = stdout.read().decode().strip()
            return result == "alive"
            
        except Exception:
            return False
    
    def execute_command(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        """
        Execute command on remote server.
        
        Args:
            command: Command to execute
            timeout: Command timeout in seconds
            
        Returns:
            Dict containing stdout, stderr, and exit_code
        """
        try:
            if not self.is_connected():
                if not self.connect():
                    raise paramiko.SSHException("Failed to establish SSH connection")
            
            stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            
            # Wait for command completion
            exit_code = stdout.channel.recv_exit_status()
            
            stdout_data = stdout.read().decode().strip()
            stderr_data = stderr.read().decode().strip()
            
            return {
                'stdout': stdout_data,
                'stderr': stderr_data,
                'exit_code': exit_code,
                'success': exit_code == 0
            }
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Command execution failed: {e}")
            return {
                'stdout': '',
                'stderr': str(e),
                'exit_code': -1,
                'success': False
            }
    
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
        return False