"""Main entry point for MOQUI automation system.

This is the main entry point that initializes and runs the MOQUI automation system.
The application is now decomposed into smaller controller classes for better maintainability.
"""

from controllers.lifecycle_manager import LifecycleManager


def main():
    """Main function to start the MOQUI automation system."""
    try:
        with LifecycleManager() as controller:
            controller.run()
    except KeyboardInterrupt:
        print("Shutting down...")
    except Exception as e:
        print(f"CRITICAL: Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()