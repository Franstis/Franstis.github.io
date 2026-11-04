# francescopistis.github.io

Personal academic website of Francesco Pistis, built with Jekyll and hosted on GitHub Pages.

## Editing

Each page is a Markdown or HTML file:

| File | Page |
|---|---|
| `index.html` | Home (figures and latest papers are filled in from the data files) |
| `research.html` | Research → Publications (from `_data/publications.json`) |
| `research-data.html` | Research → Data |
| `research-codes.html` | Research → Codes (from `_data/codes.yml`) |
| `research-networks.html` | Research → Networks (from `_data/networks.json`) |
| `talks.html` | Talks and conferences (from `_data/talks.json`, built from the CV) |
| `cv.html` | CV (from `_data/cv.json`, built from the CV) |
| `contact.html` | Contact |

- Site title, description, the top menu, the Research tabs and the banner image are in `_config.yml`.
  The banner image file goes in `assets/img/banner.jpg`.
- The page template is `_layouts/default.html`; styles are in `assets/css/style.css`;
  search, filters, copy buttons, the light/dark switch and animations are in `assets/js/site.js`.
- To add a talk, add a `\talkentry` to the CV in Overleaf (see below).

## Publication list

The papers on the Research page come from SciX (scixplorer.org, the successor of NASA ADS). Every day, and on every
push, the workflow in `.github/workflows/pages.yml` runs
`scripts/update_publications.py`. The script asks SciX for papers linked to the
ORCID or by name and sorts them into first-author papers, co-authored papers,
accepted/submitted preprints and conference proceedings.

Above the list, four charts show papers per year (counted from the list),
citations and reads per year (from the SciX metrics API, for the papers in the
list) and the h-index at the end of each year, as two curves. The strict curve
counts only refereed papers citing refereed papers, and leaves out
self-citations: a citing paper that shares any author with the cited one (same
surname and first name, or the same initial when one is abbreviated). The other
counts all citations to all papers. The h-index on the home page is SciX's own,
counting all citations.
The citations, reads and h-index charts, the h-index and the "Copy BibTeX"
buttons only appear when the list comes from SciX.

The Networks tab (`research-networks.html`) shows SciX's author network and
paper network for the same papers, drawn as SciX draws them: the author network
as a ring of co-author groups around you (tick "Show links" for the links
between co-authors), the paper network as a ring of groups of papers that cite
the same references. Each has SciX's "group activity over time" chart and the
list of each group. The build saves SciX's replies in
`assets/data/scix-*-network.json`, and `scripts/networks.py` turns them into
`_data/networks.json` (run it alone to redo it from the saved replies). The
charts on both tabs share `_includes/chart.html`.

## CV and talks (from Overleaf)

The CV lives in Overleaf, synced to the private GitHub repository
`Franstis/Academic-CV`. Every build (daily, and on every push here) reads its
`main.tex` with `scripts/update_cv.py`, fills in the CV and Talks pages, and
compiles the PDF behind the "Download the full CV" button.

- After editing the CV in Overleaf, push it to GitHub (Overleaf: **Menu → GitHub
  → Push Overleaf changes to GitHub**). The site picks it up within a day, or
  straight away with **Run workflow** in the Actions tab.
- Entries are read from `\cventry{date}{text}` and
  `\talkentry{date}{event (oral|poster|ePoster)}{title}` under each `\section`.
  The Publications and Talks sections become links to their own pages.
- The CV's publication list is written from SciX: every build replaces
  `publications.tex` in the CV repository (read by `\input{publications}` in
  `main.tex`) and pushes it there when it changes. Get it in Overleaf with
  **Menu → GitHub → Pull GitHub changes into Overleaf**. Papers SciX does not
  list yet go in `publications-extra.tex`, under a `\subsection` named
  Published, Accepted, Submitted or Proceedings; each one is removed from that
  file automatically once SciX lists a paper with the same title.
- The build reads (and writes `publications.tex` to) the private repository with
  a token saved as the `CV_TOKEN` secret; it needs **Contents: Read and write**. Without it, the CV and Talks data saved in this repository
  (`_data/cv.json`, `_data/talks.json`) are used, and the download button opens
  the Drive copy set in `_config.yml`.

## Search engines and sharing

- Each page has a `description:` line in its front matter; Google shows it under
  the page title in search results.
- The home page carries profile data for Google (name, job, affiliations,
  ORCID, Scopus, GitHub), taken from `author:` in `_config.yml`.
- `assets/img/share.jpg` (1200×630) is the picture shown when a link to the site
  is shared. To verify the site in Google Search Console, choose the "HTML tag"
  method and paste its code into `google_site_verification` in `_config.yml`.
- The banner comes in two files: `banner.webp` (used by almost all browsers) and
  `banner.jpg`. To change the picture, replace both, keeping them to about
  2560 pixels wide.
- `404.html` is the page shown for addresses that do not exist.

- The query, papers to leave out and other settings are at the top of the script.
- SciX finds papers by ORCID only once they are claimed in SciX
  (scixplorer.org → ORCID).
- `_data/publications.json` in the repository holds a list taken from the CV. It
  is used until the SciX token is set up, and whenever the token is missing.
- To refresh the list now, open the **Actions** tab, pick **Build and deploy site**
  and click **Run workflow**.

## Publishing

1. Create a public repository named `francescopistis.github.io` on GitHub.
2. Push these files to its `main` branch.
3. In the repository, open **Settings → Pages** and set the source to
   **GitHub Actions**.
4. Create a SciX API token (scixplorer.org → Account → Settings → API Token).
   In the repository, open **Settings → Secrets and variables → Actions**, click
   **New repository secret**, name it `ADS_TOKEN` and paste the token.

GitHub builds the site after every push and every day. It appears at
<https://francescopistis.github.io> within a minute or two.
