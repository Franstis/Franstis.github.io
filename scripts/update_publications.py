#!/usr/bin/env python3
"""Fetch the publication list from SciX (NASA ADS) and write _data/publications.json.

Papers are grouped into first-author papers, co-authored papers, accepted and
submitted preprints, and conference proceedings. The Research page is built
from that file.

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
import urllib.parse
import urllib.request

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
FIELDS = "bibcode,title,author,year,pub,volume,page,doi,identifier,doctype,property,comment"
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
    r"\mu": "μ", r"\odot": "⊙",
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


def clean_title(raw):
    text = re.sub(r"<[^>]+>", "", raw)
    for cmd in sorted(LATEX, key=len, reverse=True):
        text = re.sub(re.escape(cmd) + r"(?![A-Za-z])", LATEX[cmd], text)
    text = re.sub(r"[${}]", "", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    return html.escape(text, quote=False)


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
    }


def build(docs, source):
    grouped = {key: [] for key, _ in GROUPS}
    for doc in docs:
        if doc.get("bibcode") in EXCLUDE_BIBCODES:
            continue
        group = group_of(doc)
        if group:
            grouped[group].append(entry(doc, group))
    return {
        "source": source,
        "updated": datetime.date.today().isoformat(),
        "me_name": short_name(f"{MY_SURNAME}, F."),
        "groups": [{"key": k, "title": t, "items": grouped[k]} for k, t in GROUPS],
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

    data = build(docs, args.source)
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
