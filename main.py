#!/usr/bin/env python3
"""
MOQUI Automation System - Main Entry Point

This is the clean entry point for the MOQUI automation system.
It initializes and runs the main application.
"""

import sys
import traceback
from controllers.app import Application


def main():
    """Main entry point for the MOQUI automation system."""
    try:
        # Create and run the application
        app = Application()
        app.run()
    except KeyboardInterrupt:
        print("Shutting down...")
    except Exception as e:
        print(f"CRITICAL: Error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()