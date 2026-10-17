"""Playwright rendering tests with all network responses supplied locally."""

import sys
import asyncio
from types import ModuleType, SimpleNamespace

import pytest

import app as webwatch


def patch_resource_fetch(monkeypatch, fetcher):
    async def fetch(url, **kwargs):
        kwargs.pop("validate_url", None)
        kwargs.pop("deadline", None)
        return fetcher(url, **kwargs)
    monkeypatch.setattr(webwatch, "fetch_public_resource_async", fetch)


def test_render_timeout_diagnostic_exposes_only_bounded_public_request_metadata():
    diagnostic = webwatch.sanitized_render_request_diagnostic(
        "https://user:password@news.example/story?id=secret#fragment",
        "script", "main_document", "started", 90_000,
    )

    assert diagnostic == {
        "host": "news.example",
        "resource_type": "script",
        "role": "main_document",
        "phase": "started",
        "elapsed_ms": 35_000,
    }
    assert "password" not in str(diagnostic)
    assert "story" not in str(diagnostic)
    assert "secret" not in str(diagnostic)
    assert "fragment" not in str(diagnostic)


def test_chromium_launch_failure_logs_type_and_redacted_message(monkeypatch, caplog):
    launch_options = []

    class Chromium:
        executable_path = str(
            webwatch.PLAYWRIGHT_BROWSERS_DIR / "chromium-1248" / "chrome-win64" / "chrome.exe"
        )

        @staticmethod
        async def launch(**kwargs):
            launch_options.append(kwargs)
            raise RuntimeError(
                "launch failed at https://user:password@auth.example/private/page?token=topsecret "
                "Authorization: Bearer api-secret"
            )

    class AsyncManager:
        async def __aenter__(self):
            return SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *_args):
            return False

    playwright_package = ModuleType("playwright")
    playwright_package.__path__ = []
    async_api = ModuleType("playwright.async_api")
    async_api.async_playwright = AsyncManager
    async_api.TimeoutError = type("PlaywrightTimeoutError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    with caplog.at_level("WARNING", logger="webwatch.render"):
        with pytest.raises(webwatch.WebsiteTemporaryError, match="Check the browser installation"):
            webwatch.render_page_text("https://example.com/")

    assert "phase=chromium_launch" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "launch failed at https://auth.example/[redacted]" in caplog.text
    for sensitive in ("user", "password", "/private/page", "topsecret", "api-secret"):
        assert sensitive not in caplog.text
    assert launch_options[0]["headless"] is True
    assert launch_options[0]["executable_path"] == str(
        webwatch.playwright_headless_shell_path(Chromium.executable_path)
    )


def test_rendered_javascript_text_and_private_subresource_blocking(monkeypatch):
    requested = []
    safely_fetched = []

    def safe_resource(url, accept="*/*", max_bytes=512 * 1024, require_html=False):
        requested.append(url)
        webwatch.validated_http_url(url)
        safely_fetched.append(url)
        if url == "https://example.com/start":
            html = b"<html><head><title>Example</title><script defer src='/dynamic.js'></script></head><body>Shell<img src='http://127.0.0.1/private.png'></body></html>"
            return html, {"Content-Type": "text/html; charset=utf-8"}, "https://example.com/final/"
        if url == "https://example.com/dynamic.js":
            script = b"document.body.insertAdjacentHTML('beforeend', '<p>Rendered notice</p>'); fetch('http://127.0.0.1/admin');"
            return script, {"Content-Type": "application/javascript"}, url
        pytest.fail(f"Unexpected resource URL: {url}")

    monkeypatch.setattr(
        webwatch.socket,
        "getaddrinfo",
        lambda host, port, type: [(None, None, None, None, ("93.184.216.34", port))],
    )
    patch_resource_fetch(monkeypatch, safe_resource)

    text = webwatch.render_page_text("https://example.com/start")

    assert "Rendered notice" in text
    assert "Shell" in text
    assert "http://127.0.0.1/admin" in requested
    assert "http://127.0.0.1/admin" not in safely_fetched
    assert "http://127.0.0.1/private.png" in requested
    assert "http://127.0.0.1/private.png" not in safely_fetched
    assert requested[:2] == ["https://example.com/start", "https://example.com/dynamic.js"]


def test_rendered_challenge_is_rejected(monkeypatch):
    html = (
        b"<html><head><title>Just a moment...</title></head><body>"
        b"<h1>Checking your browser before accessing</h1>"
        b"<div id='cf-chl-widget'>Please enable JavaScript and cookies to continue.</div>"
        b"</body></html>"
    )
    monkeypatch.setattr(
        webwatch,
        "fetch_public_resource_async",
        lambda url, **_kwargs: asyncio.sleep(0, result=(html, {"Content-Type": "text/html"}, url)),
    )

    with pytest.raises(webwatch.WebsiteBlockedError, match="No page snapshot was saved"):
        webwatch.render_page_text("https://example.com/")


def test_playwright_capture_screenshot_and_apply_saved_regions_safely(monkeypatch):
    html = b"""<html><body><header>Ignore this header</header>
      <main><article id='policy'>Policy is open</article><time id='clock'>Updated 4 minutes ago</time></main>
      <aside>Sponsored content</aside></body></html>"""
    monkeypatch.setattr(
        webwatch, "fetch_public_resource_async",
        lambda url, **_kwargs: asyncio.sleep(0, result=(html, {"Content-Type": "text/html"}, url)),
    )

    rendered = webwatch.render_page_text("https://example.com/", selectors={
        "watched": ["#policy"], "ignored": ["#clock"],
    })
    preview = webwatch.render_page_text("https://example.com/", preview=True)

    assert "Policy is open" in rendered
    assert "Ignore this header" not in rendered
    assert "Updated 4 minutes ago" not in rendered
    assert rendered.screenshot and len(rendered.screenshot) <= webwatch.MAX_SCREENSHOT_BYTES
    assert any(region["selector"] == "#policy" for region in preview.regions)
    assert preview.screenshot


def test_failed_third_party_iframe_does_not_discard_main_page_content(monkeypatch):
    requested = []
    main_url = "https://www.indiatv.in/"
    iframe_url = "https://dunsregistered.dnb.com/SealAuthentication.aspx?Cid=1"
    html = (
        b"<html><body><main>IndiaTV headlines remain available</main>"
        b'<iframe title="publisher badge" src="https://dunsregistered.dnb.com/SealAuthentication.aspx?Cid=1"></iframe>'
        b"</body></html>"
    )

    def safe_resource(url, **_kwargs):
        requested.append(url)
        if url == main_url:
            return html, {"Content-Type": "text/html"}, url
        if url == iframe_url:
            raise webwatch.WebsiteHTTPError("The webpage returned HTTP 500.")
        pytest.fail(f"Unexpected resource URL: {url}")

    patch_resource_fetch(monkeypatch, safe_resource)

    result = webwatch.render_page_text(main_url)

    assert "IndiaTV headlines remain available" in result
    assert iframe_url in requested


def test_per_site_document_limit_does_not_raise_subresource_limit(monkeypatch):
    limits = {}

    def safe_resource(url, max_bytes, require_html=False, **_kwargs):
        limits[url] = (max_bytes, require_html)
        if url == "https://example.com/":
            return (
                b"<html><body><script defer src='/asset.js'></script>Page content</body></html>",
                {"Content-Type": "text/html"}, url,
            )
        return b"document.body.dataset.loaded='true';", {"Content-Type": "application/javascript"}, url

    patch_resource_fetch(monkeypatch, safe_resource)
    result = webwatch.render_page_text("https://example.com/", max_document_size_kib=768)

    assert "Page content" in result
    assert limits["https://example.com/"] == (768 * 1024, True)
    assert limits["https://example.com/asset.js"] == (512 * 1024, False)


def test_document_size_failure_names_the_configured_limit(monkeypatch):
    monkeypatch.setattr(
        webwatch, "fetch_public_resource_async",
        lambda _url, **_kwargs: (_ for _ in ()).throw(
            ValueError("The webpage resource is too large to check.")
        ),
    )

    with pytest.raises(webwatch.WebsiteResourceLimitError, match="768 KiB per-resource limit"):
        webwatch.render_page_text("https://example.com/", max_document_size_kib=768)


def test_oversized_optional_subresource_does_not_discard_main_document(monkeypatch):
    requested = []

    def safe_resource(url, **_kwargs):
        requested.append(url)
        if url == "https://example.com/":
            return (
                b"<html><body><script src='/large.js'></script><main>Usable page content</main></body></html>",
                {"Content-Type": "text/html"}, url,
            )
        raise ValueError("The webpage resource is too large to check.")

    patch_resource_fetch(monkeypatch, safe_resource)
    result = webwatch.render_page_text("https://example.com/")

    assert "Usable page content" in result
    assert requested == ["https://example.com/", "https://example.com/large.js"]


def test_aggregate_budget_skips_optional_subresources_and_keeps_document(monkeypatch):
    script_tags = "".join(f"<script src='/asset-{index}.js'></script>" for index in range(20))
    html = f"<html><body><main>Aggregate budget page</main>{script_tags}</body></html>".encode()
    total_bytes = 0
    skipped = []

    def bounded_resource(url, max_bytes, require_html=False, **_kwargs):
        nonlocal total_bytes
        body = html if require_html else b" " * (512 * 1024)
        if len(body) > max_bytes:
            skipped.append(url)
            raise ValueError("The webpage resource is too large to check.")
        total_bytes += len(body)
        return body, {"Content-Type": "text/html" if require_html else "application/javascript"}, url

    patch_resource_fetch(monkeypatch, bounded_resource)
    result = webwatch.render_page_text("https://example.com/")

    assert "Aggregate budget page" in result
    assert total_bytes <= 8 * 1024 * 1024
    assert skipped


def test_playwright_invalid_watched_selector_returns_clear_error(monkeypatch):
    html = b"<html><body><main>Page content</main></body></html>"
    patch_resource_fetch(
        monkeypatch,
        lambda url, **_kwargs: (html, {"Content-Type": "text/html"}, url),
    )

    with pytest.raises(webwatch.WebsiteSelectorError, match="selector"):
        webwatch.render_page_text("https://example.com/", selectors={"watched": ["[broken"], "ignored": []})


def test_playwright_resource_limit_caps_subresource_requests(monkeypatch):
    images = "".join(
        f"<img src='/asset-{index}.png'>"
        for index in range(webwatch.MAX_RENDER_REQUESTS + 10)
    )
    html = f"<html><body><main>Request capped</main>{images}</body></html>".encode()
    requested = []

    def fixture_resource(url, **_kwargs):
        requested.append(url)
        if url == "https://fixture.example/":
            return html, {"Content-Type": "text/html"}, url
        return b"image", {"Content-Type": "image/png"}, url

    patch_resource_fetch(monkeypatch, fixture_resource)
    result = webwatch.render_page_text("https://fixture.example/")

    assert "Request capped" in result
    assert len(requested) <= webwatch.MAX_RENDER_REQUESTS


def test_fast_main_document_remains_responsive_while_slow_subresource_fetches(monkeypatch):
    event_loop_responsive = asyncio.Event()
    cancelled = asyncio.Event()
    requested = []

    async def safe_resource(url, **_kwargs):
        requested.append(url)
        if url == "https://example.com/":
            async def tick():
                await asyncio.sleep(0.03)
                event_loop_responsive.set()
            asyncio.create_task(tick())
            return (b"<html><body><script defer src='/slow.js'></script>Shell</body></html>",
                    {"Content-Type": "text/html"}, url)
        assert url == "https://example.com/slow.js"
        await asyncio.wait_for(event_loop_responsive.wait(), timeout=0.2)
        try:
            await asyncio.sleep(2)
            return (b"document.body.insertAdjacentHTML('beforeend','<p>Loaded</p>');",
                    {"Content-Type": "application/javascript"}, url)
        finally:
            cancelled.set()

    monkeypatch.setattr(webwatch, "fetch_public_resource_async", safe_resource)
    result = webwatch.render_page_text("https://example.com/")

    assert "Shell" in result and "Loaded" not in result
    assert requested == ["https://example.com/", "https://example.com/slow.js"]
    assert event_loop_responsive.is_set()
    assert cancelled.is_set()


def test_render_timeout_cancels_pending_route_and_releases_renderer_slot(monkeypatch):
    cancelled = asyncio.Event()
    # Browser startup on CI can take a few seconds; the route itself remains
    # blocked far beyond the 5-second end-to-end deadline.
    monkeypatch.setattr(webwatch, "RENDER_DEADLINE_SECONDS", 5)
    monkeypatch.setattr(webwatch, "RENDER_CLEANUP_RESERVE_SECONDS", 0.5)

    async def slow_resource(url, **_kwargs):
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    monkeypatch.setattr(webwatch, "fetch_public_resource_async", slow_resource)
    with pytest.raises(webwatch.WebsiteTimeoutError):
        webwatch.render_page_text("https://example.com/")
    assert cancelled.is_set()

    monkeypatch.setattr(
        webwatch, "fetch_public_resource_async",
        lambda url, **_kwargs: asyncio.sleep(0, result=(
            b"<html><body>Recovered after timeout</body></html>",
            {"Content-Type": "text/html"}, url,
        )),
    )
    assert "Recovered after timeout" in webwatch.render_page_text("https://example.com/")


def test_concurrent_subresources_obey_bounded_fetch_and_aggregate_byte_budget(monkeypatch):
    images = "".join(f"<img src='/asset-{index}.png'>" for index in range(24))
    html = f"<html><body><main>Budget page</main>{images}</body></html>".encode()
    active = 0
    maximum_active = 0
    consumed = 0
    guard = asyncio.Lock()

    async def bounded_resource(url, max_bytes, require_html=False, **_kwargs):
        nonlocal active, maximum_active, consumed
        if require_html:
            body = html
        else:
            async with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                await asyncio.sleep(0.02)
                body = b"x" * max_bytes
            finally:
                async with guard:
                    active -= 1
        async with guard:
            consumed += len(body)
        return body, {"Content-Type": "text/html" if require_html else "image/png"}, url

    monkeypatch.setattr(webwatch, "fetch_public_resource_async", bounded_resource)
    result = webwatch.render_page_text("https://example.com/")

    assert "Budget page" in result
    assert maximum_active <= 4
    assert consumed <= 8 * 1024 * 1024
