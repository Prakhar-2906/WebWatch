"""Environment-gated demo member provisioning and account isolation."""

import secrets
import sqlite3

import auth
import app as webwatch
from conftest import TEST_PASSWORD, TEST_SECRET, TEST_SETUP_TOKEN, prepare_test_client, setup_test_owner


DEMO_EMAIL = "test123@gmail.com"


def test_demo_account_provisioning_is_hashed_idempotent_and_uses_selected_database(tmp_path, monkeypatch):
    demo_password = secrets.token_urlsafe(24)
    database = tmp_path / "selected" / "webwatch.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    monkeypatch.setenv("WEBWATCH_DEMO_ACCOUNT", "true")
    monkeypatch.setenv("WEBWATCH_DEMO_EMAIL", DEMO_EMAIL)
    monkeypatch.setenv("WEBWATCH_DEMO_PASSWORD", demo_password)

    webwatch.init_db()
    webwatch._initialized_database_paths.discard(str(database.resolve()))
    webwatch.init_db()

    with sqlite3.connect(database) as db:
        rows = db.execute(
            "SELECT id, email_normalized, password_hash, role FROM users WHERE email_normalized = ?",
            (DEMO_EMAIL,),
        ).fetchall()
    assert len(rows) == 1
    user_id, email, password_hash, role = rows[0]
    assert email == DEMO_EMAIL
    assert password_hash != demo_password
    assert auth.password_hasher.verify(password_hash, demo_password)
    assert role == "member"

    # A later configured password must never reset an already provisioned account.
    auth.provision_demo_account(database, DEMO_EMAIL, secrets.token_urlsafe(24))
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE email_normalized = ?", (DEMO_EMAIL,)).fetchone()[0] == 1
        stored_hash = db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    assert stored_hash == password_hash

    client = webwatch.app.test_client()
    sign_in = client.get("/login")
    assert b"Demo sign-in is available only when the service operator has configured" in sign_in.data
    assert demo_password.encode() not in sign_in.data
    prepare_test_client(client)
    response = client.post("/auth/login", json={"email": DEMO_EMAIL, "password": demo_password},
                           headers={"X-CSRF-Token": client.environ_base["HTTP_X_CSRF_TOKEN"]})
    assert response.status_code == 200
    assert response.get_json()["user"]["role"] == "member"

    # Provisioning the demo member does not consume the separate first-owner setup flow.
    assert client.get("/auth/status").get_json()["initial_setup_available"] is True
    setup_token = client.get("/auth/csrf").get_json()
    assert setup_token["ok"] is True and setup_token["csrf_token"]
    csrf_cookie = client.get_cookie(auth.CSRF_COOKIE).value
    owner_setup = client.post("/auth/setup", json={"email": "real-owner@example.test",
                                                    "password": "long secure owner password"}, headers={
        "X-CSRF-Token": csrf_cookie, "X-WebWatch-Owner-Setup": TEST_SETUP_TOKEN,
    })
    assert owner_setup.status_code == 201
    assert owner_setup.get_json()["user"]["role"] == "owner"


def test_demo_account_cannot_read_another_accounts_pages(tmp_path, monkeypatch):
    demo_password = secrets.token_urlsafe(24)
    database = tmp_path / "isolation.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    webwatch.init_db()

    owner = webwatch.app.test_client()
    setup_test_owner(owner)
    auth.provision_demo_account(database, DEMO_EMAIL, demo_password)
    demo = webwatch.app.test_client()
    prepare_test_client(demo)
    login = demo.post("/auth/login", json={"email": DEMO_EMAIL, "password": demo_password},
                      headers={"X-CSRF-Token": demo.environ_base["HTTP_X_CSRF_TOKEN"]})
    assert login.status_code == 200

    created = owner.post("/api/websites", json={"url": "https://1.1.1.1/private"})
    assert created.status_code == 201
    assert demo.get("/api/websites").get_json() == []
    assert len(owner.get("/api/websites").get_json()) == 1
