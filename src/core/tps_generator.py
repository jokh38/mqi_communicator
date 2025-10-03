# =====================================================================================
# Target File: src/core/tps_generator.py
# Source Reference: Legacy TPS Generator service
# =====================================================================================
"""Contains the TpsGenerator service for creating moqui_tps.in files."""

from pathlib import Path
from typing import Dict, Any, List, Optional

from src.config.settings import Settings
from src.infrastructure.logging_handler import StructuredLogger
from src.core.data_integrity_validator import DataIntegrityValidator
from src.domain.errors import ProcessingError


class TpsGenerator:
    """Service for generating dynamic moqui_tps.in configuration files for cases.

    This service replaces the static input.mqi dependency by generating case-specific
    configuration files at runtime based on parameters from config.yaml and dynamic
    case data such as GPU allocation and file paths.
    """

    def __init__(self, settings: Settings, logger: StructuredLogger):
        """Initialize TPS generator with configuration settings and logger.

        Args:
            settings (Settings): Application settings containing moqui_tps_parameters.
            logger (StructuredLogger): Structured logger for error reporting and debugging.
        """
        self.settings = settings
        self.logger = logger
        self.base_parameters = settings.get_moqui_tps_parameters()

        # Fetch and resolve TPS generator paths from configuration
        self.resolved_paths = self._resolve_tps_generator_paths()

    def _resolve_tps_generator_paths(self) -> Dict[str, str]:
        """Resolve TPS generator paths from configuration using Settings.get_path().

        Note: Paths containing {case_id} or other runtime placeholders are kept as templates
        and will be resolved later when the actual values are available.

        Returns:
            Dict[str, str]: Dictionary containing path templates with placeholders preserved.
        """
        tps_config = self.settings.get_tps_generator_config()
        default_paths = tps_config.get("default_paths", {})
        resolved = {}

        for key, path_template in default_paths.items():
            # Skip base_directory as it's a direct value
            if key == "base_directory":
                resolved[key] = path_template
                continue

            # Extract the path name from placeholder format {paths.local.csv_output_dir}
            if isinstance(path_template, str) and path_template.startswith("{paths."):
                # Extract placeholder: {paths.local.csv_output_dir} -> csv_output_dir
                parts = path_template.strip("{}").split(".")
                if len(parts) >= 3 and parts[0] == "paths":
                    path_name = parts[-1]  # e.g., "csv_output_dir"
                    try:
                        # Resolve the path template, which may still contain runtime placeholders like {case_id}
                        # These will be resolved later when the actual case_id is available
                        resolved_path = self.settings.get_path(path_name, handler_name="CsvInterpreter")
                        resolved[key] = resolved_path
                        self.logger.debug(f"Resolved TPS path template '{key}': {resolved_path}")
                    except (KeyError, ValueError, IndexError) as e:
                        # This happens when the path contains runtime placeholders like {case_id}
                        # that aren't known at initialization time - this is expected and normal
                        # Store the original placeholder reference for later resolution
                        self.logger.debug(f"Path '{key}' contains runtime placeholders, will be resolved at generation time")
                        resolved[key] = path_template
                else:
                    resolved[key] = path_template
            else:
                resolved[key] = path_template

        return resolved

    def generate_tps_file_with_gpu_assignments(
        self,
        case_path: Path,
        case_id: str,
        gpu_assignments: List[Dict[str, Any]],
        execution_mode: str = "local",
        output_dir: Optional[Path] = None,
        beam_name: Optional[str] = None
    ) -> bool:
        """Generate moqui_tps.in file for a case with multiple beam-to-GPU assignments.

        Args:
            case_path (Path): Path to the case directory.
            case_id (str): Unique identifier for the case.
            gpu_assignments (List[Dict[str, Any]]): List of GPU assignments with beam numbers and GPU IDs.
            execution_mode (str): "local" or "remote" - determines path construction.
            output_dir (Optional[Path]): Directory where the TPS file should be saved. If None, uses case_path.
            beam_name (Optional[str]): Beam name to use in filename. If None, uses default "moqui_tps.in".

        Returns:
            bool: True if file was generated successfully, False otherwise.
        """
        try:
            self.logger.info("Generating moqui_tps.in file with dynamic GPU assignments", {
                "case_id": case_id,
                "case_path": str(case_path),
                "gpu_assignments": gpu_assignments,
                "execution_mode": execution_mode
            })

            # Start with base parameters from config
            parameters = self.base_parameters.copy()

            # Generate dynamic paths based on execution mode
            dynamic_paths = self._generate_dynamic_paths(
                case_path, case_id, execution_mode)
            parameters.update(dynamic_paths)

            # Extract case-specific data (gantry number and beam count)
            try:
                case_specific_data = self._extract_case_data(case_path, case_id)
                parameters.update(case_specific_data)
            except Exception as e:
                # _extract_case_data already logs the error
                return False  # Fail the case

            # Set beam count and GPU assignments
            beam_count = len(gpu_assignments)
            parameters["BeamNumbers"] = beam_count

            # Create GPU assignment mapping
            if gpu_assignments:
                # For multiple beams, set BeamNumbers to map to actual GPU IDs
                # Format: "BeamNumber1:GPUID1,BeamNumber2:GPUID2,..."
                gpu_mapping = []
                for i, assignment in enumerate(gpu_assignments):
                    beam_number = i + 1  # Beam numbers are 1-indexed
                    # Use the actual GPU ID from the assignment, not the sequential index
                    gpu_id = assignment.get("gpu_id", 0)
                    gpu_mapping.append(f"{beam_number}:{gpu_id}")

                # Set GPUID as comma-separated mapping or single value for compatibility
                if len(gpu_assignments) == 1:
                    # Single beam case - just use the GPU ID
                    parameters["GPUID"] = gpu_assignments[0].get("gpu_id", 0)
                else:
                    # Multiple beams - use mapping format
                    parameters["GPUID"] = ",".join(gpu_mapping)
            else:
                # Fallback to default single GPU
                parameters["GPUID"] = 0
                parameters["BeamNumbers"] = 1

            # Validate required parameters
            if not self._validate_parameters(parameters, case_id):
                return False

            # Generate and write the file
            content = self._format_parameters_to_string(parameters)

            # Determine output directory
            if output_dir:
                output_directory = Path(output_dir)
                output_directory.mkdir(parents=True, exist_ok=True)
            else:
                output_directory = case_path

            # Determine filename based on beam_name
            if beam_name:
                output_filename = f"moqui_tps_{beam_name}.in"
            else:
                output_filename = "moqui_tps.in"

            output_file = output_directory / output_filename
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(content)

            self.logger.info("moqui_tps.in file generated successfully with GPU assignments", {
                "case_id": case_id,
                "output_file": str(output_file),
                "parameters_count": len(parameters),
                "beam_count": beam_count,
                "gpu_assignments": gpu_assignments
            })
            return True
        except Exception as e:
            self.logger.error("Failed to generate moqui_tps.in file with GPU assignments", {
                "case_id": case_id,
                "case_path": str(case_path),
                "error": str(e),
                "exception_type": type(e).__name__
            })
            return False

    def _generate_dynamic_paths(
        self,
        case_path: Path,
        case_id: str,
        execution_mode: str
    ) -> Dict[str, Any]:
        """Generate dynamic file paths based on execution mode.

        Prioritizes paths from tps_generator configuration over hardcoded logic.
        Falls back to original logic if configuration paths are not available.

        Args:
            case_path (Path): Path to the case directory.
            case_id (str): Unique identifier for the case.
            execution_mode (str): "local" or "remote" execution mode.

        Returns:
            Dict[str, Any]: Dictionary containing dynamic path parameters.
        """
        paths = {}

        # Check for configured paths first (prioritize configuration)
        if execution_mode == "local" and self.resolved_paths:
            # Map configuration keys to moqui_tps.in parameter names
            if "outputs_dir" in self.resolved_paths:
                # OutputDir from configuration - format with case_id
                output_dir = self.resolved_paths["outputs_dir"]
                # If the path contains {case_id}, replace it
                if "{case_id}" in output_dir:
                    paths["OutputDir"] = output_dir.format(case_id=case_id)
                else:
                    paths["OutputDir"] = output_dir

            if "interpreter_outputs_dir" in self.resolved_paths:
                # ParentDir from configuration - format with case_id
                parent_dir = self.resolved_paths["interpreter_outputs_dir"]
                if "{case_id}" in parent_dir:
                    paths["ParentDir"] = parent_dir.format(case_id=case_id)
                else:
                    paths["ParentDir"] = parent_dir

        # Apply fallback logic for missing paths
        if execution_mode == "remote":
            # Use HPC paths from config for remote execution
            hpc_paths = self.settings.get_hpc_paths()
            # Get base_directory from config
            base_dir = hpc_paths.get('base_dir', self.settings._yaml_config.get("paths", {}).get("base_directory", "/home/jokh38/MOQUI_SMC"))

            # Get csv_output_dir from settings (consistent with local mode)
            csv_output_base = self.settings.get_path("csv_output_dir", handler_name="CsvInterpreter")
            csv_output_dir = Path(csv_output_base) / case_id

            paths.setdefault("DicomDir", str(case_path))
            paths.setdefault("OutputDir", f"{base_dir}/Dose_raw/{case_id}")
            paths.setdefault("logFilePath", f"{base_dir}/Dose_raw/{case_id}/simulation.log")
            paths.setdefault("ParentDir", str(csv_output_dir))
        else:
            # Use local paths for local execution with relative paths from tps_env directory
            # Extract the DICOM subdirectory name (last part of case_path)
            dicom_subdir = "/" + case_path.name  # e.g., "/1.2.840.113854.19.1.19556.1"

            # All paths are relative to tps_env directory
            paths.setdefault("ParentDir", f"../data/SHI_log/{case_id}")
            paths.setdefault("DicomDir", dicom_subdir)
            paths.setdefault("OutputDir", f"../data/Dose_raw/{case_id}")
            paths.setdefault("logFilePath", f"../data/Outputs_csv/{case_id}")

        return paths

    def _extract_case_data(self, case_path: Path, case_id: str) -> Dict[str, Any]:
        """Extract case-specific data from DICOM files or other sources.

        Args:
            case_path (Path): Path to the case directory.
            case_id (str): Unique identifier for the case.

        Returns:
            Dict[str, Any]: Dictionary containing case-specific parameters.
        """
        case_data = {}
        try:
            validator = DataIntegrityValidator(self.logger)

            # Get beam count using existing logic
            beam_info = validator.get_beam_information(case_path)
            beam_count = beam_info.get("beam_count", 0)

            # Extract gantry number and RT Plan directory from DICOM (NEW)
            try:
                gantry_number, rtplan_dir = validator.extract_gantry_number_from_rtplan(
                    case_path
                )
                case_data["GantryNum"] = gantry_number
                case_data["DicomDir"] = str(rtplan_dir)
                self.logger.info(
                    f"Extracted gantry number {gantry_number} from DICOM RT Plan at {rtplan_dir}"
                )
            except ProcessingError as e:
                self.logger.error(
                    f"Failed to extract gantry number for case {case_id}: {e}"
                )
                raise  # Re-raise to fail the case

            if beam_count > 0:
                case_data["BeamNumbers"] = beam_count

            self.logger.debug("Extracted case-specific data", {
                "case_id": case_id,
                "beam_count": beam_count,
                "case_data": case_data
            })
        except Exception as e:
            self.logger.error(f"Case data extraction failed for {case_id}: {e}")
            raise  # Fail the case
        return case_data


    def _validate_parameters(self, parameters: Dict[str, Any],
                             case_id: str) -> bool:
        """Validate that all required parameters are present and valid.

        Args:
            parameters (Dict[str, Any]): Dictionary of parameters to validate.
            case_id (str): Case ID for logging context.

        Returns:
            bool: True if validation passes, False otherwise.
        """
        try:
            # Get required parameters from config
            tps_config = self.settings._yaml_config.get('tps_generator', {})
            validation_config = tps_config.get('validation', {})
            required_params = validation_config.get('required_params', [])
            if not required_params:
                # Default required parameters if not configured
                required_params = [
                    'GPUID', 'DicomDir', 'logFilePath', 'OutputDir'
                ]
            missing_params = []
            empty_params = []
            for param in required_params:
                if param not in parameters:
                    missing_params.append(param)
                elif not parameters[param] and parameters[param] != 0:  # Allow GPUID=0
                    empty_params.append(param)
            if missing_params or empty_params:
                self.logger.error("TPS parameter validation failed", {
                    "case_id": case_id,
                    "missing_params": missing_params,
                    "empty_params": empty_params
                })
                return False
            self.logger.debug("TPS parameter validation passed", {
                "case_id": case_id,
                "validated_params": list(parameters.keys())
            })
            return True
        except Exception as e:
            self.logger.error("Error during parameter validation", {
                "case_id": case_id,
                "error": str(e)
            })
            return False
    
    def _format_parameters_to_string(self, parameters: Dict[str, Any]) -> str:
        """Format parameters dictionary into the moqui_tps.in file format.

        The format is: key value
 for each parameter.

        Args:
            parameters (Dict[str, Any]): Dictionary of parameters.

        Returns:
            str: Formatted string content for the file.
        """
        lines = []
        # Sort parameters for consistent output
        sorted_params = sorted(parameters.items())
        for key, value in sorted_params:
            # Format value appropriately
            if isinstance(value, bool):
                formatted_value = "true" if value else "false"
            elif isinstance(value, (int, float)):
                formatted_value = str(value)
            else:
                formatted_value = str(value)
            lines.append(f"{key} {formatted_value}")
        # Add final newline
        content = "\n".join(lines) + "\n"
        return content