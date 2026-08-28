"""paperpulse CLI — ingest / enrich / rank / show.

Staged deliberately so the expensive calls come last:

    ingest   arXiv Atom API, full coverage, free footprint from the comment field
    enrich   HF papers API for the top-N by free footprint only
    stars    GitHub, top-N only (60 req/hour unauthenticated)
    rank     final composite
"""

import argparse
import json
import sys

from . import links, rank as ranker, report, store, taste
from .sources import arxiv, github, hf, page


def _ranked(conn, cfg, limit=None):
    """Rank with the learned taste model applied, if it has enough labels."""
    scores, _ = taste.fit(conn, cfg)
    return ranker.rank(conn, cfg, limit=limit, taste_scores=scores)


def _window_dates(conn, days):
    """The last `days` calendar dates covered by papers in the db."""
    rows = conn.execute(
        "SELECT DISTINCT substr(submitted_at,1,10) d FROM papers"
        " WHERE d IS NOT NULL ORDER BY d DESC LIMIT ?", (days,)
    ).fetchall()
    return [r["d"] for r in rows]


def cmd_ingest(args):
    cfg = ranker.load_config(args.config)
    cats = args.categories or cfg["ingest"]["categories"]
    days = args.days or cfg["ingest"]["days"]

    conn = store.connect(args.db)
    print("ingesting %s over last %d days ..." % (", ".join(cats), days))

    known = {r[0] for r in conn.execute("SELECT arxiv_id FROM papers")}
    n = n_new = 0
    for paper in arxiv.fetch_recent(cats, days=days, max_papers=args.limit,
                                    known=known,
                                    break_after=getattr(args, "break_after", 2)):
        if paper["arxiv_id"] not in known:
            n_new += 1
        store.upsert_paper(conn, links.enrich(paper))
        n += 1
        if n % 500 == 0:
            conn.commit()
    conn.commit()

    total = store.count(conn)
    with_proj = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE project_url IS NOT NULL").fetchone()[0]
    with_code = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE code_url IS NOT NULL").fetchone()[0]
    with_venue = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE venue IS NOT NULL").fetchone()[0]

    print("\nsaw %d papers, %d genuinely new, %d in db" % (n, n_new, total))
    print("  project page: %d (%.1f%%)" % (with_proj, 100.0 * with_proj / max(total, 1)))
    print("  code url:     %d (%.1f%%)" % (with_code, 100.0 * with_code / max(total, 1)))
    print("  venue:        %d (%.1f%%)" % (with_venue, 100.0 * with_venue / max(total, 1)))

    # Exit status is the signal downstream stages gate on — the arxiv-sanity-lite
    # pattern. Nonzero means "nothing new", not "something broke".
    return 0 if n_new else 1


def cmd_reparse(args):
    """Re-run venue/URL extraction over stored papers. No network."""
    conn = store.connect(args.db)
    rows = conn.execute(
        "SELECT arxiv_id, comment, abstract, journal_ref FROM papers"
    ).fetchall()
    changed = 0
    for r in rows:
        project, code = links.extract_urls(r["comment"], r["abstract"])
        venue, tier, oral = links.extract_venue(r["comment"], r["journal_ref"])
        cur = conn.execute(
            "UPDATE papers SET project_url=?, code_url=?, venue=?, venue_tier=?,"
            " is_oral=? WHERE arxiv_id=? AND ("
            " project_url IS NOT ? OR code_url IS NOT ? OR venue IS NOT ?"
            " OR venue_tier IS NOT ? OR is_oral IS NOT ?)",
            (project, code, venue, tier, 1 if oral else 0, r["arxiv_id"],
             project, code, venue, tier, 1 if oral else 0),
        )
        changed += cur.rowcount
    conn.commit()
    print("reparsed %d papers, %d changed" % (len(rows), changed))


def cmd_enrich(args):
    cfg = ranker.load_config(args.config)
    conn = store.connect(args.db)

    ids = ranker.prescore(conn, cfg, args.top)

    # The prescore only sees the comment field, so a paper with a bare comment
    # but real community traction would never be enriched and would stay
    # invisible. Union in HF daily papers for the window to cover that side.
    if args.include_daily:
        known = {r[0] for r in conn.execute("SELECT arxiv_id FROM papers")}
        daily = []
        for day in _window_dates(conn, args.include_daily):
            daily += [i for i in hf.daily_papers(day) if i in known]
        print("  + %d from HF daily papers" % len(set(daily) - set(ids)))
        ids = list(dict.fromkeys(ids + daily))

    if args.ids:
        ids = list(dict.fromkeys(args.ids + ids))  # force-include, keep order

    print("enriching %d papers from HF ..." % len(ids))
    for i, aid in enumerate(ids, 1):
        meta = hf.fetch_paper(aid)
        store.upsert_hf(conn, meta)
        if meta.get("on_hf"):
            store.record_signal(conn, aid, hf_upvotes=meta.get("upvotes"),
                                github_stars=meta.get("hf_github_stars"))
        if i % 25 == 0:
            conn.commit()
            print("  %d/%d" % (i, len(ids)), flush=True)
    conn.commit()

    marks = ",".join("?" * len(ids))
    on_hf = conn.execute(
        "SELECT COUNT(*) FROM hf_meta WHERE on_hf=1 AND arxiv_id IN (%s)" % marks, ids
    ).fetchone()[0]
    print("done. %d of %d have an HF paper page" % (on_hf, len(ids)))


def cmd_pages(args):
    """Fetch project pages for the top-N to get teaser images and video flags."""
    cfg = ranker.load_config(args.config)
    conn = store.connect(args.db)

    rows = [r for r in _ranked(conn, cfg, limit=args.top) if r["project_url"]]
    if not args.refetch:
        seen = {r[0] for r in conn.execute("SELECT arxiv_id FROM page_meta WHERE ok=1")}
        rows = [r for r in rows if r["arxiv_id"] not in seen]
    print("fetching %d project pages ..." % len(rows))

    session = None
    for i, r in enumerate(rows, 1):
        store.upsert_page(conn, r["arxiv_id"], page.fetch(r["project_url"], session))
        if i % 20 == 0:
            conn.commit()
            print("  %d/%d" % (i, len(rows)), flush=True)
    conn.commit()

    ok = conn.execute("SELECT COUNT(*) FROM page_meta WHERE ok=1").fetchone()[0]
    img = conn.execute(
        "SELECT COUNT(*) FROM page_meta WHERE og_image IS NOT NULL").fetchone()[0]
    vid = conn.execute("SELECT COUNT(*) FROM page_meta WHERE has_video=1").fetchone()[0]
    print("reachable %d · teaser image %d · demo video %d" % (ok, img, vid))


def cmd_weekly(args):
    """Whole pipeline, then re-render.

    Everything after ingest is skipped when arXiv had nothing new — enrich
    alone is 30+ minutes of throttled requests, so a daily cron on a quiet day
    should cost one API call, not that.
    """
    print("[1/5] ingest")
    if cmd_ingest(args) and not args.force:
        print("\nno new papers — skipping enrich/pages/stars/report."
              " Use --force to run anyway.")
        return 0
    print("\n[2/5] enrich"); cmd_enrich(args)
    print("\n[3/5] project pages"); cmd_pages(args)
    print("\n[4/5] github stars"); cmd_stars(args)
    print("\n[5/5] report"); cmd_report(args)
    return 0


def cmd_stars(args):
    cfg = ranker.load_config(args.config)
    conn = store.connect(args.db)

    rows = _ranked(conn, cfg, limit=args.top)
    need = [(r["arxiv_id"], r["code_url"]) for r in rows
            if r["code_url"] and not r["stars"]]
    print("resolving stars for %d repos ..." % len(need))

    found = github.stars_bulk([u for _, u in need], limit=args.top)
    for aid, url in need:
        if url in found:
            store.set_github_stars(conn, aid, found[url])
            store.record_signal(conn, aid, github_stars=found[url])
    conn.commit()
    print("resolved %d" % len(found))


def cmd_rank(args):
    cfg = ranker.load_config(args.config)
    conn = store.connect(args.db)
    rows = _ranked(conn, cfg, limit=args.top)

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    for i, r in enumerate(rows, 1):
        bits = ", ".join("%s=%s" % (k, v) for k, v in sorted(r["parts"].items()))
        print("%3d. %-6s %6.2f  %s" % (i, r["arxiv_id"], r["score"], r["title"][:82]))
        print("      %s" % (bits or "no signals"))
        if r["venue"] or r["project_url"]:
            print("      %s%s" % (
                (r["venue"] + "  ") if r["venue"] else "",
                r["project_url"] or "",
            ))


def cmd_report(args):
    """Write the ranking to reports/<date>-top<N>.{html,md}.

    The ranking itself is never stored — it is a pure function of the tables
    and the weights, so it is recomputed on demand. Only the report is a file,
    and it records the weights it was produced under so a later run that scores
    things differently is not silently confused with this one.
    """
    import os

    cfg = ranker.load_config(args.config)
    conn = store.connect(args.db)
    scores, words = taste.fit(conn, cfg)
    rows = ranker.rank(conn, cfg, taste_scores=scores)

    # Papers you have already rated are papers you have already seen. Leaving
    # them in means next week's digest is clogged with last week's reading —
    # and thumbs-up papers rank themselves straight back to the top.
    if not args.include_rated:
        rated = {r[0] for r in conn.execute("SELECT arxiv_id FROM feedback")}
        rows = [r for r in rows if r["arxiv_id"] not in rated]
        if rated:
            print("  (hiding %d already-rated; --include-rated to keep)" % len(rated))
    rows = rows[:args.top]
    span = conn.execute(
        "SELECT MIN(substr(submitted_at,1,10)), MAX(substr(submitted_at,1,10))"
        " FROM papers"
    ).fetchone()

    render = (report.render_html if args.format == "html" else report.render_markdown)
    text = render(rows, span, store.count(conn), store.count(conn, "hf_meta"),
                  cfg, words=words)

    out = args.out or os.path.join(
        os.path.dirname(store.DEFAULT_DB), "reports",
        "%s-top%d.%s" % (store.today(), args.top, args.format),
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(text)
    print("wrote %s (%d rows)" % (out, len(rows)))


def cmd_feedback(args):
    """Thumbs up/down a paper. Positives train the taste model."""
    conn = store.connect(args.db)
    for spec in args.specs:
        if spec[-1] in "+-":
            aid, label = spec[:-1], 1 if spec[-1] == "+" else -1
        else:
            aid, label = spec, 1
        if not conn.execute(
            "SELECT 1 FROM papers WHERE arxiv_id=?", (aid,)
        ).fetchone():
            print("%s: not in db (ingest it first)" % aid)
            continue
        store.set_feedback(conn, aid, label)
        print("%s %s" % (aid, "+" if label > 0 else "-"))
    conn.commit()

    pos, neg = taste.labels(conn)
    if len(pos) < taste.MIN_POSITIVES:
        print("\n%d/%d positives — taste model inactive until %d"
              % (len(pos), taste.MIN_POSITIVES, taste.MIN_POSITIVES))
    else:
        print("\n%d positives, %d negatives — taste model active" % (len(pos), len(neg)))


def cmd_taste(args):
    """Show what the taste model has learned, in words."""
    cfg = ranker.load_config(args.config)
    conn = store.connect(args.db)
    scores, words = taste.fit(conn, cfg)
    if not words:
        pos, _ = taste.labels(conn)
        print("taste model inactive: %d/%d positives. Use `paperpulse feedback "
              "<arxiv_id>+`." % (len(pos), taste.MIN_POSITIVES))
        return
    print("trained on %d positives, %d negatives\n"
          % (words["n_positive"], words["n_negative"]))
    print("likes:    %s" % ", ".join(words["positive"]))
    print("dislikes: %s" % ", ".join(words["negative"]))


def cmd_show(args):
    cfg = ranker.load_config(args.config)
    conn = store.connect(args.db)
    rows = _ranked(conn, cfg)
    index = {r["arxiv_id"]: i for i, r in enumerate(rows, 1)}

    for aid in args.ids:
        i = index.get(aid)
        if not i:
            print("%s: not in db" % aid)
            continue
        r = rows[i - 1]
        print("%s  rank %d of %d   score %.2f" % (aid, i, len(rows), r["score"]))
        print("  %s" % r["title"])
        print("  venue:   %s" % (r["venue"] or "-"))
        print("  project: %s" % (r["project_url"] or "-"))
        print("  code:    %s" % (r["code_url"] or "-"))
        print("  upvotes: %d   stars: %d" % (r["upvotes"], r["stars"]))
        print("  parts:   %s" % json.dumps(r["parts"]))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="paperpulse")
    ap.add_argument("--db", default=store.DEFAULT_DB)
    ap.add_argument("--config", default=ranker.DEFAULT_CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="fetch arXiv metadata")
    p.add_argument("--days", type=int)
    p.add_argument("--categories", nargs="+")
    p.add_argument("--limit", type=int)
    p.add_argument("--break-after", type=int, default=2, metavar="N",
                   help="stop after N all-known pages (0 disables; use for backfill)")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("reparse", help="re-run venue/URL extraction, no network")
    p.set_defaults(func=cmd_reparse)

    p = sub.add_parser("enrich", help="HF papers API for top-N by free footprint")
    p.add_argument("--top", type=int, default=300)
    p.add_argument("--include-daily", type=int, metavar="DAYS",
                   help="also enrich HF daily papers over the last DAYS")
    p.add_argument("--ids", nargs="+", help="force-include these arXiv ids")
    p.set_defaults(func=cmd_enrich)

    p = sub.add_parser("stars", help="GitHub stars for top-N")
    p.add_argument("--top", type=int, default=50)
    p.set_defaults(func=cmd_stars)

    p = sub.add_parser("pages", help="fetch project pages for teaser + video")
    p.add_argument("--top", type=int, default=60)
    p.add_argument("--refetch", action="store_true")
    p.set_defaults(func=cmd_pages)

    p = sub.add_parser("weekly", help="ingest + enrich + pages + stars + report")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--categories", nargs="+")
    p.add_argument("--limit", type=int)
    p.add_argument("--top", type=int, default=60)
    p.add_argument("--format", choices=["html", "md"], default="html")
    p.add_argument("--include-daily", type=int, default=7, metavar="DAYS")
    p.add_argument("--ids", nargs="+")
    p.add_argument("--refetch", action="store_true")
    p.add_argument("--include-rated", action="store_true")
    p.add_argument("--break-after", type=int, default=2, metavar="N")
    p.add_argument("--force", action="store_true",
                   help="run downstream stages even if arXiv had nothing new")
    p.add_argument("--out")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_weekly)

    p = sub.add_parser("rank", help="print the ranking")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_rank)

    p = sub.add_parser("report", help="write the ranking to reports/")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--format", choices=["html", "md"], default="html")
    p.add_argument("--include-rated", action="store_true")
    p.add_argument("--out")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("feedback", help="thumbs up/down: 2607.25948+ or 2607.11111-")
    p.add_argument("specs", nargs="+", metavar="ID[+|-]")
    p.set_defaults(func=cmd_feedback)

    p = sub.add_parser("taste", help="show what the taste model learned")
    p.set_defaults(func=cmd_taste)

    p = sub.add_parser("show", help="where does a given paper rank, and why")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_show)

    args = ap.parse_args(argv)
    rc = args.func(args)
    return rc if isinstance(rc, int) else 0


if __name__ == "__main__":
    sys.exit(main())
