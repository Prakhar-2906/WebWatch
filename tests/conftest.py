"""Keep unrelated unit tests fast; the explicit rate-limit test sets its own delay."""

import pytest
import auth as webwatch_auth

import app as webwatch


@pytest.fixture(autouse=True)
def disable_real_waits_between_mocked_checks(monkeypatch):
    monkeypatch.setattr(webwatch, "DOMAIN_MIN_INTERVAL_SECONDS", 0)


TEST_SECRET = "test-secret-key-that-is-long-enough-for-hmac"
TEST_SETUP_TOKEN = "test-owner-setup-token-that-is-long-enough"
TEST_PASSWORD = "long test owner password"


def prepare_test_client(client):
    client.environ_base["wsgi.url_scheme"] = "https"
    client.environ_base["HTTP_X_WEBWATCH_REQUEST"] = "1"
    client.get("/auth/csrf")
    csrf = client.get_cookie(webwatch_auth.CSRF_COOKIE).value
    client.environ_base["HTTP_X_CSRF_TOKEN"] = csrf
    return csrf


def setup_test_owner(client, email="owner@example.test"):
    csrf = prepare_test_client(client)
    response = client.post("/auth/setup", json={"email": email, "password": TEST_PASSWORD}, headers={
        "X-CSRF-Token": csrf, "X-WebWatch-Owner-Setup": TEST_SETUP_TOKEN,
    })
    assert response.status_code == 201, response.get_json()
    client.environ_base["HTTP_X_CSRF_TOKEN"] = client.get_cookie(webwatch_auth.CSRF_COOKIE).value
    return response.get_json()["user"]


def login_test_user(client, email="owner@example.test", password=TEST_PASSWORD):
    csrf = prepare_test_client(client)
    response = client.post("/auth/login", json={"email": email, "password": password},
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.get_json()
    client.environ_base["HTTP_X_CSRF_TOKEN"] = client.get_cookie(webwatch_auth.CSRF_COOKIE).value
    return response.get_json()["user"]
