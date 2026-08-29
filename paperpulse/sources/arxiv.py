"""arXiv Atom API client.

Pages backwards through submissions by date and stops once it crosses the
requested window. Date-range syntax in `search_query` is unreliable across
mirrors, so we sort descending and cut the tail ourselves.

The API asks for 3s between requests and returns empty pages under load; both
are handled here rather than by the caller.
"""

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

API = "https://export.arxiv.org/api/query"
PAGE = 200
DELAY = 3.0
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
}
UA = {"User-Agent": "paperpulse/0.1 (research digest; contact genawas@gmail.com)"}


class ArxivUnavailable(Exception):
    """arXiv could not be reached after retries. Distinct from a quiet day:
    the caller must not report "nothing new" when the truth is "no answer"."""


def _text(entry, path):
    el = entry.find(path, NS)
    if el is None or el.text is None:
        return None
    return " ".join(el.text.split())


def _parse_entry(entry):
    raw_id = _text(entry, "a:id") or ""
    # http://arxiv.org/abs/2607.25948v1 -> ("2607.25948", 1)
    tail = raw_id.rsplit("/", 1)[-1]
    if "v" in tail:
        arxiv_id, _, v = tail.rpartition("v")
        version = int(v) if v.isdigit() else None
    else:
        arxiv_id, version = tail, None
    if not arxiv_id:
        return None

    pdf_url = None
    for link in entry.findall("a:link", NS):
        if link.get("title") == "pdf":
            pdf_url = link.get("href")

    prim = entry.find("arxiv:primary_category", NS)

    return {
        "arxiv_id": arxiv_id,
        "version": version,
        "title": _text(entry, "a:title") or "",
        "abstract": _text(entry, "a:summary"),
        "authors": [
            " ".join(n.text.split())
            for n in entry.findall("a:author/a:name", NS)
            if n.text
        ],
        "categories": [c.get("term") for c in entry.findall("a:category", NS)],
        "primary_category": prim.get("term") if prim is not None else None,
        "submitted_at": _text(entry, "a:published"),
        "updated_at": _text(entry, "a:updated"),
        "comment": _text(entry, "arxiv:comment"),
        "journal_ref": _text(entry, "arxiv:journal_ref"),
        "doi": _text(entry, "arxiv:doi"),
        "pdf_url": pdf_url,
    }


def _fetch_page(query, start, session, retries=4):
    params = {
        "search_query": query,
        "start": start,
        "max_results": PAGE,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    err = None
    for attempt in range(retries):
        try:
            r = session.get(API, params=params, headers=UA, timeout=60)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            entries = root.findall("a:entry", NS)
            if entries:
                return entries
            # An empty page mid-range is usually transient throttling — but
            # past totalResults it is the genuine end, not worth retrying.
            total = root.find("os:totalResults", NS)
            if total is not None and (total.text or "").isdigit() \
                    and start >= int(total.text):
                return []
            if start == 0:
                return []
            err = None  # arXiv answered; the page was just empty
        except (requests.RequestException, ET.ParseError) as e:
            err = e
        time.sleep(DELAY * (attempt + 2))
    if err is not None:
        raise ArxivUnavailable(str(err))
    return []


def fetch_recent(categories, days=14, max_papers=None, verbose=True,
                 known=None, break_after=2):
    """Yield papers in `categories` submitted within the last `days`.

    Papers cross-listed into several requested categories are returned once.

    `known` is a set of arXiv ids already stored. When given, paging stops
    after `break_after` consecutive pages containing nothing new — and
    immediately if the very first page is all-known. Ported from
    arxiv-sanity-lite's daemon: on a quiet day a daily run then costs one API
    call instead of walking the whole window. Pass break_after=0 to disable,
    which is what you want for a backfill.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = " OR ".join("cat:%s" % c for c in categories)

    session = requests.Session()
    seen = set()
    start = 0
    stop = False
    zero_pages = 0

    while not stop:
        entries = _fetch_page(query, start, session)
        if not entries:
            break

        page_new = 0
        for entry in entries:
            p = _parse_entry(entry)
            if not p or not p["submitted_at"]:
                continue
            submitted = datetime.fromisoformat(p["submitted_at"].replace("Z", "+00:00"))
            if submitted < cutoff:
                stop = True
                break
            if p["arxiv_id"] in seen:
                continue
            seen.add(p["arxiv_id"])
            if known is None or p["arxiv_id"] not in known:
                page_new += 1
            yield p
            if max_papers and len(seen) >= max_papers:
                stop = True
                break

        if verbose:
            print("  arxiv: %d fetched, %d new (start=%d)"
                  % (len(seen), page_new, start), flush=True)

        if known is not None and break_after > 0 and not stop:
            if page_new == 0:
                zero_pages += 1
                if start == 0 or zero_pages >= break_after:
                    if verbose:
                        print("  arxiv: nothing new, stopping early", flush=True)
                    stop = True
            else:
                zero_pages = 0

        start += PAGE
        if not stop:
            time.sleep(DELAY)


def fetch_by_ids(ids):
    """Fetch specific arXiv IDs (used to backfill a known paper)."""
    session = requests.Session()
    out = []
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        r = session.get(
            API, params={"id_list": ",".join(chunk), "max_results": len(chunk)},
            headers=UA, timeout=60,
        )
        r.raise_for_status()
        for entry in ET.fromstring(r.content).findall("a:entry", NS):
            p = _parse_entry(entry)
            if p:
                out.append(p)
        time.sleep(DELAY)
    return out
