"""Inspect homepage menu structure + sample article structure."""
import sys
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; md-archiver/1.0)"
HEADERS = {"User-Agent": UA}

def show(url, label):
    print(f"\n=== {label}: {url} ===")
    r = requests.get(url, headers=HEADERS, timeout=20)
    print("status:", r.status_code, "size:", len(r.text))
    return BeautifulSoup(r.text, "lxml")

# URL to probe: pass it as the first CLI arg, otherwise fall back to example.com
base_url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com/"
if "://" not in base_url:
    base_url = "https://" + base_url
host = urlparse(base_url).netloc

home = show(base_url, "HOME")

menu = home.select_one("ul#main-menu")
print("\nmain-menu found:", menu is not None)
if menu:
    links = menu.find_all("a", href=True)
    print(f"  links count: {len(links)}")
    seen = set()
    for a in links:
        h = a["href"]
        if h in seen:
            continue
        seen.add(h)
        print(f"  - {a.get_text(strip=True)[:50]:50s} -> {h}")

# Pick first non-home link to inspect article layout
first_article = None
home_root = base_url.rstrip("/")
for a in (menu.find_all("a", href=True) if menu else []):
    href = a["href"]
    if href.startswith("http") and urlparse(href).netloc == host and href.rstrip("/") != home_root:
        first_article = href
        break
    if href.startswith("/") and len(href) > 1:
        first_article = urljoin(base_url, href)
        break

if first_article:
    art = show(first_article, "SAMPLE ARTICLE")
    # Try common content containers
    for sel in ["main", "article", ".entry-content", ".post-content", ".content", "#content", ".td-post-content"]:
        el = art.select_one(sel)
        if el:
            print(f"  selector '{sel}' -> tag={el.name}, classes={el.get('class')}, text_len={len(el.get_text(strip=True))}")
    title = art.select_one("h1")
    print("  h1:", title.get_text(strip=True) if title else None)
