"""SQLite store.

Schema is a subset of the design doc's §3 — the parts a metadata-only ranker
needs. `scores` (LLM triage) and `posts` are deliberately absent; they arrive
when there is an LLM in the pipeline.

`signals` is append-only from day one even though nothing reads the history
yet. Velocity is the whole point of that table and it cannot be backfilled.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paperpulse.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    arxiv_id          TEXT PRIMARY KEY,
    version           INTEGER,
    title             TEXT NOT NULL,
    abstract          TEXT,
    authors           TEXT,          -- JSON list
    categories        TEXT,          -- JSON list
    primary_category  TEXT,
    submitted_at      TEXT,          -- UTC ISO8601
    updated_at        TEXT,
    first_seen_at     TEXT,
    comment           TEXT,          -- arxiv:comment, carries venue + project page
    journal_ref       TEXT,
    doi               TEXT,
    pdf_url           TEXT,
    -- derived by links.py
    project_url       TEXT,
    code_url          TEXT,
    venue             TEXT,          -- e.g. "ICML 2026"
    venue_tier        TEXT,          -- top | strong | other
    is_oral           INTEGER        -- oral / spotlight / highlight
);

CREATE INDEX IF NOT EXISTS idx_papers_submitted ON papers(submitted_at);

CREATE TABLE IF NOT EXISTS hf_meta (
    arxiv_id              TEXT PRIMARY KEY,
    checked_at            TEXT,
    on_hf                 INTEGER,   -- paper page exists at all
    upvotes               INTEGER,
    num_media             INTEGER,   -- teaser figures uploaded; "showable" proxy
    hf_project_page       TEXT,
    hf_github_repo        TEXT,
    hf_github_stars       INTEGER,
    submitted_on_daily_at TEXT,
    n_models              INTEGER,
    n_datasets            INTEGER,
    n_spaces              INTEGER,
    raw_json              TEXT
);

CREATE TABLE IF NOT EXISTS page_meta (
    arxiv_id       TEXT PRIMARY KEY,
    checked_at     TEXT,
    url            TEXT,
    final_url      TEXT,
    ok             INTEGER,
    og_image       TEXT,       -- teaser the authors chose; ideal card thumbnail
    og_description TEXT,
    has_video      INTEGER,    -- demo video: the best free "showable" proxy
    page_title     TEXT
);

-- Thumbs up/down. Positives train the taste model; the unlabeled corpus is
-- the negative class, so this table stays small by design.
CREATE TABLE IF NOT EXISTS feedback (
    arxiv_id   TEXT PRIMARY KEY,
    label      INTEGER NOT NULL,   -- +1 / -1
    created_at TEXT
);

-- One row per (paper, day). Append, never overwrite: the delta is the feature.
CREATE TABLE IF NOT EXISTS signals (
    arxiv_id     TEXT NOT NULL,
    observed_on  TEXT NOT NULL,      -- UTC date
    hf_upvotes   INTEGER,
    github_stars INTEGER,
    PRIMARY KEY (arxiv_id, observed_on)
);
"""


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today():
    return datetime.now(timezone.utc).date().isoformat()


def connect(path=DEFAULT_DB):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_paper(conn, p):
    """Insert a paper, or refresh the arXiv-derived fields if it already exists.

    first_seen_at is preserved across updates — it is what defines the weekly
    window, so a v2 posting must not reset a paper's age.
    """
    conn.execute(
        """
        INSERT INTO papers (
            arxiv_id, version, title, abstract, authors, categories,
            primary_category, submitted_at, updated_at, first_seen_at,
            comment, journal_ref, doi, pdf_url,
            project_url, code_url, venue, venue_tier, is_oral
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(arxiv_id) DO UPDATE SET
            version=excluded.version,
            title=excluded.title,
            abstract=excluded.abstract,
            updated_at=excluded.updated_at,
            comment=excluded.comment,
            journal_ref=excluded.journal_ref,
            doi=excluded.doi,
            project_url=excluded.project_url,
            code_url=excluded.code_url,
            venue=excluded.venue,
            venue_tier=excluded.venue_tier,
            is_oral=excluded.is_oral
        """,
        (
            p["arxiv_id"], p.get("version"), p["title"], p.get("abstract"),
            json.dumps(p.get("authors") or []), json.dumps(p.get("categories") or []),
            p.get("primary_category"), p.get("submitted_at"), p.get("updated_at"),
            p.get("first_seen_at") or utcnow(),
            p.get("comment"), p.get("journal_ref"), p.get("doi"), p.get("pdf_url"),
            p.get("project_url"), p.get("code_url"),
            p.get("venue"), p.get("venue_tier"), 1 if p.get("is_oral") else 0,
        ),
    )


def upsert_hf(conn, m):
    conn.execute(
        """
        INSERT INTO hf_meta (
            arxiv_id, checked_at, on_hf, upvotes, num_media,
            hf_project_page, hf_github_repo, hf_github_stars,
            submitted_on_daily_at, n_models, n_datasets, n_spaces, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(arxiv_id) DO UPDATE SET
            checked_at=excluded.checked_at, on_hf=excluded.on_hf,
            upvotes=excluded.upvotes, num_media=excluded.num_media,
            hf_project_page=excluded.hf_project_page,
            hf_github_repo=excluded.hf_github_repo,
            hf_github_stars=excluded.hf_github_stars,
            submitted_on_daily_at=excluded.submitted_on_daily_at,
            -- NULL here means "the repos call failed", never "zero repos"
            -- (that is 0), so keep the previously observed counts.
            n_models=COALESCE(excluded.n_models, hf_meta.n_models),
            n_datasets=COALESCE(excluded.n_datasets, hf_meta.n_datasets),
            n_spaces=COALESCE(excluded.n_spaces, hf_meta.n_spaces),
            raw_json=excluded.raw_json
        """,
        (
            m["arxiv_id"], utcnow(), 1 if m.get("on_hf") else 0,
            m.get("upvotes"), m.get("num_media"),
            m.get("hf_project_page"), m.get("hf_github_repo"), m.get("hf_github_stars"),
            m.get("submitted_on_daily_at"),
            m.get("n_models"), m.get("n_datasets"), m.get("n_spaces"),
            json.dumps(m.get("raw") or {}),
        ),
    )


def upsert_page(conn, arxiv_id, p):
    conn.execute(
        """
        INSERT INTO page_meta (arxiv_id, checked_at, url, final_url, ok,
                               og_image, og_description, has_video, page_title)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(arxiv_id) DO UPDATE SET
            checked_at=excluded.checked_at, url=excluded.url,
            final_url=excluded.final_url, ok=excluded.ok,
            og_image=excluded.og_image, og_description=excluded.og_description,
            has_video=excluded.has_video, page_title=excluded.page_title
        """,
        (arxiv_id, utcnow(), p.get("url"), p.get("final_url"),
         1 if p.get("ok") else 0, p.get("og_image"), p.get("og_description"),
         1 if p.get("has_video") else 0, p.get("page_title")),
    )


def set_feedback(conn, arxiv_id, label):
    conn.execute(
        "INSERT INTO feedback (arxiv_id, label, created_at) VALUES (?,?,?)"
        " ON CONFLICT(arxiv_id) DO UPDATE SET label=excluded.label,"
        " created_at=excluded.created_at",
        (arxiv_id, 1 if label > 0 else -1, utcnow()),
    )


def record_signal(conn, arxiv_id, hf_upvotes=None, github_stars=None):
    conn.execute(
        """
        INSERT INTO signals (arxiv_id, observed_on, hf_upvotes, github_stars)
        VALUES (?,?,?,?)
        ON CONFLICT(arxiv_id, observed_on) DO UPDATE SET
            hf_upvotes=COALESCE(excluded.hf_upvotes, signals.hf_upvotes),
            github_stars=COALESCE(excluded.github_stars, signals.github_stars)
        """,
        (arxiv_id, today(), hf_upvotes, github_stars),
    )


def set_github_stars(conn, arxiv_id, stars):
    # rowcount is per-statement; conn.total_changes is cumulative for the
    # connection and would never read as 0 after any earlier write.
    cur = conn.execute(
        "UPDATE hf_meta SET hf_github_stars=? WHERE arxiv_id=?", (stars, arxiv_id)
    )
    if cur.rowcount == 0:
        conn.execute(
            "INSERT OR IGNORE INTO hf_meta (arxiv_id, checked_at, on_hf, hf_github_stars)"
            " VALUES (?,?,0,?)",
            (arxiv_id, utcnow(), stars),
        )


TABLES = ("papers", "hf_meta", "page_meta", "feedback", "signals")


def count(conn, table="papers"):
    if table not in TABLES:  # table names can't be bound parameters
        raise ValueError("unknown table: %r" % table)
    return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
