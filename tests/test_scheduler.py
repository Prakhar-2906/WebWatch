"""Tests for automatic monitoring configuration and scheduled check execution."""

import sqlite3
import threading

import pytest

import app as webwatch
from conftest import setup_test_owner, login_test_user, TEST_SECRET, TEST_SETUP_TOKEN


@pytest.fixture
def scheduler_client(tmp_path, monkeypatch):
    database = tmp_path / "scheduler-test.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    webwatch.init_db()
    with webwatch.app.test_client() as client:
        setup_test_owner(client)
        yield client, database


def add_site(client, url, interval=60):
    response = client.post("/api/websites", json={"url": url, "check_interval_minutes": interval})
    assert response.status_code == 201
    return response.get_json()["id"]


def test_pending_retry_runs_inside_flask_context_and_completes(scheduler_client, monkeypatch, caplog):
    _client, database = scheduler_client
    website_id = add_site(_client, "https://1.1.1.1/")
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        job_id, _created = webwatch.enqueue_check_job(db, website_id, "manual")
        webwatch.update_check_job(
            db, job_id, "retry", attempts=1, error="temporary timeout",
            next_run_at="2000-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr(webwatch, "reserve_domain_slot", lambda _url: None)
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: "Recovered page")
    result = webwatch.run_due_schedule()

    assert result is None  # The disabled automatic schedule only drains durable jobs.
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        job = db.execute("SELECT status, attempts FROM check_jobs WHERE id = ?", (job_id,)).fetchone()
        website = db.execute(
            "SELECT last_check_status FROM websites WHERE id = ?", (website_id,)
        ).fetchone()
        snapshot = db.execute(
            "SELECT content_text FROM snapshots WHERE website_id = ?", (website_id,)
        ).fetchone()
    assert (job["status"], job["attempts"]) == ("succeeded", 2)
    assert website["last_check_status"] == "baseline"
    assert snapshot["content_text"] == "Recovered page"
    assert "Working outside of application context" not in caplog.text


def test_schedule_defaults_and_enable_disable_are_persisted(scheduler_client):
    client, database = scheduler_client

    initial = client.get("/api/schedule").get_json()
    assert initial == {
        "enabled": False,
        "interval_minutes": 60,
        "last_run_at": None,
        "next_run_at": None,
        "run_status": "never",
        "run_summary": None,
    }

    enabled = client.put("/api/schedule", json={"enabled": True, "interval_minutes": 15})
    assert enabled.status_code == 200
    assert enabled.get_json()["enabled"] is True
    assert enabled.get_json()["interval_minutes"] == 15
    assert enabled.get_json()["next_run_at"] is None  # No websites have been added yet.

    disabled = client.put("/api/schedule", json={"enabled": False, "interval_minutes": 15})
    assert disabled.status_code == 200
    assert disabled.get_json()["enabled"] is False
    assert disabled.get_json()["next_run_at"] is None
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM app_settings").fetchone() == (8,)
    assert webwatch.run_scheduled_checks()["skipped"] is True


@pytest.mark.parametrize("payload", [
    {"enabled": True, "interval_minutes": 0},
    {"enabled": True, "interval_minutes": 10081},
    {"enabled": "yes", "interval_minutes": 10},
    {"enabled": True, "interval_minutes": 1.5},
])
def test_schedule_rejects_invalid_settings(scheduler_client, payload):
    client, _database = scheduler_client
    assert client.put("/api/schedule", json=payload).status_code == 400


def test_scheduled_run_includes_hidden_sites_and_excludes_demo(scheduler_client, monkeypatch):
    client, _database = scheduler_client
    public_id = add_site(client, "https://1.1.1.1/")
    hidden_id = add_site(client, "https://9.9.9.9/")
    client.patch(f"/api/websites/{hidden_id}/hidden", json={"is_hidden": True})
    demo_id = client.post("/api/demo").get_json()["id"]
    client.put("/api/schedule", json={"enabled": True, "interval_minutes": 60})
    checked_ids = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def get_json(silent=True):
            return {"last_check_status": "changed"}

    def fake_check(site_id, wait_for_lock=False):
        checked_ids.append(site_id)
        with sqlite3.connect(_database) as db:
            db.execute("UPDATE websites SET last_checked_at = ? WHERE id = ?", (webwatch.utc_now(), site_id))
        return FakeResponse()

    monkeypatch.setattr(webwatch, "check_site", fake_check)
    summary = webwatch.run_scheduled_checks()

    assert set(checked_ids) == {public_id, hidden_id}
    assert demo_id not in checked_ids
    assert summary == {
        "skipped": False,
        "total": 2,
        "completed": 2,
        "successful": 2,
        "failed": 0,
        "changed": 2,
        "failures": [],
    }
    schedule = client.get("/api/schedule").get_json()
    assert schedule["last_run_at"]
    assert schedule["next_run_at"]


def test_scheduled_run_continues_after_a_site_error(scheduler_client, monkeypatch):
    client, _database = scheduler_client
    first = add_site(client, "https://1.1.1.1/")
    second = add_site(client, "https://9.9.9.9/")
    third = add_site(client, "https://8.8.8.8/")
    client.put("/api/schedule", json={"enabled": True, "interval_minutes": 60})
    checked_ids = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def get_json(silent=True):
            return {"last_check_status": "changed"}

    def fake_check(site_id, wait_for_lock=False):
        checked_ids.append(site_id)
        if site_id == first:
            raise RuntimeError("simulated check failure")
        return FakeResponse()

    monkeypatch.setattr(webwatch, "check_site", fake_check)
    summary = webwatch.run_scheduled_checks()

    assert checked_ids == [first, second, third]
    assert summary["completed"] == summary["total"] == 3
    assert summary["successful"] == 2
    assert summary["failed"] == 1
    assert summary["changed"] == 2
    schedule = client.get("/api/schedule").get_json()
    assert schedule["run_status"] == "partial_failure"
    assert schedule["run_summary"]["failed"] == 1


def test_due_run_uses_real_check_path_saves_each_site_and_skips_demo(scheduler_client, monkeypatch):
    client, database = scheduler_client
    first = add_site(client, "https://1.1.1.1/")
    hidden = add_site(client, "https://9.9.9.9/")
    failed = add_site(client, "https://8.8.8.8/")
    client.patch(f"/api/websites/{hidden}/hidden", json={"is_hidden": True})
    demo = client.post("/api/demo").get_json()["id"]
    client.put("/api/schedule", json={"enabled": True, "interval_minutes": 30})
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE app_settings SET setting_value = '2000-01-01T00:00:00+00:00' "
            "WHERE setting_key = 'schedule_next_run_at'"
        )

    fetched_urls = []

    def fake_fetch(url):
        fetched_urls.append(url)
        if url == "https://8.8.8.8/":
            raise ValueError("simulated website fetch failure")
        return f"Readable content for {url}"

    monkeypatch.setattr(webwatch, "render_page_text", fake_fetch)
    result = webwatch.run_due_schedule()

    assert result["total"] == result["completed"] == 3
    assert result["successful"] == 2, (result, fetched_urls)
    assert result["failed"] == 1
    assert set(fetched_urls) == {"https://1.1.1.1/", "https://9.9.9.9/", "https://8.8.8.8/"}
    assert all(not url.startswith("demo:") for url in fetched_urls)

    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        rows = {
            row["id"]: dict(row)
            for row in db.execute(
                "SELECT id, last_checked_at, last_check_status, last_error FROM websites"
            )
        }
        snapshot_counts = dict(db.execute(
            "SELECT website_id, COUNT(*) FROM snapshots GROUP BY website_id"
        ).fetchall())
    assert rows[first]["last_checked_at"] and rows[first]["last_check_status"] == "baseline"
    assert rows[hidden]["last_checked_at"] and rows[hidden]["last_check_status"] == "baseline"
    assert rows[failed]["last_checked_at"] and rows[failed]["last_check_status"] == "error"
    assert snapshot_counts == {first: 1, hidden: 1}
    assert rows[demo]["last_checked_at"] is None
    settings = client.get("/api/schedule").get_json()
    assert settings["run_status"] == "partial_failure"
    assert settings["run_summary"]["completed"] == 3
    assert settings["run_summary"]["failed"] == 1


def test_scheduler_fetches_shared_url_once_for_two_hundred_owners(scheduler_client, monkeypatch):
    client, database = scheduler_client
    add_site(client, "https://1.1.1.1/")
    client.put("/api/schedule", json={"enabled": True, "interval_minutes": 60})
    with sqlite3.connect(database) as db:
        for index in range(2, 201):
            email = f"watcher{index}@example.test"
            db.execute(
                "INSERT INTO users (email, email_normalized, password_hash, role, created_at) "
                "VALUES (?, ?, 'unused-test-hash', 'member', ?)",
                (email, email, webwatch.utc_now()),
            )
            owner_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO user_settings (owner_id, setting_key, setting_value) VALUES (?, 'schedule_enabled', '1')",
                (owner_id,),
            )
            url = "https://1.1.1.1:443#ignored-fragment" if index % 2 else "https://1.1.1.1/"
            db.execute(
                "INSERT INTO websites (url, created_at, display_name, owner_id) VALUES (?, ?, ?, ?)",
                (url, webwatch.utc_now(), f"Watcher {index}", owner_id),
            )
    requests = []

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "text/html", "Content-Length": "39"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size):
            assert chunk_size == 16384
            yield b"<html><body>Shared page</body></html>"

    class FakeSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def mount(_prefix, _adapter):
            return None

        def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return FakeResponse()

    def fake_render(url, **kwargs):
        body, _headers, _final_url = webwatch.fetch_public_resource(
            url, accept="text/html,application/xhtml+xml",
            max_bytes=webwatch.MAX_RESPONSE_BYTES, require_html=True,
        )
        return webwatch.extract_readable_text(body)

    monkeypatch.setattr(webwatch, "reserve_domain_slot", lambda _url: None)
    monkeypatch.setattr(webwatch.requests, "Session", FakeSession)
    monkeypatch.setattr(webwatch, "render_page_text", fake_render)

    result = webwatch.run_due_schedule()

    assert result["total"] == result["completed"] == result["successful"] == 200
    assert result["failed"] == 0
    # Count actual requests.Session.get calls at WebWatch's SSRF-validated
    # HTTP fetch boundary; the transport itself is controlled and offline.
    assert [url for url, _kwargs in requests] == ["https://1.1.1.1/"]
    assert requests[0][1]["allow_redirects"] is False
    assert requests[0][1]["stream"] is True
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 200
        assert db.execute(
            "SELECT COUNT(DISTINCT w.owner_id) FROM snapshots s JOIN websites w ON w.id=s.website_id"
        ).fetchone()[0] == 200


def test_due_run_records_read_timeout_as_site_error(scheduler_client, monkeypatch):
    client, database = scheduler_client
    website_id = add_site(client, "https://1.1.1.1/")
    client.put("/api/schedule", json={"enabled": True, "interval_minutes": 30})
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE app_settings SET setting_value = '2000-01-01T00:00:00+00:00' "
            "WHERE setting_key = 'schedule_next_run_at'"
        )

    monkeypatch.setattr(
        webwatch,
        "validated_http_url",
        lambda _url: ("https://1.1.1.1/", "1.1.1.1", {"1.1.1.1"}),
    )

    class TimeoutSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def mount(_prefix, _adapter):
            pass

        @staticmethod
        def get(*_args, **_kwargs):
            raise webwatch.ReadTimeout("simulated read timeout")

    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: (_ for _ in ()).throw(
        webwatch.WebsiteTemporaryError("The webpage took too long to respond. Try again later.")
    ))
    result = webwatch.run_due_schedule()

    assert result["completed"] == result["total"] == 1
    assert result["successful"] == 0
    assert result["failed"] == 1
    with sqlite3.connect(database) as db:
        row = db.execute(
            "SELECT last_checked_at, last_check_status, last_error FROM websites WHERE id = ?",
            (website_id,),
        ).fetchone()
        snapshots = db.execute("SELECT COUNT(*) FROM snapshots WHERE website_id = ?", (website_id,)).fetchone()[0]
    assert row[0]
    assert row[1] == "error"
    assert row[2] == "The webpage took too long to respond. Try again later."
    assert snapshots == 0


def test_due_run_retries_one_transient_read_timeout(scheduler_client, monkeypatch):
    client, database = scheduler_client
    website_id = add_site(client, "https://1.1.1.1/")
    client.put("/api/schedule", json={"enabled": True, "interval_minutes": 30})
    monkeypatch.setattr(
        webwatch,
        "validated_http_url",
        lambda _url: ("https://1.1.1.1/", "1.1.1.1", {"1.1.1.1"}),
    )
    attempts = []

    class PageResponse:
        status_code = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            assert chunk_size == 16384
            yield b"<html><body>Recovered scheduled check</body></html>"

    class FlakySession:
        trust_env = False
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def mount(self, *_args): pass
        def get(self, *_args, **_kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise webwatch.ReadTimeout("simulated one-off read timeout")
            return PageResponse()

    attempts = []
    def flaky_render(_url):
        attempts.append(True)
        if len(attempts) == 1:
            raise webwatch.WebsiteTemporaryError("The webpage took too long to respond. Try again later.")
        return "Recovered scheduled check"
    monkeypatch.setattr(webwatch, "render_page_text", flaky_render)
    result = webwatch.run_due_schedule()

    assert len(attempts) == 2
    assert result["completed"] == result["total"] == 1
    assert result["successful"] == 1
    assert result["failed"] == 0
    with sqlite3.connect(database) as db:
        status = db.execute(
            "SELECT last_check_status FROM websites WHERE id = ?", (website_id,)
        ).fetchone()[0]
        snapshots = db.execute(
            "SELECT content_text FROM snapshots WHERE website_id = ?", (website_id,)
        ).fetchall()
        notifications = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE website_id = ?", (website_id,)
        ).fetchone()[0]
    assert status == "baseline"
    assert snapshots == [("Recovered scheduled check",)]
    assert notifications == 0


def test_scheduled_outage_recovery_and_content_change_history(scheduler_client, monkeypatch):
    client, database = scheduler_client
    website_id = add_site(client, "https://1.1.1.1/")
    client.put("/api/schedule", json={"enabled": True, "interval_minutes": 1})
    results = iter([
        "Initial readable content",
        webwatch.WebsiteUnreachableError("Simulated connection loss."),
        webwatch.WebsiteUnreachableError("Simulated continued connection loss."),
        "Content after recovery",
        "Ordinary content revision",
    ])
    fetched_urls = []

    def fake_fetch(url):
        fetched_urls.append(url)
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(webwatch, "render_page_text", fake_fetch)

    def run_due_check():
        # Move the temporary test website's own due time into the past.
        with sqlite3.connect(database) as db:
            db.execute("UPDATE websites SET last_checked_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (website_id,))
        return webwatch.run_due_schedule()

    baseline = run_due_check()
    outage = run_due_check()
    repeated_outage = run_due_check()
    recovery = run_due_check()
    ordinary_change = run_due_check()

    assert baseline["successful"] == 1
    assert outage["failed"] == repeated_outage["failed"] == 1
    assert recovery["successful"] == ordinary_change["successful"] == 1
    assert ordinary_change["changed"] == 1
    assert fetched_urls == ["https://1.1.1.1/"] * 5

    events = client.get("/api/notifications?limit=10").get_json()
    assert [event["event_type"] for event in events] == ["recovered", "unreachable"]
    assert all(event["website_id"] == website_id for event in events)
    assert events[0]["confidence"] == "observed"
    assert events[1]["confidence"] == "suspected"
    with sqlite3.connect(database) as db:
        snapshot_count = db.execute(
            "SELECT COUNT(*) FROM snapshots WHERE website_id = ?", (website_id,)
        ).fetchone()[0]
        notification_count = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE website_id = ?", (website_id,)
        ).fetchone()[0]
    assert snapshot_count == 3
    assert notification_count == 2


def test_shared_connectivity_recovery_creates_one_recovery_event(scheduler_client):
    client, _database = scheduler_client
    ids = [add_site(client, f"https://{ip}/") for ip in ("1.1.1.1", "9.9.9.9", "8.8.8.8")]
    with webwatch.get_db() as db:
        sites = [db.execute("SELECT * FROM websites WHERE id=?", (website_id,)).fetchone() for website_id in ids]
        when = webwatch.utc_now()
        for site in sites:
            webwatch.record_connectivity_failure(db, site["id"], when)
            db.execute("UPDATE websites SET availability_state='unreachable' WHERE id=?", (site["id"],))
        events = [webwatch.record_connectivity_recovery(db, site) for site in sites]
    assert sum(event is not None for event in events) == 1
    notifications = client.get("/api/notifications?limit=10").get_json()
    assert [item["event_type"] for item in notifications] == ["recovered"]
    assert "shared connectivity" in notifications[0]["explanation"].lower()


def test_overlapping_scheduled_runs_are_skipped(scheduler_client, monkeypatch):
    client, _database = scheduler_client
    add_site(client, "https://1.1.1.1/")
    client.put("/api/schedule", json={"enabled": True, "interval_minutes": 60})
    entered_check = threading.Event()
    finish_check = threading.Event()

    class FakeResponse:
        status_code = 200

        @staticmethod
        def get_json(silent=True):
            return {"last_check_status": "unchanged"}

    def slow_check(_site_id, wait_for_lock=False):
        entered_check.set()
        assert finish_check.wait(timeout=3)
        return FakeResponse()

    monkeypatch.setattr(webwatch, "check_site", slow_check)
    first_result = {}
    worker = threading.Thread(target=lambda: first_result.setdefault("result", webwatch.run_scheduled_checks()))
    worker.start()
    assert entered_check.wait(timeout=3)
    try:
        assert webwatch.run_scheduled_checks() is None
    finally:
        finish_check.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert first_result["result"]["successful"] == 1


def test_dashboard_has_accessible_schedule_controls(scheduler_client):
    client, _database = scheduler_client
    page = client.get("/")

    assert page.status_code == 200
    assert b'id="schedule-form"' in page.data
    assert b'id="schedule-enabled"' in page.data
    assert b'id="schedule-interval"' in page.data
    assert b"api('/api/schedule')" in client.get("/static/app.js").data
    assert b"method: 'PUT'" in client.get("/static/app.js").data
    assert b'id="website-interval-presets"' in page.data


def test_website_next_check_uses_saved_interval_and_global_scheduler_state(scheduler_client):
    client, database = scheduler_client
    website_id = add_site(client, "https://1.1.1.1/", interval=60)

    disabled_site = client.get("/api/websites").get_json()[0]
    assert disabled_site["automatic_checks_enabled"] is False
    assert disabled_site["next_check_at"] is None
    assert webwatch.next_website_check_at({"last_checked_at": "not-a-time", "check_interval_minutes": 60}) is None

    client.put("/api/schedule", json={"enabled": True, "interval_minutes": 60})
    never_checked = client.get("/api/websites").get_json()[0]
    assert never_checked["automatic_checks_enabled"] is True
    assert never_checked["last_checked_at"] is None
    assert never_checked["next_check_at"] is None  # Due on the scheduler's next poll.

    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE websites SET last_checked_at = ? WHERE id = ?",
            ("2026-10-15T10:00:00+00:00", website_id),
        )
    checked = client.get("/api/websites").get_json()[0]
    assert checked["next_check_at"] == "2026-10-15T11:00:00+00:00"

    updated = client.patch(f"/api/websites/{website_id}/settings", json={
        "display_name": "1.1.1.1", "check_interval_minutes": 15,
    }).get_json()
    assert updated["next_check_at"] == "2026-10-15T10:15:00+00:00"

    client.put("/api/schedule", json={"enabled": False, "interval_minutes": 60})
    disabled_again = client.get("/api/websites").get_json()[0]
    assert disabled_again["automatic_checks_enabled"] is False
    assert disabled_again["next_check_at"] is None


def test_editing_sixty_to_thirty_persists_and_replaces_stale_scheduled_job(scheduler_client):
    client, database = scheduler_client
    website_id = add_site(client, "https://1.1.1.1/", interval=60)
    client.put("/api/schedule", json={"enabled": True, "interval_minutes": 60})
    with sqlite3.connect(database) as db:
        db.execute("UPDATE websites SET last_checked_at = ? WHERE id = ?",
                   ("2030-10-15T10:00:00+00:00", website_id))
        stale_job, _created = webwatch.enqueue_check_job(db, website_id, "scheduled")
    response = client.patch(f"/api/websites/{website_id}/settings", json={
        "display_name": "1.1.1.1", "check_interval_minutes": 30,
    })
    assert response.status_code == 200
    saved = response.get_json()
    assert saved["check_interval_minutes"] == 30
    assert saved["next_check_at"] == "2030-10-15T10:30:00+00:00"
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT check_interval_minutes FROM websites WHERE id = ?",
                          (website_id,)).fetchone() == (30,)
        assert db.execute("SELECT status FROM check_jobs WHERE id = ?", (stale_job,)).fetchone() == ("cancelled",)
    # An obsolete scheduled job cannot bypass the newly saved due time.
    with webwatch.get_db() as db:
        assert webwatch.due_website_rows(db) == []


def test_scheduler_checks_only_due_sites_using_individual_intervals(scheduler_client, monkeypatch):
    client, database = scheduler_client
    soon_due = add_site(client, "https://1.1.1.1/", interval=15)
    later_due = add_site(client, "https://9.9.9.9/", interval=1440)
    demo = client.post("/api/demo").get_json()["id"]
    now = webwatch.utc_now()
    with sqlite3.connect(database) as db:
        db.execute("UPDATE websites SET last_checked_at = ? WHERE id = ?", (now, soon_due))
        db.execute("UPDATE websites SET last_checked_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (later_due,))
        db.execute("UPDATE websites SET last_checked_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (demo,))
    client.put("/api/schedule", json={"enabled": True, "interval_minutes": 60})
    seen = []

    class FakeResponse:
        status_code = 200
        @staticmethod
        def get_json(silent=True):
            return {"last_check_status": "unchanged"}

    monkeypatch.setattr(webwatch, "check_site", lambda site_id, wait_for_lock=False: (seen.append(site_id) or FakeResponse()))
    summary = webwatch.run_scheduled_checks()
    assert seen == [later_due]
    assert summary["total"] == 1
    assert demo not in seen and soon_due not in seen
