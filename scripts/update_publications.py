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
import unicodedata
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


# Citation types as SciX splits them (citing paper -> cited paper), in the
# order they are stacked on the chart
# (key, SciX name, legend label, short label for the tooltip)
CITATION_TYPES = [
    ("ref-ref", "refereed to refereed", "Refereed citations to refereed papers", "refereed → refereed"),
    ("nonref-ref", "nonrefereed to refereed", "Non-refereed citations to refereed papers", "non-refereed → refereed"),
    ("ref-nonref", "refereed to nonrefereed", "Refereed citations to non-refereed papers", "refereed → non-refereed"),
    ("nonref-nonref", "nonrefereed to nonrefereed", "Non-refereed citations to non-refereed papers",
     "non-refereed → non-refereed"),
]


def fetch_metrics(token, bibcodes):
    """Citations per year by type ({type: {year: n}}), reads per year
    ({"all"/"refereed": {year: n}}) and the h-index, in one call."""
    metrics = post_ads(METRICS_URL, token, {
        "bibcodes": bibcodes, "types": ["histograms", "indicators"], "histograms": ["citations", "reads"]})
    per_year = lambda h: {int(y): int(n) for y, n in h.items()}
    histogram = metrics["histograms"]["citations"]
    citations = {key: per_year(histogram.get(name, {})) for key, name, _, _ in CITATION_TYPES}
    reads_histogram = metrics["histograms"].get("reads", {})
    reads = {"all": per_year(reads_histogram.get("all reads", {})),
             "refereed": per_year(reads_histogram.get("refereed reads", {}))}
    return citations, reads, metrics["indicators"]["h"]


def author_key(full):
    """'Pérez-Ràfols, Ignasi' -> ('perezrafols', 'ignasi'); first given name or initial."""
    fold = lambda t: re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower())
    surname, _, given = full.partition(",")
    first = given.replace(".", ". ").split()
    return fold(surname), (first[0] if first else "")


def same_person(a, b):
    """Same surname, and the same first name (or the same initial when one is abbreviated)."""
    if a[0] != b[0]:
        return False
    fa, fb = a[1], b[1]
    if not fa or not fb:
        return True
    if fa.endswith(".") or fb.endswith(".") or len(fa) == 1 or len(fb) == 1:
        return fa[0].lower() == fb[0].lower()
    return fa.lower() == fb.lower()


def shares_author(authors_a, authors_b):
    keys_b = {}
    for key in map(author_key, authors_b):
        keys_b.setdefault(key[0], []).append(key)
    return any(same_person(a, b) for a in map(author_key, authors_a) for b in keys_b.get(a[0], []))


def fetch_clean_citations(token, papers):
    """For each refereed paper, the years of the refereed papers citing it,
    leaving out self-citations: citing papers that share any author with it."""
    years, dropped = {}, 0
    for doc in papers:
        citing, start = [], 0
        while True:
            params = urllib.parse.urlencode({
                "q": f'citations(bibcode:"{doc["bibcode"]}") property:refereed',
                "fl": "bibcode,author,year", "rows": 2000, "start": start})
            req = urllib.request.Request(f"{ADS_URL}?{params}", headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.load(resp)["response"]
            citing += body["docs"]
            start += len(body["docs"])
            if not body["docs"] or start >= body["numFound"]:
                break
        own = doc.get("author") or []
        kept = [c for c in citing if not shares_author(c.get("author") or [], own)]
        dropped += len(citing) - len(kept)
        years[doc["bibcode"]] = [int(c["year"]) for c in kept if str(c.get("year") or "").isdigit()]
    print(f"h-index citations: {sum(map(len, years.values()))} kept, {dropped} self-citations left out")
    return years


def h_of(counts):
    counts = sorted(counts, reverse=True)
    return sum(1 for i, n in enumerate(counts) if n >= i + 1)


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


# Papers chart: (key, legend label, short label for the tooltip)
PAPER_TYPES = [
    ("refereed", "Refereed papers", "refereed"),
    ("other", "Non-refereed papers (preprints, proceedings)", "non-refereed"),
]


def year_chart(key, title, unit, note, types, per_year, detail=None, prefix=None):
    """One stacked bar per year; per_year: {year: [count for each of `types`]}.
    detail: line under the title in place of the legend; prefix: tooltip reads
    "2024: <prefix> 5" instead of "2024: 5 <unit>"."""
    this_year = datetime.date.today().year
    years = sorted(per_year)
    # Label every year, or every other one (ending on the latest) when crowded
    label = lambda y: len(years) <= 10 or (this_year - y) % 2 == 0
    top = axis(max(sum(c) for c in per_year.values()))[-1]
    # Only the types with something to show get a colour and a legend entry
    shown = [i for i in range(len(types)) if any(c[i] for c in per_year.values())]
    return {
        "id": key, "title": title, "note": note, "unit": unit, "detail": detail, "prefix": prefix,
        "types": [{"key": types[i][0], "label": types[i][1]} for i in shown],
        "ticks": [{"value": t, "pct": pct(t, top)} for t in axis(top)],
        "years": [{
            "year": y, "label": label(y), "partial": y == this_year,
            "count": sum(per_year[y]), "bar_pct": pct(sum(per_year[y]), top),
            # Top segment first, as the bar is drawn from the top down
            "segments": [{"key": types[i][0], "short": types[i][2], "count": per_year[y][i],
                          "pct": pct(per_year[y][i], top)} for i in reversed(shown) if per_year[y][i]],
        } for y in years],
    }


# Reads chart: same colours as the papers chart
READ_TYPES = [
    ("refereed", "Reads of refereed papers", "refereed"),
    ("other", "Reads of non-refereed papers", "non-refereed"),
]
# h-index chart: one colour, the refereed -> refereed one of the citations chart
H_TYPES = [("ref-ref", "h-index", "h-index")]


def charts(grouped, citations=None, h_index=None, reads=None, cited_years=None):
    """Papers, citations and reads per year, and the h-index over the years,
    for the Publications tab, drawn alike."""
    papers = {}
    for key, items in grouped.items():
        kind = 0 if key in ("first_author", "coauthor") else 1
        for item in items:
            if str(item["year"] or "").isdigit():
                papers.setdefault(int(item["year"]), [0, 0])[kind] += 1
    if not papers:
        return None
    years = range(min(papers), max(max(papers), datetime.date.today().year) + 1)
    total = sum(sum(c) for c in papers.values())
    out = [year_chart("papers", "Papers per year", {"one": "paper", "many": "papers"},
                      f"{total} in total", PAPER_TYPES, {y: papers.get(y, [0, 0]) for y in years})]
    if citations is not None:
        # citations: {type: {year: n}}, types as in CITATION_TYPES
        per_year = {y: [citations[key].get(y, 0) for key, *_ in CITATION_TYPES] for y in years}
        total = sum(sum(c) for c in per_year.values())
        note = f"{total} in total" + (f" · h-index {h_index}" if h_index else "")
        out.append(year_chart("citations", "Citations per year", {"one": "citation", "many": "citations"},
                              note, [(k, label, short) for k, _, label, short in CITATION_TYPES], per_year))
    if reads is not None and any(reads["all"].get(y) for y in years):
        # SciX gives all reads and reads of refereed papers; the rest are non-refereed
        per_year = {y: [reads["refereed"].get(y, 0), max(reads["all"].get(y, 0) - reads["refereed"].get(y, 0), 0)]
                    for y in years}
        total = sum(sum(c) for c in per_year.values())
        out.append(year_chart("reads", "Reads per year", {"one": "read", "many": "reads"},
                              f"{total} in total", READ_TYPES, per_year))
    if cited_years is not None:
        # h-index at the end of each year, from the citations made up to then
        per_year = {y: [h_of([sum(1 for c in cy if c <= y) for cy in cited_years.values()])] for y in years}
        now = per_year[years[-1]][0]
        note = f"{now} now" + (f" · {h_index} counting all citations" if h_index and h_index != now else "")
        out.append(year_chart("h-index", "h-index over the years", {"one": "", "many": ""}, note, H_TYPES,
                              per_year, prefix="h-index",
                              detail="Refereed citations to refereed papers, without self-citations of any author"))
    return out


def build(docs, source, citations=None, h_index=None, bibtex=None, reads=None, cited_years=None):
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
        "charts": charts(grouped, citations, h_index, reads, cited_years),
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
    citations = h_index = bibtex = reads = cited_years = None
    if not args.input:
        listed = [d["bibcode"] for d in docs if d.get("bibcode") and group_of(d)
                  and d["bibcode"] not in EXCLUDE_BIBCODES]
        try:
            citations, reads, h_index = fetch_metrics(token, listed)
        except Exception as err:
            print(f"SciX metrics failed ({err}); citations, reads and h-index are left out.", file=sys.stderr)
        try:
            refereed = [d for d in docs if d.get("bibcode") in listed and group_of(d) in ("first_author", "coauthor")]
            cited_years = fetch_clean_citations(token, refereed)
        except Exception as err:
            print(f"SciX citation lists failed ({err}); the h-index chart is left out.", file=sys.stderr)
        try:
            bibtex = fetch_bibtex(token, listed)
        except Exception as err:
            print(f"SciX BibTeX export failed ({err}); the BibTeX buttons are left out.", file=sys.stderr)
    data = build(docs, args.source, citations, h_index, bibtex, reads, cited_years)
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
