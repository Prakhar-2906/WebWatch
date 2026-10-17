"""Security and cancellation tests for the cancellable async fetcher."""

import asyncio
import threading
import time

import pytest

from async_public_fetch import (
    PublicFetchTimeout,
    ResourceTooLarge,
    _validate_twice,
    fetch_public_resource_async,
)


async def start_http_fixture(responses):
    requests = []

    async def handle(reader, writer):
        try:
            request_line = await reader.readline()
            path = request_line.decode("ascii", "replace").split(" ")[1]
            requests.append(path)
            while await reader.readline() not in (b"\r\n", b"\n", b""):
                pass
            status, headers, body = responses[path]
            writer.write(
                f"HTTP/1.1 {status}\r\n".encode()
                + b"".join(f"{key}: {value}\r\n".encode() for key, value in headers.items())
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, requests, port


def run(coro):
    return asyncio.run(coro)


def test_async_fetch_revalidates_redirect_destination_and_pins_connection():
    async def scenario():
        server, requests, port = await start_http_fixture({
            "/start": (302, {"Location": "/target"}, b""),
            "/target": (200, {"Content-Type": "text/html", "Content-Length": "4"}, b"page"),
        })
        validations = []

        def validate(url):
            validations.append(url)
            return url, "public.example", {"127.0.0.1"}

        try:
            url = f"http://public.example:{port}/start"
            body, _headers, final_url = await fetch_public_resource_async(
                url, validate_url=validate, deadline=asyncio.get_running_loop().time() + 3,
                require_html=True,
            )
            assert body == b"page"
            assert final_url.endswith("/target")
            assert requests == ["/start", "/target"]
            assert validations.count(url) == 2
            assert validations.count(final_url) == 2
        finally:
            server.close()
            await server.wait_closed()

    run(scenario())


def test_async_fetch_enforces_streamed_per_resource_byte_limit():
    async def scenario():
        # Chunked transfer avoids relying on a Content-Length precheck.
        async def handle(reader, writer):
            try:
                await reader.readline()
                while await reader.readline() not in (b"\r\n", b"\n", b""):
                    pass
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nTransfer-Encoding: chunked\r\n\r\n")
                writer.write(b"5\r\n12345\r\n0\r\n\r\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        url = f"http://public.example:{port}/large"
        try:
            with pytest.raises(ResourceTooLarge):
                await fetch_public_resource_async(
                    url,
                    validate_url=lambda value: (value, "public.example", {"127.0.0.1"}),
                    deadline=asyncio.get_running_loop().time() + 3,
                    max_bytes=4,
                )
        finally:
            server.close()
            await server.wait_closed()

    run(scenario())


def test_async_fetch_cancellation_closes_the_pending_connection():
    async def scenario():
        disconnected = asyncio.Event()
        connected = asyncio.Event()

        async def handle(reader, writer):
            try:
                connected.set()
                await reader.readline()
                while await reader.readline() not in (b"\r\n", b"\n", b""):
                    pass
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n\r\npartial")
                await writer.drain()
                await reader.read()
            finally:
                disconnected.set()
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        url = f"http://public.example:{port}/slow"
        task = asyncio.create_task(fetch_public_resource_async(
            url,
            validate_url=lambda value: (value, "public.example", {"127.0.0.1"}),
            deadline=asyncio.get_running_loop().time() + 10,
        ))
        try:
            await asyncio.wait_for(connected.wait(), 1)
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(disconnected.wait(), 1)
        finally:
            task.cancel()
            server.close()
            await server.wait_closed()

    run(scenario())


def test_expired_dns_validation_finishes_before_the_render_loop_returns():
    resolver_finished = threading.Event()

    def slow_system_resolver(url):
        time.sleep(0.05)
        resolver_finished.set()
        return url, "public.example", {"93.184.216.34"}

    async def scenario():
        with pytest.raises(PublicFetchTimeout, match="expired during URL validation"):
            await _validate_twice(
                slow_system_resolver,
                "https://public.example/",
                asyncio.get_running_loop().time() + 0.005,
            )

    run(scenario())
    # asyncio.run shuts down its default executor before returning. This keeps
    # a timed-out OS resolver call from continuing after render completion,
    # though the system resolver itself can make the render exceed its deadline.
    assert resolver_finished.is_set()
