# Display Logic Improvement Plan

This document outlines a plan to refactor and improve the display logic in `status_display.py` and its interaction with `sftp_manager.py`. The goal is to eliminate redundancy, improve separation of concerns, and simplify the API.

## 1. Problem Analysis

- **Duplicated Logic:** Both the rich (`_create_cases_table`) and basic (`_display_basic`) display methods contain logic to parse progress information from older, string-based fields like `transfer_info`. This is fragile and hard to maintain.
- **Tight Coupling & Poor Separation of Concerns:** `sftp_manager.py` is responsible for formatting display strings (e.g., `f"Uploading - {current_speed:.1f} MB/s"`) and passing them to `status_display.py`. The SFTP manager should only provide raw data, and the display manager should handle all formatting.
- **Complex API:** The `update_case_status` method has a large number of parameters, mixing old and new fields. This makes it difficult to use and understand.
- **Obsolete Code:** The `_display_rich` method is no longer used and its logic has been moved to `_display_loop`, but the method itself was not removed.

## 2. Refactoring Plan

### Step 1: Refactor `status_display.py`

1.  **Introduce `UpdateData` Dataclass:** Create a new dataclass `UpdateData` to encapsulate all possible fields for a case status update. This will simplify the method signature.

    ```python
    @dataclass
    class UpdateData:
        case_id: str
        status: Optional[str] = None
        progress: Optional[float] = None
        # ... all other optional fields
    ```

2.  **Simplify `update_case_status`:** Refactor the method to accept a single `UpdateData` object. This method will be the single point of entry for all case updates and will handle the logic of creating or updating the internal `CaseDisplayInfo` object.

    ```python
    def update_case_status(self, data: UpdateData) -> None:
        # ... logic to update self.cases[data.case_id]
    ```

3.  **Centralize Data Transformation:** All logic for interpreting data (e.g., calculating percentages, formatting strings from raw data) should reside within `status_display.py`. The display methods (`_create_cases_table`, `_display_basic`) should rely on the consistently structured `CaseDisplayInfo` object and perform no parsing themselves.

4.  **Remove Obsolete `_display_rich` Method:** Delete the unused `_display_rich` method to improve code clarity.

5.  **Update Convenience Functions:** Modify the helper functions at the end of the file (`update_case_progress`, `mark_case_complete`, etc.) to use the new `UpdateData` dataclass when calling `update_case_status`.

### Step 2: Refactor `sftp_manager.py`

1.  **Decouple Display Logic:** Remove all string formatting logic related to the status display. Instead of creating strings like `"Uploading - 15.2 MB/s"`, the SFTP manager should pass raw data.

2.  **Use `UpdateData` for Status Updates:** All calls to `status_display.update_case_status` from within `sftp_manager.py` must be updated. They will now instantiate and pass an `UpdateData` object with raw, unformatted data.

    **Example (Before):**
    ```python
    status_display.update_case_status(
        case_id=case_id,
        detailed_status=f"Uploading - {current_speed:.1f} MB/s"
    )
    ```

    **Example (After):**
    ```python
    # In sftp_manager.py
    from status_display import UpdateData

    display.update_case_status(UpdateData(
        case_id=case_id,
        detailed_status=f"Uploading", # Or just the action
        # Pass raw data for status_display to format
        # e.g., transfer_speed=current_speed 
    ))
    # Note: The final data fields will need to be decided during implementation.
    # The key is to pass raw numbers and state, not pre-formatted strings.
    ```

By implementing these changes, the codebase will be cleaner, more maintainable, and have a clearer separation between data handling and presentation.
