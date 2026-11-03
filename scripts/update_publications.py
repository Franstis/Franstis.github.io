#!/usr/bin/env python3
"""Fetch the publication list from SciX (NASA ADS) and write _data/publications.json.

Papers are grouped into first-author papers, co-authored papers, accepted and
submitted preprints, and conference proceedings. The Research page is built
from that file.

SciX can take a few weeks to add the journal version of a preprint, so
preprints are also checked on arXiv (for "accepted"/"submitted" comments) and
on Crossref (for a journal version with the same title and first author).

The Publications tab also shows papers per year (from this list) and
citations per year (from the SciX metrics API).

Needs a SciX API token in the ADS_TOKEN environment variable
(scixplorer.org → Account → Settings → API Token; an ADS token works too). Without one, the existing data file
is left untouched.

Usage:
  python3 scripts/update_publications.py               # query SciX
  python3 scripts/update_publications.py --input F.json  # use saved SciX docs instead
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

# ----------------------------------------------------------------------------

# SciX is served by the NASA ADS API
ADS_URL = "https://api.adsabs.harvard.edu/v1/search/query"
METRICS_URL = "https://api.adsabs.harvard.edu/v1/metrics"
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


def fetch_citations(token, bibcodes):
    """Year -> citations the listed papers received that year (all citing papers)."""
    body = json.dumps({"bibcodes": bibcodes, "types": ["histograms"],
                       "histograms": ["citations"]}).encode()
    req = urllib.request.Request(METRICS_URL, data=body, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        histogram = json.load(resp)["histograms"]["citations"]
    # Split into refereed/non-refereed citing and cited papers; add them up
    years = {}
    for kind, counts in histogram.items():
        if "normalized" not in kind:
            for year, n in counts.items():
                years[int(year)] = years.get(int(year), 0) + int(n)
    return years


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


def entry(doc, group):
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
        ticks = axis(max(citations.values(), default=0))
        out["citations"] = {
            "total": sum(citations.get(y, 0) for y in years),
            "ticks": [{"value": t, "pct": pct(t, ticks[-1])} for t in ticks],
            "years": [{
                "year": y, "label": label(y), "count": citations.get(y, 0),
                "pct": pct(citations.get(y, 0), ticks[-1]), "partial": y == this_year,
            } for y in years],
        }
    return out


def build(docs, source, citations=None):
    grouped = {key: [] for key, _ in GROUPS}
    recent = []
    for doc in docs:
        if doc.get("bibcode") in EXCLUDE_BIBCODES:
            continue
        group = group_of(doc)
        if group:
            item = entry(doc, group)
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
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="JSON file with a list of ADS docs, used instead of the API")
    parser.add_argument("--source", default="ads", help="label stored in the output (default: ads)")
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
    citations = None
    if not args.input:
        listed = [d["bibcode"] for d in docs if d.get("bibcode") and group_of(d)
                  and d["bibcode"] not in EXCLUDE_BIBCODES]
        try:
            citations = fetch_citations(token, listed)
        except Exception as err:
            print(f"SciX metrics failed ({err}); the citations chart is left out.", file=sys.stderr)
    data = build(docs, args.source, citations)
    if not any(g["items"] for g in data["groups"]):
        print("SciX returned no publications; keeping the existing list.", file=sys.stderr)
        return 1
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    counts = ", ".join(f"{g['key']}={len(g['items'])}" for g in data["groups"])
    print(f"Wrote {OUT_FILE.name}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
