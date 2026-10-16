"""Durable job, rate-limit, alert delivery, and shared-render coverage."""

import json
import sqlite3
import threading
import time

import pytest

import app as webwatch
from conftest import setup_test_owner, login_test_user, TEST_SECRET, TEST_SETUP_TOKEN


@pytest.fixture
def jobs_client(tmp_path, monkeypatch):
    database = tmp_path / "jobs-alerts.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    webwatch.init_db()
    with webwatch.app.test_client() as client:
        setup_test_owner(client)
        yield client, database


def add_site(client, url):
    response = client.post("/api/websites", json={"url": url})
    assert response.status_code == 201
    return response.get_json()["id"]


def test_chromium_render_sessions_are_serialized(jobs_client, monkeypatch):
    _client, _database = jobs_client
    entered = threading.Event()
    release_first = threading.Event()
    guard = threading.Lock()
    active = 0
    max_active = 0
    calls = 0

    def fake_browser_render(*_args, **_kwargs):
        nonlocal active, max_active, calls
        with guard:
            active += 1
            calls += 1
            max_active = max(max_active, active)
            current_call = calls
        if current_call == 1:
            entered.set()
            assert release_first.wait(3)
        with guard:
            active -= 1
        return "Rendered page"

    monkeypatch.setattr(webwatch, "_render_page_text_with_browser", fake_browser_render)
    results = []
    first = threading.Thread(target=lambda: results.append(webwatch.render_page_text("https://one.example/")))
    second = threading.Thread(target=lambda: results.append(webwatch.render_page_text("https://two.example/")))
    first.start()
    assert entered.wait(3)
    second.start()
    time.sleep(0.05)
    assert calls == 1
    release_first.set()
    first.join(3)
    second.join(3)

    assert not first.is_alive() and not second.is_alive()
    assert results == ["Rendered page", "Rendered page"]
    assert max_active == 1


def test_active_job_is_unique_and_running_jobs_recover_after_restart(jobs_client):
    _client, database = jobs_client
    site_id = add_site(_client, "https://1.1.1.1/")
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        first_id, created = webwatch.enqueue_check_job(db, site_id, "manual")
        duplicate_id, duplicate_created = webwatch.enqueue_check_job(db, site_id, "scheduled")
        assert created is True
        assert duplicate_created is False
        assert duplicate_id == first_id
        webwatch.update_check_job(db, first_id, "running", attempts=1)

    webwatch.init_db()  # Same initialization path used after process startup.
    with sqlite3.connect(database) as db:
        status, attempts = db.execute(
            "SELECT status, attempts FROM check_jobs WHERE id = ?", (first_id,)
        ).fetchone()
    assert (status, attempts) == ("queued", 1)


def test_retry_status_and_backoff_deadline_are_persistent(jobs_client):
    _client, database = jobs_client
    site_id = add_site(_client, "https://9.9.9.9/")
    with sqlite3.connect(database) as db:
        job_id, _created = webwatch.enqueue_check_job(db, site_id, "manual")
        webwatch.update_check_job(db, job_id, "retry", attempts=2, error="timeout",
                                  next_run_at="2999-01-01T00:00:00+00:00")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT status, attempts, last_error, next_run_at FROM check_jobs WHERE id = ?",
                          (job_id,)).fetchone() == ("retry", 2, "timeout", "2999-01-01T00:00:00+00:00")


def test_manual_timeout_is_saved_as_retry_then_succeeds_on_explicit_retry(jobs_client, monkeypatch):
    client, database = jobs_client
    site_id = add_site(client, "https://1.1.1.1/")
    outcomes = iter([webwatch.WebsiteTimeoutError("temporary timeout"), "Recovered page"])

    def render(_url):
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(webwatch, "render_page_text", render)
    monkeypatch.setattr(webwatch, "reserve_domain_slot", lambda _url: None)
    failed = client.post(f"/api/websites/{site_id}/check")
    assert failed.status_code == 502
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT status, attempts FROM check_jobs WHERE website_id = ?", (site_id,)).fetchone() == ("retry", 1)
    recovered = client.post(f"/api/websites/{site_id}/check")
    assert recovered.status_code == 200
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT status, attempts FROM check_jobs WHERE website_id = ? ORDER BY id DESC LIMIT 1",
                          (site_id,)).fetchone() == ("succeeded", 2)


def test_manual_duplicate_check_returns_conflict_while_first_job_runs(jobs_client, monkeypatch):
    client, _database = jobs_client
    site_id = add_site(client, "https://1.1.1.1/")
    started = threading.Event()
    finish = threading.Event()

    def slow_render(_url):
        started.set()
        assert finish.wait(5)
        return "First valid page"

    monkeypatch.setattr(webwatch, "render_page_text", slow_render)
    monkeypatch.setattr(webwatch, "reserve_domain_slot", lambda _url: None)
    first_result = {}

    def run_first():
        with webwatch.app.test_client() as first_client:
            login_test_user(first_client)
            first_result["response"] = first_client.post(f"/api/websites/{site_id}/check")

    worker = threading.Thread(target=run_first)
    worker.start()
    assert started.wait(5)
    try:
        with webwatch.app.test_client() as second_client:
            login_test_user(second_client)
            response = second_client.post(f"/api/websites/{site_id}/check", headers={"X-WebWatch-Request": "1"})
        assert response.status_code == 409
        assert response.get_json()["job_id"]
        site_response = second_client.get("/api/websites")
        site = next(item for item in site_response.get_json() if item["id"] == site_id)
        assert site["active_check_job"]["status"] == "running"
    finally:
        finish.set()
        worker.join(5)
    assert not worker.is_alive()
    assert first_result["response"].status_code == 200


def test_unexpected_manual_check_failure_is_saved_as_a_visible_result(jobs_client, monkeypatch):
    client, database = jobs_client
    site_id = add_site(client, "https://1.1.1.1/")

    def fail_before_check(_url):
        raise RuntimeError("private internal exception detail")

    monkeypatch.setattr(webwatch, "reserve_domain_slot", fail_before_check)
    response = client.post(f"/api/websites/{site_id}/check")

    assert response.status_code == 500
    assert response.get_json()["category"] == "check_error"
    assert "private internal exception detail" not in response.get_json()["error"]
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        site = db.execute(
            "SELECT last_checked_at, last_check_status, last_check_category, last_error "
            "FROM websites WHERE id = ?", (site_id,)
        ).fetchone()
        job = db.execute(
            "SELECT status, last_error FROM check_jobs WHERE website_id = ?", (site_id,)
        ).fetchone()
    assert site["last_checked_at"]
    assert (site["last_check_status"], site["last_check_category"]) == ("error", "check_error")
    assert job["status"] == "failed"
    assert "could not be completed" in site["last_error"]


def test_same_normalized_render_settings_fetch_once_and_distribute_history(jobs_client, monkeypatch):
    client, database = jobs_client
    first_id = add_site(client, "https://EXAMPLE.com/")
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO websites (url, created_at, display_name, owner_id) VALUES (?, ?, ?, 1)",
                   ("https://example.com", webwatch.utc_now(), "Second page"))
        second_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    calls = []
    monkeypatch.setattr(webwatch, "reserve_domain_slot", lambda _url: None)
    monkeypatch.setattr(webwatch, "render_page_text", lambda url: (calls.append(url) or "Shared public content"))
    cache_token = webwatch._render_cache.set({})
    try:
        assert client.post(f"/api/websites/{first_id}/check").status_code == 200
        assert client.post(f"/api/websites/{second_id}/check").status_code == 200
    finally:
        webwatch._render_cache.reset(cache_token)
    assert len(calls) == 1
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT website_id, content_text FROM snapshots ORDER BY website_id").fetchall() == [
            (first_id, "Shared public content"), (second_id, "Shared public content")]


def test_concurrent_checks_for_different_owners_share_one_inflight_fetch(jobs_client, monkeypatch):
    client, database = jobs_client
    first_id = add_site(client, "https://1.1.1.1/")
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO users (email, email_normalized, password_hash, role, created_at) "
            "VALUES ('member@example.test', 'member@example.test', 'unused-test-hash', 'member', ?)",
            (webwatch.utc_now(),),
        )
        second_owner = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO websites (url, created_at, display_name, owner_id) VALUES (?, ?, ?, ?)",
            ("https://1.1.1.1/", webwatch.utc_now(), "Other owner's same URL", second_owner),
        )
        second_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    fetch_started = threading.Event()
    release_fetch = threading.Event()
    both_ready = threading.Barrier(2)
    requests = []
    results = {}
    errors = []

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "text/html"}

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
            yield b"<html><body>Shared concurrent content</body></html>"

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
            fetch_started.set()
            assert release_fetch.wait(timeout=3)
            return FakeResponse()

    def fake_render(url, **_kwargs):
        body, _headers, _final_url = webwatch.fetch_public_resource(
            url, accept="text/html,application/xhtml+xml",
            max_bytes=webwatch.MAX_RESPONSE_BYTES, require_html=True,
        )
        return webwatch.extract_readable_text(body)

    def check(website_id):
        try:
            with webwatch.app.app_context():
                both_ready.wait(timeout=3)
                response = webwatch.check_site(website_id)
                results[website_id] = response.status_code
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(webwatch, "render_page_text", fake_render)
    monkeypatch.setattr(webwatch.requests, "Session", FakeSession)
    workers = [threading.Thread(target=check, args=(site_id,)) for site_id in (first_id, second_id)]
    for worker in workers:
        worker.start()
    assert fetch_started.wait(timeout=3)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with webwatch._shared_render_flights_guard:
            waiting = any(flight["waiters"] for flight in webwatch._shared_render_flights.values())
        if waiting:
            break
        time.sleep(0.01)
    assert waiting, "the second owner's check should join the in-flight shared fetch"
    release_fetch.set()
    for worker in workers:
        worker.join(timeout=3)

    assert not errors
    assert all(not worker.is_alive() for worker in workers)
    assert results == {first_id: 200, second_id: 200}
    assert [url for url, _kwargs in requests] == ["https://1.1.1.1/"]
    assert requests[0][1]["allow_redirects"] is False
    assert requests[0][1]["stream"] is True
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT website_id, content_text FROM snapshots ORDER BY website_id").fetchall() == [
            (first_id, "Shared concurrent content"), (second_id, "Shared concurrent content")]


def test_shared_cycle_does_not_reuse_render_for_incompatible_selectors(jobs_client, monkeypatch):
    client, database = jobs_client
    first_id = add_site(client, "https://1.1.1.1/")
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO websites (url, created_at, display_name, owner_id) VALUES (?, ?, ?, 1)",
                   ("https://1.1.1.1:443#ignored-fragment", webwatch.utc_now(), "Selected region"))
        second_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("UPDATE websites SET watched_selectors='[\"main\"]', max_document_size_kib=768 WHERE id=?",
                   (second_id,))
    calls = []
    monkeypatch.setattr(webwatch, "reserve_domain_slot", lambda _url: None)
    monkeypatch.setattr(webwatch, "render_page_text", lambda url, **kwargs: (calls.append((url, kwargs)) or "Page"))
    cache_token = webwatch._render_cache.set({})
    try:
        assert client.post(f"/api/websites/{first_id}/check").status_code == 200
        assert client.post(f"/api/websites/{second_id}/check").status_code == 200
    finally:
        webwatch._render_cache.reset(cache_token)

    assert len(calls) == 2
    assert calls[0][1] == {}
    assert calls[1][1]["selectors"]["watched"] == ["main"]
    assert calls[1][1]["max_document_size_kib"] == 768


def test_shared_fetch_failures_are_not_cached_across_retry_attempts():
    cache_token = webwatch._render_cache.set({})
    calls = []

    def fail_once():
        calls.append(True)
        if len(calls) == 1:
            raise webwatch.WebsiteTemporaryError("temporary test failure")
        return "recovered content"

    try:
        key = ("https://1.1.1.1/", "[]", 512)
        with pytest.raises(webwatch.WebsiteTemporaryError, match="temporary test failure"):
            webwatch.shared_render_result(key, fail_once)
        assert webwatch.shared_render_result(key, fail_once) == "recovered content"
    finally:
        webwatch._render_cache.reset(cache_token)
    assert len(calls) == 2


def test_shared_results_do_not_cross_monitoring_cycles():
    calls = []

    def fetch():
        calls.append(True)
        return f"cycle {len(calls)}"

    key = ("https://1.1.1.1/", "[]", 512)
    with webwatch.render_cycle():
        assert webwatch.shared_render_result(key, fetch) == "cycle 1"
        assert webwatch.shared_render_result(key, fetch) == "cycle 1"
    with webwatch.render_cycle():
        assert webwatch.shared_render_result(key, fetch) == "cycle 2"
    assert len(calls) == 2


def test_keyword_only_alerts_filter_meaningful_change_and_rss_lists_versions(jobs_client, monkeypatch):
    client, database = jobs_client
    site_id = add_site(client, "https://1.1.1.1/")
    changed = []
    monkeypatch.setattr(webwatch, "enqueue_alert_event", lambda _db, _key, _site, summary, *_a, **_kw: changed.append(summary))
    assert client.patch(f"/api/websites/{site_id}/settings", json={
        "display_name": "Example", "check_interval_minutes": 60,
        "alert_keywords_enabled": True, "alert_keywords": ["release"],
    }).status_code == 200
    pages = iter(["Old page", "Unrelated update", "Release 2.0"])
    monkeypatch.setattr(webwatch, "render_page_text", lambda _url: next(pages))
    monkeypatch.setattr(webwatch, "reserve_domain_slot", lambda _url: None)
    for _ in range(3):
        response = client.post(f"/api/websites/{site_id}/check")
        assert response.status_code == 200
    assert len(changed) == 1
    assert "Release 2.0" in changed[0]
    feed = client.get(f"/feed/websites/{site_id}.xml")
    assert feed.status_code == 200
    assert feed.mimetype == "application/rss+xml"
    assert b"Release 2.0" in feed.data
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM snapshots WHERE website_id=? AND record_type='changed'", (site_id,)).fetchone()[0] == 2


def test_numeric_metric_api_and_daily_digest_queue_are_persisted(jobs_client):
    client, database = jobs_client
    site_id = add_site(client, "https://9.9.9.9/")
    assert client.patch(f"/api/websites/{site_id}/settings", json={
        "display_name": "Metric site", "check_interval_minutes": 60,
        "numeric_selectors": [".price"],
    }).status_code == 200
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO numeric_observations(website_id,selector,checked_at,value,raw_text) VALUES(?,?,?,?,?)",
                   (site_id, ".price", "2026-10-15T10:00:00+00:00", 12.5, "$12.50"))
        webwatch.save_owner_settings_for_id(db, 1, {"alert_daily_digest_enabled":"1", "alert_webhook_enabled":"1", "alert_webhook_url":"https://example.com/hook"})
    metric = client.get(f"/api/websites/{site_id}/metrics?selector=.price")
    assert metric.status_code == 200
    assert metric.get_json()["points"][0]["value"] == 12.5
    assert client.get(f"/api/websites/{site_id}/metrics?selector=body").status_code == 400
    webwatch.queue_daily_digest_if_due()
    webwatch.queue_daily_digest_if_due()
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM alert_jobs WHERE event_key LIKE 'digest:%'").fetchone()[0] == 1


def test_successful_render_persists_numeric_observation_without_duplicate_snapshot(jobs_client, monkeypatch):
    client, database = jobs_client
    site_id = add_site(client, "https://1.0.0.1/")
    client.patch(f"/api/websites/{site_id}/settings", json={
        "display_name": "Counter", "check_interval_minutes": 60, "numeric_selectors": [".count"],
    })
    rendered = webwatch.RenderedPageText("Stable page", numeric_values=[
        {"selector": ".count", "value": 42.0, "raw_text": "42 items"}])
    monkeypatch.setattr(webwatch, "render_page_text", lambda *_args, **_kwargs: rendered)
    monkeypatch.setattr(webwatch, "reserve_domain_slot", lambda _url: None)
    assert client.post(f"/api/websites/{site_id}/check").status_code == 200
    assert client.post(f"/api/websites/{site_id}/check").status_code == 200
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM snapshots WHERE website_id=?", (site_id,)).fetchone()[0] == 1
        assert db.execute("SELECT value FROM numeric_observations WHERE website_id=? ORDER BY id",
                          (site_id,)).fetchall() == [(42.0,), (42.0,)]


def test_numeric_selector_fixture_flows_from_render_to_saved_chart_points(jobs_client, monkeypatch):
    client, database = jobs_client
    site_id = add_site(client, "https://93.184.216.34/")
    saved = client.patch(f"/api/websites/{site_id}/settings", json={
        "display_name": "Local metric fixture", "check_interval_minutes": 60,
        "numeric_selectors": [".price"],
    })
    assert saved.status_code == 200
    html = (
        b"<html><body><main>Product listing</main>"
        b"<span class='price'>$1,234.50 per item</span></body></html>"
    )
    fetched = []

    def fixture_resource(url, **_kwargs):
        fetched.append(url)
        return html, {"Content-Type": "text/html; charset=utf-8"}, url

    monkeypatch.setattr(webwatch, "fetch_public_resource", fixture_resource)
    monkeypatch.setattr(webwatch, "reserve_domain_slot", lambda _url: None)

    first = client.post(f"/api/websites/{site_id}/check")
    second = client.post(f"/api/websites/{site_id}/check")

    assert first.status_code == second.status_code == 200
    assert fetched == ["https://93.184.216.34/", "https://93.184.216.34/"]
    with sqlite3.connect(database) as db:
        observations = db.execute(
            "SELECT value,raw_text FROM numeric_observations WHERE website_id=? AND selector='.price' ORDER BY id",
            (site_id,),
        ).fetchall()
        snapshots = db.execute(
            "SELECT COUNT(*) FROM snapshots WHERE website_id=?", (site_id,)
        ).fetchone()[0]
    assert observations == [(1234.5, "$1,234.50 per item")] * 2
    assert snapshots == 1

    metrics = client.get(f"/api/websites/{site_id}/metrics?selector=.price")
    assert metrics.status_code == 200
    points = metrics.get_json()["points"]
    assert [point["value"] for point in points] == [1234.5, 1234.5]
    assert [point["raw_text"] for point in points] == ["$1,234.50 per item"] * 2


def test_ai_summary_falls_back_without_key_and_rejects_private_provider(jobs_client, monkeypatch):
    monkeypatch.delenv("WEBWATCH_SUMMARY_API_KEY", raising=False)
    assert webwatch.optional_ai_change_summary("added", "removed") is None
    monkeypatch.setenv("WEBWATCH_SUMMARY_API_KEY", "test-secret")
    monkeypatch.setenv("WEBWATCH_SUMMARY_API_URL", "https://127.0.0.1/v1/chat/completions")
    assert webwatch.optional_ai_change_summary("added", "removed") is None


def test_domain_rate_slots_are_persisted_and_wait_before_next_fetch(jobs_client, monkeypatch):
    _client, database = jobs_client
    sleeps = []
    monkeypatch.setattr(webwatch.time, "sleep", sleeps.append)
    monkeypatch.setattr(webwatch, "DOMAIN_MIN_INTERVAL_SECONDS", 3)
    webwatch.reserve_domain_slot("https://news.example/")
    webwatch.reserve_domain_slot("https://news.example/other")
    assert sleeps and 0 < sleeps[0] <= 3
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM domain_limits WHERE domain = 'news.example'").fetchone()[0] == 1


def test_alert_events_deduplicate_and_contain_useful_history_payload(jobs_client):
    client, database = jobs_client
    site_id = add_site(client, "https://1.1.1.1/")
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        webwatch.save_owner_settings_for_id(db, 1, {"alert_webhook_enabled":"1", "alert_webhook_url":"https://hooks.example/receiver"})
        site = db.execute("SELECT * FROM websites WHERE id = ?", (site_id,)).fetchone()
        webwatch.enqueue_alert_event(db, "change:1:42", site, "Price was added", "2026-10-16T00:00:00+00:00", 42)
        webwatch.enqueue_alert_event(db, "change:1:42", site, "Price was added", "2026-10-16T00:00:00+00:00", 42)
        row = db.execute("SELECT event_key, destination, payload FROM alert_jobs").fetchone()
    assert row[0:2] == ("change:1:42", "webhook")
    payload = json.loads(row[2])
    assert payload["website_name"]
    assert payload["url"] == "https://1.1.1.1/"
    assert payload["summary"] == "Price was added"
    assert payload["timestamp"]
    assert payload["history_url"].endswith("/history.html#snapshot-42")
    assert client.get("/api/jobs").status_code == 200


def test_content_change_alerts_are_throttled_per_site_without_dropping_versions(jobs_client):
    client, database = jobs_client
    first_id = add_site(client, "https://1.1.1.1/")
    second_id = add_site(client, "https://9.9.9.9/")
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        webwatch.save_owner_settings_for_id(db, 1, {"alert_webhook_enabled":"1", "alert_webhook_url":"https://hooks.example/receiver"})
        first = db.execute("SELECT * FROM websites WHERE id = ?", (first_id,)).fetchone()
        second = db.execute("SELECT * FROM websites WHERE id = ?", (second_id,)).fetchone()
        webwatch.enqueue_alert_event(db, f"change:{first_id}:41", first, "First change", "2026-10-16T12:00:00+00:00", 41)
        webwatch.enqueue_alert_event(db, f"change:{first_id}:42", first, "Frequent update", "2026-10-16T12:30:00+00:00", 42)
        webwatch.enqueue_alert_event(db, f"change:{second_id}:51", second, "Other site change", "2026-10-16T12:30:00+00:00", 51)
        webwatch.enqueue_alert_event(db, f"change:{first_id}:43", first, "Later change", "2026-10-16T13:00:00+00:00", 43)
        queued = db.execute("SELECT event_key FROM alert_jobs ORDER BY id").fetchall()
        throttle_state = db.execute(
            "SELECT setting_value FROM user_settings WHERE owner_id=1 AND setting_key = ?",
            (f"alert_last_change_queued:{first_id}",),
        ).fetchone()[0]
    assert [row[0] for row in queued] == [
        f"change:{first_id}:41", f"change:{second_id}:51", f"change:{first_id}:43",
    ]
    assert throttle_state == "2026-10-16T13:00:00+00:00"


def test_webhook_payload_supports_discord_and_telegram_chat_id(jobs_client, monkeypatch):
    _client, database = jobs_client
    with sqlite3.connect(database) as db:
        webwatch.save_owner_settings_for_id(db, 1, {"alert_webhook_url":"https://hooks.example/receiver", "alert_webhook_chat_id":"12345"})
    monkeypatch.setattr(webwatch, "validated_http_url", lambda _url: ("https://hooks.example/receiver", "hooks.example", {"93.184.216.34"}))
    sent = {}

    class Response:
        status_code = 204

    class Session:
        trust_env = True
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def mount(self, *_args): pass
        def post(self, url, **kwargs): sent.update(url=url, **kwargs); return Response()

    monkeypatch.setattr(webwatch.requests, "Session", Session)
    job = {"owner_id": 1, "destination": "webhook", "payload": json.dumps({
        "website_name": "News", "url": "https://example.com/", "summary": "New article",
        "timestamp": "now", "history_url": "/history",})}
    webwatch.deliver_alert_job(job)
    assert sent["json"]["content"].startswith("WebWatch: News")
    assert sent["json"]["chat_id"] == "12345"
    assert sent["json"]["text"].endswith("/history")


def test_email_message_includes_event_details_and_uses_saved_recipient(jobs_client, monkeypatch):
    _client, database = jobs_client
    monkeypatch.setenv("WEBWATCH_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("WEBWATCH_SMTP_PORT", "587")
    monkeypatch.setenv("WEBWATCH_SMTP_FROM", "watch@example.test")
    monkeypatch.setenv("WEBWATCH_SMTP_USER", "mailer")
    monkeypatch.setenv("WEBWATCH_SMTP_PASSWORD", "environment-only-secret")
    with sqlite3.connect(database) as db:
        webwatch.save_owner_settings_for_id(db, 1, {"alert_email_recipient":"reader@example.test"})
    sent = {}

    class SMTP:
        def __init__(self, host, port, timeout): sent.update(host=host, port=port, timeout=timeout)
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def starttls(self, context): sent["tls"] = context is not None
        def login(self, username, password): sent["login"] = (username, password)
        def send_message(self, message): sent["message"] = message

    monkeypatch.setattr(webwatch.smtplib, "SMTP", SMTP)
    job = {"owner_id": 1, "destination": "email", "payload": json.dumps({
        "website_name": "News", "url": "https://news.example/", "summary": "Added a new article",
        "timestamp": "2026-10-16T00:00:00+00:00", "history_url": "/api/websites/1/history.html#snapshot-2",
    })}
    webwatch.deliver_alert_job(job)
    email = sent["message"]
    assert email["To"] == "reader@example.test"
    assert email["From"] == "watch@example.test"
    body = email.get_content()
    assert "Added a new article" in body
    assert "https://news.example/" in body
    assert "snapshot-2" in body
    assert sent["tls"] is True
    assert sent["login"] == ("mailer", "environment-only-secret")


def test_unconfigured_alert_delivery_is_reported_without_fake_success(jobs_client):
    client, _database = jobs_client
    response = client.post("/api/alerts/test")
    assert response.status_code == 400
    assert "configured" in response.get_json()["error"]


def test_webhook_delivery_failure_redacts_secret_from_api_logs_and_saved_error(jobs_client, monkeypatch, caplog):
    client, database = jobs_client
    secret_url = "https://hooks.example/receiver?token=do-not-leak"
    with sqlite3.connect(database) as db:
        webwatch.save_owner_settings_for_id(db, 1, {"alert_webhook_enabled":"1", "alert_webhook_url":secret_url})

    def fail_delivery(_job):
        raise webwatch.requests.ConnectionError(f"Connection failed for {secret_url}")

    monkeypatch.setattr(webwatch, "deliver_alert_job", fail_delivery)
    response = client.post("/api/alerts/test")
    assert response.status_code == 502
    assert secret_url not in response.get_data(as_text=True)
    assert secret_url not in caplog.text
    with sqlite3.connect(database) as db:
        error = db.execute("SELECT last_error FROM alert_jobs WHERE event_key LIKE 'test:%'").fetchone()[0]
    assert error == "Webhook delivery failed; check the HTTPS destination and server configuration."


def test_flask_cli_entrypoint_starts_the_existing_scheduler(jobs_client, monkeypatch):
    client, _database = jobs_client
    started = []
    monkeypatch.setenv("FLASK_RUN_FROM_CLI", "true")
    monkeypatch.setattr(webwatch, "start_scheduler", lambda: started.append(True))
    assert client.get("/").status_code == 200
    assert started == [True]


def test_alert_configuration_redacts_webhook_and_rejects_private_targets(jobs_client, monkeypatch):
    client, _database = jobs_client
    monkeypatch.setattr(webwatch, "validated_http_url", lambda _url: ("http://127.0.0.1/", "127.0.0.1", {"127.0.0.1"}))
    denied = client.put("/api/alerts", json={
        "email_enabled": False, "email_recipient": "", "webhook_enabled": True,
        "webhook_url": "http://127.0.0.1/", "webhook_chat_id": "",
    })
    assert denied.status_code == 400
    monkeypatch.setattr(webwatch, "validated_http_url", lambda _url: ("https://hooks.example/receiver", "hooks.example", {"93.184.216.34"}))
    saved = client.put("/api/alerts", json={
        "email_enabled": False, "email_recipient": "", "webhook_enabled": True,
        "webhook_url": "https://hooks.example/receiver", "webhook_chat_id": "",
    })
    assert saved.status_code == 200
    assert saved.get_json()["webhook_configured"] is True
    assert "webhook_url" not in saved.get_json()
    update_without_secret = client.put("/api/alerts", json={
        "email_enabled": False, "email_recipient": "", "webhook_enabled": True,
        "webhook_url": "", "webhook_chat_id": "",
    })
    assert update_without_secret.status_code == 200
    with webwatch.get_db() as db:
        saved_url = db.execute("SELECT setting_value FROM user_settings WHERE owner_id=1 AND setting_key = 'alert_webhook_url'").fetchone()[0]
    assert saved_url == "https://hooks.example/receiver"
