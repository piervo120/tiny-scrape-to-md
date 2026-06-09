# site-to-markdown

Tiny Python scraper that walks a site's menu links and saves each page as clean
Markdown, mirroring the URL structure into folders.

Works on any site whose menu can be targeted with a CSS selector.

## Usage

Requires Python 3.10+.

```powershell
# Install dependencies in a venv
python -m venv .venv
.\.venv\Scripts\pip.exe install requests beautifulsoup4 markdownify lxml

# Run (without --menu, the script detects menus and asks which one to use)
.\.venv\Scripts\python.exe scrape.py --url https://example.com/

# Target a specific menu and output folder
.\.venv\Scripts\python.exe scrape.py `
  --url https://example.com/ `
  --menu "nav.primary ul" `
  --out  ./example-md
```

| Flag     | Default                    | Purpose                                           |
|----------|----------------------------|---------------------------------------------------|
| `--url`  | *(required)*               | Root URL of the site to scrape                    |
| `--menu` | *(interactive discovery)*  | CSS selector of the menu to extract links from    |
| `--out`  | `./output`                 | Output folder; a per-site subfolder is created (`output/<site>/`) |

Without `--menu`, the script lists detected menus with their link counts and
lets you pick (Enter = the largest; `q` to abort):

```
  [1] ul#main-menu    (14 links)
  [2] nav.primary ul  (9 links)

Pick a selector [1] (q to abort):
```

## How it runs

1. Fetches the home page and extracts the unique links from the chosen menu.
2. For each link: downloads the page, isolates the main content block, and
   strips boilerplate (scripts, sidebars, share widgets…).
3. Converts the HTML to Markdown and writes a file in a tree that mirrors the
   URL (`/foo/bar/` → `foo/bar.md`).
4. Generates an `INDEX.md` linking to every page.

Each site lands in its own subfolder (`output/example_com/` for `example.com`),
`INDEX.md` included.

The crawl is intentionally shallow: it follows **only** the menu links, not the
ones found inside article bodies.

## Technical details

Each `.md` file starts with a YAML frontmatter:

```yaml
---
title: "Title pulled from the <h1>"
source: https://example.com/source-url/
scraped_at: 2026-05-24T08:51:38+00:00
---
```

**Tuning** — four constants at the top of `scrape.py` cover the common cases:
`CONTENT_SELECTORS` (where to look for the article), `STRIP_TAGS` (tags to
remove), `STRIP_CLASS_FRAGMENTS` (non-content widgets), `THROTTLE_SECONDS`
(delay between requests, 0.8s by default).

**`probe.py`** — ad-hoc inspection tool: tries several selectors on a page of a
new site and prints each one's text length, to pick the right
`CONTENT_SELECTORS` before the full crawl.

**Limitations** — no JavaScript rendering (`requests` only, so SPAs are
unsupported), no recursion beyond the menu, and `robots.txt` is not checked:
respect the target site's terms of use and keep the request rate reasonable.
