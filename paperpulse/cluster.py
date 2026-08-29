"""TF-IDF theme clustering.

A cheaper substitute for doc.md §5's embedding + UMAP/HDBSCAN pipeline. This
is for *browsing* — grouping the ranked pool so it can be searched/filtered
by theme — not for essay generation, so it skips the parts of §5 that only
matter there (no LLM naming call, no "drop themes with <2 papers" rule).

KMeans over TF-IDF of title+abstract, same vectorizer family as taste.py
(ngram_range=(1,2), max_df to drop boilerplate). k is picked by silhouette
score over a small range — doc.md targets 3-5 clusters, so that's the
default search window. Each cluster is labeled by its own top TF-IDF terms,
each term claimed by at most one cluster so labels don't collide.

Clusters smaller than `min_cluster_size`, and the whole pool when it's too
small to cluster meaningfully, fall into theme_id=-1 / theme="Other" —
doc.md's "one-off" bucket.
"""

import numpy as np
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score

MIN_PAPERS = 8   # below this, k-means over TF-IDF is noise
TOP_TERMS = 3    # words per label, e.g. "diffusion / video / editing"
OTHER = "Other"


def _stem(w):
    """Collapse plural/singular so "world model" and "world models" don't
    both make the same label. Crude on purpose — this only needs to catch
    trailing-s, not do real stemming."""
    return " ".join(t[:-1] if t.endswith("s") and len(t) > 3 else t for t in w.split())


def _label(centroid, ivocab, used_terms):
    order = np.argsort(-centroid)
    words = []
    for i in order:
        if centroid[i] <= 0:
            break
        w = ivocab[i]
        stem = _stem(w)
        if stem in used_terms:
            continue
        words.append(w)
        used_terms.add(stem)
        if len(words) == TOP_TERMS:
            break
    return " / ".join(words) if words else None


def _all_other(rows):
    return {r["arxiv_id"]: {"theme_id": -1, "theme": OTHER} for r in rows}


def fit(rows, cfg=None):
    """Cluster `rows` (dicts with arxiv_id/title/abstract) by TF-IDF.

    Returns {arxiv_id: {"theme_id": int, "theme": label}}. theme_id is -1
    and theme is "Other" for papers that don't land in a real cluster.
    """
    if len(rows) < MIN_PAPERS:
        return _all_other(rows)

    ccfg = (cfg or {}).get("cluster", {}) if cfg else {}
    texts = ["%s. %s" % (r.get("title") or "", r.get("abstract") or "") for r in rows]

    vec = TfidfVectorizer(
        stop_words="english", strip_accents="unicode", lowercase=True,
        ngram_range=(1, 2), min_df=ccfg.get("min_df", 2),
        max_df=ccfg.get("max_df", 0.4), max_features=ccfg.get("max_features", 5000),
        norm="l2", use_idf=True, sublinear_tf=True,
    )
    try:
        x = vec.fit_transform(texts)
    except ValueError:
        return _all_other(rows)  # vocabulary collapsed to nothing usable

    lo = ccfg.get("k_min", 3)
    hi = min(ccfg.get("k_max", 6), len(rows) - 1)
    best = None
    for k in range(lo, hi + 1):
        if k < 2:
            continue
        km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(x)
        if len(set(km.labels_)) < 2:
            continue
        score = silhouette_score(x, km.labels_)
        if best is None or score > best[0]:
            best = (score, km)
    if best is None:
        return _all_other(rows)

    _, km = best
    k = km.n_clusters
    ivocab = {v: w for w, v in vec.vocabulary_.items()}
    counts = np.bincount(km.labels_, minlength=k)
    min_size = ccfg.get("min_cluster_size", 2)

    used_terms = set()
    cluster_label = {}
    # Label largest clusters first so the most-populated themes get first
    # pick of their strongest terms.
    for c in sorted(range(k), key=lambda c: -counts[c]):
        if counts[c] < min_size:
            cluster_label[c] = None
            continue
        cluster_label[c] = _label(km.cluster_centers_[c], ivocab, used_terms) or OTHER

    out = {}
    for r, c in zip(rows, km.labels_):
        label = cluster_label[c]
        out[r["arxiv_id"]] = (
            {"theme_id": -1, "theme": OTHER} if label is None
            else {"theme_id": int(c), "theme": label}
        )
    return out
