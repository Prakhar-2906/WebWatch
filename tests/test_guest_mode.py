"""Server-side guest identity, isolation, expiry, and cleanup coverage."""

import sqlite3
from datetime import datetime, timezone

import auth
import app as webwatch
from conftest import TEST_SECRET, TEST_SETUP_TOKEN, prepare_test_client, setup_test_owner


def _guest(client):
    token = prepare_test_client(client)
    response = client.post("/auth/guest", json={}, headers={"X-CSRF-Token": token})
    assert response.status_code == 200, response.get_json()
    client.environ_base["HTTP_X_CSRF_TOKEN"] = client.get_cookie(auth.CSRF_COOKIE).value
    return response


def _isolated_database(tmp_path, monkeypatch):
    database = tmp_path / "guest.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    monkeypatch.delenv("WEBWATCH_DEMO_ACCOUNT", raising=False)
    monkeypatch.delenv("WEBWATCH_DEMO_EMAIL", raising=False)
    monkeypatch.delenv("WEBWATCH_DEMO_PASSWORD", raising=False)
    monkeypatch.delenv("WEBWATCH_GUEST_TTL_SECONDS", raising=False)
    webwatch.init_db()
    return database


def test_guest_can_enter_and_use_isolated_monitoring_features(tmp_path, monkeypatch):
    database = _isolated_database(tmp_path, monkeypatch)
    owner = webwatch.app.test_client()
    setup_test_owner(owner)
    owner_site = owner.post("/api/websites", json={"url": "https://1.1.1.1/owner-private"})
    assert owner_site.status_code == 201

    guest = webwatch.app.test_client()
    started = _guest(guest)
    assert started.get_json()["ok"] is True
    expires_at = datetime.fromisoformat(started.get_json()["expires_at"].replace("Z", "+00:00"))
    remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
    assert 5 * 60 * 60 <= remaining <= 6 * 60 * 60
    assert "Secure" in started.headers.get("Set-Cookie", "")
    assert guest.get("/").status_code == 200
    assert b"Temporary guest session" in guest.get("/").data
    token = guest.get_cookie(auth.CSRF_COOKIE).value
    assert guest.post("/auth/guest", json={}, headers={"X-CSRF-Token": token}).status_code == 409

    created = guest.post("/api/websites", json={"url": "https://1.1.1.1/guest-private"})
    assert created.status_code == 201
    guest_site_id = created.get_json()["id"]
    assert guest.get("/api/websites").get_json()[0]["id"] == guest_site_id
    assert guest.get(f"/api/websites/{owner_site.get_json()['id']}/history").status_code == 404
    assert guest.get("/account").status_code == 403
    assert guest.get("/auth/me").status_code == 403
    assert guest.get("/api/schedule").status_code == 403
    assert guest.get("/api/alerts").status_code == 403

    second_guest = webwatch.app.test_client()
    _guest(second_guest)
    assert second_guest.get("/api/websites").get_json() == []
    assert second_guest.delete(f"/api/websites/{guest_site_id}").status_code == 404
    assert owner.get("/api/websites").get_json()[0]["id"] == owner_site.get_json()["id"]

    with sqlite3.connect(database) as db:
        guest_user_id, token_hash = db.execute(
            "SELECT g.user_id, s.token_hash FROM guest_accounts g JOIN auth_sessions s ON s.user_id=g.user_id "
            "WHERE g.user_id != 1 LIMIT 1"
        ).fetchone()
        stored_password = db.execute("SELECT password_hash FROM users WHERE id=?", (guest_user_id,)).fetchone()[0]
        assert stored_password != ""
        assert db.execute("SELECT email FROM users WHERE id=?", (guest_user_id,)).fetchone()[0].endswith("@guest.invalid")
        assert len(token_hash) == 64


def test_guest_creation_and_mutations_keep_csrf_and_guest_cookie_unforgeable(tmp_path, monkeypatch):
    _isolated_database(tmp_path, monkeypatch)
    anonymous = webwatch.app.test_client()
    assert anonymous.post("/auth/guest", json={}).status_code == 403

    guest = webwatch.app.test_client()
    _guest(guest)
    site = guest.post("/api/websites", json={"url": "https://9.9.9.9/guest"})
    assert site.status_code == 201

    forged = webwatch.app.test_client()
    token = guest.get_cookie(auth.SESSION_COOKIE).value
    forged.set_cookie(auth.SESSION_COOKIE, token[:-1] + ("A" if token[-1] != "A" else "B"))
    assert forged.get("/api/websites").status_code == 401

    guest.environ_base.pop("HTTP_X_CSRF_TOKEN", None)
    rejected = guest.delete(f"/api/websites/{site.get_json()['id']}")
    assert rejected.status_code == 403


def test_expired_guest_record_rejects_still_valid_session_without_member_fallback(tmp_path, monkeypatch):
    database = _isolated_database(tmp_path, monkeypatch)
    guest = webwatch.app.test_client()
    _guest(guest)
    session_token = guest.get_cookie(auth.SESSION_COOKIE).value

    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE guest_accounts SET expires_at='2000-01-01T00:00:00Z'"
        )
        session_expiry = db.execute(
            "SELECT expires_at FROM auth_sessions"
        ).fetchone()[0]
        assert session_expiry > "2000-01-01T00:00:00Z"

    # Resolve the identity directly, before the request cleanup hook can
    # delete the guest rows. This reproduces an expired guest record paired
    # with a still-valid session and verifies it cannot become a member.
    with webwatch.app.test_request_context(
        "/", headers={"Cookie": f"{auth.SESSION_COOKIE}={session_token}"}
    ):
        assert auth.authenticated_user(database) is None


def test_expired_guest_is_denied_and_cleanup_preserves_registered_data(tmp_path, monkeypatch):
    database = _isolated_database(tmp_path, monkeypatch)
    owner = webwatch.app.test_client()
    setup_test_owner(owner)
    owned = owner.post("/api/websites", json={"url": "https://1.0.0.1/registered"}).get_json()
    guest = webwatch.app.test_client()
    _guest(guest)
    temporary = guest.post("/api/websites", json={"url": "https://9.9.9.9/temporary"}).get_json()
    with sqlite3.connect(database) as db:
        guest_user_id = db.execute("SELECT user_id FROM guest_accounts").fetchone()[0]
        db.execute("INSERT INTO snapshots(website_id,content_text,checked_at,record_type) VALUES(?,?,?,?)",
                   (temporary["id"], "temporary history", webwatch.utc_now(), "baseline"))
        db.execute("UPDATE guest_accounts SET expires_at='2000-01-01T00:00:00Z' WHERE user_id=?", (guest_user_id,))
        db.execute("UPDATE auth_sessions SET expires_at='2000-01-01T00:00:00Z' WHERE user_id=?", (guest_user_id,))

    # Expiry is enforced on every request; cleanup itself is throttled to one
    # pass per minute, so force the next opportunistic sweep for this test.
    monkeypatch.setattr(webwatch, "_last_guest_cleanup_at", 0.0)
    assert guest.get("/api/websites").status_code == 401
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT 1 FROM users WHERE id=?", (guest_user_id,)).fetchone() is None
        assert db.execute("SELECT 1 FROM websites WHERE id=?", (temporary["id"],)).fetchone() is None
        assert db.execute("SELECT 1 FROM snapshots WHERE website_id=?", (temporary["id"],)).fetchone() is None
        assert db.execute("SELECT 1 FROM users WHERE id=1 AND role='owner'").fetchone() is not None
        assert db.execute("SELECT 1 FROM websites WHERE id=? AND owner_id=1", (owned["id"],)).fetchone() is not None
