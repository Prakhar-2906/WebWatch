"""Deployment configuration regressions; all database use is isolated to tmp_path."""

import json
import runpy
import sqlite3
import sys
from types import ModuleType
from http.cookies import SimpleCookie
from pathlib import Path

import pytest
from werkzeug.test import EnvironBuilder, run_wsgi_app

import app as webwatch
import auth
from conftest import TEST_SECRET, TEST_SETUP_TOKEN


def _wsgi_request(application, path, method="GET", *, payload=None, headers=None):
    builder = EnvironBuilder(
        path=path,
        method=method,
        base_url="http://internal.render.local",
        json=payload,
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "watch.example.test",
            "X-Forwarded-For": "203.0.113.20",
            **(headers or {}),
        },
    )
    environ = builder.get_environ()
    app_iter, status, response_headers = run_wsgi_app(application, environ)
    body = b"".join(app_iter)
    return webwatch.app.response_class(body, status=status, headers=response_headers)


def _cookies(response):
    values = {}
    for header in response.headers.getlist("Set-Cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        values.update({name: morsel.value for name, morsel in parsed.items()})
    return values


@pytest.fixture
def isolated_proxy_app(tmp_path, monkeypatch):
    database = tmp_path / "render-proxy.sqlite3"
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(database))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    webwatch.init_db()
    original = webwatch.app.wsgi_app
    wrapped = webwatch.trusted_proxy_wsgi(original, enabled=True)
    monkeypatch.setattr(webwatch.app, "wsgi_app", wrapped)
    return database, wrapped


def test_render_proxy_supports_setup_login_csrf_and_same_origin(isolated_proxy_app):
    database, application = isolated_proxy_app
    origin = "https://watch.example.test"
    csrf_response = _wsgi_request(application, "/auth/csrf")
    assert csrf_response.status_code == 200
    csrf_cookie = _cookies(csrf_response)[auth.CSRF_COOKIE]

    setup = _wsgi_request(application, "/auth/setup", "POST", payload={
        "email": "owner@example.test", "password": "long test owner password",
    }, headers={
        "Origin": origin,
        "Cookie": f"{auth.CSRF_COOKIE}={csrf_cookie}",
        "X-CSRF-Token": csrf_cookie,
        "X-WebWatch-Owner-Setup": TEST_SETUP_TOKEN,
    })
    assert setup.status_code == 201
    assert "Secure" in setup.headers.get("Set-Cookie", "")

    setup_cookies = _cookies(setup)
    login = _wsgi_request(application, "/auth/login", "POST", payload={
        "email": "owner@example.test", "password": "long test owner password",
    }, headers={
        "Origin": origin,
        "Cookie": "; ".join(f"{name}={value}" for name, value in setup_cookies.items()),
        "X-CSRF-Token": setup_cookies[auth.CSRF_COOKIE],
    })
    assert login.status_code == 200
    login_cookies = _cookies(login)

    mutation_headers = {
        "Origin": origin,
        "Cookie": "; ".join(f"{name}={value}" for name, value in login_cookies.items()),
        "X-CSRF-Token": login_cookies[auth.CSRF_COOKIE],
        "X-WebWatch-Request": "1",
    }
    accepted = _wsgi_request(application, "/api/schedule", "PUT", payload={
        "enabled": True, "interval_minutes": 30,
    }, headers=mutation_headers)
    assert accepted.status_code == 200

    rejected = _wsgi_request(application, "/api/schedule", "PUT", payload={
        "enabled": False, "interval_minutes": 30,
    }, headers={**mutation_headers, "Origin": "https://attacker.example"})
    assert rejected.status_code == 403
    assert json.loads(rejected.get_data(as_text=True))["error"] == "Cross-origin requests are not allowed."

    with sqlite3.connect(database) as db:
        assert db.execute("SELECT setting_value FROM user_settings WHERE owner_id=1 AND setting_key='schedule_enabled'").fetchone()[0] == "1"


def test_deployment_data_path_defaults_to_local_location_and_accepts_override(tmp_path, monkeypatch):
    expected_local = str(Path(webwatch.app.instance_path, "webwatch.sqlite3").resolve())
    assert webwatch.app.config["DATABASE"] == expected_local
    selected = tmp_path / "render" / "webwatch.sqlite3"
    monkeypatch.setenv("WEBWATCH_DATABASE", str(selected))
    assert webwatch.configured_database_path(expected_local) == str(selected.resolve())
    assert not selected.exists()


def test_csrf_origin_check_requires_trusted_proxy_scheme_to_match_https_origin(tmp_path, monkeypatch):
    monkeypatch.setitem(webwatch.app.config, "DATABASE", str(tmp_path / "csrf-proxy.sqlite3"))
    monkeypatch.setitem(webwatch.app.config, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("WEBWATCH_OWNER_SETUP_TOKEN", TEST_SETUP_TOKEN)
    webwatch.init_db()
    with webwatch.app.app_context():
        token = auth._csrf_serializer().dumps("test-csrf-nonce")
    with webwatch.app.test_request_context(
        "/auth/login",
        base_url="http://internal.voroa.local",
        headers={"Origin": "https://watch.example.test", "Cookie": f"{auth.CSRF_COOKIE}={token}"},
    ):
        from flask import request
        request.headers.environ["HTTP_X_CSRF_TOKEN"] = token
        assert auth.csrf_request_valid() is False

    application = webwatch.trusted_proxy_wsgi(
        webwatch.app.wsgi_app,
        enabled=webwatch.trusted_proxy_enabled({"PORT": "10000"}),
    )
    response = _wsgi_request(
        application,
        "/auth/login",
        "POST",
        payload={"email": "nobody@example.test", "password": "incorrect"},
        headers={
            "Origin": "https://watch.example.test",
            "Cookie": f"{auth.CSRF_COOKIE}={token}",
            "X-CSRF-Token": token,
        },
    )
    # CSRF passes at the proxy-aware origin; authentication then rejects the
    # deliberately invalid credentials.
    assert response.status_code == 401


def test_trusted_proxy_autodetection_is_limited_to_platform_port_and_can_be_disabled():
    assert webwatch.trusted_proxy_enabled({"PORT": "10000"}) is True
    assert webwatch.trusted_proxy_enabled({}) is False
    assert webwatch.trusted_proxy_enabled({"PORT": "10000", "WEBWATCH_TRUST_PROXY": "false"}) is False


def test_gunicorn_configuration_uses_one_worker_and_scheduler_hooks(monkeypatch):
    config_path = Path(__file__).parents[1] / "gunicorn.conf.py"
    config = runpy.run_path(str(config_path))
    assert config["workers"] == 1
    assert config["worker_class"] == "gthread"
    assert config["threads"] == 4

    starts, stops = [], []
    checks = []
    monkeypatch.setattr(webwatch, "verify_playwright_browser", lambda: checks.append(True))
    monkeypatch.setattr(webwatch, "start_scheduler", lambda: starts.append(True))
    monkeypatch.setattr(webwatch, "stop_scheduler", lambda: stops.append(True))
    config["post_worker_init"](object())
    config["worker_exit"](object(), object())
    assert checks == [True]
    assert starts == [True]
    assert stops == [True]


def test_playwright_deployment_check_rejects_missing_headless_shell(monkeypatch, tmp_path):
    missing_executable = tmp_path / "chromium_headless_shell" / "chrome-headless-shell"

    class FakePlaywrightContext:
        chromium = type("Chromium", (), {"executable_path": str(missing_executable)})()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = FakePlaywrightContext
    playwright_package = ModuleType("playwright")
    playwright_package.__path__ = []
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    with pytest.raises(RuntimeError, match="headless-shell executable is missing") as error:
        webwatch.verify_playwright_browser()
    assert str(missing_executable) in str(error.value)


def test_browser_installer_uses_running_python_and_shared_runtime_cache(monkeypatch):
    script_path = Path(__file__).parents[1] / "tools" / "install_playwright_browser.py"
    install_calls, verified = [], []
    monkeypatch.setattr(webwatch, "verify_playwright_browser", lambda: verified.append(True))
    installer = runpy.run_path(str(script_path))
    monkeypatch.setattr(installer["subprocess"], "run", lambda command, **kwargs: install_calls.append((command, kwargs)))

    installer["main"](platform="linux")

    command, options = install_calls[0]
    assert command == [
        installer["sys"].executable, "-m", "playwright", "install",
        "--only-shell", "--with-deps", "chromium",
    ]
    assert options["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(webwatch.PLAYWRIGHT_BROWSERS_DIR)
    assert verified == [True]
