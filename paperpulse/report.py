"""Report rendering — interactive HTML and plain markdown.

The HTML page embeds its rows as JSON and does search / sort / filter in the
browser. No build step, no framework, no network at load time except the
thumbnails themselves (which are hotlinked, see `THUMBNAIL NOTE` below).

THUMBNAIL NOTE: teaser images point at the authors' own hosts. That keeps the
file small (~50 KB for 60 papers) but means the page needs network to show
images and will rot as project pages move. Embedding them as data: URIs would
make it truly self-contained at roughly 2 MB. Hotlinking is the right default
for a local report; reconsider before publishing to the blog.
"""

import html
import json


def _meta_line(span, n_papers, n_enriched):
    return ("%s to %s · %d papers · %d enriched · ranked on artifact footprint, "
            "no LLM." % (span[0], span[1], n_papers, n_enriched))


def render_markdown(rows, span, n_papers, n_enriched, cfg, words=None):
    has_themes = any(r.get("theme") for r in rows)
    lines = [
        "# PaperPulse — top %d" % len(rows), "",
        _meta_line(span, n_papers, n_enriched), "",
        "Weights: `%s`  age_normalize=%s"
        % (cfg["rank"]["footprint"], cfg["rank"].get("age_normalize")), "",
    ]
    header = ["#", "Score", "Paper"] + (["Theme"] if has_themes else []) + \
             ["Venue", "Links", "Signals"]
    lines.append("| %s |" % " | ".join(header))
    lines.append("|%s|" % ("---|" * len(header)))
    for i, r in enumerate(rows, 1):
        links = []
        if r["project_url"]:
            links.append("[page](%s)" % r["project_url"])
        if r["code_url"]:
            links.append("[code](%s)" % r["code_url"])
        links.append("[arXiv](https://arxiv.org/abs/%s)" % r["arxiv_id"])
        signals = ", ".join(k for k in sorted(r["parts"]) if k != "community")
        cells = [str(i), "%.2f" % r["score"], "**%s**" % r["title"].replace("|", "\\|")[:88]]
        if has_themes:
            cells.append(r.get("theme") or "—")
        cells += [r["venue"] or "—", " · ".join(links), signals]
        lines.append("| %s |" % " | ".join(cells))
    return "\n".join(lines) + "\n"


CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--muted:#6b6b66;--line:#e6e6e1;--card:#fff;
--chip:#f0f0ec;--chipfg:#5d5d57;--bar:#c2d3ba;--accent:#3d6b45;--shadow:0 1px 2px rgba(0,0,0,.05);
--top:#2f5d3a;--topbg:#e3efe4;--strong:#6b5a2f;--strongbg:#f3ecdb;--other:#5d5d57;--otherbg:#eee;
--vid:#7a3b52;--vidbg:#f7e6ec;--ph:#eeeee9}
@media(prefers-color-scheme:dark){:root{--bg:#131310;--fg:#e9e9e3;--muted:#9b9b93;--line:#2a2a24;
--card:#1a1a15;--chip:#26261f;--chipfg:#a3a39a;--bar:#3d5744;--accent:#8fc79b;--shadow:none;
--top:#9fd4a9;--topbg:#1d3323;--strong:#d8c389;--strongbg:#312b18;--other:#a3a39a;--otherbg:#26261f;
--vid:#e3a8bd;--vidbg:#361f27;--ph:#201f1a}}
:root[data-theme=light]{--bg:#fbfbfa;--fg:#1a1a18;--muted:#6b6b66;--line:#e6e6e1;--card:#fff;
--chip:#f0f0ec;--chipfg:#5d5d57;--bar:#c2d3ba;--accent:#3d6b45;--shadow:0 1px 2px rgba(0,0,0,.05);
--top:#2f5d3a;--topbg:#e3efe4;--strong:#6b5a2f;--strongbg:#f3ecdb;--other:#5d5d57;--otherbg:#eee;
--vid:#7a3b52;--vidbg:#f7e6ec;--ph:#eeeee9}
:root[data-theme=dark]{--bg:#131310;--fg:#e9e9e3;--muted:#9b9b93;--line:#2a2a24;--card:#1a1a15;
--chip:#26261f;--chipfg:#a3a39a;--bar:#3d5744;--accent:#8fc79b;--shadow:none;
--top:#9fd4a9;--topbg:#1d3323;--strong:#d8c389;--strongbg:#312b18;--other:#a3a39a;--otherbg:#26261f;
--vid:#e3a8bd;--vidbg:#361f27;--ph:#201f1a}

*{box-sizing:border-box}
body{margin:0;padding:2.25rem 1.1rem 4rem;background:var(--bg);color:var(--fg);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:1.7rem;font-weight:650;letter-spacing:-.02em;margin:0 0 .35rem}
.sub{color:var(--muted);font-size:.92rem;margin:0 0 1.4rem}

.bar{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin-bottom:.75rem}
input[type=search],select{font:inherit;font-size:.87rem;padding:.4rem .6rem;color:var(--fg);
background:var(--card);border:1px solid var(--line);border-radius:6px}
input[type=search]{flex:1;min-width:190px}
input[type=search]:focus,select:focus{outline:2px solid var(--accent);outline-offset:-1px}
.filters{display:flex;flex-wrap:wrap;gap:.35rem;margin-bottom:1.1rem}
.f{font:inherit;font-size:.78rem;padding:.26rem .6rem;border-radius:20px;cursor:pointer;
background:var(--chip);color:var(--chipfg);border:1px solid transparent;transition:.12s}
.f:hover{border-color:var(--line)}
.f[aria-pressed=true]{background:var(--accent);color:var(--bg);font-weight:600}
.count{color:var(--muted);font-size:.82rem;margin-bottom:1rem}

.card{display:flex;gap:.95rem;padding:.9rem 0;border-top:1px solid var(--line)}
.card:hover .t a{border-bottom-color:var(--accent)}
.rk{color:var(--muted);font-variant-numeric:tabular-nums;font-size:.8rem;
min-width:1.6rem;text-align:right;padding-top:.15rem}
.thumb{width:132px;height:80px;flex-shrink:0;border-radius:6px;overflow:hidden;
background:var(--ph);box-shadow:var(--shadow)}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}
.thumb.empty{display:flex;align-items:center;justify-content:center;
color:var(--muted);font-size:.66rem;letter-spacing:.05em}
.body{flex:1;min-width:0}
.t{font-weight:560;font-size:.97rem;line-height:1.35;margin:0 0 .3rem}
.t a{color:inherit;text-decoration:none;border-bottom:1px solid transparent}
.meta{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;
font-size:.75rem;color:var(--muted);margin-bottom:.4rem}
.aid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.badge{padding:.1rem .4rem;border-radius:4px;font-size:.7rem;font-weight:650}
.badge.top{color:var(--top);background:var(--topbg)}
.badge.strong{color:var(--strong);background:var(--strongbg)}
.badge.other{color:var(--other);background:var(--otherbg)}
.badge.video{color:var(--vid);background:var(--vidbg)}
.badge.theme{color:var(--chipfg);background:var(--chip)}
.blurb{font-size:.85rem;color:var(--muted);margin:0 0 .45rem;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.blurb.open{-webkit-line-clamp:unset;display:block}
.more{background:none;border:0;padding:0;font:inherit;font-size:.76rem;
color:var(--accent);cursor:pointer;margin-bottom:.4rem}
.more:hover{text-decoration:underline}
.chips{display:flex;flex-wrap:wrap;gap:.22rem;margin-bottom:.4rem}
.chip{background:var(--chip);color:var(--chipfg);border-radius:3px;padding:.08rem .32rem;
font-size:.68rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.links a{color:var(--accent);text-decoration:none;font-size:.8rem;margin-right:.7rem}
.links a:hover{text-decoration:underline}
.sc{text-align:right;min-width:3.4rem;padding-top:.1rem}
.sc b{display:block;font-size:.9rem;font-variant-numeric:tabular-nums;font-weight:650}
.sc .track{height:3px;background:var(--line);border-radius:2px;overflow:hidden;margin-top:.25rem}
.sc .fill{height:100%;background:var(--bar);display:block}
.empty-msg{padding:2.5rem 0;text-align:center;color:var(--muted)}
.taste{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:.7rem .9rem;margin-bottom:1.1rem;font-size:.8rem}
.taste summary{cursor:pointer;color:var(--muted);font-weight:600}
.taste p{margin:.55rem 0 0;line-height:1.6}
.taste b{color:var(--fg);font-weight:650}
.taste .w{background:var(--chip);color:var(--chipfg);border-radius:3px;
padding:.06rem .3rem;margin-right:.2rem;display:inline-block;margin-bottom:.2rem;
font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.72rem}
footer{margin-top:2.5rem;padding-top:1.1rem;border-top:1px solid var(--line);
color:var(--muted);font-size:.79rem}
@media(max-width:620px){
  .thumb{width:84px;height:56px}
  .rk{display:none}
  .card{gap:.65rem}
}
"""

JS = """
const DATA = __DATA__;
const $ = s => document.querySelector(s);
const state = {q:'', sort:'score', theme:'', filters:new Set()};

function populateThemes(){
  const sel = $('#theme');
  if (!sel) return;
  const counts = new Map();
  for (const r of DATA) if (r.theme) counts.set(r.theme, (counts.get(r.theme)||0)+1);
  if (!counts.size) { sel.hidden = true; return; }
  const other = counts.get('Other') || 0;
  const names = [...counts.keys()].filter(t => t !== 'Other')
    .sort((a,b) => counts.get(b)-counts.get(a));
  if (other) names.push('Other');
  for (const t of names) {
    const o = document.createElement('option');
    o.value = t; o.textContent = `${t} (${counts.get(t)})`;
    sel.appendChild(o);
  }
}

const TESTS = {
  video:  r => r.has_video,
  demo:   r => r.parts.hf_space,
  code:   r => !!r.code_url,
  page:   r => !!r.project_url,
  venue:  r => r.venue_tier === 'top',
  weights:r => r.parts.hf_model || r.parts.hf_dataset,
  fresh:  r => r.age_days <= 3,
};

function current(){
  let rows = DATA.filter(r => {
    if (state.theme && r.theme !== state.theme) return false;
    for (const f of state.filters) if (!TESTS[f](r)) return false;
    if (!state.q) return true;
    return (r.title + ' ' + (r.abstract||'') + ' ' + (r.venue||'')).toLowerCase()
           .includes(state.q);
  });
  const key = state.sort;
  rows.sort((a,b) => key==='date' ? (b.submitted_at>a.submitted_at?1:-1)
                   : key==='upvotes' ? b.upvotes-a.upvotes
                   : b.score-a.score);
  return rows;
}

// Escapes quotes too: these strings land inside attributes (src, href), and
// og:image / titles come from arbitrary external pages.
function esc(s){ return String(s??'').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function card(r, i, max){
  const thumb = r.thumb
    ? `<div class="thumb"><img src="${esc(r.thumb)}" alt="" loading="lazy"
         referrerpolicy="no-referrer" onerror="this.parentNode.classList.add('empty');
         this.parentNode.textContent='NO IMAGE'"></div>`
    : `<div class="thumb empty">NO IMAGE</div>`;

  const badges = [];
  if (r.theme && r.theme !== 'Other')
    badges.push(`<span class="badge theme">${esc(r.theme)}</span>`);
  if (r.venue) badges.push(`<span class="badge ${r.venue_tier||'other'}">${esc(r.venue)}</span>`);
  if (r.has_video) badges.push(`<span class="badge video">video</span>`);

  const links = [];
  if (r.project_url) links.push(`<a href="${esc(r.project_url)}">page</a>`);
  if (r.code_url) links.push(`<a href="${esc(r.code_url)}">code</a>`);
  links.push(`<a href="https://arxiv.org/abs/${esc(r.arxiv_id)}">arXiv</a>`);

  const chips = Object.keys(r.parts).filter(k=>k!=='community').sort()
    .map(k=>`<span class="chip">${esc(k.replace(/_/g,' '))}</span>`).join('');

  const text = r.blurb || r.abstract || '';
  const age = r.age_days < 1 ? 'today' : `${Math.round(r.age_days)}d ago`;

  return `<article class="card">
    <div class="rk">${i+1}</div>
    ${thumb}
    <div class="body">
      <p class="t"><a href="https://arxiv.org/abs/${esc(r.arxiv_id)}">${esc(r.title)}</a></p>
      <div class="meta"><span class="aid">${esc(r.arxiv_id)}</span><span>·</span>
        <span>${age}</span>${r.upvotes?`<span>·</span><span>${r.upvotes} upvotes</span>`:''}
        ${r.stars?`<span>·</span><span>${r.stars}★</span>`:''}${badges.join('')}</div>
      ${text?`<p class="blurb">${esc(text)}</p>
        <button class="more" onclick="this.previousElementSibling.classList.toggle('open');
          this.textContent=this.textContent==='more'?'less':'more'">more</button>`:''}
      <div class="chips">${chips}</div>
      <div class="links">${links.join('')}</div>
    </div>
    <div class="sc"><b>${r.score.toFixed(2)}</b>
      <span class="track"><span class="fill" style="width:${100*r.score/max}%"></span></span></div>
  </article>`;
}

function render(){
  const rows = current();
  const max = Math.max(...DATA.map(r=>r.score), 1);
  $('#count').textContent = `${rows.length} of ${DATA.length} papers`;
  $('#list').innerHTML = rows.length
    ? rows.map((r,i)=>card(r,i,max)).join('')
    : `<p class="empty-msg">Nothing matches those filters.</p>`;
}

$('#q').addEventListener('input', e => { state.q = e.target.value.toLowerCase(); render(); });
$('#sort').addEventListener('change', e => { state.sort = e.target.value; render(); });
if ($('#theme')) $('#theme').addEventListener('change', e => { state.theme = e.target.value; render(); });
document.querySelectorAll('.f').forEach(b => b.addEventListener('click', () => {
  const k = b.dataset.f;
  if (state.filters.has(k)) { state.filters.delete(k); b.setAttribute('aria-pressed','false'); }
  else { state.filters.add(k); b.setAttribute('aria-pressed','true'); }
  render();
}));
populateThemes();
render();
"""

FILTERS = [
    ("video", "has demo video"), ("demo", "hosted demo"), ("code", "code"),
    ("weights", "weights/data"), ("page", "project page"),
    ("venue", "top venue"), ("fresh", "last 3 days"),
]


def render_html(rows, span, n_papers, n_enriched, cfg, words=None):
    esc = html.escape
    keep = ("arxiv_id", "title", "abstract", "blurb", "thumb", "has_video",
            "age_days", "submitted_at", "venue", "venue_tier", "score", "parts",
            "project_url", "code_url", "upvotes", "stars", "theme")
    data = [{k: r.get(k) for k in keep} for r in rows]
    # </script> inside JSON would close the tag early.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    panel = ""
    if words:
        chips = lambda ws: "".join('<span class="w">%s</span>' % esc(w) for w in ws)
        panel = (
            '<details class="taste"><summary>Learned taste — %d liked, %d disliked'
            '</summary><p><b>likes</b><br>%s</p><p><b>dislikes</b><br>%s</p>'
            '</details>' % (words["n_positive"], words["n_negative"],
                            chips(words["positive"]), chips(words["negative"]))
        )

    filters = "".join(
        '<button class="f" data-f="%s" aria-pressed="false">%s</button>' % (k, esc(label))
        for k, label in FILTERS
    )

    return (
        '<title>PaperPulse — top %d</title>\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<style>%s</style>\n'
        '<div class="wrap">\n'
        '  <h1>PaperPulse — top %d</h1>\n'
        '  <p class="sub">%s</p>\n'
        '  <div class="bar">\n'
        '    <input type="search" id="q" placeholder="Search titles and abstracts…">\n'
        '    <select id="sort" aria-label="Sort by">\n'
        '      <option value="score">Sort: score</option>\n'
        '      <option value="date">Sort: newest</option>\n'
        '      <option value="upvotes">Sort: upvotes</option>\n'
        '    </select>\n'
        '    <select id="theme" aria-label="Theme"><option value="">All themes</option></select>\n'
        '  </div>\n'
        '  %s\n'
        '  <div class="filters">%s</div>\n'
        '  <p class="count" id="count"></p>\n'
        '  <div id="list"></div>\n'
        '  <footer>Ranked on artifact footprint — project page, code, venue, '
        'released weights, demo video, hosted demo — from arXiv metadata, '
        'HuggingFace and GitHub. No LLM, no embeddings, no seed corpus.<br>'
        'age_normalize=%s · %s</footer>\n'
        '</div>\n'
        '<script>%s</script>\n' % (
            len(rows), CSS, len(rows),
            esc(_meta_line(span, n_papers, n_enriched)),
            panel, filters,
            cfg["rank"].get("age_normalize"), esc(str(cfg["rank"]["footprint"])),
            JS.replace("__DATA__", payload),
        )
    )
