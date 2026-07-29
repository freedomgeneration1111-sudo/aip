"""URL policy and SSRF guard for Web Source Acquisition (ADR-017 WS-1).

Pure-stdlib module that decides whether a URL is safe to fetch.
The HTTP fetcher (WS-2) calls ``is_url_allowed`` on the requested URL
and on every redirect hop; it also resolves the hostname and calls
``is_ip_allowed`` on each resolved address to defeat DNS rebinding.

This module MUST NOT import any network library — it is referenced
by the fake provider and by tests that must run without network
(``tests/test_no_network.py`` contract).  DNS resolution happens in
the fetcher, not here.

SSRF defense matrix (denied when ``allow_private_networks=False``):

    - IPv4 loopback         127.0.0.0/8
    - IPv4 private          10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
    - IPv4 link-local       169.254.0.0/16
    - IPv4 multicast        224.0.0.0/4
    - IPv4 broadcast        255.255.255.255
    - IPv4 unspecified      0.0.0.0
    - IPv4 CARP / vRRP      224.0.0.0/8 (overlap with multicast; covered)
    - IPv6 loopback         ::1
    - IPv6 link-local       fe80::/10
    - IPv6 unique-local     fc00::/7
    - IPv6 multicast        ff00::/8
    - IPv6 unspecified      ::
    - IPv6 IPv4-mapped      ::ffff:127.0.0.1 (and all mapped private IPs)
    - Decimal/octal/hex IP  2130706433, 0177.0.0.1, 0x7f.0x0.0x0.0x1
    - Empty host            denied
    - Non-IP host           allowed (DNS resolution happens in fetcher)

The function returns ``(allowed, reason)`` so callers can log a
structured denial reason without parsing exception strings.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from aip.foundation.schemas.web import FetchPolicy

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Schemes that may appear in a fetched URL.  Overridable via FetchPolicy.
DEFAULT_ALLOWED_SCHEMES: tuple[str, ...] = ("http", "https")

#: Reasons returned by ``is_url_allowed`` / ``is_ip_allowed``.
REASON_OK = "ok"
REASON_EMPTY_URL = "empty url"
REASON_NO_SCHEME = "no scheme"
REASON_EMPTY_HOST = "empty host"
REASON_SCHEME_DENIED = "scheme not allowed"
REASON_PRIVATE_NETWORK = "private/loopback/link-local/multicast address denied"
REASON_UNRESOLVED_IP = "host is not an IP literal; defer to fetcher DNS check"
REASON_INVALID_IP = "host looks like an IP but failed to parse"
REASON_OBFUSCATED_IP_DENIED = "obfuscated IP literal denied"

# ---------------------------------------------------------------------------
# IP-literal parsing (handles decimal / octal / hex / IPv6 forms)
# ---------------------------------------------------------------------------


def _parse_dotted_decimal(host: str) -> ipaddress.IPv4Address | None:
    """Parse a dotted-decimal IPv4 literal strictly.

    Returns ``None`` if ``host`` is not a strict dotted-decimal IPv4
    (4 octets, each 0–255, no leading zeros except for the literal
    "0").  Strict parsing rejects obfuscated forms like ``2130706433``
    or ``0177.0.0.1`` — those are handled by ``_parse_obfuscated_ipv4``.
    """
    parts = host.split(".")
    if len(parts) != 4:
        return None
    octets: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        # Reject leading zeros (e.g. "0177") — they are an obfuscation signal.
        if len(part) > 1 and part.startswith("0"):
            return None
        value = int(part)
        if value < 0 or value > 255:
            return None
        octets.append(value)
    try:
        return ipaddress.IPv4Address(bytes(octets))
    except ValueError:
        return None


def _parse_obfuscated_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse obfuscated IPv4 literals: single int, hex, octal, mixed.

    Examples that this function recognizes and converts (so that we
    can then apply the SSRF check consistently):
        - ``2130706433``      (decimal, = 127.0.0.1)
        - ``0x7f000001``      (hex, = 127.0.0.1)
        - ``0177.0.0.1``      (octal first octet, = 127.0.0.1)
        - ``0x7f.0.0.1``      (hex first octet, = 127.0.0.1)

    Returns ``None`` if the host is not a parseable obfuscated IPv4.

    NOTE: obfuscated forms are denied by ``is_url_allowed`` regardless
    of whether they resolve to a public IP — they are an obfuscation
    signal that should never appear in a legitimate fetch target.
    """
    # Single-integer form (e.g. "2130706433")
    if "." not in host:
        try:
            value = int(host, 0)  # auto-detects base from prefix
        except (ValueError, TypeError):
            return None
        if value < 0 or value > 0xFFFFFFFF:
            return None
        try:
            return ipaddress.IPv4Address(value)
        except ValueError:
            return None

    # Dotted form with at least one hex/octal octet
    parts = host.split(".")
    if len(parts) != 4:
        return None
    octets: list[int] = []
    has_obfuscation = False
    for part in parts:
        if len(part) > 1 and (part.startswith("0x") or part.startswith("0X")):
            # Hex octet (e.g. "0x7f")
            try:
                value = int(part, 16)
            except ValueError:
                return None
            has_obfuscation = True
        elif len(part) > 1 and part.startswith("0") and all(c in "01234567" for c in part):
            # Octal octet (e.g. "0177") — Python 3's int(part, 0) rejects
            # leading-zero decimals, so we handle octal explicitly here.
            try:
                value = int(part, 8)
            except ValueError:
                return None
            has_obfuscation = True
        elif part.isdigit():
            # Plain decimal octet (no leading zero unless it's exactly "0")
            value = int(part, 10)
        else:
            return None
        if value < 0 or value > 255:
            return None
        octets.append(value)
    if not has_obfuscation:
        return None
    try:
        return ipaddress.IPv4Address(bytes(octets))
    except ValueError:
        return None


def _parse_ip_literal(host: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address | None, bool, bool]:
    """Parse a host that might be an IP literal.

    Returns a tuple ``(addr, is_obfuscated, is_ip_literal)``:
        - ``addr``             — the parsed IP, or ``None``
        - ``is_obfuscated``    — True if the host was an obfuscated IPv4 form
        - ``is_ip_literal``    — True if the host was any IP literal (plain or obfuscated)

    For non-IP hosts (domain names), returns ``(None, False, False)``.
    """
    # Strip IPv6 brackets: [::1] → ::1
    cleaned = host
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]

    # Try plain IPv6 first (contains ":" or is "::")
    if ":" in cleaned or cleaned == "::":
        try:
            return ipaddress.IPv6Address(cleaned), False, True
        except ValueError:
            return None, False, True  # looked like IPv6 but malformed

    # Try strict dotted-decimal IPv4
    addr = _parse_dotted_decimal(cleaned)
    if addr is not None:
        return addr, False, True

    # Try obfuscated IPv4 (decimal-int, hex, octal)
    obf = _parse_obfuscated_ipv4(cleaned)
    if obf is not None:
        return obf, True, True

    # Not an IP literal — it's a hostname
    return None, False, False


# ---------------------------------------------------------------------------
# SSRF check on a resolved/parseable IP
# ---------------------------------------------------------------------------


def is_ip_allowed(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    policy: FetchPolicy,
) -> tuple[bool, str]:
    """Check whether a resolved IP is allowed by the policy.

    Returns ``(True, REASON_OK)`` if the IP is a public, routable
    address (or if ``policy.allow_private_networks`` is True).

    Returns ``(False, REASON_PRIVATE_NETWORK)`` for:
        - loopback (127.0.0.0/8, ::1)
        - private (10/8, 172.16/12, 192.168/16, fc00::/7)
        - link-local (169.254/16, fe80::/10)
        - multicast (224.0.0.0/4, ff00::/8)
        - unspecified (0.0.0.0, ::)
        - broadcast (255.255.255.255)
        - IPv4-mapped IPv6 that maps to a private IPv4
        - reserved (240.0.0.0/4, etc.)
    """
    if policy.allow_private_networks:
        return True, REASON_OK

    # IPv4-mapped IPv6: extract the embedded IPv4 and re-check
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_ip_allowed(ip.ipv4_mapped, policy)

    if ip.is_loopback:
        return False, REASON_PRIVATE_NETWORK
    if ip.is_private:
        return False, REASON_PRIVATE_NETWORK
    if ip.is_link_local:
        return False, REASON_PRIVATE_NETWORK
    if ip.is_multicast:
        return False, REASON_PRIVATE_NETWORK
    if ip.is_unspecified:
        return False, REASON_PRIVATE_NETWORK
    if ip.is_reserved:
        return False, REASON_PRIVATE_NETWORK

    # IPv4 broadcast (255.255.255.255) — ipaddress treats as reserved/private,
    # but be explicit for clarity.
    if isinstance(ip, ipaddress.IPv4Address) and int(ip) == 0xFFFFFFFF:
        return False, REASON_PRIVATE_NETWORK

    return True, REASON_OK


# ---------------------------------------------------------------------------
# Top-level URL policy check (no DNS — that's the fetcher's job)
# ---------------------------------------------------------------------------


def is_url_allowed(
    url: str,
    policy: FetchPolicy,
) -> tuple[bool, str]:
    """Check whether a URL is allowed by the fetch policy.

    This performs the **static** check (no DNS resolution).  The
    fetcher is responsible for resolving the hostname and calling
    ``is_ip_allowed`` on each resolved address.

    Args:
        url: The URL to check.
        policy: The fetch policy.

    Returns:
        Tuple ``(allowed, reason)``.  ``reason`` is one of the
        ``REASON_*`` constants in this module.

    Denials:
        - Empty URL
        - No scheme
        - Scheme not in ``policy.allowed_schemes``
        - Empty host
        - Host is an IP literal that resolves to a private/loopback/
          link-local/multicast/unspecified/reserved address
        - Host is an **obfuscated** IP literal (denied regardless of
          the underlying address — obfuscation is itself a red flag)
        - Host is a malformed IP literal (looks like one but won't parse)

    Allows (defer DNS check to fetcher):
        - Host is a non-IP domain name
    """
    if not url or not url.strip():
        return False, REASON_EMPTY_URL

    try:
        parts = urlsplit(url)
    except ValueError:
        return False, REASON_INVALID_IP

    scheme = (parts.scheme or "").lower()
    if not scheme:
        return False, REASON_NO_SCHEME
    if scheme not in {s.lower() for s in policy.allowed_schemes}:
        return False, REASON_SCHEME_DENIED

    host = (parts.hostname or "").lower()
    if not host:
        return False, REASON_EMPTY_HOST

    # IP-literal check
    addr, is_obfuscated, is_ip_literal = _parse_ip_literal(host)

    if is_ip_literal and addr is None:
        # Looked like an IP but failed to parse — deny defensively.
        return False, REASON_INVALID_IP

    if is_obfuscated:
        # Obfuscated IP literals are denied regardless of the
        # underlying address.  Legitimate fetches use plain hostnames
        # or plain IP literals.
        return False, REASON_OBFUSCATED_IP_DENIED

    if is_ip_literal and addr is not None:
        return is_ip_allowed(addr, policy)

    # Non-IP host — defer to fetcher's DNS-resolution check.
    return True, REASON_UNRESOLVED_IP


__all__ = [
    "DEFAULT_ALLOWED_SCHEMES",
    "REASON_OK",
    "REASON_EMPTY_URL",
    "REASON_NO_SCHEME",
    "REASON_EMPTY_HOST",
    "REASON_SCHEME_DENIED",
    "REASON_PRIVATE_NETWORK",
    "REASON_UNRESOLVED_IP",
    "REASON_INVALID_IP",
    "REASON_OBFUSCATED_IP_DENIED",
    "is_url_allowed",
    "is_ip_allowed",
]
