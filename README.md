# francescopistis.github.io

Personal academic website of Francesco Pistis, built with Jekyll and hosted on GitHub Pages.

## Editing

Each page is a Markdown file:

| File | Page |
|---|---|
| `index.md` | Home |
| `research.html` | Research → Publications (from `_data/publications.json`) |
| `research-data.html` | Research → Data |
| `research-codes.html` | Research → Codes (from `_data/codes.yml`) |
| `talks.md` | Talks and conferences |
| `cv.md` | CV |
| `contact.md` | Contact |

- Site title, description, the top menu, the Research tabs and the banner image are in `_config.yml`.
  The banner image file goes in `assets/img/banner.jpg`.
- The page template is `_layouts/default.html`; styles are in `assets/css/style.css`.

## Publication list

The papers on the Research page come from SciX (scixplorer.org, the successor of NASA ADS). Every Monday, and on every
push, the workflow in `.github/workflows/pages.yml` runs
`scripts/update_publications.py`. The script asks SciX for papers linked to the
ORCID or by name and sorts them into first-author papers, co-authored papers,
accepted/submitted preprints and conference proceedings.

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

GitHub builds the site after every push and every Monday. It appears at
<https://francescopistis.github.io> within a minute or two.
