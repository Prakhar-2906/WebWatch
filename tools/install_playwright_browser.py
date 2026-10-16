"""Install the Playwright browser into WebWatch's deployable application tree."""

import os
import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from app import PLAYWRIGHT_BROWSERS_DIR, verify_playwright_browser


def playwright_install_command(platform=None, *, with_deps=True):
    target_platform = sys.platform if platform is None else platform
    command = [sys.executable, "-m", "playwright", "install", "--only-shell"]
    if with_deps and target_platform.startswith("linux"):
        command.append("--with-deps")
    command.append("chromium")
    return command


def main(platform=None, *, runtime=False):
    environment = os.environ.copy()
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(PLAYWRIGHT_BROWSERS_DIR)
    if runtime:
        # Build artifacts may omit ignored browser binaries. Ensure the browser
        # exists in the running image before Gunicorn starts; system packages
        # are installed during the build step, where package-manager access is
        # available.
        try:
            verify_playwright_browser()
            return
        except RuntimeError:
            pass
    # Invoke Playwright through this interpreter so the browser revision is
    # selected by the exact installed Python package used by Gunicorn.
    subprocess.run(
        playwright_install_command(platform, with_deps=not runtime),
        check=True,
        env=environment,
    )
    verify_playwright_browser()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime", action="store_true",
        help="verify the browser in the runtime image and install it there if missing (without system packages)",
    )
    main(runtime=parser.parse_args().runtime)
