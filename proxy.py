"""Show third-party pages inside SCRAPR without leaving the game.

Tier 1  probe(): if the site allows being framed, the viewer embeds the real page directly (full fidelity, the player's
        own browser does the loading). Sites that opt out of framing are never embedded live.
Tier 2  fetch(): otherwise our server fetches the public page itself (no cookies, a Chrome-like TLS handshake so ordinary
        bot filters don't refuse it) and serves a script-free sandboxed copy.
Pages that need JavaScript to show anything, or that refuse the server, get a readable error instead of a blank frame."""
import ipaddress
import re
import socket
import threading
import time
from collections import namedtuple
from urllib.parse import urljoin, urlparse

from curl_cffi import requests as cffi
from curl_cffi.const import CurlOpt

MAX_BYTES = 4 * 1024 * 1024
MAX_HOPS = 5
TIMEOUT = 12
PROBE_TTL = 3600

Fetched = namedtuple("Fetched", "url status ctype body encoding headers")

# Runs inside the framed copy: send link clicks to SCRAPR instead of navigating the frame.
INJECT = (
    '<script>document.addEventListener("click",function(e){var a=e.target.closest&&e.target.closest("a[href]");'
    'if(!a||!/^https?:/i.test(a.href))return;e.preventDefault();parent.postMessage({scraprNav:a.href},"*")},true)</script>'
)


class ProxyError(Exception):
    pass


def _public_target(url):
    """Refuse non-http(s) URLs and anything that resolves to a private / loopback address (SSRF guard).
    Returns (host, port, ip); the connection is then pinned to exactly this ip, so DNS can't change underneath us."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ProxyError("Only http(s) links can be opened.")
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(u.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ProxyError("Could not find that site.")
    ips = [i[4][0] for i in infos]
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ProxyError("That address is not allowed.")
    ip = next((i for i in ips if ":" not in i), ips[0])  # prefer IPv4
    return u.hostname, port, f"[{ip}]" if ":" in ip else ip


def _open(url):
    """Follow redirects (re-checking every hop). Returns (final_url, streaming response)."""
    for _ in range(MAX_HOPS):
        host, port, ip = _public_target(url)
        try:
            r = cffi.get(url, impersonate="chrome", allow_redirects=False, timeout=TIMEOUT, stream=True,
                         headers={"Accept-Language": "en-US,en;q=0.9"},
                         curl_options={CurlOpt.RESOLVE: [f"{host}:{port}:{ip}".encode()]})
        except Exception:
            raise ProxyError("The site did not respond.")
        if 300 <= r.status_code < 400 and r.headers.get("Location"):
            url = urljoin(url, r.headers["Location"])
            r.close()
            continue
        return url, r
    raise ProxyError("Too many redirects.")


def fetch(url):
    final, r = _open(url)
    try:
        body = b""
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) > MAX_BYTES:
                break
        enc = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type", ""), re.I)
        return Fetched(final, r.status_code, r.headers.get("Content-Type", "application/octet-stream"), body[:MAX_BYTES],
                       enc.group(1) if enc else None, {k.lower(): v for k, v in r.headers.items()})
    finally:
        r.close()


# ---- tier 1: may we embed the real page? --------------------------------------------------
_probe_cache, _probe_lock = {}, threading.Lock()


def frameable(headers):
    """True only if the site sends no framing restriction (or explicitly allows any ancestor)."""
    if headers.get("x-frame-options"):
        return False
    m = re.search(r"frame-ancestors([^;,]*)", headers.get("content-security-policy", ""), re.I)
    return not m or m.group(1).strip() == "*"


_WALL_SIGNS = (b"awswaf", b"awswafintegration", b"px-captcha", b"captcha-delivery", b"just a moment", b"datadome",
               b"/cdn-cgi/challenge-platform", b"verify you are human", b"enable javascript and cookies")


def bot_wall(status, headers, body):
    """Is this a bot-check page (AWS WAF, Cloudflare, PerimeterX, DataDome...) instead of the real page?
    Those checks need to store a cookie to be passed, which neither a sandboxed copy nor a third-party frame can do."""
    if headers.get("x-amzn-waf-action") or headers.get("cf-mitigated"):
        return True
    low = body[:30000].lower()
    return status in (202, 401, 403, 405, 429, 503) and any(sign in low for sign in _WALL_SIGNS)


def probe(url):
    """'direct' (embed the real page) or 'copy' (use our server-fetched copy). Cached per host for an hour."""
    host = (urlparse(url).hostname or "").lower()
    now = time.time()
    with _probe_lock:
        hit = _probe_cache.get(host)
        if hit and now - hit[0] < PROBE_TTL:
            return hit[1]
    try:
        final, r = _open(url)
        try:
            headers = {k.lower(): v for k, v in r.headers.items()}
            first = next(r.iter_content(30000), b"") if r.status_code != 200 or headers.get("x-amzn-waf-action") else b""
            walled = bot_wall(r.status_code, headers, first)
            mode = "direct" if frameable(headers) and not walled and urlparse(final).hostname == host else "copy"
        finally:
            r.close()
    except ProxyError:
        mode = "copy"
    with _probe_lock:
        if len(_probe_cache) > 2000:
            _probe_cache.clear()
        _probe_cache[host] = (now, mode)
    return mode


# ---- tier 2: the copy ---------------------------------------------------------------------
def text_length(html):
    html = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", html)
    return len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip())


def prepare_html(url, body, encoding):
    html = body.decode(encoding or "utf-8", errors="replace")
    html = re.sub(r"<meta[^>]+http-equiv=[\"']?(content-security-policy|refresh)[^>]*>", "", html, flags=re.I)
    base = f'<base href="{url}">'
    if re.search(r"<head[^>]*>", html, re.I):
        return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + base + INJECT, html, count=1, flags=re.I)
    return base + INJECT + html


def explain(status):
    """Plain-English reason for a failed fetch."""
    if status in (401, 403):
        return "This site doesn't let our server read its page (it answered “forbidden”)."
    if status == 404:
        return "That page doesn't exist any more (404)."
    if status == 429:
        return "This site is limiting how fast we can load its pages. Try again in a minute."
    if status >= 500:
        return f"The site itself is having trouble right now (error {status})."
    return f"The site answered with an error ({status})."
