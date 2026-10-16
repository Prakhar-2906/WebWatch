"""Run mocked-browser notification tests against the shipped dashboard script."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for browser-script tests")
def test_frontend_notification_dispatch_with_mocked_browser():
    test_file = Path(__file__).with_name("frontend_notifications.test.cjs")
    result = subprocess.run(
        ["node", "--test", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
