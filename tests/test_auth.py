"""Focused tests for the Stage 1 authentication and schema foundation."""

import json
import sqlite3

import pytest
from itsdangerous import TimestampSigner, URLSafeTimedSerializer

import app as webwatch
import auth


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    database = tmp_path / "auth.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-hmac")
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", "owner-setup-test-token-that-is-long-enough")
    monkeypatch.setenv("WEBWATCH_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("WEBWATCH_SMTP_FROM", "webwatch@example.test")
    CAPTURED_INVITATION_EMAILS.clear()
    class FakeSMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def starttls(self, **kwargs): pass
        def send_message(self, message): CAPTURED_INVITATION_EMAILS.append(message)
    monkeypatch.setattr(auth.smtplib, "SMTP", FakeSMTP)
    webwatch.init_db()
    client = webwatch.app.test_client()
    client.environ_base["wsgi.url_scheme"] = "https"
    return client, database


def csrf(client):
    response = client.get("/auth/csrf")
    assert response.status_code == 200
    return client.get_cookie(auth.CSRF_COOKIE).value


CAPTURED_INVITATION_EMAILS = []


def post_auth(client, url, payload, token, **headers):
    return client.post(url, json=payload, headers={"X-CSRF-Token": token, **headers})


def test_owner_setup_claims_legacy_rows_once_and_login_logout_rotate_sessions(auth_client):
    client, database = auth_client
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO websites(url, created_at, display_name) VALUES (?, ?, ?)",
                   ("https://legacy.example/", "2026-01-01T00:00:00Z", "Legacy"))
        db.executemany("INSERT INTO websites(url, created_at, display_name) VALUES (?, ?, ?)", [
            (f"https://legacy-{index}.example/", "2026-01-01T00:00:00Z", f"Legacy {index}")
            for index in range(2, 7)
        ])
        db.execute("INSERT INTO app_settings(setting_key, setting_value) VALUES ('legacy_test', 'preserved')")
        site_id = db.execute("SELECT id FROM websites").fetchone()[0]
        db.execute("INSERT INTO notifications(website_id,website_name,website_url,category,event_type,confidence,explanation,occurred_at) "
                   "VALUES(?,?,?,?,?,?,?,?)", (site_id,"Legacy","https://legacy.example/","Availability","unreachable","suspected","legacy outage","2026-01-01T00:00:00Z"))
        notification_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO check_jobs(website_id,created_at,updated_at,next_run_at) VALUES(?,?,?,?)",
                   (site_id,"x","x","x"))
        db.execute("INSERT INTO connectivity_incidents(started_at) VALUES('2026-01-01T00:00:00Z')")
        incident_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO connectivity_incident_sites(incident_id,website_id) VALUES(?,?)", (incident_id,site_id))
        db.execute("INSERT INTO connectivity_incidents(started_at) VALUES('2026-01-02T00:00:00Z')")
        orphan_incident_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO alert_jobs(event_key,destination,payload,next_run_at,created_at) VALUES(?,?,?,?,?)",
                   (f"availability:{notification_id}","webhook","{}","x","x"))
        db.execute("INSERT INTO alert_jobs(event_key,destination,payload,next_run_at,created_at) VALUES(?,?,?,?,?)",
                   ("orphan-event","webhook","{}","x","x"))

    token = csrf(client)
    setup = post_auth(client, "/auth/setup", {"email": "Owner@Example.com", "password": "long secure owner password"},
                      token, **{"X-WebWatch-Owner-Setup": "owner-setup-test-token-that-is-long-enough"})
    assert setup.status_code == 201
    assert setup.get_json()["user"]["email"] == "owner@example.com"
    assert "__Host-webwatch_session" in setup.headers.get("Set-Cookie", "")
    assert "HttpOnly" in setup.headers.get("Set-Cookie", "")
    assert "Secure" in setup.headers.get("Set-Cookie", "")
    with sqlite3.connect(database) as db:
        password_hash = db.execute("SELECT password_hash FROM users").fetchone()[0]
        assert password_hash.startswith("$argon2id$")
        assert db.execute("SELECT owner_id FROM websites WHERE id = ?", (site_id,)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM websites WHERE owner_id = 1").fetchone()[0] == 6
        assert db.execute("SELECT setting_value FROM user_settings WHERE owner_id = 1 AND setting_key = 'legacy_test'").fetchone()[0] == "preserved"
        assert db.execute("SELECT owner_id FROM notifications WHERE website_id=?", (site_id,)).fetchone()[0] == 1
        assert db.execute("SELECT owner_id FROM check_jobs WHERE website_id=?", (site_id,)).fetchone()[0] == 1
        assert db.execute("SELECT owner_id FROM alert_jobs WHERE event_key=?", (f"availability:{notification_id}",)).fetchone()[0] == 1
        assert db.execute("SELECT owner_id FROM connectivity_incidents WHERE id=?", (incident_id,)).fetchone()[0] == 1
        assert db.execute("SELECT owner_id FROM connectivity_incident_sites WHERE incident_id=? AND website_id=?",
                          (incident_id,site_id)).fetchone()[0] == 1
        assert db.execute("SELECT owner_id FROM connectivity_incidents WHERE id=?", (orphan_incident_id,)).fetchone()[0] is None
        assert db.execute("SELECT owner_id FROM alert_jobs WHERE event_key='orphan-event'").fetchone()[0] is None
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    assert post_auth(client, "/auth/setup", {"email": "next@example.com", "password": "long secure owner password"},
                     csrf(client), **{"X-WebWatch-Owner-Setup": "owner-setup-test-token-that-is-long-enough"}).status_code == 404
    assert client.get("/auth/me").status_code == 200

    old_cookie = client.get_cookie(auth.SESSION_COOKIE).value
    login_token = csrf(client)
    login = post_auth(client, "/auth/login", {"email": "owner@example.com", "password": "long secure owner password"}, login_token)
    assert login.status_code == 200
    assert client.get_cookie(auth.SESSION_COOKIE).value != old_cookie
    logout = post_auth(client, "/auth/logout", {}, csrf(client))
    assert logout.status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_invitation_is_email_bound_single_use_and_registration_is_closed(auth_client):
    client, _database = auth_client
    setup = post_auth(client, "/auth/setup", {"email": "owner@example.com", "password": "long secure owner password"},
                      csrf(client), **{"X-WebWatch-Owner-Setup": "owner-setup-test-token-that-is-long-enough"})
    assert setup.status_code == 201
    created = post_auth(client, "/auth/invitations", {"email": "member@example.com"}, csrf(client))
    assert created.status_code == 201
    assert "invitation_token" not in created.get_json()
    assert len(CAPTURED_INVITATION_EMAILS) == 1
    invitation = CAPTURED_INVITATION_EMAILS[-1].get_content().strip().splitlines()[-1]

    other_client = webwatch.app.test_client()
    other_client.environ_base["wsgi.url_scheme"] = "https"
    other_csrf = csrf(other_client)
    wrong_email = post_auth(other_client, "/auth/redeem", {
        "email": "attacker@example.com", "password": "long secure member password", "invitation_token": invitation,
    }, other_csrf)
    assert wrong_email.status_code == 400
    redeemed = post_auth(other_client, "/auth/redeem", {
        "email": "member@example.com", "password": "long secure member password", "invitation_token": invitation,
    }, other_csrf)
    assert redeemed.status_code == 201
    replay = post_auth(other_client, "/auth/redeem", {
        "email": "member@example.com", "password": "long secure member password", "invitation_token": invitation,
    }, other_csrf)
    assert replay.status_code == 400
    assert "invitation_token" not in json.dumps(replay.get_json())


def test_auth_mutations_require_csrf_and_login_failures_are_generic_and_limited(auth_client):
    client, database = auth_client
    no_csrf = client.post("/auth/login", json={"email": "nobody@example.com", "password": "not the password"})
    assert no_csrf.status_code == 403
    token = csrf(client)
    responses = [post_auth(client, "/auth/login", {"email": "nobody@example.com", "password": "wrong"}, token)
                 for _ in range(6)]
    assert {response.status_code for response in responses} == {401}
    assert {response.get_json()["error"] for response in responses} == {"Email or password is incorrect."}
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT attempts FROM login_rate_limits").fetchone()[0] == 5


def test_csrf_tokens_expire_and_fresh_tokens_remain_valid(auth_client):
    _client, _database = auth_client

    class ExpiredTimestampSigner(TimestampSigner):
        def get_timestamp(self):
            return super().get_timestamp() - 3601

    expired_serializer = URLSafeTimedSerializer(
        webwatch.app.config["SECRET_KEY"],
        salt="webwatch-csrf-v1",
        signer=ExpiredTimestampSigner,
    )
    expired_token = expired_serializer.dumps("old-csrf-nonce")
    with webwatch.app.app_context():
        fresh_token = auth._csrf_serializer().dumps("fresh-csrf-nonce")

    with webwatch.app.test_request_context(
        "/auth/login",
        headers={"Cookie": f"{auth.CSRF_COOKIE}={expired_token}", "X-CSRF-Token": expired_token},
    ):
        assert auth.csrf_request_valid() is False

    with webwatch.app.test_request_context(
        "/auth/login",
        headers={"Cookie": f"{auth.CSRF_COOKIE}={fresh_token}", "X-CSRF-Token": fresh_token},
    ):
        assert auth.csrf_request_valid() is True


def test_expired_session_and_invitation_are_rejected(auth_client):
    client, database = auth_client
    setup = post_auth(client, "/auth/setup", {"email": "owner@example.com", "password": "long secure owner password"},
                      csrf(client), **{"X-WebWatch-Owner-Setup": "owner-setup-test-token-that-is-long-enough"})
    assert setup.status_code == 201
    invitation_response = post_auth(client, "/auth/invitations", {"email": "late@example.com"}, csrf(client))
    assert "invitation_token" not in invitation_response.get_json()
    invitation = CAPTURED_INVITATION_EMAILS[-1].get_content().strip().splitlines()[-1]
    with sqlite3.connect(database) as db:
        db.execute("UPDATE auth_sessions SET expires_at = '2000-01-01T00:00:00Z'")
        db.execute("UPDATE invitations SET expires_at = '2000-01-01T00:00:00Z'")

    assert client.get("/auth/me").status_code == 401
    invitee = webwatch.app.test_client()
    invitee.environ_base["wsgi.url_scheme"] = "https"
    expired = post_auth(invitee, "/auth/redeem", {
        "email": "late@example.com", "password": "long secure member password", "invitation_token": invitation,
    }, csrf(invitee))
    assert expired.status_code == 400


def test_auth_pages_do_not_render_setup_or_csrf_secrets(auth_client):
    client, _database = auth_client
    setup_page = client.get("/setup")
    login_page = client.get("/login")
    invite_page = client.get("/invite")
    assert setup_page.status_code == login_page.status_code == invite_page.status_code == 200
    for response in (setup_page, login_page, invite_page):
        assert b"owner-setup-test-token" not in response.data
    csrf_response = client.get("/auth/csrf")
    assert csrf_response.get_json() == {"ok": True}
    assert "webwatch_csrf" in csrf_response.headers.get("Set-Cookie", "")
    assert client.get("/auth/status").get_json()["initial_setup_available"] is True

    created = post_auth(client, "/auth/setup", {"email":"owner@example.com", "password":"long secure owner password"},
                        csrf(client), **{"X-WebWatch-Owner-Setup":"owner-setup-test-token-that-is-long-enough"})
    assert created.status_code == 201
    assert client.get("/auth/status").get_json()["initial_setup_available"] is False
    assert client.get("/account").status_code == 200
    assert auth.SESSION_COOKIE not in json.dumps(client.get("/auth/me").get_json())


def _make_legacy_database(path, monkeypatch):
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(path))
    monkeypatch.setattr(webwatch, "migrate_auth_schema", lambda _path: None)
    webwatch.init_db()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO websites(id, url, created_at, display_name) VALUES (11, ?, ?, ?)",
                   ("https://legacy.example/", "2026-01-01T00:00:00Z", "Legacy"))
        db.execute("INSERT INTO snapshots(id, website_id, content_text, checked_at, record_type, screenshot) "
                   "VALUES (21, 11, 'saved page', '2026-01-01T00:00:00Z', 'baseline', ?)", (b"image-bytes",))
        db.execute("INSERT INTO notifications(id, website_id, website_name, website_url, category, event_type, confidence, explanation, occurred_at) "
                   "VALUES (31, 11, 'Legacy', 'https://legacy.example/', 'availability', 'unreachable', 'suspected', 'error', '2026-01-01T00:00:00Z')")
        db.execute("INSERT INTO check_jobs(id, website_id, created_at, updated_at, next_run_at) "
                   "VALUES (41, 11, 'x', 'x', 'x')")
        db.execute("INSERT INTO numeric_observations(id, website_id, selector, checked_at, value, raw_text, snapshot_id) "
                   "VALUES (51, 11, 'strong', '2026-01-01T00:00:00Z', 3, '3', 21)")
        db.execute("INSERT INTO connectivity_incidents(id, started_at) VALUES (61, '2026-01-01T00:00:00Z')")
        db.execute("INSERT INTO connectivity_incident_sites(incident_id, website_id) VALUES (61, 11)")
        db.execute("INSERT INTO alert_jobs(id, event_key, destination, payload, next_run_at, created_at) "
                   "VALUES (71, 'key', 'destination', '{}', 'x', 'x')")
    return path


def test_versioned_migration_preserves_ids_rows_screenshots_and_allows_per_owner_urls(tmp_path, monkeypatch):
    database = _make_legacy_database(tmp_path / "legacy.sqlite3", monkeypatch)
    tables = ("websites", "snapshots", "notifications", "check_jobs", "numeric_observations",
              "connectivity_incidents", "connectivity_incident_sites", "alert_jobs", "app_settings")
    primary_keys = {"connectivity_incident_sites": "incident_id, website_id"}
    with sqlite3.connect(database) as db:
        before = {table: db.execute(f'SELECT {primary_keys.get(table, "id")} FROM "{table}" ORDER BY 1, 2' if table == "connectivity_incident_sites" else f'SELECT {primary_keys.get(table, "id")} FROM "{table}" ORDER BY 1').fetchall()
                  if table != "app_settings" else db.execute("SELECT setting_key FROM app_settings ORDER BY setting_key").fetchall()
                  for table in tables}
        screenshot_before = db.execute("SELECT screenshot FROM snapshots WHERE id = 21").fetchone()[0]

    auth.migrate_auth_schema(database)
    auth.migrate_auth_schema(database)
    with sqlite3.connect(database) as db:
        after = {table: db.execute(f'SELECT {primary_keys.get(table, "id")} FROM "{table}" ORDER BY 1, 2' if table == "connectivity_incident_sites" else f'SELECT {primary_keys.get(table, "id")} FROM "{table}" ORDER BY 1').fetchall()
                 if table != "app_settings" else db.execute("SELECT setting_key FROM app_settings ORDER BY setting_key").fetchall()
                 for table in tables}
        assert after == before
        assert db.execute("SELECT screenshot FROM snapshots WHERE id = 21").fetchone()[0] == screenshot_before
        assert db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 1").fetchone()[0] == 1
        columns = {row[1] for row in db.execute("PRAGMA table_info(websites)")}
        assert "owner_id" in columns
        assert db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='websites_owner_url_unique'").fetchone()
        db.execute("INSERT INTO users(email, email_normalized, password_hash, role, created_at) VALUES ('one@example.com', 'one@example.com', 'x', 'owner', 'x')")
        db.execute("INSERT INTO users(email, email_normalized, password_hash, role, created_at) VALUES ('two@example.com', 'two@example.com', 'x', 'member', 'x')")
        db.execute("UPDATE websites SET owner_id = 1 WHERE id = 11")
        db.execute("INSERT INTO websites(url, created_at, owner_id) VALUES ('https://legacy.example/', 'x', 2)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO websites(url, created_at, owner_id) VALUES ('https://legacy.example/', 'x', 1)")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_versioned_migration_rolls_back_completely_on_failure(tmp_path, monkeypatch):
    database = _make_legacy_database(tmp_path / "rollback.sqlite3", monkeypatch)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE index_collision(value TEXT)")
        db.execute("CREATE INDEX websites_owner_url_unique ON index_collision(value)")
        before = db.execute("SELECT id, url FROM websites").fetchall()
    with pytest.raises(sqlite3.OperationalError):
        auth.migrate_auth_schema(database)
    with sqlite3.connect(database) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(websites)")}
        assert "owner_id" not in columns
        assert db.execute("SELECT id, url FROM websites").fetchall() == before
        assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone() is None
        assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone() is None


def test_stage_two_incident_migration_rolls_back_completely_on_failure(tmp_path):
    database = tmp_path / "incident-rollback.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO schema_migrations(version,applied_at) VALUES(1,'x');
            CREATE TABLE users(id INTEGER PRIMARY KEY);
            CREATE TABLE connectivity_incidents(id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
                ended_at TEXT, shared INTEGER NOT NULL DEFAULT 0, recovery_notified_at TEXT);
            INSERT INTO connectivity_incidents(id,started_at) VALUES(42,'2026-01-01T00:00:00Z');
            CREATE TABLE collision(value TEXT);
            CREATE INDEX connectivity_incidents_owner_recent ON collision(value);
        """)
    with pytest.raises(RuntimeError):
        auth.migrate_auth_schema(database)
    with sqlite3.connect(database) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(connectivity_incidents)")}
        assert "owner_id" not in columns
        assert db.execute("SELECT id,started_at FROM connectivity_incidents").fetchall() == [(42,"2026-01-01T00:00:00Z")]
        assert db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [(1,)]
