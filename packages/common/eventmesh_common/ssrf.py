"""
SSRF protection (PRD §61).

Webhook destinations are user-controlled. Hostname string checks alone
are not sufficient (DNS rebinding, redirects) — PRD explicitly calls
this out: "Do not assume hostname validation alone prevents SSRF."

This validator:
  1. Rejects non-http(s) schemes.
  2. Resolves the hostname to concrete IP addresses.
  3. Rejects the request if ANY resolved address is private, loopback,
     link-local, multicast, reserved, or a known cloud metadata address.
  4. Is re-run by the delivery engine at send-time (not just at
     registration time), because DNS answers can change between
     registration and delivery (rebinding).

Redirects are handled separately: PRD §62 sets max_redirects=0, so a
redirect response is classified as a permanent failure rather than
followed — this closes the most common SSRF-via-redirect bypass without
needing per-hop validation.
"""
import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTS = {"metadata.google.internal"}
_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure metadata
}


class SSRFValidationError(ValueError):
    pass


def validate_webhook_url(url: str, max_length: int = 2048) -> None:
    if len(url) > max_length:
        raise SSRFValidationError(f"URL exceeds max length of {max_length}")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFValidationError("only http/https schemes are allowed")
    if not parsed.hostname:
        raise SSRFValidationError("URL must include a hostname")
    if parsed.hostname.lower() in _BLOCKED_HOSTS:
        raise SSRFValidationError("this hostname is blocked")

    try:
        addr_infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        raise SSRFValidationError(f"could not resolve hostname: {e}") from e

    if not addr_infos:
        raise SSRFValidationError("hostname did not resolve to any address")

    for family, _type, _proto, _canon, sockaddr in addr_infos:
        ip = ipaddress.ip_address(sockaddr[0])
        _reject_if_unsafe(ip)


def _reject_if_unsafe(ip: ipaddress._BaseAddress) -> None:
    if ip in _METADATA_IPS:
        raise SSRFValidationError(f"{ip} is a cloud metadata address")
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise SSRFValidationError(f"{ip} resolves to a private/internal address range")
