"""Metadata-only ranker.

No embeddings, no LLM. Scores a paper on artifact footprint plus a small
community term, both of which are available on the paper's first day.

Every score carries its own breakdown. On a first run the breakdown matters
more than the number — the point is to see *why* something ranked, and to
catch a signal that is silently absent for 95% of the corpus.
"""

import json
import math
import os
import re
from datetime import datetime, timezone

import yaml

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")


def load_config(path=DEFAULT_CONFIG):
    with open(path) as f:
        return yaml.safe_load(f)


def _age_days(submitted_at):
    if not submitted_at:
        return None
    try:
        dt = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)


def score_row(row, cfg):
    """Score one joined papers+hf_meta row. Returns (total, breakdown dict)."""
    fw = cfg["rank"]["footprint"]
    cw = cfg["rank"]["community"]
    parts = {}

    # --- artifact footprint -------------------------------------------------
    # Project page and code can each come from two places; either counts once.
    if row["project_url"] or row["hf_project_page"]:
        parts["project_page"] = fw["project_page"]
    if row["code_url"] or row["hf_github_repo"]:
        parts["code"] = fw["code"]

    if row["venue_tier"] == "top":
        parts["venue_top"] = fw["venue_top"]
    elif row["venue_tier"] == "strong":
        parts["venue_strong"] = fw["venue_strong"]
    if row["is_oral"]:
        parts["oral"] = fw["oral"]

    if (row["num_media"] or 0) > 0:
        parts["hf_media"] = fw["hf_media"]
    if row["submitted_on_daily_at"]:
        parts["hf_daily"] = fw["hf_daily"]
    if (row["n_models"] or 0) > 0:
        parts["hf_model"] = fw["hf_model"]
    if (row["n_datasets"] or 0) > 0:
        parts["hf_dataset"] = fw["hf_dataset"]
    if (row["n_spaces"] or 0) > 0:
        parts["hf_space"] = fw["hf_space"]
    # A demo video on the project page is the strongest free evidence that the
    # result is showable — which is most of what "sexy" means operationally.
    if row["has_video"]:
        parts["demo_video"] = fw["demo_video"]

    footprint = sum(parts.values())

    # --- community ----------------------------------------------------------
    upvotes = row["upvotes"] or 0
    stars = row["hf_github_stars"] or 0
    community = cw["upvotes"] * math.log1p(upvotes) + cw["stars"] * math.log1p(stars)

    age = _age_days(row["submitted_at"])
    if cfg["rank"].get("age_normalize") and age is not None:
        community /= math.log1p(age + 1)

    if community:
        parts["community"] = round(community, 3)

    total = footprint + community
    return total, parts


def _http(url):
    """Only http(s) URLs may reach the report. HF projectPage/githubRepo and
    og:image are free text set by whoever submitted the paper — a javascript:
    value here would become a live link in the rendered HTML."""
    if url and re.match(r"https?://", url, re.I):
        return url
    return None


def _thumb(row):
    """Card image: the authors' own teaser first, HF's upload as fallback."""
    og = _http(row["og_image"])
    if og:
        return og
    try:
        media = json.loads(row["raw_json"] or "{}").get("mediaUrls") or []
    except ValueError:
        media = []
    return _http(media[0]) if media else None


SELECT = """
SELECT p.arxiv_id, p.title, p.abstract, p.submitted_at, p.categories, p.comment,
       p.project_url, p.code_url, p.venue, p.venue_tier, p.is_oral,
       h.upvotes, h.num_media, h.hf_project_page, h.hf_github_repo,
       h.hf_github_stars, h.submitted_on_daily_at, h.raw_json,
       h.n_models, h.n_datasets, h.n_spaces,
       g.og_image, g.og_description, g.has_video, g.final_url
FROM papers p
LEFT JOIN hf_meta h ON h.arxiv_id = p.arxiv_id
LEFT JOIN page_meta g ON g.arxiv_id = p.arxiv_id
"""


def rank(conn, cfg, limit=None, require_category=None, taste_scores=None):
    """Rank every stored paper. Returns list of dicts, best first.

    `taste_scores` is an optional {arxiv_id: percentile} from taste.fit().
    """
    tw = cfg["rank"].get("taste", 0.0)
    out = []
    for row in conn.execute(SELECT):
        if require_category:
            cats = json.loads(row["categories"] or "[]")
            if not any(c in require_category for c in cats):
                continue
        total, parts = score_row(row, cfg)
        if taste_scores and tw:
            t = taste_scores.get(row["arxiv_id"])
            if t is not None:
                parts["taste"] = round(tw * t, 3)
                total += tw * t
        out.append({
            "arxiv_id": row["arxiv_id"],
            "title": row["title"],
            "abstract": row["abstract"],
            "blurb": row["og_description"],
            "thumb": _thumb(row),
            "has_video": bool(row["has_video"]),
            "age_days": round(_age_days(row["submitted_at"]) or 0, 1),
            "submitted_at": row["submitted_at"],
            "venue": row["venue"],
            "venue_tier": row["venue_tier"],
            "score": round(total, 3),
            "parts": parts,
            # links.py URLs are regex-validated https?://; the HF fallbacks
            # are not, so they pass through the scheme allowlist.
            "project_url": row["project_url"] or _http(row["hf_project_page"]),
            "code_url": row["code_url"] or _http(row["hf_github_repo"]),
            "upvotes": row["upvotes"] or 0,
            "stars": row["hf_github_stars"] or 0,
            # 0 stars and "never looked" both display as 0; the stars stage
            # needs the difference to avoid re-fetching genuine zeros.
            "stars_fetched": row["hf_github_stars"] is not None,
        })
    out.sort(key=lambda r: (-r["score"], r["arxiv_id"]))
    return out[:limit] if limit else out


def prescore(conn, cfg, limit):
    """Cheapest possible ranking, arXiv metadata only — no HF, no GitHub.

    Used to pick which papers are worth spending an HF request on.
    """
    fw = cfg["rank"]["footprint"]
    out = []
    for row in conn.execute(
        "SELECT arxiv_id, project_url, code_url, venue_tier, is_oral FROM papers"
    ):
        s = 0.0
        if row["project_url"]:
            s += fw["project_page"]
        if row["code_url"]:
            s += fw["code"]
        if row["venue_tier"] == "top":
            s += fw["venue_top"]
        elif row["venue_tier"] == "strong":
            s += fw["venue_strong"]
        if row["is_oral"]:
            s += fw["oral"]
        out.append((s, row["arxiv_id"]))
    out.sort(key=lambda t: (-t[0], t[1]))
    return [aid for _, aid in out[:limit]]
