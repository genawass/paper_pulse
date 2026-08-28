# PaperPulse — weekly research post generator

Design spec. Hand this to Claude Code as the starting brief.

---

## 1. What this is

A nightly ingestion job that silently accumulates scored papers, plus a weekly job
that turns the accumulated pool into a **draft blog post** with an evidence packet
per cited paper.

It does not publish. A human edits the draft before it ships.

**Explicit non-goal:** a daily digest. Daily reading is already covered by
Scholar Inbox. Building a daily report would also force ranking papers on day 0,
when every signal that correlates with significance (citations, code, forks,
upvote accumulation) is still zero. Letting papers age 1–7 days before they are
ranked is the single biggest quality lever in the system.

---

## 2. Decisions still open

Resolve these before writing code — each one changes the architecture.

| # | Decision | Options | Consequence |
|---|---|---|---|
| D1 | Audience | Public post on genawass.github.io / internal Controp memo / both | Public needs the claim-verification pass in §6 and a distinct voice; internal can skip it |
| D2 | Input depth | Abstracts only / abstract + figures / full PDF | Abstracts are ~40× cheaper and cover 80% of triage; full PDF only pays off for the 3–5 papers that make the final post |
| D3 | Lanes | One merged lane / three separate (ATR, synthetic-3D, frontier) | Separate lanes prevent a hot week in 3D generation from crowding out the one thermal paper that mattered |
| D4 | Publish loop | Local draft / auto-PR to the Pages repo | PR is nice but only worth it once the draft quality is proven over ~4 weeks |
| D5 | Post shape | Themed essay (3 sections) / annotated top-10 | Essay is the differentiator; top-10 is a commodity |

Default assumptions if unresolved: D1 public, D2 abstracts for triage + full PDF for
finalists, D3 three lanes, D4 local draft, D5 themed essay.

---

## 3. Data model

SQLite. One table does most of the work.

```
papers
  arxiv_id TEXT PRIMARY KEY
  version INTEGER
  title, abstract, authors TEXT
  categories TEXT            -- JSON list
  submitted_at, updated_at   -- UTC
  first_seen_at
  pdf_url, code_url TEXT     -- code_url from arXiv links + HF metadata
  embedding BLOB             -- float32, cached so re-ranking is free

signals                      -- one row per (paper, day); this is the time series
  arxiv_id, observed_on
  hf_upvotes, hf_comments INTEGER
  github_stars INTEGER
  s2_citation_count INTEGER
  revision_count INTEGER

scores                       -- LLM triage output, one row per (paper, profile)
  arxiv_id, profile, scored_at, model
  relevance, novelty, rigor, applicability  -- 0-5 ints
  contribution TEXT          -- one sentence, extracted not invented
  why_it_matters TEXT        -- tied to the profile charter
  claimed_results TEXT       -- verbatim-ish numbers the paper claims
  confidence REAL
  raw_json TEXT

posts
  week_of, profile, draft_path, published_at, papers_used TEXT
```

Keep `embedding` in the row. Weekly re-ranking must not re-embed.

---

## 4. Nightly job (silent)

1. **Fetch.** arXiv API by category (`cs.CV`, `eess.IV`, `cs.GR`, `cs.RO`, `cs.LG`)
   over the last 24h. Respect the 3s inter-request delay; paginate at 200.
   Expect 400–900 papers/night across those categories.
2. **Fetch community signals.** HuggingFace daily-papers API for the same date.
   This is a separate axis, not a filter — most relevant papers never appear there.
3. **Refresh signals for the trailing 7 days.** Re-poll HF upvotes, GitHub stars,
   and Semantic Scholar citations for every paper still inside the weekly window.
   *This is the step that makes significance measurable.* Append to `signals`, do
   not overwrite — the delta is itself a ranking feature.
4. **Embed.** Local model on the 3060 (`bge-m3` or `Qwen3-Embedding-0.6B`).
   Title + abstract. Cache.
5. **Prefilter.** Cosine against per-profile seed centroids. Keep top 30–40 per lane.
   Free, and cuts LLM cost ~20×.
6. **LLM triage.** Structured JSON per surviving paper. Prompt requirements:
   - Score `relevance` against the profile *charter text*, not keywords.
   - `contribution` must be extractive. If the abstract does not state it, return null.
   - Return `confidence`; low confidence routes to a "check manually" bucket rather
     than being silently dropped.
   - Few-shot with **rejected** examples from `CONTROP_DISTILLED.md`, not just
     accepted ones. Negative examples are what stop the scorer drifting generous.
7. Write to DB. **Emit nothing.**

---

## 5. Weekly job

1. **Assemble pool.** All papers first seen in the last 7 days, per lane.
2. **Composite significance re-rank.** Weighted sum, roughly:
   `relevance .25 / novelty .25 / rigor .15 / applicability .15 / community .12 / artifacts .08`
   Community score = log-scaled HF upvotes **plus upvote velocity** from `signals`.
   Artifacts = code or weights released. Make weights config, expect to tune them
   for the first month.
3. **Cluster.** UMAP + HDBSCAN over the top ~40 embeddings — same recipe as the
   CVPR distillation pipeline, port it. Target 3–5 clusters. Singletons that score
   very high get their own "one-off" section rather than being discarded.
4. **Name the themes.** LLM labels each cluster from its member titles+abstracts.
   Reject labels that are just the lane name.
5. **Deepen the finalists.** For the ~6–10 papers that will actually appear: fetch
   the PDF, extract abstract + intro + results tables + figure captions. This is
   the only place full-text cost is justified.
6. **Draft.** See §6.

---

## 6. Draft generation

The output is a markdown file with Jekyll front matter, written to `drafts/`.

Structure the generator around **evidence packets**, not free generation. For each
finalist, assemble a packet: title, link, authors, extracted contribution, claimed
numbers with the table they came from, code link, and the two or three papers it
displaces or builds on. The drafting prompt sees only packets. It cannot cite a
paper that has no packet.

Post shape:

```
---
title, date, tags, papers_covered (list of arxiv ids)
---

Opening: one paragraph naming the through-line of the week. Not "here are
this week's papers" — an actual claim about what shifted.

## Theme 1
2-3 paragraphs. Papers cited inline. Each paper gets one sentence of what it
does and one of why it matters for the lane.

## Theme 2, Theme 3

## Also worth a look
Bare list, 3-5 items, one line each. This is the pressure valve — it stops the
generator from padding the themed sections with weak papers.

## [PLACEHOLDER] Your take
Explicit empty section the generator must leave for the human.
```

**Verification pass (required if D1 = public).** Second LLM call, adversarial:
given the draft and the packets, flag every factual claim not supported by a
packet. Numbers, dataset names, comparative claims ("outperforms X"), and
architectural descriptions are the failure modes. Output a checklist appended to
the draft as HTML comments, stripped at publish time.

**Anti-slop constraints to enforce in the prompt:**
- No paper appears in more than one theme.
- No sentence of the form "this could have significant implications for."
- Every "why it matters" claim must name a concrete constraint (sensor modality,
  latency budget, hardware, dataset regime) — not a generic benefit.
- If a theme has fewer than 2 genuinely related papers, drop the theme.

---

## 7. Seed corpora

The relevance signal is the entire product. Everything else is plumbing.

Bootstrap each lane from `CONTROP_DISTILLED.md`: the PROD/OFF tags and A/B grades
are already a hand-labeled relevance dataset. Extract to `seeds/<lane>.txt` as
arXiv IDs with a `+`/`-` label. Target 25–40 positives and 15+ negatives per lane.

Add a `paperpulse feedback <arxiv_id> +|-` CLI so grading a paper while reading
the weekly post updates the seed set. Re-fit the centroid weekly. Without this
the tool ossifies at whatever you seeded it with in week one.

---

## 8. Layout

```
paperpulse/
  config.yaml            # lanes, charters, weights, model names
  seeds/<lane>.txt
  paperpulse/
    sources/arxiv.py, hf.py, s2.py, github.py
    store.py             # SQLite, migrations
    embed.py             # local, GPU
    triage.py            # LLM scoring, structured output
    rank.py              # composite score
    cluster.py           # UMAP + HDBSCAN
    packet.py            # evidence packet assembly + PDF extraction
    draft.py             # post generation
    verify.py            # adversarial fact-check pass
    cli.py               # ingest / weekly / feedback / backfill
  drafts/
  reports/
```

---

## 9. Build order

Do not build this end to end. Each step is independently checkable.

1. `sources/arxiv.py` + `store.py` + `cli ingest`. Run it for a week. Just look at
   what lands in the DB.
2. `embed.py` + prefilter. Manually check the top 40 for one lane against your own
   judgement. **If the prefilter is bad, nothing downstream can save it** — fix the
   seeds before continuing.
3. `triage.py`. Score 100 papers, compare against your own grades on the same 100.
   Target: the scorer's top 10 and yours overlap by 6+.
4. `rank.py` + `cluster.py`. Eyeball the clusters for 2 weeks with no drafting.
5. `packet.py` + `draft.py`. First drafts will be bad. Iterate on the prompt with
   real packets, not synthetic ones.
6. `verify.py`, then the publish loop.

Steps 1–3 are ~60% of the value. Consider stopping there for a month and writing
the posts yourself from the ranked pool before automating step 5.

---

## 10. Cost sanity check

At 40 papers/lane/night × 3 lanes × ~600 tokens in / 250 out, triage is roughly
2M input tokens per month. Embeddings are free on local hardware. Full-PDF
extraction touches ~40 papers/month. This is a small monthly bill, not a
consideration that should shape the design — do not compromise the pipeline to
save tokens.