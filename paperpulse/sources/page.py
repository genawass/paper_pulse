"""Project-page scraper.

Two things worth having that nothing else provides:

- **og:image** — the teaser figure the authors chose to represent the work.
  Better than anything extractable from the PDF, and already sized for a card.
- **a `<video>` tag** — a page that ships a demo video is the closest free
  proxy for "showable" there is. Building one costs real effort, so it
  separates work meant to travel from work meant to be cited.

Regex over the head, not a parser: these are meta tags, and pulling in a
dependency to read five of them is not worth it.
"""

import ipaddress
import re
import socket
import time

import requests
from urllib.parse import urljoin, urlparse

DELAY = 0.3
MAX_BYTES = 400_000  # heads are small; do not download a 50 MB demo page
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 paperpulse/0.1"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

META_RE = re.compile(
    r"<meta[^>]+(?:property|name)\s*=\s*[\"']([^\"']+)[\"'][^>]*"
    r"content\s*=\s*[\"']([^\"']*)[\"']", re.I)
META_REV_RE = re.compile(  # content= before property=, which is equally common
    r"<meta[^>]+content\s*=\s*[\"']([^\"']*)[\"'][^>]*"
    r"(?:property|name)\s*=\s*[\"']([^\"']+)[\"']", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
VIDEO_RE = re.compile(
    r"<video[\s>]|\.mp4[\"'?]|youtube\.com/embed|player\.vimeo\.com", re.I)


MAX_REDIRECTS = 5


def _public_http_url(url):
    """True only for http(s) URLs whose host resolves to public addresses.

    These URLs come from paper comments — untrusted text. Without this check a
    crafted comment could point the scraper at 169.254.169.254 or a service on
    localhost and leak whatever it answers into og_description/page_title.
    Checked per redirect hop, since a public host can redirect inward.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def _metas(html):
    out = {}
    for key, val in META_RE.findall(html):
        out.setdefault(key.lower(), val)
    for val, key in META_REV_RE.findall(html):
        out.setdefault(key.lower(), val)
    return out


def fetch(url, session=None):
    """Return dict with og_image / og_description / has_video / final_url.

    Never raises — a dead or hostile project page is normal and should cost
    the paper its bonus, not abort the run.
    """
    result = {"url": url, "ok": False, "og_image": None, "og_description": None,
              "has_video": False, "final_url": None, "page_title": None}
    session = session or requests.Session()
    try:
        # Redirects followed by hand so every hop passes the SSRF check.
        target = url
        for _ in range(MAX_REDIRECTS + 1):
            if not _public_http_url(target):
                return result
            r = session.get(target, headers=UA, timeout=20, stream=True,
                            allow_redirects=False)
            if r.is_redirect or r.is_permanent_redirect:
                loc = r.headers.get("location")
                r.close()
                if not loc:
                    return result
                target = urljoin(target, loc)
                continue
            break
        else:
            return result  # redirect loop
        if not r.ok:
            return result
        chunks, size = [], 0
        for chunk in r.iter_content(16384, decode_unicode=False):
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_BYTES:
                break
        r.close()
        html = b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")
    except (requests.RequestException, ValueError):
        return result

    meta = _metas(html)
    img = (meta.get("og:image") or meta.get("twitter:image")
           or meta.get("twitter:image:src"))
    desc = meta.get("og:description") or meta.get("description")
    title = TITLE_RE.search(html)

    result.update(
        ok=True,
        final_url=str(r.url),
        og_image=urljoin(str(r.url), img) if img else None,
        og_description=" ".join(desc.split())[:400] if desc else None,
        has_video=bool(VIDEO_RE.search(html)),
        page_title=" ".join(title.group(1).split())[:200] if title else None,
    )
    return result


def fetch_many(urls, verbose=True):
    session = requests.Session()
    out = {}
    for i, url in enumerate(urls, 1):
        out[url] = fetch(url, session)
        if verbose and i % 20 == 0:
            print("  pages: %d/%d" % (i, len(urls)), flush=True)
        time.sleep(DELAY)
    return out
