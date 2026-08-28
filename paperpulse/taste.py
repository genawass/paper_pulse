"""Learned taste ranker — ported from karpathy/arxiv-sanity-lite.

The idea worth stealing: train a LinearSVC where your thumbs-up papers are the
positive class and *the entire unlabeled corpus* is the negative class. You
never have to curate negatives — there are thousands of them and they are all
free. `class_weight='balanced'` absorbs the 10-vs-1000 imbalance.

This is strictly better than the seed-centroid in doc.md §7, which cannot use
negative evidence at all: cosine-to-a-mean has no decision boundary to push.

The second thing worth stealing is that `clf.coef_` is directly readable. The
model can tell you, in words, what it thinks you like — which makes a wrong
model debuggable instead of merely disappointing.

TF-IDF settings are arxiv-sanity-lite's: ngram_range=(1,2), min_df=5,
max_df=0.1, max_features=20000. max_df in particular is doing real work — it
drops terms appearing in >10% of abstracts, which is most academic boilerplate.
"""

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC

MIN_POSITIVES = 5  # below this the boundary is noise; taste stays off


def _corpus(conn):
    ids, texts = [], []
    for row in conn.execute(
        "SELECT arxiv_id, title, abstract FROM papers ORDER BY arxiv_id"
    ):
        ids.append(row["arxiv_id"])
        texts.append("%s. %s" % (row["title"] or "", row["abstract"] or ""))
    return ids, texts


def labels(conn):
    pos, neg = set(), set()
    for row in conn.execute("SELECT arxiv_id, label FROM feedback"):
        (pos if row["label"] > 0 else neg).add(row["arxiv_id"])
    return pos, neg


def fit(conn, cfg=None):
    """Train on current feedback. Returns (scores_by_id, words) or (None, None).

    `scores_by_id` maps arxiv_id -> percentile in [0, 1] rather than the raw
    decision function, so the blend weight in config.yaml means the same thing
    regardless of C, corpus size, or how many labels exist.
    """
    pos, neg = labels(conn)
    if len(pos) < MIN_POSITIVES:
        return None, None

    ids, texts = _corpus(conn)
    tcfg = (cfg or {}).get("taste", {}) if cfg else {}

    vec = TfidfVectorizer(
        input="content", encoding="utf-8", stop_words="english",
        strip_accents="unicode", lowercase=True,
        ngram_range=(1, 2), min_df=tcfg.get("min_df", 5),
        max_df=tcfg.get("max_df", 0.1), max_features=tcfg.get("max_features", 20000),
        norm="l2", use_idf=True, sublinear_tf=True,
    )
    try:
        x = vec.fit_transform(texts)
    except ValueError:
        return None, None  # corpus too small / vocabulary empty

    index = {a: i for i, a in enumerate(ids)}
    y = np.zeros(len(ids), dtype=np.float32)
    for a in pos:
        if a in index:
            y[index[a]] = 1.0

    # Explicit thumbs-down count for more than an unlabeled paper: they are
    # the near-misses, and near-misses are where the boundary actually lives.
    weight = np.ones(len(ids), dtype=np.float32)
    for a in neg:
        if a in index:
            weight[index[a]] = 5.0

    clf = LinearSVC(class_weight="balanced", max_iter=10000, tol=1e-6,
                    C=tcfg.get("C", 0.01))
    clf.fit(x, y, sample_weight=weight)
    raw = clf.decision_function(x)

    # Percentile, so 0 = worst paper in the corpus and 1 = best.
    order = np.argsort(np.argsort(raw))
    pct = order / max(len(order) - 1, 1)
    scores = {a: float(pct[i]) for a, i in index.items()}

    ivocab = {v: k for k, v in vec.vocabulary_.items()}
    coef = clf.coef_[0]
    sortix = np.argsort(-coef)
    words = {
        "positive": [ivocab[i] for i in sortix[:25]],
        "negative": [ivocab[i] for i in sortix[-15:]][::-1],
        "n_positive": len(pos),
        "n_negative": len(neg),
    }
    return scores, words
