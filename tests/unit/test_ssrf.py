import ipaddress
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "common"))

from eventmesh_common.ssrf import SSRFValidationError, _reject_if_unsafe, validate_webhook_url  # noqa: E402


def test_rejects_non_http_scheme():
    with pytest.raises(SSRFValidationError, match="scheme"):
        validate_webhook_url("ftp://example.com/hook")


def test_rejects_overlong_url():
    with pytest.raises(SSRFValidationError, match="length"):
        validate_webhook_url("https://example.com/" + "a" * 3000, max_length=2048)


def test_rejects_missing_hostname():
    with pytest.raises(SSRFValidationError):
        validate_webhook_url("https:///path")


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
    "169.254.169.254", "::1", "fc00::1", "fe80::1",
])
def test_rejects_private_and_metadata_ips(ip):
    with pytest.raises(SSRFValidationError):
        _reject_if_unsafe(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34"])
def test_allows_public_ips(ip):
    _reject_if_unsafe(ipaddress.ip_address(ip))  # should not raise


def test_rejects_blocked_hostname(monkeypatch):
    with pytest.raises(SSRFValidationError, match="blocked"):
        validate_webhook_url("https://metadata.google.internal/hook")


def test_resolution_failure_is_rejected(monkeypatch):
    def fake_getaddrinfo(*_args, **_kwargs):
        raise socket.gaierror("name not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFValidationError, match="resolve"):
        validate_webhook_url("https://nonexistent.invalid/hook")


def test_dns_rebinding_style_case_rejected(monkeypatch):
    # Hostname looks legitimate but resolves to a private IP.
    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFValidationError):
        validate_webhook_url("https://looks-legit.example.com/hook")
