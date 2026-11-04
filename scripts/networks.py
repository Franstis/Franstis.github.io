#!/usr/bin/env python3
"""Turn SciX's author network and paper network into _data/networks.json,
drawn the way SciX draws them (scixplorer.org → Visualizations).

  author network  a sunburst: the author in the middle, the groups SciX finds
                  of people who write together on the inner ring, each
                  co-author on the outer ring (sized by how often they appear),
                  and SciX's links between co-authors as curves through the middle
  paper network   a ring of the groups of papers that cite the same references
                  (sized by number of papers, labelled with SciX's keywords),
                  with bands between groups that share references
Each also gets SciX's "group activity over time" chart (papers per group per
year) and the list of who or what is in each group.

update_publications.py saves SciX's replies in assets/data/ and calls build();
this file can also be run alone to redo everything from the saved replies:

  python3 scripts/networks.py
"""
import datetime
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "assets" / "data"
OUT_FILE = ROOT / "_data" / "networks.json"
ABS_URL = "https://scixplorer.org/abs/{}/abstract"
MAX_GROUPS = 6  # colours net-1 ... net-6
# How to show some of the words SciX gives as group labels
WORDS = {"starforming": "star-forming", "sfr": "SFR", "agn": "AGN", "qso": "QSO", "sdss": "SDSS", "vipers": "VIPERS",
         "lya": "Lyα", "irx": "IRX", "ism": "ISM", "igm": "IGM", "jwst": "JWST"}
STOPWORDS = {"its", "the", "and", "for", "from", "with", "using", "between", "into", "our", "their"}

SIZE = 560          # square drawing, centre in the middle
C = SIZE / 2


def point(r, angle):
    """Angle in radians, clockwise from the top."""
    return round(C + r * math.sin(angle), 2), round(C - r * math.cos(angle), 2)


def sector(r0, r1, a0, a1):
    """SVG path of a ring sector from angle a0 to a1 between radii r0 and r1."""
    if a1 - a0 >= 2 * math.pi - 1e-6:  # a full ring: two halves
        mid = a0 + math.pi
        return sector(r0, r1, a0, mid) + " " + sector(r0, r1, mid, a1)
    large = 1 if a1 - a0 > math.pi else 0
    (x0, y0), (x1, y1) = point(r1, a0), point(r1, a1)
    (x2, y2), (x3, y3) = point(r0, a1), point(r0, a0)
    return (f"M{x0},{y0} A{r1},{r1} 0 {large} 1 {x1},{y1} L{x2},{y2} "
            f"A{r0},{r0} 0 {large} 0 {x3},{y3} Z")


def label(r, angle, size):
    """Text placed outwards along the radius, never upside down."""
    x, y = point(r, angle)
    deg = math.degrees(angle) - 90
    left = math.sin(angle) < 0
    return {"x": x, "y": y, "rotate": round(deg + 180 if left else deg, 2),
            "anchor": "end" if left else "start", "size": size}


def curve(r, a0, a1, bend=0.15):
    """A curve between two points on a circle, pulled towards the centre."""
    (x0, y0), (x1, y1) = point(r, a0), point(r, a1)
    cx, cy = C + ((x0 + x1) / 2 - C) * bend, C + ((y0 + y1) / 2 - C) * bend
    return f"M{x0},{y0} Q{round(cx, 2)},{round(cy, 2)} {x1},{y1}"


def scixname(name):
    """SciX writes 'Pollo, A'; 'Junais,' -> 'Junais'."""
    return name.strip().rstrip(",")


def activity(kind, groups, years_of, labels):
    """SciX's "group activity over time": papers per group per year, as curves.
    years_of: {group key: [year of each paper]}."""
    import update_publications as pubs  # same folder; shares the chart helpers
    this_year = datetime.date.today().year
    every = [y for ys in years_of.values() for y in ys]
    if not every:
        return None
    years = range(min(every), max(max(every), this_year) + 1)
    keys = [g["key"] for g in groups]
    values = {y: [sum(1 for v in years_of.get(k, []) if v == y) for k in keys] for y in years}
    top = pubs.axis(max(max(v) for v in values.values()))[-1]
    n = len(years)
    return {
        "id": f"{kind}-activity", "kind": "line", "title": "Group activity over time",
        "note": "papers published each year", "unit": {"one": "paper", "many": "papers"},
        "detail": None, "prefix": "papers",
        "types": [{"key": f"net-{k}", "label": labels[k]} for k in keys],
        "ticks": [{"value": t, "pct": pubs.pct(t, top)} for t in pubs.axis(top)],
        "lines": [{"key": f"net-{k}", "points": " ".join(
            f"{round(100 * (i + 0.5) / n, 2)},{round(100 - pubs.pct(values[y][j], top), 2)}"
            for i, y in enumerate(years))} for j, k in enumerate(keys)],
        "years": [{
            "year": y, "label": n <= 10 or (this_year - y) % 2 == 0, "partial": y == this_year,
            "count": sum(values[y]), "bar_pct": pubs.pct(max(values[y]), top),
            "segments": [{"key": f"net-{k}", "short": f"group {k}", "count": values[y][j],
                          "pct": pubs.pct(values[y][j], top)} for j, k in enumerate(keys)],
        } for y in years],
    }


def author_network(raw):
    data = raw["data"]
    me = scixname((data["root"].get("name") or [{}])[0].get("nodeName", ""))
    groups = []
    for g in sorted(data["root"]["children"], key=lambda g: -sum(c["size"] for c in g["children"]))[:MAX_GROUPS]:
        members = sorted(g["children"], key=lambda c: -c["size"])
        if members:
            groups.append({"key": len(groups) + 1, "members": members})
    if not groups:
        return None
    total = sum(c["size"] for g in groups for c in g["members"])
    r_hole, r_group, r_author, r_label = 84, 120, 176, 181
    angle, authors, where = 0.0, [], {}
    for g in groups:
        start = angle
        for c in g["members"]:
            span = 2 * math.pi * c["size"] / total
            mid = angle + span / 2
            arc = span * r_label  # room for the name, in pixels
            authors.append({
                "id": c["numberName"], "name": scixname(c["name"]), "group": g["key"],
                "path": sector(r_group + 2, r_author, angle, angle + span),
                "label": label(r_label, mid, round(max(4.5, min(11, arc * 0.85)), 1)),
                "papers": len(c.get("papers") or []), "citations": c.get("citation_count"),
                "reads": c.get("read_count"),
            })
            where[c["numberName"]] = mid
            angle += span
        g["path"] = sector(r_hole, r_group, start, angle)
    weights = [w for a, b, w in data.get("link_data", []) if a in where and b in where]
    top = max(weights, default=1)
    links = [{"a": a, "b": b, "path": curve(r_hole - 2, where[a], where[b]),
              "w": round(0.5 + 2.5 * w / top, 2)}
             for a, b, w in data.get("link_data", []) if a in where and b in where and a != b]
    labels = {g["key"]: ", ".join(scixname(c["name"]).split(",")[0] for c in g["members"][:2])
              + (" and others" if len(g["members"]) > 2 else "") for g in groups}
    papers_of = {g["key"]: {b for c in g["members"] for b in c.get("papers") or []} for g in groups}
    return {
        "size": SIZE, "view": f"0 0 {SIZE} {SIZE}", "centre": C, "hole": r_hole - 2, "me": me,
        "groups": [{"key": g["key"], "label": labels[g["key"]], "path": g["path"], "count": len(g["members"]),
                    "papers": len(papers_of[g["key"]]),
                    "members": [{"name": scixname(c["name"]), "papers": len(c.get("papers") or [])}
                                for c in g["members"]]} for g in groups],
        "authors": authors, "links": links, "count": len(authors),
        "activity": activity("author", groups, {k: [int(b[:4]) for b in v if b[:4].isdigit()]
                                      for k, v in papers_of.items()}, labels),
    }


def paper_network(raw):
    data = raw["data"]
    summary = sorted(data["summaryGraph"]["nodes"], key=lambda n: -n["paper_count"])[:MAX_GROUPS]
    if not summary:
        return None
    # Papers name their group by the summary node's id
    key_of = {n["id"]: i + 1 for i, n in enumerate(summary)}
    total = sum(n["paper_count"] for n in summary)
    r_inner, r_outer, r_label = 112, 168, 178
    angle, groups, mid_of = 0.0, [], {}
    for n in summary:
        span = 2 * math.pi * n["paper_count"] / total
        mid = angle + span / 2
        words = [WORDS.get(w.lower(), w) for w in n.get("node_label") or {} if w.lower() not in STOPWORDS][:5]
        key = key_of[n["id"]]
        # Words stacked on lines next to the ring, as SciX shows them
        x, y = point(r_label, mid)
        right = math.sin(mid) >= 0
        groups.append({
            "key": key, "path": sector(r_inner, r_outer, angle, angle + span),
            "words": words, "label": " · ".join(words[:4]) or f"Group {key}",
            "text": {"x": x, "y": round(y - (len(words) - 1) * 7.5, 2), "anchor": "start" if right else "end"},
            "papers": n["paper_count"], "citations": n.get("total_citations"), "reads": n.get("total_reads"),
            "list": [],
        })
        mid_of[n["id"]] = mid
        angle += span
    links = [l for l in data["summaryGraph"]["links"]
             if l["source"] != l["target"] and l["source"] in mid_of and l["target"] in mid_of]
    top = max((l["weight"] for l in links), default=1)
    bands = [{"a": key_of[l["source"]], "b": key_of[l["target"]], "weight": l["weight"],
              "path": curve(r_inner - 2, mid_of[l["source"]], mid_of[l["target"]], bend=0.3),
              "w": round(4 + 22 * l["weight"] / top, 1)} for l in links]
    by_key = {g["key"]: g for g in groups}
    years_of = {}
    for p in data["fullGraph"]["nodes"]:
        if p.get("group") not in key_of:
            continue
        key, bibcode = key_of[p["group"]], p["node_name"]
        by_key[key]["list"].append({"title": p.get("title") or bibcode, "author": p.get("first_author") or "",
                                    "year": bibcode[:4], "citations": p.get("citation_count") or 0,
                                    "url": ABS_URL.format(bibcode)})
        if bibcode[:4].isdigit():
            years_of.setdefault(key, []).append(int(bibcode[:4]))
    for g in groups:
        g["list"].sort(key=lambda p: (p["year"], p["title"]), reverse=True)
    return {
        # Only the ring and its words: no room needed above and below
        "size": SIZE, "view": f"0 {round(C - r_outer - 22)} {SIZE} {2 * (r_outer + 22)}",
        "groups": groups, "bands": bands, "count": total,
        "activity": activity("paper", groups, years_of, {g["key"]: g["label"] for g in groups}),
    }


def build(author_raw=None, paper_raw=None):
    """Both networks from SciX's replies (read from assets/data/ when not given)."""
    out = {"updated": datetime.date.today().isoformat()}
    for kind, make, given in (("author", author_network, author_raw), ("paper", paper_network, paper_raw)):
        path = RAW_DIR / f"scix-{kind}-network.json"
        raw = given if given is not None else (json.loads(path.read_text()) if path.exists() else None)
        try:
            out[kind] = make(raw) if raw else None
        except (KeyError, TypeError, ValueError) as err:
            print(f"::warning::SciX {kind} network could not be read ({err!r}); it is left out.")
            out[kind] = None
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"Wrote {OUT_FILE.name}: " + ", ".join(
        f"{k} network ({out[k]['count']} {'co-authors' if k == 'author' else 'papers'})" if out[k] else f"no {k} network"
        for k in ("author", "paper")))
    return out


if __name__ == "__main__":
    build()
