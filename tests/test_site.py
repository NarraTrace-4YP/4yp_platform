"""Checks for the landing page in site/, which Caddy serves at the domain itself.

- Every link and file a page points to within the site exists, including #sections.
- The pages keep to what the Caddyfile's Content-Security-Policy allows: their own CSS,
  fonts and images only. No scripts, no inline styles, nothing loaded from other sites.
  (Links to other sites are fine: following a link isn't loading it.)
- Caddy is set up to serve site/.
"""

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PAGES = sorted(SITE.rglob("*.html"))


class Page(HTMLParser):
    """The parts of a page these tests need."""

    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self.ids: set[str] = set()
        self.links: list[str] = []  # <a href>: may point anywhere
        self.loads: list[str] = []  # files the browser fetches to show the page
        self.problems: list[str] = []
        self.has_title = False
        self.feed(path.read_text(encoding="utf-8"))

    def handle_starttag(self, tag, attrs):
        a = {name: value or "" for name, value in attrs}
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "title":
            self.has_title = True
        if tag in ("script", "style", "iframe", "object", "embed"):
            self.problems.append(f"<{tag}> is not allowed")
        for name in a:
            if name == "style" or name.startswith("on"):
                self.problems.append(f"<{tag} {name}=...> is not allowed")
        if tag == "a" and "href" in a:
            self.links.append(a["href"])
        if tag in ("img", "source") and "src" in a:
            self.loads.append(a["src"])
        if tag == "link" and "href" in a:
            rel = a.get("rel", "").split()
            if {"stylesheet", "icon", "preload", "apple-touch-icon"} & set(rel):
                self.loads.append(a["href"])


def target(page: Path, ref: str) -> tuple[Path, str]:
    """The file a local link points to, and its #fragment."""
    parts = urlsplit(ref)
    if parts.path.startswith("/"):
        file = SITE / parts.path.lstrip("/")
    elif parts.path:
        file = page.parent / parts.path
    else:
        file = page
    if parts.path.endswith("/") or file.is_dir():
        file = file / "index.html"
    return file.resolve(), parts.fragment


def is_local(ref: str) -> bool:
    parts = urlsplit(ref)
    return not parts.scheme and not parts.netloc


def test_the_pages_exist():
    for name in ("index.html", "about/index.html", "404.html", "robots.txt", "favicon.ico"):
        assert (SITE / name).is_file(), f"site/{name} is missing"


@pytest.mark.parametrize("path", PAGES, ids=lambda p: str(p.relative_to(SITE)))
def test_page_has_a_title_and_no_scripts_or_inline_styles(path):
    page = Page(path)
    assert page.has_title, "every page needs a <title>"
    assert not page.problems, "; ".join(page.problems)


@pytest.mark.parametrize("path", PAGES, ids=lambda p: str(p.relative_to(SITE)))
def test_page_loads_only_its_own_files(path):
    page = Page(path)
    outside = [ref for ref in page.loads if not is_local(ref)]
    assert not outside, f"loads from other sites, which the Caddyfile blocks: {outside}"


@pytest.mark.parametrize("path", PAGES, ids=lambda p: str(p.relative_to(SITE)))
def test_local_links_and_files_exist(path):
    page = Page(path)
    broken = []
    for ref in page.links + page.loads:
        if not is_local(ref):
            continue
        file, fragment = target(path, ref)
        if not file.is_relative_to(SITE.resolve()) or not file.is_file():
            broken.append(ref)
        elif fragment and fragment not in Page(file).ids:
            broken.append(f"{ref} (no id={fragment!r})")
    assert not broken, f"broken: {broken}"


def test_stylesheet_files_exist():
    css = SITE / "assets" / "site.css"
    text = css.read_text(encoding="utf-8")
    refs = [part.split(")", 1)[0].strip("'\" ") for part in text.split("url(")[1:]]
    assert refs, "expected the @font-face rules"
    missing = [ref for ref in refs if not (css.parent / ref).is_file()]
    assert not missing, f"site.css points to missing files: {missing}"


def test_caddy_serves_the_site():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    caddyfile = (ROOT / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    assert "./site:/srv/site:ro" in compose
    assert "{$DOMAIN} {" in caddyfile
    assert "root * /srv/site" in caddyfile
