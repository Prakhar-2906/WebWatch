"""Cancellable async public-resource fetching with per-request IP pinning."""

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin

import aiohttp
from aiohttp.abc import AbstractResolver


class ResourceTooLarge(ValueError):
    def __init__(self, message, consumed_bytes=0):
        self.consumed_bytes = consumed_bytes
        super().__init__(message)


class PublicHTTPError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"The webpage returned HTTP {status}.")


class PublicFetchTimeout(Exception):
    def __init__(self, message, consumed_bytes=0):
        self.consumed_bytes = consumed_bytes
        super().__init__(message)


class PublicFetchTLSFailure(Exception):
    pass


class PublicFetchUnreachable(Exception):
    def __init__(self, message, consumed_bytes=0):
        self.consumed_bytes = consumed_bytes
        super().__init__(message)


class PublicFetchFailure(ValueError):
    """A network/protocol failure that still accounts for bytes already read."""

    def __init__(self, message, consumed_bytes=0):
        self.consumed_bytes = consumed_bytes
        super().__init__(message)


class PinnedResolver(AbstractResolver):
    """Return only addresses validated for this request's hostname."""

    def __init__(self, hostname, addresses):
        self.hostname = hostname.lower().rstrip(".")
        self.addresses = tuple(addresses)

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if host.lower().rstrip(".") != self.hostname:
            raise OSError("Request host did not match the validated public hostname.")
        records = []
        for address in self.addresses:
            parsed = ipaddress.ip_address(address)
            address_family = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET
            if family not in (socket.AF_UNSPEC, address_family):
                continue
            records.append({
                "hostname": host,
                "host": str(parsed),
                "port": port,
                "family": address_family,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            })
        if not records:
            raise OSError("No validated address is available for this request.")
        return records

    async def close(self):
        return None


async def _validate_twice(validate_url, url, deadline):
    """Run the existing URL/DNS validator off-loop and reject DNS rebinding."""
    loop = asyncio.get_running_loop()
    remaining = deadline - loop.time()
    if remaining <= 0:
        raise PublicFetchTimeout("The render deadline expired during URL validation.")
    try:
        first = await asyncio.wait_for(asyncio.to_thread(validate_url, url), remaining)
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise PublicFetchTimeout("The render deadline expired during URL validation.")
        second = await asyncio.wait_for(asyncio.to_thread(validate_url, first[0]), remaining)
    except asyncio.TimeoutError as exc:
        raise PublicFetchTimeout("The render deadline expired during URL validation.") from exc
    if first[2] != second[2]:
        raise ValueError("The website address changed while it was being checked. Try again later.")
    return first[0], first[1], first[2]


async def fetch_public_resource_async(
    start_url, *, validate_url, deadline, accept="*/*", max_bytes=512 * 1024,
    require_html=False,
):
    """Fetch through validated, pinned sockets; cancellation closes the active socket."""
    current_url = start_url
    headers = {"User-Agent": "WebWatch/1.0 (manual page check)", "Accept": accept}
    loop = asyncio.get_running_loop()
    for redirect_number in range(6):
        safe_url, hostname, addresses = await _validate_twice(validate_url, current_url, deadline)
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise PublicFetchTimeout("The render deadline expired while fetching a resource.")
        resolver = PinnedResolver(hostname, addresses)
        timeout = aiohttp.ClientTimeout(
            total=remaining,
            connect=min(4.0, remaining),
            sock_connect=min(4.0, remaining),
            sock_read=min(8.0, remaining),
        )
        connector = aiohttp.TCPConnector(
            resolver=resolver, use_dns_cache=False, force_close=True, limit=1,
        )
        size = 0
        try:
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout, trust_env=False,
                auto_decompress=True,
            ) as session:
                async with session.get(
                    safe_url, headers=headers, allow_redirects=False,
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        if redirect_number >= 5:
                            raise ValueError("The webpage redirected too many times.")
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError("The webpage returned a redirect without a destination.")
                        current_url = urljoin(safe_url, location)
                        continue
                    if response.status >= 400:
                        raise PublicHTTPError(response.status)
                    content_type = response.headers.get("Content-Type", "").lower()
                    if require_html and not any(
                        allowed in content_type
                        for allowed in ("text/html", "application/xhtml+xml")
                    ):
                        raise ValueError("The address did not return an HTML webpage.")
                    if response.content_length is not None and response.content_length > max_bytes:
                        raise ResourceTooLarge("The webpage resource is too large to check.")
                    chunks = []
                    size = 0
                    while True:
                        remaining_bytes = max_bytes - size
                        if remaining_bytes <= 0:
                            if response.content.at_eof():
                                break
                            raise ResourceTooLarge(
                                "The webpage resource is too large to check.", size,
                            )
                        chunk = await response.content.read(min(16 * 1024, remaining_bytes))
                        if not chunk:
                            break
                        size += len(chunk)
                        chunks.append(chunk)
                    return b"".join(chunks), dict(response.headers), safe_url
        except PublicHTTPError:
            raise
        except (aiohttp.ClientSSLError, aiohttp.ServerFingerprintMismatch) as exc:
            raise PublicFetchTLSFailure("The webpage's secure connection could not be verified.") from exc
        except asyncio.TimeoutError as exc:
            raise PublicFetchTimeout("The webpage took too long to respond.", size) from exc
        except aiohttp.ClientConnectorCertificateError as exc:
            raise PublicFetchTLSFailure("The webpage's secure connection could not be verified.") from exc
        except aiohttp.ClientConnectorError as exc:
            raise PublicFetchUnreachable(
                "Could not establish a network connection to the webpage.", size,
            ) from exc
        except aiohttp.ClientError as exc:
            raise PublicFetchFailure(
                "The webpage could not be checked because of a network error.", size,
            ) from exc
    raise ValueError("The webpage could not be checked.")
