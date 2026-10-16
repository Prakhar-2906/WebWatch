"""Install the Playwright browser into WebWatch's deployable application tree."""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from app import PLAYWRIGHT_BROWSERS_DIR, verify_playwright_browser


def playwright_install_command(platform=None):
    target_platform = sys.platform if platform is None else platform
    command = [sys.executable, "-m", "playwright", "install", "--only-shell"]
    if target_platform.startswith("linux"):
        command.append("--with-deps")
    command.append("chromium")
    return command


def main(platform=None):
    environment = os.environ.copy()
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(PLAYWRIGHT_BROWSERS_DIR)
    # Invoke Playwright through this interpreter so the browser revision is
    # selected by the exact installed Python package used by Gunicorn.
    subprocess.run(
        playwright_install_command(platform),
        check=True,
        env=environment,
    )
    verify_playwright_browser()


if __name__ == "__main__":
    main()
