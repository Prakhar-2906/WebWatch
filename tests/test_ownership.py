"""Regression coverage for request-time ownership boundaries."""

import json
import re
import sqlite3

import auth
import app as webwatch
from conftest import TEST_PASSWORD, TEST_SECRET, TEST_SETUP_TOKEN, prepare_test_client, setup_test_owner


def _account_client(database, email):
    client = webwatch.app.test_client()
    prepare_test_client(client)
    response = client.post("/auth/login", json={"email": email, "password": TEST_PASSWORD},
                           headers={"X-CSRF-Token": client.environ_base["HTTP_X_CSRF_TOKEN"]})
    assert response.status_code == 200
    client.environ_base["HTTP_X_CSRF_TOKEN"] = client.get_cookie(auth.CSRF_COOKIE).value
    return client


def test_two_accounts_are_isolated_and_can_monitor_the_same_url(tmp_path, monkeypatch):
    database = tmp_path / "owner-isolation.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    webwatch.init_db()
    owner = webwatch.app.test_client()
    setup_test_owner(owner)
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO users(email,email_normalized,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                   ("member@example.test", "member@example.test", auth.password_hasher.hash(TEST_PASSWORD),
                    "member", webwatch.utc_now()))
    member = _account_client(database, "member@example.test")
    url = "https://1.1.1.1/shared-url"
    first = owner.post("/api/websites", json={"url": url, "display_name": "Owner page"})
    second = member.post("/api/websites", json={"url": url, "display_name": "Member page"})
    assert first.status_code == second.status_code == 201
    owner_id, member_id = first.get_json()["id"], second.get_json()["id"]
    assert owner_id != member_id

    with webwatch.get_db() as db:
        db.execute("INSERT INTO snapshots(website_id,content_text,checked_at,record_type) VALUES(?,?,?,?)",
                   (owner_id, "owner only snapshot", webwatch.utc_now(), "baseline"))
        db.execute("INSERT INTO notifications(website_id,website_name,website_url,category,event_type,confidence,explanation,occurred_at,owner_id) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (owner_id,"Owner page",url,"Availability","unreachable","suspected","private","2026-01-01T00:00:00Z",1))
        db.execute("INSERT INTO user_settings(owner_id,setting_key,setting_value) VALUES(1,'alert_webhook_url','https://secret.example/token')")
    snapshot_id = owner.get(f"/api/websites/{owner_id}/history/page?offset=0&filter=all").get_json()["history"][0]["id"]

    assert len(owner.get("/api/websites").get_json()) == 1
    assert len(member.get("/api/websites").get_json()) == 1
    assert member.get("/api/notifications").get_json() == []
    assert member.get("/api/jobs").get_json() == []
    assert member.get("/api/stats").get_json()["websites"] == 1
    assert "secret.example" not in json.dumps(member.get("/api/alerts").get_json())
    schedule_saved = owner.put("/api/schedule", json={"enabled": True, "interval_minutes": 30})
    assert schedule_saved.status_code == 200
    assert schedule_saved.get_json()["enabled"] is True
    assert member.get("/api/schedule").get_json()["enabled"] is False
    monkeypatch.setattr(webwatch, "validated_http_url", lambda value: (value, "hooks.example", {"93.184.216.34"}))
    alert_saved = owner.put("/api/alerts", json={"email_enabled":False,"email_recipient":"",
        "webhook_enabled":True,"webhook_url":"https://secret.example/token","webhook_chat_id":""})
    assert alert_saved.status_code == 200
    assert "webhook_url" not in alert_saved.get_json()
    assert member.get("/api/alerts").get_json()["webhook_configured"] is False
    assert "secret.example" not in json.dumps(member.get("/api/alerts").get_json())

    foreign_paths = [
        f"/api/websites/{owner_id}/history", f"/feed/websites/{owner_id}.xml",
        f"/api/websites/{owner_id}/metrics", f"/api/websites/{owner_id}/history/page",
        f"/api/websites/{owner_id}/history.html", f"/api/websites/{owner_id}/history.json",
        f"/api/websites/{owner_id}/snapshots/{snapshot_id}/screenshot",
        f"/api/websites/{owner_id}/compare?earlier={snapshot_id}&later={snapshot_id}",
    ]
    assert [member.get(path).status_code for path in foreign_paths] == [404] * len(foreign_paths)
    for method, path in (("patch", f"/api/websites/{owner_id}/hidden"),
                         ("patch", f"/api/websites/{owner_id}/settings"),
                         ("put", f"/api/websites/{owner_id}/selectors"),
                         ("post", f"/api/websites/{owner_id}/preview"),
                         ("post", f"/api/websites/{owner_id}/demo-version"),
                         ("post", f"/api/websites/{owner_id}/check"),
                         ("delete", f"/api/websites/{owner_id}")):
        response = getattr(member, method)(path, json={"is_hidden": True})
        assert response.status_code == 404, (method, path, response.status_code)
    assert owner.get(f"/api/websites/{owner_id}/history").status_code == 200


def test_sensitive_dashboard_and_resource_routes_require_authentication(tmp_path, monkeypatch):
    database = tmp_path / "unauthenticated.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    webwatch.init_db()
    client = webwatch.app.test_client()
    protected_paths = set()
    for rule in webwatch.app.url_map.iter_rules():
        if rule.rule.startswith(("/static/", "/auth/")) or rule.endpoint in {
            "healthcheck", "login_page", "setup_page", "invite_page",
        }:
            continue
        protected_paths.add(re.sub(r"<(?:(?:[^:>]+):)?[^>]+>", "1", rule.rule))
    responses = [(path, client.get(path)) for path in sorted(protected_paths)]
    assert all(response.status_code in {302, 401, 403} for _path, response in responses), [
        (path, response.status_code) for path, response in responses if response.status_code not in {302, 401, 403}]
    assert client.get("/login").status_code == 200
    assert client.get("/auth/status").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_delete_website_requires_csrf_and_persists_cascade(tmp_path, monkeypatch):
    database = tmp_path / "delete-website.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    webwatch.init_db()
    client = webwatch.app.test_client()
    setup_test_owner(client)
    created = client.post("/api/websites", json={"url": "https://example.com/"})
    assert created.status_code == 201
    website_id = created.get_json()["id"]
    with webwatch.get_db() as db:
        db.execute(
            "INSERT INTO snapshots(website_id,content_text,checked_at,record_type) VALUES(?,?,?,?)",
            (website_id, "saved history", webwatch.utc_now(), "baseline"),
        )

    rejected = client.delete(f"/api/websites/{website_id}", headers={
        "X-WebWatch-Request": "1", "X-CSRF-Token": "invalid-token",
    })
    assert rejected.status_code == 403
    with webwatch.get_db() as db:
        assert db.execute("SELECT 1 FROM websites WHERE id=?", (website_id,)).fetchone()

    response = client.delete(f"/api/websites/{website_id}", headers={
        "X-WebWatch-Request": "1", "X-CSRF-Token": client.get_cookie(auth.CSRF_COOKIE).value,
    })
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    with webwatch.get_db() as db:
        assert db.execute("SELECT 1 FROM websites WHERE id=?", (website_id,)).fetchone() is None
        assert db.execute("SELECT 1 FROM snapshots WHERE website_id=?", (website_id,)).fetchone() is None


def test_background_manual_jobs_keep_their_owners(tmp_path, monkeypatch):
    database = tmp_path / "owner-jobs.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    webwatch.init_db()
    owner = webwatch.app.test_client()
    setup_test_owner(owner)
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO users(email,email_normalized,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                   ("member@example.test", "member@example.test", auth.password_hasher.hash(TEST_PASSWORD),
                    "member", webwatch.utc_now()))
    member = _account_client(database, "member@example.test")
    first = owner.post("/api/websites", json={"url":"https://1.1.1.1/owner"}).get_json()["id"]
    second = member.post("/api/websites", json={"url":"https://9.9.9.9/member"}).get_json()["id"]
    with webwatch.get_db() as db:
        db.execute("UPDATE websites SET last_checked_at='2099-01-01T00:00:00+00:00' WHERE id IN (?,?)", (first, second))
        first_job, _ = webwatch.enqueue_check_job(db, first, "manual")
        second_job, _ = webwatch.enqueue_check_job(db, second, "manual")
        owners = dict(db.execute("SELECT id,owner_id FROM check_jobs WHERE id IN (?,?)", (first_job,second_job)).fetchall())
    assert owners == {first_job: 1, second_job: 2}
    checked = []
    def check_site(website_id, wait_for_lock=False):
        checked.append(website_id)
        return webwatch.app.response_class(json.dumps({"ok": True}), status=200, mimetype="application/json")
    monkeypatch.setattr(webwatch, "check_site", check_site)
    monkeypatch.setattr(webwatch, "reserve_domain_slot", lambda _url: None)
    webwatch.run_due_schedule()
    with webwatch.get_db() as db:
        statuses = dict(db.execute("SELECT id,status FROM check_jobs WHERE id IN (?,?)", (first_job,second_job)).fetchall())
    assert set(checked) == {first, second}
    assert statuses == {first_job: "succeeded", second_job: "succeeded"}


def test_unowned_orphan_alert_jobs_are_not_delivered(tmp_path, monkeypatch):
    database = tmp_path / "orphan-alerts.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    webwatch.init_db()
    client = webwatch.app.test_client()
    setup_test_owner(client)
    with webwatch.get_db() as db:
        db.execute("INSERT INTO alert_jobs(event_key,destination,payload,next_run_at,created_at) "
                   "VALUES('orphan-event','webhook','{}',?,?)", (webwatch.utc_now(),webwatch.utc_now()))
    delivered = []
    monkeypatch.setattr(webwatch, "deliver_alert_job", lambda job: delivered.append(job["id"]))
    webwatch.deliver_pending_alerts()
    assert delivered == []
    with webwatch.get_db() as db:
        row = db.execute("SELECT owner_id,status FROM alert_jobs WHERE event_key='orphan-event'").fetchone()
    assert row["owner_id"] is None and row["status"] == "queued"
