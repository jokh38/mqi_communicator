# =====================================================================================
# Target File: src/core/case_aggregator.py
# =====================================================================================
"""Contains logic for aggregating beam statuses to update case status."""

from src.repositories.case_repo import CaseRepository
from src.domain.enums import CaseStatus, BeamStatus


def update_case_status_from_beams(case_id: str, case_repo: CaseRepository):
    """Checks the status of all beams for a given case and updates the parent case's status.

    - If all beams are COMPLETED, the case is COMPLETED.
    - If any beam is FAILED, the case is FAILED.
    - Otherwise, the case remains PROCESSING.

    Args:
        case_id (str): The ID of the parent case to check.
        case_repo (CaseRepository): An instance of the CaseRepository for database access.
    """
    beams = case_repo.get_beams_for_case(case_id)

    if not beams:
        # No beams found for this case, cannot determine status from them.
        # This might be an error condition handled elsewhere.
        return

    total_beams = len(beams)
    completed_beams = 0
    failed_beams = 0

    for beam in beams:
        if beam.status == BeamStatus.COMPLETED:
            completed_beams += 1
        elif beam.status == BeamStatus.FAILED:
            failed_beams += 1

    # Check for terminal case statuses
    if failed_beams > 0:
        # If any beam has failed, the entire case is considered failed.
        case_repo.logger.info(
            f"Case '{case_id}' has failed beams. Marking case as FAILED.")
        case_repo.update_case_status(
            case_id, CaseStatus.FAILED,
            error_message=f"{failed_beams} beam(s) failed.")
    elif completed_beams == total_beams:
        # If all beams are completed, the case is completed.
        case_repo.logger.info(
            f"All {total_beams} beams for case '{case_id}' are complete. "
            f"Marking case as COMPLETED.")
        case_repo.update_case_status(case_id, CaseStatus.COMPLETED)
    else:
        # Otherwise, the case is still processing.
        # We can optionally update the progress here.
        progress = (completed_beams / total_beams) * 100
        case_repo.update_case_status(case_id, CaseStatus.PROCESSING, progress=progress)
