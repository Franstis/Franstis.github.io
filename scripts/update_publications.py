#!/usr/bin/env python3
"""Fetch the publication list from SciX (NASA ADS) and write _data/publications.json.

Papers are grouped into first-author papers, co-authored papers, accepted and
submitted preprints, and conference proceedings. The Research page is built
from that file.

SciX can take a few weeks to add the journal version of a preprint, so
preprints are also checked on arXiv (for "accepted"/"submitted" comments) and
on Crossref (for a journal version with the same title and first author).

The Publications tab also shows papers per year (from this list), citations
per year and the h-index (from the SciX metrics API), and offers each paper's
BibTeX entry (from the SciX export API).

With --tex, the same papers are also written as the Publications section of the
LaTeX CV (publications.tex in the Overleaf repository), in the \\pubentry style
of its main.tex. Papers SciX does not list yet can be kept by hand in
publications-extra.tex next to it; they are added to their group, and taken
out of that file once SciX lists a paper with the same title.

Needs a SciX API token in the ADS_TOKEN environment variable
(scixplorer.org → Account → Settings → API Token; an ADS token works too). Without one, the existing data file
is left untouched.

Usage:
  python3 scripts/update_publications.py               # query SciX
  python3 scripts/update_publications.py --input F.json  # use saved SciX docs instead
  python3 scripts/update_publications.py --tex _cv/publications.tex  # also write the CV list
"""
import argparse
import datetime
import html
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# --- Settings ---------------------------------------------------------------

# Papers linked to the ORCID, plus papers that list the author by name.
# Claiming papers in SciX (scixplorer.org → ORCID) makes the first part complete.
ADS_QUERY = 'orcid:0000-0003-1189-2617 OR author:"Pistis, F"'
MY_SURNAME = "Pistis"

# Bibcodes to leave out (wrong matches, duplicates you don't want listed).
EXCLUDE_BIBCODES = set()

# Include conference abstracts (e.g. EAS meeting abstracts) under proceedings.
INCLUDE_ABSTRACTS = False

# Authors shown before "et al."
MAX_AUTHORS = 5

# Authors listed in the CV before "et al." (the CV lists surnames only)
MAX_TEX_AUTHORS = 30

# ----------------------------------------------------------------------------

# SciX is served by the NASA ADS API
ADS_URL = "https://api.adsabs.harvard.edu/v1/search/query"
METRICS_URL = "https://api.adsabs.harvard.edu/v1/metrics"
EXPORT_URL = "https://api.adsabs.harvard.edu/v1/export/bibtex"
ARXIV_URL = "https://export.arxiv.org/api/query"
CROSSREF_URL = "https://api.crossref.org/works"
USER_AGENT = "publication-list/1.0 (+https://franstis.github.io)"
FIELDS = "bibcode,title,author,year,pub,volume,page,doi,identifier,doctype,property,comment,abstract"
OUT_FILE = pathlib.Path(__file__).resolve().parent.parent / "_data" / "publications.json"

JOURNALS = {
    "astronomy and astrophysics": "A&A",
    "astronomy & astrophysics": "A&A",
    "monthly notices of the royal astronomical society": "MNRAS",
    "the astrophysical journal": "ApJ",
    "the astrophysical journal letters": "ApJL",
    "the astrophysical journal supplement series": "ApJS",
    "the astronomical journal": "AJ",
    "publications of the astronomical society of the pacific": "PASP",
    "nature astronomy": "Nature Astronomy",
}

LATEX = {
    r"\sim": "∼", r"\simeq": "≃", r"\approx": "≈",
    r"\geq": "≥", r"\ge": "≥", r"\leq": "≤", r"\le": "≤",
    r"\lt": "<", r"\gt": ">", r"\times": "×", r"\pm": "±",
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\Lambda": "Λ",
    r"\mu": "μ", r"\odot": "⊙", r"\AA": "Å", r"\rm": "", r"\mathrm": "",
}

# Subsections of the CV's Publications section
TEX_GROUPS = [
    ("published", "Refereed --- Published"),
    ("accepted", "Refereed --- Accepted"),
    ("submitted", "Refereed --- Submitted"),
    ("proceedings", "Proceedings"),
]

GROUPS = [
    ("first_author", "First-author papers"),
    ("coauthor", "Co-authored papers"),
    ("preprints", "Accepted and submitted"),
    ("proceedings", "Conference proceedings"),
]


def fetch_ads(token):
    docs, start = [], 0
    while True:
        params = urllib.parse.urlencode({
            "q": ADS_QUERY, "fl": FIELDS, "rows": 200, "start": start,
            "sort": "date desc, bibcode desc",
        })
        req = urllib.request.Request(f"{ADS_URL}?{params}",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.load(resp)["response"]
        docs += body["docs"]
        start += len(body["docs"])
        if not body["docs"] or start >= body["numFound"]:
            return docs


def post_ads(url, token, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def citations_per_year(token, bibcodes):
    """Year -> citations the papers received that year (from any citing paper)."""
    if not bibcodes:
        return {}
    histogram = post_ads(METRICS_URL, token, {
        "bibcodes": bibcodes, "types": ["histograms"], "histograms": ["citations"]})["histograms"]["citations"]
    # Split into refereed/non-refereed citing and cited papers; add them up
    years = {}
    for kind, counts in histogram.items():
        if "normalized" not in kind:
            for year, n in counts.items():
                years[int(year)] = years.get(int(year), 0) + int(n)
    return years


def fetch_metrics(token, docs):
    """Citations per year to refereed papers and to the others, and the h-index."""
    refereed = [d["bibcode"] for d in docs if group_of(d) in ("first_author", "coauthor")]
    other = [d["bibcode"] for d in docs if group_of(d) not in ("first_author", "coauthor")]
    h_index = post_ads(METRICS_URL, token, {
        "bibcodes": refereed + other, "types": ["indicators"]})["indicators"]["h"]
    citations = {"refereed": citations_per_year(token, refereed),
                 "other": citations_per_year(token, other)}
    return citations, h_index


def fetch_bibtex(token, bibcodes):
    """Bibcode -> BibTeX entry, as SciX exports it."""
    export = post_ads(EXPORT_URL, token, {"bibcode": bibcodes})["export"]
    entries = {}
    for item in re.split(r"\n(?=@)", export.strip()):
        m = re.match(r"@\w+\{([^,]+),", item)
        if m:
            entries[m.group(1).strip()] = item.strip()
    return entries


def get(url, params, tries=3):
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": USER_AGENT})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            # Crossref and arXiv ask clients to slow down with 429/503
            if err.code not in (429, 503) or attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def arxiv_comments(ids):
    """arXiv id -> author comment ("13 pages, 9 figures. Accepted in A&A")."""
    if not ids:
        return {}
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    feed = ET.fromstring(get(ARXIV_URL, {"id_list": ",".join(ids), "max_results": len(ids)}))
    comments = {}
    for item in feed.findall("a:entry", ns):
        m = re.search(r"(\d{4}\.\d{4,5})", item.findtext("a:id", "", ns))
        if m:
            comments[m.group(1)] = item.findtext("arxiv:comment", "", ns)
    return comments


def letters(text):
    return re.sub(r"[^a-z0-9]", "", re.sub(r"<[^>]+>|\$[^$]*\$", "", text.lower()))


def journal_version(doc):
    """Look on Crossref for a journal article with the same title and first author."""
    title = (doc.get("title") or [""])[0]
    first = (doc.get("author") or [""])[0].split(",")[0].strip()
    found = json.loads(get(CROSSREF_URL, {
        "query.bibliographic": title, "query.author": first,
        "filter": "type:journal-article", "rows": 5,
        "select": "DOI,title,subtitle,author,container-title,volume,page,article-number,published",
    }))["message"]["items"]
    for item in found:
        full = " ".join((item.get("title") or []) + (item.get("subtitle") or []))
        family = ((item.get("author") or [{}])[0].get("family") or "")
        if letters(full) == letters(title) and family.lower() == first.lower():
            return item
    return None


def update_preprints(docs):
    """Fill in what SciX does not have yet for preprints; network errors are skipped."""
    preprints = [d for d in docs if group_of(d) == "preprints"]
    try:
        comments = arxiv_comments([i for i in map(arxiv_id, preprints) if i])
    except Exception as err:
        print(f"arXiv lookup failed ({err}); preprints keep the SciX comment.", file=sys.stderr)
        comments = {}
    for doc in preprints:
        if comments.get(arxiv_id(doc)):
            doc["comment"] = [comments[arxiv_id(doc)]]
        try:
            item = journal_version(doc)
        except Exception as err:
            print(f"Crossref lookup failed for {doc.get('bibcode')} ({err}).", file=sys.stderr)
            continue
        if not item:
            continue
        print(f"Journal version on Crossref: {doc.get('bibcode')} -> {item['DOI']}")
        doc.update({
            "doctype": "article",
            "property": (doc.get("property") or []) + ["REFEREED"],
            "pub": html.unescape((item.get("container-title") or [""])[0]),
            "volume": item.get("volume"),
            # Advance-access papers have an article code but no volume yet
            "page": [item.get("article-number") or item.get("page")] if item.get("volume") else [],
            "doi": [item["DOI"]],
        })
        year = (item.get("published") or {}).get("date-parts", [[None]])[0][0]
        if year:
            doc["year"] = str(year)


def title_key(doc):
    """Title reduced to letters and digits, so versions of a paper compare equal."""
    return letters(clean_text(html.unescape((doc.get("title") or [""])[0])))


def drop_duplicates(docs):
    """Leave out preprints SciX still lists next to their journal version."""
    published = {title_key(d) for d in docs if group_of(d) in ("first_author", "coauthor")}
    kept = []
    for doc in docs:
        if group_of(doc) == "preprints" and title_key(doc) in published:
            print(f"Leaving out {doc.get('bibcode')}: the journal version is listed")
            continue
        kept.append(doc)
    return kept


def clean_text(raw):
    text = re.sub(r"<[^>]+>", "", raw)
    for cmd in sorted(LATEX, key=len, reverse=True):
        text = re.sub(re.escape(cmd) + r"(?![A-Za-z])", LATEX[cmd], text)
    text = re.sub(r"[${}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_title(raw):
    return html.escape(clean_text(raw).rstrip("."), quote=False)


def clean_abstract(raw):
    """Abstract as safe HTML: LaTeX turned into characters, ^ and _ into <sup>/<sub>."""
    text = re.sub(r"(?i)<sub>(.*?)</sub>", r"_{\1}", raw)
    text = re.sub(r"(?i)<sup>(.*?)</sup>", r"^{\1}", text)
    text = re.sub(r"<[^>]+>", "", text)
    # Formatting commands go, their content stays (\text{O}, \mathrm{A}, \tiny ...)
    text = re.sub(r"\\(?:text|textrm|textit|textbf|texttt|textup|mathrm|mathit|mathbf|mbox|emph|rm|it|bf|sc|tiny|small|left|right)(?![A-Za-z])\s*", "", text)
    while re.search(r"\{\s*\{([^{}]*)\}\s*\}|\{\}", text):
        text = re.sub(r"\{\s*\{([^{}]*)\}\s*\}", r"{\1}", text).replace("{}", "")
    text = re.sub(r"\\(?:mathring|r)\s*\{?\s*A\s*\}?|\\AA(?![A-Za-z])", "Å", text)
    text = text.replace("\\%", "%").replace("\\&", "&").replace("\\_", "_")
    text = re.sub(r"\\[,;:! ]", " ", text)            # LaTeX spacing commands
    text = re.sub(r"~(?=[^\W\d_]|\\)", " ", text)      # non-breaking space before a word or unit
    text = text.replace("~", "∼")                      # otherwise "approximately"
    for cmd in sorted(LATEX, key=len, reverse=True):
        text = re.sub(re.escape(cmd) + r"(?![A-Za-z])", LATEX[cmd], text)
    text = html.escape(text.replace("$", ""), quote=False)
    for mark, tag in (("^", "sup"), ("_", "sub")):
        text = re.sub(re.escape(mark) + r"(?:\{([^{}]*)\}|([\w.+−-]))",
                      lambda m, tag=tag: f"<{tag}>{m.group(1) if m.group(1) is not None else m.group(2)}</{tag}>", text)
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"\\(?=[A-Za-z])", "", text)          # any command left: keep its name as text
    return re.sub(r"\s+", " ", text).strip()


def short_name(full):
    """'Pérez-Ràfols, Ignasi' -> 'Pérez-Ràfols I.'"""
    surname, _, given = full.partition(",")
    initials = []
    for part in given.replace(".", " ").split():
        initials.append("-".join(p[0] + "." for p in part.split("-") if p))
    return " ".join([surname.strip()] + initials)


def is_me(full):
    return full.split(",")[0].strip().lower() == MY_SURNAME.lower()


def venue(doc):
    pub = (doc.get("pub") or "").strip()
    return JOURNALS.get(pub.lower(), pub)


def arxiv_id(doc):
    for ident in doc.get("identifier") or []:
        m = re.match(r"^(?:arXiv:)?(\d{4}\.\d{4,5})$", ident)
        if m:
            return m.group(1)
    return None


def status(doc):
    comment = " ".join(doc.get("comment") or []).lower()
    if "accepted" in comment or "in press" in comment:
        return "Accepted"
    if "submitted" in comment:
        return "Submitted"
    return "Preprint"


def group_of(doc):
    doctype = doc.get("doctype")
    refereed = "REFEREED" in (doc.get("property") or [])
    if doctype == "article" and refereed:
        return "first_author" if is_me((doc.get("author") or [""])[0]) else "coauthor"
    if doctype in ("eprint", "article"):
        return "preprints"
    if doctype == "inproceedings" or (INCLUDE_ABSTRACTS and doctype == "abstract"):
        return "proceedings"
    return None  # catalogues, software, theses, errata...


def entry(doc, group, bibtex):
    authors = doc.get("author") or []
    shown = authors[:MAX_AUTHORS]
    me_hidden = not any(is_me(a) for a in shown) and any(is_me(a) for a in authors)
    page = (doc.get("page") or [None])[0]
    ref = ", ".join(str(x) for x in (doc.get("volume"), page) if x)
    preprint = group == "preprints"
    return {
        "title": clean_title((doc.get("title") or ["Untitled"])[0]),
        "authors": [{"name": short_name(a), "me": is_me(a)} for a in shown],
        "et_al": len(authors) > MAX_AUTHORS,
        "me_hidden": me_hidden,
        "year": doc.get("year"),
        "venue": "" if preprint and venue(doc).lower() == "arxiv e-prints" else venue(doc),
        "ref": "" if preprint else ref,
        "status": status(doc) if preprint else "",
        "doi": (doc.get("doi") or [None])[0],
        "arxiv": arxiv_id(doc),
        "ads": f"https://scixplorer.org/abs/{doc['bibcode']}/abstract" if doc.get("bibcode") else None,
        "abstract": clean_abstract(doc["abstract"]) if doc.get("abstract") else None,
        "bibtex": bibtex.get(doc.get("bibcode")),
    }


def axis(top):
    """Round tick values from 0 to at least `top` (at most 4 steps)."""
    step = 1
    while step * 4 < top:
        step = step * 5 // 2 if str(step)[0] == "2" else step * 2
    ticks = [0]
    while ticks[-1] < max(top, 1):
        ticks.append(ticks[-1] + step)
    return ticks


def pct(n, top):
    return round(100 * n / top, 1)


def charts(grouped, citations):
    """Per-year bars for the Publications tab, scaled to their axis."""
    this_year = datetime.date.today().year
    papers = {}
    for key, items in grouped.items():
        kind = "refereed" if key in ("first_author", "coauthor") else "other"
        for item in items:
            if str(item["year"] or "").isdigit():
                counts = papers.setdefault(int(item["year"]), {"refereed": 0, "other": 0})
                counts[kind] += 1
    if not papers:
        return None
    years = range(min(papers), max(max(papers), this_year) + 1)
    # Label every year, or every other one (ending on the latest) when crowded
    label = lambda y: len(years) <= 10 or (this_year - y) % 2 == 0

    zero = {"refereed": 0, "other": 0}
    ticks = axis(max(sum(c.values()) for c in papers.values()))
    out = {"papers": {
        "total": sum(sum(c.values()) for c in papers.values()),
        "ticks": [{"value": t, "pct": pct(t, ticks[-1])} for t in ticks],
        "years": [{
            "year": y, "label": label(y), **papers.get(y, zero),
            "refereed_pct": pct(papers.get(y, zero)["refereed"], ticks[-1]),
            "other_pct": pct(papers.get(y, zero)["other"], ticks[-1]),
        } for y in years],
    }}
    if citations is not None:
        # citations: {"refereed": {year: n}, "other": {year: n}}
        per_year = {y: {k: citations[k].get(y, 0) for k in ("refereed", "other")} for y in years}
        ticks = axis(max(sum(c.values()) for c in per_year.values()))
        out["citations"] = {
            "total": sum(sum(c.values()) for c in per_year.values()),
            "ticks": [{"value": t, "pct": pct(t, ticks[-1])} for t in ticks],
            "years": [{
                "year": y, "label": label(y), **per_year[y], "count": sum(per_year[y].values()),
                "refereed_pct": pct(per_year[y]["refereed"], ticks[-1]),
                "other_pct": pct(per_year[y]["other"], ticks[-1]), "partial": y == this_year,
            } for y in years],
        }
    return out


def build(docs, source, citations=None, h_index=None, bibtex=None):
    grouped = {key: [] for key, _ in GROUPS}
    recent = []
    for doc in docs:
        if doc.get("bibcode") in EXCLUDE_BIBCODES:
            continue
        group = group_of(doc)
        if group:
            item = entry(doc, group, bibtex or {})
            grouped[group].append(item)
            # Only papers people can open (skips proceedings and unpublished drafts)
            if group != "proceedings" and (item["doi"] or item["arxiv"]):
                recent.append(item)
    # Newest papers for the home page (sorted() keeps the SciX order within a year)
    recent = sorted(recent, key=lambda i: str(i["year"] or ""), reverse=True)[:3]
    return {
        "source": source,
        "updated": datetime.date.today().isoformat(),
        "me_name": short_name(f"{MY_SURNAME}, F."),
        "groups": [{"key": k, "title": t, "items": grouped[k]} for k, t in GROUPS],
        "latest": recent,
        "charts": charts(grouped, citations),
        "h_index": h_index,
    }


# --- The CV's Publications section (publications.tex) -------------------------

# Characters pdfLaTeX cannot print as text (SciX writes e.g. "Ly&alpha;")
TEX_SYMBOLS = {
    "α": r"$\alpha$", "β": r"$\beta$", "γ": r"$\gamma$", "δ": r"$\delta$", "ε": r"$\epsilon$",
    "η": r"$\eta$", "θ": r"$\theta$", "κ": r"$\kappa$", "λ": r"$\lambda$", "μ": r"$\mu$",
    "ν": r"$\nu$", "π": r"$\pi$", "ρ": r"$\rho$", "σ": r"$\sigma$", "τ": r"$\tau$",
    "φ": r"$\phi$", "χ": r"$\chi$", "ψ": r"$\psi$", "ω": r"$\omega$", "Λ": r"$\Lambda$",
    "Ω": r"$\Omega$", "Δ": r"$\Delta$", "∼": r"$\sim$", "≃": r"$\simeq$", "≈": r"$\approx$",
    "≥": r"$\geq$", "≤": r"$\leq$", "×": r"$\times$", "±": r"$\pm$", "−": "-", "⊙": r"$\odot$",
    "∝": r"$\propto$", "→": r"$\rightarrow$", "°": r"$^\circ$", "Å": r"\AA{}", "\u2009": " ",
    "\u00a0": "~", "\u200b": "",
}


def tex_text(text):
    """Text from SciX -> LaTeX: special characters escaped, $...$ math kept."""
    text = html.unescape(text)
    # Inside $...$ a symbol needs no extra $ signs
    text = re.sub(r"\$[^$]*\$", lambda m: "".join(
        TEX_SYMBOLS.get(c, c).strip("$") if c in TEX_SYMBOLS else c for c in m.group(0)), text)
    text = "".join(TEX_SYMBOLS.get(c, c) for c in text) if "$" not in text else re.sub(
        r"(\$[^$]*\$)|([^$]+)", lambda m: m.group(1) or "".join(TEX_SYMBOLS.get(c, c) for c in m.group(2)), text)
    text = re.sub(r"(?i)<sub>(.*?)</sub>", r"$_{\1}$", text)
    text = re.sub(r"(?i)<sup>(.*?)</sup>", r"$^{\1}$", text)
    text = re.sub(r"<[^>]+>", "", text)
    parts = re.split(r"(\$[^$]*\$)", text)
    return "".join(p if p.startswith("$") and len(p) > 1
                   else re.sub(r"(?<!\\)([&%#_])", r"\\\1", p).replace("~", "$\\sim$")
                   for p in parts)


def tex_url(url):
    return url.replace("&", "%26").replace("%", "\\%").replace("#", "\\#")


def tex_group(doc):
    group = group_of(doc)
    if group in ("first_author", "coauthor"):
        return "published"
    if group == "preprints":
        return "accepted" if status(doc) == "Accepted" else "submitted"
    return group


def tex_entry(doc, group):
    names = [a.split(",")[0].strip() for a in doc.get("author") or []]
    shown = [f"\\textbf{{{tex_text(n)}}}" if n.lower() == MY_SURNAME.lower() else tex_text(n)
             for n in names[:MAX_TEX_AUTHORS]]
    authors = ", ".join(shown)
    if len(names) > MAX_TEX_AUTHORS:
        authors += ", et al." + ("" if any(MY_SURNAME in n for n in names[:MAX_TEX_AUTHORS])
                                 else f" (incl.\\ \\textbf{{{MY_SURNAME}}})")
    title = tex_text(clean_text_keep_math((doc.get("title") or ["Untitled"])[0]))
    doi, arxiv, bibcode = (doc.get("doi") or [None])[0], arxiv_id(doc), doc.get("bibcode") or ""
    if group == "published" and doi:
        link = f"\\href{{https://doi.org/{tex_url(doi)}}}{{{tex_text(venue(doc))}, {tex_text(doi)}}}"
    elif group in ("accepted", "submitted"):
        link = f"arXiv:\\href{{https://arxiv.org/abs/{arxiv}}}{{{arxiv}}}" if arxiv else group
    else:
        link = f"\\href{{https://scixplorer.org/abs/{tex_url(bibcode)}/abstract}}{{{tex_text(bibcode)}}}"
    label = doc.get("year") if group in ("published", "proceedings") else group.capitalize()
    return (f"\\pubentry{{{label}}}{{%\n    {authors}\\newline\n    \\textit{{{title}}}%\n}}{{{link}}}")


def clean_text_keep_math(raw):
    """SciX title with whitespace tidied; LaTeX math left for the CV."""
    return re.sub(r"\s+", " ", raw).strip().rstrip(".")


def extra_entries(raw):
    """Hand-written \\pubentry blocks of publications-extra.tex, by group.

    Each entry is (title key, its LaTeX as written, start, end in `raw`).
    """
    import update_cv  # same folder; reads LaTeX arguments
    # Comments blanked out with spaces, so positions still match `raw`
    tex = re.sub(r"(?<!\\)%.*", lambda m: " " * len(m.group(0)), raw)
    found, group, pos = {}, "submitted", 0
    pattern = re.compile(r"\\(subsection|pubentry)\*?(?![A-Za-z])")
    while (m := pattern.search(tex, pos)):
        count = 1 if m.group(1) == "subsection" else 3
        args, pos = update_cv.read_args(tex, m.end(), count)
        if m.group(1) == "subsection":
            name = args[0].lower()
            group = next((key for key, _ in TEX_GROUPS if key in name), group)
            continue
        italic = re.search(r"\\textit\s*\{", args[1])
        title = ""
        if italic:
            inner, _ = update_cv.read_group(args[1], italic.end() - 1)
            title = update_cv.plain(update_cv.to_html(inner))
        found.setdefault(group, []).append((letters(title), raw[m.start():pos], m.start(), pos))
    return found


def write_tex(docs, path):
    listed = [d for d in docs if d.get("bibcode") not in EXCLUDE_BIBCODES and tex_group(d)]
    listed.sort(key=lambda d: str(d.get("year") or ""), reverse=True)  # keeps SciX order within a year
    known = {title_key(d) for d in listed}
    extra_path = path.parent / "publications-extra.tex"
    extra_raw = extra_path.read_text(encoding="utf-8") if extra_path.exists() else ""
    extras = extra_entries(extra_raw)
    lines = [
        "% Publications, written from SciX by scripts/update_publications.py",
        "% (website repository Franstis/Franstis.github.io). Do not edit: this file is",
        "% replaced at every website build. Papers SciX does not list yet go in",
        "% publications-extra.tex.",
    ]
    for key, title in TEX_GROUPS:
        entries = [tex_entry(d, key) for d in listed if tex_group(d) == key]
        entries += [text.strip() for name, text, _, _ in extras.get(key, []) if name not in known]
        if entries:
            lines += ["", f"\\subsection{{{title}}}"]
            for e in entries:
                lines += ["", e]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}")

    # Extra papers SciX now lists are taken out of publications-extra.tex
    listed_now = sorted((e for group in extras.values() for e in group if e[0] in known),
                        key=lambda e: e[2], reverse=True)
    for name, text, start, end in listed_now:
        end += len(extra_raw[end:]) - len(extra_raw[end:].lstrip())  # and the blank lines after it
        extra_raw = extra_raw[:start] + extra_raw[end:]
        print(f"SciX lists it now, removed from {extra_path.name}: {text.split(chr(92) + 'textit', 1)[-1][:70]!r}")
    if listed_now:
        extra_path.write_text(extra_raw, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="JSON file with a list of ADS docs, used instead of the API")
    parser.add_argument("--source", default="ads", help="label stored in the output (default: ads)")
    parser.add_argument("--tex", help="also write the CV's Publications section to this .tex file")
    args = parser.parse_args()

    if args.input:
        docs = json.loads(pathlib.Path(args.input).read_text())
    else:
        token = os.environ.get("ADS_TOKEN")
        if not token:
            print("ADS_TOKEN is not set; keeping the existing publication list.")
            return 0
        docs = fetch_ads(token)

    update_preprints(docs)
    docs = drop_duplicates(docs)
    citations = h_index = bibtex = None
    if not args.input:
        listed_docs = [d for d in docs if d.get("bibcode") and group_of(d)
                       and d["bibcode"] not in EXCLUDE_BIBCODES]
        listed = [d["bibcode"] for d in listed_docs]
        try:
            citations, h_index = fetch_metrics(token, listed_docs)
        except Exception as err:
            print(f"SciX metrics failed ({err}); citations and h-index are left out.", file=sys.stderr)
        try:
            bibtex = fetch_bibtex(token, listed)
        except Exception as err:
            print(f"SciX BibTeX export failed ({err}); the BibTeX buttons are left out.", file=sys.stderr)
    data = build(docs, args.source, citations, h_index, bibtex)
    if not any(g["items"] for g in data["groups"]):
        print("SciX returned no publications; keeping the existing list.", file=sys.stderr)
        return 1
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    counts = ", ".join(f"{g['key']}={len(g['items'])}" for g in data["groups"])
    print(f"Wrote {OUT_FILE.name}: {counts}")
    if args.tex:
        write_tex(docs, pathlib.Path(args.tex))
    return 0


if __name__ == "__main__":
    sys.exit(main())
