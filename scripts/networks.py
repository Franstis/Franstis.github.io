#!/usr/bin/env python3
"""Turn SciX's author network and paper network into _data/networks.json.

SciX (scixplorer.org) computes both networks for a list of papers:
  author network  co-authors, in groups of people who often write together;
                  links join authors who share papers
  paper network   papers, in groups that cite the same references; links
                  join papers by the number of references they share
update_publications.py saves SciX's replies in assets/data/ and calls build();
this file can also be run alone to redo the layout from the saved replies:

  python3 scripts/networks.py
"""
import datetime
import json
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from network_layout import layout  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "assets" / "data"
OUT_FILE = ROOT / "_data" / "networks.json"
ABS_URL = "https://scixplorer.org/abs/{}/abstract"
# Group colours, in order (CSS classes net-1, net-2...)
MAX_GROUPS = 6
# Words SciX may use as group labels that say nothing about the topic
# How to show some of the words SciX gives
WORDS = {"starforming": "star-forming", "sfr": "SFR", "agn": "AGN", "qso": "QSO", "sdss": "SDSS", "vipers": "VIPERS",
         "lya": "Lyα", "irx": "IRX", "ism": "ISM", "igm": "IGM", "jwst": "JWST"}
STOPWORDS = {"its", "the", "and", "for", "from", "with", "using", "between", "into", "our", "their",
             "new", "study", "data", "based", "first", "two", "via", "role", "impact", "effect", "effects"}


def short(name):
    """'Pollo, A' -> 'Pollo A.'; 'Junais,' -> 'Junais'."""
    surname, _, given = name.partition(",")
    initials = "".join(p[0] + "." for p in re.split(r"[\s.]+", given) if p)
    return f"{surname.strip()} {initials}".strip()


def scale_r(value, low, high, r_min, r_max):
    """Circle radius with area proportional to value."""
    if high <= low:
        return (r_min + r_max) / 2
    t = (math.sqrt(value) - math.sqrt(low)) / (math.sqrt(high) - math.sqrt(low))
    return round(r_min + t * (r_max - r_min), 1)


def draw(nodes, links, width, height, pin=None, seed=1, anchors=None, keep=None):
    """Add x, y to nodes and line ends to links; drop links to missing nodes.
    keep: draw only each node's `keep` heaviest links (all are used for the layout)."""
    pos = layout(nodes, links, width, height, seed=seed, pin=pin, anchors=anchors)
    if keep:
        best = set()
        for n in nodes:
            own = sorted((l for l in links if n["id"] in (l["source"], l["target"])), key=lambda l: -l["weight"])
            best.update(id(l) for l in own[:keep])
        links = [l for l in links if id(l) in best]
    for n in nodes:
        n["x"], n["y"] = pos[n["id"]]
    top = max((l["weight"] for l in links if l["source"] != pin), default=1)
    drawn = []
    for l in links:
        if l["source"] in pos and l["target"] in pos and l["source"] != l["target"]:
            (x1, y1), (x2, y2) = pos[l["source"]], pos[l["target"]]
            drawn.append({**l, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                          "w": round(0.6 + 2.6 * min(l["weight"] / top, 1), 2),
                          "o": round(0.25 + 0.6 * min(l["weight"] / top, 1), 2)})
    return nodes, drawn


def author_network(raw, width=640, height=440):
    data = raw["data"]
    me = (data["root"].get("name") or [{}])[0].get("nodeName", "")
    groups, nodes = [], []
    papers_of = {}
    for g in data["root"]["children"][:MAX_GROUPS]:
        members = sorted(g["children"], key=lambda c: -c["size"])
        if not members:
            continue
        index = len(groups) + 1
        names = ", ".join(short(c["name"]) for c in members[:2])
        groups.append({"key": index, "label": names + (" and others" if len(members) > 2 else ""),
                       "size": len(members)})
        for c in members:
            nodes.append({"id": c["numberName"], "label": short(c["name"]), "group": index,
                          "papers": len(c.get("papers") or []), "weight": c["size"]})
            papers_of[c["numberName"]] = set(c.get("papers") or [])
    if not nodes:
        return None
    sizes = [n["weight"] for n in nodes]
    for n in nodes:
        n["r"] = scale_r(n["weight"], min(sizes), max(sizes), 5, 17)
    # Name the four biggest of each group on the plot; the rest show their name on hover
    for g in groups:
        members = sorted((n for n in nodes if n["group"] == g["key"]), key=lambda n: -n["weight"])
        for rank, n in enumerate(members):
            n["named"] = rank < 4
    # Links: papers two co-authors share (SciX's own link list can miss a group)
    links = []
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            shared = len(papers_of[a["id"]] & papers_of[b["id"]])
            if shared:
                links.append({"source": a["id"], "target": b["id"], "weight": shared})
    for n in nodes:
        n["space"] = n["r"] + (16 if n["named"] else 3)
    # Each group gets its own side of the centre
    count = len(groups)
    anchors = {n["id"]: (0.28 * math.cos(2 * math.pi * (n["group"] - 1) / count - math.pi / 2),
                         0.28 * math.sin(2 * math.pi * (n["group"] - 1) / count - math.pi / 2))
               for n in nodes} if count > 1 else {}
    # The author themself sits in the middle, tied to everyone
    centre = {"id": "me", "label": short(me) if me else "Me", "group": 0, "papers": None, "weight": 0,
              "r": 16, "space": 34, "named": True, "me": True}
    spokes = [{"source": "me", "target": n["id"], "weight": 0.5} for n in nodes]
    all_nodes, drawn = draw([centre] + nodes, links + spokes, width, height, pin="me", seed=3, anchors=anchors,
                            keep=4)
    # The ties to the author only hold the layout together; they are not drawn
    drawn = [l for l in drawn if l["source"] != "me"]
    for l in drawn:
        l["spoke"] = False
    return {"width": width, "height": height, "groups": groups, "nodes": all_nodes,
            "links": sorted(drawn, key=lambda l: (not l["spoke"], l["weight"])),
            "count": len(nodes)}


def paper_network(raw, width=640, height=380):
    data = raw["data"]
    # Papers name their group by the summary node's id
    summary = {n["id"]: n for n in data["summaryGraph"]["nodes"]}
    order = sorted(summary, key=lambda k: -summary[k]["paper_count"])[:MAX_GROUPS]
    colour = {name: i + 1 for i, name in enumerate(order)}
    groups = []
    for name in order:
        words = [WORDS.get(w.lower(), w) for w in summary[name].get("node_label") or {}
                 if w.lower() not in STOPWORDS][:4]
        groups.append({"key": colour[name], "label": " · ".join(words) or f"Group {colour[name]}",
                       "papers": summary[name]["paper_count"], "citations": summary[name].get("total_citations")})
    nodes = []
    for n in data["fullGraph"]["nodes"]:
        if n.get("group") not in colour:
            continue
        bibcode = n["node_name"]
        surname = (n.get("first_author") or "").split(",")[0].strip()
        nodes.append({"id": n["id"], "label": f"{surname} {bibcode[:4]}", "group": colour[n["group"]],
                      "title": n.get("title") or "", "citations": n.get("citation_count") or 0,
                      "url": ABS_URL.format(bibcode), "named": True})
    if not nodes:
        return None
    cites = [n["citations"] for n in nodes]
    for n in nodes:
        n["r"] = scale_r(n["citations"] + 1, min(cites) + 1, max(cites) + 1, 7, 18)
    links = [{"source": l["source"], "target": l["target"], "weight": l["weight"]}
             for l in data["fullGraph"]["links"]]
    for n in nodes:
        n["space"] = n["r"] + 16
    nodes, drawn = draw(nodes, links, width, height, seed=2, keep=3)
    for l in drawn:
        l["spoke"] = False
    return {"width": width, "height": height, "groups": groups, "nodes": nodes,
            "links": sorted(drawn, key=lambda l: l["weight"]), "count": len(nodes)}


def build(author_raw=None, paper_raw=None):
    """Both networks from SciX's replies (read from assets/data/ when not given)."""
    def load(kind, given):
        if given is not None:
            return given
        path = RAW_DIR / f"scix-{kind}-network.json"
        return json.loads(path.read_text()) if path.exists() else None
    out = {"updated": datetime.date.today().isoformat()}
    for kind, make in (("author", author_network), ("paper", paper_network)):
        raw = load(kind, author_raw if kind == "author" else paper_raw)
        try:
            out[kind] = make(raw) if raw else None
        except (KeyError, TypeError, ValueError) as err:
            print(f"::warning::SciX {kind} network could not be read ({err!r}); it is left out.")
            out[kind] = None
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"Wrote {OUT_FILE.name}: " + ", ".join(
        f"{k} network {out[k]['count']} nodes" if out[k] else f"no {k} network" for k in ("author", "paper")))
    return out


if __name__ == "__main__":
    build()
