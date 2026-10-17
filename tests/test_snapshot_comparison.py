"""Focused tests for the existing Flask snapshot comparison path."""

import sqlite3

import pytest

import app as webwatch
from conftest import setup_test_owner, TEST_SECRET, TEST_SETUP_TOKEN


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Give each test its own small SQLite database."""
    database = tmp_path / "test-webwatch.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    webwatch.init_db()
    with webwatch.app.test_client() as test_client:
        setup_test_owner(test_client)
        yield test_client


def add_test_website(client):
    """Create a record through the normal add route without making a request."""
    response = client.post("/api/websites", json={"url": "https://1.1.1.1/"})
    assert response.status_code == 201
    return response.get_json()["id"]


def test_duplicate_add_returns_existing_row_and_watchlist_stays_intact(client):
    url = "https://1.1.1.1/"
    first = client.post("/api/websites", json={"url": url})
    duplicate = client.post("/api/websites", json={"url": url})

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.get_json()["website"]["id"] == first.get_json()["id"]
    listing = client.get("/api/websites")
    assert listing.status_code == 200
    assert [site["url"] for site in listing.get_json()] == [url]


def test_duplicate_existing_site_remains_visible_alongside_new_site(client):
    existing_url = "https://1.1.1.1/"
    new_url = "https://9.9.9.9/"
    existing = client.post("/api/websites", json={"url": existing_url}).get_json()
    client.post("/api/websites", json={"url": new_url})

    duplicate = client.post("/api/websites", json={"url": existing_url})
    listing = client.get("/api/websites")

    assert duplicate.status_code == 409
    assert duplicate.get_json()["website"]["id"] == existing["id"]
    assert listing.headers["Cache-Control"] == "no-store"
    assert {site["url"] for site in listing.get_json()} == {existing_url, new_url}
    assert len(listing.get_json()) == 2


def test_duplicate_public_urls_use_canonical_host_port_and_fragment(client, monkeypatch):
    monkeypatch.setattr(webwatch.socket, "getaddrinfo", lambda _host, port, type: [
        (None, None, None, None, ("93.184.216.34", port))
    ])
    first = client.post("/api/websites", json={"url": "HTTPS://Example.COM:443/releases#latest"})
    assert first.status_code == 201
    assert first.get_json()["url"] == "https://example.com/releases"
    # Existing databases may contain rows created before URL canonicalization.
    with sqlite3.connect(webwatch.app.config["DATABASE"]) as db:
        db.execute("UPDATE websites SET url = ? WHERE id = ?",
                   ("https://Example.COM:443/releases#legacy", first.get_json()["id"]))
    duplicate = client.post("/api/websites", json={"url": "https://example.com/releases"})
    assert duplicate.status_code == 409
    assert duplicate.get_json()["website"]["id"] == first.get_json()["id"]
    assert len(client.get("/api/websites").get_json()) == 1


def test_add_and_edit_website_name_and_interval(client, monkeypatch):
    monkeypatch.setattr(webwatch, "validated_http_url", lambda value: (value, "example.com", {"93.184.216.34"}))
    response = client.post("/api/websites", json={
        "url": "https://example.com/", "display_name": "Project docs", "check_interval_minutes": 1440,
    })
    assert response.status_code == 201
    site = response.get_json()
    assert site["display_name"] == "Project docs"
    assert site["check_interval_minutes"] == 1440

    updated = client.patch(f"/api/websites/{site['id']}/settings", json={
        "display_name": "Release notes", "check_interval_minutes": 15,
    })
    assert updated.status_code == 200
    assert updated.get_json()["display_name"] == "Release notes"
    assert updated.get_json()["check_interval_minutes"] == 15


def test_main_document_limit_is_per_site_bounded_and_persisted(client):
    site = client.post("/api/websites", json={"url": "https://1.1.1.1/"}).get_json()
    assert site["max_document_size_kib"] == 512

    updated = client.patch(f"/api/websites/{site['id']}/settings", json={
        "display_name": site["display_name"], "check_interval_minutes": 60,
        "max_document_size_kib": 768,
    })
    assert updated.status_code == 200
    assert updated.get_json()["max_document_size_kib"] == 768

    invalid = client.patch(f"/api/websites/{site['id']}/settings", json={
        "display_name": site["display_name"], "check_interval_minutes": 60,
        "max_document_size_kib": 1025,
    })
    assert invalid.status_code == 400
    assert client.get("/api/websites").get_json()[0]["max_document_size_kib"] == 768


def test_history_retention_defaults_and_settings_validation(client):
    site = client.post("/api/websites", json={"url": "https://1.1.1.1/"}).get_json()
    assert site["unchanged_history_limit"] == 200
    assert site["changed_history_limit"] == 400
    assert site["serious_event_limit"] == 600

    settings = {
        "display_name": site["display_name"], "check_interval_minutes": 60,
        "unchanged_history_limit": 3, "changed_history_limit": 5, "serious_event_limit": 7,
    }
    updated = client.patch(f"/api/websites/{site['id']}/settings", json=settings)
    assert updated.status_code == 200
    assert [updated.get_json()[key] for key in (
        "unchanged_history_limit", "changed_history_limit", "serious_event_limit"
    )] == [3, 5, 7]

    for invalid in (0, -1, True, 1.5, "3", 10001):
        rejected = client.patch(
            f"/api/websites/{site['id']}/settings",
            json={**settings, "unchanged_history_limit": invalid},
        )
        assert rejected.status_code == 400
    still_valid = client.get("/api/websites").get_json()[0]
    assert still_valid["unchanged_history_limit"] == 3


def test_history_retention_is_per_site_and_keeps_baseline_and_latest(client):
    first_id = add_test_website(client)
    second_id = client.post("/api/websites", json={"url": "https://9.9.9.9/"}).get_json()["id"]
    with webwatch.get_db() as db:
        for website_id, prefix in ((first_id, "first"), (second_id, "second")):
            version = 0
            for index, record_type in enumerate((
                "baseline", "unchanged", "changed", "unchanged", "changed",
                "unchanged", "changed", "unchanged", "changed",
            )):
                if record_type == "changed":
                    version += 1
                db.execute(
                    "INSERT INTO snapshots (website_id, content_text, checked_at, record_type) "
                    "VALUES (?, ?, ?, ?)",
                    (website_id, f"{prefix} version {version}", f"2026-10-16T00:{index:02d}:00+00:00", record_type),
                )
            for index in range(4):
                db.execute(
                    "INSERT INTO notifications "
                    "(website_id, website_name, website_url, category, event_type, confidence, explanation, occurred_at) "
                    "VALUES (?, ?, ?, 'Availability', 'unreachable', 'suspected', 'fixture', ?)",
                    (website_id, prefix, f"https://{prefix}.example/", f"2026-10-16T00:{index:02d}:00+00:00"),
                )

    response = client.patch(f"/api/websites/{first_id}/settings", json={
        "display_name": "first", "check_interval_minutes": 60,
        "unchanged_history_limit": 2, "changed_history_limit": 3, "serious_event_limit": 2,
    })
    assert response.status_code == 200
    with webwatch.get_db() as db:
        first_types = db.execute(
            "SELECT record_type, COUNT(*) FROM snapshots WHERE website_id = ? GROUP BY record_type",
            (first_id,),
        ).fetchall()
        first_ids = [row[0] for row in db.execute(
            "SELECT id FROM snapshots WHERE website_id = ? ORDER BY id", (first_id,)
        )]
        second_types = db.execute(
            "SELECT record_type, COUNT(*) FROM snapshots WHERE website_id = ? GROUP BY record_type",
            (second_id,),
        ).fetchall()
        first_events = db.execute("SELECT COUNT(*) FROM notifications WHERE website_id = ?", (first_id,)).fetchone()[0]
        second_events = db.execute("SELECT COUNT(*) FROM notifications WHERE website_id = ?", (second_id,)).fetchone()[0]
    assert {row[0]: row[1] for row in first_types} == {"baseline": 1, "changed": 3, "unchanged": 2}
    assert len(first_ids) == 6
    assert first_ids[0] == 1  # The baseline stays available.
    assert first_ids[-1] == 9  # The latest valid snapshot is never pruned.
    assert {row[0]: row[1] for row in second_types} == {"baseline": 1, "changed": 4, "unchanged": 4}
    assert first_events == 2
    assert second_events == 4
    history = client.get(f"/api/websites/{first_id}/history/page?limit=20&offset=0").get_json()["history"]
    changes = client.get(f"/api/websites/{first_id}/history/page?filter=changes").get_json()["history"]
    export = client.get(f"/api/websites/{first_id}/history.json").get_json()["history"]
    assert [entry["status"] for entry in history] == ["baseline", "changed", "unchanged", "changed", "unchanged", "changed"]
    assert len(changes) == 3
    assert len(export) == 6


def test_new_serious_events_obey_per_website_retention(client, monkeypatch):
    fetches = iter([
        "Stable page",
        webwatch.WebsiteUnreachableError("Simulated outage"),
        "Stable page",
        webwatch.WebsiteUnreachableError("Simulated later outage"),
    ])

    def fetch(_url):
        result = next(fetches)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(webwatch, "render_page_text", fetch)
    website_id = add_test_website(client)
    client.patch(f"/api/websites/{website_id}/settings", json={
        "display_name": "Test", "check_interval_minutes": 60,
        "unchanged_history_limit": 200, "changed_history_limit": 400, "serious_event_limit": 1,
    })
    client.post(f"/api/websites/{website_id}/check")
    client.post(f"/api/websites/{website_id}/check")
    client.post(f"/api/websites/{website_id}/check")
    client.post(f"/api/websites/{website_id}/check")

    events = client.get("/api/notifications?limit=10").get_json()
    assert len(events) == 1
    assert events[0]["event_type"] == "unreachable"
    assert events[0]["explanation"]


@pytest.mark.parametrize("interval", [14, 1441, True, 15.5])
def test_website_interval_boundaries_are_enforced(client, monkeypatch, interval):
    monkeypatch.setattr(webwatch, "validated_http_url", lambda value: (value, "example.com", {"93.184.216.34"}))
    response = client.post("/api/websites", json={"url": "https://example.com/", "check_interval_minutes": interval})
    assert response.status_code == 400


def test_additive_migration_keeps_old_site_and_snapshot_data(tmp_path, monkeypatch):
    database = tmp_path / "old-schema.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE websites (id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
              last_checked_at TEXT, last_check_status TEXT NOT NULL DEFAULT 'not_checked', last_changed INTEGER,
              last_error TEXT, is_hidden INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE snapshots (id INTEGER PRIMARY KEY, website_id INTEGER NOT NULL, content_text TEXT NOT NULL, checked_at TEXT NOT NULL);
            CREATE TABLE private_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL);
            INSERT INTO websites (id, url, created_at) VALUES (41, 'https://example.com/', '2026-01-01T00:00:00+00:00');
            INSERT INTO snapshots (id, website_id, content_text, checked_at) VALUES (51, 41, 'kept baseline', '2026-01-01T00:01:00+00:00');
            INSERT INTO snapshots (id, website_id, content_text, checked_at) VALUES (52, 41, 'kept baseline', '2026-01-01T00:02:00+00:00');
            INSERT INTO snapshots (id, website_id, content_text, checked_at) VALUES (53, 41, 'revised baseline', '2026-01-01T00:03:00+00:00');
        """)
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    webwatch.init_db()
    with sqlite3.connect(database) as db:
        row = db.execute(
            "SELECT id, display_name, check_interval_minutes, unchanged_history_limit, "
            "changed_history_limit, serious_event_limit FROM websites"
        ).fetchone()
        document_limit = db.execute(
            "SELECT max_document_size_kib FROM websites WHERE id = 41"
        ).fetchone()[0]
        snapshot = db.execute("SELECT id, website_id, content_text, record_type FROM snapshots").fetchone()
        record_types = db.execute("SELECT id, record_type FROM snapshots ORDER BY id").fetchall()
        tables = {entry[0] for entry in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert row == (41, "example.com", 60, 200, 400, 600)
    assert document_limit == 512
    assert snapshot == (51, 41, "kept baseline", "baseline")
    assert record_types == [(51, "baseline"), (52, "unchanged"), (53, "changed")]
    assert "private_settings" in tables


def test_watchlist_search_and_status_filters_return_matching_sites(client, monkeypatch):
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: "A real baseline")
    first_id = add_test_website(client)
    second = client.post("/api/websites", json={"url": "https://9.9.9.9/"})
    assert second.status_code == 201

    checked = client.post(f"/api/websites/{first_id}/check")
    assert checked.status_code == 200
    baseline = client.get("/api/websites?q=1.1.1.1&status=baseline")
    unchecked = client.get("/api/websites?status=not_checked")

    assert [site["id"] for site in baseline.get_json()] == [first_id]
    assert baseline.headers["X-Total-Count"] == "2"
    assert [site["url"] for site in unchecked.get_json()] == ["https://9.9.9.9/"]
    assert client.get("/api/websites?status=made-up").status_code == 400
    assert client.get(f"/api/websites?q={'x' * 121}").status_code == 400


def test_dashboard_includes_demo_control_and_loadable_frontend_assets(client):
    page = client.get("/")
    script = client.get("/static/app.js")

    assert page.status_code == 200
    assert b'id="start-demo"' in page.data
    assert b'id="check-all"' in page.data
    assert b'id="check-all-meter"' in page.data
    assert b'id="website-search"' in page.data
    assert b'id="status-filter"' in page.data
    assert b'id="hidden-view-toggle"' in page.data
    assert b'id="notification-total-count"' in page.data
    assert b'id="notification-unread-count"' in page.data
    assert b'aria-label="Open Hidden Websites"' in page.data
    assert b'href="/hidden"' not in page.data
    assert b"data-view=" not in page.data
    assert client.get("/hidden").status_code == 404
    assert b"url_for('static', filename='app.js')" not in page.data
    assert b"/static/app.js" in page.data
    assert page.headers["Cache-Control"] == "no-store"
    assert script.status_code == 200
    assert b"/api/demo" in script.data
    assert b"history.json" in script.data
    assert b"/api/websites/${id}/hidden" in script.data
    assert b"params.set('hidden', String(requestedHiddenView))" in script.data
    assert b"async function switchWebsiteView()" in script.data
    assert b"hiddenViewToggle.addEventListener('click', switchWebsiteView)" in script.data
    assert b"window.location" not in script.data
    assert b"api('/api/websites?hidden=false')" in script.data
    assert b"api('/api/websites?hidden=true')" in script.data
    assert b"if (!site.is_demo) eligibleById.set(site.id, site)" in script.data
    assert b"/api/websites/${site.id}/check" in script.data
    assert b"checkAllButton.addEventListener('click', checkAllWebsites)" in script.data
    assert b"checkAllRunning" in script.data
    assert b"changed websites" in script.data
    assert b"recordSeriousNotification(error.data?.notification)" in script.data
    assert b"Notification.requestPermission()" in script.data
    assert b"browserNotificationButton.addEventListener('click', requestBrowserNotifications)" in script.data
    assert b"api('/api/notifications?limit=100')" in script.data
    assert b"whole render stays within 8 MiB and 100 requests" in script.data
    assert b"notifyNew: !firstScheduleObservation" in script.data
    assert b"unseenEntries.reverse().forEach((entry) => showNotificationToast(entry))" in script.data
    assert b"window.isSecureContext === false" in script.data
    assert b"webwatch.notifications.read.v1" in script.data
    assert b"aria-controls" in script.data and b"notification-details-" in script.data
    assert b"markNotificationRead(event.id)" in script.data
    assert page.data.index(b'id="notification-heading"') < page.data.index(b'id="watchlist-heading"')
    assert page.data.index(b'id="watchlist-heading"') < page.data.index(b'id="schedule-heading"')
    assert b"Unhide" in script.data
    assert b"/api/private/" not in script.data
    assert b"checks excluded from these totals" in script.data
    assert script.headers["Cache-Control"] == "no-store"
    assert client.get("/api/websites").headers["Cache-Control"] == "no-store"


def test_notification_endpoint_separates_total_from_recent_limit(client, monkeypatch):
    results = iter(["Reachable content", webwatch.WebsiteUnreachableError("Connection refused.")])

    def fake_fetch(_url):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(webwatch, "render_page_text", fake_fetch)
    website_id = add_test_website(client)
    client.post(f"/api/websites/{website_id}/check")
    client.post(f"/api/websites/{website_id}/check")

    response = client.get("/api/notifications?limit=1")
    assert response.status_code == 200
    assert len(response.get_json()) == 1
    assert response.headers["X-Total-Count"] == "1"


def stored_snapshots(database_path, website_id):
    """Read saved snapshot text for a test assertion."""
    with sqlite3.connect(database_path) as db:
        return db.execute(
            "SELECT content_text FROM snapshots WHERE website_id = ? ORDER BY id",
            (website_id,),
        ).fetchall()


def test_first_successful_check_saves_baseline(client, tmp_path, monkeypatch):
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: "First page text")
    website_id = add_test_website(client)

    response = client.post(f"/api/websites/{website_id}/check")

    result = response.get_json()
    assert response.status_code == 200
    assert result["last_check_status"] == "baseline"
    assert result["last_changed"] is None
    assert result["last_checked_at"]
    assert stored_snapshots(tmp_path / "test-webwatch.sqlite3", website_id) == [("First page text",)]


def test_identical_successful_check_reports_no_change_without_duplicate_snapshot(client, tmp_path, monkeypatch):
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: "Same page text")
    website_id = add_test_website(client)

    first = client.post(f"/api/websites/{website_id}/check").get_json()
    second = client.post(f"/api/websites/{website_id}/check").get_json()

    assert first["last_check_status"] == "baseline"
    assert second["last_check_status"] == "unchanged"
    assert second["last_changed"] == 0
    assert stored_snapshots(tmp_path / "test-webwatch.sqlite3", website_id) == [("Same page text",)]
    assert client.get("/api/stats").get_json()["successful_checks"] == 2


def test_whitespace_only_difference_reports_no_change(client, monkeypatch):
    page_texts = iter(["A page\nwith   normal text.", "A   page with\nnormal text."])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(page_texts))
    website_id = add_test_website(client)

    client.post(f"/api/websites/{website_id}/check")
    second = client.post(f"/api/websites/{website_id}/check").get_json()

    assert second["last_check_status"] == "unchanged"
    assert second["last_changed"] == 0
    assert second["added_content"] == ""
    assert second["removed_content"] == ""


def test_comparison_preserves_case_and_punctuation():
    same, added, removed = webwatch.compare_snapshot_text("Policy is active.", "policy is active!")

    assert not same
    assert added == "policy active!"
    assert removed == "Policy active."


def test_change_sensitivity_filters_only_configured_display_noise():
    previous = "Policy is active. 12 views. Published 4 minutes ago."
    current = "Policy is active. 38 views. Published just now."

    assert webwatch.compare_snapshot_text(previous, current, "normal")[0] is False
    assert webwatch.compare_snapshot_text(previous, current, "low")[0] is True
    assert webwatch.compare_snapshot_text(previous, current, "high")[0] is False
    assert webwatch.snapshot_content_hash(previous, "low") == webwatch.snapshot_content_hash(current, "low")
    assert webwatch.snapshot_content_hash(previous, "normal") != webwatch.snapshot_content_hash(current, "normal")


def test_sensitivity_is_per_website_setting_and_validated(client):
    first = client.post("/api/websites", json={"url": "https://1.1.1.1/"}).get_json()
    second = client.post("/api/websites", json={"url": "https://9.9.9.9/"}).get_json()
    assert first["change_sensitivity"] == "normal"
    assert second["change_sensitivity"] == "normal"

    updated = client.patch(f"/api/websites/{first['id']}/settings", json={
        "display_name": first["display_name"], "check_interval_minutes": 60,
        "change_sensitivity": "low",
    })
    assert updated.status_code == 200
    assert updated.get_json()["change_sensitivity"] == "low"
    listed = {site["id"]: site["change_sensitivity"] for site in client.get("/api/websites").get_json()}
    assert listed == {first["id"]: "low", second["id"]: "normal"}

    rejected = client.patch(f"/api/websites/{first['id']}/settings", json={
        "display_name": first["display_name"], "check_interval_minutes": 60,
        "change_sensitivity": "sometimes",
    })
    assert rejected.status_code == 400
    listed = {site["id"]: site["change_sensitivity"] for site in client.get("/api/websites").get_json()}
    assert listed[first["id"]] == "low"


def test_changing_sensitivity_does_not_compare_incompatible_saved_hashes(client, monkeypatch):
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: "News update, published 4 minutes ago.")
    site = client.post("/api/websites", json={"url": "https://1.1.1.1/"}).get_json()
    client.post(f"/api/websites/{site['id']}/check")
    changed = client.patch(f"/api/websites/{site['id']}/settings", json={
        "display_name": site["display_name"], "check_interval_minutes": 60,
        "change_sensitivity": "low",
    })
    assert changed.status_code == 200

    second = client.post(f"/api/websites/{site['id']}/check").get_json()
    assert second["last_check_status"] == "unchanged"
    with webwatch.get_db() as db:
        snapshot = db.execute(
            "SELECT content_hash, hash_sensitivity, hash_version FROM snapshots WHERE website_id = ?",
            (site["id"],),
        ).fetchone()
    assert snapshot["hash_sensitivity"] == "low"
    assert snapshot["hash_version"] == webwatch.SNAPSHOT_HASH_VERSION


@pytest.mark.parametrize("sensitivity, expected_status, expected_snapshots", [
    ("normal", "changed", 2),
    ("low", "unchanged", 1),
    ("high", "changed", 2),
])
def test_check_hashes_versions_and_deduplicates_configured_display_noise(
    client, monkeypatch, tmp_path, sensitivity, expected_status, expected_snapshots,
):
    page_texts = iter([
        "Announcement: enrollment is open. 12 views. Updated 4 minutes ago.",
        "Announcement: enrollment is open. 38 views. Updated just now.",
    ])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(page_texts))
    website_id = add_test_website(client)
    settings = client.get("/api/websites").get_json()[0]
    updated = client.patch(f"/api/websites/{website_id}/settings", json={
        "display_name": settings["display_name"], "check_interval_minutes": 60,
        "change_sensitivity": sensitivity,
    })
    assert updated.status_code == 200

    client.post(f"/api/websites/{website_id}/check")
    second = client.post(f"/api/websites/{website_id}/check").get_json()

    assert second["last_check_status"] == expected_status
    with webwatch.get_db() as db:
        rows = db.execute(
            "SELECT content_text, content_hash FROM snapshots WHERE website_id = ? ORDER BY id",
            (website_id,),
        ).fetchall()
    assert len(rows) == expected_snapshots
    assert all(row["content_hash"] and len(row["content_hash"]) == 64 for row in rows)
    # The saved baseline retains raw extracted text even when Low treats its
    # changing display details as noise.
    assert "12 views" in rows[0]["content_text"]


def test_screenshots_are_saved_per_content_version_and_missing_images_are_reported(client, monkeypatch):
    captures = iter([
        webwatch.RenderedPageText("Version one", b"jpeg-one"),
        webwatch.RenderedPageText("Version one", None, screenshot_error="unchanged version screenshot unavailable"),
        webwatch.RenderedPageText("Version two", None, screenshot_error="capture failed"),
    ])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(captures))
    website_id = add_test_website(client)

    first = client.post(f"/api/websites/{website_id}/check").get_json()
    same = client.post(f"/api/websites/{website_id}/check").get_json()
    changed = client.post(f"/api/websites/{website_id}/check").get_json()
    with webwatch.get_db() as db:
        rows = db.execute(
            "SELECT id, content_text, screenshot, screenshot_error FROM snapshots "
            "WHERE website_id = ? ORDER BY id", (website_id,),
        ).fetchall()

    assert first["last_check_status"] == "baseline"
    assert same["last_check_status"] == "unchanged"
    assert same["last_screenshot_warning"] == "unchanged version screenshot unavailable"
    assert changed["last_check_status"] == "changed"
    assert len(rows) == 2
    assert rows[0]["screenshot"] == b"jpeg-one"
    assert rows[1]["screenshot"] is None
    assert rows[1]["screenshot_error"] == "capture failed"
    image = client.get(f"/api/websites/{website_id}/snapshots/{rows[0]['id']}/screenshot")
    assert image.status_code == 200 and image.data == b"jpeg-one"
    missing = client.get(f"/api/websites/{website_id}/snapshots/{rows[1]['id']}/screenshot")
    assert missing.status_code == 404 and missing.get_json()["error"] == "capture failed"


def test_saved_version_comparison_includes_text_and_screenshot_availability(client, monkeypatch):
    page_texts = iter([
        webwatch.RenderedPageText("Policy is open", b"older image"),
        webwatch.RenderedPageText("Policy is closed", b"newer image"),
    ])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(page_texts))
    website_id = add_test_website(client)
    client.post(f"/api/websites/{website_id}/check")
    client.post(f"/api/websites/{website_id}/check")
    with webwatch.get_db() as db:
        ids = [row[0] for row in db.execute(
            "SELECT id FROM snapshots WHERE website_id = ? ORDER BY id", (website_id,)
        )]

    comparison = client.get(f"/api/websites/{website_id}/compare?older={ids[0]}&newer={ids[1]}")
    assert comparison.status_code == 200
    payload = comparison.get_json()
    assert payload["same"] is False
    assert payload["added_content"] == "closed"
    assert payload["removed_content"] == "open"
    assert payload["older"]["has_screenshot"] is True
    assert payload["newer"]["has_screenshot"] is True
    assert client.get(f"/api/websites/{website_id}/compare?older={ids[0]}&newer={ids[0]}").status_code == 400
    assert client.get(f"/api/websites/{website_id}/compare?older=999&newer={ids[1]}").status_code == 404


def test_selector_rules_persist_and_are_applied_to_the_normal_check_path(client, monkeypatch):
    website_id = add_test_website(client)
    saved = client.put(f"/api/websites/{website_id}/selectors", json={
        "watched": ["main article"], "ignored": [".advertisement"],
    })
    assert saved.status_code == 200
    assert saved.get_json()["watched_selectors"] == ["main article"]
    assert saved.get_json()["ignored_selectors"] == [".advertisement"]
    received = []

    def render(_url, selectors=None):
        received.append(selectors)
        return webwatch.RenderedPageText(
            "Watched article text", b"screen", selector_warnings=["ignored selector no longer matches"],
        )

    monkeypatch.setattr(webwatch, "render_page_text", render)
    result = client.post(f"/api/websites/{website_id}/check").get_json()
    assert received == [{"watched": ["main article"], "ignored": [".advertisement"], "numeric": []}]
    assert result["last_check_status"] == "baseline"
    assert result["last_selector_warning"] == ["ignored selector no longer matches"]
    with webwatch.get_db() as db:
        snapshot = db.execute(
            "SELECT content_text, screenshot FROM snapshots WHERE website_id = ?", (website_id,)
        ).fetchone()
    assert tuple(snapshot) == ("Watched article text", b"screen")


def test_changing_monitored_regions_establishes_a_new_baseline_not_a_false_change(client, monkeypatch):
    website_id = add_test_website(client)
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: "Full page including unrelated footer")
    first = client.post(f"/api/websites/{website_id}/check").get_json()
    assert first["last_check_status"] == "baseline"
    client.put(f"/api/websites/{website_id}/selectors", json={
        "watched": ["main article"], "ignored": [],
    })
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url, selectors=None: "Watched article only")

    second = client.post(f"/api/websites/{website_id}/check").get_json()
    history = client.get(f"/api/websites/{website_id}/history").get_json()
    assert second["last_check_status"] == "baseline"
    assert second["last_changed_at"] is None
    assert [entry["status"] for entry in history] == ["baseline", "baseline"]


def test_invalid_or_stale_watched_selector_is_reported_without_snapshot_or_crash(client, monkeypatch):
    website_id = add_test_website(client)
    saved = client.put(f"/api/websites/{website_id}/selectors", json={
        "watched": ["[broken"], "ignored": [],
    })
    assert saved.status_code == 200

    def render(_url, selectors=None):
        assert selectors["watched"] == ["[broken"]
        raise webwatch.WebsiteSelectorError("Invalid watched CSS selector was skipped: [broken")

    monkeypatch.setattr(webwatch, "render_page_text", render)
    response = client.post(f"/api/websites/{website_id}/check")
    assert response.status_code == 502
    assert response.get_json()["category"] == "selector_error"
    with webwatch.get_db() as db:
        site = db.execute(
            "SELECT last_check_status, last_check_category, last_selector_warning FROM websites WHERE id = ?",
            (website_id,),
        ).fetchone()
        count = db.execute("SELECT COUNT(*) FROM snapshots WHERE website_id = ?", (website_id,)).fetchone()[0]
    assert tuple(site[:2]) == ("error", "selector_error")
    assert "Invalid watched CSS selector" in site[2]
    assert count == 0


def test_selector_preview_uses_rendered_result_without_saving_a_version(client, monkeypatch):
    website_id = add_test_website(client)
    captured = []

    def render(url, selectors=None, preview=False):
        captured.append((url, selectors, preview))
        return webwatch.RenderedPageText(
            "Preview text", b"preview-image", regions=[{"selector": "main", "label": "Main"}],
        )

    monkeypatch.setattr(webwatch, "render_page_text", render)
    response = client.post(f"/api/websites/{website_id}/preview")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["screenshot"].startswith("data:image/jpeg;base64,")
    assert payload["regions"] == [{"selector": "main", "label": "Main"}]
    assert captured == [("https://1.1.1.1/", {"watched": [], "ignored": []}, True)]
    with webwatch.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM snapshots WHERE website_id = ?", (website_id,)).fetchone()[0] == 0


def test_selector_api_limits_input_and_keeps_existing_rules(client):
    website_id = add_test_website(client)
    saved = client.put(f"/api/websites/{website_id}/selectors", json={"watched": ["main"], "ignored": []})
    assert saved.status_code == 200
    invalid = client.put(f"/api/websites/{website_id}/selectors", json={
        "watched": ["main"] * (webwatch.MAX_REGION_SELECTORS + 1), "ignored": [],
    })
    assert invalid.status_code == 400
    site = client.get("/api/websites").get_json()[0]
    assert site["watched_selectors"] == ["main"]


def test_timeout_and_captcha_recover_against_last_good_content(client, monkeypatch):
    results = iter([
        webwatch.RenderedPageText("Known good text", b"first image"),
        webwatch.WebsiteTimeoutError("render timeout"),
        webwatch.WebsiteCaptchaError("CAPTCHA challenge; no snapshot was saved."),
        webwatch.RenderedPageText("A genuine content update", b"new image"),
    ])

    def render(_url):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(webwatch, "render_page_text", render)
    website_id = add_test_website(client)
    baseline = client.post(f"/api/websites/{website_id}/check").get_json()
    timeout = client.post(f"/api/websites/{website_id}/check")
    captcha = client.post(f"/api/websites/{website_id}/check")
    changed = client.post(f"/api/websites/{website_id}/check").get_json()
    assert baseline["last_check_status"] == "baseline"
    assert timeout.get_json()["category"] == "timeout"
    assert captcha.get_json()["category"] == "captcha"
    assert changed["last_check_status"] == "changed"
    with webwatch.get_db() as db:
        rows = db.execute(
            "SELECT content_text FROM snapshots WHERE website_id = ? ORDER BY id", (website_id,)
        ).fetchall()
        site = db.execute(
            "SELECT last_check_category, last_changed_at FROM websites WHERE id = ?", (website_id,)
        ).fetchone()
    assert [row[0] for row in rows] == ["Known good text", "A genuine content update"]
    assert site["last_check_category"] == "changed"
    assert site["last_changed_at"] is not None


@pytest.mark.parametrize("failure, category, display_status", [
    (webwatch.WebsiteUnreachableError("connection refused"), "network_error", "error"),
    (webwatch.WebsiteTimeoutError("request timed out"), "timeout", "error"),
    (webwatch.WebsiteHTTPError("The webpage returned HTTP 503."), "http_error", "error"),
    (webwatch.WebsiteRateLimitedError("HTTP 429"), "rate_limited", "rate_limited"),
    (webwatch.WebsiteBlockedError("HTTP 403"), "blocked", "blocked"),
    (webwatch.WebsiteCaptchaError("CAPTCHA challenge"), "captcha", "captcha"),
    (webwatch.WebsiteResourceLimitError("The page requested too many resources."), "render_error", "error"),
])
def test_failure_types_have_distinct_latest_categories_and_keep_snapshot(client, monkeypatch, failure, category, display_status):
    values = iter([webwatch.RenderedPageText("Known good", b"baseline"), failure])

    def render(_url):
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(webwatch, "render_page_text", render)
    website_id = add_test_website(client)
    client.post(f"/api/websites/{website_id}/check")
    failed = client.post(f"/api/websites/{website_id}/check")
    assert failed.get_json()["category"] == category
    if isinstance(failure, webwatch.WebsiteResourceLimitError):
        assert "retryable" not in failed.get_json()
    with webwatch.get_db() as db:
        site = db.execute(
            "SELECT last_check_status, last_check_category FROM websites WHERE id = ?", (website_id,)
        ).fetchone()
        snapshots = db.execute(
            "SELECT content_text FROM snapshots WHERE website_id = ?", (website_id,)
        ).fetchall()
        job = db.execute(
            "SELECT status FROM check_jobs WHERE website_id = ? ORDER BY id DESC LIMIT 1",
            (website_id,),
        ).fetchone()
    assert tuple(site) == (display_status, category)
    assert [row[0] for row in snapshots] == ["Known good"]
    if isinstance(failure, webwatch.WebsiteResourceLimitError):
        assert job["status"] == "failed"


def test_long_change_details_are_bounded_for_history_responses():
    added, removed, truncated = webwatch.displayable_diff("new " * 2000, "old " * 2000)

    assert len(added) == webwatch.MAX_DIFF_DISPLAY_CHARS
    assert len(removed) == webwatch.MAX_DIFF_DISPLAY_CHARS
    assert truncated


def test_different_successful_check_reports_content_changed(client, monkeypatch):
    page_texts = iter(["Original text", "Updated text"])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(page_texts))
    website_id = add_test_website(client)

    client.post(f"/api/websites/{website_id}/check")
    second = client.post(f"/api/websites/{website_id}/check").get_json()

    assert second["last_check_status"] == "changed"
    assert second["last_changed"] == 1
    assert second["added_content"] == "Updated"
    assert second["removed_content"] == "Original"
    assert "notification" not in second
    assert client.get("/api/notifications").get_json() == []


def test_dynamic_time_and_score_changes_are_annotated_but_preserved(client, monkeypatch):
    page_texts = iter([
        "Match update 3-2. Published 4 minutes ago. Season 2026 attendance 12 thousand.",
        "Match update 4-2. Published just now. Season 2026 attendance 15 thousand.",
    ])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(page_texts))
    website_id = add_test_website(client)

    client.post(f"/api/websites/{website_id}/check")
    changed = client.post(f"/api/websites/{website_id}/check").get_json()
    history = client.get(f"/api/websites/{website_id}/history").get_json()

    assert changed["last_check_status"] == "changed"
    assert changed["volatile_hints"] == ["relative_time", "score_like"]
    assert "4-2" in changed["added_content"] and "3-2" in changed["removed_content"]
    assert "just now" in changed["added_content"] and "4 minutes ago" in changed["removed_content"]
    assert "15" in changed["added_content"] and "12" in changed["removed_content"]
    assert history[1]["volatile_hints"] == ["relative_time", "score_like"]
    assert client.get("/api/notifications").get_json() == []


def test_meaningful_numeric_changes_remain_changes_without_volatile_hint(client, monkeypatch):
    page_texts = iter(["Report year 2026 revenue 12 million", "Report year 2026 revenue 15 million"])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(page_texts))
    website_id = add_test_website(client)

    client.post(f"/api/websites/{website_id}/check")
    changed = client.post(f"/api/websites/{website_id}/check").get_json()

    assert changed["last_check_status"] == "changed"
    assert changed["added_content"] == "15"
    assert changed["removed_content"] == "12"
    assert changed["volatile_hints"] == []


def test_reachable_to_unreachable_transition_is_persisted_once(client, tmp_path, monkeypatch):
    results = iter(["Reachable baseline", webwatch.WebsiteUnreachableError("Connection refused."), webwatch.WebsiteUnreachableError("Connection refused again.")])

    def fake_fetch(_url):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(webwatch, "render_page_text", fake_fetch)
    website_id = add_test_website(client)
    baseline = client.post(f"/api/websites/{website_id}/check").get_json()
    first_failure = client.post(f"/api/websites/{website_id}/check")
    repeated_failure = client.post(f"/api/websites/{website_id}/check")

    assert baseline["last_check_status"] == "baseline"
    assert first_failure.status_code == 502
    event = first_failure.get_json()["notification"]
    assert event["event_type"] == "unreachable"
    assert event["category"] == "Availability"
    assert event["confidence"] == "suspected"
    assert "does not confirm the site is down" in event["explanation"]
    assert "notification" not in repeated_failure.get_json()
    assert client.get("/api/notifications").get_json() == [event]
    with sqlite3.connect(tmp_path / "test-webwatch.sqlite3") as db:
        assert db.execute("SELECT availability_state FROM websites WHERE id = ?", (website_id,)).fetchone() == ("unreachable",)
        assert db.execute("SELECT COUNT(*) FROM snapshots WHERE website_id = ?", (website_id,)).fetchone() == (1,)


def test_recovery_and_recurring_outage_each_create_one_history_event(client, tmp_path, monkeypatch):
    results = iter([
        "Initial content",
        webwatch.WebsiteUnreachableError("Connection refused."),
        "Recovered content",
        "Recovered content",
        webwatch.WebsiteUnreachableError("Connection refused again."),
    ])

    def fake_fetch(_url):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(webwatch, "render_page_text", fake_fetch)
    website_id = add_test_website(client)
    client.post(f"/api/websites/{website_id}/check")
    outage = client.post(f"/api/websites/{website_id}/check")
    recovered = client.post(f"/api/websites/{website_id}/check")
    stable = client.post(f"/api/websites/{website_id}/check")
    recurring_outage = client.post(f"/api/websites/{website_id}/check")
    events = client.get("/api/notifications?limit=10").get_json()

    assert outage.get_json()["notification"]["event_type"] == "unreachable"
    assert recovered.status_code == 200
    assert recovered.get_json()["notification"]["event_type"] == "recovered"
    assert recovered.get_json()["notification"]["confidence"] == "observed"
    assert "notification" not in stable.get_json()
    assert recurring_outage.get_json()["notification"]["event_type"] == "unreachable"
    assert [event["event_type"] for event in events] == ["unreachable", "recovered", "unreachable"]
    assert all(event["website_id"] == website_id and event["website_name"] == "1.1.1.1" for event in events)
    with sqlite3.connect(tmp_path / "test-webwatch.sqlite3") as db:
        assert db.execute("SELECT availability_state FROM websites WHERE id = ?", (website_id,)).fetchone() == ("unreachable",)
        assert db.execute("SELECT COUNT(*) FROM snapshots WHERE website_id = ?", (website_id,)).fetchone() == (2,)


def test_http_error_and_read_timeout_are_not_classified_as_unreachable_or_security_incident(client, monkeypatch):
    results = iter([
        "Page content",
        ValueError("The webpage returned HTTP 503."),
        ValueError("The webpage took too long to respond."),
        "Page content again",
    ])

    def fake_fetch(_url):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(webwatch, "render_page_text", fake_fetch)
    website_id = add_test_website(client)
    client.post(f"/api/websites/{website_id}/check")
    failed = client.post(f"/api/websites/{website_id}/check")
    timed_out = client.post(f"/api/websites/{website_id}/check")
    recovered_http = client.post(f"/api/websites/{website_id}/check")

    assert failed.status_code == 502
    assert "notification" not in failed.get_json()
    assert timed_out.status_code == 502
    assert "notification" not in timed_out.get_json()
    assert "notification" not in recovered_http.get_json()
    assert client.get("/api/notifications").get_json() == []


def test_history_lists_baseline_and_changes_chronologically(client, monkeypatch):
    page_texts = iter(["A stable heading", "A revised heading"])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(page_texts))
    website_id = add_test_website(client)
    client.post(f"/api/websites/{website_id}/check")
    client.post(f"/api/websites/{website_id}/check")

    response = client.get(f"/api/websites/{website_id}/history")
    history = response.get_json()

    assert response.status_code == 200
    assert [entry["status"] for entry in history] == ["baseline", "changed"]
    assert history[0]["checked_at"] < history[1]["checked_at"] or history[0]["id"] < history[1]["id"]
    assert history[1]["added_content"] == "revised"
    assert history[1]["removed_content"] == "stable"


def test_history_pages_are_bounded_and_change_filter_uses_real_comparisons(client):
    website_id = add_test_website(client)
    with webwatch.get_db() as db:
        for number in range(23):
            db.execute(
                "INSERT INTO snapshots (website_id, content_text, checked_at) VALUES (?, ?, ?)",
                (website_id, f"Page revision {number}", f"2026-10-{(number // 24) + 1:02d}T{number % 24:02d}:00:00+00:00"),
            )
    newest = client.get(f"/api/websites/{website_id}/history/page?limit=20&offset=0")
    first_page = newest.get_json()
    assert newest.status_code == 200
    assert len(first_page["history"]) == 20
    assert first_page["has_more"] is True
    assert first_page["history"][-1]["added_content"] == "22"
    older = client.get(f"/api/websites/{website_id}/history/page?limit=20&offset=20").get_json()
    assert len(older["history"]) == 3
    assert older["history"][0]["status"] == "baseline"
    changes = client.get(f"/api/websites/{website_id}/history/page?filter=changes").get_json()
    assert len(changes["history"]) == 20
    assert all(entry["status"] == "changed" for entry in changes["history"])


def test_empty_history_is_successful_and_missing_history_ids_return_json_404(client):
    website_id = add_test_website(client)

    empty = client.get(f"/api/websites/{website_id}/history/page?offset=0&filter=all")
    missing = client.get("/api/websites/99999/history/page?offset=0&filter=all")
    stale_route = client.get(f"/api/websites/{website_id}/history/pgae")

    assert empty.status_code == 200
    assert empty.get_json() == {"history": [], "has_more": False, "next_offset": 20}
    assert missing.status_code == 404
    assert missing.get_json()["error"] == "Website not found."
    assert stale_route.status_code == 404
    assert "API endpoint not found" in stale_route.get_json()["error"]


def test_history_page_returns_snapshot_json_and_deleted_site_json_error(client):
    website_id = add_test_website(client)
    with webwatch.get_db() as db:
        db.executemany(
            "INSERT INTO snapshots (website_id, content_text, checked_at) VALUES (?, ?, ?)",
            [
                (website_id, "IndiaTV baseline", "2026-10-15T10:00:00+00:00"),
                (website_id, "IndiaTV revised headline", "2026-10-15T11:00:00+00:00"),
            ],
        )

    response = client.get(f"/api/websites/{website_id}/history/page?offset=0&filter=all")
    assert response.status_code == 200
    assert response.is_json
    assert [item["status"] for item in response.get_json()["history"]] == ["baseline", "changed"]
    assert response.get_json()["history"][1]["checked_at"] == "2026-10-15T11:00:00+00:00"

    assert client.delete(f"/api/websites/{website_id}").status_code == 200
    deleted = client.get(f"/api/websites/{website_id}/history/page?offset=0&filter=all")
    assert deleted.status_code == 404
    assert deleted.get_json() == {"error": "Website not found."}


def test_history_query_failure_returns_json_error_instead_of_flask_html(client, monkeypatch):
    website_id = add_test_website(client)

    def fail_history_query(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated schema/query failure")

    monkeypatch.setattr(webwatch, "history_page_for_website", fail_history_query)
    response = client.get(f"/api/websites/{website_id}/history/page?offset=0&filter=all")

    assert response.status_code == 500
    assert response.is_json
    assert response.get_json() == {"error": "Could not load this website's history from the database."}


def test_html_history_export_escapes_user_supplied_website_name(client, monkeypatch):
    monkeypatch.setattr(webwatch, "validated_http_url", lambda value: (value, "example.com", {"93.184.216.34"}))
    created = client.post("/api/websites", json={
        "url": "https://example.com/", "display_name": "<script>alert(1)</script>",
    })
    website_id = created.get_json()["id"]
    response = client.get(f"/api/websites/{website_id}/history.html")
    assert response.status_code == 200
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in response.data
    assert b"<script>alert(1)</script>" not in response.data


def test_confirmed_http_denial_is_blocked_and_never_saved_as_page_text(client, monkeypatch):
    website_id = add_test_website(client)
    async def denied_fetch(*_args, **_kwargs):
        from async_public_fetch import PublicHTTPError
        raise PublicHTTPError(403)

    monkeypatch.setattr(webwatch, "fetch_public_resource_async", denied_fetch)
    result = client.post(f"/api/websites/{website_id}/check")
    assert result.status_code == 502
    assert "HTTP 403" in result.get_json()["error"]
    assert "denied this check" in result.get_json()["error"]
    with webwatch.get_db() as db:
        site = db.execute("SELECT last_check_status, last_error FROM websites WHERE id = ?", (website_id,)).fetchone()
        assert db.execute("SELECT COUNT(*) FROM snapshots WHERE website_id = ?", (website_id,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM notifications WHERE website_id = ?", (website_id,)).fetchone()[0] == 0
    assert site[0] == "blocked"


@pytest.mark.parametrize("html", [
    b"<html><head><title>Just a moment...</title></head><body>"
    b"<h1>Checking your browser before accessing</h1>"
    b"<div id='cf-chl-widget'>Please enable JavaScript and cookies to continue.</div>"
    b"</body></html>",
    b"<html><head><title>503 Service Unavailable</title></head><body>"
    b"The service is temporarily unavailable. Try again later. Request ID: abc123"
    b"</body></html>",
])
def test_http_200_challenge_or_error_page_does_not_replace_good_snapshot(client, monkeypatch, html):
    website_id = add_test_website(client)
    monkeypatch.setattr(webwatch, "validated_http_url", lambda _url: (
        "https://1.1.1.1/", "1.1.1.1", {"1.1.1.1"}
    ))

    with webwatch.get_db() as db:
        db.execute(
            "INSERT INTO snapshots (website_id, content_text, checked_at) VALUES (?, ?, ?)",
            (website_id, "Known good page", webwatch.utc_now()),
        )

    class PageResponse:
        status_code = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            assert chunk_size == 16384
            yield html

    class PageSession:
        trust_env = False
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def mount(self, *_args): pass
        def get(self, *_args, **_kwargs): return PageResponse()

    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: (_ for _ in ()).throw(
        webwatch.WebsiteBlockedError(webwatch.rejected_page_reason(html))
    ))
    result = client.post(f"/api/websites/{website_id}/check")

    assert result.status_code == 502
    assert "No page snapshot was saved" in result.get_json()["error"]
    with webwatch.get_db() as db:
        site = db.execute(
            "SELECT last_check_status, last_error FROM websites WHERE id = ?", (website_id,)
        ).fetchone()
        snapshots = db.execute(
            "SELECT content_text FROM snapshots WHERE website_id = ? ORDER BY id", (website_id,)
        ).fetchall()
    assert site[0] == "blocked"
    assert [row[0] for row in snapshots] == ["Known good page"]


def test_legitimate_article_mentioning_captcha_is_not_rejected():
    article = b"""<html><head><title>How browser challenges work</title></head><body>
      <h1>Why websites ask visitors to verify they are human</h1>
      <p>Some websites use CAPTCHA checks. A challenge may show text such as
      checking your browser before accessing a page. This article explains the
      user experience and does not present an actual challenge.</p>
    </body></html>"""

    assert webwatch.rejected_page_reason(article) is None


def test_history_json_export_contains_saved_comparisons_and_download_headers(client, monkeypatch):
    page_texts = iter(["Before export", "After export"])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(page_texts))
    website_id = add_test_website(client)
    client.post(f"/api/websites/{website_id}/check")
    client.post(f"/api/websites/{website_id}/check")

    response = client.get(f"/api/websites/{website_id}/history.json")
    exported = response.get_json()

    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == f'attachment; filename="webwatch-history-{website_id}.json"'
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert exported["website"]["url"] == "https://1.1.1.1/"
    assert [entry["status"] for entry in exported["history"]] == ["baseline", "changed"]
    assert exported["history"][1]["added_content"] == "After"

    missing = client.get("/api/websites/999/history.json")
    assert missing.status_code == 404


def test_summary_counts_come_from_real_website_snapshots(client, monkeypatch):
    page_texts = iter(["Before", "After"])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(page_texts))
    website_id = add_test_website(client)
    client.post(f"/api/websites/{website_id}/check")
    client.post(f"/api/websites/{website_id}/check")
    client.post("/api/demo")

    stats = client.get("/api/stats").get_json()

    assert stats == {
        "websites": 1,
        "successful_checks": 2,
        "detected_changes": 1,
        "demo_runs": 1,
    }


def test_demo_changes_appear_in_history_but_not_real_monitoring_summary(client, monkeypatch):
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: "Real website baseline")
    website_id = add_test_website(client)
    client.post(f"/api/websites/{website_id}/check")

    demo = client.post("/api/demo").get_json()
    client.post(f"/api/websites/{demo['id']}/check")
    client.post(f"/api/websites/{demo['id']}/demo-version")
    demo_change = client.post(f"/api/websites/{demo['id']}/check").get_json()

    stats = client.get("/api/stats").get_json()
    history = client.get(f"/api/websites/{demo['id']}/history").get_json()

    assert demo_change["last_check_status"] == "changed"
    assert [entry["status"] for entry in history] == ["baseline", "changed"]
    assert stats == {
        "websites": 1,
        "successful_checks": 1,
        "detected_changes": 0,
        "demo_runs": 1,
    }


def test_failed_check_keeps_previous_successful_snapshot(client, tmp_path, monkeypatch):
    checks = iter(["Saved page text", ValueError("The webpage timed out."), "Saved page text"])

    def fake_fetch(_url):
        result = next(checks)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(webwatch, "render_page_text", fake_fetch)
    website_id = add_test_website(client)
    database_path = tmp_path / "test-webwatch.sqlite3"
    baseline = client.post(f"/api/websites/{website_id}/check").get_json()

    failed = client.post(f"/api/websites/{website_id}/check")
    assert failed.status_code == 502
    assert failed.get_json()["error"] == "The webpage timed out."
    after_failure = client.get("/api/websites").get_json()[0]
    assert after_failure["last_check_status"] == "error"
    assert after_failure["baseline_at"] == baseline["baseline_at"]
    assert stored_snapshots(database_path, website_id) == [("Saved page text",)]
    failed_history = client.get(f"/api/websites/{website_id}/history").get_json()
    assert [entry["status"] for entry in failed_history] == ["baseline"]
    failed_stats = client.get("/api/stats").get_json()
    assert failed_stats["successful_checks"] == 1
    assert failed_stats["detected_changes"] == 0

    # The next successful comparison must still use the pre-failure snapshot.
    recovered = client.post(f"/api/websites/{website_id}/check").get_json()
    assert recovered["last_check_status"] == "unchanged"
    assert stored_snapshots(database_path, website_id) == [("Saved page text",)]
    recovered_history = client.get(f"/api/websites/{website_id}/history").get_json()
    assert [entry["status"] for entry in recovered_history] == ["baseline"]
    assert client.get("/api/stats").get_json()["successful_checks"] == 2


def test_local_demo_runs_through_real_snapshot_comparison(client, monkeypatch):
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: pytest.fail("demo must not fetch a URL"))
    demo = client.post("/api/demo").get_json()
    website_id = demo["id"]

    baseline = client.post(f"/api/websites/{website_id}/check").get_json()
    switched = client.post(f"/api/websites/{website_id}/demo-version").get_json()
    changed = client.post(f"/api/websites/{website_id}/check").get_json()

    assert demo["is_demo"] == 1
    assert baseline["last_check_status"] == "baseline"
    assert switched["demo_version"] == 2
    assert changed["last_check_status"] == "changed"
    assert changed["added_content"] == "2 Club selection demo: this is a controlled local fixture."


def test_database_migration_preserves_existing_website_and_snapshot(tmp_path, monkeypatch):
    database_path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(database_path) as db:
        db.executescript(
            """
            CREATE TABLE websites (
                id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                last_checked_at TEXT, last_check_status TEXT NOT NULL DEFAULT 'not_checked',
                last_changed INTEGER, last_error TEXT,
                visibility TEXT NOT NULL DEFAULT 'public'
            );
            CREATE TABLE snapshots (
                id INTEGER PRIMARY KEY, website_id INTEGER NOT NULL REFERENCES websites(id),
                content_text TEXT NOT NULL, checked_at TEXT NOT NULL
            );
            CREATE TABLE private_settings (setting_id INTEGER PRIMARY KEY, password_hash TEXT NOT NULL);
            CREATE TABLE private_sessions (token_hash TEXT PRIMARY KEY, last_activity REAL NOT NULL);
            INSERT INTO private_settings (setting_id, password_hash) VALUES (1, 'obsolete-hash');
            INSERT INTO private_sessions (token_hash, last_activity) VALUES ('obsolete-session', 1);
            INSERT INTO websites (url, created_at, visibility)
                VALUES ('https://preserve.example/', '2026-10-01T00:00:00+00:00', 'private');
            INSERT INTO snapshots (website_id, content_text, checked_at)
            VALUES (1, 'Existing baseline', '2026-10-01T00:01:00+00:00');
            """
        )
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database_path))

    webwatch.init_db()
    webwatch.init_db()  # Additive migration remains safe when run again.

    with sqlite3.connect(database_path) as db:
        website = db.execute(
            "SELECT id, url, is_demo, is_hidden, availability_state, successful_checks_count FROM websites"
        ).fetchone()
        snapshot = db.execute("SELECT content_text FROM snapshots WHERE website_id = 1").fetchone()
        remaining_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        notification_count = db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    assert website == (1, "https://preserve.example/", 0, 1, "reachable", 1)
    assert snapshot == ("Existing baseline",)
    assert notification_count == 0
    assert "private_settings" in remaining_tables
    assert "private_sessions" in remaining_tables
    with sqlite3.connect(database_path) as db:
        db.execute("UPDATE websites SET is_hidden = 0 WHERE id = 1")
    webwatch.init_db()
    with sqlite3.connect(database_path) as db:
        assert db.execute("SELECT is_hidden FROM websites WHERE id = 1").fetchone() == (0,)


def test_hide_and_unhide_moves_existing_website_without_losing_history(client, tmp_path, monkeypatch):
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: "Saved before hiding")
    website_id = add_test_website(client)
    assert client.post(f"/api/websites/{website_id}/check").get_json()["last_check_status"] == "baseline"

    hidden = client.patch(
        f"/api/websites/{website_id}/hidden",
        json={"is_hidden": True},
        headers={"X-WebWatch-Request": "1", "Content-Type": "application/json"},
    )
    assert hidden.status_code == 200
    assert hidden.get_json() == {"ok": True, "is_hidden": True}
    assert client.get("/api/websites").get_json() == []
    hidden_rows = client.get("/api/websites?hidden=true")
    assert hidden_rows.headers["X-Total-Count"] == "1"
    assert len(hidden_rows.get_json()) == 1
    assert hidden_rows.get_json()[0]["id"] == website_id
    assert hidden_rows.get_json()[0]["is_hidden"] == 1
    assert len(client.get(f"/api/websites/{website_id}/history").get_json()) == 1

    restored = client.patch(f"/api/websites/{website_id}/hidden", json={"is_hidden": False})
    assert restored.status_code == 200
    assert client.get("/api/websites?hidden=true").get_json() == []
    dashboard_rows = client.get("/api/websites").get_json()
    assert [row["id"] for row in dashboard_rows] == [website_id]
    with sqlite3.connect(tmp_path / "test-webwatch.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM websites").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM snapshots WHERE website_id = ?", (website_id,)).fetchone() == (1,)
        assert db.execute("SELECT is_hidden FROM websites WHERE id = ?", (website_id,)).fetchone() == (0,)


def test_hidden_website_remains_checkable_and_in_monitoring_summary(client, monkeypatch):
    page_texts = iter(["First baseline", "First baseline with a real change"])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(page_texts))
    website_id = add_test_website(client)
    assert client.patch(f"/api/websites/{website_id}/hidden", json={"is_hidden": True}).status_code == 200

    baseline = client.post(f"/api/websites/{website_id}/check")
    changed = client.post(f"/api/websites/{website_id}/check")

    assert baseline.status_code == 200
    assert baseline.get_json()["last_check_status"] == "baseline"
    assert changed.status_code == 200
    assert changed.get_json()["last_check_status"] == "changed"
    assert changed.get_json()["added_content"] == "with a real change"
    history = client.get(f"/api/websites/{website_id}/history").get_json()
    assert [entry["status"] for entry in history] == ["baseline", "changed"]
    assert client.get("/api/websites").get_json() == []
    assert len(client.get("/api/websites?hidden=true").get_json()) == 1
    assert client.get("/api/stats").get_json() == {
        "websites": 1,
        "successful_checks": 2,
        "detected_changes": 1,
        "demo_runs": 0,
    }


def test_check_all_eligible_lists_include_hidden_real_sites_once_and_exclude_demo(client, monkeypatch):
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: "Shared real page text")
    public_id = add_test_website(client)
    hidden_id = client.post("/api/websites", json={"url": "https://9.9.9.9/hidden"}).get_json()["id"]
    assert client.patch(f"/api/websites/{hidden_id}/hidden", json={"is_hidden": True}).status_code == 200
    demo_id = client.post("/api/demo").get_json()["id"]

    public_response = client.get("/api/websites?hidden=false")
    hidden_response = client.get("/api/websites?hidden=true")
    eligible_by_id = {
        site["id"]: site
        for site in [*public_response.get_json(), *hidden_response.get_json()]
        if not site["is_demo"]
    }
    assert set(eligible_by_id) == {public_id, hidden_id}
    assert len(eligible_by_id) == 2
    for website_id in eligible_by_id:
        checked = client.post(f"/api/websites/{website_id}/check")
        assert checked.status_code == 200
        assert checked.get_json()["last_check_status"] == "baseline"

    assert client.get(f"/api/websites/{demo_id}/history").get_json() == []
    assert client.get(f"/api/websites/{hidden_id}/history").get_json()[0]["status"] == "baseline"
    public_ids = {site["id"] for site in client.get("/api/websites?hidden=false").get_json()}
    hidden_ids = {site["id"] for site in client.get("/api/websites?hidden=true").get_json()}
    assert public_id in public_ids
    assert demo_id in public_ids  # Demo fixtures are visible but excluded from Check All.
    assert hidden_ids == {hidden_id}


def test_new_websites_default_to_visible_and_hide_endpoint_validates_input(client):
    added = client.post("/api/websites", json={"url": "https://9.9.9.9/visible-by-default"})
    assert added.status_code == 201
    assert added.get_json()["is_hidden"] == 0
    website_id = added.get_json()["id"]
    assert client.patch(f"/api/websites/{website_id}/hidden", json={"is_hidden": "yes"}).status_code == 400
    assert client.patch("/api/websites/9999/hidden", json={"is_hidden": True}).status_code == 404


def test_mutating_api_rejects_missing_or_foreign_origin_marker(client):
    raw_client = webwatch.app.test_client()
    missing_marker = raw_client.post("/api/websites", json={"url": "https://1.1.1.1/"})
    foreign_origin = client.post(
        "/api/websites",
        json={"url": "https://1.1.1.1/"},
        headers={"Origin": "https://outside.example"},
    )
    assert missing_marker.status_code == 403
    assert foreign_origin.status_code == 403
    assert client.get("/api/websites").get_json() == []
