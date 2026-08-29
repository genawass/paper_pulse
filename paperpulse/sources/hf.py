"""HuggingFace enrichment.

`/api/papers/{arxiv_id}` is the single densest call in the pipeline: upvotes,
project page, GitHub repo *and its star count*, and uploaded teaser images —
all in one request. Coverage is partial (only papers someone submitted), so it
enriches rather than discovers.

`/api/arxiv/{id}/repos` lists linked models/datasets/spaces, but the link is
made by hand and lags the paper by days. Absence is not evidence.
"""

import time

import requests

BASE = "https://huggingface.co/api"
DELAY = 0.2
UA = {"User-Agent": "paperpulse/0.1 (research digest; contact genawas@gmail.com)"}


# A 404 means "not on HF" and is a fact worth storing. Anything else —
# timeout, 5xx, persistent 429 — means we learned nothing, and must not be
# allowed to overwrite data from a previous successful fetch.
ERROR = object()


def _get(session, url, timeout=30, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(url, headers=UA, timeout=timeout)
        except requests.RequestException:
            return ERROR
        if r.status_code == 404:
            return None
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        if not r.ok:
            return ERROR
        try:
            return r.json()
        except ValueError:
            return ERROR
    return ERROR


def fetch_paper(arxiv_id, session=None, with_repos=True):
    """Return an hf_meta dict for one paper, `on_hf` False if it has no page —
    or None on a transient error, in which case nothing should be written."""
    session = session or requests.Session()
    data = _get(session, "%s/papers/%s" % (BASE, arxiv_id))
    time.sleep(DELAY)

    if data is ERROR:
        return None
    if not data:
        return {"arxiv_id": arxiv_id, "on_hf": False}

    meta = {
        "arxiv_id": arxiv_id,
        "on_hf": True,
        "upvotes": data.get("upvotes") or 0,
        "num_media": len(data.get("mediaUrls") or []),
        "hf_project_page": data.get("projectPage"),
        "hf_github_repo": data.get("githubRepo"),
        "hf_github_stars": data.get("githubStars"),
        "submitted_on_daily_at": data.get("submittedOnDailyAt"),
        "raw": {k: v for k, v in data.items() if k not in ("authors", "summary")},
    }

    if with_repos:
        repos = _get(session, "%s/arxiv/%s/repos" % (BASE, arxiv_id))
        time.sleep(DELAY)
        if repos is not ERROR:
            repos = repos or {}
            meta["n_models"] = len(repos.get("models") or [])
            meta["n_datasets"] = len(repos.get("datasets") or [])
            meta["n_spaces"] = len(repos.get("spaces") or [])
        # on error the n_* keys stay absent; upsert_hf keeps the old values

    return meta


def daily_papers(date, session=None):
    """arXiv IDs featured in HF daily papers on `date` (YYYY-MM-DD)."""
    session = session or requests.Session()
    data = _get(session, "%s/daily_papers?date=%s" % (BASE, date))
    data = [] if (data is ERROR or not data) else data
    time.sleep(DELAY)
    out = []
    for item in data:
        paper = item.get("paper") or {}
        if paper.get("id"):
            out.append(paper["id"])
    return out
