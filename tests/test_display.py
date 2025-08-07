import time
import queue
from core.display import StatusDisplay, UpdateData

def test_status_display_can_be_started_and_stopped():
    """Test that the StatusDisplay thread can be started and stopped."""
    display = StatusDisplay(update_interval=0.1)
    display.start()
    assert display.running
    assert display.display_thread.is_alive()

    display.stop()
    # Give the thread a moment to stop
    display.display_thread.join(timeout=1)
    assert not display.running
    assert not display.display_thread.is_alive()

def test_update_case_status_adds_new_case():
    """Test that update_case_status adds a new case to the display."""
    display = StatusDisplay()
    update = UpdateData(case_id="case1", status="PROCESSING", progress=50.0)
    display.update_case_status(update)

    assert "case1" in display.cases
    assert display.cases["case1"].status == "PROCESSING"
    assert display.cases["case1"].progress == 50.0

def test_update_case_status_updates_existing_case():
    """Test that update_case_status updates an existing case."""
    display = StatusDisplay()
    initial_update = UpdateData(case_id="case1", status="PROCESSING", progress=50.0)
    display.update_case_status(initial_update)

    new_update = UpdateData(case_id="case1", status="COMPLETED", progress=100.0)
    display.update_case_status(new_update)

    assert display.cases["case1"].status == "COMPLETED"
    assert display.cases["case1"].progress == 100.0

def test_remove_case():
    """Test that remove_case removes a case from the display."""
    display = StatusDisplay()
    update = UpdateData(case_id="case1", status="PROCESSING")
    display.update_case_status(update)
    assert "case1" in display.cases

    display.remove_case("case1")
    assert "case1" not in display.cases

def test_update_system_info():
    """Test that update_system_info updates the system info dictionary."""
    display = StatusDisplay()
    system_info = {"cpu_percent": 50.0, "memory_percent": 60.0}
    display.update_system_info(system_info)

    assert display.system_info["cpu_percent"] == 50.0
    assert display.system_info["memory_percent"] == 60.0

def test_log_queue_processing():
    """Test that the display processes messages from the log queue."""
    log_queue = queue.Queue()
    display = StatusDisplay(log_queue=log_queue)

    # Mock a log record
    class MockLogRecord:
        def __init__(self, message):
            self.message = message
        def getMessage(self):
            return self.message

    log_queue.put(MockLogRecord("Test log message"))

    # The display loop runs in a separate thread, so we need to wait
    display.start()
    time.sleep(0.2)  # Give time for the display loop to process the queue
    display.stop()

    assert "Test log message" in display.log_messages
