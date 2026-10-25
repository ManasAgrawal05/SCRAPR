"""Fetch third-party pages server-side so they can be shown inside SCRAPR."""
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import requests

MAX_BYTES = 4 * 1024 * 1024
MAX_HOPS = 5
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Runs inside the framed page: send link clicks to SCRAPR instead of navigating the frame.
INJECT = (
    '<script>document.addEventListener("click",function(e){var a=e.target.closest&&e.target.closest("a[href]");'
    'if(!a||!/^https?:/i.test(a.href))return;e.preventDefault();parent.postMessage({scraprNav:a.href},"*")},true)</script>'
)


class ProxyError(Exception):
    pass


def _check_public(url):
    """Refuse non-http(s) URLs and anything resolving to a private / loopback address (SSRF guard)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ProxyError("Only http(s) links can be opened.")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ProxyError("Could not find that site.")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ProxyError("That address is not allowed.")


def fetch(url):
    """Returns (final_url, content_type, body_bytes, encoding). Follows redirects, re-checking each hop."""
    for _ in range(MAX_HOPS):
        _check_public(url)
        try:
            r = requests.get(url, stream=True, timeout=10, allow_redirects=False,
                             headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                                      "Accept-Language": "en-US,en;q=0.9"})
        except requests.RequestException:
            raise ProxyError("The site did not respond.")
        if r.is_redirect and r.headers.get("Location"):
            url = urljoin(url, r.headers["Location"])
            r.close()
            continue
        body = b""
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) > MAX_BYTES:
                break
        r.close()
        return url, r.headers.get("Content-Type", "application/octet-stream"), body[:MAX_BYTES], r.encoding
    raise ProxyError("Too many redirects.")


def prepare_html(url, body, encoding):
    html = body.decode(encoding or "utf-8", errors="replace")
    html = re.sub(r"<meta[^>]+http-equiv=[\"']?(content-security-policy|refresh)[^>]*>", "", html, flags=re.I)
    base = f'<base href="{url}">'
    if re.search(r"<head[^>]*>", html, re.I):
        return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + base + INJECT, html, count=1, flags=re.I)
    return base + INJECT + html
