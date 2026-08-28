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


def _get(session, url, timeout=30):
    try:
        r = session.get(url, headers=UA, timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code == 404:
        return None
    if r.status_code == 429:
        time.sleep(5)
        return _get(session, url, timeout)
    if not r.ok:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def fetch_paper(arxiv_id, session=None, with_repos=True):
    """Return an hf_meta dict for one paper. `on_hf` False if it has no page."""
    session = session or requests.Session()
    data = _get(session, "%s/papers/%s" % (BASE, arxiv_id))
    time.sleep(DELAY)

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
        repos = _get(session, "%s/arxiv/%s/repos" % (BASE, arxiv_id)) or {}
        time.sleep(DELAY)
        meta["n_models"] = len(repos.get("models") or [])
        meta["n_datasets"] = len(repos.get("datasets") or [])
        meta["n_spaces"] = len(repos.get("spaces") or [])

    return meta


def daily_papers(date, session=None):
    """arXiv IDs featured in HF daily papers on `date` (YYYY-MM-DD)."""
    session = session or requests.Session()
    data = _get(session, "%s/daily_papers?date=%s" % (BASE, date)) or []
    time.sleep(DELAY)
    out = []
    for item in data:
        paper = item.get("paper") or {}
        if paper.get("id"):
            out.append(paper["id"])
    return out
