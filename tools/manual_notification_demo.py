"""Run an isolated local browser test for WebWatch availability notifications.

This disposable harness uses a fresh SQLite database, seeds one synthetic site,
and replaces the page fetcher with a controlled in-process fixture. It never
starts the scheduler and blocks API mutations other than checking its one test
record. It does not change the normal WebWatch entry point or database.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as webwatch  # noqa: E402  (load only after project root is on sys.path)
from flask import jsonify, request  # noqa: E402


HOST = "127.0.0.1"
PORT = 5055
FIXTURE_URL = "https://notification-fixture.invalid/"
FIXTURE_TEXT = "WebWatch desktop notification fixture is reachable."
MODE = {"value": "normal"}
TEST_SITE_ID: int | None = None


def controlled_fetch(url: str) -> str:
    """Return fixture text or simulate an outage; never open a network socket."""
    if url != FIXTURE_URL:
        raise ValueError("The disposable notification harness blocked an unexpected URL.")
    if MODE["value"] == "outage":
        raise webwatch.WebsiteUnreachableError(
            "Simulated connection failure from the disposable notification harness."
        )
    return FIXTURE_TEXT


def seed_test_site() -> int:
    """Insert only a synthetic, last-known reachable site and its baseline."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with webwatch.get_db() as db:
        cursor = db.execute(
            "INSERT INTO websites "
            "(url, created_at, last_checked_at, last_check_status, last_changed, "
            "last_error, is_hidden, availability_state, is_demo, demo_version) "
            "VALUES (?, ?, ?, 'unchanged', 0, NULL, 0, 'reachable', 0, 1)",
            (FIXTURE_URL, now, now),
        )
        website_id = cursor.lastrowid
        db.execute(
            "INSERT INTO snapshots (website_id, content_text, checked_at) VALUES (?, ?, ?)",
            (website_id, FIXTURE_TEXT, now),
        )
    return int(website_id)


@webwatch.app.before_request
def limit_disposable_mutations():
    """Allow only Check now for the one seeded record; reject all other writes."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if (
        request.method == "POST"
        and TEST_SITE_ID is not None
        and request.path == f"/api/websites/{TEST_SITE_ID}/check"
    ):
        return None
    return jsonify(error="Writes are disabled in the disposable notification harness."), 403


@webwatch.app.after_request
def remove_remote_font_import(response):
    """Keep the browser test local by removing the stylesheet's Google Fonts import."""
    if request.path == "/static/styles.css" and response.mimetype == "text/css":
        css = response.get_data(as_text=True)
        css = "\n".join(line for line in css.splitlines() if "fonts.googleapis.com" not in line)
        response.set_data(css)
    return response


def read_fixture_commands() -> None:
    """Accept fixture state changes from this local process's terminal only."""
    print("Fixture commands: normal, outage, recovery. Stop the server with Ctrl+C.")
    for raw_command in sys.stdin:
        command = raw_command.strip().lower()
        if command in {"normal", "recovery"}:
            MODE["value"] = "normal"
            print("Fixture mode: reachable. Click Check now in the browser to verify recovery.")
        elif command == "outage":
            MODE["value"] = "outage"
            print("Fixture mode: simulated outage. Click Check now in the browser.")
        elif command:
            print("Use one of: normal, outage, recovery.")


def main() -> None:
    global TEST_SITE_ID

    with tempfile.TemporaryDirectory(prefix="webwatch-notification-demo-") as temporary_directory:
        temp_root = Path(temporary_directory).resolve(strict=True)
        database_path = (temp_root / "notification-demo.sqlite3").resolve()
        try:
            database_path.relative_to(temp_root)
        except ValueError as exc:
            raise RuntimeError("Refusing to start: the demo database is outside its temporary directory.") from exc
        if database_path == temp_root or database_path.exists():
            raise RuntimeError("Refusing to start: the demo database path is not fresh and isolated.")

        # Set and verify the database destination before the first database call.
        webwatch.app.config["DATABASE"] = str(database_path)
        configured_path = Path(webwatch.app.config["DATABASE"]).resolve()
        try:
            configured_path.relative_to(temp_root)
        except ValueError as exc:
            raise RuntimeError("Refusing to start: Flask resolved the database outside the temporary directory.") from exc
        if configured_path != database_path:
            raise RuntimeError("Refusing to start: the configured database path changed unexpectedly.")
        production_database = (Path(webwatch.app.instance_path) / "webwatch.sqlite3").resolve()
        if configured_path == production_database:
            raise RuntimeError("Refusing to start: the demo path resolves to WebWatch's normal database.")

        # The app module's normal entry point is not used: init only this temp DB,
        # and intentionally do not call start_scheduler().
        webwatch.init_db()
        TEST_SITE_ID = seed_test_site()
        webwatch.fetch_page_text = controlled_fetch

        command_thread = threading.Thread(target=read_fixture_commands, daemon=True)
        command_thread.start()

        print(f"Temporary database: {configured_path}")
        print(f"Only seeded website ID {TEST_SITE_ID} can be checked; its URL is a reserved .invalid name.")
        print("Page fetching is mocked; unexpected URLs fail closed. No scheduler is running.")
        print("Open http://127.0.0.1:5055, allow browser notifications, then use the site's Check now button.")
        print("Click Check now once in normal mode for an unchanged check, then type outage and click again.")
        print("Type recovery and click Check now again to produce the recovery event.")
        print("Stop with Ctrl+C. The temporary database is removed when this process exits.")

        webwatch.app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
