#!/usr/bin/env python3
"""Entry point: launches the offline Word Report Studio desktop GUI.
The Studio UI (preview + editing + template filling) is the default;
the legacy simple form remains available with --classic."""
import sys
from cleanup import run_cleanup

if __name__ == "__main__":
    run_cleanup()
    if "--classic" in sys.argv:
        from app.desktop_ui import main
    else:
        from app.studio_ui import main
    main()
