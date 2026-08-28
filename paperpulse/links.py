"""Extract artifact signals from arXiv metadata alone.

The `arxiv:comment` field is the highest-value text in the whole record. It
routinely carries both a venue acceptance and a project-page URL, at no cost
and on the paper's first day:

    Accepted at ICML 2026. Project page: https://modus-multimodal.epfl.ch

Everything here is regex over comment + abstract. No network.
"""

import re

URL_RE = re.compile(r"https?://[^\s<>\"'\)\]}]+", re.I)

# Venue -> tier. Order matters: longer names are matched first so that
# "SIGGRAPH Asia" does not get swallowed by "SIGGRAPH".
VENUES = [
    ("SIGGRAPH Asia", "top"), ("SIGGRAPH", "top"),
    ("NeurIPS", "top"), ("NIPS", "top"),
    ("CVPR", "top"), ("ICCV", "top"), ("ECCV", "top"),
    ("ICML", "top"), ("ICLR", "top"),
    ("CoRL", "strong"), ("RSS", "strong"), ("ICRA", "strong"), ("IROS", "strong"),
    ("AAAI", "strong"), ("WACV", "strong"), ("BMVC", "strong"), ("3DV", "strong"),
    ("ACM Multimedia", "strong"), ("ACM MM", "strong"), ("ACMMM", "strong"),
    ("MICCAI", "strong"), ("ACCV", "strong"), ("ICME", "other"),
    ("ICIP", "other"), ("ICASSP", "other"), ("ICPR", "other"),
    ("ACL", "strong"), ("EMNLP", "strong"), ("NAACL", "strong"),
    ("TPAMI", "top"), ("IJCV", "strong"), ("TOG", "top"),
]

ORAL_RE = re.compile(r"\b(oral|spotlight|highlight|best paper|award)\b", re.I)
YEAR_RE = r"(?:20\d{2}|'\d{2})"

PROJECT_CUE_RE = re.compile(
    r"(project\s*(?:page|site|website)|webpage|web\s*page|"
    r"more\s+results|videos?\s+(?:and|&)\s+code|demo)\W{0,20}$",
    re.I,
)
CODE_CUE_RE = re.compile(r"(code|implementation|source|repo(?:sitory)?)\W{0,20}$", re.I)

PROJECT_HOST_HINTS = ("github.io", "sites.google.com", "notion.site", "gitlab.io",
                      "pages.dev", "vercel.app", "netlify.app")
SKIP_HOSTS = ("arxiv.org", "doi.org", "openreview.net", "creativecommons.org",
              "paperswithcode.com", "semanticscholar.org")


def _clean(url):
    """Trim trailing sentence punctuation that regex greedily absorbed."""
    return url.rstrip(".,;:!?’'\")]}")


def _host(url):
    m = re.match(r"https?://([^/]+)", url, re.I)
    if not m:
        return ""
    host = m.group(1).lower()
    return host[4:] if host.startswith("www.") else host


def extract_venue(comment, journal_ref=None):
    """Return (venue, tier, is_oral). Venue is e.g. "ICML 2026"."""
    text = " ".join(t for t in (comment, journal_ref) if t)
    if not text:
        return None, None, False

    # Computed independently of the venue lookup: "accepted for oral
    # presentation at <venue we don't recognise>" should still score the
    # oral, otherwise an unlisted venue silently costs two signals.
    is_oral = bool(ORAL_RE.search(text)) and bool(
        re.search(r"\b(accept\w*|present\w*|selected)\b", text, re.I)
    )

    accepted = re.search(
        r"\b(accepted|to appear|camera[- ]ready|published|appears?)\b", text, re.I
    )

    for name, tier in VENUES:
        pat = re.compile(r"\b%s\b\s*(%s)?" % (re.escape(name), YEAR_RE), re.I)
        m = pat.search(text)
        if not m:
            continue
        # Guard against "we outperform the CVPR 2024 baseline". A real
        # acceptance sits next to an acceptance verb, or stands alone in a
        # short clause ("CVPR 2026 Oral", "10 pages. NeurIPS 2025").
        if accepted and abs(m.start() - accepted.start()) < 60:
            pass
        elif _standalone_clause(text, m.start()):
            pass
        else:
            continue
        year = m.group(1)
        venue = "%s %s" % (name, year) if year else name
        return venue, tier, is_oral

    return None, None, is_oral


def _standalone_clause(text, pos):
    """True if the clause containing `pos` is short and not comparative."""
    start = max(text.rfind(c, 0, pos) for c in ".;,()") + 1
    ends = [i for i in (text.find(c, pos) for c in ".;,()") if i != -1]
    clause = text[start:min(ends)] if ends else text[start:]
    if len(clause.strip()) > 45:
        return False
    return not re.search(
        r"\b(than|outperform\w*|baseline|compared|versus|vs|prior|previous|"
        r"follow\w*|based on|unlike|the\s+\w+\s*$)\b", clause, re.I
    )


def extract_urls(comment, abstract=None):
    """Return (project_url, code_url), best-effort, from free text.

    Classification is by host first, then by the words immediately preceding
    the URL — comments phrase it as "Project page: <url>" or "Code: <url>"
    often enough that the cue is worth reading.
    """
    project, code = None, None
    bare_in_comment = []  # unclassified URLs from the comment field only

    for text, is_comment in ((comment, True), (abstract, False)):
        if not text:
            continue
        for m in URL_RE.finditer(text):
            url = _clean(m.group(0))
            host = _host(url)
            if not host or any(s in host for s in SKIP_HOSTS):
                continue

            before = text[max(0, m.start() - 60):m.start()]

            if "github.com" in host or "gitlab.com" in host or "bitbucket.org" in host:
                if not code:
                    code = url
                continue

            if "huggingface.co" in host:
                continue  # captured separately by the HF enrichment step

            if project:
                continue
            if any(h in host for h in PROJECT_HOST_HINTS):
                project = url
            elif PROJECT_CUE_RE.search(before):
                project = url
            elif CODE_CUE_RE.search(before) and not code:
                code = url
            elif is_comment:
                bare_in_comment.append(url)

    # A bare URL in the comment field is nearly always the project page —
    # there is no other reason to put one there. Abstracts are noisier
    # ("code will be released at ..."), so this fallback excludes them.
    if not project and len(bare_in_comment) == 1:
        project = bare_in_comment[0]

    return project, code


def enrich(paper):
    """Attach project_url / code_url / venue / venue_tier / is_oral in place."""
    project, code = extract_urls(paper.get("comment"), paper.get("abstract"))
    venue, tier, oral = extract_venue(paper.get("comment"), paper.get("journal_ref"))
    paper["project_url"] = project
    paper["code_url"] = code
    paper["venue"] = venue
    paper["venue_tier"] = tier
    paper["is_oral"] = oral
    return paper
