# =====================================================================================
# Target File: src/core/data_integrity_validator.py
# =====================================================================================
"""
Data integrity validation module for ensuring complete data transfer.

This module provides functionality to:
1. Read RT plan DICOM files to determine expected beam/field count
2. Validate that all expected subfolders are present after data transfer
3. Ensure data transfer completion before processing begins
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pydicom
from pydicom.errors import InvalidDicomError
import re

from src.infrastructure.logging_handler import StructuredLogger
from src.domain.errors import ProcessingError


class DataIntegrityValidator:
    """Validates data integrity for case processing."""

    def __init__(self, logger: StructuredLogger):
        """
        Initialize the data integrity validator.

        Args:
            logger: The structured logger instance
        """
        self.logger = logger

    def find_rtplan_file(self, case_path: Path) -> Optional[Path]:
        """
        Find RT plan DICOM file in the case directory.

        Args:
            case_path: Path to the case directory

        Returns:
            Path to RT plan file if found, None otherwise
        """
        try:
            # Common RT plan file patterns
            rtplan_patterns = [
                "*.dcm",
                "*.DICOM",
                "*.dicom",
                "*rtplan*",
                "*RTPLAN*",
                "*plan*",
                "*PLAN*",
            ]

            for pattern in rtplan_patterns:
                for file_path in case_path.glob(pattern):
                    if file_path.is_file():
                        try:
                            # Check if this is actually an RT plan file
                            ds = pydicom.dcmread(file_path, stop_before_pixels=True)
                            if ds.get("Modality") == "RTPLAN":
                                self.logger.info(f"Found RT plan file: " f"{file_path}")
                                return file_path
                        except Exception:
                            # Not a valid DICOM or not RT plan, continue searching
                            continue

            # Also check in subdirectories (one level deep)
            for subdir in case_path.iterdir():
                if subdir.is_dir():
                    for pattern in rtplan_patterns:
                        for file_path in subdir.glob(pattern):
                            if file_path.is_file():
                                try:
                                    ds = pydicom.dcmread(file_path, stop_before_pixels=True)
                                    if ds.get("Modality") == "RTPLAN":
                                        self.logger.info(
                                            f"Found RT plan file in "
                                            f"subdirectory: {file_path}"
                                        )
                                        return file_path
                                except Exception:
                                    continue

            return None

        except Exception as e:
            self.logger.error(
                f"Error searching for RT plan file in " f"{case_path}: {str(e)}"
            )
            return None

    def parse_rtplan_beam_count(self, rtplan_path: Path) -> int:
        """
        Parse RT plan file to get expected beam count.

        Args:
            rtplan_path: Path to the RT plan DICOM file

        Returns:
            Number of beams expected from the RT plan

        Raises:
            ProcessingError: If RT plan cannot be parsed or beam count cannot be determined
        """
        try:
            # Use the reference parser logic
            if not rtplan_path.exists():
                raise ProcessingError(f"RT plan file not found: {rtplan_path}")

            ds = pydicom.dcmread(rtplan_path)

            # Validate it's an RT plan
            if ds.get("Modality") != "RTPLAN":
                modality = ds.get("Modality")
                raise ProcessingError(f"File is not an RT plan. " f"Modality: {modality}")

            beam_count = 0

            # Check for ion beam sequence (for proton plans)
            if hasattr(ds, "IonBeamSequence") and ds.IonBeamSequence:
                for i, beam_ds in enumerate(ds.IonBeamSequence):
                    # Filter out setup beams as in reference parser
                    beam_description = getattr(beam_ds, "BeamDescription", "")
                    beam_name = getattr(beam_ds, "BeamName", "")

                    if beam_description == "Site Setup" or beam_name == "SETUP":
                        self.logger.debug(f"Skipping setup beam: {beam_name}")
                        continue

                    beam_count += 1

            # Fallback: check regular beam sequence (for photon plans)
            elif hasattr(ds, "BeamSequence") and ds.BeamSequence:
                for i, beam_ds in enumerate(ds.BeamSequence):
                    beam_description = getattr(beam_ds, "BeamDescription", "")
                    beam_name = getattr(beam_ds, "BeamName", "")

                    if beam_description == "Site Setup" or beam_name == "SETUP":
                        self.logger.debug(f"Skipping setup beam: {beam_name}")
                        continue

                    beam_count += 1

            # Alternative: check fraction group sequence
            elif hasattr(ds, "FractionGroupSequence") and ds.FractionGroupSequence:
                fraction_group = ds.FractionGroupSequence[0]
                if hasattr(fraction_group, "NumberOfBeams"):
                    beam_count = fraction_group.NumberOfBeams

            self.logger.info(f"RT plan contains {beam_count} treatment beams")
            return beam_count

        except InvalidDicomError as e:
            raise ProcessingError(f"Invalid DICOM file: {str(e)}")
        except Exception as e:
            raise ProcessingError(f"Error parsing RT plan file: {str(e)}")

    def count_beam_subdirectories(self, case_path: Path) -> int:
        """
        Count the number of beam subdirectories in the case directory.

        Args:
            case_path: Path to the case directory

        Returns:
            Number of subdirectories found
        """
        try:
            subdirs = [d for d in case_path.iterdir() if d.is_dir()]
            count = len(subdirs)
            self.logger.debug(f"Found {count} subdirectories in {case_path}")
            return count
        except Exception as e:
            self.logger.error(f"Error counting subdirectories in " f"{case_path}: {str(e)}")
            return 0

    def validate_data_transfer_completion(
        self, case_id: str, case_path: Path
    ) -> Tuple[bool, str]:
        """
        Validate that data transfer is complete for a case.

        This function:
        1. Finds and parses the RT plan file to get expected beam count
        2. Counts actual beam subdirectories present
        3. Validates that the counts match

        Args:
            case_id: The case identifier
            case_path: Path to the case directory

        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if data transfer appears complete
            - error_message: Error description if validation fails, empty string if valid
        """
        try:
            self.logger.info(f"Validating data transfer completion for " f"case: {case_id}")

            # Step 1: Find RT plan file
            rtplan_path = self.find_rtplan_file(case_path)
            if not rtplan_path:
                error_msg = f"No RT plan file found in case directory: " f"{case_path}"
                self.logger.warning(error_msg)
                return False, error_msg

            # Step 2: Parse RT plan to get expected beam count
            try:
                expected_beam_count = self.parse_rtplan_beam_count(rtplan_path)
            except ProcessingError as e:
                error_msg = f"Failed to parse RT plan file: {str(e)}"
                self.logger.error(error_msg)
                return False, error_msg

            # Step 3: Count actual beam subdirectories
            actual_subdir_count = self.count_beam_subdirectories(case_path)

            # Step 4: Validate counts match
            if expected_beam_count == 0:
                self.logger.warning(f"RT plan indicates 0 beams for case {case_id}")
                # This might be valid in some cases, but worth noting
                return True, ""

            if actual_subdir_count < expected_beam_count:
                error_msg = (
                    f"Data transfer incomplete: Expected "
                    f"{expected_beam_count} beams from RT plan, but only "
                    f"found {actual_subdir_count} subdirectories"
                )
                self.logger.warning(error_msg)
                return False, error_msg

            if actual_subdir_count > expected_beam_count:
                # This might be okay - there could be extra directories
                self.logger.info(
                    f"Found {actual_subdir_count} subdirectories, "
                    f"RT plan expects {expected_beam_count} beams. "
                    f"Extra directories may be present."
                )

            self.logger.info(
                f"Data transfer validation passed for case "
                f"{case_id}: {expected_beam_count} expected beams, "
                f"{actual_subdir_count} subdirectories found"
            )
            return True, ""

        except Exception as e:
            error_msg = f"Unexpected error during data transfer " f"validation: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg

    def get_beam_information(self, case_path: Path) -> Dict:
        """
        Extract detailed beam information from RT plan for validation and processing.

        Args:
            case_path: Path to the case directory

        Returns:
            Dictionary containing beam information from RT plan
        """
        try:
            rtplan_path = self.find_rtplan_file(case_path)
            if not rtplan_path:
                return {"beam_count": 0, "beams": [], "error": "No RT plan file found"}

            # Parse full RT plan data using reference parser logic
            ds = pydicom.dcmread(rtplan_path)

            beam_info = {
                "beam_count": 0,
                "beams": [],
                "patient_id": ds.get("PatientID", "Unknown"),
                "plan_label": ds.get("RTPlanLabel", "Unknown"),
                "plan_date": ds.get("RTPlanDate", "Unknown"),
            }

            # Try to extract gantry number
            try:
                gantry_number, rtplan_dir = self.extract_gantry_number_from_rtplan(case_path)
                beam_info["gantry_number"] = gantry_number
                beam_info["rtplan_dir"] = str(rtplan_dir)
            except ProcessingError as e:
                beam_info["gantry_error"] = str(e)
                self.logger.warning(f"Could not extract gantry number: {e}")

            if hasattr(ds, "IonBeamSequence") and ds.IonBeamSequence:
                for i, beam_ds in enumerate(ds.IonBeamSequence):
                    beam_description = getattr(beam_ds, "BeamDescription", "")
                    beam_name = getattr(beam_ds, "BeamName", f"Beam_{i+1}")

                    if beam_description == "Site Setup" or beam_name == "SETUP":
                        continue

                    beam_info["beams"].append(
                        {
                            "beam_name": beam_name,
                            "beam_description": beam_description,
                            "treatment_machine": getattr(
                                beam_ds, "TreatmentMachineName", "Unknown"
                            ),
                        }
                    )
                    beam_info["beam_count"] += 1

            return beam_info

        except Exception as e:
            return {"beam_count": 0, "beams": [], "error": str(e)}

    def get_treatment_beam_numbers(self, case_path: Path) -> List[int]:
        """
        Extract treatment beam numbers from RT plan, excluding setup beams.

        This returns the actual beam numbers from the DICOM file for treatment beams,
        which may not be sequential if setup beams are present.

        Args:
            case_path: Path to the case directory

        Returns:
            List of beam numbers (1-indexed) for treatment beams only
        """
        try:
            rtplan_path = self.find_rtplan_file(case_path)
            if not rtplan_path:
                self.logger.error("No RT plan file found for beam number extraction")
                return []

            ds = pydicom.dcmread(rtplan_path)

            if ds.get("Modality") != "RTPLAN":
                self.logger.error(f"File is not an RT plan. Modality: {ds.get('Modality')}")
                return []

            treatment_beam_numbers = []

            # Check for ion beam sequence (for proton plans)
            if hasattr(ds, "IonBeamSequence") and ds.IonBeamSequence:
                for beam_ds in ds.IonBeamSequence:
                    beam_description = getattr(beam_ds, "BeamDescription", "")
                    beam_name = getattr(beam_ds, "BeamName", "")
                    beam_number = getattr(beam_ds, "BeamNumber", None)

                    # Skip setup beams
                    if beam_description == "Site Setup" or beam_name == "SETUP":
                        self.logger.debug(f"Skipping setup beam: {beam_name} (number: {beam_number})")
                        continue

                    if beam_number is not None:
                        treatment_beam_numbers.append(int(beam_number))
                        self.logger.debug(f"Found treatment beam: {beam_name} (number: {beam_number})")

            # Fallback: check regular beam sequence (for photon plans)
            elif hasattr(ds, "BeamSequence") and ds.BeamSequence:
                for beam_ds in ds.BeamSequence:
                    beam_description = getattr(beam_ds, "BeamDescription", "")
                    beam_name = getattr(beam_ds, "BeamName", "")
                    beam_number = getattr(beam_ds, "BeamNumber", None)

                    # Skip setup beams
                    if beam_description == "Site Setup" or beam_name == "SETUP":
                        self.logger.debug(f"Skipping setup beam: {beam_name} (number: {beam_number})")
                        continue

                    if beam_number is not None:
                        treatment_beam_numbers.append(int(beam_number))
                        self.logger.debug(f"Found treatment beam: {beam_name} (number: {beam_number})")

            self.logger.info(f"Extracted {len(treatment_beam_numbers)} treatment beam numbers: {treatment_beam_numbers}")
            return treatment_beam_numbers

        except Exception as e:
            self.logger.error(f"Error extracting treatment beam numbers: {str(e)}")
            return []

    def _extract_gantry_number_from_machine_name(self, machine_name: str) -> int:
        """
        Extract gantry number from machine name containing G1/G2 pattern.

        Args:
            machine_name: Treatment machine name from DICOM
                (e.g., "Gantry_G1", "ProtonBeam_G2")

        Returns:
            int: Gantry number (1 or 2)

        Raises:
            ProcessingError: If G1/G2 pattern not found
        """
        if not machine_name:
            raise ProcessingError("Machine name is empty or None")

        # Look for G1 or G2 pattern in machine name
        pattern = r'G([12])'
        match = re.search(pattern, machine_name, re.IGNORECASE)

        if not match:
            raise ProcessingError(
                f"No G1/G2 gantry pattern found in machine name: {machine_name}"
            )

        gantry_number = int(match.group(1))
        self.logger.debug(
            f"Extracted gantry number {gantry_number} from machine name: "
            f"{machine_name}"
        )
        return gantry_number

    def extract_gantry_number_from_rtplan(self, case_path: Path) -> tuple[int, Path]:
        """
        Extract and validate gantry number from RT Plan file.

        Returns:
            tuple[int, Path]: Gantry number from first treatment beam and RT Plan file directory path

        Raises:
            ProcessingError: If validation fails (multiple gantries,
                no G1/G2, no DICOM, etc.)
        """
        try:
            # Find RT Plan file
            rtplan_path = self.find_rtplan_file(case_path)
            if not rtplan_path:
                raise ProcessingError(
                    "No DICOM RT Plan file found for gantry number extraction"
                )

            # Parse RT plan
            ds = pydicom.dcmread(rtplan_path)

            if ds.get("Modality") != "RTPLAN":
                raise ProcessingError(
                    f"File is not an RT plan. Modality: {ds.get('Modality')}"
                )

            gantry_numbers = set()
            treatment_beams_found = False

            # Check for ion beam sequence (for proton plans)
            if hasattr(ds, "IonBeamSequence") and ds.IonBeamSequence:
                for i, beam_ds in enumerate(ds.IonBeamSequence):
                    beam_description = getattr(beam_ds, "BeamDescription", "")
                    beam_name = getattr(beam_ds, "BeamName", "")

                    # Skip setup beams
                    if beam_description == "Site Setup" or beam_name == "SETUP":
                        self.logger.debug(f"Skipping setup beam: {beam_name}")
                        continue

                    treatment_beams_found = True
                    machine_name = getattr(beam_ds, "TreatmentMachineName", "")

                    if machine_name:
                        try:
                            gantry_num = self._extract_gantry_number_from_machine_name(
                                machine_name
                            )
                            gantry_numbers.add(gantry_num)
                        except ProcessingError as e:
                            self.logger.error(
                                f"Failed to extract gantry from beam {beam_name}: {e}"
                            )
                            raise

            # Fallback: check regular beam sequence (for photon plans)
            elif hasattr(ds, "BeamSequence") and ds.BeamSequence:
                for i, beam_ds in enumerate(ds.BeamSequence):
                    beam_description = getattr(beam_ds, "BeamDescription", "")
                    beam_name = getattr(beam_ds, "BeamName", "")

                    # Skip setup beams
                    if beam_description == "Site Setup" or beam_name == "SETUP":
                        self.logger.debug(f"Skipping setup beam: {beam_name}")
                        continue

                    treatment_beams_found = True
                    machine_name = getattr(beam_ds, "TreatmentMachineName", "")

                    if machine_name:
                        try:
                            gantry_num = self._extract_gantry_number_from_machine_name(
                                machine_name
                            )
                            gantry_numbers.add(gantry_num)
                        except ProcessingError as e:
                            self.logger.error(
                                f"Failed to extract gantry from beam {beam_name}: {e}"
                            )
                            raise

            # Validation checks
            if not treatment_beams_found:
                raise ProcessingError("No treatment beams found in RT Plan")

            if not gantry_numbers:
                raise ProcessingError("No G1/G2 gantry pattern found in machine names")

            if len(gantry_numbers) > 1:
                raise ProcessingError(
                    f"Multiple different gantry numbers found in single case: "
                    f"{sorted(gantry_numbers)}"
                )

            # Return the single gantry number and RT Plan directory
            gantry_number = list(gantry_numbers)[0]
            rtplan_dir = rtplan_path.parent
            self.logger.info(
                f"Successfully extracted gantry number {gantry_number} from RT Plan at {rtplan_dir}"
            )
            return gantry_number, rtplan_dir

        except ProcessingError:
            # Re-raise ProcessingError as-is
            raise
        except Exception as e:
            raise ProcessingError(
                f"Unexpected error during gantry number extraction: {str(e)}"
            )
