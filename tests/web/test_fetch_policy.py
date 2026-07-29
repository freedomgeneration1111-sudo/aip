"""SSRF and URL policy tests for ``aip.adapter.web.policy`` (ADR-017 WS-1).

Exhaustive matrix of denials and allows.  These tests are the security
contract for the WS-2 HTTP fetcher — every case listed here MUST be
denied (or allowed, as specified) by ``is_url_allowed``.
"""

from __future__ import annotations

import ipaddress

import pytest

from aip.adapter.web.policy import (
    REASON_EMPTY_HOST,
    REASON_EMPTY_URL,
    REASON_NO_SCHEME,
    REASON_OBFUSCATED_IP_DENIED,
    REASON_OK,
    REASON_PRIVATE_NETWORK,
    REASON_SCHEME_DENIED,
    REASON_UNRESOLVED_IP,
    is_ip_allowed,
    is_url_allowed,
)
from aip.foundation.schemas.web import FetchPolicy

# ---------------------------------------------------------------------------
# is_url_allowed — basic structure checks
# ---------------------------------------------------------------------------


def test_empty_url_denied(strict_policy):
    allowed, reason = is_url_allowed("", strict_policy)
    assert allowed is False
    assert reason == REASON_EMPTY_URL


def test_whitespace_only_url_denied(strict_policy):
    allowed, reason = is_url_allowed("   ", strict_policy)
    assert allowed is False
    assert reason == REASON_EMPTY_URL


def test_no_scheme_denied(strict_policy):
    allowed, reason = is_url_allowed("example.com/page", strict_policy)
    assert allowed is False
    assert reason == REASON_NO_SCHEME


def test_file_scheme_denied(strict_policy):
    allowed, reason = is_url_allowed("file:///etc/passwd", strict_policy)
    assert allowed is False
    assert reason == REASON_SCHEME_DENIED


def test_ftp_scheme_denied(strict_policy):
    allowed, reason = is_url_allowed("ftp://example.com/file", strict_policy)
    assert allowed is False
    assert reason == REASON_SCHEME_DENIED


def test_http_allowed_for_non_ip_host(strict_policy):
    allowed, reason = is_url_allowed("http://example.com/page", strict_policy)
    assert allowed is True
    assert reason == REASON_UNRESOLVED_IP


def test_https_allowed_for_non_ip_host(strict_policy):
    allowed, reason = is_url_allowed("https://example.com/page", strict_policy)
    assert allowed is True
    assert reason == REASON_UNRESOLVED_IP


def test_empty_host_denied(strict_policy):
    allowed, reason = is_url_allowed("http:///path", strict_policy)
    assert allowed is False
    assert reason == REASON_EMPTY_HOST


def test_scheme_case_insensitive(strict_policy):
    """HTTP and HTTPS in any case should be allowed."""
    allowed, _ = is_url_allowed("HTTP://example.com", strict_policy)
    assert allowed is True
    allowed, _ = is_url_allowed("Https://example.com", strict_policy)
    assert allowed is True


# ---------------------------------------------------------------------------
# SSRF — IPv4 private ranges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.1.2.3",
        "127.255.255.254",
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.1",
        "192.168.0.0",
        "169.254.1.1",
        "169.254.169.254",  # AWS metadata endpoint
        "0.0.0.0",
        "255.255.255.255",
        "224.0.0.1",  # multicast
        "239.0.0.1",  # multicast
        "240.0.0.1",  # reserved
    ],
)
def test_private_ipv4_denied(ip, strict_policy):
    allowed, reason = is_url_allowed(f"http://{ip}/", strict_policy)
    assert allowed is False, f"{ip} should be denied"
    assert reason == REASON_PRIVATE_NETWORK


# ---------------------------------------------------------------------------
# SSRF — IPv6 private ranges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        # IPv6 literals in URLs MUST be bracketed per RFC 3986.
        # Unbracketed forms like "http://::1/" produce an empty host
        # in urlsplit, which is a different denial class (REASON_EMPTY_HOST)
        # and is tested separately.
        "[::1]",
        "[fe80::1]",
        "[fc00::1]",
        "[fd00::1]",
        "[ff00::1]",
        "[::]",
    ],
)
def test_private_ipv6_denied(ip, strict_policy):
    """Bracketed IPv6 literals in private ranges must be denied."""
    allowed, reason = is_url_allowed(f"http://{ip}/", strict_policy)
    assert allowed is False, f"{ip} should be denied"
    assert reason == REASON_PRIVATE_NETWORK


def test_unbracketed_ipv6_denied_as_empty_host(strict_policy):
    """Unbracketed IPv6 (e.g. ``http://::1/``) is a malformed URL —
    urlsplit returns an empty host, which we deny as REASON_EMPTY_HOST.
    This is still a denial, just a different class."""
    allowed, reason = is_url_allowed("http://::1/", strict_policy)
    assert allowed is False
    assert reason == REASON_EMPTY_HOST


# ---------------------------------------------------------------------------
# SSRF — IPv4-mapped IPv6
# ---------------------------------------------------------------------------


def test_ipv4_mapped_ipv6_loopback_denied(strict_policy):
    """::ffff:127.0.0.1 must be denied — it maps to loopback."""
    allowed, reason = is_url_allowed("http://[::ffff:127.0.0.1]/", strict_policy)
    assert allowed is False
    assert reason == REASON_PRIVATE_NETWORK


def test_ipv4_mapped_ipv6_private_denied(strict_policy):
    """::ffff:10.0.0.1 must be denied — it maps to private 10/8."""
    allowed, reason = is_url_allowed("http://[::ffff:10.0.0.1]/", strict_policy)
    assert allowed is False
    assert reason == REASON_PRIVATE_NETWORK


# ---------------------------------------------------------------------------
# SSRF — obfuscated IPv4 forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obf",
    [
        "2130706433",       # decimal int = 127.0.0.1
        "0x7f000001",       # hex int = 127.0.0.1
        "0177.0.0.1",       # octal first octet = 127.0.0.1
        "0x7f.0.0.1",       # hex first octet = 127.0.0.1
        "0x7f.0x00.0x00.0x01",  # all hex = 127.0.0.1
    ],
)
def test_obfuscated_ipv4_denied(obf, strict_policy):
    """Obfuscated IP literals are denied regardless of the underlying address."""
    allowed, reason = is_url_allowed(f"http://{obf}/", strict_policy)
    assert allowed is False, f"{obf} should be denied"
    assert reason == REASON_OBFUSCATED_IP_DENIED


# ---------------------------------------------------------------------------
# SSRF — public IPs allowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "8.8.8.8",
        "1.1.1.1",
        "172.217.16.46",  # google.com-ish; not in 172.16/12
    ],
)
def test_public_ipv4_allowed(ip, strict_policy):
    """Public IPv4 literals should be allowed under the strict policy.

    Note: TEST-NET ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24)
    are marked as ``is_private`` by Python's ipaddress module per RFC 6890
    and are therefore denied — see ``test_documentation_ranges_denied``.
    """
    allowed, reason = is_url_allowed(f"http://{ip}/", strict_policy)
    assert allowed is True, f"{ip} should be allowed"
    assert reason == REASON_OK


@pytest.mark.parametrize(
    "ip",
    [
        "192.0.2.1",     # TEST-NET-1 (RFC 5737)
        "198.51.100.1",  # TEST-NET-2
        "203.0.113.1",   # TEST-NET-3
    ],
)
def test_documentation_ranges_denied(ip, strict_policy):
    """RFC 5737 documentation ranges are not globally reachable and are
    denied by ``ipaddress.is_private`` — this is the safe default."""
    allowed, reason = is_url_allowed(f"http://{ip}/", strict_policy)
    assert allowed is False, f"{ip} should be denied"
    assert reason == REASON_PRIVATE_NETWORK


# ---------------------------------------------------------------------------
# SSRF — allow_private_networks=True relaxes the check
# ---------------------------------------------------------------------------


def test_allow_private_networks_permits_loopback(private_allowed_policy):
    """When allow_private_networks=True, loopback is allowed (for local fixtures)."""
    allowed, reason = is_url_allowed("http://127.0.0.1/", private_allowed_policy)
    assert allowed is True
    assert reason == REASON_OK


def test_allow_private_networks_permits_ipv6_loopback(private_allowed_policy):
    allowed, reason = is_url_allowed("http://[::1]/", private_allowed_policy)
    assert allowed is True
    assert reason == REASON_OK


# ---------------------------------------------------------------------------
# is_ip_allowed — direct unit tests
# ---------------------------------------------------------------------------


def test_is_ip_allowed_loopback_denied(strict_policy):
    allowed, reason = is_ip_allowed(ipaddress.IPv4Address("127.0.0.1"), strict_policy)
    assert allowed is False
    assert reason == REASON_PRIVATE_NETWORK


def test_is_ip_allowed_public_allowed(strict_policy):
    allowed, reason = is_ip_allowed(ipaddress.IPv4Address("8.8.8.8"), strict_policy)
    assert allowed is True
    assert reason == REASON_OK


def test_is_ip_allowed_ipv6_loopback_denied(strict_policy):
    allowed, reason = is_ip_allowed(ipaddress.IPv6Address("::1"), strict_policy)
    assert allowed is False
    assert reason == REASON_PRIVATE_NETWORK


def test_is_ip_allowed_ipv6_public_allowed(strict_policy):
    """2606:4700::1 (Cloudflare) is a public IPv6 — should be allowed."""
    allowed, reason = is_ip_allowed(ipaddress.IPv6Address("2606:4700::1"), strict_policy)
    assert allowed is True
    assert reason == REASON_OK


def test_is_ip_allowed_relaxed_by_policy(private_allowed_policy):
    allowed, reason = is_ip_allowed(ipaddress.IPv4Address("10.0.0.1"), private_allowed_policy)
    assert allowed is True
    assert reason == REASON_OK


# ---------------------------------------------------------------------------
# Scheme allowlist customization
# ---------------------------------------------------------------------------


def test_custom_scheme_allowlist():
    """A policy that allows only https should deny http URLs."""
    https_only = FetchPolicy(allowed_schemes=("https",))
    allowed, reason = is_url_allowed("http://example.com", https_only)
    assert allowed is False
    assert reason == REASON_SCHEME_DENIED

    allowed, reason = is_url_allowed("https://example.com", https_only)
    assert allowed is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_url_with_port_allowed(strict_policy):
    """Non-IP host with port should be allowed (DNS check deferred to fetcher)."""
    allowed, reason = is_url_allowed("http://example.com:8080/", strict_policy)
    assert allowed is True
    assert reason == REASON_UNRESOLVED_IP


def test_url_with_userinfo_allowed_for_non_ip_host(strict_policy):
    """Userinfo in URL is allowed at the policy layer (the fetcher may strip it)."""
    allowed, reason = is_url_allowed("https://user:pass@example.com/", strict_policy)
    assert allowed is True
    assert reason == REASON_UNRESOLVED_IP


def test_url_with_private_host_in_userinfo_denied(strict_policy):
    """If the host (not userinfo) is a private IP, deny — userinfo is irrelevant."""
    allowed, reason = is_url_allowed("https://user:pass@127.0.0.1/", strict_policy)
    assert allowed is False
    assert reason == REASON_PRIVATE_NETWORK


def test_uppercase_http_scheme_allowed(strict_policy):
    """Scheme matching is case-insensitive."""
    allowed, _ = is_url_allowed("HTTP://example.com/", strict_policy)
    assert allowed is True
