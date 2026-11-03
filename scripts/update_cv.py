#!/usr/bin/env python3
"""Build the CV and Talks pages from the LaTeX CV (the Overleaf project).

Reads main.tex and writes:
  _data/cv.json     every \\section of the CV as date + text entries; Publications
                    and Talks only as links to their own pages
  _data/talks.json  the \\talkentry lines of "Selected Talks & Conferences"

The CV is expected to use the commands defined in main.tex:
  \\cventry{date}{text}
  \\talkentry{date}{event (oral|poster|ePoster)}{title}
  \\pubentry{...}{...}{...}   (skipped: the Research page comes from SciX)

Without the .tex file (for example when the CV repository could not be checked
out), the existing data files are left untouched.

Usage:
  python3 scripts/update_cv.py _cv/main.tex
"""
import datetime
import html
import json
import pathlib
import re
import sys
import unicodedata

DATA = pathlib.Path(__file__).resolve().parent.parent / "_data"

# Sections shown on their own pages: the CV page links to them instead
OWN_PAGES = {"publications": "/research/", "talks": "/talks/"}

ACCENTS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
           "=": "\u0304", ".": "\u0307", "v": "\u030c", "u": "\u0306", "c": "\u0327",
           "H": "\u030b", "k": "\u0328", "r": "\u030a"}
LETTERS = {"l": "ł", "L": "Ł", "o": "ø", "O": "Ø", "ss": "ß", "aa": "å", "AA": "Å",
           "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ", "i": "ı"}
SYMBOLS = {"ldots": "…", "dots": "…", "textperiodcentered": "·", "cdot": "·", "LaTeX": "LaTeX",
           "TeX": "TeX", "enspace": " ", "quad": " ", "qquad": " ", "sim": "∼", "simeq": "≃",
           "approx": "≈", "geq": "≥", "ge": "≥", "leq": "≤", "le": "≤", "lt": "&lt;", "gt": "&gt;",
           "times": "×", "pm": "±", "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
           "lambda": "λ", "Lambda": "Λ", "mu": "μ", "odot": "⊙", "AA": "Å", "textendash": "–",
           "textemdash": "—", "textbar": "|", "S": "§"}
# Formatting commands without arguments: dropped
IGNORE = {"small", "footnotesize", "scriptsize", "tiny", "normalsize", "large", "Large",
          "selectfont", "noindent", "raggedright", "centering", "par", "relax", "bfseries",
          "itshape", "rmfamily", "sffamily", "ttfamily", "hfill", "vfill"}
# Commands whose single argument is kept, wrapped in a tag (None: no tag)
WRAP = {"textbf": "strong", "textit": "em", "emph": "em", "textsl": "em", "texttt": "code",
        "textrm": None, "textsf": None, "textup": None, "textnormal": None, "mbox": None,
        "text": None, "mathrm": None, "MakeUppercase": None}


def strip_comments(tex):
    return re.sub(r"(?<!\\)%.*", "", tex)


def read_group(s, i):
    """s[i] is "{": return (inside, index after the closing brace)."""
    depth, start = 0, i
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1:i], i + 1
        i += 1
    raise ValueError(f"unbalanced braces near: {s[start:start + 60]!r}")


def read_args(s, i, count):
    """Read `count` {...} arguments starting at s[i] (spaces between them allowed)."""
    args = []
    for _ in range(count):
        while i < len(s) and s[i].isspace():
            i += 1
        if i >= len(s) or s[i] != "{":
            raise ValueError(f"missing argument near: {s[i:i + 60]!r}")
        arg, i = read_group(s, i)
        args.append(arg)
    return args, i


def to_html(s, math=False):
    """LaTeX fragment -> safe HTML."""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", s[i:], re.S)
            name, i = m.group(1), i + len(m.group(0))
            if name.isalpha():
                while i < len(s) and s[i] == " ":  # spaces after a command word are eaten
                    i += 1
            if name in ACCENTS:
                if i < len(s) and s[i] == "{":
                    arg, i = read_group(s, i)
                else:
                    arg, i = s[i:i + 1], i + 1
                base = to_html(arg) or " "
                out.append(unicodedata.normalize("NFC", base[0] + ACCENTS[name] + base[1:]))
                if i < len(s) and s[i:i + 2] == "{}":
                    i += 2
            elif name in LETTERS:
                out.append(LETTERS[name])
                if s[i:i + 2] == "{}":
                    i += 2
            elif name in SYMBOLS:
                out.append(SYMBOLS[name])
                if s[i:i + 2] == "{}":
                    i += 2
            elif name in WRAP:
                (arg,), i = read_args(s, i, 1)
                inner = to_html(arg, math)
                tag = WRAP[name]
                out.append(f"<{tag}>{inner}</{tag}>" if tag else inner)
            elif name == "href":
                (url, text), i = read_args(s, i, 2)
                url = url.replace("\\%", "%").replace("\\#", "#").replace("\\&", "&")
                out.append(f'<a href="{html.escape(url)}">{to_html(text)}</a>')
            elif name == "url":
                (url,), i = read_args(s, i, 1)
                out.append(f'<a href="{html.escape(url)}">{html.escape(url)}</a>')
            elif name == "role":
                (arg,), i = read_args(s, i, 1)
                out.append(f'<span class="tag">{to_html(arg)}</span>')
            elif name == "textcolor":
                (_, arg), i = read_args(s, i, 2)
                out.append('<span class="sep">·</span>' if arg.strip() == "|" else to_html(arg))
            elif name == "color":
                i = read_args(s, i, 1)[1]  # Gray text: handled by the enclosing group
            elif name in ("newline", "linebreak") or name == "\\":
                if s[i:i + 1] == "[":  # \\[0.25em]
                    i = s.index("]", i) + 1
                out.append("<br>")
            elif name in ("vspace", "hspace", "rule", "addvspace"):
                i = read_args(s, i, 2 if name == "rule" else 1)[1]
            elif name in (",", ";", ":", " ", "\n"):
                out.append(" " if name != "," else "\u2009")
            elif name == "!":
                pass
            elif name in "&%$#_{}":
                out.append(html.escape(name))
            elif name in IGNORE:
                pass
            else:
                out.append(html.escape(name))  # unknown command: keep its name as text
        elif c == "{":
            inner, i = read_group(s, i)
            muted = re.match(r"\s*\\color\{(?:Gray|gray)\}", inner)
            text = to_html(inner, math)
            out.append(f'<span class="muted">{text.strip()}</span>' if muted else text)
        elif c == "}":
            i += 1
        elif c == "$":
            end = s.index("$", i + 1)
            out.append(to_html(s[i + 1:end], math=True).replace(" ", ""))
            i = end + 1
        elif not math and s.startswith("---", i):
            out.append("—"); i += 3
        elif not math and s.startswith("--", i):
            out.append("–"); i += 2
        elif not math and s.startswith("``", i):
            out.append("“"); i += 2
        elif not math and s.startswith("''", i):
            out.append("”"); i += 2
        elif c == "~":
            out.append("\u00a0"); i += 1
        elif c in "^_" and math:
            i += 1
            if i < len(s) and s[i] == "{":
                arg, i = read_group(s, i)
            else:
                arg, i = s[i], i + 1
            tag = "sup" if c == "^" else "sub"
            out.append(f"<{tag}>{to_html(arg, True)}</{tag}>")
        else:
            out.append(html.escape(c)); i += 1
    return "".join(out)


def tidy(fragment):
    """Collapse whitespace, including around line breaks."""
    text = re.sub(r"\s+", " ", fragment)
    text = re.sub(r"\s*<br>\s*", "<br>", text)
    return text.strip()


def plain(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", tidy(fragment)))


def slug(title):
    return re.sub(r"[^a-z0-9]+", "-", plain(to_html(title)).lower()).strip("-")


def parse(tex):
    body = strip_comments(tex)
    body = body[body.index("\\begin{document}"):body.index("\\end{document}")]
    sections, talks = [], []
    pattern = re.compile(r"\\(section|subsection|cventry|talkentry|pubentry)\*?(?![A-Za-z])")
    pos = 0
    while True:
        m = pattern.search(body, pos)
        if not m:
            break
        kind = m.group(1)
        count = {"section": 1, "subsection": 1, "cventry": 2, "talkentry": 3, "pubentry": 3}[kind]
        args, pos = read_args(body, m.end(), count)
        if kind == "section":
            title = plain(to_html(args[0]))
            link = next((url for word, url in OWN_PAGES.items() if word in title.lower()), None)
            sections.append({"title": title, "id": slug(args[0]), "link": link, "groups": []})
        elif not sections:
            continue
        elif kind == "subsection":
            sections[-1]["groups"].append({"title": plain(to_html(args[0])), "entries": []})
        elif kind == "cventry":
            if not sections[-1]["groups"]:
                sections[-1]["groups"].append({"title": None, "entries": []})
            sections[-1]["groups"][-1]["entries"].append(
                {"date": plain(to_html(args[0])), "html": tidy(to_html(args[1]))})
        elif kind == "talkentry":
            event = plain(to_html(args[1]))
            m_type = re.search(r"\s*\((oral|poster|e-?poster|invited)[^)]*\)\s*$", event, re.I)
            kind_of = m_type.group(1).lower() if m_type else "oral"
            talks.append({
                "date": plain(to_html(args[0])).replace(" ", ""),
                "type": {"oral": "Oral", "poster": "Poster", "invited": "Invited"}.get(kind_of, "ePoster"),
                "event": event[:m_type.start()] if m_type else event,
                "title": plain(to_html(args[2])),
            })
    for s in sections:
        if s["link"]:
            s["groups"] = []
    shown = [s for s in sections if s["link"] or any(g["entries"] for g in s["groups"])]
    return shown, talks


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = pathlib.Path(sys.argv[1])
    if not path.exists():
        print(f"{path} not found; keeping the existing CV and talks data.")
        return 0
    sections, talks = parse(path.read_text(encoding="utf-8"))
    if not sections or not talks:
        print("No CV entries or talks found in the .tex file; keeping the existing data.", file=sys.stderr)
        return 1
    cv = {"updated": datetime.date.today().isoformat(), "sections": sections}
    (DATA / "cv.json").write_text(json.dumps(cv, ensure_ascii=False, indent=2) + "\n")
    (DATA / "talks.json").write_text(json.dumps(talks, ensure_ascii=False, indent=2) + "\n")
    entries = sum(len(g["entries"]) for s in sections for g in s["groups"])
    print(f"Wrote cv.json ({len(sections)} sections, {entries} entries) and talks.json ({len(talks)} talks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
