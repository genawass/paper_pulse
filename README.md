# PaperPulse — metadata ranker (stage 1)

Finds new, applied, *showable* CV work without an LLM, an embedding model, or a
seed corpus. Ranks on **artifact footprint**: the signals a paper carries on its
first day, before anyone has cited it.

This is the cheapest slice of [doc.md](doc.md) — built first because it tests the
premise. If footprint alone surfaces the papers you'd have wanted to see, most of
the LLM pipeline is garnish.

## Why footprint

The design doc weights `artifacts` at 0.08, behind `rigor`. That is backwards for
this goal. Building a project page, releasing weights, and hosting a demo is an
expensive signal — labs don't do it for work they think is incremental — and
unlike citations it exists on day 1.

The single highest-value field is `arxiv:comment`, which routinely carries both a
venue acceptance and a project page for free:

```
Accepted at ICML 2026. Project page: https://modus-multimodal.epfl.ch
```

## Result on the first run

14 days of cs.CV/eess.IV/cs.GR, 2026-07-15 → 07-28: **1136 papers ingested, 290
enriched, MODUS ranked 3rd.**

MODUS (arXiv 2607.25948) is the control — spotted by hand on LinkedIn, so it is a
known positive. It had **4 HF upvotes and 16 GitHub stars** the day after posting,
while that day's top HF paper had 78. A community-signal ranker would have buried
it. It ranks 3rd on footprint: project page, code, ICML 2026, teaser media, daily-papers.

Age normalization is what moves it from 20th to 3rd, and it is on by default for
that reason — without it the ranking sorts by how long a paper has been up, which
for a weekly post is the wrong axis.

Signal coverage across the 1136:

| Signal | Count | Share |
|---|---|---|
| code URL | 211 | 18.6% |
| venue | 139 | 12.2% |
| — top tier | 84 | 7.4% |
| project page | 113 | 9.9% |

Of the 290 enriched: 106 have an HF page, 71 hit daily papers, 27 released a model,
25 a dataset, 15 a Space.

## Signals

| Source | Signal | Cost |
|---|---|---|
| arXiv `comment` | venue + tier, oral/spotlight, project page, code URL | free, in the ingest response |
| arXiv `abstract` | code URL fallback | free |
| HF `/api/papers/{id}` | upvotes, project page, GitHub repo **and stars**, teaser images, daily-papers date | 1 request |
| HF `/api/arxiv/{id}/repos` | linked models / datasets / spaces | 1 request, lags by days |
| GitHub | stars for repos HF doesn't cover | 60/hr unauthenticated |

## Running

One command runs everything and re-renders the page:

```bash
pip install -r requirements.txt
python -m paperpulse.cli weekly --days 7 --top 60
```

That chains ingest → enrich → pages → stars → report. Or run the stages
individually:

```bash
python -m paperpulse.cli ingest --days 14          # arXiv, full coverage, free
python -m paperpulse.cli enrich --top 400 --include-daily 14
python -m paperpulse.cli pages --top 60            # project pages: teaser + demo video
python -m paperpulse.cli stars --top 50            # needs GITHUB_TOKEN to be useful
python -m paperpulse.cli report --top 60           # -> reports/<date>-top60.html
python -m paperpulse.cli report --format md        # markdown instead

python -m paperpulse.cli rank --top 40             # ranking to terminal
python -m paperpulse.cli show 2607.25948           # where one paper landed, and why
python -m paperpulse.cli reparse                   # re-apply extraction rules, no network

python -m paperpulse.cli feedback 2607.25948+ 2607.11111-   # thumbs up / down
python -m paperpulse.cli taste                             # what it learned, in words
```

## Automation

`run.sh` is the cron entry point, structured after arxiv-sanity-lite's update
script: **the fetch signals "nothing new" through its exit status**, and every
expensive stage downstream is gated on it.

```bash
crontab -e
17 7 * * *  /home/genadiy/dev/paper_pulse/run.sh >> /home/genadiy/dev/paper_pulse/paperpulse.log 2>&1
```

`ingest` exits 2 when arXiv had nothing genuinely new, so a quiet day costs one
API call rather than 30+ minutes of throttled HuggingFace requests. Exit 1 means
arXiv was unreachable — kept distinct so an outage never reads as a quiet day.
`weekly` has the same gate built in (`--force` overrides).

Paging also terminates early: `--break-after N` stops after N consecutive
all-known pages, and immediately if the very first page is all-known. Default 2.
Use `--break-after 0` for a backfill, where you want the full window every time.

Off-the-hour cron time is deliberate — everything on earth fires at :00.

## Learned taste

Ported from [karpathy/arxiv-sanity-lite](https://github.com/karpathy/arxiv-sanity-lite).
Thumbs-up papers are the positive class and **the entire unlabeled corpus is the
negative class** — `LinearSVC(class_weight='balanced', C=0.01)` over TF-IDF of
title+abstract. You never curate negatives; there are a thousand of them and they
are free.

This is strictly better than the seed centroid in doc.md §7, which cannot use
negative evidence at all — cosine-to-a-mean has no boundary to push. Explicit
thumbs-down get 5× sample weight, since near-misses are where the boundary lives.

It stays off until 5 thumbs-up exist, then contributes up to `rank.taste` (3.0)
points by percentile. `paperpulse taste` prints the words the model weights most
and least — so a model that has learned the wrong thing is visible rather than
merely disappointing.

Rated papers are hidden from the report by default: you have already seen them,
and thumbs-up papers otherwise rank themselves straight back to the top.
`--include-rated` keeps them.

The HTML report is interactive: search over titles and abstracts, sort by score /
date / upvotes, and seven filter toggles (has demo video, hosted demo, code,
weights or data, project page, top venue, last 3 days). Teaser thumbnails come
from the project page's `og:image`, falling back to HuggingFace uploads.
Everything is inline — no framework, no build step, no CDN — and it follows the
OS light/dark setting.

One caveat: **thumbnails are hotlinked** to the authors' own hosts, which keeps
the file at ~135 KB but means the page needs network to show images and will rot
as project pages move. Embedding them as data: URIs would make it genuinely
self-contained at roughly 2 MB. Hotlinking is right for a local report;
reconsider before publishing to the blog.

Staged so the expensive calls come last: ingest is free and complete, `enrich`
only spends requests on papers that already showed a free signal, `stars` only on
the shortlist.

`--include-daily` unions in HF daily papers so that a paper with a bare comment
field but real community traction still gets enriched. Without it the prescore is
blind to anything that didn't announce itself in its own metadata.

## Layout

```
config.yaml            weights — every number is a starting guess
paperpulse/
  store.py             SQLite; papers, hf_meta, signals (append-only)
  links.py             venue + URL extraction from comment/abstract. No network.
  rank.py              footprint composite, with per-paper breakdown
  cli.py               ingest / enrich / stars / rank / show
  sources/
    arxiv.py           Atom API, paginated, 3s pacing
    hf.py              papers API + daily papers
    github.py          stars, top-N only
```

## Known limits

- **Footprint favours well-resourced labs.** EPFL, Apple, Meta ship project pages
  and Spaces; a sharp three-person group often ships a bare PDF. This is in part a
  big-lab detector, and nothing in the pipeline will tell you what it missed.
- **`/api/arxiv/{id}/repos` lags.** MODUS has a HF model, dataset, and Space listed
  on its project page, and the endpoint returned empty for all three the day after
  posting. Absence is not evidence; the `hf_model`/`hf_dataset`/`hf_space` weights
  under-fire on the newest papers, which are exactly the ones being ranked.
- **No demonstrability or novelty axis yet.** Both need an LLM. `hf_media` (teaser
  images uploaded) is a weak proxy for the first and nothing covers the second.
- **Community is near zero inside a 7-day window** and is meant to break ties, not
  drive the ranking. `rank.age_normalize` exists for when it starts to matter.
