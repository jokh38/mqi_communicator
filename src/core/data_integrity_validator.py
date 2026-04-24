# =====================================================================================
# Target File: src/core/data_integrity_validator.py
# =====================================================================================
"""
Data integrity validation module for RT plan and beam metadata checks.

This module provides functionality to:
1. Read RT plan DICOM files to determine expected beam/field count
2. Extract treatment beam metadata before processing begins
"""

from pathlib import Path
from typing import Dict, List, Optional
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

    def find_ptn_files(self, case_path: Path) -> List[Path]:
        """Find PTN files under the case directory."""
        try:
            return sorted(
                file_path
                for file_path in case_path.rglob("*.ptn")
                if file_path.is_file()
            )
        except Exception as e:
            self.logger.error(f"Error searching for PTN files in {case_path}: {str(e)}")
            return []

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
            raise ProcessingError(f"Invalid DICOM file: {str(e)}") from e
        except Exception as e:
            raise ProcessingError(f"Error parsing RT plan file: {str(e)}") from e

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
                            "beam_number": getattr(beam_ds, "BeamNumber", None),
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

        # Match G (or g) followed by optional non-digit chars then 1 or 2 (not followed by
        # another digit).  Handles names like G1, G2, GTR1, GTR2, Gantry1, G_1, etc.
        pattern = r'G[^0-9]*([12])(?![0-9])'
        match = re.search(pattern, machine_name, re.IGNORECASE)

        if not match:
            raise ProcessingError(
                f"No G1/G2 gantry pattern found in machine name: '{machine_name}'. "
                f"Expected a name containing G1, G2, GTR1, GTR2, or similar."
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
            ) from e
