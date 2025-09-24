# =====================================================================================
# Target File: src/handlers/local_handler.py
# Source Reference: src/local_handler.py
# =====================================================================================
"""Handles the execution of local command-line interface (CLI) tools."""

from typing import NamedTuple, Optional
from pathlib import Path
import shlex
import platform

from src.infrastructure.logging_handler import StructuredLogger
from src.infrastructure.process_manager import CommandExecutor
from src.config.settings import Settings
from src.utils.retry_policy import RetryPolicy
from src.domain.errors import ProcessingError


class ExecutionResult(NamedTuple):
    """A structured result from subprocess execution."""

    success: bool
    output: str
    error: str
    return_code: int


class LocalHandler:
    """Handles the execution of local command-line interface (CLI) tools.

    This class uses injected dependencies (CommandExecutor, RetryPolicy)
    to execute commands with a retry policy.
    """

    def __init__(
        self,
        settings: Settings,
        logger: StructuredLogger,
        command_executor: CommandExecutor,
        retry_policy: RetryPolicy,
    ):
        """Initializes the LocalHandler with injected dependencies.

        Args:
            settings (Settings): Application settings.
            logger (StructuredLogger): Logger for recording events.
            command_executor (CommandExecutor): Command execution service.
            retry_policy (RetryPolicy): Retry policy for failed executions.
        """
        self.settings = settings
        self.logger = logger
        self.command_executor = command_executor
        self.retry_policy = retry_policy

        # Get Python interpreter path from settings
        self.python_interpreter = self._get_python_interpreter()

    def _get_python_interpreter(self) -> str:
        """Get the Python interpreter path from configuration."""
        return self.settings.get_executables().get("python_interpreter", "python")

    def _normalize_path_for_command(self, path: str) -> str:
        """
        Normalize a path for command-line execution.
        - On WSL, converts Windows paths (e.g., C:\\...) to WSL paths (e.g., /mnt/c/...).
        - On Windows, converts backslashes to forward slashes to prevent escape
          sequence issues in command strings.
        - On other Linux/macOS, returns the path as is.

        Args:
            path (str): The path string that might be a Windows path.

        Returns:
            str: The normalized path string.
        """
        # Check if we're running in WSL
        if platform.system() == "Linux" and "microsoft" in platform.release(
        ).lower():
            # Convert Windows path to WSL path
            if path.startswith("C:"):
                return path.replace("C:", "/mnt/c").replace("\\", "/")
            elif path.startswith("D:"):
                return path.replace("D:", "/mnt/d").replace("\\", "/")
        # On native Windows, convert backslashes to forward slashes.
        # This is safer for string formatting and subprocess execution.
        elif platform.system() == "Windows":
            return path.replace("\\", "/")
        return path

    def _build_command_from_template(self, template_name: str,
                                     **kwargs) -> list[str]:
        """Builds the final execution command by combining a template from config.yaml
        with dynamic arguments.
        Args:
            template_name (str): The name of the template to use from command_templates
            in config.yaml.
            **kwargs: Dynamic arguments to pass to the template.
        Returns:
            list[str]: A list of strings representing the command, suitable for use with
            subprocess.
        Raises:
            ProcessingError: If the template cannot be found or formatting fails.
        """
        try:
            command_templates = getattr(self.settings, 'command_templates', {})
            if template_name not in command_templates:
                raise ProcessingError(
                    f"Command template '{template_name}' not found in config.yaml"
                )
            template = command_templates[template_name]
            executables = self.settings.get_executables()
            converted_executables = {}
            for key, value in executables.items():
                converted_executables[key] = self._normalize_path_for_command(
                    value)
            converted_kwargs = {}
            for key, value in kwargs.items():
                if isinstance(value, str):
                    converted_kwargs[key] = self._normalize_path_for_command(
                        value)
                else:
                    converted_kwargs[key] = value
            all_format_args = {**converted_executables, **converted_kwargs}
            formatted_command = template.format(**all_format_args)
            return shlex.split(formatted_command)
        except KeyError as e:
            raise ProcessingError(f"Missing template argument: {e}")
        except Exception as e:
            raise ProcessingError(
                f"Failed to build command from template '{template_name}': {e}"
            )

    def _execute_command_with_retry(
        self,
        case_id: str,
        case_path: Path,
        command: list[str],
        operation_name: str,
        log_message: str,
    ) -> ExecutionResult:
        """A generic helper to execute a local command with a retry policy.

        Args:
            case_id (str): Case identifier for logging.
            case_path (Path): Path to the case directory (CWD for the command).
            command (list[str]): The command to execute as a list of strings.
            operation_name (str): A unique name for the operation (for retry policy).
            log_message (str): The message to log for this operation.

        Returns:
            ExecutionResult: containing the outcome of the execution.
        """
        self.logger.info(
            log_message,
            {"case_id": case_id, "command": " ".join(command)},
        )

        def execute_attempt() -> ExecutionResult:
            try:
                result = self.command_executor.execute_command(
                    command=command,
                    cwd=case_path,
                    timeout=self.settings.processing.case_timeout,
                )
                return ExecutionResult(
                    success=True,
                    output=result.stdout,
                    error=result.stderr,
                    return_code=result.returncode,
                )
            except ProcessingError as e:
                log_ctx = {
                    "case_id": case_id,
                    "command": " ".join(command),
                    "error": str(e),
                }
                self.logger.error(f"{operation_name} execution failed",
                                  log_ctx)
                return_code = getattr(e, "return_code", -1)
                return ExecutionResult(success=False,
                                       output="",
                                       error=str(e),
                                       return_code=return_code)

        result = self.retry_policy.execute(
            execute_attempt,
            operation_name=operation_name,
            context={"case_id": case_id},
        )

        if result.success:
            self.logger.info(
                f"{operation_name} completed successfully", {
                    "case_id": case_id,
                    "output_length": len(result.output)
                },
            )
        else:
            self.logger.error(
                f"{operation_name} failed after retries",
                {
                    "case_id": case_id,
                    "return_code": result.return_code,
                    "error": result.error,
                },
            )

        return result

    def execute_mqi_interpreter(
        self,
        case_id: str,
        case_path: Path,
        command: list[str],
    ) -> ExecutionResult:
        """Executes the mqi_interpreter using a generalized command runner.

        Args:
            case_id (str): The case identifier for logging.
            case_path (Path): The path to the case directory.
            command (list[str]): The command to execute.

        Returns:
            ExecutionResult: An ExecutionResult containing the outcome.
        """
        return self._execute_command_with_retry(
            case_id=case_id,
            case_path=case_path,
            command=command,
            operation_name="mqi_interpreter",
            log_message="Executing MQI interpreter",
        )

    def execute_raw_to_dicom(
        self,
        case_id: str,
        case_path: Path,
        command: list[str],
    ) -> ExecutionResult:
        """Executes the RawToDCM converter using a generalized command runner.

        Args:
            case_id (str): The case identifier for logging.
            case_path (Path): The path to the case directory.
            command (list[str]): The command to execute.

        Returns:
            ExecutionResult: An ExecutionResult containing the outcome.
        """
        return self._execute_command_with_retry(
            case_id=case_id,
            case_path=case_path,
            command=command,
            operation_name="raw_to_dicom",
            log_message="Executing Raw to DICOM converter",
        )

    def validate_case_structure(self, case_path: Path) -> bool:
        """Validate that the case directory has the required structure and files.

        Args:
            case_path (Path): Path to the case directory.

        Returns:
            bool: True if the case structure is valid, False otherwise.
        """
        self.logger.debug("Validating case structure",
                          {"case_path": str(case_path)})

        try:
            if not case_path.exists():
                self.logger.error("Case path does not exist",
                                  {"case_path": str(case_path)})
                return False

            if not case_path.is_dir():
                self.logger.error("Case path is not a directory",
                                  {"case_path": str(case_path)})
                return False

            required_file = case_path / "case_config.yaml"
            if not required_file.exists():
                self.logger.warning(
                    f"Required file not found: {required_file}")

            return True

        except Exception as e:
            self.logger.error(
                "Case structure validation failed",
                {"case_path": str(case_path), "error": str(e)},
            )
            return False

    def run_mqi_interpreter(
            self,
            beam_directory: Path,
            output_dir: Path,
            case_id: Optional[str] = None) -> ExecutionResult:
        """Wrapper method for running the mqi_interpreter.

        Defines the dynamic arguments needed for the template and delegates command creation.

        Args:
            beam_directory (Path): Path to the beam directory (or case directory).
            output_dir (Path): Path to the output directory for generated files.
            case_id (Optional[str]): Optional case_id. If not provided, it's inferred
            from the parent directory.

        Returns:
            ExecutionResult: containing execution details.
        """
        dynamic_args = {
            "beam_directory": str(beam_directory),
            "output_dir": str(output_dir),
        }

        command_to_execute = self._build_command_from_template(
            "mqi_interpreter", **dynamic_args)

        id_to_use = case_id if case_id is not None else beam_directory.parent.name

        return self.execute_mqi_interpreter(id_to_use, beam_directory,
                                            command_to_execute)

    def run_raw_to_dcm(self, input_file: Path, output_dir: Path,
                       case_path: Path) -> ExecutionResult:
        """Wrapper method for running RawToDCM.

        Defines the dynamic arguments needed for the template and delegates command creation.

        Args:
            input_file (Path): Input .raw file.
            output_dir (Path): Output directory for DCM files.
            case_path (Path): Case directory path.

        Returns:
            ExecutionResult: containing execution details.
        """
        dynamic_args = {
            "input_file": str(input_file),
            "output_dir": str(output_dir),
        }

        command_to_execute = self._build_command_from_template(
            "raw_to_dicom", **dynamic_args)

        case_id = case_path.name
        return self.execute_raw_to_dicom(case_id, case_path,
                                         command_to_execute)
