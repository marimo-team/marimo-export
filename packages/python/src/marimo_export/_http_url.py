"""Validate HTTP URL authorities shared by portable module records."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

import idna

_INVALID_HTTP_URL_CHARACTER = re.compile(r"[\x00-\x20\x7f\\]")
_HTTP_HOST = re.compile(r"[A-Za-z0-9._-]+")


def validate_http_url_authority(value: str) -> None:
    """Validate one HTTP URL authority with browser-compatible brackets."""

    if _INVALID_HTTP_URL_CHARACTER.search(value) is not None:
        raise ValueError("HTTP URL contains an incompatible character")
    scheme, _, remainder = value.partition(":")
    normalized = value
    if not remainder.startswith("//"):
        authority = remainder.lstrip("/")
        if not authority:
            raise ValueError("HTTP URL has no authority")
        normalized = f"{scheme}://{authority}"
    parsed = urlsplit(normalized)
    try:
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as error:
        raise ValueError("HTTP URL authority is invalid") from error
    if not hostname:
        raise ValueError("HTTP URL has no hostname")

    authority = parsed.netloc.rpartition("@")[2]
    if authority.startswith("["):
        closing = authority.find("]")
        suffix = authority[closing + 1 :] if closing >= 0 else ""
        if closing <= 1 or (suffix and not suffix.startswith(":")):
            raise ValueError("HTTP URL bracketed authority is invalid")
        if "%" in hostname:
            raise ValueError("HTTP URL scoped IPv6 hostnames are invalid")
        try:
            ipaddress.IPv6Address(hostname)
        except ValueError as error:
            raise ValueError("HTTP URL bracketed hostname is not IPv6") from error
        return
    if "[" in authority or "]" in authority or ":" in hostname:
        raise ValueError("HTTP URL hostname has invalid brackets")

    ascii_hostname = hostname
    if not hostname.isascii():
        try:
            ascii_hostname = idna.encode(
                hostname,
                uts46=True,
                transitional=False,
            ).decode("ascii")
        except idna.IDNAError as error:
            raise ValueError("HTTP URL IDNA hostname is invalid") from error
    if _HTTP_HOST.fullmatch(ascii_hostname) is None:
        raise ValueError("HTTP URL hostname is invalid")
    for label in ascii_hostname.split("."):
        if not label.lower().startswith("xn--"):
            continue
        try:
            decoded = idna.decode(label, uts46=True)
            encoded = idna.encode(decoded, uts46=True, transitional=False).decode("ascii")
        except idna.IDNAError as error:
            raise ValueError("HTTP URL IDNA hostname is invalid") from error
        if encoded.lower() != label.lower():
            raise ValueError("HTTP URL IDNA hostname is invalid")
    if ascii_hostname.replace(".", "").isdigit():
        try:
            ipaddress.IPv4Address(ascii_hostname)
        except ValueError as error:
            raise ValueError("HTTP URL IPv4 hostname is invalid") from error


__all__ = ["validate_http_url_authority"]
