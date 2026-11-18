"""Checks for the landing page in site/, which Caddy serves at the domain itself.

- Every link and file a page points to within the site exists, including #sections.
- The pages load only local CSS, fonts, images and scripts. The O1 page gets its
  instance-specific Windmill iframe URL from a generated local runtime file.
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
O1_PAGE = SITE / "o1" / "index.html"
O1_RUNTIME_CONFIG = "/runtime/o1.json"


class Page(HTMLParser):
    """The parts of a page these tests need."""

    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self.ids: set[str] = set()
        self.links: list[str] = []  # <a href>: may point anywhere
        self.loads: list[str] = []  # files the browser fetches to show the page
        self.frames: list[str] = []
        self.problems: list[str] = []
        self.has_title = False
        self.feed(path.read_text(encoding="utf-8"))

    def handle_starttag(self, tag, attrs):
        a = {name: value or "" for name, value in attrs}
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "title":
            self.has_title = True
        if tag in ("style", "object", "embed"):
            self.problems.append(f"<{tag}> is not allowed")
        if tag == "script":
            if not a.get("src"):
                self.problems.append("inline <script> is not allowed")
            else:
                self.loads.append(a["src"])
        if tag == "iframe":
            self.frames.append(a.get("src", ""))
            if not a.get("title", "").strip():
                self.problems.append("<iframe> needs a title")
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
    if ref.startswith('{{env "'):
        return False  # Caddy renders this as an environment-specific absolute URL.
    parts = urlsplit(ref)
    return not parts.scheme and not parts.netloc


def test_the_pages_exist():
    for name in ("index.html", "about/index.html", "404.html", "robots.txt", "favicon.ico"):
        assert (SITE / name).is_file(), f"site/{name} is missing"
    assert O1_PAGE.is_file()


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
    assert page.frames == ([""] if path == O1_PAGE else [])


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
    assert "./runtime:/srv/runtime:ro" in compose
    assert "{$PUBLIC_SCHEME}://{$DOMAIN} {" in caddyfile
    assert "templates" in caddyfile
    assert "root * /srv/site" in caddyfile
    assert "root * /srv/runtime" in caddyfile
    assert "frame-src {$PUBLIC_SCHEME}://windmill.{$DOMAIN}" in caddyfile


@pytest.mark.parametrize(
    ("scheme", "domain", "expected_windmill", "expected_mlflow"),
    [
        ("http", "localhost", "http://windmill.localhost/", "http://mlflow.localhost/"),
        (
            "https",
            "project.example",
            "https://windmill.project.example/",
            "https://mlflow.project.example/",
        ),
    ],
)
def test_service_links_render_for_local_and_server_environments(
    scheme, domain, expected_windmill, expected_mlflow
):
    html = (SITE / "index.html").read_text(encoding="utf-8")
    rendered = html.replace('{{env "PUBLIC_SCHEME"}}', scheme).replace('{{env "DOMAIN"}}', domain)
    assert expected_windmill in rendered
    assert expected_mlflow in rendered
    assert "rshields.xyz" not in rendered


def test_local_environment_selects_localhost_over_http():
    settings = (ROOT / ".env.local.example").read_text(encoding="utf-8").splitlines()
    assert "DOMAIN=localhost" in settings
    assert "PUBLIC_SCHEME=http" in settings

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "PUBLIC_SCHEME: ${PUBLIC_SCHEME:-https}" in compose


def test_embedded_o1_app_uses_generated_instance_specific_public_url():
    metadata = (ROOT / "f/dhr/O1/app.raw_app/raw_app.yaml").read_text(encoding="utf-8")
    page = (SITE / "o1/index.html").read_text(encoding="utf-8")
    script = (SITE / "assets/objective-embed.js").read_text(encoding="utf-8")
    assert "public: true" in metadata
    assert f'data-objective-embed="{O1_RUNTIME_CONFIG}"' in page
    assert 'src="/assets/objective-embed.js"' in page
    assert "/public/dhr/" in script
    assert "09f7276fa53f893213d238447a5f345e" not in page
