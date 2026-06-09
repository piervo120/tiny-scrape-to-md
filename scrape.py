"""
Scrape a website's menu links and save each page as a Markdown file.

Usage:
    python scrape.py
    python scrape.py --url https://example.com --menu "ul#main-menu" --out ./out

The script is intentionally parameterised so you can reuse it for other sites.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

UA = "Mozilla/5.0 (compatible; md-archiver/1.0; +contact: local user)"
REQUEST_TIMEOUT = 25
THROTTLE_SECONDS = 0.8  # be polite to the origin server

# Selectors tried in order to locate the main article body.
CONTENT_SELECTORS = [
    ".entry-content",
    "article .post-content",
    "article",
    "main",
    "#content",
]

# Tags removed from the content block before markdown conversion.
STRIP_TAGS = ["script", "style", "noscript", "iframe", "form", "nav", "aside"]

# CSS class fragments (case-insensitive) that mark non-content widgets.
STRIP_CLASS_FRAGMENTS = [
    "share", "social", "newsletter", "subscribe", "comment",
    "related", "sidebar", "widget", "breadcrumb", "advert", "ads",
    "post-meta", "author-bio", "wp-block-buttons",
]


def fetch(session: requests.Session, url: str) -> BeautifulSoup:
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    # Force UTF-8 if server didn't declare one (the site uses utf-8)
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return BeautifulSoup(resp.text, "lxml")


def _links_from_menu(menu, base_url: str) -> list[str]:
    """Collect unique same-host links from an already-selected menu element."""
    base_host = urlparse(base_url).netloc
    out: list[str] = []
    seen: set[str] = set()
    for a in menu.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        absolute = urljoin(base_url, href)
        # Strip fragment + normalise the trailing slash so `/foo` and `/foo/`
        # dedupe to a single URL (avoids fetching the same page twice).
        parsed = urlparse(absolute)
        if parsed.netloc != base_host:
            continue
        norm_path = parsed.path.rstrip("/") or "/"
        clean = parsed._replace(path=norm_path, fragment="").geturl()
        if clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def extract_menu_links(soup: BeautifulSoup, menu_selector: str, base_url: str) -> list[str]:
    menu = soup.select_one(menu_selector)
    if not menu:
        raise SystemExit(f"Menu selector '{menu_selector}' not found on {base_url}")
    return _links_from_menu(menu, base_url)


# Literal selectors tried in addition to the ones derived from the DOM.
FALLBACK_MENU_SELECTORS = [
    "nav ul", "header nav", "[role=navigation]", ".menu", "#menu",
]


def discover_menu_selectors(soup: BeautifulSoup, base_url: str) -> list[tuple[str, int]]:
    """Detect candidate menu containers and count their crawlable links.

    Returns [(css_selector, link_count), ...] sorted by link_count descending.
    Each selector is counted with the same select_one() the crawl will use, so
    the displayed count equals what would actually be crawled.
    """
    candidates: list[str] = []
    # Selectors derived from the DOM: prefer #id, else first class.
    for el in soup.find_all(["nav", "ul"]):
        if el.get("id"):
            candidates.append(f"{el.name}#{el['id']}")
        elif el.get("class"):
            candidates.append(f"{el.name}.{el['class'][0]}")
    candidates.extend(FALLBACK_MENU_SELECTORS)

    results: list[tuple[str, int]] = []
    seen_selectors: set[str] = set()
    seen_linksets: list[frozenset[str]] = []
    for sel in candidates:
        if sel in seen_selectors:
            continue
        seen_selectors.add(sel)
        try:
            el = soup.select_one(sel)
        except Exception:
            # soupsieve raises on selectors with exotic class names.
            continue
        if not el:
            continue
        links = _links_from_menu(el, base_url)
        if not links:
            continue
        linkset = frozenset(links)
        # Drop selectors that resolve to the exact same set of links as one we
        # already kept (e.g. `nav ul` and `ul#main-menu` over the same menu).
        if linkset in seen_linksets:
            continue
        seen_linksets.append(linkset)
        results.append((sel, len(links)))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def choose_menu_selector(candidates: list[tuple[str, int]]) -> str:
    """Print the candidate menu selectors and let the user pick one."""
    if not candidates:
        raise SystemExit(
            "No menu container with links could be detected on the home page.\n"
            "Pass one explicitly, e.g. --menu \"nav.primary ul\"."
        )
    print("\nNo --menu given — menu selectors detected on the home page:\n")
    width = max(len(sel) for sel, _ in candidates)
    for i, (sel, n) in enumerate(candidates, 1):
        print(f"  [{i}] {sel:<{width}}  ({n} links)")

    while True:
        try:
            raw = input("\nPick a selector [1] (q to abort): ").strip()
        except EOFError:
            # Non-interactive stdin: fall back to the top candidate.
            sel = candidates[0][0]
            print(f"(no input — using '{sel}')")
            return sel
        if raw.lower() == "q":
            raise SystemExit("Aborted.")
        if raw == "":
            return candidates[0][0]
        if raw.isdigit() and 1 <= int(raw) <= len(candidates):
            return candidates[int(raw) - 1][0]
        print(f"  Please enter a number between 1 and {len(candidates)}, or q.")


def pick_content(soup: BeautifulSoup) -> BeautifulSoup | None:
    for sel in CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 200:
            return el
    return None


def clean_content(node) -> None:
    """Strip boilerplate elements in place."""
    for tag in node.find_all(STRIP_TAGS):
        tag.decompose()
    for el in list(node.find_all(class_=True)):
        # Treat hyphen/underscore/space as token boundaries so a fragment only
        # matches whole class tokens: "share" hits "post-share-buttons" but not
        # "shareholder", and "ads" no longer matches "downloads".
        classes = " ".join(el.get("class", [])).lower()
        if any(
            re.search(rf"(?<![a-z0-9]){re.escape(frag)}(?![a-z0-9])", classes)
            for frag in STRIP_CLASS_FRAGMENTS
        ):
            el.decompose()


def absolutize_links(node, base_url: str) -> None:
    """Rewrite relative href/src in place so the Markdown links work standalone."""
    for a in node.find_all("a", href=True):
        a["href"] = urljoin(base_url, a["href"])
    for img in node.find_all("img", src=True):
        img["src"] = urljoin(base_url, img["src"])


# Windows device names that can't be used as a file/folder name.
_RESERVED_NAMES = {"con", "prn", "aux", "nul",
                   *(f"com{i}" for i in range(1, 10)),
                   *(f"lpt{i}" for i in range(1, 10))}


def _safe_segment(seg: str) -> str:
    """Make a single URL path segment safe as a Windows file/folder name."""
    seg = re.sub(r'[<>:"/\\|?*]+', "_", seg)
    seg = seg.rstrip(" .")  # Windows forbids trailing spaces/dots
    if seg.lower() in _RESERVED_NAMES:
        seg += "_"
    return seg or "_"


def url_to_path(url: str, parent_urls: set[str]) -> Path:
    """
    Map an URL to a relative Path under the output dir.

    Rules:
      /                      -> index.md
      /foo/                  -> foo.md   (or foo/index.md if another URL is a child of /foo/)
      /foo/bar/              -> foo/bar.md (or foo/bar/index.md if a child exists)
      /foo                   -> foo.md
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return Path("index.md")
    segments = [seg for seg in path.split("/") if seg]
    # Is this URL the parent of any other? Then use index.md inside its folder.
    # Computed on the raw segments so it matches the raw paths in parent_urls.
    prefix = "/" + "/".join(segments) + "/"
    is_parent = any(
        other != url and urlparse(other).path.startswith(prefix)
        for other in parent_urls
    )
    # Sanitise only for the on-disk path, after the parent check.
    safe = [_safe_segment(seg) for seg in segments]
    if is_parent:
        return Path(*safe) / "index.md"
    if len(safe) == 1:
        return Path(f"{safe[0]}.md")
    return Path(*safe[:-1]) / f"{safe[-1]}.md"


def host_to_slug(url: str) -> str:
    """Map a root URL to a folder-safe site name. example.com -> 'example_com';
    a leading 'www.' is dropped."""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return re.sub(r"[^a-z0-9]+", "_", host).strip("_") or "site"


def safe_title(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1")
    if h1:
        return h1.get_text(" ", strip=True)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return ""


def to_markdown(content_node) -> str:
    # markdownify accepts HTML strings
    html = str(content_node)
    text = md(
        html,
        heading_style="ATX",        # use # / ## / ### headings
        bullets="-",
        escape_asterisks=False,
        escape_underscores=False,
    )
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def build_frontmatter(url: str, title: str) -> str:
    safe_title_val = title.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "---\n"
        f'title: "{safe_title_val}"\n'
        f"source: {url}\n"
        f"scraped_at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        "---\n\n"
    )


def process(url: str, session: requests.Session, out_dir: Path,
            parent_urls: set[str]) -> tuple[str, Path] | None:
    soup = fetch(session, url)
    content = pick_content(soup)
    if content is None:
        print(f"  [warn] no content container found for {url}", file=sys.stderr)
        return None
    # Read the title before cleaning, in case the <h1> sits in a stripped block.
    title = safe_title(soup) or urlparse(url).path
    clean_content(content)
    absolutize_links(content, url)
    body = to_markdown(content)
    rel = url_to_path(url, parent_urls)
    target = out_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_frontmatter(url, title) + body + "\n", encoding="utf-8")
    return title, rel


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://example.com/")
    parser.add_argument("--menu", default=None,
                        help="CSS selector for the menu container to extract links from. "
                             "If omitted, candidate selectors are detected and you are "
                             "prompted to pick one.")
    parser.add_argument("--out", default=str(Path(__file__).parent / "output"),
                        help="Output directory for markdown files. A per-site "
                             "subfolder (derived from --url) is created under it.")
    args = parser.parse_args()

    out_dir = (Path(args.out) / host_to_slug(args.url)).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "fr,en;q=0.8"})

    print(f"Fetching home: {args.url}")
    try:
        home = fetch(session, args.url)
    except requests.RequestException as e:
        raise SystemExit(f"Could not fetch home page {args.url}: {e}")
    menu_selector = args.menu
    if menu_selector is None:
        menu_selector = choose_menu_selector(discover_menu_selectors(home, args.url))
    urls = extract_menu_links(home, menu_selector, args.url)
    print(f"Discovered {len(urls)} unique menu links")

    parent_set = set(urls)
    index_entries: list[tuple[str, Path]] = []

    for i, url in enumerate(urls, 1):
        print(f"[{i:>2}/{len(urls)}] {url}")
        try:
            result = process(url, session, out_dir, parent_set)
            if result:
                index_entries.append(result)
        except requests.RequestException as e:
            print(f"  [error] {e}", file=sys.stderr)
        except Exception as e:
            print(f"  [error] {type(e).__name__}: {e}", file=sys.stderr)
        if i < len(urls):
            time.sleep(THROTTLE_SECONDS)

    # Index file
    if index_entries:
        index_path = out_dir / "INDEX.md"
        lines = ["# Index\n",
                 f"Source: {args.url}",
                 f"Scraped: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
                 f"Pages: {len(index_entries)}\n"]
        for title, rel in sorted(index_entries, key=lambda x: str(x[1])):
            posix = rel.as_posix()
            lines.append(f"- [{title}]({posix})")
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nWrote {index_path}")

    print(f"Done. Files under: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
