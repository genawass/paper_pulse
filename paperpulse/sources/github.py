"""GitHub star counts.

Unauthenticated the API allows 60 requests/hour, so this is deliberately a
late, top-N-only step: rank on free signals first, resolve stars for the
shortlist. Set GITHUB_TOKEN to lift the ceiling to 5000/hour.

HF already reports githubStars for papers it covers; prefer that and call this
only for the gaps.
"""

import os
import re
import time

import requests

API = "https://api.github.com/repos/%s/%s"
REPO_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)
UA = {"User-Agent": "paperpulse/0.1", "Accept": "application/vnd.github+json"}


def parse_repo(url):
    """('owner', 'repo') from any github.com URL, or None."""
    if not url:
        return None
    m = REPO_RE.search(url)
    if not m:
        return None
    repo = m.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return m.group(1), repo


def _headers():
    h = dict(UA)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        h["Authorization"] = "Bearer %s" % token
    return h


def stars(url, session=None):
    """Star count for a repo URL. None if missing, private, or rate-limited."""
    parsed = parse_repo(url)
    if not parsed:
        return None
    session = session or requests.Session()
    try:
        r = session.get(API % parsed, headers=_headers(), timeout=30)
    except requests.RequestException:
        return None
    if r.status_code == 403 and "rate limit" in r.text.lower():
        return None
    if not r.ok:
        return None
    try:
        return r.json().get("stargazers_count")
    except ValueError:
        return None


def stars_bulk(urls, limit=50, verbose=True):
    """Resolve up to `limit` repos, stopping early once rate-limited."""
    session = requests.Session()
    out = {}
    misses = 0
    for url in urls[:limit]:
        n = stars(url, session)
        if n is None:
            misses += 1
            if misses >= 3:
                if verbose:
                    print("  github: stopping early (rate limit or bad repos)")
                break
        else:
            misses = 0
            out[url] = n
        time.sleep(0.3)
    return out
