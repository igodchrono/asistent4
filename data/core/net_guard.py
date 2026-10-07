# -*- coding: utf-8 -*-
"""Блоклист URL для fetch: localhost, RFC1918, link-local, metadata, non-http."""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPHandler,
    HTTPSHandler,
    HTTPRedirectHandler,
    OpenerDirector,
    Request,
    build_opener,
)

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
    "instance-data",
    "internal",
}

_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".corp",
    ".lan",
    ".home",
    ".localdomain",
)


def _parse_ip(host: str):
    h = (host or "").strip("[]")
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    if h.isdigit() or (h.lower().startswith("0x") and len(h) > 2):
        try:
            n = int(h, 0)
            if 0 <= n <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(n)
        except ValueError:
            pass
    if h.count(".") in (1, 2, 3) and all(p.isdigit() or p == "" for p in h.split(".")):
        try:
            packed = socket.inet_aton(h)
            return ipaddress.IPv4Address(packed)
        except OSError:
            pass
    return None


def _ip_blocked(ip: ipaddress._BaseAddress) -> bool:
    if ip.version == 4 and ip == ipaddress.IPv4Address("169.254.169.254"):
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or getattr(ip, "is_site_local", False)
    )


def _host_blocked_name(host: str) -> bool:
    h = (host or "").strip("[]").lower().rstrip(".")
    if not h:
        return True
    if h in _BLOCKED_HOSTS:
        return True
    return any(h.endswith(suf) for suf in _BLOCKED_HOST_SUFFIXES)


def blocked_url_reason(url: str, resolve: bool = True) -> Optional[str]:
    """Почему URL нельзя качать. None = можно."""
    raw = (url or "").strip()
    if not raw:
        return "пустой URL"
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return f"схема {parsed.scheme or '(нет)'} запрещена"
    host = (parsed.hostname or "").strip("[]")
    if _host_blocked_name(host):
        return f"хост {host} в блоклисте"
    ip = _parse_ip(host)
    if ip is not None:
        if _ip_blocked(ip):
            return f"IP {ip} не публичный"
        return None
    if not resolve:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return f"не резолвится: {host}"
    if not infos:
        return f"не резолвится: {host}"
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _ip_blocked(ip):
            return f"{host} → {ip} (не публичный)"
    return None


def is_public_http_url(url: str, resolve: bool = True) -> bool:
    return blocked_url_reason(url, resolve=resolve) is None


class GuardedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = blocked_url_reason(newurl)
        if reason:
            raise URLError(f"blocked redirect: {reason}")
        return HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl
        )


def guarded_opener() -> OpenerDirector:
    return build_opener(GuardedRedirectHandler, HTTPHandler, HTTPSHandler)


def guarded_urlopen(url: str, req: Optional[Request] = None, timeout: int = 25):
    target = url if req is None else (req.full_url if hasattr(req, "full_url") else url)
    if req is not None and not target:
        target = req.get_full_url()
    if req is not None:
        target = req.get_full_url()
    reason = blocked_url_reason(target)
    if reason:
        raise URLError(f"blocked url: {reason}")
    opener = guarded_opener()
    if req is None:
        req = Request(target)
    return opener.open(req, timeout=timeout)


if __name__ == "__main__":
    cases = [
        ("https://example.com/x", True),
        ("http://127.0.0.1:1234/v1", False),
        ("http://localhost/x", False),
        ("http://192.168.0.1/x", False),
        ("http://10.0.0.5/x", False),
        ("http://172.16.1.1/x", False),
        ("http://169.254.169.254/latest/meta-data", False),
        ("http://[::1]/", False),
        ("file:///etc/passwd", False),
        ("https://2130706433/", False),
        ("ftp://example.com/x", False),
        ("http://metadata.google.internal/", False),
    ]
    fail = 0
    for url, expect_ok in cases:
        reason = blocked_url_reason(url, resolve=False)
        ok = reason is None
        mark = "OK" if ok == expect_ok else "FAIL"
        if ok != expect_ok:
            fail += 1
        print(f"  {mark} {url!r} → {reason or 'allow'} (expect {'allow' if expect_ok else 'block'})")
    raise SystemExit(fail)
